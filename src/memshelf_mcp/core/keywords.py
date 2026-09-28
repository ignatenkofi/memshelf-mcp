"""Deterministic keyword carry-over for a rollup's digest and INDEX line (#172).

Owner decision on PR #172 (measurement (b), M2 dogfood: 0/5 recall via plain
"INDEX → point fetch" once an episode is two rollup generations deep) —
"давай нести в дайджест роллапа ключевые слова поглощённого". A rollup's
digest used to be pure synthesis-by-the-caller ("N episodes folded in"), and
what actually renders on the INDEX line — ``description`` (see
``rebuild.render_meta`` / docshelf's ``scan_shelf`` — the line is built from
the episode's frontmatter ``description``, not its digest) — carried nothing
of what was absorbed at all. Neither place could be searched by topic, so
navigation-by-INDEX went blind exactly where a rollup made the shelf smaller.

This module computes one thing — a short, ranked, deduplicated keyword list
for a rollup absorbing a batch of episodes — and two ways to fold it into
text under a hard budget: :func:`digest_with_keywords` (the digest's own
120-word contract, ``digest.MAX_WORDS``) and, via :func:`render_keyword_list`,
whatever prose the caller (``archive.rollup``) builds for ``description``
(120 *characters*, ``episode.MAX_DESCRIPTION_CHARS`` / ``clamp_description``).
The canonical, unclamped list is also the caller's to store verbatim in the
new rollup's own ``keywords`` frontmatter field — that stored field, not a
re-parse of generated prose, is what makes transitivity exact (see
:func:`rollup_keywords`).

Nothing here touches disk or git; every function is a pure transform over
strings the caller already read, so it is unit-testable without a shelf.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Mapping, Sequence
from datetime import date as _date

from memshelf_mcp.core.digest import MAX_WORDS

__all__ = [
    "MAX_ROLLUP_KEYWORDS",
    "MIN_KEYWORD_LENGTH",
    "KEYWORDS_LABEL",
    "rollup_keywords",
    "render_keyword_list",
    "digest_with_keywords",
]

#: How many keywords a rollup's frontmatter/description/digest carry forward.
#: Sized against the *description* budget, not guessed: `description` is
#: capped at `episode.MAX_DESCRIPTION_CHARS` (120 chars ≈ 30 of doctor's
#: 80-token-per-entry INDEX allowance — `doctor.INDEX_TOKENS_PER_ENTRY`), and
#: the fixed prose around the list ("Роллап: N эп. Ключевые слова: ") already
#: spends ~30 of those characters. Eight comma-separated words (bilingual
#: average observed on the dogfood shelf: ~8–10 chars incl. ", ") fill the
#: rest without routinely tripping `clamp_description`'s ellipsis — which
#: still stands as the hard backstop if a shelf's words run longer.
MAX_ROLLUP_KEYWORDS = 8

#: Below this many letters a token is almost always a function word the
#: stoplist missed (transliteration, a stray abbreviation) rather than a
#: topic — applied uniformly to tags, title words and digest words alike,
#: which is simpler to reason about (and to test) than a per-source floor.
MIN_KEYWORD_LENGTH = 4

#: The clause's label, in the shelves' own working language (bilingual
#: Russian/English digests — CLAUDE.md "Язык и стиль" across this portfolio).
KEYWORDS_LABEL = "Ключевые слова поглощённого"

#: A generic function-word stoplist (Russian + English) — deliberately not
#: domain-tuned. Digests across this portfolio share plenty of high-frequency
#: *domain* words too ("тест", "issue", "PR", "коммит"), and excluding those
#: would be guessing at what counts as generic from this session's fixtures
#: alone. Structural words are the one class safe to exclude everywhere: a
#: preposition is never the reason a reader recognises a topic.
_STOPWORDS = frozenset(
    """
    и в на с со по для что как это эти эта этот они он она оно мы вы я ты
    но а же ли бы не ни из к у о об от до за при про над под без между
    через или если то так там тут здесь когда где куда откуда который
    которая которое которые которых его её их наш ваш свой есть был была
    было были будет быть уже ещё очень только также тоже все весь всё вся
    себя себе свои свою нам нас нам мочь надо нужно можно нельзя чтобы
    the a an and or but if then than that this these those it its they
    them their we our us you your he she his her of in on at by for with
    to from as is are was were be been being not no do does did has have
    had will would can could should may might into over under about after
    before between through per via which who whom whose what when where
    why how all any some each every both few more most other such only
    own same so too very just
    """.split()
)

_TOKEN_RE = re.compile(r"[^\W\d_]+", re.UNICODE)


def _tokenize(text: str) -> list[str]:
    """Lowercased letter-only tokens — no digits, no punctuation, unicode-aware
    so Cyrillic and Latin words both split correctly."""
    return [m.group(0).lower() for m in _TOKEN_RE.finditer(text)]


def _parse_bracket_list(raw: str | None) -> tuple[str, ...]:
    """``"[a, b]"`` → ``("a", "b")``. Tolerates quotes, blanks, a bare ``[]``.

    Same tolerant shape — and, deliberately, the same five lines — as
    :func:`memshelf_mcp.core.reuse.parse_tags`, not imported from there:
    ``reuse`` imports ``rebuild``, which imports ``shelve``, which imports
    ``archive`` at module scope. This module is imported by ``archive`` at
    module scope too (see ``archive.rollup``), so pulling in ``reuse`` here
    would close that loop through keyword-carrying of all things.
    """
    if not raw:
        return ()
    inner = raw.strip()
    if inner.startswith("[") and inner.endswith("]"):
        inner = inner[1:-1]
    seen: list[str] = []
    for part in inner.split(","):
        tag = part.strip().strip("'\"").strip()
        if tag and tag not in seen:
            seen.append(tag)
    return tuple(seen)


def _digest_section(body: str) -> str:
    """The episode's own ``## Digest`` text, up to the next H2 (or the end).

    Duplicated from ``rebuild._digest_of`` rather than imported: that name is
    private, and ``rebuild`` is exactly the module this one must not import
    at load time (see :func:`_parse_bracket_list`) — same cycle, same fix.
    """
    lines = body.splitlines()
    try:
        start = next(i for i, line in enumerate(lines) if line.strip() == "## Digest")
    except StopIteration:
        return ""
    out: list[str] = []
    for line in lines[start + 1 :]:
        if line.startswith("## "):
            break
        out.append(line)
    return "\n".join(out).strip()


def _candidate_terms(tags: Sequence[str], title: str, digest_text: str) -> list[str]:
    """One absorbed episode's keyword candidates, deduplicated *within* the
    episode — so cross-episode frequency (below) counts "how many episodes
    raised this term", not "how many times one wordy digest repeats it".

    Only called for a non-rollup source: :func:`rollup_keywords` never mines
    tags/title/digest for a source tagged ``"rollup"`` (its own transitivity
    branch handles those instead), so ``tags`` here never contains the
    structural ``"rollup"`` marker in the first place.
    """
    seen: dict[str, None] = {}

    def _add(term: str) -> None:
        term = term.strip().lower()
        if len(term) < MIN_KEYWORD_LENGTH or term in _STOPWORDS:
            return
        seen.setdefault(term, None)

    for tag in tags:
        _add(tag)
    for token in _tokenize(title):
        _add(token)
    for token in _tokenize(digest_text):
        _add(token)
    return list(seen)


def _date_ordinal(value: str) -> int:
    """ISO date → a comparable ordinal; unparsable/missing sorts as oldest."""
    try:
        return _date.fromisoformat(value).toordinal()
    except ValueError:
        return 0


def rollup_keywords(
    sources: Sequence[tuple[Mapping[str, str], str]],
    *,
    limit: int = MAX_ROLLUP_KEYWORDS,
) -> list[str]:
    """The keyword list for a rollup absorbing ``sources``.

    Each source is ``(frontmatter_fields, raw_episode_body)`` for one
    about-to-be-archived episode, read *before* it moves — ``archive.rollup``
    already reads this to name what it archived, so no extra I/O here.

    Transitivity (#172's actual requirement, not just its symptom): a source
    that is itself a previous rollup (tagged ``"rollup"``) contributes
    *only* the keyword list already stored in its own ``keywords``
    frontmatter field, verbatim — never terms re-derived from its own tags,
    title or digest. A rollup's digest and title are generic prose about the
    rollup mechanism ("N episodes folded in...", "Q1 rollup"), not a topic;
    mining them would rank recordkeeping words over the words the first
    generation actually carried. Reading the stored field back is also exact
    where re-derivation would be lossy: the first generation may have already
    dropped lower-priority words to fit its own limit, and there is no way to
    recover those from prose alone. A rollup written before #172 has no
    ``keywords`` field and so contributes nothing — the honest answer, not a
    crash and not a fallback to mining its prose.

    Ranking / overflow rule (owner decision, #172 — documented here because
    this is the one place it is enforced): most-cited term first — cited by
    the most *distinct* absorbed units (an inherited rollup counts as one,
    regardless of how many episodes it once absorbed) — ties broken by the
    newest contributing unit's ``date``, ties on that broken alphabetically
    for full determinism. Only the top ``limit`` survive. A shelf rolling up
    quarter after quarter always ends up with more candidate words than an
    INDEX line can afford; frequency-then-freshness keeps the words several
    episodes agreed mattered and, among equals, the ones most recently live.
    """
    frequency: Counter[str] = Counter()
    freshness: dict[str, str] = {}

    def _record(term: str, date: str) -> None:
        frequency[term] += 1
        if term not in freshness or date > freshness[term]:
            freshness[term] = date

    for fields, body in sources:
        date = fields.get("date") or ""
        tags = _parse_bracket_list(fields.get("tags"))
        if "rollup" in tags:
            for term in _parse_bracket_list(fields.get("keywords")):
                _record(term.lower(), date)
            continue  # never mined — see the transitivity note above
        for term in _candidate_terms(tags, fields.get("display_title", ""), _digest_section(body)):
            _record(term, date)

    ranked = sorted(
        frequency,
        key=lambda term: (-frequency[term], -_date_ordinal(freshness[term]), term),
    )
    return ranked[:limit]


def render_keyword_list(keywords: Sequence[str]) -> str:
    """The plain, comma-joined form shared by the digest clause and the
    description — one rendering, so the two never disagree on punctuation."""
    return ", ".join(keywords)


def digest_with_keywords(
    digest: str,
    keywords: Sequence[str],
    *,
    label: str = KEYWORDS_LABEL,
    max_words: int = MAX_WORDS,
) -> tuple[str, list[str]]:
    """Append ``label: kw1, kw2, ...`` to ``digest``, fitted to ``max_words``.

    ``keywords`` is priority-ordered (:func:`rollup_keywords`'s own order,
    highest first); when the caller's own prose leaves little room, the
    *lowest*-priority keywords are dropped first — trimmed from the tail,
    never reordered — until the whole digest (caller's prose plus the clause)
    fits the contract ``digest.validate_digest`` enforces (``MAX_WORDS``
    words, #172 requirement 4). A clause that cannot fit even as a single
    keyword is omitted rather than pushed past the cap.

    Returns ``(new_digest, warnings)``. Dropping (or omitting) is reported,
    never silent — the full list still rides in the rollup's own
    ``keywords`` frontmatter field and in its ``description``, so nothing
    that fit in this call's budget is actually lost, only left out of this
    one rendering of it.
    """
    base = digest.strip()
    if not keywords:
        return base, []

    kept = list(keywords)
    while kept:
        clause = f"{label}: {render_keyword_list(kept)}."
        candidate = f"{base}\n\n{clause}" if base else clause
        if len(candidate.split()) <= max_words:
            dropped = len(keywords) - len(kept)
            warnings = (
                [
                    f"rollup digest: dropped {dropped} of {len(keywords)} keyword(s) to "
                    f"stay within the {max_words}-word digest contract; the full list is "
                    "in the episode's `keywords` frontmatter and its INDEX description"
                ]
                if dropped
                else []
            )
            return candidate, warnings
        kept = kept[:-1]

    return base, [
        f"rollup digest: no room for a keywords clause within the {max_words}-word "
        "digest contract; the full list is in the episode's `keywords` frontmatter "
        "and its INDEX description"
    ]
