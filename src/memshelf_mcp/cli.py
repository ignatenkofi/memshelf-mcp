"""``memshelf`` CLI — the portability surface for hosts without MCP.

Anything that can run a shell command can drive the shelf: ``init``,
``shelve``, ``recall``, ``index``, ``search``, ``stats``, ``advise``,
``rebuild``, ``rollup``, ``purge``, ``resolve``, ``doctor``, ``lint-digest``.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from pydantic import ValidationError

from memshelf_mcp import __version__
from memshelf_mcp.core.advisor import DEFAULT_BUDGET_TOKENS, STALE_AFTER_TURNS
from memshelf_mcp.core.archive import ArchiveError
from memshelf_mcp.core.doctor import DERIVED_STALE_AFTER_HOURS
from memshelf_mcp.core.episode import EpisodeError
from memshelf_mcp.core.gitsync import DirtyShelfError, PushRejectedError, SyncDivergedError
from memshelf_mcp.core.importer import TranscriptError
from memshelf_mcp.core.init import InitError
from memshelf_mcp.core.recall import EpisodeNotFound
from memshelf_mcp.core.shelve import (
    AmendTargetMissing,
    DigestContractError,
    EpisodeExists,
    SlugContractError,
)
from memshelf_mcp.tools import (
    SHELF_PATH_ENV,
    AdviseInput,
    DoctorInput,
    ImportInput,
    IndexInput,
    InitInput,
    LintDigestInput,
    OccupantInput,
    PruneSplitsInput,
    PurgeInput,
    RebuildInput,
    RecallInput,
    ResolveInput,
    RollupInput,
    SearchInput,
    ShelveInput,
    StatsInput,
    default_shelf_path,
    run_advise,
    run_doctor,
    run_import,
    run_index,
    run_init,
    run_lint_digest,
    run_prune_splits,
    run_purge,
    run_rebuild,
    run_recall,
    run_resolve,
    run_rollup,
    run_search,
    run_shelve,
    run_stats,
)

_SHELF_HELP = (
    "Path to the shelf. Optional: falls back to $MEMSHELF_SHELF_PATH when omitted; "
    "an explicit path always wins over it."
)


def _resolve_shelf(value: str | None) -> str | None:
    """Fill an omitted ``--shelf`` from ``$MEMSHELF_SHELF_PATH``, and say when it did.

    One resolver for every subcommand, reading the variable through
    ``tools.default_shelf_path()`` — the same function the MCP inputs use. The
    variable was honoured by the tools and ignored here, which reads as a bug to
    whoever sets it and then tries the CLI (#86), and the CLI is the surface most
    likely to be scripted around.

    The note on stderr is what makes honouring it safe: an implicit shelf in a
    script is a footgun exactly when it is silent. stderr rather than stdout,
    because these commands print JSON that gets piped.
    """
    if value:
        return value
    resolved = default_shelf_path()
    if not resolved:
        return None
    print(f"memshelf: shelf from ${SHELF_PATH_ENV}: {resolved}", file=sys.stderr)
    return resolved


def _parse_sections(items: list[str]) -> dict[str, str]:
    sections: dict[str, str] = {}
    for item in items:
        name, sep, body = item.partition("=")
        if not sep:
            raise SystemExit(f"--section must be NAME=BODY, got {item!r}")
        sections[name.strip()] = body
    return sections


def _cmd_shelve(args: argparse.Namespace) -> int:
    params = ShelveInput(
        shelf_path=args.shelf,
        slug=args.slug,
        kind=args.kind,
        digest=args.digest,
        sections=_parse_sections(args.section),
        display_title=args.display_title,
        description=args.description,
        tags=args.tag,
        span=args.span,
        session=args.session,
        approx_tokens=args.approx_tokens,
        mode=args.mode,
        notes=args.notes,
        retain_until=args.retain_until,
        date=args.date,
        autocommit=not args.no_commit,
        amend=args.amend,
        sync=not args.no_sync,
        push=args.push,
        publish=args.publish,
    )
    try:
        result = run_shelve(params)
    except (
        DigestContractError,
        SlugContractError,
        EpisodeError,
        AmendTargetMissing,
        EpisodeExists,
        DirtyShelfError,
        SyncDivergedError,
        PushRejectedError,
    ) as exc:
        # Expected, actionable failures: print the fix to stderr, exit non-zero.
        print(str(exc), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def _cmd_lint_digest(args: argparse.Namespace) -> int:
    if args.digest_file:
        digest = Path(args.digest_file).expanduser().read_text(encoding="utf-8")
    elif args.digest is not None:
        digest = args.digest
    else:
        digest = sys.stdin.read()
    result = run_lint_digest(LintDigestInput(digest=digest, strict=args.strict))
    print(result["report"])
    return 0 if result["passed"] else 1


def _cmd_recall(args: argparse.Namespace) -> int:
    params = RecallInput(
        shelf_path=args.shelf,
        episode_id=args.id,
        section=args.section,
        max_bytes=args.max_bytes,
        log=args.log,
    )
    try:
        result = run_recall(params)
    except (EpisodeNotFound, FileNotFoundError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(result["content"])
    if "summary" in result:
        # stdout stays pipeable content; the savings note goes to stderr.
        print(result["summary"], file=sys.stderr)
    return 0


def _cmd_index(args: argparse.Namespace) -> int:
    try:
        result = run_index(IndexInput(shelf_path=args.shelf))
    except FileNotFoundError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(result["index"])
    return 0


def _cmd_search(args: argparse.Namespace) -> int:
    result = run_search(
        SearchInput(shelf_path=args.shelf, query=args.query, max_results=args.max_results)
    )
    for hit in result["hits"]:
        print(f"{hit['address']}\t{hit['score']}\t{hit['snippet']}")
    return 0


def _cmd_stats(args: argparse.Namespace) -> int:
    if args.chart:
        from memshelf_mcp.core.chart import write_chart

        rel = write_chart(args.shelf)
        if rel is None:
            print("no ledger rows yet — nothing to chart", file=sys.stderr)
            return 1
        print(rel)
        return 0
    result = run_stats(StatsInput(shelf_path=args.shelf))
    if args.banner:
        print(result["banner"])
        return 0
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def _parse_occupant(item: str) -> OccupantInput:
    """Parse ``LABEL=TOKENS[,closed][,idle=N][,kind=K][,episode=ID]``.

    One repeatable flag rather than five parallel ones: the whole occupant has
    to survive as a single quoted shell argument, since a caller assembles
    these by hand while its context is already full.
    """
    label, sep, rest = item.partition("=")
    if not sep or not rest.strip():
        raise SystemExit(f"--occupant must be LABEL=TOKENS[,attr...], got {item!r}")
    parts = [p.strip() for p in rest.split(",")]
    try:
        fields: dict = {"label": label.strip(), "approx_tokens": int(parts[0])}
    except ValueError:
        raise SystemExit(f"--occupant {item!r}: {parts[0]!r} is not a token count") from None
    for attr in parts[1:]:
        if not attr:
            continue
        key, eq, value = attr.partition("=")
        if not eq:
            fields["state"] = key  # bare word: live | closed | unknown
        elif key == "idle":
            try:
                fields["idle_turns"] = int(value)
            except ValueError:
                raise SystemExit(f"--occupant {item!r}: idle={value!r} is not a number") from None
        elif key == "kind":
            fields["kind"] = value
        elif key == "episode":
            fields["episode_id"] = value
        else:
            raise SystemExit(f"--occupant {item!r}: unknown attribute {key!r}")
    try:
        return OccupantInput(**fields)
    except ValidationError as exc:
        raise SystemExit(f"--occupant {item!r}: {exc.errors()[0]['msg']}") from None


def _load_occupants_json(path: str) -> list[OccupantInput]:
    raw = sys.stdin.read() if path == "-" else Path(path).read_text(encoding="utf-8")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"--occupants-json: {exc}") from None
    if not isinstance(data, list):
        raise SystemExit("--occupants-json must hold a JSON list of occupant objects")
    try:
        return [OccupantInput(**item) for item in data]
    except (ValidationError, TypeError) as exc:
        raise SystemExit(f"--occupants-json: {exc}") from None


def _cmd_advise(args: argparse.Namespace) -> int:
    occupants = [_parse_occupant(item) for item in args.occupant]
    if args.occupants_json:
        occupants += _load_occupants_json(args.occupants_json)
    result = run_advise(
        AdviseInput(
            shelf_path=args.shelf,
            occupants=occupants,
            budget_tokens=args.budget,
            stale_after_turns=args.stale_after,
            include_memory_overhead=not args.exclude_self,
        )
    )
    if args.summary:
        print(result["summary"])
        return 0
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def _cmd_init(args: argparse.Namespace) -> int:
    try:
        result = run_init(
            InitInput(
                shelf_path=args.shelf, name=args.name, storage=args.storage, remote=args.remote
            )
        )
    except InitError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def _cmd_freshness(args: argparse.Namespace) -> int:
    from memshelf_mcp.core.freshness import main as freshness_main

    return freshness_main([args.repo] if args.repo else [])


def _cmd_doctor(args: argparse.Namespace) -> int:
    result = run_doctor(
        DoctorInput(
            shelf_path=args.shelf,
            check_remote=args.check_remote,
            derived_stale_after_hours=args.derived_stale_hours,
        )
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["errors"] == 0 else 1  # non-zero on errors, for CI / hooks


def _cmd_resolve(args: argparse.Namespace) -> int:
    result = run_resolve(ResolveInput(shelf_path=args.shelf, commit=args.commit))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if result["in_merge"] and not result["committed"] and not result["unresolved"]:
        print("merge resolved and staged — `git commit` completes it", file=sys.stderr)
    return 0 if result["status"] == "ok" else 1


def _cmd_rebuild(args: argparse.Namespace) -> int:
    result = run_rebuild(RebuildInput(shelf_path=args.shelf, check=args.check, adopt=args.adopt))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if args.check and result["drifted"]:
        print(
            "derived files diverge from the episodes: "
            + ", ".join(result["drifted"])
            + "\nrun `memshelf rebuild --shelf ...` on main (the bot does this)",
            file=sys.stderr,
        )
    return 0 if result["ok"] else 1


def _cmd_rollup(args: argparse.Namespace) -> int:
    try:
        result = run_rollup(
            RollupInput(
                shelf_path=args.shelf,
                slug=args.slug,
                digest=args.digest,
                until=args.until,
                episode_ids=args.episode,
                display_title=args.display_title,
                sections=_parse_sections(args.section),
                date=args.date,
            )
        )
    except (ArchiveError, EpisodeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def _cmd_purge(args: argparse.Namespace) -> int:
    result = run_purge(PurgeInput(shelf_path=args.shelf, apply=args.apply, today=args.today))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if result["expired"] and not result["applied"]:
        print("dry run — re-run with --apply to delete", file=sys.stderr)
    return 0


def _cmd_prune_splits(args: argparse.Namespace) -> int:
    result = run_prune_splits(PruneSplitsInput(shelf_path=args.shelf, apply=args.apply))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if result["local"] and not result["applied"]:
        print("dry run — re-run with --apply to delete", file=sys.stderr)
    if result["tracked"]:
        print(
            "left alone (git tracks them, so every checkout has them): "
            + ", ".join(result["tracked"]),
            file=sys.stderr,
        )
    return 0


def _cmd_import(args: argparse.Namespace) -> int:
    params = ImportInput(
        method=args.method,
        path=args.path,
        format=args.format,
        markers=args.marker,
        limit=args.limit,
        select=args.select,
        out=args.out,
    )
    try:
        result = run_import(params)
    except (TranscriptError, FileNotFoundError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="memshelf", description="Working-memory shelf CLI.")
    parser.add_argument("--version", action="version", version=f"memshelf {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    sh = sub.add_parser("shelve", help="Shelve one episode to the shelf.")
    sh.add_argument("--shelf", help=_SHELF_HELP)
    sh.add_argument("--slug", required=True, help="Latin date-prefixed id, e.g. 2026-07-22-topic.")
    sh.add_argument("--kind", required=True, choices=["topic", "research", "session"])
    sh.add_argument("--digest", required=True, help="The <=120-word digest.")
    sh.add_argument(
        "--section",
        action="append",
        default=[],
        metavar="NAME=BODY",
        help="An H2 section; repeatable, e.g. --section 'Decisions=...'.",
    )
    sh.add_argument("--display-title", help="Free-form INDEX title (defaults to slug).")
    sh.add_argument("--description")
    sh.add_argument("--tag", action="append", default=[], help="A tag; repeatable.")
    sh.add_argument(
        "--span",
        help="When the work happened, YYYY-MM-DD or A..B (defaults to --date/today).",
    )
    sh.add_argument("--session")
    sh.add_argument("--approx-tokens", type=int, default=0)
    sh.add_argument("--mode", choices=["live", "import"], default="live")
    sh.add_argument("--notes", default="")
    sh.add_argument(
        "--retain-until",
        help="ISO date after which `memshelf purge` drops this episode (opt-in).",
    )
    sh.add_argument("--date", help="YYYY-MM-DD (defaults to today).")
    sh.add_argument("--no-commit", action="store_true", help="Skip the auto-commit.")
    sh.add_argument(
        "--no-sync",
        action="store_true",
        help="Skip the fetch + fast-forward preflight (#108). Default: sync before "
        "writing; a dirty tracked tree or a diverged branch refuses the shelve.",
    )
    sh.add_argument(
        "--push",
        action="store_true",
        help="Push after the commit; on a rejection, rebase and retry exactly once "
        "(#108). The report then names the post-push sha.",
    )
    sh.add_argument(
        "--publish",
        action="store_true",
        help="Publish the shelve commit to origin as a NEW branch shelve/<slug> and "
        "print a one-click compare link (#118) — for shelves whose main requires a "
        "PR. Exclusive with --push; the checkout never switches branches.",
    )
    sh.add_argument(
        "--amend",
        action="store_true",
        help="Rewrite an episode already on the shelf under the same slug: one episode, "
        "one recomputed ledger row, redaction and the digest contract re-run. "
        "Fails if the slug is not there.",
    )
    sh.set_defaults(func=_cmd_shelve)

    ld = sub.add_parser(
        "lint-digest",
        help="Check a digest against the contract without writing anything.",
    )
    ld.add_argument("--digest", help="The digest text. Omit both to read stdin.")
    ld.add_argument("--digest-file", help="Read the digest from this file.")
    ld.add_argument(
        "--strict",
        action="store_true",
        help="Exit non-zero on warnings too (default: only errors fail).",
    )
    ld.set_defaults(func=_cmd_lint_digest)

    rc = sub.add_parser("recall", help="Recall an episode, or one section of it.")
    rc.add_argument("--shelf", help=_SHELF_HELP)
    rc.add_argument("--id", required=True, help="Episode id / slug.")
    rc.add_argument("--section", help="Fetch only this H2 section (e.g. Decisions).")
    rc.add_argument("--max-bytes", type=int, default=100_000)
    rc.add_argument(
        "--log", action="store_true", help="Log this recall (feeds realized-economy stats)."
    )
    rc.set_defaults(func=_cmd_recall)

    ix = sub.add_parser("index", help="Print the shelf INDEX.")
    ix.add_argument("--shelf", help=_SHELF_HELP)
    ix.set_defaults(func=_cmd_index)

    se = sub.add_parser("search", help="Grep the shelf for episodes.")
    se.add_argument("--shelf", help=_SHELF_HELP)
    se.add_argument("--query", required=True, help="Space-separated tokens (all must match).")
    se.add_argument("--max-results", type=int, default=10)
    se.set_defaults(func=_cmd_search)

    st = sub.add_parser("stats", help="Token accounting for the shelf.")
    st.add_argument("--shelf", help=_SHELF_HELP)
    st.add_argument("--banner", action="store_true", help="Print the one-line summary only.")
    st.add_argument(
        "--chart", action="store_true", help="(Re)draw stats.svg at the shelf root and exit."
    )
    st.set_defaults(func=_cmd_stats)

    ad = sub.add_parser(
        "advise",
        help="Where the context window went, and what could be put down (proposals only).",
    )
    ad.add_argument("--shelf", help=_SHELF_HELP)
    ad.add_argument(
        "--occupant",
        action="append",
        default=[],
        metavar="SPEC",
        help="LABEL=TOKENS[,live|closed][,idle=N][,kind=K][,episode=ID]; repeatable. "
        "Example: --occupant 'auth refactor=30000,closed'. Without occupants you get "
        "the shelf side only.",
    )
    ad.add_argument(
        "--occupants-json",
        metavar="PATH",
        help="JSON list of occupant objects ('-' reads stdin); the full form of --occupant.",
    )
    ad.add_argument(
        "--budget",
        type=int,
        default=DEFAULT_BUDGET_TOKENS,
        help=f"Your context window in tokens (default {DEFAULT_BUDGET_TOKENS}).",
    )
    ad.add_argument(
        "--stale-after",
        type=int,
        default=STALE_AFTER_TURNS,
        help="Turns of silence after which an occupant of unstated state counts as stale.",
    )
    ad.add_argument(
        "--exclude-self",
        action="store_true",
        help="Don't count memshelf's own standing cost (INDEX + digests) as an occupant.",
    )
    ad.add_argument("--summary", action="store_true", help="Print the one-line summary only.")
    ad.set_defaults(func=_cmd_advise)

    it = sub.add_parser("init", help="Bootstrap a memory shelf (idempotent).")
    it.add_argument("--shelf", help=_SHELF_HELP)
    it.add_argument("--name", default="Memory shelf")
    it.add_argument("--storage", choices=["plain", "git-local", "git-remote"], default="git-local")
    it.add_argument("--remote", help="Remote URL (git-remote mode only; private repos).")
    it.set_defaults(func=_cmd_init)

    rv = sub.add_parser(
        "resolve",
        help="Resolve multi-writer merge conflicts: union appends, rebuild derived, doctor.",
    )
    rv.add_argument("--shelf", help=_SHELF_HELP)
    rv.add_argument(
        "--commit",
        action="store_true",
        help="Commit the resolution (inside a merge: completes the merge).",
    )
    rv.set_defaults(func=_cmd_resolve)

    dc = sub.add_parser("doctor", help="Check shelf integrity (exit 1 on errors).")
    dc.add_argument("--shelf", help=_SHELF_HELP)
    dc.add_argument(
        "--check-remote",
        action="store_true",
        help="Probe git remotes; fail on a publicly visible one (needs network).",
    )
    dc.add_argument(
        "--derived-stale-hours",
        type=float,
        default=DERIVED_STALE_AFTER_HOURS,
        help="Hours the derived layer may go unrewritten with uncounted episodes "
        "before `derived-stale` fires (default %(default)s; #89). A shelf whose "
        "renderer normally answers in minutes should pick a much shorter one.",
    )
    dc.set_defaults(func=_cmd_doctor)

    fr = sub.add_parser(
        "freshness",
        help="Probe installed consumers (pipx, Desktop extension) against the working "
        "tree: which code actually answers calls, and is anything merged but "
        "unreleased (#125). The deliberate-ask side of the doctor freshness findings.",
    )
    fr.add_argument(
        "repo",
        nargs="?",
        default=None,
        help="memshelf-mcp checkout to compare against (default: cwd).",
    )
    fr.set_defaults(func=_cmd_freshness)

    rb = sub.add_parser(
        "rebuild",
        help="Regenerate derived files (ledger/INDEX/.meta/stats) from the episodes.",
    )
    rb.add_argument("--shelf", help=_SHELF_HELP)
    rb.add_argument(
        "--check",
        action="store_true",
        help="Verify only: write nothing, exit 1 if any derived file has drifted. "
        "This is what a shelf's PR guard runs.",
    )
    rb.add_argument(
        "--adopt",
        action="store_true",
        help="One-shot migration for a pre-#58 shelf: copy date/notes/display title "
        "out of ledger.tsv and .meta.json into the episodes before regenerating.",
    )
    rb.set_defaults(func=_cmd_rebuild)

    ru = sub.add_parser(
        "rollup",
        help="Collapse a period into one digest-of-digests; originals move to archive/.",
    )
    ru.add_argument("--shelf", help=_SHELF_HELP)
    ru.add_argument("--slug", required=True, help="Latin slug/id of the rollup episode.")
    ru.add_argument(
        "--digest",
        required=True,
        help="Your synthesis of the period — the tool does not write it for you.",
    )
    ru.add_argument("--until", help="Archive every episode dated on or before this ISO date.")
    ru.add_argument(
        "--episode",
        action="append",
        default=[],
        help="Explicit episode id to archive (repeatable); alternative to --until.",
    )
    ru.add_argument("--display-title", help="Free-form INDEX title for the rollup.")
    ru.add_argument(
        "--section", action="append", default=[], metavar="NAME=BODY", help="Extra H2 section."
    )
    ru.add_argument("--date", help="Rollup date (default: today).")
    ru.set_defaults(func=_cmd_rollup)

    pu = sub.add_parser("purge", help="Delete episodes whose retain_until has passed.")
    pu.add_argument("--shelf", help=_SHELF_HELP)
    pu.add_argument("--apply", action="store_true", help="Actually delete (default: dry run).")
    pu.add_argument("--today", help="Treat this ISO date as today.")
    pu.set_defaults(func=_cmd_purge)

    ps = sub.add_parser(
        "prune-splits",
        help="Remove H2 split directories git does not track (migration for #109).",
    )
    ps.add_argument("--shelf", help=_SHELF_HELP)
    ps.add_argument("--apply", action="store_true", help="Actually delete (default: dry run).")
    ps.set_defaults(func=_cmd_prune_splits)

    im = sub.add_parser("import", help="Prepare an exported transcript for shelving.")
    im.add_argument("method", choices=["discover", "extract"])
    im.add_argument("--path", required=True, help="Path to the transcript file.")
    im.add_argument(
        "--format", choices=["auto", "claude-json", "claude-code-jsonl"], default="auto"
    )
    im.add_argument(
        "--marker",
        action="append",
        default=[],
        help="A content substring the target must contain; repeatable (all must match).",
    )
    im.add_argument("--limit", type=int, default=50, help="discover: max conversations returned.")
    im.add_argument("--select", help="extract: conversation id / title / index.")
    im.add_argument("--out", help="extract: output file for the cleaned transcript.")
    im.set_defaults(func=_cmd_import)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    # Resolved here rather than as an argparse default: the variable is read per
    # call on the MCP side too, because a host may set it after the process
    # starts — and an argparse default would freeze it at import.
    if hasattr(args, "shelf"):
        args.shelf = _resolve_shelf(args.shelf)
        if args.shelf is None:
            print(
                f"memshelf {args.command}: no shelf to work on — pass --shelf, or set "
                f"${SHELF_PATH_ENV} to the shelf directory.",
                file=sys.stderr,
            )
            return 2
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
