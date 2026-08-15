"""Shelf integrity check — the memory-shelf ``doctor``.

Wraps docshelf's structural ``doctor`` (stale meta, orphaned splits, stale
INDEX, …) and adds memshelf-specific checks: per-episode schema (id/kind,
required sections), the digest contract at rest, secret-shaped strings that
slipped onto disk, ledger consistency, and the INDEX injection budget. Read
only — nothing is written. See ARCHITECTURE.md → MCP tool surface / Failure
modes and ``docs/M0.md``.
"""

from __future__ import annotations

import re
import subprocess
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from memshelf_mcp.core.archive import archived_episodes
from memshelf_mcp.core.digest import validate_digest
from memshelf_mcp.core.episode import CATEGORY_BY_KIND, required_sections
from memshelf_mcp.core.frontmatter import parse_frontmatter
from memshelf_mcp.core.policy import load_pattern_pack
from memshelf_mcp.core.redact import scan, scan_patterns
from memshelf_mcp.core.remote import PRIVATE, PUBLIC, configured_remotes, remote_visibility

# ROADMAP M2 keeps INDEX under ~10 KB; at chars/4 that is ~2500 tokens injected
# every session, so warn past it.
INDEX_BUDGET_TOKENS = 2500

# How long the derived layer may lag before "not rendered yet" becomes "not
# being rendered" (#89). A day, because rendering is push-triggered: with the
# bot, a push to main starts it; without one, `rebuild` is part of shelving.
# Either way an episode still uncounted after a full day is not waiting for
# anything.
#
# The threshold is the whole point, because two states produced one signal. A
# shelve one second ago and a renderer dead since Tuesday both showed
# `warning no-ledger-row` — and the shelf's own rules say, correctly, that the
# fresh one must not be fixed by hand. So the documentation trained the reader
# to look past the only visible symptom of the second, and the longer the
# renderer stayed down, the more confidently the warning was dismissed. Nine
# episodes piled up that way on the dogfood shelf before anyone noticed.
DERIVED_STALE_AFTER_HOURS = 24

# Digest/body mismatch sampling (write-only-memory guard, ARCHITECTURE Failure
# modes). A digest that shares almost no vocabulary with the episode it
# summarizes is probably not grounded in it — a warning, not an error, and only
# on episodes rich enough for the ratio to mean something (a one-line episode
# has too few words to judge). Thresholds are deliberately lenient: a real
# digest names the referents that recur in the body, so grounding runs high; a
# fire here means genuine divergence.
DIGEST_GROUNDING_MIN = 0.2
_MISMATCH_MIN_DIGEST_WORDS = 8
_MISMATCH_MIN_BODY_WORDS = 40

# 4+ char function words that survive the length filter below and would inflate
# the grounding overlap without carrying meaning. Bilingual: shelves hold both.
_STOPWORDS = frozenset(
    """
    that this with from have been will would could should about there their these those then than
    what when which were where while your yours them they into over under only also such more most
    some each other than because after before again both same very much many just like
    это что как для при был была были есть нет они оно она все уже или так тот эта эти его нее них
    этот того чтобы который которая которые была были быть этом чем над под без про если
    """.split()
)

_WORD_RE = re.compile(r"\w{4,}", re.UNICODE)

# Overlap is measured on a stem prefix, not on whole tokens. Russian inflects
# by suffix, and exact matching therefore reads «партии» / «партия» and
# «студентом» / «студента» as unrelated words — on a Russian shelf the metric
# undercounts grounding systematically, which is not a property of the digest
# but of the language it is written in. Measured on the live shelf: an episode
# whose digest and body plainly share their subject scored 11%, and 11 of its
# digest words had same-root counterparts in the body that exact matching threw
# away. Five characters is enough to keep the root and drop the ending; it also
# merges English word families («refactor» / «refactoring»). The cost is
# occasional over-matching («конфиг» / «конфликт»), which is acceptable in a
# check whose whole question is "does this digest share *almost nothing* with
# its episode".
_STEM_LEN = 5


@dataclass
class Finding:
    level: str  # "error" | "warning" | "info"
    code: str
    path: str  # relative to the shelf root, or "" for shelf-wide
    detail: str
    fix: str = ""


@dataclass
class DoctorReport:
    findings: list[Finding]
    episodes_checked: int

    @property
    def ok(self) -> bool:
        return not any(f.level == "error" for f in self.findings)

    def as_dict(self) -> dict:
        return {
            "status": "ok",
            "healthy": self.ok,
            "episodes_checked": self.episodes_checked,
            "errors": sum(f.level == "error" for f in self.findings),
            "warnings": sum(f.level == "warning" for f in self.findings),
            "findings": [asdict(f) for f in self.findings],
        }


def _sections(body: str) -> list[str]:
    return re.findall(r"^\#\#[ \t]+(.+?)[ \t]*$", body, re.MULTILINE)


def _section_body(body: str, name: str) -> str | None:
    m = re.search(
        r"^\#\#[ \t]+" + re.escape(name) + r"[ \t]*$(.*?)(?=^\#\#[ \t]|\Z)",
        body,
        re.MULTILINE | re.DOTALL | re.IGNORECASE,
    )
    return m.group(1).strip() if m else None


def _content_words(text: str) -> set[str]:
    """Lowercased 4+ char word tokens, minus stopwords and pure digits.

    Digits really are dropped now: the docstring always said so, but the filter
    was missing, and years like ``2026`` counted as shared vocabulary in every
    episode on a dated shelf — overlap that says nothing about grounding.
    """
    return {
        w
        for w in (m.group(0).lower() for m in _WORD_RE.finditer(text))
        if w not in _STOPWORDS and not w.isdigit()
    }


def _stems(words: set[str]) -> set[str]:
    return {w[:_STEM_LEN] for w in words}


def _strip_section(body: str, name: str) -> str:
    """``body`` with the named ``## Section`` (heading + content) removed."""
    return re.sub(
        r"^\#\#[ \t]+" + re.escape(name) + r"[ \t]*$.*?(?=^\#\#[ \t]|\Z)",
        "",
        body,
        flags=re.MULTILINE | re.DOTALL | re.IGNORECASE,
    )


def _digest_body_grounding(digest: str, body: str) -> float | None:
    """Fraction of the digest's content words that also occur in the episode
    body (Digest section excluded). ``None`` when the episode is too small for
    the ratio to carry signal — the check abstains rather than guess."""
    digest_words = _content_words(digest)
    body_words = _content_words(_strip_section(body, "Digest"))
    if len(digest_words) < _MISMATCH_MIN_DIGEST_WORDS or len(body_words) < _MISMATCH_MIN_BODY_WORDS:
        return None
    body_stems = _stems(body_words)
    grounded = sum(1 for w in digest_words if w[:_STEM_LEN] in body_stems)
    return grounded / len(digest_words)


def _ledger_ids(path: Path) -> set[str]:
    return {cols[1] for _, cols in _ledger_rows(path) if len(cols) >= 2}


# shelf-spec v0 § 4.4. Kept here rather than imported: doctor is offline and
# dependency-free by design, and the spec's own validator is the second reader
# — the point is that both agree, not that one calls the other.
_LEDGER_COLUMNS = ("date", "episode_id", "mode", "approx_tokens_in", "digest_tokens", "notes")
_LEDGER_MODES = {"live", "import"}
_ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


def _ledger_rows(path: Path) -> list[tuple[int, list[str]]]:
    """``(line_number, cells)`` for every non-blank data row, 1-indexed."""
    if not path.is_file():
        return []
    return [
        (n, line.split("\t"))
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1)
        if n > 1 and line.strip()
    ]


def _parse_git_timestamp(line: str) -> datetime | None:
    """Parse git's ``%cI``, in both spellings, on every supported Python.

    Two spellings because git prints ``+00:00`` in one environment and a
    trailing ``Z`` in another (git 2.54 on the CI runner did the latter for a
    commit made in UTC; the development container did the former), and Python
    3.10's ``fromisoformat`` — the floor this package supports — rejects ``Z``.
    That combination is invisible from a UTC-offset machine: it turned every
    `doctor` run on 3.10 into a ``ValueError`` while the local suite stayed
    green, and the CI matrix is what caught it.

    Returns ``None`` rather than raising: a clock this check cannot read must
    cost the caller the *freshness* finding, not the whole diagnosis.
    """
    try:
        return datetime.fromisoformat(re.sub(r"Z$", "+00:00", line))
    except ValueError:
        return None


def _derived_layer_age_hours(root: Path, now: datetime) -> float | None:
    """How long since `ledger.tsv` was last written, in hours (``None`` if never).

    Read from git — the commit that last touched the file — rather than from its
    mtime, because a fresh clone or a checkout rewrites mtimes and would report
    a shelf abandoned in June as rendered a minute ago. mtime is the fallback
    for a shelf that is not a git repository at all, where it is the only clock
    there is.

    Deliberately *not* keyed on the bot's commit message: a shelf whose owner
    runs `rebuild` by hand has no bot and the same question ("is the accounting
    keeping up?") still applies to it.
    """
    stamp: datetime | None = None
    if (root / ".git").exists():
        proc = subprocess.run(
            ["git", "-C", str(root), "log", "-1", "--format=%cI", "--", "ledger.tsv"],
            capture_output=True,
            text=True,
        )
        line = proc.stdout.strip()
        if proc.returncode == 0 and line:
            stamp = _parse_git_timestamp(line)
    if stamp is None:
        ledger = root / "ledger.tsv"
        if not ledger.is_file():
            return None
        stamp = datetime.fromtimestamp(ledger.stat().st_mtime, tz=timezone.utc)
    return (now - stamp).total_seconds() / 3600


def _check_derived_freshness(
    root: Path, uncounted: list[str], now: datetime, stale_after_hours: float
) -> list[Finding]:
    """Tell "the renderer has not run yet" apart from "the renderer cannot run" (#89).

    The per-episode ``no-ledger-row`` warnings stay exactly as they are — they
    are right for the fresh case, and the advice attached to them ("do not fix
    this by hand") is right too. This adds the shelf-level finding the fresh
    case cannot produce: episodes uncounted *while the derived layer itself has
    not moved for a day* mean the accounting is not lagging, it is stopped.

    One finding for the shelf rather than one per episode: the diagnosis is
    about the renderer, and N copies of it would bury the episode list they are
    trying to deliver.
    """
    if not uncounted:
        return []
    age = _derived_layer_age_hours(root, now)
    if age is None or age < stale_after_hours:
        return []
    shown = ", ".join(sorted(uncounted)[:3])
    if len(uncounted) > 3:
        shown += f", … (+{len(uncounted) - 3})"
    return [
        Finding(
            "error",
            "derived-stale",
            "ledger.tsv",
            f"{len(uncounted)} episode(s) have no ledger row and the derived layer has not "
            f"been rewritten for {age:.0f}h — the renderer is not lagging, it is stopped: "
            f"{shown}",
            "check the derived-files job (a dead runner, a failing step) and rerun it; "
            "on a shelf without one, run `memshelf rebuild --shelf .`",
        )
    ]


def _check_ledger(root: Path) -> list[Finding]:
    """Validate the register itself, not just the episodes it points at.

    doctor used to read ``ledger.tsv`` only to cross-check episode ids, which
    left the file's own integrity unchecked — and the shelf rule "doctor clean
    ⇒ safe to push" then handed out a false guarantee twice in one day: 30
    duplicate rows passed with 0 errors (#63), and a ``span`` interval in the
    date column passed while ``shelf-spec validate`` rejected it (#65, #66).

    Coverage here is deliberately a superset of the spec validator's
    ``ledger-malformed``: everything it calls an error must be an error here
    too (ARCHITECTURE, finding-name divergence — names may differ, coverage and
    severity may not), plus the uniqueness of ``episode_id``, which is
    memshelf's own invariant on the register.
    """
    path = root / "ledger.tsv"
    if not path.is_file():
        return []
    out: list[Finding] = []
    lines = path.read_text(encoding="utf-8").splitlines()
    if not lines or lines[0].split("\t") != list(_LEDGER_COLUMNS):
        out.append(
            Finding(
                "error",
                "ledger-malformed",
                "ledger.tsv",
                "header line does not match '" + "\t".join(_LEDGER_COLUMNS) + "'",
                "regenerate the ledger: `memshelf rebuild --shelf .`",
            )
        )

    problems: list[str] = []
    seen: dict[str, int] = {}
    for n, cols in _ledger_rows(path):
        if len(cols) != len(_LEDGER_COLUMNS):
            problems.append(f"line {n}: expected {len(_LEDGER_COLUMNS)} tab-separated columns")
            continue
        date, episode_id, mode, tokens_in, digest_tokens, _notes = cols
        if not _ISO_DATE.fullmatch(date):
            problems.append(f"line {n}: date {date!r} is not YYYY-MM-DD")
        if mode not in _LEDGER_MODES:
            problems.append(f"line {n}: mode {mode!r} is not one of {sorted(_LEDGER_MODES)}")
        for col_name, value in (("approx_tokens_in", tokens_in), ("digest_tokens", digest_tokens)):
            if not value.isdigit():
                problems.append(f"line {n}: {col_name} {value!r} is not a non-negative integer")
        if episode_id in seen:
            problems.append(
                f"line {n}: episode_id {episode_id!r} already recorded on line {seen[episode_id]}"
            )
        else:
            seen[episode_id] = n

    if problems:
        out.append(
            Finding(
                "error",
                "ledger-malformed",
                "ledger.tsv",
                "; ".join(problems[:10]) + ("; ..." if len(problems) > 10 else ""),
                "fix the ledger per shelf-spec v0 § 4.4, or regenerate it: "
                "`memshelf rebuild --shelf .`",
            )
        )
    return out


# Frontmatter fields shelf-spec v0 § 5.2 marks REQUIRED. An episode missing any
# of them passes a naive read but fails the shelf's own advisory CI
# (shelf_validate → episode-frontmatter-invalid), so doctor must catch them
# first — "doctor clean" has to imply "validate green" (#56).
_REQUIRED_FRONTMATTER = ("id", "kind", "span", "tags", "approx_tokens")


def _check_episode(
    root: Path, rel: str, pack_patterns: list[tuple[str, str]] | None = None
) -> list[Finding]:
    out: list[Finding] = []
    text = (root / rel).read_text(encoding="utf-8")
    fields, body = parse_frontmatter(text)
    stem = Path(rel).stem

    if not fields:
        out.append(
            Finding(
                "error",
                "no-frontmatter",
                rel,
                "no '---'-fenced frontmatter block (shelf-spec v0 § 5.1)",
                "add the frontmatter block; re-shelving via the tool writes a valid one",
            )
        )
    else:
        for field_name in _REQUIRED_FRONTMATTER:
            if field_name not in fields:
                out.append(
                    Finding(
                        "error",
                        "frontmatter-missing-field",
                        rel,
                        f"missing required field {field_name!r} (shelf-spec v0 § 5.2)",
                        f"add {field_name!r} to the frontmatter",
                    )
                )
        approx = fields.get("approx_tokens")
        if approx is not None and not approx.lstrip("-").isdigit():
            out.append(
                Finding(
                    "error",
                    "bad-approx-tokens",
                    rel,
                    f"approx_tokens {approx!r} is not an integer (shelf-spec v0 § 5.2)",
                    "set approx_tokens to the in-window cost estimate (chars/4)",
                )
            )

    # shelf_validate treats id != stem as an error, so a lower severity here
    # would recreate the false "doctor clean, CI red" guarantee (#56).
    if fields.get("id") and fields["id"] != stem:
        out.append(
            Finding(
                "error",
                "id-mismatch",
                rel,
                f"frontmatter id {fields['id']!r} != filename {stem!r}",
                "align the id with the filename",
            )
        )

    kind = fields.get("kind")
    if kind is not None and kind not in CATEGORY_BY_KIND:
        out.append(
            Finding("error", "bad-kind", rel, f"kind {kind!r} is not valid", "set a valid kind")
        )
    elif kind is not None:
        for section in required_sections(kind):
            if _section_body(body, section) is None:
                out.append(
                    Finding(
                        "error",
                        "missing-section",
                        rel,
                        f"kind={kind} requires a ## {section} section",
                        f"add a ## {section} section",
                    )
                )
        if kind == "research" and len(_sections(body)) < 2:
            out.append(
                Finding(
                    "error",
                    "missing-section",
                    rel,
                    "kind=research needs Digest plus at least one body section",
                    "add a body section",
                )
            )

    digest = _section_body(body, "Digest")
    if digest is None:
        out.append(Finding("error", "no-digest", rel, "no ## Digest section", "add a Digest"))
    else:
        for err in validate_digest(digest).errors:
            out.append(Finding("error", f"digest-{err.code}", rel, err.message, "fix the digest"))
        grounding = _digest_body_grounding(digest, body)
        if grounding is not None and grounding < DIGEST_GROUNDING_MIN:
            out.append(
                Finding(
                    "warning",
                    "digest-body-mismatch",
                    rel,
                    f"digest shares ~{grounding:.0%} of its content words with the body; "
                    "it may not reflect the episode (write-only-memory guard)",
                    "rewrite the digest from the body, or confirm the episode is complete",
                )
            )

    secrets = scan(text)
    if not secrets.clean:
        out.append(
            Finding(
                "error",
                "secret-at-rest",
                rel,
                f"secret-shaped strings on disk ({secrets.summary()})",
                "redact and re-shelve the episode",
            )
        )

    if pack_patterns:
        policy_hits = scan_patterns(text, pack_patterns)
        if not policy_hits.clean:
            out.append(
                Finding(
                    "error",
                    "policy-pattern-at-rest",
                    rel,
                    f"POLICY.patterns-forbidden strings on disk ({policy_hits.summary()})",
                    "redact per the shelf's POLICY.patterns and re-shelve",
                )
            )
    return out


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def check_shelf(
    shelf_root: str | Path,
    *,
    check_remote: bool = False,
    remote_prober: Callable[[str], tuple[str, str]] | None = None,
    now: datetime | None = None,
    stale_after_hours: float = DERIVED_STALE_AFTER_HOURS,
) -> DoctorReport:
    """Diagnose a memory shelf. Offline and deterministic by default.

    ``check_remote`` enables the remote-visibility gate (MANIFEST principle 8):
    each configured git remote is probed and a *publicly visible* one is an
    error. The probe hits the network, which is why it is opt-in. ``remote_prober``
    overrides the default probe (one url -> ``(verdict, detail)``) — the seam the
    tests inject through.

    ``now`` and ``stale_after_hours`` are the seam for the one check that is not
    a pure function of the files (#89): whether the derived layer has stopped
    being rewritten. A guard about elapsed time has to be drivable from the test
    to fail at all — its whole content is what happens after a day.
    """
    from docshelf_mcp.core.shelf import Shelf

    root = Path(shelf_root).expanduser().resolve()
    shelf = Shelf(root)
    findings: list[Finding] = []

    # docshelf structural checks (rule/severity/path/detail/suggested_fix).
    for f in shelf.doctor():
        findings.append(Finding(f.severity, f.rule, f.path, f.detail, f.suggested_fix))

    # The shelf's machine-readable POLICY pack (#16). A malformed pack is a
    # warning — its good rules still run at rest, but the broken ones silently
    # aren't guarding, so surface them.
    pack = load_pattern_pack(root)
    for err in pack.errors:
        findings.append(
            Finding(
                "warning",
                "policy-pattern-invalid",
                "POLICY.patterns",
                err,
                "fix the rule; until then it does not guard this shelf",
            )
        )

    findings.extend(_check_ledger(root))

    ledger_ids = _ledger_ids(root / "ledger.tsv")
    seen: set[str] = set()
    uncounted: list[str] = []
    episodes = 0
    # Archived episodes (#15) are out of the INDEX, not out of the shelf: they
    # keep their ledger rows, so a doctor blind to `archive/` would report every
    # rolled-up episode as an orphan row — a rollup would look like corruption.
    archived_rel = [str(path.relative_to(root)) for path in archived_episodes(root)]
    for entry_rel in [e.relative_path for e in shelf.scan()] + archived_rel:
        episodes += 1
        rel = entry_rel
        stem = Path(rel).stem
        seen.add(stem)
        findings.extend(_check_episode(root, rel, pack.patterns))
        if stem not in ledger_ids:
            uncounted.append(rel)
            findings.append(
                Finding(
                    "warning",
                    "no-ledger-row",
                    rel,
                    "episode has no ledger.tsv row (its savings go uncounted)",
                    # The advice carries more weight than the finding (#80).
                    # This warning is the *normal* state one second after a
                    # shelve, on any branch — `main` included — and the wrong
                    # response to it, rebuilding and committing the derived files
                    # by hand, recreates the exact conflict class #58 removed.
                    # That happened on 2026-08-08: the docs excused the warning
                    # "on a branch", the reader was looking at main, and the
                    # merge conflict followed.
                    "expected right after a shelve, on any branch including main — "
                    "the derived files are rendered by `rebuild` (a bot, on shelves "
                    "that have one), so wait for it rather than committing them by "
                    "hand; on a shelf without a bot, run `memshelf rebuild --shelf .`",
                )
            )

    findings.extend(_check_derived_freshness(root, uncounted, now or _utc_now(), stale_after_hours))

    for orphan in sorted(ledger_ids - seen):
        findings.append(
            Finding(
                "warning",
                "orphan-ledger-row",
                "ledger.tsv",
                f"ledger row for {orphan!r} has no episode file",
                "remove the stale row",
            )
        )

    index = root / "INDEX.md"
    if index.is_file():
        tokens = len(index.read_text(encoding="utf-8")) // 4
        if tokens > INDEX_BUDGET_TOKENS:
            findings.append(
                Finding(
                    "warning",
                    "index-bloat",
                    "INDEX.md",
                    f"INDEX is ~{tokens} tokens (> {INDEX_BUDGET_TOKENS}); "
                    "recall pays this every session",
                    "roll up old episodes (ROADMAP M2)",
                )
            )

    if check_remote:
        findings.extend(_check_remotes(root, remote_prober))

    return DoctorReport(findings, episodes)


def _check_remotes(root: Path, prober: Callable[[str], tuple[str, str]] | None) -> list[Finding]:
    """The remote-visibility gate: a publicly visible remote fails the shelf."""
    probe = prober or remote_visibility
    remotes = configured_remotes(root)
    if not remotes:
        return [
            Finding(
                "info",
                "no-remote",
                "",
                "no git remote configured — the default private (git-local) posture",
                "",
            )
        ]
    out: list[Finding] = []
    for remote in remotes:
        verdict, detail = probe(remote.url)
        if verdict == PUBLIC:
            out.append(
                Finding(
                    "error",
                    "public-remote",
                    "",
                    f"remote {remote.name!r} is publicly visible: {detail}. A memory "
                    "shelf must never push conversation memory to a public remote",
                    "make the remote repository private, or remove the remote",
                )
            )
        elif verdict == PRIVATE:
            out.append(
                Finding("info", "remote-private", "", f"remote {remote.name!r}: {detail}", "")
            )
        else:  # UNKNOWN — network could not confirm; warn rather than block
            out.append(
                Finding(
                    "warning",
                    "remote-unverified",
                    "",
                    f"remote {remote.name!r} visibility could not be verified: {detail}",
                    "re-run the check with network access, or verify visibility by hand",
                )
            )
    return out
