"""#108 — the shelve syncs its clone, pushes with one rebase-retry, and says so.

Every test here uses real git against a local bare origin: the failure class
under test («the bot moved main while this clone slept») is a git behavior,
and mocking git would test the mock.
"""

import subprocess

import pytest

pytest.importorskip("docshelf_mcp")

from docshelf_mcp.core.shelf import Shelf  # noqa: E402

from memshelf_mcp.core.gitsync import (  # noqa: E402
    DirtyShelfError,
    PushRejectedError,
    SyncDivergedError,
    SyncReport,
    push_with_retry,
)
from memshelf_mcp.core.shelve import shelve  # noqa: E402

GOOD_DIGEST = (
    "The auth refactor moved token checks into middleware; the decided approach "
    "is JWT with a shared secret. The cookie-session alternative was rejected "
    "for cross-service calls. Open: rotating the shared secret."
)
SECTIONS = {
    "Decisions": "JWT with a shared secret; cookie-session rejected for cross-service calls."
}


def _git(root, *args):
    return subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, text=True, check=False
    )


def _must(root, *args):
    proc = _git(root, *args)
    assert proc.returncode == 0, proc.stderr
    return proc


def _shelf_with_origin(tmp_path):
    """A bare origin plus a working shelf clone tracking origin/main."""
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True)
    work = tmp_path / "work"
    work.mkdir()
    Shelf(work).init(name="test shelf", default_categories=["topics", "research", "sessions"])
    subprocess.run(["git", "init", "-q", "-b", "main", str(work)], check=True)
    _must(work, "config", "user.email", "t@t.test")
    _must(work, "config", "user.name", "tester")
    _must(work, "add", "-A")
    _must(work, "commit", "-q", "-m", "init shelf")
    _must(work, "remote", "add", "origin", str(origin))
    _must(work, "push", "-q", "-u", "origin", "main")
    return origin, work


def _second_clone(tmp_path, origin, name="bot"):
    clone = tmp_path / name
    subprocess.run(["git", "clone", "-q", str(origin), str(clone)], check=True)
    _must(clone, "config", "user.email", "bot@t.test")
    _must(clone, "config", "user.name", name)
    return clone


def _advance_origin(tmp_path, origin, filename="ledger.tsv", name="bot"):
    """A foreign commit lands on origin/main — the bot rendering derived files."""
    clone = _second_clone(tmp_path, origin, name=name)
    (clone / filename).write_text("rendered by the bot\n", encoding="utf-8")
    _must(clone, "add", "-A")
    _must(clone, "commit", "-q", "-m", "chore: regenerate derived files")
    _must(clone, "push", "-q", "origin", "main")
    return _must(clone, "rev-parse", "HEAD").stdout.strip()


def _origin_head(origin):
    return _must(origin, "rev-parse", "main").stdout.strip()


# --- the clean run says so -------------------------------------------------


def test_clean_run_reports_pulled_0_retries_0_and_the_post_push_sha(tmp_path):
    _origin, work = _shelf_with_origin(tmp_path)
    result = shelve(
        work,
        slug="2026-08-20-clean-run",
        kind="topic",
        digest=GOOD_DIGEST,
        sections=SECTIONS,
        push=True,
    )
    sync = result.sync
    assert sync is not None and sync.performed
    assert sync.commits_pulled == 0
    assert sync.push_retries == 0
    assert sync.pushed
    assert sync.final_sha == _origin_head(_origin)
    assert "pulled 0" in sync.line() and "retries 0" in sync.line()


# --- preflight: catch up, or refuse before writing -------------------------


def test_preflight_pulls_the_bot_commit_before_writing(tmp_path):
    origin, work = _shelf_with_origin(tmp_path)
    _advance_origin(tmp_path, origin)
    result = shelve(
        work,
        slug="2026-08-20-after-bot",
        kind="topic",
        digest=GOOD_DIGEST,
        sections=SECTIONS,
        push=True,
    )
    assert result.sync.commits_pulled == 1
    assert result.sync.push_retries == 0  # preflight already caught up
    assert (work / "ledger.tsv").read_text(encoding="utf-8") == "rendered by the bot\n"
    assert result.sync.final_sha == _origin_head(origin)


def test_dirty_tracked_tree_refuses_before_anything_is_written(tmp_path):
    _origin, work = _shelf_with_origin(tmp_path)
    (work / "INDEX.md").write_text("hand edit\n", encoding="utf-8")  # tracked file
    with pytest.raises(DirtyShelfError):
        shelve(work, slug="2026-08-20-dirty", kind="topic", digest=GOOD_DIGEST, sections=SECTIONS)
    assert not (work / "docs" / "topics" / "2026-08-20-dirty.md").exists()


def test_untracked_scratch_does_not_count_as_dirty(tmp_path):
    _origin, work = _shelf_with_origin(tmp_path)
    (work / "scratch.txt").write_text("wip\n", encoding="utf-8")  # untracked
    result = shelve(
        work, slug="2026-08-20-scratch", kind="topic", digest=GOOD_DIGEST, sections=SECTIONS
    )
    assert result.sync.performed and result.committed


def test_diverged_clone_refuses_with_the_executable_fix(tmp_path):
    origin, work = _shelf_with_origin(tmp_path)
    (work / "local.txt").write_text("local\n", encoding="utf-8")
    _must(work, "add", "-A")
    _must(work, "commit", "-q", "-m", "local-only commit")
    _advance_origin(tmp_path, origin)  # both sides moved: no fast-forward
    with pytest.raises(SyncDivergedError) as exc:
        shelve(
            work, slug="2026-08-20-diverged", kind="topic", digest=GOOD_DIGEST, sections=SECTIONS
        )
    assert "pull --rebase" in str(exc.value)
    assert not (work / "docs" / "topics" / "2026-08-20-diverged.md").exists()


# --- the push fork: one rebase-retry, then git's own words -----------------


def test_race_push_retries_exactly_once_and_names_the_final_sha(tmp_path):
    origin, work = _shelf_with_origin(tmp_path)
    # The race window of #108's acceptance: a local commit exists, and origin
    # advances *after* it but before the push.
    (work / "docs" / "topics" / "manual.md").write_text("episode\n", encoding="utf-8")
    _must(work, "add", "-A")
    _must(work, "commit", "-q", "-m", "shelve: manual")
    _advance_origin(tmp_path, origin)

    report = SyncReport()
    push_with_retry(work, report)

    assert report.pushed
    assert report.push_retries == 1
    assert report.commits_pulled == 1  # the rebase pulled the bot's commit
    assert report.final_sha == _origin_head(origin)
    log = _must(origin, "log", "--format=%s", "main").stdout
    assert "shelve: manual" in log and "regenerate derived files" in log
    assert "push retries 1" in report.line() and "pushed" in report.line()


def test_second_rejection_surfaces_gits_words(tmp_path):
    origin, work = _shelf_with_origin(tmp_path)
    (work / "note.txt").write_text("x\n", encoding="utf-8")
    _must(work, "add", "-A")
    _must(work, "commit", "-q", "-m", "local")
    # Make every push impossible: the remote path stops existing.
    _must(work, "remote", "set-url", "origin", str(tmp_path / "gone.git"))
    with pytest.raises(PushRejectedError):
        push_with_retry(work, SyncReport())


# --- degradation is loud, never silent -------------------------------------


def test_no_remote_skips_sync_and_still_shelves(tmp_path):
    work = tmp_path / "local-shelf"
    work.mkdir()
    Shelf(work).init(name="t", default_categories=["topics", "research", "sessions"])
    subprocess.run(["git", "init", "-q", "-b", "main", str(work)], check=True)
    _must(work, "config", "user.email", "t@t.test")
    _must(work, "config", "user.name", "tester")
    result = shelve(
        work, slug="2026-08-20-local", kind="topic", digest=GOOD_DIGEST, sections=SECTIONS
    )
    assert result.committed
    assert result.sync.performed is False
    assert "no remote" in result.sync.skipped_reason
    assert any("sync: skipped" in w for w in result.warnings)


def test_fetch_failure_is_loud_but_does_not_cost_the_episode(tmp_path):
    _origin, work = _shelf_with_origin(tmp_path)
    _must(work, "remote", "set-url", "origin", str(tmp_path / "gone.git"))
    result = shelve(
        work, slug="2026-08-20-offline", kind="topic", digest=GOOD_DIGEST, sections=SECTIONS
    )
    assert result.committed  # the episode is written and committed locally
    assert result.sync.performed is False
    assert result.sync.skipped_reason.startswith("fetch failed")
    assert any("fetch failed" in w for w in result.warnings)


def test_committed_without_push_hands_back_the_executable_catchup(tmp_path):
    _origin, work = _shelf_with_origin(tmp_path)
    result = shelve(
        work, slug="2026-08-20-hint", kind="topic", digest=GOOD_DIGEST, sections=SECTIONS
    )
    assert result.committed and not result.sync.pushed
    assert "pull --rebase origin main" in result.sync.hint
    assert "push origin main" in result.sync.hint


def test_push_without_autocommit_is_a_loud_misuse(tmp_path):
    _origin, work = _shelf_with_origin(tmp_path)
    with pytest.raises(ValueError):
        shelve(
            work,
            slug="2026-08-20-misuse",
            kind="topic",
            digest=GOOD_DIGEST,
            sections=SECTIONS,
            autocommit=False,
            push=True,
        )


# --- #118: the branch destination -------------------------------------------


def _protect_main(origin):
    """A pre-receive hook standing in for the ruleset: main refuses pushes."""
    hook = origin / "hooks" / "pre-receive"
    hook.write_text(
        "#!/bin/sh\n"
        "while read old new ref; do\n"
        '  if [ "$ref" = "refs/heads/main" ]; then\n'
        '    echo "main requires a pull request" >&2\n'
        "    exit 1\n"
        "  fi\n"
        "done\n"
        "exit 0\n",
        encoding="utf-8",
    )
    hook.chmod(0o755)


def _shelve_published(work, slug="2026-08-31-ruleset-probe"):
    return shelve(
        work,
        slug=slug,
        kind="topic",
        digest=GOOD_DIGEST,
        sections=SECTIONS,
        date=slug[:10],
        publish=True,
    )


def test_publish_lands_the_episode_when_main_refuses_pushes(tmp_path):
    """#118 acceptance: the episode leaves the container even under the ruleset."""
    origin, work = _shelf_with_origin(tmp_path)
    _protect_main(origin)

    result = _shelve_published(work)

    branch = result.sync.published_branch
    assert branch == "shelve/2026-08-31-ruleset-probe"
    # The episode is on origin, on that branch — not on main.
    on_branch = _must(origin, "ls-tree", "-r", "--name-only", branch).stdout
    assert "docs/topics/2026-08-31-ruleset-probe.md" in on_branch
    on_main = _must(origin, "ls-tree", "-r", "--name-only", "main").stdout
    assert "2026-08-31-ruleset-probe" not in on_main
    # The sha in the report is the pushed one — identical to local HEAD, since
    # nothing rebased (the #146 rule: never quote a sha the push can rewrite).
    assert result.sync.final_sha == _must(work, "rev-parse", "HEAD").stdout.strip()
    # The checkout never switched: recall still answers from this clone.
    assert _must(work, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip() == "main"
    assert (work / "docs" / "topics" / "2026-08-31-ruleset-probe.md").is_file()
    # No catch-up hint: the commit is exactly where the mode wants it.
    assert result.sync.hint is None
    # A filesystem origin has no web UI — no link is fabricated.
    assert result.sync.compare_url is None
    assert "published shelve/2026-08-31-ruleset-probe" in result.sync.line()


def test_push_dies_where_publish_survives(tmp_path):
    """The control: under the same ruleset, push=True is a policy death —
    the #108 retry cannot help, which is why the branch mode exists."""
    origin, work = _shelf_with_origin(tmp_path)
    _protect_main(origin)
    with pytest.raises(PushRejectedError):
        shelve(
            work,
            slug="2026-08-31-doomed-push",
            kind="topic",
            digest=GOOD_DIGEST,
            sections=SECTIONS,
            date="2026-08-31",
            push=True,
        )


def test_publish_behaves_the_same_without_a_ruleset(tmp_path):
    """#118 acceptance: behavior must not depend on a setting the tool can't see."""
    origin, work = _shelf_with_origin(tmp_path)
    result = _shelve_published(work)
    assert result.sync.published_branch == "shelve/2026-08-31-ruleset-probe"
    on_branch = _must(origin, "ls-tree", "-r", "--name-only", result.sync.published_branch).stdout
    assert "docs/topics/2026-08-31-ruleset-probe.md" in on_branch


def test_a_taken_branch_name_retries_qualified_by_the_commit(tmp_path):
    """Same slug from a second session collides by design; the retry names why."""
    origin, work = _shelf_with_origin(tmp_path)
    other = _second_clone(tmp_path, origin, name="other-session")
    (other / "unrelated.txt").write_text("someone else's shelve\n", encoding="utf-8")
    _must(other, "add", "-A")
    _must(other, "commit", "-q", "-m", "other")
    _must(other, "push", "-q", "origin", "HEAD:refs/heads/shelve/2026-08-31-ruleset-probe")

    result = _shelve_published(work)
    head7 = _must(work, "rev-parse", "HEAD").stdout.strip()[:7]
    assert result.sync.published_branch == f"shelve/2026-08-31-ruleset-probe-{head7}"


def test_publish_and_push_are_two_destinations(tmp_path):
    origin, work = _shelf_with_origin(tmp_path)
    with pytest.raises(ValueError, match="two destinations"):
        shelve(
            work,
            slug="2026-08-31-both",
            kind="topic",
            digest=GOOD_DIGEST,
            sections=SECTIONS,
            date="2026-08-31",
            push=True,
            publish=True,
        )


def test_publish_needs_a_commit_to_publish(tmp_path):
    origin, work = _shelf_with_origin(tmp_path)
    with pytest.raises(ValueError, match="autocommit"):
        shelve(
            work,
            slug="2026-08-31-nocommit",
            kind="topic",
            digest=GOOD_DIGEST,
            sections=SECTIONS,
            date="2026-08-31",
            autocommit=False,
            publish=True,
        )


def test_web_url_normalizes_the_remotes_that_have_a_web_ui():
    from memshelf_mcp.core.gitsync import _web_url

    assert (
        _web_url("git@github.com:ignatenkofi/main-memshelf.git")
        == "https://github.com/ignatenkofi/main-memshelf"
    )
    assert (
        _web_url("ssh://git@github.com/ignatenkofi/main-memshelf")
        == "https://github.com/ignatenkofi/main-memshelf"
    )
    assert (
        _web_url("https://github.com/ignatenkofi/main-memshelf.git")
        == "https://github.com/ignatenkofi/main-memshelf"
    )
    assert _web_url("/home/user/origin.git") is None


def test_a_dirty_recall_log_does_not_block_a_shelve(tmp_path):
    """#112: recall logs by default, and reading memory must never refuse
    writing it — the log is append-only telemetry no renderer touches."""
    origin, work = _shelf_with_origin(tmp_path)
    (work / "recall-log.tsv").write_text("ts\tepisode\tfetched\tsaved\n", encoding="utf-8")
    _must(work, "add", "-A")
    _must(work, "commit", "-q", "-m", "track the recall log")
    _must(work, "push", "-q", "origin", "main")
    with (work / "recall-log.tsv").open("a", encoding="utf-8") as fh:
        fh.write("2026-08-31T17:00Z\tx\t100\t900\n")

    result = shelve(
        work,
        slug="2026-08-31-after-a-logged-recall",
        kind="topic",
        digest=GOOD_DIGEST,
        sections=SECTIONS,
        date="2026-08-31",
    )
    assert result.committed
    # The log's local appends survived, uncommitted — the shelve staged only
    # the episode, exactly as before.
    assert "2026-08-31T17:00Z" in (work / "recall-log.tsv").read_text(encoding="utf-8")


def test_any_other_dirty_tracked_file_still_refuses(tmp_path):
    """The exemption is one file, not a loophole."""
    origin, work = _shelf_with_origin(tmp_path)
    index = work / "INDEX.md"
    assert index.is_file()  # tracked since the init commit — a real guard case
    index.write_text(index.read_text(encoding="utf-8") + "edited by hand\n", encoding="utf-8")
    with pytest.raises(DirtyShelfError):
        shelve(
            work,
            slug="2026-08-31-blocked",
            kind="topic",
            digest=GOOD_DIGEST,
            sections=SECTIONS,
            date="2026-08-31",
        )
