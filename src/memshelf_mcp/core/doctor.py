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
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path

from memshelf_mcp.core.digest import validate_digest
from memshelf_mcp.core.episode import CATEGORY_BY_KIND, required_sections
from memshelf_mcp.core.frontmatter import parse_frontmatter
from memshelf_mcp.core.policy import load_pattern_pack
from memshelf_mcp.core.redact import scan, scan_patterns
from memshelf_mcp.core.remote import PRIVATE, PUBLIC, configured_remotes, remote_visibility

# ROADMAP M2 keeps INDEX under ~10 KB; at chars/4 that is ~2500 tokens injected
# every session, so warn past it.
INDEX_BUDGET_TOKENS = 2500

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
    """Lowercased 4+ char word tokens, minus stopwords and pure digits."""
    return {w for w in (m.group(0).lower() for m in _WORD_RE.finditer(text)) if w not in _STOPWORDS}


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
    return len(digest_words & body_words) / len(digest_words)


def _ledger_ids(path: Path) -> set[str]:
    ids: set[str] = set()
    if not path.is_file():
        return ids
    for i, line in enumerate(path.read_text(encoding="utf-8").splitlines()):
        if i == 0 or not line.strip():
            continue
        cols = line.split("\t")
        if len(cols) >= 2:
            ids.add(cols[1])
    return ids


def _check_episode(
    root: Path, rel: str, pack_patterns: list[tuple[str, str]] | None = None
) -> list[Finding]:
    out: list[Finding] = []
    text = (root / rel).read_text(encoding="utf-8")
    fields, body = parse_frontmatter(text)
    stem = Path(rel).stem

    if fields.get("id") and fields["id"] != stem:
        out.append(
            Finding(
                "warning",
                "id-mismatch",
                rel,
                f"frontmatter id {fields['id']!r} != filename {stem!r}",
                "align the id with the filename",
            )
        )

    kind = fields.get("kind")
    if kind not in CATEGORY_BY_KIND:
        out.append(
            Finding("error", "bad-kind", rel, f"kind {kind!r} is not valid", "set a valid kind")
        )
    else:
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


def check_shelf(
    shelf_root: str | Path,
    *,
    check_remote: bool = False,
    remote_prober: Callable[[str], tuple[str, str]] | None = None,
) -> DoctorReport:
    """Diagnose a memory shelf. Offline and deterministic by default.

    ``check_remote`` enables the remote-visibility gate (MANIFEST principle 8):
    each configured git remote is probed and a *publicly visible* one is an
    error. The probe hits the network, which is why it is opt-in. ``remote_prober``
    overrides the default probe (one url -> ``(verdict, detail)``) — the seam the
    tests inject through.
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

    ledger_ids = _ledger_ids(root / "ledger.tsv")
    seen: set[str] = set()
    episodes = 0
    for entry in shelf.scan():
        episodes += 1
        rel = entry.relative_path
        stem = Path(rel).stem
        seen.add(stem)
        findings.extend(_check_episode(root, rel, pack.patterns))
        if stem not in ledger_ids:
            findings.append(
                Finding(
                    "warning",
                    "no-ledger-row",
                    rel,
                    "episode has no ledger.tsv row (its savings go uncounted)",
                    "re-shelve via the tool, or add a ledger row",
                )
            )

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
