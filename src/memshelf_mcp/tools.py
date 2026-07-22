"""Typed entry points wrapping the core for the MCP server and the CLI.

``ShelveInput`` is the validated surface; ``run_shelve`` calls the core and
returns a JSON-serializable dict. Keeping this here — not in ``server.py`` —
lets the CLI and the tests reuse it without importing the MCP SDK.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from memshelf_mcp.core.shelve import shelve


class ShelveInput(BaseModel):
    shelf_path: str = Field(description="Path to an initialized memory shelf.")
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
    span: str | None = None
    session: str | None = None
    approx_tokens: int = 0
    mode: Literal["live", "import"] = "live"
    notes: str = ""
    date: str | None = Field(default=None, description="YYYY-MM-DD; defaults to today.")
    autocommit: bool = True


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
        date=params.date,
        autocommit=params.autocommit,
    )
    return {
        "status": "ok",
        "address": result.address,
        "display_title": result.display_title,
        "committed": result.committed,
        "commit": result.commit,
        "redaction": {
            "total": result.redaction.total,
            "counts": result.redaction.counts,
            "summary": result.redaction.summary(),
        },
        "digest_warnings": [f.code for f in result.validation.warnings],
        "ledger_row": result.ledger_row,
    }
