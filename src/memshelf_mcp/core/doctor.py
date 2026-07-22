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
from dataclasses import asdict, dataclass
from pathlib import Path

from memshelf_mcp.core.digest import validate_digest
from memshelf_mcp.core.episode import CATEGORY_BY_KIND, required_sections
from memshelf_mcp.core.frontmatter import parse_frontmatter
from memshelf_mcp.core.redact import scan

# ROADMAP M2 keeps INDEX under ~10 KB; at chars/4 that is ~2500 tokens injected
# every session, so warn past it.
INDEX_BUDGET_TOKENS = 2500


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


def _check_episode(root: Path, rel: str) -> list[Finding]:
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
    return out


def check_shelf(shelf_root: str | Path) -> DoctorReport:
    from docshelf_mcp.core.shelf import Shelf

    root = Path(shelf_root).expanduser().resolve()
    shelf = Shelf(root)
    findings: list[Finding] = []

    # docshelf structural checks (rule/severity/path/detail/suggested_fix).
    for f in shelf.doctor():
        findings.append(Finding(f.severity, f.rule, f.path, f.detail, f.suggested_fix))

    ledger_ids = _ledger_ids(root / "ledger.tsv")
    seen: set[str] = set()
    episodes = 0
    for entry in shelf.scan():
        episodes += 1
        rel = entry.relative_path
        stem = Path(rel).stem
        seen.add(stem)
        findings.extend(_check_episode(root, rel))
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

    return DoctorReport(findings, episodes)
