"""The shelve response must describe the shelf that exists (#98, #99).

Since #58 the write is the episode alone; ledger.tsv/INDEX.md are rendered by
`rebuild` or the shelf's bot. Three false findings in a row (08.08, 15.08,
16.08) came from the response pretending otherwise: the tool docstring
promised a ledger append, and `shelf_totals` quietly reported the state as of
the last rebuild. The response now says whose numbers it is quoting, counts
the disk, and names the one step that makes the derived layer catch up.
"""

import subprocess

import pytest

pytest.importorskip("docshelf_mcp")

from docshelf_mcp.core.shelf import Shelf  # noqa: E402

from memshelf_mcp.core.rebuild import rebuild  # noqa: E402
from memshelf_mcp.tools import ShelveInput, run_shelve  # noqa: E402

GOOD_DIGEST = (
    "The auth refactor moved token checks into middleware; the decided approach "
    "is JWT with a shared secret. The cookie-session alternative was rejected "
    "for cross-service calls. Open: rotating the shared secret."
)


def _init_shelf(root, *, git=True, origin=None):
    """A fresh shelf: plain, git-local (git, no remote) or, given ``origin``,
    a clone with a bare remote it has never pushed to."""
    root.mkdir(parents=True, exist_ok=True)
    Shelf(root).init(name="test shelf", default_categories=["topics", "research", "sessions"])
    if git:
        subprocess.run(["git", "-C", str(root), "init", "-q"], check=True)
        subprocess.run(["git", "-C", str(root), "config", "user.email", "t@t.test"], check=True)
        subprocess.run(["git", "-C", str(root), "config", "user.name", "tester"], check=True)
    if origin is not None:
        subprocess.run(["git", "init", "-q", "--bare", str(origin)], check=True)
        subprocess.run(["git", "-C", str(root), "remote", "add", "origin", str(origin)], check=True)
    return root


def _git(root, *args):
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)


def _shelve(root, slug="2026-07-22-auth-refactor"):
    return run_shelve(
        ShelveInput(
            shelf_path=str(root),
            slug=slug,
            kind="topic",
            digest=GOOD_DIGEST,
            sections={"Decisions": "JWT chosen."},
            approx_tokens=4000,
            date=slug[:10],
        )
    )


def test_the_response_marks_the_derived_layer_as_stale(tmp_path):
    """Right after a shelve the ledger cannot know the episode — say so."""
    resp = _shelve(_init_shelf(tmp_path))
    totals = resp["shelf_totals"]
    assert totals["as_of"] == "last-rebuild"
    assert totals["episodes"] == 0  # what the (absent) ledger claims
    assert totals["episodes_on_disk"] == 1  # what the disk holds
    assert totals["derived_stale"] is True
    assert "derived lag 1 episode(s)" in resp["summary"]


def test_the_response_says_what_makes_derived_catch_up(tmp_path):
    """#99: the missing link was push (bot shelf) / rebuild (botless), named."""
    resp = _shelve(_init_shelf(tmp_path / "shelf", origin=tmp_path / "origin.git"))
    # A committed-but-unpushed episode on a botless shelf: both halves named.
    assert "push" in resp["next"]
    assert "memshelf rebuild" in resp["next"]


def test_a_bot_shelf_is_told_to_push_not_to_rebuild(tmp_path):
    """A manual rebuild on a bot shelf recreates the #58 conflict class."""
    root = _init_shelf(tmp_path / "shelf", origin=tmp_path / "origin.git")
    bot = root / ".github" / "workflows" / "shelf-derived.yml"
    bot.parent.mkdir(parents=True)
    bot.write_text("name: shelf-derived\n", encoding="utf-8")
    resp = _shelve(root)
    assert "push" in resp["next"]
    assert "rebuild" not in resp["next"]


def test_a_git_local_shelf_is_not_told_to_push(tmp_path):
    """git-local is git without a remote (ARCHITECTURE, storage modes): the
    commit is the end of the line. The old wording sent the caller to push and
    to a sync.hint such a shelf never gets (seen in #176)."""
    resp = _shelve(_init_shelf(tmp_path))
    assert resp["committed"] is True
    assert resp["sync"]["hint"] is None
    assert "no remote" in resp["next"]
    assert "push it" not in resp["next"]
    assert "sync.hint" not in resp["next"]
    assert "memshelf rebuild" in resp["next"]


def test_a_git_local_shelf_rebuilds_even_with_a_bot_workflow(tmp_path):
    """No remote, no bot run: the workflow file alone renders nothing."""
    root = _init_shelf(tmp_path)
    bot = root / ".github" / "workflows" / "shelf-derived.yml"
    bot.parent.mkdir(parents=True)
    bot.write_text("name: shelf-derived\n", encoding="utf-8")
    resp = _shelve(root)
    assert "no remote" in resp["next"]
    assert "memshelf rebuild" in resp["next"]


def test_sync_hint_is_cited_only_when_the_response_carries_one(tmp_path):
    """A remote but no hint (unborn branch, nothing to sync onto) → push, no pointer."""
    resp = _shelve(_init_shelf(tmp_path / "shelf", origin=tmp_path / "origin.git"))
    assert resp["sync"]["hint"] is None
    assert "push it" in resp["next"]
    assert "sync.hint" not in resp["next"]


def test_a_tracking_clone_gets_the_hint_and_the_pointer_to_it(tmp_path):
    root = _init_shelf(tmp_path / "shelf", origin=tmp_path / "origin.git")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "init shelf")
    _git(root, "push", "-q", "-u", "origin", "HEAD")
    resp = _shelve(root)
    assert resp["sync"]["hint"] is not None
    assert "push it (see sync.hint)" in resp["next"]


def test_a_plain_shelf_is_told_to_rebuild(tmp_path):
    resp = _shelve(_init_shelf(tmp_path, git=False))
    assert "memshelf rebuild" in resp["next"]
    assert "push" not in resp["next"]


def test_totals_agree_once_the_derived_layer_is_rendered(tmp_path):
    """After a rebuild the two counts converge and the marker goes quiet."""
    root = _init_shelf(tmp_path)
    _shelve(root)
    rebuild(root)
    subprocess.run(["git", "-C", str(root), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(root), "commit", "-qm", "derived"], check=True)
    resp = _shelve(root, slug="2026-07-23-second-topic")
    totals = resp["shelf_totals"]
    assert totals["episodes"] == 1  # the ledger knows the first episode
    assert totals["episodes_on_disk"] == 2  # the disk already holds both
    assert totals["derived_stale"] is True


def test_the_tool_docstring_no_longer_promises_a_ledger_append(tmp_path):
    """#98 item 1: the first thing a calling agent reads must not lie."""
    pytest.importorskip("mcp")
    from memshelf_mcp import server

    doc = server.memshelf_shelve.__doc__ or ""
    assert "appends the ledger row" not in doc
    assert "rendered by" in doc
