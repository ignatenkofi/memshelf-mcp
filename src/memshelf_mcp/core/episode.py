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


#: How long a description may be where it is *displayed* — one INDEX line.
#: 120 characters is roughly one clause: enough to tell two episodes of the
#: same week apart, not enough to become a second digest. The digest is what
#: summarises the episode, and it lives in the episode; a description that
#: grows into a summary is paid for in every session by every reader who only
#: wanted to know which file to open.
#:
#: The number is also one of the four terms in ``doctor.INDEX_TOKENS_PER_ENTRY``
#: (120 chars ≈ 30 tokens of its 80), so moving one means moving the other.
MAX_DESCRIPTION_CHARS = 120


def required_sections(kind: str) -> tuple[str, ...]:
    """The named H2 sections a given kind must carry besides Digest."""
    return _REQUIRED_SECTIONS.get(kind, ())


def clamp_description(text: str | None) -> tuple[str, str | None]:
    """Hold a description to ``MAX_DESCRIPTION_CHARS``: ``(text, warning)``.

    Called on both paths that put a description in front of a reader — the
    write path in ``shelve`` and the render path in ``rebuild.render_meta`` —
    because capping only one of them fixes only half a shelf. Capping on write
    alone leaves every episode already on disk oversized until someone rewrites
    it; capping on render alone lets the author believe a 400-character
    description was accepted as written.

    Before this, the cap existed but applied to one branch of one expression:
    ``shelve`` used ``_first_sentence(digest)`` — truncating at 200 — only when
    the caller passed no ``description``, and wrote an explicit one through
    unmeasured. Since callers almost always pass one, the cap was effectively
    off: the author's shelf carried 15 descriptions past 200 characters, the
    longest 420, and descriptions alone were 43% of INDEX.

    Truncation is word-aware and marked with an ellipsis, so a cut line reads
    as cut rather than as a sentence that happens to end oddly.
    """
    text = flatten(text or "").strip()
    if len(text) <= MAX_DESCRIPTION_CHARS:
        return text, None
    head = text[: MAX_DESCRIPTION_CHARS - 1]
    # Prefer a word boundary, but only when one is near the end. Cutting at the
    # *last* space in the head unconditionally is how a description with one
    # long unbroken run — a URL, a hash, a wall of one token — collapses to
    # almost nothing: "Итог: yyyy…(200 more)" came back as "Итог…", and a value
    # whose head ended in punctuation could `rstrip` away to a bare ellipsis.
    # Below this floor a hard cut mid-word loses less than a "clean" one.
    floor = MAX_DESCRIPTION_CHARS * 2 // 3
    cut = head.rsplit(" ", 1)[0].rstrip(" ,;:—-") if " " in head else ""
    if len(cut) < floor:
        cut = head.rstrip()
    kept = f"{cut}…"
    return kept, (
        f"description was {len(text)} chars, cut to {len(kept)} "
        f"({len(text) - len(cut)} dropped): INDEX shows it in every session. "
        "Put the full account in the digest, which is what recall fetches."
    )


class EpisodeError(ValueError):
    """The episode's parts don't satisfy the format contract."""


def flatten(text: str) -> str:
    """Collapse a value to one line — the frontmatter block is flat ``key: value``.

    A newline in a value would end the field and, past the closing ``---``,
    silently turn the rest into body text. Callers pass free-form strings
    (display titles, ledger notes), so flattening belongs here rather than in
    each caller.
    """
    return " ".join(text.split())


def yaml_scalar(text: str) -> str:
    """Quote a free-text value so the block stays valid **YAML**.

    The frontmatter is read by two very different parsers: memshelf's own
    forgiving ``key: value`` splitter, and a real YAML loader — shelf-spec's
    validator, which is what the shelves run in CI. A display title like
    ``Охота на X1 Carbon: Грузия`` is fine for the first and a syntax error
    for the second (YAML reads the inner ``: `` as a nested mapping), and the
    failure mode is nasty: the validator reports the episode as having *no
    frontmatter at all*, not as having a bad line.

    Free-text fields are therefore always double-quoted. Always, not
    conditionally — a rule with exceptions is a rule someone's title will
    eventually fall through.
    """
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


#: Where an episode's ``approx_tokens`` came from (#79/#110/#113). The number
#: is the input to the shelf's headline metrics, and nothing in the data told
#: a measured value from an eyeballed one — or from no measurement at all,
#: which arrived as an arithmetically working 0. Three values, closed set:
#: ``estimate`` (a caller's judgment — the honest default for any number, per
#: the M0 chars/4 methodology), ``measured`` (an explicit claim), and
#: ``unmeasured`` (no number was passed; the stored 0 is a placeholder, not a
#: measurement). Absent on pre-field episodes — readers treat absence as
#: legacy, not as any of the three.
APPROX_TOKENS_SOURCES = ("estimate", "measured", "unmeasured")


@dataclass(frozen=True)
class Frontmatter:
    """The episode's frontmatter — and, since #58, the single source for every
    derived file on the shelf.

    ``date``, ``notes``, ``display_title`` and ``description`` are here because
    ``ledger.tsv`` and ``.meta.json`` are regenerated from the episodes: a
    column that lives only in the derived file cannot be regenerated, and the
    file stops being derived. ``date`` is the shelve date, deliberately
    distinct from ``span`` (what the conversation covered).
    """

    id: str
    kind: str
    span: str | None = None
    tags: tuple[str, ...] = ()
    approx_tokens: int = 0
    #: One of APPROX_TOKENS_SOURCES; "" on legacy episodes (field not written).
    approx_tokens_source: str = ""
    mode: str = "live"
    session: str | None = None
    date: str | None = None
    display_title: str | None = None
    description: str | None = None
    notes: str = ""
    #: Retention (#15): after this date `memshelf purge` drops the episode.
    #: Absent means "keep" — retention is opt-in per episode, never a default.
    retain_until: str | None = None

    def to_yaml(self) -> str:
        lines = [f"id: {self.id}", f"kind: {self.kind}"]
        if self.session:
            lines.append(f"session: {self.session}")
        if self.span:
            lines.append(f"span: {self.span}")
        if self.date:
            lines.append(f"date: {self.date}")
        if self.retain_until:
            lines.append(f"retain_until: {self.retain_until}")
        if self.display_title:
            lines.append(f"display_title: {yaml_scalar(flatten(self.display_title))}")
        if self.description:
            lines.append(f"description: {yaml_scalar(flatten(self.description))}")
        lines.append(f"tags: [{', '.join(self.tags)}]")
        lines.append(f"approx_tokens: {self.approx_tokens}")
        if self.approx_tokens_source:
            lines.append(f"approx_tokens_source: {self.approx_tokens_source}")
        lines.append(f"mode: {self.mode}")
        if self.notes:
            lines.append(f"notes: {yaml_scalar(flatten(self.notes))}")
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
