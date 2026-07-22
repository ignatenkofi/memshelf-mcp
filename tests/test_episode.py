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
