"""``memshelf`` CLI — the portability surface for hosts without MCP.

Anything that can run a shell command can drive the shelf. Slice 3 ships
``memshelf shelve``; recall / search / index / stats mirror the MCP tools in
later slices.
"""

from __future__ import annotations

import argparse
import json
import sys

from memshelf_mcp import __version__
from memshelf_mcp.core.episode import EpisodeError
from memshelf_mcp.core.shelve import DigestContractError
from memshelf_mcp.tools import ShelveInput, run_shelve


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
        date=args.date,
        autocommit=not args.no_commit,
    )
    try:
        result = run_shelve(params)
    except (DigestContractError, EpisodeError) as exc:
        # Expected, actionable failures: print the fix to stderr, exit non-zero.
        print(str(exc), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="memshelf", description="Working-memory shelf CLI.")
    parser.add_argument("--version", action="version", version=f"memshelf {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    sh = sub.add_parser("shelve", help="Shelve one episode to the shelf.")
    sh.add_argument("--shelf", required=True, help="Path to an initialized shelf.")
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
    sh.add_argument("--span")
    sh.add_argument("--session")
    sh.add_argument("--approx-tokens", type=int, default=0)
    sh.add_argument("--mode", choices=["live", "import"], default="live")
    sh.add_argument("--notes", default="")
    sh.add_argument("--date", help="YYYY-MM-DD (defaults to today).")
    sh.add_argument("--no-commit", action="store_true", help="Skip the auto-commit.")
    sh.set_defaults(func=_cmd_shelve)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
