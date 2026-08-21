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
from memshelf_mcp.core.gitsync import SyncReport, hint_command, preflight, push_with_retry
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


class AmendTargetMissing(FileNotFoundError):
    """Raised when ``amend=True`` names an episode that isn't on the shelf.

    Creating it instead would be the wrong kindness: the overwhelmingly likely
    cause is a mistyped slug, and a silent create leaves the author believing
    they fixed an episode that still carries the old text (#71).
    """


class EpisodeExists(FileExistsError):
    """Raised when a plain shelve would clobber an existing episode.

    docshelf's own guard says «pass overwrite=True» — advice the CLI could not
    take before #71. This one names the flag that exists.
    """


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
    amended: bool = False
    #: Set when an amend changed the episode's kind and therefore its category:
    #: the old path, relative to the shelf root. The caller needs it because the
    #: episode's address changed under them — and because "the file moved" is
    #: not visible in `address` alone.
    moved_from: str | None = None
    #: What the sync around this shelve did (#108): pulled-count, retry count,
    #: post-push sha or the executable catch-up hint. None when sync was
    #: disabled or the shelf is not a git repository.
    sync: SyncReport | None = None


def _category_dirs() -> list[str]:
    """The shelf's episode directories, in a stable order for error messages."""
    return [f"docs/{category}" for category in sorted(set(CATEGORY_BY_KIND.values()))]


def _find_episode(root: Path, doc_stem: str) -> Path | None:
    """Where this slug already lives on the shelf, whatever kind it was shelved as.

    Returns the first match in ``CATEGORY_BY_KIND`` order. A shelf with the same
    stem in two categories is already broken (two ledger rows for one slug), and
    picking a winner here is not the place to fix that — doctor's ledger checks
    are.
    """
    for category in sorted(set(CATEGORY_BY_KIND.values())):
        candidate = root / "docs" / category / f"{doc_stem}.md"
        if candidate.is_file():
            return candidate
    return None


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
    amend: bool = False,
    sync: bool = True,
    push: bool = False,
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

    ``amend`` rewrites an episode that is already on the shelf, under the same
    slug (#71). The whole pipeline runs again — redaction, the digest contract,
    composition — so an amended episode is exactly as guarded as a fresh one,
    which a hand-edit of the file never is. Since #58 the ledger row is
    rendered by ``rebuild`` from the frontmatter, so rewriting the one episode
    recomputes the one row rather than adding a second: the reason a new slug
    was the wrong workaround disappears with it.

    ``sync`` (default on) fetches and fast-forwards the clone *before anything
    is written* (#108): in bot-draws mode the clone is behind origin by
    construction, and a dirty tracked tree or a diverged branch refuses the
    shelve with the fix in the message instead of silently writing onto a
    stale base. ``push`` (default off) pushes the shelve commit and, on a
    rejection, rebases and retries exactly once; the result then carries the
    post-push sha. Either way ``result.sync`` states the outcome explicitly —
    a clean run says «pulled 0, retries 0» rather than staying silent.
    """
    from docshelf_mcp.core.shelf import DocumentExistsError, Shelf  # heavy dep, lazy
    from docshelf_mcp.core.slugify import slugify

    root = Path(shelf_root).expanduser().resolve()
    sections = dict(sections or {})
    warnings: list[str] = []

    if push and not autocommit:
        raise ValueError(
            "push=True needs autocommit=True — without the commit there is nothing to push"
        )

    # #108 — sync the clone before anything is written. In bot-draws mode the
    # clone is behind origin by construction (the bot commits derived files in
    # response to every push), so writing first and discovering the lag at
    # `git push` is the normal path to a rejected push, not an edge case
    # (main-memshelf#146). DirtyShelfError / SyncDivergedError propagate:
    # both are states where writing first loses work.
    sync_report: SyncReport | None = None
    if sync and (root / ".git").exists():
        sync_report = preflight(root)
        if sync_report.skipped_reason:
            warnings.append(sync_report.line())

    # Resolve the target before any work: an amend of a slug that isn't there
    # must cost nothing and say so. The path is derived exactly the way
    # docshelf will derive it (add_document gets title=slug and no `slug=`, so
    # the stem is slugify(title, max_len=80) or "document"), and this one
    # derivation then feeds the amend guard, the returned address and git
    # staging. Держать вторую — как раз то, из-за чего `address` мог назвать
    # несуществующий файл: он собирался из сырого слага, пока docshelf писал в
    # нормализованный. Слаг вида «2026-08-03-Проверка Слага» уезжал в
    # docs/topics/2026-08-03-проверка-слага.md, а вызывающему возвращался
    # исходный путь, и `git add` по нему тихо не находил ничего.
    category = CATEGORY_BY_KIND[kind]
    doc_stem = slugify(slug, max_len=80) or "document"
    episode_path = root / "docs" / category / f"{doc_stem}.md"

    # A slug identifies an episode on the whole shelf, not within one category —
    # it is the ledger key, so the same slug living in two categories is two
    # ledger rows for one episode. The lookup therefore spans categories, and
    # both branches below need that answer (#90).
    found_at = _find_episode(root, doc_stem)
    moved_from: str | None = None

    if amend:
        if found_at is None:
            raise AmendTargetMissing(
                f"--amend: no episode {slug!r} on this shelf "
                f"(no {doc_stem}.md under {', '.join(_category_dirs())}). "
                "Check the slug, or shelve it without --amend to create it."
            )
        if found_at != episode_path:
            # A kind change *is* a category change: the field decides which
            # sections doctor demands, so correcting a wrong kind is one of the
            # few things amend is genuinely needed for. Refusing here (which is
            # what resolving the target from the new kind alone used to do) left
            # only a manual path — shelve without --amend, then delete the old
            # file by hand — and a caller who skipped the second half ended up
            # with one episode in two categories.
            #
            # Decided here, performed at the write below: everything between is
            # redaction and the digest contract, and both can refuse the shelve.
            # A move done here would survive that refusal — the episode would
            # land in the new category still carrying its old text, while the
            # caller is told nothing happened.
            moved_from = found_at.relative_to(root).as_posix()
    elif found_at is not None and found_at != episode_path:
        # Not an amend, and the slug is already on the shelf under another
        # category. docshelf's own guard cannot see this — it checks the target
        # path — so without this the write succeeds and leaves a duplicate.
        raise EpisodeExists(
            f"episode {slug!r} is already on this shelf at "
            f"{found_at.relative_to(root).as_posix()}, under a different kind. "
            "Pass --amend (CLI) / amend=True to rewrite it under "
            f"kind={kind!r} — the file is moved, so the shelf keeps one episode "
            "and one ledger row."
        )

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

    # The kind change decided above is performed here, after everything that can
    # still refuse this shelve — redaction, the digest contract, and the section
    # contract inside `compose_episode`. A refused amend must leave the shelf
    # exactly as it found it; a move done at decision time would outlive the
    # refusal and strand the episode in the new category with its old text.
    if moved_from is not None:
        episode_path.parent.mkdir(parents=True, exist_ok=True)
        (root / moved_from).rename(episode_path)

    # Layer 1 — write through docshelf.
    shelf = Shelf(root)

    # add_document slugifies its `title` into the filename, and docshelf's
    # slugify keeps Cyrillic — so a free-form title would become a Cyrillic
    # filename. Write with title=slug (latin name); the free-form title reaches
    # INDEX through .meta.json, which `memshelf rebuild` renders from the
    # frontmatter (annoyance #1, now on the derived side).
    fd, tmp_name = tempfile.mkstemp(suffix=".md")
    os.close(fd)
    tmp = Path(tmp_name)
    # add_document also records title/description in the category's sidecar for
    # docshelf's indexer — a derived file the shelve contract promises not to
    # touch (#58, #69). Snapshot it and put it back: on `main` the bot renders
    # it, on a branch it is supposed to lag, and the version add_document would
    # leave behind is worse than either (it writes title=<slug>, since
    # display_title only reaches the sidecar through `rebuild`).
    sidecar = root / "docs" / category / ".meta.json"
    sidecar_before = sidecar.read_text(encoding="utf-8") if sidecar.is_file() else None
    try:
        tmp.write_text(markdown, encoding="utf-8")
        try:
            shelf.add_document(
                tmp,
                category=category,
                title=slug,
                description=desc,
                rebuild_index=False,
                overwrite=amend,
            )
        except DocumentExistsError as exc:
            # docshelf's guard points at its own Python kwarg. Name the flag the
            # caller actually has — that gap is what #71 was filed about.
            raise EpisodeExists(
                f"episode {slug!r} is already on this shelf. Pass --amend "
                "(CLI) / amend=True to rewrite it in place — same slug, one "
                f"ledger row, redaction and the digest contract re-run.\n{exc}"
            ) from exc
    finally:
        tmp.unlink(missing_ok=True)
        if sidecar_before is None:
            sidecar.unlink(missing_ok=True)
        elif sidecar.is_file() and sidecar.read_text(encoding="utf-8") != sidecar_before:
            sidecar.write_text(sidecar_before, encoding="utf-8")

    address = episode_path.relative_to(root).as_posix()

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
        subject = f"shelve: {slug} (amend)" if amend else f"shelve: {slug}"
        # A move needs both ends staged, or the commit carries the new file and
        # leaves the old one in the tree — the same duplicate the move exists to
        # prevent, only now recorded in history.
        staged = [address] if moved_from is None else [address, moved_from]
        committed, sha = git_commit(root, subject, paths=staged)

    # #108 — the push fork. On a rejection push_with_retry rebases and retries
    # exactly once; a second rejection surfaces git's words (PushRejectedError
    # propagates — by then the episode is written and committed locally).
    if push:
        if not committed:
            warnings.append("push: nothing was committed — nothing to push")
        else:
            if sync_report is None:
                sync_report = SyncReport()
            push_with_retry(root, sync_report)
    if (
        sync_report is not None
        and committed
        and not sync_report.pushed
        and sync_report.remote
        and sync_report.branch
    ):
        # The commit stayed local — hand the caller the executable catch-up
        # instead of a sha that the next rebase will rewrite (#146).
        sync_report.hint = hint_command(root, sync_report.remote, sync_report.branch)

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
        amended=amend,
        moved_from=moved_from,
        sync=sync_report,
    )
