"""Typed entry points wrapping the core for the MCP server and the CLI.

``ShelveInput`` is the validated surface; ``run_shelve`` calls the core and
returns a JSON-serializable dict. Keeping this here — not in ``server.py`` —
lets the CLI and the tests reuse it without importing the MCP SDK.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from memshelf_mcp.core.advisor import (
    DEFAULT_BUDGET_TOKENS,
    STALE_AFTER_TURNS,
    Occupant,
    advise,
)
from memshelf_mcp.core.archive import purge as purge_shelf
from memshelf_mcp.core.archive import rollup as rollup_shelf
from memshelf_mcp.core.digest import validate_digest
from memshelf_mcp.core.doctor import DERIVED_STALE_AFTER_HOURS, check_shelf
from memshelf_mcp.core.importer import discover as import_discover
from memshelf_mcp.core.importer import extract as import_extract
from memshelf_mcp.core.init import init_shelf
from memshelf_mcp.core.rebuild import adopt as adopt_shelf
from memshelf_mcp.core.rebuild import rebuild
from memshelf_mcp.core.recall import read_index, recall, search
from memshelf_mcp.core.resolve import resolve_shelf
from memshelf_mcp.core.shelve import shelve
from memshelf_mcp.core.splits import prune_split_dirs
from memshelf_mcp.core.stats import banner, compute_stats, episode_mass

SHELF_PATH_ENV = "MEMSHELF_SHELF_PATH"

_SHELF_PATH_DESCRIPTION = (
    "Path to an initialized memory shelf. Optional: when omitted, the shelf named "
    f"by ${SHELF_PATH_ENV} is used. Pass it explicitly to address a different shelf "
    "than that default."
)


def default_shelf_path() -> str:
    """The shelf a call falls back to when it names none (empty when unset).

    Read per call, not at import: a host may set the variable after the process
    starts, and the tests flip it between cases.
    """
    return os.environ.get(SHELF_PATH_ENV, "").strip()


class ShelfScopedInput(BaseModel):
    """Base for every input addressed at one shelf.

    An explicit ``shelf_path`` always wins; ``$MEMSHELF_SHELF_PATH`` fills in when
    the call names none. That split is what a packaged host needs: Claude Desktop
    can only configure an extension through the environment, so the bundle carries
    one *global* shelf, and a project overrides it by naming its own path in the
    call. Neither present is an error with a fixable message, not a path of ``''``.
    """

    # An unknown key is a failed call, not a fussy one (#104). pydantic's default
    # is `extra='ignore'`, and `_accept_flat_arguments` wraps a flat call into
    # `params` before validation, so a misspelled flag used to be dropped in
    # silence — and the tool then ran the flag's *other* behaviour and reported
    # success. `memshelf_rebuild(chek=True)` wrote instead of checking. Refusing
    # the call is the only outcome that cannot be mistaken for the one asked for;
    # the error names the key, so the typo is one line away from fixed.
    model_config = ConfigDict(extra="forbid")

    shelf_path: str = Field(default="", description=_SHELF_PATH_DESCRIPTION)

    @model_validator(mode="after")
    def _resolve_shelf_path(self) -> ShelfScopedInput:
        resolved = self.shelf_path.strip() or default_shelf_path()
        if not resolved:
            raise ValueError(
                "no shelf to work on: pass shelf_path, or set "
                f"{SHELF_PATH_ENV} to the shelf directory in the host's configuration."
            )
        if resolved != self.shelf_path:
            self.shelf_path = resolved
        return self


class ShelveInput(ShelfScopedInput):
    slug: str = Field(
        description="Latin, date-prefixed episode id / filename, e.g. 2026-07-22-auth-refactor."
    )
    kind: Literal["topic", "research", "session"]
    digest: str = Field(description="The <=120-word digest; validated before any write.")
    sections: dict[str, str] = Field(
        default_factory=dict, description="H2 heading -> body, e.g. {'Decisions': '...'}."
    )
    display_title: str | None = Field(
        default=None, description="Free-form INDEX title; defaults to the slug."
    )
    description: str | None = None
    tags: list[str] = Field(default_factory=list)
    span: str | None = Field(
        default=None,
        description="When the work happened, YYYY-MM-DD or A..B; defaults to date/today.",
    )
    session: str | None = None
    approx_tokens: int = 0
    mode: Literal["live", "import"] = "live"
    notes: str = ""
    retain_until: str | None = Field(
        default=None,
        description="Retention (#15): ISO date after which `memshelf purge` drops this "
        "episode. Absent means keep — retention is opt-in per episode.",
    )
    date: str | None = Field(default=None, description="YYYY-MM-DD; defaults to today.")
    autocommit: bool = True
    sync: bool = Field(
        default=True,
        description="Fetch + fast-forward the shelf to its remote before anything is "
        "written (#108): a dirty tracked tree or a diverged branch refuses the shelve "
        "with the fix in the message instead of silently writing onto a stale base. "
        "A failed fetch (offline) does not refuse — it is reported loudly.",
    )
    push: bool = Field(
        default=False,
        description="Push the shelve commit; on a rejection, rebase and retry exactly "
        "once (#108). The result then carries the post-push sha — the only sha worth "
        "quoting, since a rebase rewrites the local one.",
    )
    amend: bool = Field(
        default=False,
        description="Rewrite an episode already on the shelf, under the same slug (#71): "
        "one episode, one recomputed ledger row, redaction and the digest contract re-run. "
        "Errors if the slug is not there — that is a typo, not a create.",
    )


def run_shelve(params: ShelveInput) -> dict:
    """Shelve one episode and return a serializable summary of the result."""
    result = shelve(
        params.shelf_path,
        slug=params.slug,
        kind=params.kind,
        digest=params.digest,
        sections=params.sections,
        display_title=params.display_title,
        description=params.description,
        tags=params.tags,
        span=params.span,
        session=params.session,
        approx_tokens=params.approx_tokens,
        mode=params.mode,
        notes=params.notes,
        retain_until=params.retain_until,
        date=params.date,
        autocommit=params.autocommit,
        amend=params.amend,
        sync=params.sync,
        push=params.push,
    )
    totals = compute_stats(params.shelf_path)
    return {
        "status": "ok",
        "address": result.address,
        "display_title": result.display_title,
        "committed": result.committed,
        "commit": result.commit,
        "amended": result.amended,
        # Set only when an amend changed the kind and therefore moved the
        # episode between categories: the caller's previous address stopped
        # being valid, and a move reported by nothing is a move that looks like
        # a write (#90).
        "moved_from": result.moved_from,
        # #108 — what the sync around this shelve did, stated even when it did
        # nothing: a clean run says pulled 0 / retries 0 explicitly.
        "sync": None
        if result.sync is None
        else {
            "performed": result.sync.performed,
            "skipped_reason": result.sync.skipped_reason,
            "remote": result.sync.remote,
            "branch": result.sync.branch,
            "commits_pulled": result.sync.commits_pulled,
            "push_requested": result.sync.push_requested,
            "pushed": result.sync.pushed,
            "push_retries": result.sync.push_retries,
            "final_sha": result.sync.final_sha,
            "hint": result.sync.hint,
            "summary": result.sync.line(),
        },
        "redaction": {
            "total": result.redaction.total,
            "counts": result.redaction.counts,
            "summary": result.redaction.summary(),
        },
        "digest_warnings": [f.code for f in result.validation.warnings],
        "warnings": result.warnings,
        "ledger_row": result.ledger_row,
        "shelf_totals": {
            "episodes": totals.episodes,
            "shelved_mass": totals.shelved_mass,
            "standing_cost": totals.standing_cost,
            "compression_ratio": totals.compression_ratio,
        },
        "summary": f"+{params.approx_tokens:,} tok shelved · {banner(totals)}",
    }


class RecallInput(ShelfScopedInput):
    episode_id: str = Field(description="Episode id / slug, e.g. 2026-07-22-auth-refactor.")
    section: str | None = Field(
        default=None, description="Optional H2 section to fetch alone, e.g. 'Decisions'."
    )
    max_bytes: int = 100_000
    log: bool = Field(
        default=False,
        description="Append this recall to recall-log.tsv (feeds realized-economy stats).",
    )


class IndexInput(ShelfScopedInput):
    """Nothing beyond the shelf: INDEX is small and read whole."""


class SearchInput(ShelfScopedInput):
    query: str = Field(description="Space-separated tokens; a hit must contain all of them.")
    max_results: int = 10


def run_recall(params: RecallInput) -> dict:
    """Recall an episode (or one section) enveloped as data, not instructions."""
    log_path = str(Path(params.shelf_path) / "recall-log.tsv") if params.log else None
    result = recall(
        params.shelf_path,
        params.episode_id,
        section=params.section,
        max_bytes=params.max_bytes,
        log_path=log_path,
    )
    payload = {
        "status": "ok",
        "address": result.address,
        "section": result.section,
        "truncated": result.truncated,
        "content": result.content,
    }
    if params.log:
        fetched = len(result.content) // 4
        mass = episode_mass(params.shelf_path, params.episode_id)
        if mass:
            saved = max(mass - fetched, 0)
            payload["saved_tokens"] = saved
            payload["summary"] = (
                f"fetched ~{fetched:,} tok vs the episode's {mass:,} — saved ~{saved:,}"
            )
    return payload


def run_index(params: IndexInput) -> dict:
    """Return the shelf INDEX — the recall entry point."""
    return {"status": "ok", "index": read_index(params.shelf_path)}


def run_search(params: SearchInput) -> dict:
    """Grep the shelf; return matching episode addresses with snippets."""
    hits = search(params.shelf_path, params.query, max_results=params.max_results)
    return {
        "status": "ok",
        "hits": [{"address": h.address, "score": h.score, "snippet": h.snippet} for h in hits],
    }


class StatsInput(ShelfScopedInput):
    """Nothing beyond the shelf: the accounting covers all of it."""


def run_stats(params: StatsInput) -> dict:
    """Token accounting over the shelf: claimed economy (ledger) and, if any
    recalls are logged, realized economy (recall log)."""
    stats = compute_stats(params.shelf_path)
    payload = {"status": "ok", "banner": banner(stats), **stats.as_dict()}
    if stats.recalls == 0:
        payload["note"] = (
            "realized metrics are zero because no recalls are logged; "
            "recall with log=true (CLI: --log) to accumulate them."
        )
    return payload


class InitInput(ShelfScopedInput):
    shelf_path: str = Field(
        default="",
        description="Directory to create (or top up) the shelf in. Optional: when "
        f"omitted, ${SHELF_PATH_ENV} is used.",
    )
    name: str = "Memory shelf"
    storage: Literal["plain", "git-local", "git-remote"] = "git-local"
    remote: str | None = Field(
        default=None, description="Remote URL; only with storage=git-remote (private repos only)."
    )


def run_init(params: InitInput) -> dict:
    """Bootstrap a memory shelf with the memory conventions. Idempotent."""
    result = init_shelf(
        params.shelf_path, name=params.name, storage=params.storage, remote=params.remote
    )
    return {
        "status": "ok",
        "root": result.root,
        "storage": result.storage,
        "created": result.created,
        "committed": result.committed,
        "commit": result.commit,
    }


class LintDigestInput(BaseModel):
    # Unknown keys are refused here too — the note on `ShelfScopedInput` (#104).
    model_config = ConfigDict(extra="forbid")

    digest: str = Field(description="The digest text to check. Nothing is written.")
    strict: bool = Field(
        default=False,
        description="Make warnings count as failure. Off by default: a reference digest "
        "legitimately carries no decision marker, so `thin` must not block by default.",
    )


def run_lint_digest(params: LintDigestInput) -> dict:
    """Check a digest against the Layer-3 contract without writing anything (#71).

    The contract has always been enforced at shelve time, which is one moment
    too late to be useful while the digest is still being written. Same
    validator, no side effects — the point is that «check before you write» stops
    requiring a throwaway shelve.
    """
    result = validate_digest(params.digest)
    ok = result.ok and (not result.warnings or not params.strict)
    return {
        "status": "ok" if ok else "rejected",
        "passed": ok,
        "word_count": result.word_count,
        "errors": [{"code": f.code, "message": f.message} for f in result.errors],
        "warnings": [{"code": f.code, "message": f.message} for f in result.warnings],
        "report": result.report(),
    }


class DoctorInput(ShelfScopedInput):
    check_remote: bool = Field(
        default=False,
        description="Also probe git remotes and fail the shelf if any is publicly "
        "visible (MANIFEST principle 8). Off by default because it hits the network.",
    )
    derived_stale_after_hours: float = Field(
        default=DERIVED_STALE_AFTER_HOURS,
        gt=0,
        description="Hours the derived layer may go unrewritten with uncounted "
        "episodes before `derived-stale` fires (#89). A shelf picks its own "
        "threshold — a bot that renders in minutes deserves a far shorter one "
        "than the day-long default.",
    )


def run_doctor(params: DoctorInput) -> dict:
    """Check shelf integrity: schema, digest contract, secrets at rest, ledger,
    INDEX budget, plus docshelf's structural checks. Optionally (``check_remote``)
    gate on remote visibility."""
    return check_shelf(
        params.shelf_path,
        check_remote=params.check_remote,
        stale_after_hours=params.derived_stale_after_hours,
    ).as_dict()


class ResolveInput(ShelfScopedInput):
    commit: bool = Field(
        default=False,
        description="Commit the resolution (inside a merge this completes the "
        "merge with git's prepared message). Default: leave changes staged.",
    )


def run_resolve(params: ResolveInput) -> dict:
    """Resolve the multi-writer conflict class (#58): union ledger/recall-log
    rows and .meta.json keys from both branches, rebuild INDEX/stats from
    docs/, run doctor. Conflicting episodes are reported, never auto-merged."""
    return resolve_shelf(params.shelf_path, commit=params.commit).as_dict()


class RebuildInput(ShelfScopedInput):
    check: bool = Field(
        default=False,
        description="Verify instead of write: report which derived files would "
        "change and exit non-zero if any would. The shelf's PR guard runs this.",
    )
    adopt: bool = Field(
        default=False,
        description="One-shot migration for a shelf written before #58: copy the "
        "shelve date, ledger notes and display title out of ledger.tsv/.meta.json "
        "into each episode's frontmatter, so regenerating cannot drop them.",
    )


def run_rebuild(params: RebuildInput) -> dict:
    """Regenerate every derived file from the episodes (#58): ledger.tsv,
    each category's .meta.json, INDEX.md, stats.svg. The episode is the source;
    these four are output, and a bot on `main` owns them. With check=True
    nothing is written — the same code path answers 'do the committed
    artifacts still match the episodes?'."""
    if params.adopt and params.check:
        raise ValueError("adopt writes to the episodes — it cannot be combined with check")
    payload = {}
    if params.adopt:
        payload["adoption"] = adopt_shelf(params.shelf_path)
    payload.update(rebuild(params.shelf_path, check=params.check).as_dict())
    return payload


class RollupInput(ShelfScopedInput):
    slug: str = Field(description="Latin slug/id of the rollup episode, e.g. 2026-Q2-rollup.")
    digest: str = Field(
        description="The digest-of-digests — YOUR synthesis of the period, not the tool's. "
        "Same contract as a shelve digest: what was decided, what was rejected, what stays open."
    )
    until: str | None = Field(
        default=None,
        description="Archive every episode dated on or before this ISO date.",
    )
    episode_ids: list[str] = Field(
        default_factory=list,
        description="Explicit episode ids to archive instead of a date range.",
    )
    display_title: str | None = Field(default=None, description="Free-form INDEX title.")
    sections: dict[str, str] = Field(
        default_factory=dict, description="Extra H2 sections for the rollup episode."
    )
    date: str | None = Field(default=None, description="Rollup date (default: today).")


def run_rollup(params: RollupInput) -> dict:
    """Collapse a period into one digest-of-digests and move the originals into
    the archive sub-shelf (#15). N INDEX lines become one; nothing is deleted,
    recall by id keeps working, and the ledger keeps every row — an archived
    episode still holds the mass it saved."""
    return rollup_shelf(
        params.shelf_path,
        slug=params.slug,
        digest=params.digest,
        until=params.until,
        episode_ids=list(params.episode_ids) or None,
        display_title=params.display_title,
        sections=dict(params.sections),
        date=params.date,
    ).as_dict()


class PurgeInput(ShelfScopedInput):
    apply: bool = Field(
        default=False,
        description="Actually delete. Default is a dry run that only lists what expired.",
    )
    today: str | None = Field(
        default=None, description="Treat this ISO date as today (testing/backdating)."
    )


def run_purge(params: PurgeInput) -> dict:
    """Delete episodes whose `retain_until` has passed, then reindex (#15).
    Dry-run by default. Deletes the working-tree file only — git history still
    contains it, and real erasure is a deliberate filter-repo pass."""
    return purge_shelf(params.shelf_path, today=params.today, apply=params.apply).as_dict()


class PruneSplitsInput(ShelfScopedInput):
    apply: bool = Field(
        default=False,
        description="Actually delete. Default is a dry run that only lists what would go.",
    )


def run_prune_splits(params: PruneSplitsInput) -> dict:
    """Remove H2 split directories git does not track (#109).

    The migration for shelves written before `shelve` stopped splitting: those
    sections were never committed, so the machine holding them renders an INDEX
    no other checkout can produce and `doctor` reports a `stale-index` no
    rebuild clears. The episode file keeps every section — the split was a copy.
    A directory git *does* track is coherent everywhere and is reported, never
    deleted. CLI-only on purpose: deleting files is a decision a human makes."""
    return prune_split_dirs(params.shelf_path, apply=params.apply).to_dict()


class OccupantInput(BaseModel):
    """One thing the caller reports as sitting in its context window."""

    # Unknown keys are refused here too — the note on `ShelfScopedInput` (#104).
    model_config = ConfigDict(extra="forbid")

    label: str = Field(description="What this is, in your own words — it is echoed in proposals.")
    approx_tokens: int = Field(description="Rough size in tokens; an eyeball estimate is fine.")
    state: Literal["live", "closed", "unknown"] = Field(
        default="unknown",
        description="Is the topic still in play? Only you can tell. Unstated counts as live.",
    )
    kind: Literal["topic", "research", "tool-output", "instructions", "other"] = "topic"
    idle_turns: int | None = Field(
        default=None, description="Turns since this was last referenced, if you can tell."
    )
    episode_id: str | None = Field(
        default=None,
        description="Set if you believe this is ALREADY shelved. The claim is verified "
        "against the shelf before anything is proposed.",
    )


class AdviseInput(ShelfScopedInput):
    occupants: list[OccupantInput] = Field(
        default_factory=list,
        description="What is in your context right now. Empty is valid — you then get the "
        "shelf side only, and the report says the window side is missing.",
    )
    budget_tokens: int = Field(
        default=DEFAULT_BUDGET_TOKENS,
        description="Your context window. Default is a common size, not a memshelf constant.",
    )
    stale_after_turns: int = Field(
        default=STALE_AFTER_TURNS,
        description="Turns of silence after which an occupant of unstated state is called stale.",
    )
    include_memory_overhead: bool = Field(
        default=True,
        description="Count memshelf's own standing cost (INDEX + digests) as an occupant.",
    )


def run_advise(params: AdviseInput) -> dict:
    """Report where the window went and what could be put down (#14).

    Proposals only — this call writes nothing and changes nothing.
    """
    advice = advise(
        params.shelf_path,
        occupants=[
            Occupant(
                label=o.label,
                approx_tokens=o.approx_tokens,
                state=o.state,
                kind=o.kind,
                idle_turns=o.idle_turns,
                episode_id=o.episode_id,
            )
            for o in params.occupants
        ],
        budget_tokens=params.budget_tokens,
        stale_after_turns=params.stale_after_turns,
        include_memory_overhead=params.include_memory_overhead,
    )
    return {"status": "ok", **advice.as_dict()}


class ImportInput(BaseModel):
    # Unknown keys are refused here too — the note on `ShelfScopedInput` (#104).
    model_config = ConfigDict(extra="forbid")

    method: Literal["discover", "extract"] = Field(
        description="'discover' lists conversations by content marker; 'extract' cleans "
        "one to a working file for shelving."
    )
    path: str = Field(description="Path to the transcript FILE — content never rides in-context.")
    format: Literal["auto", "claude-json", "claude-code-jsonl"] = "auto"
    markers: list[str] = Field(
        default_factory=list,
        description="Content substrings that must ALL appear in a conversation's body "
        "(discovery is by content, not title).",
    )
    limit: int = Field(default=50, description="discover: max conversations to return.")
    select: str | None = Field(
        default=None, description="extract: conversation id / title / index to clean."
    )
    out: str | None = Field(
        default=None,
        description="extract: output file for the cleaned transcript (default: a temp "
        "working file). Never write it inside a shelf.",
    )


def run_import(params: ImportInput) -> dict:
    """Prepare an exported dialog for shelving without pulling it through context.

    ``discover`` returns conversation metadata matched by content markers;
    ``extract`` writes one cleaned, tool-block-stripped conversation to a working
    file and returns its path plus the noise ratio. The raw transcript is input
    only — never stored on a shelf.
    """
    if params.method == "discover":
        result = import_discover(
            params.path, markers=params.markers, fmt=params.format, limit=params.limit
        )
    else:
        result = import_extract(
            params.path,
            select=params.select,
            markers=params.markers,
            fmt=params.format,
            out=params.out,
        )
    return {"status": "ok", **result}
