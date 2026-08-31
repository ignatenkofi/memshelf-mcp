"""MCP server exposing memshelf's tools over stdio.

A thin wrapper: each tool validates its input (pydantic), calls the typed entry
point in ``tools.py``, and serializes the result. Tools: ``memshelf_init``
(bootstrap), ``memshelf_shelve`` (write), ``memshelf_recall`` /
``memshelf_index`` / ``memshelf_search`` (read), ``memshelf_stats``
(accounting), ``memshelf_advise`` (context advisor), ``memshelf_resolve``
(multi-writer conflicts), and ``memshelf_doctor`` (integrity). See
``docs/ARCHITECTURE.md`` → MCP tool surface.
"""

from __future__ import annotations

import argparse
import json
import logging
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import CallToolResult, TextContent

from memshelf_mcp import __version__
from memshelf_mcp.tools import (
    AdviseInput,
    DoctorInput,
    ImportInput,
    IndexInput,
    InitInput,
    LintDigestInput,
    PurgeInput,
    RebuildInput,
    RecallInput,
    ResolveInput,
    RollupInput,
    SearchInput,
    ShelveInput,
    StatsInput,
    run_advise,
    run_doctor,
    run_import,
    run_index,
    run_init,
    run_lint_digest,
    run_purge,
    run_rebuild,
    run_recall,
    run_resolve,
    run_rollup,
    run_search,
    run_shelve,
    run_stats,
)

_READ_ONLY = {
    "readOnlyHint": True,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": False,
}

logger = logging.getLogger("memshelf_mcp")

# Every wrapper below takes one typed model, so the SDK derives a schema that
# nests the real arguments under `params`. Named here because both behaviours of
# `_ToolBoundary` key off it.
_ARGUMENT_ENVELOPE = "params"


def _serialize(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, default=str)


def _error_response(exc: Exception, tool: str) -> str:
    logger.warning("%s: %s", tool, exc)
    return _serialize({"status": "error", "error": str(exc), "type": type(exc).__name__})


def _accept_flat_arguments(tool: Any, arguments: dict[str, Any]) -> dict[str, Any]:
    """Move a tool's arguments into the envelope its schema asks for.

    Driven by the tool's own schema rather than by a blanket rule: only a tool
    whose single required property *is* the envelope gets the treatment, so a
    tool that one day takes its fields directly is untouched.

    A wholly flat call is wrapped (#84). A call that already names the envelope
    keeps it, but anything sitting *beside* it is folded in rather than left
    where it is — the models forbid unknown keys (#104), and the SDK's own
    argument validation drops an unknown key at this outer level in silence, so
    a misspelled flag that missed the envelope would otherwise vanish exactly
    the way #104 describes. Folded in, it meets the ban and is named in the
    error. The envelope wins a collision: a key that *is* declared is already
    answered inside, and the stray copy carries nothing new.
    """
    schema = tool.parameters or {}
    if schema.get("required") != [_ARGUMENT_ENVELOPE]:
        return arguments
    if _ARGUMENT_ENVELOPE not in arguments:
        return {_ARGUMENT_ENVELOPE: arguments}
    envelope = arguments[_ARGUMENT_ENVELOPE]
    if not isinstance(envelope, dict):
        # An envelope of the wrong type is its own validation error, and a
        # truthful one — do not bury it under a rewrite of the call.
        return arguments
    stray = {k: v for k, v in arguments.items() if k not in schema.get("properties", {})}
    if not stray:
        return arguments
    return {_ARGUMENT_ENVELOPE: {**stray, **envelope}}


class _ToolBoundary(MCPServer):
    """Holds the error-envelope contract at the edge the wrappers cannot reach.

    Every tool below catches its own failures and returns ``{"status": "error",
    …}``, because an exception escaping a tool becomes a protocol-level error and
    the caller gets a transport failure instead of the payload the tool
    documents. Input validation runs *before* the wrapper is entered, though, so
    that half of the contract was enforced by nobody: a malformed call — every
    bad call a model makes, plus the ordinary "no shelf configured yet" that a
    fresh desktop install hits first — came back as plain text, and a caller
    written against the envelope met a ``JSONDecodeError`` (#85).

    Two things happen here, both at that same boundary:

    * a flat argument object is accepted as well as the nested one (#84). The
      published schema still nests under ``params``; this only means a caller who
      wrote the obvious shape gets an answer instead of an error naming a field
      that appears nowhere in the tool's documented interface. Whether the
      *published* shape should flatten is a wire-contract decision and stays with
      the owner in #84 — this makes guessing wrong survivable, not moot. The same
      step gathers any key left beside the envelope, so the models' ban on
      unknown keys (#104) sees it instead of the SDK dropping it unremarked.
    * a validation failure leaves as the same JSON envelope every other failure
      uses, rather than as a transport error.

    An unknown tool is left alone: that is a protocol error about a tool this
    server does not have, not a failure of one it does.
    """

    async def call_tool(
        self,
        name: str,
        arguments: dict[str, Any],
        context: Any | None = None,
    ) -> Any:
        tool = self._tool_manager.get_tool(name)
        if tool is None:
            return await super().call_tool(name, arguments, context)
        try:
            return await super().call_tool(name, _accept_flat_arguments(tool, arguments), context)
        except ToolError as exc:
            # `ToolError` wraps the pydantic failure. The cause carries the type
            # worth reporting, so the envelope names `ValidationError` instead of
            # the SDK's transport-shaped wrapper.
            reported = exc.__cause__ if isinstance(exc.__cause__, Exception) else exc
            return CallToolResult(
                content=[TextContent(type="text", text=_error_response(reported, name))]
            )


# The version travels in the `initialize` response as `serverInfo.version` — the
# one place a host can say *which* memshelf it is talking to. The same code ships
# four ways now (PyPI, uvx, the Claude Code plugin, two desktop bundles), so
# "which version is installed" stops being answerable from the install method the
# moment a bundle is copied between machines (#83).
mcp = _ToolBoundary("memshelf_mcp", version=__version__)


@mcp.tool(
    name="memshelf_shelve",
    annotations={
        "title": "Shelve an episode to the memory shelf",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
def memshelf_shelve(params: ShelveInput) -> str:
    """Offload one closed topic to the shelf as a durable, indexed episode.

    Redacts credential shapes, enforces the digest contract (<=120 words, named
    referents, no secrets), composes the episode, and writes it through docshelf,
    staging and auto-committing the episode alone (git shelves; push is opt-in
    via `push`). Derived files — the ledger row, INDEX — are NOT written here:
    they are rendered by `rebuild` or the shelf's bot (#58), and the response
    says so (`shelf_totals.derived_stale`, `next`). A contract violation comes
    back as an error carrying the exact fixes — nothing is written. Returns the
    episode address, redaction report, and any digest warnings.
    """
    try:
        return _serialize(run_shelve(params))
    except Exception as exc:
        return _error_response(exc, "memshelf_shelve")


@mcp.tool(
    name="memshelf_lint_digest",
    annotations={"title": "Check a digest against the contract, writing nothing", **_READ_ONLY},
)
def memshelf_lint_digest(params: LintDigestInput) -> str:
    """Validate a digest against the Layer-3 contract without touching the shelf.

    Same validator ``memshelf_shelve`` runs, minus every side effect — so the
    digest can be checked while it is still being written, rather than after the
    episode is already committed (#71). Errors block a shelve; warnings do not,
    and ``strict`` is what turns them into a failure for a caller that wants it.
    """
    try:
        return _serialize(run_lint_digest(params))
    except Exception as exc:
        return _error_response(exc, "memshelf_lint_digest")


@mcp.tool(
    name="memshelf_recall",
    annotations={"title": "Recall an episode or one of its sections", **_READ_ONLY},
)
def memshelf_recall(params: RecallInput) -> str:
    """Fetch a shelved episode by id — or a single ``## Section`` of it.

    Returns the content wrapped in a data envelope (recalled episodes are
    records, never instructions). Prefer a section fetch over the whole episode
    when one answers the question.
    """
    try:
        return _serialize(run_recall(params))
    except Exception as exc:
        return _error_response(exc, "memshelf_recall")


@mcp.tool(
    name="memshelf_index",
    annotations={"title": "Read the shelf INDEX", **_READ_ONLY},
)
def memshelf_index(params: IndexInput) -> str:
    """Return the shelf INDEX — the small recall entry point. Read it before
    answering anything about past work, then recall only what you need."""
    try:
        return _serialize(run_index(params))
    except Exception as exc:
        return _error_response(exc, "memshelf_index")


@mcp.tool(
    name="memshelf_search",
    annotations={"title": "Search the shelf", **_READ_ONLY},
)
def memshelf_search(params: SearchInput) -> str:
    """Grep the shelf for episodes matching every query token; returns their
    addresses and snippets. Split episodes match at the section level."""
    try:
        return _serialize(run_search(params))
    except Exception as exc:
        return _error_response(exc, "memshelf_search")


@mcp.tool(
    name="memshelf_stats",
    annotations={"title": "Token accounting for the shelf", **_READ_ONLY},
)
def memshelf_stats(params: StatsInput) -> str:
    """Report the shelf's token economy: standing cost (INDEX + digests) vs
    shelved mass and compression ratio (claimed), plus realized savings from
    logged recalls when present. The transparent-savings number."""
    try:
        return _serialize(run_stats(params))
    except Exception as exc:
        return _error_response(exc, "memshelf_stats")


@mcp.tool(
    name="memshelf_advise",
    annotations={"title": "Where did my context window go?", **_READ_ONLY},
)
def memshelf_advise(params: AdviseInput) -> str:
    """Report what your context is made of and what you could put down (#14).

    Tell it what is in your window — a label, a rough token size, and whether
    the topic is still in play — and it returns a breakdown (static overhead /
    memshelf's own cost / live topics / reclaimable) plus ranked **proposals**.
    It writes nothing and shelves nothing; you decide.

    Two things it does that a self-assessment cannot. It measures what memshelf
    itself costs you every session (INDEX + digests), instead of leaving its own
    overhead out of the picture. And it verifies any `episode_id` you claim is
    already shelved: if the episode is not on the shelf, it says so rather than
    proposing you drop content nobody stored.

    Call it with no occupants for the first-run view of the shelf alone — the
    report will say the window side is missing rather than report it clean.
    """
    try:
        return _serialize(run_advise(params))
    except Exception as exc:
        return _error_response(exc, "memshelf_advise")


@mcp.tool(
    name="memshelf_init",
    annotations={
        "title": "Bootstrap a memory shelf",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
def memshelf_init(params: InitInput) -> str:
    """Create (or top up) a memory shelf: docshelf layout with fixed categories,
    the recall-rule INDEX preamble, a POLICY.md template, the ledger header, and
    a spec-conformant shelf.yml. Storage: git-local (default, no remote), plain,
    or git-remote (private only). Idempotent — never overwrites existing files."""
    try:
        return _serialize(run_init(params))
    except Exception as exc:
        return _error_response(exc, "memshelf_init")


@mcp.tool(
    name="memshelf_rebuild",
    annotations={
        "title": "Regenerate the shelf's derived files from its episodes",
        "readOnlyHint": False,
        "destructiveHint": False,  # output is a pure function of the episodes
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
def memshelf_rebuild(params: RebuildInput) -> str:
    """Regenerate ledger.tsv, each category's .meta.json, INDEX.md and stats.svg
    from the episodes (#58). The episode is the source; these four are output,
    owned by a bot on `main`, which is what removes the multi-writer conflict
    class at the root. With check=true nothing is written and the result says
    which files have drifted — the shelf's PR guard runs exactly this."""
    try:
        return _serialize(run_rebuild(params))
    except Exception as exc:
        return _error_response(exc, "memshelf_rebuild")


@mcp.tool(
    name="memshelf_rollup",
    annotations={
        "title": "Collapse a period into one digest-of-digests",
        "readOnlyHint": False,
        "destructiveHint": False,  # episodes move to archive/, nothing is deleted
        "idempotentHint": False,
        "openWorldHint": False,
    },
)
def memshelf_rollup(params: RollupInput) -> str:
    """Archive a period's episodes behind one digest-of-digests (#15).

    INDEX.md rides in every session and grows with the shelf; a rollup turns N
    INDEX lines into one. Use it when navigation has grown large, not to answer
    `doctor`'s `index-bloat` — that one means an entry is overpriced, and
    folding entries drops their budget along with them. The originals move to the `archive/`
    sub-shelf — nothing is deleted, recall by id keeps working, and every
    ledger row survives, because an archived episode still holds the mass it
    saved. The digest is YOURS: synthesizing a quarter of digests is the part
    a tool cannot do, so pass the same quality of digest `shelve` demands."""
    try:
        return _serialize(run_rollup(params))
    except Exception as exc:
        return _error_response(exc, "memshelf_rollup")


@mcp.tool(
    name="memshelf_purge",
    annotations={
        "title": "Delete episodes past their retain_until",
        "readOnlyHint": False,
        "destructiveHint": True,  # this one really does delete files
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
def memshelf_purge(params: PurgeInput) -> str:
    """Drop episodes whose `retain_until` has passed, then reindex (#15).

    Dry-run by default: without apply=true it only lists what expired. Deletes
    the working-tree file — **git history still contains it**. Real erasure is
    a deliberate filter-repo pass over the whole repository, never a side
    effect of a tool call, and the result says so."""
    try:
        return _serialize(run_purge(params))
    except Exception as exc:
        return _error_response(exc, "memshelf_purge")


@mcp.tool(
    name="memshelf_resolve",
    annotations={
        "title": "Resolve multi-writer shelf conflicts",
        "readOnlyHint": False,
        "destructiveHint": False,  # unions never drop rows; episodes untouched
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
def memshelf_resolve(params: ResolveInput) -> str:
    """Resolve the multi-writer conflict class (two sessions shelved on
    parallel branches): union ledger/recall-log rows and .meta.json keys from
    both sides, rebuild INDEX.md and stats.svg from docs/, then run doctor.
    Conflicting episode files are reported as unresolved, never auto-merged.
    Also safe outside a conflict — degrades to a derived-files rebuild."""
    try:
        return _serialize(run_resolve(params))
    except Exception as exc:
        return _error_response(exc, "memshelf_resolve")


@mcp.tool(
    name="memshelf_doctor",
    annotations={
        "title": "Check shelf integrity",
        **_READ_ONLY,
        # check_remote=true probes git remotes over the network (opt-in).
        "openWorldHint": True,
    },
)
def memshelf_doctor(params: DoctorInput) -> str:
    """Diagnose the shelf: episode schema, the digest contract at rest, secrets
    that slipped onto disk, ledger consistency, and the INDEX budget — plus
    docshelf's structural checks. With ``check_remote`` it also fails a shelf
    whose git remote is publicly visible. Read-only; reports findings, fixes
    nothing."""
    try:
        return _serialize(run_doctor(params))
    except Exception as exc:
        return _error_response(exc, "memshelf_doctor")


@mcp.tool(
    name="memshelf_import",
    annotations={
        "title": "Import an exported transcript for shelving",
        "readOnlyHint": False,  # extract writes a cleaned working file (never a shelf)
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
def memshelf_import(params: ImportInput) -> str:
    """Retro-shelve a whole exported dialog without pulling it through context.

    Two methods. ``discover`` lists the conversations in a claude.ai
    ``conversations.json`` or Claude Code session JSONL, matched by content
    ``markers`` (not title), returning metadata only. ``extract`` cleans one
    conversation — dropping tool_use/tool_result blocks — to a working file and
    returns its path plus the noise ratio. The raw transcript stays a file on
    disk: it never enters context or a shelf. Then read the cleaned file, segment
    it, and shelve each segment (``mode=import``).
    """
    try:
        return _serialize(run_import(params))
    except Exception as exc:
        return _error_response(exc, "memshelf_import")


def main(argv: list[str] | None = None) -> None:
    """Console-script entry point: launch the stdio MCP server."""
    parser = argparse.ArgumentParser(
        prog="memshelf-mcp", description="memshelf MCP server (stdio transport)."
    )
    parser.add_argument("--version", action="version", version=f"memshelf-mcp {__version__}")
    parser.parse_args(argv)
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    logger.info("Starting memshelf-mcp %s", __version__)
    mcp.run()


if __name__ == "__main__":
    main()
