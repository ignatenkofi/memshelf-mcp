from memshelf_mcp.core.frontmatter import parse_frontmatter


def test_h1_first_layout():
    text = (
        "# 2026-07-22-x\n\n"
        "---\nid: 2026-07-22-x\nkind: topic\ntags: [a, b]\n---\n\n"
        "## Digest\nbody\n"
    )
    fields, body = parse_frontmatter(text)
    assert fields["id"] == "2026-07-22-x"
    assert fields["kind"] == "topic"
    assert body.lstrip().startswith("## Digest")


def test_frontmatter_at_byte_zero():
    text = "---\nid: x\nkind: research\n---\n\n## Digest\nb\n"
    fields, body = parse_frontmatter(text)
    assert fields["kind"] == "research"
    assert "## Digest" in body


def test_no_frontmatter_returns_empty():
    text = "# Title\n\nJust prose, no fence.\n"
    fields, body = parse_frontmatter(text)
    assert fields == {}
    assert body == text
