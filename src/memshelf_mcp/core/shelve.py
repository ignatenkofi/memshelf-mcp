"""The shelve orchestration: compose → redact → validate → write → commit.

One call turns an in-context topic into a durable, committed episode — the
three guarantees a prompt-only skill can't make (M0 annoyance log): the digest
contract (#3), the ledger row (#2), and a latin filename with a free-form
display title (#1). Since #58 the last two are delivered *through the episode*:
the ledger row and the display title live in the frontmatter, and
``memshelf rebuild`` renders ``ledger.tsv``/``.meta.json``/``INDEX.md`` from
there. Shelve writes and stages the episode alone, so two sessions closing two
topics no longer collide on four derived files. See ``docs/ARCHITECTURE.md`` →
MCP tool surface (``memshelf_shelve``) and design decision 3 (auto-commit).

The shelf must already be initialized (a docshelf shelf); ``memshelf init`` is
a later slice. ``docshelf_mcp`` is imported lazily so the pure Layer-2/3 modules
stay importable without it.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
from dataclasses import dataclass, field
from datetime import date as _date
from pathlib import Path

from memshelf_mcp.core.digest import ValidationResult, validate_digest
from memshelf_mcp.core.episode import CATEGORY_BY_KIND, Frontmatter, compose_episode
from memshelf_mcp.core.policy import load_pattern_pack
from memshelf_mcp.core.redact import RedactionReport, redact

LEDGER_HEADER = "date\tepisode_id\tmode\tapprox_tokens_in\tdigest_tokens\tnotes\n"


def _flatten_notes(notes: str) -> tuple[str, str | None]:
    """Make ``notes`` safe as the last TSV field.

    ``notes`` is free text from the caller and is the only ledger field that
    is not machine-generated. A tab in it silently shifts every later column
    (there is no later column today, but a reader counting fields still sees
    seven), and a newline forges an extra ledger row outright. shelf-spec v0
    § 4.4 forbids tabs in this field for exactly that reason.

    Returns the flattened text plus a warning when anything was replaced —
    a cosmetic field must never fail an otherwise-good shelve.
    """
    flattened = notes.replace("\t", " ").replace("\r\n", " ").replace("\n", " ").replace("\r", " ")
    if flattened == notes:
        return notes, None
    return flattened, (
        "ledger notes: tab/newline replaced with a space (shelf-spec v0 § 4.4 "
        "forbids tabs in this field; a newline would forge a ledger row)"
    )


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


def _git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True)


# Only reached when the environment has no git identity of its own — a fresh
# machine, a container, an ephemeral CI runner. Passed via `-c` so it never
# lands in the user's config and never shadows a real identity.
FALLBACK_IDENTITY = ("memshelf", "memshelf@localhost")


def git_commit(
    root: Path, message: str, *, paths: list[str] | None = None
) -> tuple[bool, str | None]:
    """Commit staged work under ``root``; return ``(committed, sha)``.

    ``paths`` narrows what gets staged. ``shelve`` passes the episode alone
    (#58): derived files are the bot's output on ``main``, and a shelve commit
    that carried a regenerated INDEX would be exactly the conflict the split
    removes. Callers that legitimately commit a whole state — ``init``,
    ``resolve`` — keep the default and stage everything.

    Durability rests on this commit, so a missing `user.name`/`user.email` must
    not cost the caller their episode: `git commit` refuses outright on a host
    without an identity, so retry once under ``FALLBACK_IDENTITY`` and raise
    only if that fails too.
    """
    if paths:
        _git(root, "add", "--", *paths)
    else:
        _git(root, "add", "-A")
    if _git(root, "diff", "--cached", "--quiet").returncode == 0:
        return False, None  # nothing staged — nothing to commit
    commit = _git(root, "commit", "-m", message)
    if commit.returncode != 0:
        name, email = FALLBACK_IDENTITY
        commit = _git(
            root, "-c", f"user.name={name}", "-c", f"user.email={email}", "commit", "-m", message
        )
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
    retain_until: str | None = None,
    extra_patterns: list[tuple[str, str]] | None = None,
    autocommit: bool = True,
) -> ShelveResult:
    """Shelve one episode into an initialized docshelf shelf.

    ``slug`` is the latin, date-prefixed filename/id; ``display_title`` is the
    optional free-form INDEX title (defaults to ``slug``). Redaction runs on the
    digest and every section first; the digest is then checked against the
    Layer-3 contract and a failure raises ``DigestContractError`` *before*
    anything is written. On success the episode is written through docshelf
    and — for a git shelf with ``autocommit`` — one commit is made, staging the
    episode only (never a push). Derived files are not touched: run
    ``memshelf rebuild`` (the shelf's bot does it on ``main``).
    """
    from docshelf_mcp.core.shelf import Shelf  # heavy dep, imported lazily

    root = Path(shelf_root).expanduser().resolve()
    sections = dict(sections or {})
    warnings: list[str] = []

    # The shelf's own machine-readable POLICY pack (#16) layers onto the builtin
    # credential shapes and any caller-supplied patterns. A malformed pack does
    # not block a shelve — the valid rules still apply and the errors surface as
    # warnings (and doctor flags them loudly).
    pack = load_pattern_pack(root)
    warnings += [f"POLICY.patterns: {e}" for e in pack.errors]
    combined_patterns = list(pack.patterns) + list(extra_patterns or [])

    # Layer 2 — redact digest + body before validation or any write.
    counts: dict[str, int] = {}

    def _scrub(text: str) -> str:
        out, rep = redact(text, extra_patterns=combined_patterns)
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

    # SPEC 5.2 makes `span` REQUIRED; a live episode is almost always
    # single-day, so default it to the episode date rather than reject (#56).
    # An explicit span (multi-day, or import backfill) always wins.
    shelved_on = date or _date.today().isoformat()
    span = span or shelved_on

    desc = description if description is not None else _first_sentence(digest)
    ledger_notes, notes_warning = _flatten_notes(notes)
    if notes_warning:
        warnings.append(notes_warning)

    # Compose (also enforces kind→required-sections via EpisodeError). Since
    # #58 the frontmatter carries everything the derived files need — the
    # episode is the source, ledger.tsv and .meta.json are output.
    frontmatter = Frontmatter(
        id=slug,
        kind=kind,
        span=span,
        tags=tuple(tags or ()),
        approx_tokens=approx_tokens,
        mode=mode,
        session=session,
        date=shelved_on,
        display_title=display_title,
        description=desc,
        notes=ledger_notes,
        retain_until=retain_until,
    )
    markdown = compose_episode(frontmatter, digest, sections)

    # Layer 1 — write through docshelf.
    category = CATEGORY_BY_KIND[kind]
    shelf = Shelf(root)

    # add_document slugifies its `title` into the filename, and docshelf's
    # slugify keeps Cyrillic — so a free-form title would become a Cyrillic
    # filename. Write with title=slug (latin name); the free-form title reaches
    # INDEX through .meta.json, which `memshelf rebuild` renders from the
    # frontmatter (annoyance #1, now on the derived side).
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
            rebuild_index=False,
        )
    finally:
        tmp.unlink(missing_ok=True)

    address = f"docs/{category}/{slug}.md"

    # The ledger row is no longer written here — it is what `rebuild` will
    # render from this episode's frontmatter. Returned anyway so the caller
    # sees the accounting it just created. digest_tokens = chars/4, the M0
    # accounting methodology.
    row = "\t".join(
        [shelved_on, slug, mode, str(approx_tokens), str(len(digest) // 4), ledger_notes]
    )

    # Auto-commit (design decision 3) — commit only, push stays configurable.
    # Only the episode is staged: derived files belong to the bot on `main`
    # (#58), and a shelve that committed a regenerated INDEX would recreate
    # the conflict class the split exists to remove.
    committed, sha = False, None
    if autocommit and (root / ".git").exists():
        committed, sha = git_commit(root, f"shelve: {slug}", paths=[address])

    return ShelveResult(
        address=address,
        display_title=display_title or slug,
        digest=digest,
        redaction=redaction,
        validation=validation,
        ledger_row=row,
        committed=committed,
        commit=sha,
        warnings=warnings,
    )
