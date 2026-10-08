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
import re
import subprocess
import tempfile
from dataclasses import dataclass, field
from datetime import date as _date
from pathlib import Path

from memshelf_mcp.core.archive import archive_root
from memshelf_mcp.core.digest import ValidationResult, validate_digest
from memshelf_mcp.core.episode import (
    APPROX_TOKENS_SOURCES,
    CATEGORY_BY_KIND,
    EpisodeError,
    Frontmatter,
    clamp_description,
    compose_episode,
    flatten,
)
from memshelf_mcp.core.frontmatter import parse_frontmatter
from memshelf_mcp.core.gitsync import (
    DEFAULT_RENDER_WAIT_S,
    SyncReport,
    await_render,
    hint_command,
    preflight,
    publish_branch,
    push_with_retry,
)
from memshelf_mcp.core.policy import load_pattern_pack
from memshelf_mcp.core.redact import RedactionReport, redact


def _render_wait(root: Path, requested: float | None) -> float:
    """Seconds to wait for the bot's render after a push (#157).

    An explicit number is obeyed. The default waits only where waiting can
    end in a render: the shelf has the bot, and the derived files in this
    clone no longer match its episodes — the same ``rebuild --check`` the bot
    runs. An amend that changes nothing derived gets no render, and waiting
    for one would cost the whole timeout for nothing.
    """
    if requested is not None:
        return requested
    if not (root / ".github" / "workflows" / "shelf-derived.yml").is_file():
        return 0.0
    from memshelf_mcp.core.rebuild import rebuild  # lazy: rebuild imports shelve

    try:
        drifted = rebuild(root, check=True).drifted
    except Exception:  # noqa: BLE001 — unknown drift: waiting is the safe side
        return DEFAULT_RENDER_WAIT_S
    return DEFAULT_RENDER_WAIT_S if drifted else 0.0


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


#: The date prefix the slug contract declares. Months 01–12, days 01–31: a
#: transposed «2026-31-08-…» must not pass a check that exists to keep the
#: shelf's natural sort chronological.
_DATED_SLUG = re.compile(r"^\d{4}-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])-")

#: An H2 heading line of an episode body, the pattern doctor (`_sections`)
#: and recall (`_slice_section`) read sections by.
_H2_LINE = re.compile(r"^\#\#[ \t]+(.+?)[ \t]*$")

#: A fenced code block's fence (CommonMark: up to three spaces of indent,
#: three or more backticks or tildes), and what follows it on the line.
_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")


def _closes(line: str, fence: str) -> bool:
    """Whether ``line`` closes the block ``fence`` opened: the same character,
    a run at least as long, and nothing else on the line."""
    m = _FENCE.match(line)
    return bool(
        m and m.group(1)[0] == fence[0] and len(m.group(1)) >= len(fence) and not m.group(2).strip()
    )


def _h2_headings(body: str) -> list[str]:
    """The H2 headings of an episode body, in file order (#205).

    A ``## …`` line inside a fenced code block is code, not a section: an
    episode quoting a template in its Decisions would otherwise move its own
    sections around on the next amend. A fence nothing closes is read as no
    fence at all: CommonMark runs it to the end of the body, but the headings
    after it are the episode's own sections, and the amend keeps them in
    place. doctor and recall have no fence rule and still count a fenced
    heading; only the order an amend keeps reads past it.
    """
    lines = body.splitlines()
    headings: list[str] = []
    i = 0
    while i < len(lines):
        m = _FENCE.match(lines[i])
        # A backtick fence's info string cannot hold a backtick (CommonMark).
        if m and not (m.group(1)[0] == "`" and "`" in m.group(2)):
            end = next((j for j in range(i + 1, len(lines)) if _closes(lines[j], m.group(1))), None)
            if end is not None:
                i = end + 1
                continue
        heading = _H2_LINE.match(lines[i])
        if heading:
            headings.append(heading.group(1))
        i += 1
    return headings


class SlugContractError(ValueError):
    """Raised when a *new* episode's slug has no date prefix (#101).

    The contract («latin, date-prefixed») was declared in this docstring and in
    the MCP schema, and enforced nowhere — an undated slug produced an undated
    file name, silently breaking the property everything downstream stands on:
    natural sort = chronological order (docs/ARCHITECTURE.md). Same class as
    the digest contract, so the same enforcement point: refuse before any
    write, with the fix in the message.

    Only new names are gated. An episode that already lives on the shelf under
    an undated name (shelved before this check) stays amendable — the contract
    guards what gets created, not access to what exists.
    """


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


class EpisodePathBlocked(FileExistsError):
    """Raised when docshelf refuses the write because a path is in its way.

    The case behind it is ``docs/<category>/<slug>/``, a directory named like
    the episode that is not a split docshelf wrote. Since docshelf-mcp#115
    (merged after 0.5.0) ``add_document`` refuses to write beside one with
    ``SplitDirConflictError``, a :class:`FileExistsError`. It is raised before
    anything is written and whatever ``overwrite`` says, so ``--amend`` does
    not clear it. Uncaught, it ended ``shelve`` in a traceback whose advice
    names docshelf's own kwargs (``title``, ``overwrite=True``). This one names
    what the caller can do: move the directory aside (#186).
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


def _find_archived_episode(root: Path, doc_stem: str) -> Path | None:
    """Where this slug lives in the rollup archive, if anywhere (#117).

    ``archive/docs`` mirrors ``docs`` category for category. A slug behind a
    rollup is still an episode — ``recall --id`` keeps answering from it — so
    the write-side lookup must see it too: without this, an amend of an
    archived episode read as a typo, and the error's advice («shelve it
    without --amend») was a recipe for one slug in two places.
    """
    for category in sorted(set(CATEGORY_BY_KIND.values())):
        candidate = archive_root(root) / "docs" / category / f"{doc_stem}.md"
        if candidate.is_file():
            return candidate
    return None


def _undo_kind_move(moved: Path, original: Path, text: bytes) -> None:
    """Put an episode a kind change moved back where it was, with ``text``."""
    if moved.exists():
        moved.replace(original)
    if not original.is_file() or original.read_bytes() != text:
        original.write_bytes(text)


def _first_sentence(text: str) -> str:
    """The digest's opening sentence, as a default description.

    No length cap of its own any more: ``clamp_description`` owns that number
    now, and owning it in one place is the point — two caps at 200 and 120 on
    two branches of the same expression is how the 200 came to apply to the
    branch nobody used.
    """
    text = text.strip()
    best = len(text)
    for sep in (". ", ".\n", "! ", "? "):
        i = text.find(sep)
        if i != -1:
            best = min(best, i + 1)
    return text[:best].strip()


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
    approx_tokens: int | None = None,
    approx_tokens_source: str | None = None,
    mode: str = "live",
    notes: str = "",
    date: str | None = None,
    retain_until: str | None = None,
    extra_patterns: list[tuple[str, str]] | None = None,
    autocommit: bool = True,
    amend: bool = False,
    sync: bool = True,
    push: bool = False,
    publish: bool = False,
    await_render_s: float | None = None,
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
    which a hand-edit of the file never is. An episode a rollup moved to
    ``archive/docs`` is amended there, in place (#117); changing its kind is
    refused, because that move would cross the archive boundary. Since #58 the ledger row is
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

    ``publish`` (default off, exclusive with ``push``) sends the shelve commit
    to origin as a **new branch** ``shelve/<slug>`` and reports a one-click
    compare link (#118): the mode for shelves whose ``main`` requires a PR,
    where a push rejection is policy — arriving after the ephemeral session
    already ended — and the episode must leave the container anyway. The
    local checkout never switches branches, so recall keeps answering from
    this clone; publication does not depend on any PR being opened.

    ``await_render_s`` (#157) — after a successful ``push``, wait up to this
    many seconds for the shelf bot's render and fast-forward onto it, so the
    clone ends the call level with the remote instead of one bot commit
    behind. ``None`` (default) means: ``DEFAULT_RENDER_WAIT_S`` when the shelf
    has the bot (``.github/workflows/shelf-derived.yml``) and the push left
    the derived files out of date, else no wait. ``0`` switches it off.
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
    if publish and not autocommit:
        raise ValueError(
            "publish=True needs autocommit=True — without the commit there is nothing to publish"
        )
    if await_render_s is not None and await_render_s < 0:
        raise ValueError("await_render_s must be >= 0 (0 switches the wait off)")
    if publish and push:
        raise ValueError(
            "push=True and publish=True are two destinations for one commit — "
            "pick one: push lands on the current branch, publish opens a new "
            "shelve/<slug> branch for a PR (#118)"
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
    archived_at = _find_archived_episode(root, doc_stem) if found_at is None else None
    moved_from: str | None = None
    amend_in_archive = False

    if amend:
        if found_at is None and archived_at is None:
            raise AmendTargetMissing(
                f"--amend: no episode {slug!r} on this shelf "
                f"(no {doc_stem}.md under {', '.join(_category_dirs())} "
                "or archive/docs/). "
                "Check the slug, or shelve it without --amend to create it."
            )
        if found_at is None:
            # #117 — the episode is behind a rollup. Amend it where it lives:
            # a rollup is not a deletion (recall by id still answers from the
            # archive), so the one guarded write path must reach it too. The
            # ledger row is untouched by construction — rebuild renders it
            # from this same frontmatter, archived or not.
            assert archived_at is not None
            archived_category = archived_at.parent.name
            if archived_category != category:
                # A kind change is a category move. In the live docs/ tree the
                # move is safe and performed below; out of (or within) the
                # archive it would silently resurrect or reshuffle what a
                # rollup deliberately put away — refuse with the reason
                # instead of guessing.
                raise EpisodeError(
                    f"--amend: {slug!r} is archived under "
                    f"kind-category {archived_category!r} "
                    f"(archive/docs/{archived_category}/{doc_stem}.md); "
                    f"amending it as kind={kind!r} would move it across the "
                    "archive boundary. Keep the original kind, or un-archive "
                    "it deliberately first — a silent move out of the archive "
                    "is exactly what this refusal prevents (#117)."
                )
            episode_path = archived_at
            amend_in_archive = True
        elif found_at != episode_path:
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
    elif archived_at is not None:
        # Not an amend, and the slug lives behind a rollup. Writing a fresh
        # docs/ file here is the «one slug in two places» the archive lookup
        # exists to prevent (#117) — the advice names the flag that works.
        raise EpisodeExists(
            f"episode {slug!r} is already on this shelf, archived at "
            f"{archived_at.relative_to(root).as_posix()}. Pass --amend (CLI) / "
            "amend=True to rewrite it in place, in the archive — a plain "
            "shelve would create a second episode under the same slug."
        )
    elif found_at is None and not _DATED_SLUG.match(slug):
        # #101 — the slug contract, enforced where the write happens. This
        # branch is reached only for a genuinely new name: an amend resolved
        # above (grandfathering pre-contract episodes), an existing slug is
        # handled below / by docshelf's own exists-guard.
        suggested = f"{date or _date.today().isoformat()}-{slug}"
        raise SlugContractError(
            f"slug {slug!r} has no date prefix; the contract is "
            f"YYYY-MM-DD-<latin-slug> (e.g. {suggested!r}). File names are "
            "date-prefixed slugs so natural sort stays chronological "
            "(docs/ARCHITECTURE.md). Nothing was written."
        )
    elif found_at is not None and found_at != episode_path:
        # Not an amend, and the slug is already on the shelf under another
        # category. docshelf's own guard cannot see this — it checks the target
        # path — so without this the write succeeds and leaves a duplicate.
        raise EpisodeExists(
            f"episode {slug!r} is already on this shelf at "
            f"{found_at.relative_to(root).as_posix()}, under a different kind. "
            "Pass --amend (CLI) / amend=True to rewrite it under "
            f"kind={kind!r} — the file is moved, so the shelf keeps one episode "
            "for the slug."
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

    # #79/#110/#113 — provenance of the one number the shelf's headline
    # metrics stand on, decided by the owner 2026-09-01: a source field, not a
    # schema change. The rule makes absence sayable: no number -> 0 with
    # source "unmeasured" (an unmeasured episode must not look like a measured
    # zero); a number without a stated source is an "estimate" — that is what
    # callers actually pass (M0 methodology, chars/4-grade); "measured" is a
    # claim the caller makes explicitly. The ledger keeps its six columns
    # (shelf-spec v0 § 4.4); the field rides in the frontmatter, which is
    # where rebuild renders the ledger from — data first, column when the
    # spec gets one.
    #
    # `unmeasured` is accepted from the caller too (#205): it is what an
    # episode shelved without a number carries, so a caller passing every
    # stored field back passes it — with no number or with the stored 0. With
    # any other number it is the mirror contradiction. Both are EpisodeErrors
    # (a ValueError), so the CLI refuses with the message instead of a
    # traceback.
    if approx_tokens is None:
        if approx_tokens_source not in (None, "", "unmeasured"):
            raise EpisodeError(
                f"approx_tokens_source={approx_tokens_source!r} without approx_tokens "
                "is a contradiction: a source asserts where a number came from, "
                "and there is no number"
            )
        approx_tokens = 0
        approx_tokens_source = "unmeasured"
    elif approx_tokens_source == "unmeasured" and approx_tokens != 0:
        raise EpisodeError(
            f"approx_tokens_source='unmeasured' with approx_tokens={approx_tokens} "
            "is a contradiction: 'unmeasured' marks the placeholder 0 of an episode "
            "shelved without a number. Pass the number with 'estimate' or "
            "'measured', or no number at all"
        )
    else:
        approx_tokens_source = approx_tokens_source or "estimate"
    if approx_tokens_source not in APPROX_TOKENS_SOURCES:
        raise ValueError(
            f"approx_tokens_source must be one of {APPROX_TOKENS_SOURCES}, "
            f"got {approx_tokens_source!r}"
        )

    # The shelve date (#170). An explicit `--date` always wins. Absent that,
    # `--amend` inherits the date already on the shelf instead of the wall
    # clock — rewriting an episode must not silently move its ledger row to
    # today, whether it lives in `docs/` or (#117) behind a rollup in
    # `archive/`. A brand-new episode instead takes its date from the slug's
    # own `YYYY-MM-DD-` prefix (the contract above already requires one for a
    # new name) rather than the machine's clock, so a session that crosses
    # midnight keeps id and date on the same day; only a legacy, undated slug
    # (grandfathered into `--amend` above) falls back to today().
    #
    # The episode an amend rewrites is read whatever `--date` says: #205
    # compares the description and the section order against it below, and
    # the repro behind #205 passed `--date`. Only the #170 inheritance of date
    # and span stays gated on its absence. The read is best effort: a byte
    # that is not UTF-8 reads as U+FFFD, not as a traceback, so an episode an
    # amend with `--date` used to overwrite without reading it still gets
    # rewritten from the call.
    stored_fields: dict[str, str] = {}
    stored_headings: list[str] = []
    if amend:
        existing_episode = found_at if found_at is not None else archived_at
        assert existing_episode is not None  # the amend guard above already required one
        stored_fields, stored_body = parse_frontmatter(
            existing_episode.read_text(encoding="utf-8", errors="replace")
        )
        stored_headings = _h2_headings(stored_body)
    existing_fields = stored_fields if date is None else {}

    if date is not None:
        shelved_on = date
    elif amend:
        shelved_on = existing_fields.get("date") or _date.today().isoformat()
    else:
        slug_date = slug[:10] if _DATED_SLUG.match(slug) else None
        shelved_on = slug_date or _date.today().isoformat()

    # SPEC 5.2 makes `span` REQUIRED; a live episode is almost always
    # single-day, so default it to the episode date rather than reject (#56).
    # An explicit span (multi-day, or import backfill) always wins; next, an
    # amend without an explicit `--date` keeps the existing episode's own
    # span too (#170) — otherwise a multi-day import span would quietly
    # collapse to one day on the next amend that only touches the digest.
    span = span or existing_fields.get("span") or shelved_on

    # One cap, applied to both branches. It used to sit inside
    # `_first_sentence`, which runs only when the caller supplies no
    # description — so the path callers actually take wrote whatever they were
    # given, straight into a line every future session reads.
    #
    # Not applied to the description an amend passes back unchanged (#205).
    # The cap decides what a shelve writes, and that value is written already:
    # capping it again cut a stored description past the cap to 119 characters
    # and left the caller no way to amend the digest and keep it. The episode
    # keeps the value; `rebuild` caps the INDEX line it renders from it, as it
    # does for every description on disk, and the warning says what that line
    # gets. "Unchanged" is judged with whitespace collapsed on both sides, and
    # the stored value is what gets written: a wrapper that trims what it read
    # (the issue's own description ends in a space) passes back the same
    # description, and it must not be cut for that.
    stored_description = stored_fields.get("description")
    if (
        amend
        and description is not None
        and stored_description is not None
        and flatten(description) == flatten(stored_description)
    ):
        desc = stored_description
        _, rendered = clamp_description(stored_description)
        if rendered:
            warnings.append(
                "description kept as stored, since --amend passed it back unchanged "
                f"(#205); rebuild renders its INDEX line through the cap: {rendered}"
            )
    else:
        desc, desc_warning = clamp_description(
            description if description is not None else _first_sentence(digest)
        )
        if desc_warning:
            warnings.append(desc_warning)
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
        approx_tokens_source=approx_tokens_source,
        mode=mode,
        session=session,
        date=shelved_on,
        display_title=display_title,
        description=desc,
        notes=ledger_notes,
        retain_until=retain_until,
    )
    markdown = compose_episode(frontmatter, digest, sections, order=stored_headings)

    # The kind change decided above is performed here, after everything that can
    # still refuse this shelve — redaction, the digest contract, and the section
    # contract inside `compose_episode`. A refused amend must leave the shelf
    # exactly as it found it; a move done at decision time would outlive the
    # refusal and strand the episode in the new category with its old text.
    moved_bytes = b""
    if moved_from is not None:
        episode_path.parent.mkdir(parents=True, exist_ok=True)
        (root / moved_from).rename(episode_path)
        moved_bytes = episode_path.read_bytes()

    # Layer 1 — the write. An archived episode is rewritten in place: docshelf
    # owns docs/, not archive/, and everything docshelf's write path adds on
    # top of the bytes — slugified naming, the exists-guard, the sidecar —
    # was either resolved above (the path exists and is the target) or is
    # rendered for the archive by `rebuild_archive_index`, not by a sidecar
    # snapshot here.
    if amend_in_archive:
        episode_path.write_text(markdown, encoding="utf-8")
        return _finish_shelve(
            root=root,
            episode_path=episode_path,
            slug=slug,
            display_title=display_title,
            digest=digest,
            redaction=redaction,
            validation=validation,
            shelved_on=shelved_on,
            mode=mode,
            approx_tokens=approx_tokens,
            ledger_notes=ledger_notes,
            warnings=warnings,
            amend=amend,
            moved_from=None,
            autocommit=autocommit,
            push=push,
            publish=publish,
            sync_report=sync_report,
            await_render_s=await_render_s,
        )

    # Write through docshelf.
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
                # No H2 split (#109). docshelf splits anything past 50 KiB into
                # sections beside the episode — and this function commits the
                # episode alone (`paths=staged` below), so those sections never
                # reach the repository. That single fact breaks the invariant
                # the whole derived split rests on: the derived layer stops
                # being a function of the *committed* episodes. Measured on the
                # author's shelf, one 53 KB episode: `INDEX.md` rendered here
                # carries a section block the bot's checkout cannot produce, so
                # `doctor` reports a `stale-index` that survives every rebuild
                # (and whose advice, followed, publishes links to paths no
                # checkout has); `search` answers with
                # `docs/sessions/<slug>/003-decisions.md`, an address that
                # exists on one laptop and that `recall --id` then rejects.
                #
                # Nothing memshelf offers reads those files: `recall --section`
                # slices the section out of the episode itself
                # (core/recall.py::_slice_section), which is why the whole file
                # is the source and the split was only ever a copy. Directories
                # left by older versions: `memshelf prune-splits`.
                split=False,
            )
        except BaseException as exc:
            # A shelve that does not write leaves the shelf as it found it,
            # whatever stopped the write — a refusal below, a permission error,
            # an interrupt. So a kind change moved above goes back, with the
            # bytes it had in case docshelf failed after writing the new text.
            if moved_from is not None:
                _undo_kind_move(episode_path, root / moved_from, moved_bytes)
            if isinstance(exc, DocumentExistsError):
                # docshelf's guard points at its own Python kwarg. Name the flag
                # the caller actually has — that gap is what #71 was filed about.
                raise EpisodeExists(
                    f"episode {slug!r} is already on this shelf. Pass --amend "
                    "(CLI) / amend=True to rewrite it in place — same slug, "
                    "redaction and the digest contract re-run; only the episode "
                    "file is rewritten, derived files come from `memshelf "
                    f"rebuild` or the shelf bot.\n{exc}"
                ) from exc
            if isinstance(exc, FileExistsError):
                # docshelf refused a path in the write's way: after 0.5.0, a
                # directory named like the episode that it did not write as
                # sections (SplitDirConflictError, docshelf-mcp#115). Caught by
                # the base class, because 0.5.0 has no such name to import.
                in_the_way = episode_path.parent / doc_stem
                what = (
                    f"{in_the_way.relative_to(root).as_posix()}/ is a directory "
                    "that docshelf did not write as split sections, and docshelf "
                    "will not add a document beside it"
                    if in_the_way.is_dir()
                    else "docshelf refused a path in the write's way"
                )
                raise EpisodePathBlocked(
                    f"episode {slug!r} was not written: {what}. Move it aside (a "
                    "new episode can take another slug instead) and shelve again; "
                    f"--amend (CLI) / amend=True does not clear this.\n{exc}"
                ) from exc
            raise
    finally:
        tmp.unlink(missing_ok=True)
        if sidecar_before is None:
            sidecar.unlink(missing_ok=True)
        elif sidecar.is_file() and sidecar.read_text(encoding="utf-8") != sidecar_before:
            sidecar.write_text(sidecar_before, encoding="utf-8")

    return _finish_shelve(
        root=root,
        episode_path=episode_path,
        slug=slug,
        display_title=display_title,
        digest=digest,
        redaction=redaction,
        validation=validation,
        shelved_on=shelved_on,
        mode=mode,
        approx_tokens=approx_tokens,
        ledger_notes=ledger_notes,
        warnings=warnings,
        amend=amend,
        moved_from=moved_from,
        autocommit=autocommit,
        push=push,
        publish=publish,
        sync_report=sync_report,
        await_render_s=await_render_s,
    )


def _finish_shelve(
    *,
    root: Path,
    episode_path: Path,
    slug: str,
    display_title: str | None,
    digest: str,
    redaction: RedactionReport,
    validation: ValidationResult,
    shelved_on: str,
    mode: str,
    approx_tokens: int,
    ledger_notes: str,
    warnings: list[str],
    amend: bool,
    moved_from: str | None,
    autocommit: bool,
    push: bool,
    publish: bool,
    sync_report: SyncReport | None,
    await_render_s: float | None = None,
) -> ShelveResult:
    """Everything after the episode's bytes are on disk: commit, push, report.

    Shared by the two write paths — through docshelf into ``docs/`` and in
    place into ``archive/docs/`` (#117) — so the guarantees stated here hold
    for both by construction, not by parallel maintenance.
    """
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
            # #157 — without this the clone ends every shelve one bot commit
            # behind, and the next doctor reports stale-index + no-ledger-row
            # for an episode that is fine.
            wait = _render_wait(root, await_render_s)
            if wait > 0:
                await_render(root, sync_report, timeout=wait)
                if sync_report.render_note:
                    warnings.append(f"bot render not pulled — {sync_report.render_note}")
    # #118 — the branch destination: for a shelf whose main requires a PR, the
    # episode must leave the container even though no push to main can. The
    # branch name comes from the file stem (slugified, ref-safe), the local
    # branch keeps the commit so recall still answers from this checkout.
    if publish:
        if not committed:
            warnings.append("publish: nothing was committed — nothing to publish")
        else:
            if sync_report is None:
                sync_report = SyncReport()
            publish_branch(root, sync_report, episode_path.stem)
    if (
        sync_report is not None
        and committed
        and not sync_report.pushed
        and not sync_report.published_branch
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
