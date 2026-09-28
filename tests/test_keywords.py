"""Keyword carry-over for rollups (#172) — pure functions, no shelf needed.

The property under test: a rollup's INDEX line and digest must still say
*what* was absorbed, not just *how many* — and a rollup that absorbs a
previous rollup must not lose the first generation's words (transitivity),
which is the exact defect #172 reported after two rollup generations.
"""

from memshelf_mcp.core.digest import MAX_WORDS, validate_digest
from memshelf_mcp.core.keywords import (
    MAX_ROLLUP_KEYWORDS,
    digest_with_keywords,
    render_keyword_list,
    rollup_keywords,
)

#: NATO-alphabet stand-ins: distinct, unambiguous, well over MIN_KEYWORD_LENGTH,
#: and alphabetically ordered — handy for overflow tests that pin exactly which
#: entries the frequency/freshness rule keeps.
WORDS = [
    "alpha",
    "bravo",
    "charlie",
    "delta",
    "echo",
    "foxtrot",
    "golf",
    "hotel",
    "india",
    "juliett",
    "kilo",
    "lima",
]


def _episode(*, date, tags="[]", title="", digest="", keywords=None):
    """One (fields, body) source as `archive.rollup` would read it."""
    fields = {"date": date, "tags": tags, "display_title": title}
    if keywords is not None:
        fields["keywords"] = f"[{', '.join(keywords)}]"
    body = f"## Digest\n{digest}\n"
    return fields, body


# --- extraction ---------------------------------------------------------


def test_keywords_come_from_tags_title_and_digest_words():
    source = _episode(
        date="2026-01-01",
        tags="[keychain, macos]",
        title="Keychain rotation",
        digest="Ключи хранятся в Keychain, ротация выполнена вручную.",
    )
    kw = rollup_keywords([source])
    assert "keychain" in kw
    assert "macos" in kw
    assert "rotation" in kw
    assert "ротация" in kw


def test_a_rollup_tagged_source_is_never_mined_for_its_own_tags_title_or_digest():
    """Only its stored `keywords` field counts. A rollup's own tags/title/
    digest are its mechanical prose ("Q1 rollup", "N episodes folded in"),
    not a topic — see the legacy-rollup test below for the case where the
    `keywords` field itself is absent."""
    source = _episode(
        date="2026-01-01",
        tags="[rollup, keychain]",
        title="Роллап Q1",
        digest="Свёрнуто эпизодов: 3.",
        keywords=["vlan"],
    )
    kw = rollup_keywords([source])
    assert kw == ["vlan"]
    assert "keychain" not in kw
    assert "rollup" not in kw
    assert "роллап" not in kw


def test_short_and_stopword_tokens_are_dropped():
    source = _episode(
        date="2026-01-01",
        title="that with from being",
        digest="этот когда через если который",
    )
    assert rollup_keywords([source]) == []


def test_no_duplicates_within_or_across_episodes():
    a = _episode(date="2026-01-01", title="vlan vlan", digest="vlan again vlan")
    b = _episode(date="2026-01-02", title="vlan setup", digest="vlan once more")
    kw = rollup_keywords([a, b])
    assert kw.count("vlan") == 1


def test_deterministic_across_repeated_calls():
    sources = [
        _episode(
            date="2026-01-01",
            tags="[auth, jwt]",
            title="Auth refactor",
            digest="JWT middleware token session cookie",
        ),
        _episode(
            date="2026-01-02",
            tags="[jwt]",
            title="Auth followups",
            digest="token rotation jwt secret",
        ),
    ]
    assert rollup_keywords(sources) == rollup_keywords(sources)


# --- ranking / overflow (owner decision, #172: frequency then freshness) ---


def test_frequency_outranks_a_single_fresher_mention():
    common_old = _episode(date="2026-01-01", title="alpha", digest="shared theme")
    common_newer = _episode(date="2026-01-05", title="alpha", digest="more detail")
    single_fresh = _episode(date="2026-02-01", title="beta", digest="later still")
    kw = rollup_keywords([common_old, common_newer, single_fresh])
    assert kw.index("alpha") < kw.index("beta")


def test_freshness_breaks_a_frequency_tie():
    older = _episode(date="2026-01-01", title="older")
    newer = _episode(date="2026-06-01", title="newer")
    kw = rollup_keywords([older, newer])
    assert kw.index("newer") < kw.index("older")


def test_overflow_is_capped_at_max_rollup_keywords():
    sources = [_episode(date=f"2026-01-{i + 1:02d}", title=w) for i, w in enumerate(WORDS)]
    assert len(WORDS) > MAX_ROLLUP_KEYWORDS  # fixture actually exercises the cap
    kw = rollup_keywords(sources)
    assert len(kw) == MAX_ROLLUP_KEYWORDS


def test_overflow_keeps_the_freshest_when_frequency_ties():
    sources = [_episode(date=f"2026-01-{i + 1:02d}", title=w) for i, w in enumerate(WORDS)]
    kw = rollup_keywords(sources)
    # every word has frequency 1, so freshness alone decides: the latest
    # MAX_ROLLUP_KEYWORDS words survive, most recent first.
    assert kw == list(reversed(WORDS))[:MAX_ROLLUP_KEYWORDS]


def test_overflow_prefers_frequency_over_freshness_then_drops_the_oldest():
    # "common" is raised by three old episodes (frequency 3); eight newer
    # episodes each raise one unique word (frequency 1 apiece) — nine
    # distinct terms competing for eight slots.
    sources = [_episode(date=f"2026-01-{i + 1:02d}", title="common") for i in range(3)]
    sources += [_episode(date=f"2026-06-{i + 1:02d}", title=w) for i, w in enumerate(WORDS[:8])]

    kw = rollup_keywords(sources, limit=MAX_ROLLUP_KEYWORDS)

    assert len(kw) == MAX_ROLLUP_KEYWORDS
    assert kw[0] == "common"  # frequency 3 beats every frequency-1 term, however fresh
    assert "alpha" not in kw  # frequency tied at 1 — but the *oldest* of that group
    assert "hotel" in kw  # the freshest of the frequency-1 group survives


# --- transitivity (#172's actual requirement) ---------------------------


def test_a_nested_rollup_contributes_its_own_stored_keywords_verbatim():
    nested_rollup = _episode(
        date="2026-03-01",
        tags="[rollup]",
        title="Роллап Q1",
        digest="Роллап: 3 эп. Ключевые слова поглощённого: keychain, rotation.",
        keywords=["keychain", "rotation"],
    )
    fresh = _episode(date="2026-04-01", title="topic", digest="unrelated content here")

    kw = rollup_keywords([nested_rollup, fresh])

    assert "keychain" in kw
    assert "rotation" in kw
    # the rollup's own boilerplate prose must not leak in as if it were a topic
    assert "роллап" not in kw
    assert "поглощённого" not in kw


def test_a_nested_rollup_with_no_stored_keywords_contributes_nothing_silently():
    """An old rollup written before #172 has no `keywords` field. It must not
    crash, and must not be mined for its generic digest text either — it
    simply has nothing to hand forward, which is the honest answer."""
    legacy_rollup = _episode(
        date="2026-03-01",
        tags="[rollup]",
        title="Роллап Q1",
        digest="Роллап: 3 эп. Свёрнуто эпизодов: 3.",
    )
    kw = rollup_keywords([legacy_rollup])
    assert kw == []


# --- folding into text under budget --------------------------------------


def test_render_keyword_list_is_a_plain_comma_join():
    assert render_keyword_list(["alpha", "beta"]) == "alpha, beta"
    assert render_keyword_list([]) == ""


def test_digest_with_keywords_appends_a_fitting_clause_and_passes_the_contract():
    digest, warnings = digest_with_keywords("Short caller digest.", ["keychain", "vlan"])
    assert "keychain" in digest and "vlan" in digest
    assert warnings == []
    assert validate_digest(digest).ok, validate_digest(digest).report()


def test_digest_with_keywords_is_a_noop_with_no_keywords():
    digest, warnings = digest_with_keywords("Short caller digest.", [])
    assert digest == "Short caller digest."
    assert warnings == []


def test_digest_with_keywords_drops_the_lowest_priority_tail_to_fit_the_budget():
    base = " ".join(f"word{i}" for i in range(115))  # 115 words, 5 left in budget
    digest, warnings = digest_with_keywords(base, ["alpha", "beta", "gamma", "delta"])

    assert len(digest.split()) <= MAX_WORDS
    assert validate_digest(digest).ok, validate_digest(digest).report()
    assert warnings and "dropped" in warnings[0]
    # priority order preserved: the trailing, lower-priority keywords go first.
    assert "alpha" in digest
    assert "delta" not in digest


def test_digest_with_keywords_omits_the_clause_entirely_when_there_is_no_room():
    full = " ".join(f"word{i}" for i in range(MAX_WORDS))  # already at the cap
    digest, warnings = digest_with_keywords(full, ["alpha"])

    assert digest == full
    assert warnings and "no room" in warnings[0]
