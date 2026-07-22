"""FastMCP server exposing memshelf's tools over stdio.

A thin wrapper: each tool validates its input (pydantic), calls the typed entry
point in ``tools.py``, and serializes the result. Slice 3 ships
``memshelf_shelve``; recall / index / search / stats / doctor land in later
slices. See ``docs/ARCHITECTURE.md`` → MCP tool surface.
"""

from __future__ import annotations

import argparse
import json
import logging
from typing import Any

from mcp.server.fastmcp import FastMCP

from memshelf_mcp import __version__
from memshelf_mcp.tools import ShelveInput, run_shelve

logger = logging.getLogger("memshelf_mcp")
mcp = FastMCP("memshelf_mcp")


def _serialize(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, default=str)


def _error_response(exc: Exception, tool: str) -> str:
    logger.warning("%s: %s", tool, exc)
    return _serialize({"status": "error", "error": str(exc), "type": type(exc).__name__})


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
    referents, no secrets), composes the episode, writes it through docshelf,
    appends the ledger row, and auto-commits (git shelves only; never pushes). A
    contract violation comes back as an error carrying the exact fixes — nothing
    is written. Returns the episode address, redaction report, and any digest
    warnings.
    """
    try:
        return _serialize(run_shelve(params))
    except Exception as exc:
        return _error_response(exc, "memshelf_shelve")


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
