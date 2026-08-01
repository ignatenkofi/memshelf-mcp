import pytest

from memshelf_mcp.core.episode import EpisodeError, Frontmatter, compose_episode


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
