"""Archive-as-raw-material views (#18): tags, graph, retro, fork, mirror."""

import subprocess

import pytest

pytest.importorskip("docshelf_mcp")

from docshelf_mcp.core.shelf import Shelf  # noqa: E402

from memshelf_mcp.cli import main  # noqa: E402
from memshelf_mcp.core import reuse  # noqa: E402
from memshelf_mcp.core.archive import rollup  # noqa: E402
from memshelf_mcp.core.rebuild import rebuild  # noqa: E402
from memshelf_mcp.core.recall import EpisodeNotFound  # noqa: E402
from memshelf_mcp.core.shelve import shelve  # noqa: E402

AUTH = "2026-06-22-auth"
ROTATION = "2026-08-03-secret-rotation"
BENCH = "2026-08-10-search-bench"
LONE = "2026-09-01-unrelated"


def _shelf(root):
    """Four episodes: two of them talk about a third, one talks to nobody."""
    Shelf(root).init(name="t", default_categories=["topics", "research", "sessions"])
    subprocess.run(["git", "-C", str(root), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.email", "t@t.test"], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.name", "tester"], check=True)
    shelve(
        root,
        slug=AUTH,
        kind="topic",
        digest="The auth refactor chose JWT; cookie-session was rejected. Open: secret rotation.",
        sections={"Decisions": "JWT chosen.", "Open threads": "rotate the shared secret"},
        display_title="Auth refactor",
        description="JWT over cookie sessions",
        tags=["auth", "jwt"],
        date="2026-06-22",
    )
    shelve(
        root,
        slug=ROTATION,
        kind="session",
        digest="Rotated the JWT secret; the plan from the auth episode held. <b>&</b> done.",
        sections={
            "Decisions": f"Follows {AUTH}: rotation every 30 days.",
            "Timeline": "one afternoon",
            "Open threads": f"{AUTH}-followup is a different id and must not count",
        },
        display_title="Secret rotation",
        description="rotation cadence settled",
        tags=["auth", "ops"],
        date="2026-08-03",
    )
    shelve(
        root,
        slug=BENCH,
        kind="research",
        digest="Search bench: grep baseline measured.",
        sections={"Findings": "grep misses paraphrases", "Open threads": f"revisit {AUTH} search"},
        display_title="Search bench",
        description="grep baseline",
        tags=["search"],
        date="2026-08-10",
    )
    shelve(
        root,
        slug=LONE,
        kind="session",
        digest="Nothing to do with the others.",
        sections={"Timeline": "n/a", "Open threads": "none"},
        display_title="Unrelated",
        description="isolated",
        date="2026-09-01",
    )
    rebuild(root)
    return root


# --- load / tags --------------------------------------------------------------


def test_load_episodes_reads_tags_kinds_and_order(tmp_path):
    episodes = reuse.load_episodes(_shelf(tmp_path))
    assert [e.id for e in episodes] == [AUTH, ROTATION, BENCH, LONE]
    by_id = {e.id: e for e in episodes}
    assert by_id[AUTH].tags == ("auth", "jwt")
    assert by_id[AUTH].kind == "topic" and by_id[BENCH].kind == "research"
    assert by_id[LONE].tags == ()
    assert by_id[AUTH].relative_path == f"docs/topics/{AUTH}.md"
    assert by_id[AUTH].section("Decisions").strip() == "JWT chosen."
    assert by_id[AUTH].section("Timeline") is None


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("[a, b]", ("a", "b")),
        ("[]", ()),
        ("", ()),
        (None, ()),
        ("['x', \"y z\", x]", ("x", "y z")),
        ("bare, list", ("bare", "list")),
    ],
)
def test_parse_tags(raw, expected):
    assert reuse.parse_tags(raw) == expected


def test_tags_group_by_tag_most_used_first(tmp_path):
    report = reuse.tags(_shelf(tmp_path))
    assert list(report.tags) == ["auth", "jwt", "ops", "search"]
    assert report.tags["auth"] == [AUTH, ROTATION]
    assert report.untagged == [LONE]


# --- graph --------------------------------------------------------------------


def test_graph_edges_come_from_whole_id_mentions_with_their_section(tmp_path):
    g = reuse.graph(_shelf(tmp_path))
    assert {n.id for n in g.nodes} == {AUTH, ROTATION, BENCH, LONE}
    edges = {(e.source, e.target, e.section) for e in g.edges}
    assert edges == {
        (ROTATION, AUTH, "Decisions"),
        (BENCH, AUTH, "Open threads"),
    }
    # `2026-06-22-auth-followup` in ROTATION's Open threads is a different id.
    assert g.mentions() == {AUTH: 2}


def test_graph_can_be_restricted_to_one_section(tmp_path):
    g = reuse.graph(_shelf(tmp_path), section="decisions")
    assert [(e.source, e.target) for e in g.edges] == [(ROTATION, AUTH)]


def test_graph_mermaid_draws_only_connected_episodes_by_default(tmp_path):
    g = reuse.graph(_shelf(tmp_path))
    mermaid = g.to_mermaid()
    assert mermaid.startswith("graph LR\n")
    assert AUTH in mermaid and ROTATION in mermaid and LONE not in mermaid
    assert '-- "Decisions" -->' in mermaid
    assert LONE in g.to_mermaid(connected_only=False)


def test_graph_json_round_trips_and_drops_bodies(tmp_path):
    import json

    data = json.loads(reuse.graph(_shelf(tmp_path)).to_json())
    assert {n["id"] for n in data["nodes"]} == {AUTH, ROTATION, BENCH, LONE}
    assert all("body" not in n for n in data["nodes"])
    assert data["edges"][0] == {"source": ROTATION, "target": AUTH, "section": "Decisions"}


def test_graph_and_tags_see_the_archive(tmp_path):
    root = _shelf(tmp_path)
    rollup(
        root,
        slug="2026-09-05-q3-rollup",
        digest="Q3 rolled up.",
        episode_ids=[ROTATION],
        display_title="Q3 rollup",
        date="2026-09-05",
    )
    episodes = {e.id: e for e in reuse.load_episodes(root)}
    assert episodes[ROTATION].archived is True
    assert episodes[ROTATION].relative_path.startswith("archive/docs/")
    assert reuse.tags(root).tags["auth"] == [AUTH, ROTATION]
    edges = {(e.source, e.target) for e in reuse.graph(root).edges}
    assert (ROTATION, AUTH) in edges  # archived episodes keep their mentions
    assert ("2026-09-05-q3-rollup", ROTATION) in edges  # the rollup names what it holds
    assert "(" in reuse.graph(root).to_mermaid()  # archived nodes get the round shape
    g = reuse.graph(root)
    assert g.mentions()[ROTATION] == 1  # the rollup lists it
    assert g.mentions(exclude_sections=("Archived",))[ROTATION] == 0  # nobody discusses it
    text = reuse.retro(root, "2026Q3")
    assert "1 archived" in text
    assert f"### `{ROTATION}`" not in text  # a rolled-up episode's open threads stay rolled up
    assert f"### `{BENCH}`" in text


# --- retro --------------------------------------------------------------------


def test_quarter_helpers():
    assert reuse.quarter_months("2026Q3") == ["2026-07", "2026-08", "2026-09"]
    assert reuse.quarter_months("2026q1") == ["2026-01", "2026-02", "2026-03"]
    assert reuse.quarter_of("2026-09-10") == "2026Q3"
    assert reuse.quarter_of("2026-01-01") == "2026Q1"
    with pytest.raises(ValueError):
        reuse.quarter_months("Q3-2026")


def test_retro_lists_the_quarter_only(tmp_path):
    text = reuse.retro(_shelf(tmp_path), "2026Q3")
    assert text.startswith("# Retro 2026Q3\n")
    assert "3 episodes (1 research, 2 session); 0 archived." in text
    assert "## 2026-08 (2)" in text and "## 2026-09 (1)" in text
    assert "## 2026-07" not in text
    assert (
        f"**Secret rotation** (`{ROTATION}`) — rotation cadence settled · tags: auth, ops" in text
    )
    assert f"(`{AUTH}`)" not in text  # a June episode is out of scope
    assert "## Open threads (3 live episodes)" in text and "revisit" in text
    assert "Tags: auth (1), ops (1), search (1)." in text


def test_retro_on_an_empty_quarter_says_so(tmp_path):
    text = reuse.retro(_shelf(tmp_path), "2025Q4")
    assert "No episodes dated 2025-10..2025-12." in text


# --- fork ---------------------------------------------------------------------


def test_fork_bundles_index_and_whole_episodes_in_the_envelope(tmp_path):
    text = reuse.fork(_shelf(tmp_path), [AUTH, ROTATION], today="2026-09-10")
    assert text.startswith(f"# Fork of {AUTH}, {ROTATION}\n")
    assert "## Shelf INDEX" in text
    assert text.count("<recalled-episode") == 3  # INDEX + two episodes
    assert text.count("</recalled-episode>") == 3
    assert "## Decisions\nJWT chosen." in text
    assert "rotation every 30 days" in text
    assert "kind: session" in text  # whole file, frontmatter included
    assert "2026-09-10" in text


def test_fork_can_take_sections_only_and_skip_the_index(tmp_path):
    text = reuse.fork(_shelf(tmp_path), [AUTH], sections=["Decisions"], with_index=False)
    assert "## Shelf INDEX" not in text
    assert text.count("<recalled-episode") == 1
    assert "JWT chosen." in text
    assert "## Digest\nThe auth refactor chose JWT" in text  # the Digest always travels
    assert text.count("## Digest") == 1
    assert "rotate the shared secret" not in text
    assert "kind: topic" not in text


def test_fork_refuses_holes(tmp_path):
    root = _shelf(tmp_path)
    with pytest.raises(EpisodeNotFound, match="no-such"):
        reuse.fork(root, [AUTH, "2026-01-01-no-such"])
    with pytest.raises(EpisodeNotFound, match="Timeline"):
        reuse.fork(root, [AUTH], sections=["Timeline"])
    with pytest.raises(EpisodeNotFound):
        reuse.fork(root, [])


# --- mirror -------------------------------------------------------------------


def test_mirror_is_one_static_page_with_anchors_for_included_episodes(tmp_path):
    html = reuse.mirror(_shelf(tmp_path), episode_ids=[ROTATION], today="2026-09-10")
    assert html.startswith("<!doctype html>")
    assert "<script" not in html and "http" not in html.split("<body>")[0]
    assert 'name="viewport"' in html
    assert f'<article id="ep-{ROTATION}">' in html
    assert f'href="#ep-{ROTATION}"' in html  # INDEX line links to the carried episode
    assert f"docs/topics/{AUTH}.md" not in html.split("<article")[0].replace("<code>", "")
    assert "&lt;b&gt;&amp;&lt;/b&gt;" in html  # episode text is escaped, not injected
    assert "<h3>Decisions</h3>" in html  # H2 in the episode sits under the article's H2
    assert "auth, ops" in html


def test_mirror_all_carries_every_live_episode(tmp_path):
    html = reuse.mirror(_shelf(tmp_path), include_all=True)
    assert html.count("<article id=") == 4
    with pytest.raises(EpisodeNotFound):
        reuse.mirror(tmp_path, episode_ids=["2026-01-01-no-such"])


# --- CLI ----------------------------------------------------------------------


def test_cli_tags_graph_retro(tmp_path, capsys):
    root = str(_shelf(tmp_path))
    assert main(["tags", "--shelf", root]) == 0
    out = capsys.readouterr().out
    assert out.splitlines()[0] == f"auth\t2\t{AUTH} {ROTATION}"
    assert out.splitlines()[-1] == f"(untagged)\t1\t{LONE}"

    assert main(["tags", "--shelf", root, "--json"]) == 0
    assert '"untagged"' in capsys.readouterr().out

    assert main(["graph", "--shelf", root, "--format", "mermaid", "--section", "Decisions"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("graph LR") and BENCH not in out

    assert main(["retro", "--shelf", root, "--quarter", "2026Q3"]) == 0
    assert "# Retro 2026Q3" in capsys.readouterr().out
    assert main(["retro", "--shelf", root, "--quarter", "third"]) == 2


def test_cli_fork_and_mirror_write_files(tmp_path, capsys):
    root = str(_shelf(tmp_path))
    fork_path = tmp_path / "fork.md"
    rc = main(
        [
            "fork",
            "--shelf",
            root,
            "--episode",
            AUTH,
            "--episode",
            BENCH,
            "--section",
            "Open threads",
            "--out",
            str(fork_path),
        ]
    )
    assert rc == 0
    captured = capsys.readouterr()
    assert captured.out == "" and "wrote" in captured.err
    forked = fork_path.read_text()
    assert forked.count("<recalled-episode") == 3
    assert "JWT chosen" not in forked and "revisit" in forked

    assert main(["fork", "--shelf", root, "--episode", "2026-01-01-nope"]) == 1
    assert "no such episode" in capsys.readouterr().err

    page = tmp_path / "index.html"
    assert main(["mirror", "--shelf", root, "--all", "--out", str(page)]) == 0
    assert page.read_text().count("<article id=") == 4
