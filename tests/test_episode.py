import pytest

from memshelf_mcp.core.episode import (
    MAX_DESCRIPTION_CHARS,
    EpisodeError,
    Frontmatter,
    clamp_description,
    compose_episode,
    yaml_scalar,
)


def _fm(kind="topic", **kw):
    return Frontmatter(id="2026-07-22-x", kind=kind, **kw)


def test_compose_is_h1_first_with_frontmatter_and_digest():
    md = compose_episode(
        _fm(tags=("a", "b"), approx_tokens=100), "A decided thing.", {"Decisions": "d"}
    )
    lines = md.splitlines()
    assert lines[0] == "# 2026-07-22-x"
    assert lines[2] == "---"
    assert "id: 2026-07-22-x" in md
    assert "tags: [a, b]" in md
    assert "## Digest" in md and "## Decisions" in md


def test_sections_are_ordered_and_empty_ones_omitted():
    md = compose_episode(
        _fm(),
        "A decided thing.",
        {"Raw excerpts": "raw", "Decisions": "d", "Artifacts": "", "Timeline": "t"},
    )
    # canonical order: Decisions, Timeline, Artifacts(omitted), Raw excerpts
    assert md.index("## Decisions") < md.index("## Timeline") < md.index("## Raw excerpts")
    assert "## Artifacts" not in md


def test_topic_requires_decisions():
    with pytest.raises(EpisodeError):
        compose_episode(_fm("topic"), "digest only", {})


def test_session_requires_timeline_and_open_threads():
    with pytest.raises(EpisodeError):
        compose_episode(_fm("session"), "digest", {"Timeline": "t"})  # missing Open threads


def test_research_requires_one_body_section():
    with pytest.raises(EpisodeError):
        compose_episode(_fm("research"), "digest only", {})
    ok = compose_episode(_fm("research"), "digest", {"Findings": "f"})
    assert "## Findings" in ok


def test_unknown_kind_rejected():
    with pytest.raises(EpisodeError):
        compose_episode(_fm("journal"), "digest", {"Decisions": "d"})


def test_missing_digest_rejected():
    with pytest.raises(EpisodeError):
        compose_episode(_fm("topic"), "   ", {"Decisions": "d"})


# --- frontmatter must be valid YAML, not just parseable by us ---------------


def test_title_with_a_colon_survives_a_round_trip():
    """The defect this guards: `display_title: Охота: Грузия` parses fine with
    memshelf's own splitter and is a YAML syntax error for a real loader —
    shelf-spec's validator then reports the episode as having NO frontmatter."""
    from memshelf_mcp.core.frontmatter import parse_frontmatter

    fm = Frontmatter(
        id="2026-07-31-x",
        kind="topic",
        span="2026-07-31",
        display_title="Охота на X1 Carbon: Грузия/Армения",
        description="Один конфиг: сербский, ~2910 €",
        notes="chat-2: fragment",
    )
    text = compose_episode(fm, "Digest text.", {"Decisions": "X"})
    fields, _ = parse_frontmatter(text)
    assert fields["display_title"] == "Охота на X1 Carbon: Грузия/Армения"
    assert fields["description"] == "Один конфиг: сербский, ~2910 €"
    assert fields["notes"] == "chat-2: fragment"


def test_frontmatter_block_loads_as_yaml():
    """Belt and braces: parse the block with a real YAML loader, the way the
    shelves' advisory validator does."""
    yaml = pytest.importorskip("yaml")

    fm = Frontmatter(
        id="2026-07-31-x",
        kind="topic",
        span="2026-07-31",
        display_title='Заголовок с "кавычками" и: двоеточием',
        notes="a\\backslash",
    )
    text = compose_episode(fm, "Digest text.", {"Decisions": "X"})
    block = text.split("---\n")[1]
    loaded = yaml.safe_load(block)
    assert loaded["display_title"] == 'Заголовок с "кавычками" и: двоеточием'
    assert loaded["notes"] == "a\\backslash"
    assert loaded["id"] == "2026-07-31-x"


# --- clamp_description: the degenerate inputs found in review ---------------


@pytest.mark.parametrize(
    "text",
    [
        "- " + "х" * 200,  # no space after the first two chars
        "Итог: " + "y" * 200,  # one space, very early
        "See https://example.com/" + "a" * 200 + " end",  # a long unbroken URL
        "а" * 200,  # no space at all
        " " * 119 + "слово" * 40,  # only leading spaces in the head
        "хвост, " + "z" * 200,  # head would `rstrip` down to nothing
    ],
)
def test_clamp_keeps_most_of_the_budget_whatever_the_input(text):
    """A word-boundary cut must not collapse the value.

    Cutting at the *last* space in the head unconditionally turned
    "Итог: yyyy…" into "Итог…" and could `rstrip` a head down to a bare
    ellipsis — a description destroyed in the name of tidiness, silently, on
    every rebuild. Found in review; the cut now falls back to a hard one when
    the clean boundary would keep too little.
    """
    kept, warning = clamp_description(text)

    assert warning is not None
    assert len(kept) <= MAX_DESCRIPTION_CHARS
    assert len(kept) >= MAX_DESCRIPTION_CHARS * 2 // 3
    assert kept.endswith("…")
    assert kept.strip("… ")  # never a bare ellipsis


def test_clamp_reports_the_length_it_actually_produced():
    """The warning said "cut to 120" while returning a single character."""
    kept, warning = clamp_description("Итог: " + "y" * 200)

    assert f"cut to {len(kept)}" in warning


@pytest.mark.parametrize(
    "text,expected",
    [
        (None, ""),
        ("", ""),
        ("   ", ""),
        ("x" * MAX_DESCRIPTION_CHARS, "x" * MAX_DESCRIPTION_CHARS),
    ],
)
def test_clamp_leaves_anything_within_budget_untouched(text, expected):
    kept, warning = clamp_description(text)
    assert (kept, warning) == (expected, None)


def test_clamp_survives_the_frontmatter_round_trip():
    """The clamped value is written into `description:`, so it has to come back
    out of the parser as the same string."""
    from memshelf_mcp.core.frontmatter import parse_frontmatter

    kept, _ = clamp_description('Он сказал "да": и вот # что {из} [этого] — ' + "ы" * 200)
    body = (
        f"# t\n\n---\nid: t\nkind: topic\nspan: 2026-08-21\ndescription: {yaml_scalar(kept)}\n---\n"
    )
    fields, _ = parse_frontmatter(body)

    assert fields["description"] == kept
