"""Compose an episode file from its parts (Layer 2 capture, write side).

Pure string assembly plus the kind→required-sections rule — no I/O, no
docshelf. The orchestration in ``shelve.py`` adds storage, ledger, and commit.
Reading frontmatter back (the H1-first parser for doctor/stats) is a separate
concern, added when those tools land. See ``docs/ARCHITECTURE.md`` → Layer 2.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
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
# insertion order after these. A new episode's order; an amend keeps the order
# the episode already has (#205, `compose_episode`).
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

    It also keeps code spans whole (#190). INDEX prints the episode's file name
    in backticks right after the description, so a single backtick the
    description leaves unpaired pairs with the file name's instead: the span
    swallows the separator, and the file name renders as plain text with a
    stray backtick. So a cut never lands inside a code span. It moves before
    the span or, when that would keep less than the floor, closes the span
    before the ellipsis. A value within the cap that leaves a single backtick
    unpaired gets it closed at its end. Such values include descriptions that
    an earlier cap cut inside a span, already on disk, so ``rebuild`` repairs
    them without an edit.

    A longer run left unpaired is literal in CommonMark and cannot pair with
    the file name's single backtick, yet it can still cost the file name its
    span: GitHub's renderer and markdown-it stop searching once a run has
    found no closer and trust a cache instead, which a code span later in the
    line can leave saying the file name's backtick has none (see
    ``_escape_before_spans``). So such a run is escaped when a code span
    follows it; escaped, it renders as it did. The value is read the way
    CommonMark reads it (see ``_backtick_runs``), so a backtick inside an
    autolink or an HTML tag is left alone.
    """
    text = flatten(text or "").strip()
    if len(text) <= MAX_DESCRIPTION_CHARS:
        balanced, done = _balance(text)
        if not done:
            return text, None
        if len(balanced) <= MAX_DESCRIPTION_CHARS:
            return balanced, " ".join(_BALANCE_NOTES[step] for step in done)
        # No room to balance it within the cap: cut, as any longer value is.
    # Escapes go in before the cut, which has to count their backslashes. The
    # single backtick left open counts as a span here: the cut closes it or
    # moves before it.
    spans, unpaired, open_single = _backtick_runs(text)
    if open_single is not None:
        spans = [s for s in spans if s[0] < open_single] + [(open_single, len(text), 1)]
        unpaired = [r for r in unpaired if r[0] < open_single]
    text = _escape_before_spans(text, spans, unpaired)
    spans, _, open_single = _backtick_runs(text)
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
        if text[len(cut) : len(cut) + 1] == "`":
            # A hard cut through a backtick run keeps none of it: a shorter run
            # pairs with other runs than the whole one did.
            cut = cut.rstrip("`").rstrip()
    kept = f"{cut}…"
    # The single backtick left open counts as a span open to the end: the
    # INDEX line would close it with the file name's backtick.
    regions = list(spans)
    if open_single is not None:
        regions.append((open_single, len(text) + 1, 1))
    for start, end, length in sorted(regions):
        if not start < len(cut) < end:
            continue
        before = text[:start].rstrip(" ,;:—-")
        # Room for a space, the closing run and the ellipsis after the content.
        # Trailing backticks go: a run the cut split in two is shorter than it
        # was, and one as long as the opener would close the span early.
        inner = text[: min(len(cut), max(0, MAX_DESCRIPTION_CHARS - 2 - length))]
        inner = inner.rstrip(" `")
        if len(before) >= floor or len(inner) <= start + length:
            cut, kept = before, f"{before}…"
        else:
            cut = inner
            kept, _ = _close_span(f"{inner}…", start, length)
        break
    cut, kept = _settle(cut, kept, floor)
    return kept, (
        f"description was {len(text)} chars, cut to {len(kept)} "
        f"({len(text) - len(cut)} dropped): INDEX shows it in every session. "
        "Put the full account in the digest, which is what recall fetches."
    )


#: What CommonMark ranks level with a code span: an autolink or an inline HTML
#: tag that starts first holds its backticks, which then delimit nothing. The
#: patterns are markdown-it-py's (``rules_inline/autolink.py``,
#: ``common/html_re.py``), the parser #190 was checked against.
_ATTRIBUTE = (
    r"(?:\s+[a-zA-Z_:][a-zA-Z0-9:._-]*"
    r"""(?:\s*=\s*(?:[^"'=<>`\x00-\x20]+|'[^']*'|"[^"]*"))?)"""
)
_AUTOLINK_OR_HTML = re.compile(
    "|".join(
        (
            r"<[a-zA-Z][a-zA-Z0-9+.\-]{1,31}:[^<>\x00-\x20]*>",
            r"<[a-zA-Z0-9.!#$%&'*+/=?^_`{|}~-]+@[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?"
            r"(?:\.[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?)*>",
            rf"<[A-Za-z][A-Za-z0-9\-]*{_ATTRIBUTE}*\s*/?>",
            r"</[A-Za-z][A-Za-z0-9\-]*\s*>",
            r"<!---?>|<!--(?:[^-]|-[^-]|--[^>])*-->",
            r"<[?][\s\S]*?[?]>",
            r"<![A-Za-z][^>]*>",
            r"<!\[CDATA\[[\s\S]*?\]\]>",
        )
    )
)


def _backtick_runs(
    text: str,
) -> tuple[list[tuple[int, int, int]], list[tuple[int, int]], int | None]:
    """Code spans in ``text``, its unpaired longer runs, and the single backtick
    it leaves open.

    Read as CommonMark reads it: a backslash escapes the character after it
    outside a span; an autolink or an inline HTML tag that starts first holds
    its backticks; a run is closed by the next run of exactly its length,
    wherever that is; a run nothing closes is literal, and the scan goes on
    after it. Returns the spans as ``(start, end, run length)``, the unclosed
    runs of two or more as ``(start, length)``, and the start of the unclosed
    run of length one, or None. That one would pair with the file name's single
    backtick. There is at most one, as any later single backtick would have
    closed it.

    Not modelled: a link destination or title, ``[text](url "title")``, also
    holds its backticks in CommonMark; here one is read as a run.
    """
    spans: list[tuple[int, int, int]] = []
    unpaired: list[tuple[int, int]] = []
    open_single = None
    i, n = 0, len(text)
    while i < n:
        if text[i] == "\\":
            i += 2
            continue
        if text[i] == "<":
            held = _AUTOLINK_OR_HTML.match(text, i)
            i = held.end() if held else i + 1
            continue
        if text[i] != "`":
            i += 1
            continue
        j = i
        while j < n and text[j] == "`":
            j += 1
        end = _closing_run_end(text, j, j - i)
        if end is not None:
            spans.append((i, end, j - i))
            i = end
            continue
        if j - i == 1:
            open_single = i
        else:
            unpaired.append((i, j - i))
        i = j
    return spans, unpaired, open_single


_BALANCE_NOTES = {
    "closed": (
        "description has an unpaired `, which the INDEX line would pair with the "
        "backtick before the file name; closed it at the end. Pair it in the "
        "episode to say where the code ends."
    ),
    "dropped": (
        "description has an unpaired `, which the INDEX line would pair with the "
        "backtick before the file name; dropped it, as nothing follows it."
    ),
    "escaped": (
        "description has an unpaired run of backticks before a code span, after "
        "which GitHub's renderer shows the file name as plain text; escaped the "
        "run, which renders the same. Escape or pair it in the episode."
    ),
}


def _balance(text: str) -> tuple[str, list[str]]:
    """Close the single backtick ``text`` leaves open, and escape the unpaired
    runs a code span follows: ``(text, steps taken)``, the steps keyed as in
    ``_BALANCE_NOTES``."""
    spans, unpaired, open_single = _backtick_runs(text)
    done = []
    if open_single is not None:
        text, closed = _close_span(text, open_single, 1)
        done.append("closed" if closed else "dropped")
        # Whatever followed the opener is inside the span now.
        spans = [s for s in spans if s[0] < open_single]
        unpaired = [r for r in unpaired if r[0] < open_single]
        if closed:
            spans.append((open_single, len(text), 1))
    escaped = _escape_before_spans(text, spans, unpaired)
    if escaped != text:
        done.append("escaped")
    return escaped, done


def _escape_before_spans(
    text: str, spans: list[tuple[int, int, int]], unpaired: list[tuple[int, int]]
) -> str:
    """Escape each unpaired run in ``text`` that a code span follows.

    CommonMark leaves such a run literal, but cmark-gfm (GitHub) and
    markdown-it search for closers with a cache: once a run has searched to
    the end of the line in vain, a later opener whose cached position for its
    length lies behind it is taken to have no closer. A span closed after the
    unpaired run updates that cache with a position inside the description,
    and the file name's opening backtick is then taken to have none: measured
    with GitHub's renderer (``POST /markdown``) on "a `` b `c` d". With every
    such run escaped no search fails before the last span, so the cache is
    never read; an escaped backtick renders as the literal run did.
    """
    if not unpaired or not any(start > unpaired[0][0] for start, _, _ in spans):
        return text
    for start, length in reversed(unpaired):
        if any(s > start for s, _, _ in spans):
            text = text[:start] + "\\`" * length + text[start + length :]
    return text


def _settle(cut: str, kept: str, floor: int) -> tuple[str, str]:
    """Balance what a cut kept: ``(cut, kept)``.

    A cut through an autolink or an HTML tag frees the backticks it held, and
    a span the cut closed may follow a run left unpaired. A single backtick
    left open goes the way of a span the cut lands in; runs a span follows are
    escaped, or cut away when the escapes do not fit.
    """
    body = kept[:-1]  # every cut ends in the ellipsis
    spans, unpaired, opened = _backtick_runs(body)
    if opened is not None:
        before = body[:opened].rstrip(" ,;:—-")
        inner = body[: MAX_DESCRIPTION_CHARS - 3].rstrip(" `")
        if len(before) >= floor or len(inner) <= opened + 1:
            cut, kept = before, f"{before}…"
        else:
            cut = inner
            kept, _ = _close_span(f"{inner}…", opened, 1)
        body = kept[:-1]
        spans, unpaired, _ = _backtick_runs(body)
    escaped = _escape_before_spans(body, spans, unpaired)
    if escaped != body:
        if len(escaped) < MAX_DESCRIPTION_CHARS:
            kept = f"{escaped}…"
        else:
            cut = body[: unpaired[0][0]].rstrip(" ,;:—-")
            kept = f"{cut}…"
    return cut, kept


def _closing_run_end(text: str, start: int, length: int) -> int | None:
    """End of the first backtick run of exactly ``length`` at or after ``start``."""
    k = text.find("`", start)
    while k >= 0:
        m = k
        while m < len(text) and text[m] == "`":
            m += 1
        if m - k == length:
            return m
        k = text.find("`", m)
    return None


def _close_span(text: str, start: int, length: int) -> tuple[str, bool]:
    """Close the span opened at ``start`` at the end of ``text``: ``(text, closed)``.

    The closing run goes before a trailing ellipsis, so a cut still reads as
    cut. A space keeps it apart from content that ends in a backtick, which
    would otherwise merge with it into a run of another length. When only
    spaces follow the opener there is nothing to close, and the opener is
    dropped instead.
    """
    body, tail = (text[:-1], "…") if text.endswith("…") else (text, "")
    body = body.rstrip()
    if not body[start + length :].strip():
        return body[:start].rstrip() + tail, False
    pad = " " if body.endswith("`") else ""
    return f"{body}{pad}{'`' * length}{tail}", True


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


#: What one frontmatter line cannot hold: the C0 and C1 control characters (a
#: line break and a tab among them; a raw tab or control character also fails
#: the strict ``json.loads`` that reads a quoted value back) and the line and
#: paragraph separators, where ``str.splitlines`` ends the line.
_NOT_ON_ONE_LINE = re.compile(r"[\x00-\x1f\x7f-\x9f\u2028\u2029]")


def _one_line(text: str) -> str:
    """``text`` as it is, unless it holds what one line cannot: then each such
    character becomes a space and the whole value is ``flatten``-ed.

    For ``description``, which reaches the frontmatter already flat on every
    path but one: ``clamp_description`` flattens what it returns. The one is
    the stored value an amend keeps (#205), and flattening it would rewrite a
    leading, trailing or doubled space, or a no-break space, the episode
    carries. Only a line break or another control character (see
    ``_NOT_ON_ONE_LINE``) still changes it, since it would end the field or
    fail to read back; ``flatten`` alone would keep one that is not
    whitespace, such as an escape character.
    """
    if _NOT_ON_ONE_LINE.search(text) is None:
        return text
    return flatten(_NOT_ON_ONE_LINE.sub(" ", text))


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
    #: Rollup-only (#172): the keywords of what this episode absorbed, so the
    #: *next* rollup can inherit them without re-deriving from this episode's
    #: own (generic, "N episodes folded in") digest. Empty on every ordinary
    #: episode — this is not a general-purpose tagging field, and emitting an
    #: empty `keywords: []` on every `shelve` would be noise `to_yaml` does not
    #: add elsewhere (`tags` is the exception, kept for backward compatibility).
    keywords: tuple[str, ...] = ()

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
            lines.append(f"description: {yaml_scalar(_one_line(self.description))}")
        lines.append(f"tags: [{', '.join(self.tags)}]")
        if self.keywords:
            lines.append(f"keywords: [{', '.join(self.keywords)}]")
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


def _section_order(sections: dict[str, str], stored: Sequence[str]) -> list[str]:
    """The non-empty ``sections`` in the order they are written.

    A new episode (no ``stored``) takes the canonical order: known sections in
    ``_SECTION_ORDER``, then the rest in the order passed. An amend passes the
    headings of the episode it rewrites, in file order (#205): the sections
    still there keep that order, a heading the file repeats counts once, at
    its first place, and a section the episode does not have yet goes right
    after the last one the canonical order puts before it (first, if none
    does). After, not before the first one ranked later: a section the
    canonical order does not know ranks after every known one, so an episode
    that opens with one (`Context`, say) would get a new `Artifacts` above it.
    An episode already in canonical order comes out as a new one would.
    """
    present = [name for name in sections if sections[name].strip()]
    fresh = [s for s in _SECTION_ORDER if s in present]
    fresh += [s for s in present if s not in _SECTION_ORDER]
    rank = {name: i for i, name in enumerate(fresh)}
    ordered = [name for name in dict.fromkeys(stored) if name in rank]
    for name in fresh:
        if name not in ordered:
            earlier = [i for i, kept in enumerate(ordered) if rank[kept] < rank[name]]
            ordered.insert(earlier[-1] + 1 if earlier else 0, name)
    return ordered


def compose_episode(
    frontmatter: Frontmatter,
    digest: str,
    sections: dict[str, str],
    *,
    order: Sequence[str] = (),
) -> str:
    """Return the episode Markdown: H1 slug, ``---``-fenced frontmatter, Digest,
    then ordered body sections. Empty sections are omitted.

    ``order`` is for an amend: the H2 headings of the episode being rewritten,
    in file order, so its sections keep their places (#205) instead of moving
    to the canonical order a new episode gets (see ``_section_order``).

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
    for name in _section_order(sections, order):
        parts += ["", f"## {name}", sections[name].strip()]
    return "\n".join(parts) + "\n"
