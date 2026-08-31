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
from memshelf_mcp.core.episode import (
    CATEGORY_BY_KIND,
    MAX_DESCRIPTION_CHARS,
    required_sections,
)
from memshelf_mcp.core.frontmatter import parse_frontmatter
from memshelf_mcp.core.policy import load_pattern_pack
from memshelf_mcp.core.redact import scan, scan_patterns
from memshelf_mcp.core.remote import PRIVATE, PUBLIC, configured_remotes, remote_visibility
from memshelf_mcp.core.splits import local_split_dirs
from memshelf_mcp.core.stats import CHARS_PER_TOKEN

#: What INDEX costs before it lists a single episode: the H1, the recall-rule
#: preamble and the category headers. Measured at 135 tokens on the author's
#: shelf; rounded up, since a shelf may carry a longer preamble or a fourth
#: category.
INDEX_BASE_TOKENS = 200

#: What one listed episode may cost, as the sum of the parts a well-formed
#: entry is made of under the renderer that actually writes them (docshelf's
#: ``_render_entry``):
#:
#: * ~20 — the display title (~80 chars)
#: * ~27 — the link. docshelf prints the filename twice, once as the label and
#:   once inside the path, so about half of this is redundancy memshelf cannot
#:   remove from its side (docshelf-mcp#96). When that lands, this drops to ~67.
#: * ~30 — the description, held to ``MAX_DESCRIPTION_CHARS`` (120) by
#:   ``clamp_description`` on both the write and the render path.
#: *  ~3 — the list markup and the newline.
INDEX_TOKENS_PER_ENTRY = 80


def index_budget(entries: int) -> int:
    """What INDEX may cost on a shelf that lists ``entries`` episodes.

    A budget, not a ceiling — and the difference is the whole point. INDEX is
    O(entries) by construction: listing episodes is its job. A *fixed* limit
    therefore stops being reachable the moment a shelf grows past it, and the
    only mechanism that can lower the number afterwards is ``rollup``, which
    buys compliance by archiving live memory. The absolute 2500 this replaces
    did exactly that: it was derived from ROADMAP M2's "~10 KB" as if one
    character were one byte (on Cyrillic it is ~1.42, so the two clauses were
    never the same budget), it went unreachable at ~30 episodes, and on the
    author's own 113-episode shelf it demanded folding two thirds of the shelf
    to silence what was actually a formatting problem.

    Making it linear moves the check onto the one quantity formatting can
    control: the price of a single line. Over budget therefore means *fat
    entries*, never *many entries* — which is why ``index-bloat`` no longer
    proposes a rollup, and why the finding reports the per-entry cost rather
    than only the total.
    """
    return INDEX_BASE_TOKENS + INDEX_TOKENS_PER_ENTRY * max(entries, 0)


#: The two shapes docshelf's ``_render_entry`` produces for a non-split
#: document: ``- **Title** — desc — [`name.md`](url)`` with a URL resolver, and
#: the same line ending in a bare `` `name.md` `` without one.
_INDEX_ENTRY = re.compile(r"^- \*\*(?P<title>.*?)\*\*(?P<rest>.*)$")
_INDEX_LINK = re.compile(r"(?:\[`[^`]*`\]\([^)]*\)|`[^`]*\.md`)\s*$")


@dataclass
class IndexEntry:
    """One rendered INDEX line, split into the parts that can be over budget."""

    title: str
    description: str
    link: str


def index_entries(text: str) -> list[IndexEntry]:
    """Parse INDEX.md into its entries.

    The entry count comes from **the rendered file**, not from the episodes on
    disk, and that is deliberate. The budget is compared against the size of
    ``INDEX.md``, so counting episodes instead would take the numerator from
    one artifact and the denominator from another — and under #58 those two
    disagree by design, since the derived layer is rendered by a bot and is
    documented as lagging. Measured while reviewing this change: a shelf that
    had rolled up 30 of 41 episodes but not yet re-rendered reported a
    fabricated `index-bloat` of "~104 tokens per entry" against a budget for 10
    entries, for a file that still listed 41 at ~30 each. Reading both numbers
    off the same file makes the ratio true whatever state the renderer is in.
    """
    entries: list[IndexEntry] = []
    for line in text.splitlines():
        m = _INDEX_ENTRY.match(line)
        if not m:
            continue
        rest = m.group("rest")
        link_match = _INDEX_LINK.search(rest)
        link = link_match.group(0) if link_match else ""
        description = rest[: len(rest) - len(link)].strip().lstrip("—").strip()
        entries.append(IndexEntry(m.group("title"), description, link.strip()))
    return entries


#: The per-entry allowance, broken into the terms it was derived from, so an
#: overage can be attributed instead of guessed at.
_ENTRY_TERM_ALLOWANCE = {"title": 20, "description": 30, "link": 27}

_ENTRY_TERM_FIX = {
    "title": (
        "the titles are what is over: shorten `display_title` in the episodes' "
        "frontmatter (it is the one entry field with no cap, by design — naming "
        "an episode is the author's call) and re-render: `memshelf rebuild "
        "--shelf .`"
    ),
    "description": (
        "the descriptions are what is over. They are capped at "
        "{cap} chars on write and on render, so this means the derived layer "
        "predates the cap — re-render it: `memshelf rebuild --shelf .`"
    ),
    "link": (
        "the links are what is over, and that is not fixable from this side: "
        "docshelf renders each entry's filename twice, once as the label and "
        "once inside the path (docshelf-mcp#96). Nothing to do here until that "
        "lands."
    ),
}


def _index_overspend_fix(entries: list[IndexEntry]) -> str:
    """Name the entry term that is furthest over its share of the allowance."""
    totals = {
        "title": sum(len(e.title) for e in entries),
        "description": sum(len(e.description) for e in entries),
        "link": sum(len(e.link) for e in entries),
    }
    worst, overage = "", 0
    for term, chars in totals.items():
        over = chars // CHARS_PER_TOKEN - _ENTRY_TERM_ALLOWANCE[term] * len(entries)
        if over > overage:
            worst, overage = term, over
    if not worst:
        # Every term within its share, yet the total is over: the slack is in
        # markup or the preamble. Say that rather than blame a term at random.
        return (
            "no single entry field is over its share — the excess is in the "
            "preamble or the list markup. Check the shelf's INDEX preamble "
            "length in `.docshelf.json`."
        )
    return _ENTRY_TERM_FIX[worst].format(cap=MAX_DESCRIPTION_CHARS)


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


def _split_by_upstream(root: Path, uncounted: list[str]) -> tuple[list[str], list[str], str | None]:
    """Which uncounted episodes the renderer could even know about (#154 opt. 3).

    The bot renders what it can see — ``origin/<branch>``. An episode that
    lives only in this checkout (a local commit not yet pushed) is invisible
    to it *by construction*, so its missing ledger row says nothing about the
    renderer's health. main-memshelf#154 counts three consecutive false shelf
    verdicts, all with one shape: the measurement was taken on state the bot
    could not see; the third one (2026-08-21) was ``doctor`` itself calling
    ``derived-stale`` on an unpushed commit.

    Returns ``(visible, local_only, upstream)``. The upstream ref is read as
    this clone last fetched it — deliberately no fetch here: doctor reads, it
    does not go to the network — so right after a push the split is only as
    fresh as the last fetch, and the finding's advice says to fetch. Without
    an upstream (git-local shelf, plain dir) everything is «visible»: there is
    no renderer to be fair to, and the old behavior is the right one.
    """
    if not uncounted or not (root / ".git").exists():
        return uncounted, [], None
    proc = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"],
        capture_output=True,
        text=True,
    )
    upstream = proc.stdout.strip()
    if proc.returncode != 0 or not upstream:
        return uncounted, [], None
    visible: list[str] = []
    local_only: list[str] = []
    for rel in uncounted:
        seen = subprocess.run(
            ["git", "-C", str(root), "cat-file", "-e", f"{upstream}:{rel}"],
            capture_output=True,
            text=True,
        )
        (visible if seen.returncode == 0 else local_only).append(rel)
    return visible, local_only, upstream


def _check_unpushed_episodes(local_only: list[str], upstream: str | None) -> list[Finding]:
    """One shelf-level finding for the episodes only this checkout has (#154).

    Warning, not error: this is the normal state between a shelve and its
    push. It exists to be *named* — the reader deciding «is the bot alive»
    must see «the bot cannot see these» instead of an error blaming the
    renderer for state it was never shown.
    """
    if not local_only or upstream is None:
        return []
    shown = ", ".join(sorted(local_only)[:3])
    if len(local_only) > 3:
        shown += f", … (+{len(local_only) - 3})"
    return [
        Finding(
            "warning",
            "episode-unpushed",
            "ledger.tsv",
            f"{len(local_only)} episode(s) exist only in this checkout — {upstream}, as of "
            f"the last fetch, does not have them, so the render bot cannot see them and "
            f"their missing ledger rows say nothing about its health: {shown}",
            "push the shelf, `git fetch`, and re-run doctor; do not rebuild derived "
            "files by hand on a shelf with a render bot (#58)",
        )
    ]


def _check_local_splits(root: Path) -> list[Finding]:
    """Name the split directories that exist only in this working copy (#109).

    They are why docshelf's ``stale-index`` can be permanent: the check compares
    ``INDEX.md`` against a render of what is *on disk*, while the file it reads
    was rendered from what is *committed*. An untracked split directory puts a
    section block on one side of that comparison and not the other, so the
    warning survives every rebuild — and the fix it suggests, run locally,
    commits links to paths no other checkout has.

    Saying so here is the part memshelf owes the reader: docshelf's finding is
    true and its advice is wrong, and only this side knows why. ``shelve`` no
    longer creates these; this covers the shelves that already have them.
    """
    return [
        Finding(
            "warning",
            "local-split-dir",
            rel,
            "H2 split directory is not committed — it exists only in this working "
            "copy, so INDEX and search rendered here cannot match any other "
            "checkout (this is what makes `stale-index` permanent)",
            "run `memshelf prune-splits --shelf . --apply` from the shelf root — "
            "the sections are a copy, the episode file keeps all of them",
        )
        for rel in local_split_dirs(root)
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

    # #154 — the renderer is judged only on what it could see: episodes not on
    # the upstream ref are named separately, not blamed on the bot.
    visible, local_only, upstream = _split_by_upstream(root, uncounted)
    findings.extend(_check_derived_freshness(root, visible, now or _utc_now(), stale_after_hours))
    findings.extend(_check_unpushed_episodes(local_only, upstream))
    findings.extend(_check_local_splits(root))

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
        index_text = index.read_text(encoding="utf-8")
        tokens = len(index_text) // CHARS_PER_TOKEN
        # Counted off the rendered file, so the budget and the size it is
        # compared against always describe the same artifact — see
        # `index_entries`. Archived episodes carry no line and so earn no
        # allowance, which falls out of this for free.
        entries = index_entries(index_text)
        listed = len(entries)
        budget = index_budget(listed)
        if listed and tokens > budget:
            # One decimal, and the overage stated outright. Integer division
            # here prints "~80 tokens per entry, allowance 80" on a shelf two
            # tokens over — a finding that reads as self-contradictory and
            # invites the reader to dismiss it. A marginal case should read as
            # marginal.
            per_entry = (tokens - INDEX_BASE_TOKENS) / listed
            findings.append(
                Finding(
                    "warning",
                    "index-bloat",
                    "INDEX.md",
                    f"INDEX is ~{tokens} tokens against a budget of {budget} "
                    f"({INDEX_BASE_TOKENS} + {INDEX_TOKENS_PER_ENTRY}×{listed} listed), "
                    f"over by {tokens - budget}: ~{per_entry:.1f} tokens per entry, "
                    f"allowance {INDEX_TOKENS_PER_ENTRY}. "
                    "Recall pays this every session.",
                    # The old advice here was "roll up old episodes", and on a
                    # linear budget that is not merely unhelpful but wrong: a
                    # rollup removes entries and their allowance together, so
                    # it moves the total and the budget by nearly the same
                    # amount and leaves the per-entry price untouched.
                    #
                    # Naming a *fixed* culprit is the same mistake one level
                    # down. "Trim the descriptions" was wrong the moment
                    # descriptions became capped on both paths: what is left
                    # uncapped is the title, so the advice pointed at the one
                    # field that could no longer be the cause. Measure instead.
                    "the overage is per-entry, not per-episode — a rollup drops "
                    "the budget along with the lines and would not fix it. "
                    + _index_overspend_fix(entries),
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
