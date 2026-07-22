"""Compose an episode file from its parts (Layer 2 capture, write side).

Pure string assembly plus the kind→required-sections rule — no I/O, no
docshelf. The orchestration in ``shelve.py`` adds storage, ledger, and commit.
Reading frontmatter back (the H1-first parser for doctor/stats) is a separate
concern, added when those tools land. See ``docs/ARCHITECTURE.md`` → Layer 2.
"""

from __future__ import annotations

from dataclasses import dataclass

CATEGORY_BY_KIND = {"topic": "topics", "research": "research", "session": "sessions"}

# Required H2 sections per kind. Digest is always required and handled apart.
# `research` needs Digest + at least one body section (checked below), so it
# has no single named requirement here.
_REQUIRED_SECTIONS: dict[str, tuple[str, ...]] = {
    "topic": ("Decisions",),
    "research": (),
    "session": ("Timeline", "Open threads"),
}

# Canonical order for known sections when present; unknown sections keep their
# insertion order after these.
_SECTION_ORDER = ("Decisions", "Timeline", "Artifacts", "Open threads", "Raw excerpts")


def required_sections(kind: str) -> tuple[str, ...]:
    """The named H2 sections a given kind must carry besides Digest."""
    return _REQUIRED_SECTIONS.get(kind, ())


class EpisodeError(ValueError):
    """The episode's parts don't satisfy the format contract."""


@dataclass(frozen=True)
class Frontmatter:
    id: str
    kind: str
    span: str | None = None
    tags: tuple[str, ...] = ()
    approx_tokens: int = 0
    mode: str = "live"
    session: str | None = None

    def to_yaml(self) -> str:
        lines = [f"id: {self.id}", f"kind: {self.kind}"]
        if self.session:
            lines.append(f"session: {self.session}")
        if self.span:
            lines.append(f"span: {self.span}")
        lines.append(f"tags: [{', '.join(self.tags)}]")
        lines.append(f"approx_tokens: {self.approx_tokens}")
        lines.append(f"mode: {self.mode}")
        return "\n".join(lines)


def _check_contract(kind: str, digest: str, sections: dict[str, str]) -> None:
    if kind not in CATEGORY_BY_KIND:
        raise EpisodeError(f"unknown kind {kind!r}; expected one of {sorted(CATEGORY_BY_KIND)}.")
    if not digest.strip():
        raise EpisodeError("every episode needs a Digest.")
    present = {name for name, body in sections.items() if body.strip()}
    missing = [s for s in _REQUIRED_SECTIONS[kind] if s not in present]
    if missing:
        raise EpisodeError(f"kind={kind} requires section(s) {missing}.")
    if kind == "research" and not present:
        raise EpisodeError("kind=research requires Digest plus at least one body section.")


def compose_episode(frontmatter: Frontmatter, digest: str, sections: dict[str, str]) -> str:
    """Return the episode Markdown: H1 slug, ``---``-fenced frontmatter, Digest,
    then ordered body sections. Empty sections are omitted.

    Raises ``EpisodeError`` on a contract miss (unknown kind, missing Digest or
    a required section). The H1-first layout matches how docshelf's
    ``add_document`` stores episodes (ARCHITECTURE Layer 2).
    """
    _check_contract(frontmatter.kind, digest, sections)
    parts = [
        f"# {frontmatter.id}",
        "",
        "---",
        frontmatter.to_yaml(),
        "---",
        "",
        "## Digest",
        digest.strip(),
    ]
    known = [s for s in _SECTION_ORDER if sections.get(s, "").strip()]
    extras = [s for s in sections if s not in _SECTION_ORDER and sections[s].strip()]
    for name in known + extras:
        parts += ["", f"## {name}", sections[name].strip()]
    return "\n".join(parts) + "\n"
