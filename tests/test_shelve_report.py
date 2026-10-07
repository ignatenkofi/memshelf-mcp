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


def _shelve(root, slug="2026-07-22-auth-refactor", **extra):
    return run_shelve(
        ShelveInput(
            shelf_path=str(root),
            slug=slug,
            kind="topic",
            digest=GOOD_DIGEST,
            sections={"Decisions": "JWT chosen."},
            approx_tokens=4000,
            date=slug[:10],
            **extra,
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


# --- #191: a branch the bot never renders ends in a PR, not in «done» --------


def _shelf_on_main(tmp_path, *, bot=True, origin_head=None):
    """A shelf whose `main` is pushed and tracked — the render branch.

    ``origin_head`` builds the clone's `refs/remotes/origin/HEAD` explicitly:
    None deletes it (the agent-session clone measured 2026-10-02 has none),
    a name points it there. git ≥ 2.48 creates it by itself on a full fetch,
    so the state is built here rather than left to the git version.
    """
    root = _init_shelf(tmp_path / "shelf", origin=tmp_path / "origin.git")
    _git(root, "checkout", "-q", "-b", "main")
    if bot:
        wf = root / ".github" / "workflows" / "shelf-derived.yml"
        wf.parent.mkdir(parents=True)
        wf.write_text("name: shelf-derived\n", encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "init shelf")
    _git(root, "push", "-q", "-u", "origin", "main")
    if origin_head is None:
        subprocess.run(["git", "-C", str(root), "remote", "set-head", "origin", "-d"], check=False)
    else:
        _git(root, "remote", "set-head", "origin", origin_head)
    return root


def _on_session_branch(root, *, tracks="own"):
    """Move onto `claude/probe`: tracking its own name on origin (pushed with
    `-u`), or `origin/main` — `git checkout -B claude/probe origin/main`, how
    night shifts start."""
    if tracks == "own":
        _git(root, "checkout", "-q", "-b", "claude/probe")
        _git(root, "push", "-q", "-u", "origin", "HEAD")
    else:
        _git(root, "checkout", "-q", "-B", "claude/probe", "origin/main")
    return root


@pytest.mark.parametrize("origin_head", [None, "main"])
def test_a_push_to_a_session_branch_names_the_pr_not_nothing_else_to_do(tmp_path, origin_head):
    """The #191 repro: the push went to `origin/claude/probe`, which the bot
    never renders, and `next` said «nothing else to do» to an unattended
    session. The render branch is resolved as doctor resolves it — with and
    without `origin/HEAD`."""
    root = _on_session_branch(_shelf_on_main(tmp_path, origin_head=origin_head))

    resp = _shelve(root, push=True, await_render_s=0)

    assert resp["sync"]["pushed"] is True
    nxt = resp["next"]
    assert "nothing else to do" not in nxt
    assert "pushed to origin/claude/probe" in nxt
    assert "a PR into main" in nxt
    assert "after the merge" in nxt
    assert "do not run `memshelf rebuild` by hand" in nxt


def test_an_unpushed_commit_on_a_session_branch_is_told_to_push_it_and_open_a_pr(tmp_path):
    """Committed, not pushed, on a branch with its own upstream: the push is
    only half of the way — the bot renders nothing after it."""
    root = _on_session_branch(_shelf_on_main(tmp_path))

    resp = _shelve(root)

    assert resp["committed"] is True and resp["sync"]["pushed"] is False
    nxt = resp["next"]
    assert "on claude/probe, not pushed" in nxt
    assert "`git push -u origin HEAD`" in nxt
    assert "a PR into main" in nxt
    assert "renders derived files after the push" not in nxt


def test_a_session_branch_tracking_main_is_told_to_publish_it_not_to_push_main(tmp_path):
    """`checkout -B claude/probe origin/main`: `sync.branch` is `main`, but
    the checkout is the session branch — `next` names the branch the commit
    is on and the same `push -u` the hint carries (#185)."""
    root = _on_session_branch(_shelf_on_main(tmp_path), tracks="main")

    resp = _shelve(root)

    assert resp["sync"]["branch"] == "main"
    assert resp["sync"]["hint"].endswith("push -u origin HEAD")
    nxt = resp["next"]
    assert "on claude/probe, not pushed" in nxt
    assert "`git push -u origin HEAD`" in nxt
    assert "a PR into main" in nxt


def test_a_botless_session_branch_gets_the_pr_and_keeps_the_rebuild(tmp_path):
    """Without a bot nobody renders after the merge either: the PR step is
    added, the rebuild advice stays. «run `memshelf rebuild`» alone would not
    pin it — the bot wording «do not run `memshelf rebuild` by hand» contains
    it — so the test names what only the bot-less wording says."""
    root = _on_session_branch(_shelf_on_main(tmp_path, bot=False))

    nxt = _shelve(root, push=True)["next"]

    assert "pushed to origin/claude/probe" in nxt
    assert "a PR into main" in nxt
    assert "commit the derived files separately" in nxt
    assert "do not run" not in nxt
    assert "nothing else to do" not in nxt


def test_a_detached_head_is_told_to_branch_push_and_open_a_pr(tmp_path):
    """«push it» on a detached HEAD has no branch to push: `next` names the
    way out doctor's `upstream-unknown` names — a branch, `push -u`, a PR —
    and the commands it names put the episode on origin as they stand."""
    root = _shelf_on_main(tmp_path)
    _git(root, "checkout", "-q", "--detach")

    resp = _shelve(root)

    assert resp["committed"] is True
    nxt = resp["next"]
    assert "detached HEAD" in nxt
    assert "`git switch -c shelve/2026-07-22-auth-refactor`" in nxt
    assert "`git push -u origin HEAD`" in nxt
    assert "a PR into main" in nxt
    assert "push it;" not in nxt
    assert "renders derived files after the push" not in nxt

    _git(root, "switch", "-q", "-c", "shelve/2026-07-22-auth-refactor")
    _git(root, "push", "-q", "-u", "origin", "HEAD")
    on_origin = subprocess.run(
        [
            "git",
            "-C",
            str(root),
            "ls-tree",
            "-r",
            "--name-only",
            "origin/shelve/2026-07-22-auth-refactor",
        ],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.split()
    assert resp["address"] in on_origin


def test_on_the_render_branch_the_wording_is_unchanged(tmp_path):
    """#191 acceptance: `main` keeps both messages word for word."""
    root = _shelf_on_main(tmp_path)

    unpushed = _shelve(root)["next"]
    pushed = _shelve(root, slug="2026-07-23-second-topic", push=True, await_render_s=0)["next"]

    assert unpushed == (
        "episode committed locally, not pushed — push it (see sync.hint); "
        "the shelf bot renders derived files after the push"
    )
    assert pushed == "pushed — the shelf bot renders derived files on main; nothing else to do"


def test_the_tool_docstring_no_longer_promises_a_ledger_append(tmp_path):
    """#98 item 1: the first thing a calling agent reads must not lie."""
    pytest.importorskip("mcp")
    from memshelf_mcp import server

    doc = server.memshelf_shelve.__doc__ or ""
    assert "appends the ledger row" not in doc
    assert "rendered by" in doc
