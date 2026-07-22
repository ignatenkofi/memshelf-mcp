"""The shelve orchestration: compose → redact → validate → write → ledger →
commit.

One call turns an in-context topic into a durable, indexed, committed episode —
the three guarantees a prompt-only skill can't make (M0 annoyance log): the
digest contract (#3), the ledger row (#2), and a latin filename with a
free-form display title (#1). See ``docs/ARCHITECTURE.md`` → MCP tool surface
(``memshelf_shelve``) and design decision 3 (auto-commit).

The shelf must already be initialized (a docshelf shelf); ``memshelf init`` is
a later slice. ``docshelf_mcp`` is imported lazily so the pure Layer-2/3 modules
stay importable without it.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from dataclasses import dataclass, field
from datetime import date as _date
from pathlib import Path

from memshelf_mcp.core.digest import ValidationResult, validate_digest
from memshelf_mcp.core.episode import CATEGORY_BY_KIND, Frontmatter, compose_episode
from memshelf_mcp.core.redact import RedactionReport, redact

LEDGER_HEADER = "date\tepisode_id\tmode\tapprox_tokens_in\tdigest_tokens\tnotes\n"


class DigestContractError(ValueError):
    """Raised when the digest fails the Layer-3 contract — carries the full
    validation result so the caller can show exactly what to fix."""

    def __init__(self, result: ValidationResult) -> None:
        self.result = result
        super().__init__("digest rejected:\n" + result.report())


@dataclass
class ShelveResult:
    address: str  # episode path relative to the shelf root
    display_title: str
    digest: str
    redaction: RedactionReport
    validation: ValidationResult
    ledger_row: str
    committed: bool
    commit: str | None = None
    warnings: list[str] = field(default_factory=list)


def _first_sentence(text: str, cap: int = 200) -> str:
    text = text.strip()
    best = len(text)
    for sep in (". ", ".\n", "! ", "? "):
        i = text.find(sep)
        if i != -1:
            best = min(best, i + 1)
    return text[:best].strip()[:cap]


def _append_ledger(path: Path, row: str) -> None:
    if not path.exists():
        path.write_text(LEDGER_HEADER, encoding="utf-8")
    with path.open("a", encoding="utf-8") as fh:
        fh.write(row + "\n")


def _set_display_title(
    root: Path, category: str, filename: str, title: str, description: str
) -> None:
    # Same on-disk shape as docshelf's own .meta.json override, so the indexer
    # picks up the free-form title while the file keeps its latin slug name.
    meta = root / "docs" / category / ".meta.json"
    data: dict = {}
    if meta.is_file():
        try:
            data = json.loads(meta.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            data = {}
    data[filename] = {"title": title, "description": description}
    meta.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True)


def _git_commit(root: Path, message: str) -> tuple[bool, str | None]:
    _git(root, "add", "-A")
    if _git(root, "diff", "--cached", "--quiet").returncode == 0:
        return False, None  # nothing staged — nothing to commit
    commit = _git(root, "commit", "-m", message)
    if commit.returncode != 0:
        raise RuntimeError(f"git commit failed: {commit.stderr.strip()}")
    return True, _git(root, "rev-parse", "HEAD").stdout.strip()


def shelve(
    shelf_root: str | Path,
    *,
    slug: str,
    kind: str,
    digest: str,
    sections: dict[str, str] | None = None,
    display_title: str | None = None,
    description: str | None = None,
    tags: list[str] | None = None,
    span: str | None = None,
    session: str | None = None,
    approx_tokens: int = 0,
    mode: str = "live",
    notes: str = "",
    date: str | None = None,
    extra_patterns: list[tuple[str, str]] | None = None,
    autocommit: bool = True,
) -> ShelveResult:
    """Shelve one episode into an initialized docshelf shelf.

    ``slug`` is the latin, date-prefixed filename/id; ``display_title`` is the
    optional free-form INDEX title (defaults to ``slug``). Redaction runs on the
    digest and every section first; the digest is then checked against the
    Layer-3 contract and a failure raises ``DigestContractError`` *before*
    anything is written. On success the episode is written through docshelf, a
    ledger row is appended, and — for a git shelf with ``autocommit`` — one
    commit is made (never a push).
    """
    from docshelf_mcp.core.shelf import Shelf  # heavy dep, imported lazily

    root = Path(shelf_root).expanduser().resolve()
    sections = dict(sections or {})

    # Layer 2 — redact digest + body before validation or any write.
    counts: dict[str, int] = {}

    def _scrub(text: str) -> str:
        out, rep = redact(text, extra_patterns=extra_patterns)
        for k, n in rep.counts.items():
            counts[k] = counts.get(k, 0) + n
        return out

    digest = _scrub(digest.strip())
    sections = {name: _scrub(body) for name, body in sections.items()}
    redaction = RedactionReport(counts)

    # Layer 3 — enforce the digest contract; reject before writing.
    validation = validate_digest(digest)
    if not validation.ok:
        raise DigestContractError(validation)

    # Compose (also enforces kind→required-sections via EpisodeError).
    frontmatter = Frontmatter(
        id=slug,
        kind=kind,
        span=span,
        tags=tuple(tags or ()),
        approx_tokens=approx_tokens,
        mode=mode,
        session=session,
    )
    markdown = compose_episode(frontmatter, digest, sections)

    # Layer 1 — write through docshelf.
    category = CATEGORY_BY_KIND[kind]
    desc = description if description is not None else _first_sentence(digest)
    shelf = Shelf(root)

    # add_document slugifies its `title` into the filename, and docshelf's
    # slugify keeps Cyrillic — so a free-form title would become a Cyrillic
    # filename. Write with title=slug (latin name), then override the display
    # title in .meta.json when one is given (annoyance #1).
    needs_override = bool(display_title) and display_title != slug

    fd, tmp_name = tempfile.mkstemp(suffix=".md")
    os.close(fd)
    tmp = Path(tmp_name)
    try:
        tmp.write_text(markdown, encoding="utf-8")
        shelf.add_document(
            tmp,
            category=category,
            title=slug,
            description=desc,
            rebuild_index=not needs_override,
        )
        if needs_override:
            _set_display_title(root, category, f"{slug}.md", display_title, desc)
            shelf.rebuild_index()
    finally:
        tmp.unlink(missing_ok=True)

    address = f"docs/{category}/{slug}.md"

    # Ledger — one row per shelve (annoyance #2). digest_tokens = chars/4, the
    # M0 accounting methodology.
    row = "\t".join(
        [
            date or _date.today().isoformat(),
            slug,
            mode,
            str(approx_tokens),
            str(len(digest) // 4),
            notes,
        ]
    )
    _append_ledger(root / "ledger.tsv", row)

    # Auto-commit (design decision 3) — commit only, push stays configurable.
    committed, sha = False, None
    if autocommit and (root / ".git").exists():
        committed, sha = _git_commit(root, f"shelve: {slug}")

    return ShelveResult(
        address=address,
        display_title=display_title or slug,
        digest=digest,
        redaction=redaction,
        validation=validation,
        ledger_row=row,
        committed=committed,
        commit=sha,
    )
