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
