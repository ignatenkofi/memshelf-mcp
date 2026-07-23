"""Typed entry points wrapping the core for the MCP server and the CLI.

``ShelveInput`` is the validated surface; ``run_shelve`` calls the core and
returns a JSON-serializable dict. Keeping this here — not in ``server.py`` —
lets the CLI and the tests reuse it without importing the MCP SDK.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from memshelf_mcp.core.doctor import check_shelf
from memshelf_mcp.core.init import init_shelf
from memshelf_mcp.core.recall import read_index, recall, search
from memshelf_mcp.core.shelve import shelve
from memshelf_mcp.core.stats import banner, compute_stats, episode_mass


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
    totals = compute_stats(params.shelf_path)
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


class RecallInput(BaseModel):
    shelf_path: str = Field(description="Path to an initialized memory shelf.")
    episode_id: str = Field(description="Episode id / slug, e.g. 2026-07-22-auth-refactor.")
    section: str | None = Field(
        default=None, description="Optional H2 section to fetch alone, e.g. 'Decisions'."
    )
    max_bytes: int = 100_000
    log: bool = Field(
        default=False,
        description="Append this recall to recall-log.tsv (feeds realized-economy stats).",
    )


class IndexInput(BaseModel):
    shelf_path: str = Field(description="Path to an initialized memory shelf.")


class SearchInput(BaseModel):
    shelf_path: str = Field(description="Path to an initialized memory shelf.")
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


class StatsInput(BaseModel):
    shelf_path: str = Field(description="Path to an initialized memory shelf.")


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


class InitInput(BaseModel):
    shelf_path: str = Field(description="Directory to create (or top up) the shelf in.")
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


class DoctorInput(BaseModel):
    shelf_path: str = Field(description="Path to an initialized memory shelf.")


def run_doctor(params: DoctorInput) -> dict:
    """Check shelf integrity: schema, digest contract, secrets at rest, ledger,
    INDEX budget, plus docshelf's structural checks."""
    return check_shelf(params.shelf_path).as_dict()
