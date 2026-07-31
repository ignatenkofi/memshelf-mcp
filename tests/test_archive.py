"""Rollups and retention (#15) — the two mechanics that keep INDEX readable.

The property under test throughout: a rollup shrinks *navigation* and nothing
else. Episodes stay recallable, searchable, and fully counted; only their
INDEX line goes away. A rollup that quietly lost an episode — or quietly
improved the shelf's own numbers — would be worse than the bloat it fixes.
"""

import subprocess

import pytest

pytest.importorskip("docshelf_mcp")

from docshelf_mcp.core.shelf import Shelf  # noqa: E402

from memshelf_mcp.core.archive import ArchiveError, purge, rollup  # noqa: E402
from memshelf_mcp.core.doctor import check_shelf  # noqa: E402
from memshelf_mcp.core.rebuild import rebuild  # noqa: E402
from memshelf_mcp.core.recall import recall, search  # noqa: E402
from memshelf_mcp.core.shelve import shelve  # noqa: E402

DIGEST = (
    "The auth refactor moved token checks into middleware; the decided approach "
    "is JWT with a shared secret. The cookie-session alternative was rejected "
    "for cross-service calls. Open: rotating the shared secret."
)
ROLLUP_DIGEST = (
    "Первый квартал свёрнут: разбор авторизации закрыт, выбранный подход остался "
    "в силе, отвергнутые варианты перечислены в исходных эпизодах. Открытым "
    "остаётся ротация общего секрета."
)


def _init(root):
    Shelf(root).init(name="t", default_categories=["topics", "research", "sessions"])
    subprocess.run(["git", "-C", str(root), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.email", "t@t.test"], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.name", "t"], check=True)
    return root


def _shelve(root, slug, *, title=None, tokens=1000):
    shelve(
        root,
        slug=slug,
        kind="topic",
        digest=DIGEST,
        sections={"Decisions": "Recorded."},
        display_title=title,
        approx_tokens=tokens,
        date=slug[:10],
    )


def _shelf_with_three(root):
    _init(root)
    _shelve(root, "2026-01-05-old-a", title="Старое А")
    _shelve(root, "2026-01-06-old-b", title="Старое Б")
    _shelve(root, "2026-07-20-new", title="Свежее")
    rebuild(root)
    return root


def _do_rollup(root, **kw):
    return rollup(
        root,
        slug="2026-Q1-rollup",
        digest=ROLLUP_DIGEST,
        display_title="Роллап Q1",
        **kw,
    )


def test_rollup_removes_index_lines_but_not_episodes(tmp_path):
    root = _shelf_with_three(tmp_path)
    report = _do_rollup(root, until="2026-06-30")

    assert report.index_tokens_after < report.index_tokens_before
    index = (root / "INDEX.md").read_text(encoding="utf-8")
    assert "old-a" not in index and "old-b" not in index
    assert "Роллап Q1" in index and "Свежее" in index

    # The files are in the archive sub-shelf, which keeps its own INDEX.
    assert (root / "archive" / "docs" / "topics" / "2026-01-05-old-a.md").is_file()
    archive_index = (root / "archive" / "INDEX.md").read_text(encoding="utf-8")
    assert "Старое А" in archive_index and "Старое Б" in archive_index


def test_rollup_names_every_episode_it_hid(tmp_path):
    """An INDEX line hiding N episodes has to say which N, or they are lost."""
    root = _shelf_with_three(tmp_path)
    _do_rollup(root, until="2026-06-30")

    text = (root / "docs" / "topics" / "2026-Q1-rollup.md").read_text(encoding="utf-8")
    assert "2026-01-05-old-a" in text
    assert "2026-01-06-old-b" in text
    assert "archive/INDEX.md" in text


def test_archived_episodes_stay_recallable_by_id(tmp_path):
    root = _shelf_with_three(tmp_path)
    _do_rollup(root, until="2026-06-30")

    result = recall(root, "2026-01-05-old-a")
    assert result.address == "archive/docs/topics/2026-01-05-old-a.md"
    assert "JWT" in result.content
    assert "recalled-episode" in result.content  # data envelope survives the move


def test_archived_episodes_stay_searchable(tmp_path):
    root = _shelf_with_three(tmp_path)
    _do_rollup(root, until="2026-06-30")

    addresses = [hit.address for hit in search(root, "JWT")]
    assert any("archive/docs/topics/2026-01-05-old-a.md" in a for a in addresses)


def test_rollup_keeps_the_accounting_whole(tmp_path):
    """Compressing navigation must not rewrite the shelf's own numbers."""
    root = _shelf_with_three(tmp_path)
    before = (root / "ledger.tsv").read_text(encoding="utf-8").splitlines()

    _do_rollup(root, until="2026-06-30")
    after = (root / "ledger.tsv").read_text(encoding="utf-8").splitlines()

    assert "2026-01-05-old-a" in "\n".join(after)
    assert "2026-01-06-old-b" in "\n".join(after)
    assert len(after) == len(before) + 1  # the three originals + the rollup itself


def test_doctor_stays_clean_after_a_rollup(tmp_path):
    """A doctor blind to archive/ would call every rolled-up row an orphan."""
    root = _shelf_with_three(tmp_path)
    _do_rollup(root, until="2026-06-30")

    report = check_shelf(root).as_dict()
    codes = {f["code"] for f in report["findings"]}
    assert report["errors"] == 0
    assert "orphan-ledger-row" not in codes
    assert "no-ledger-row" not in codes


def test_rollup_by_explicit_ids(tmp_path):
    root = _shelf_with_three(tmp_path)
    report = _do_rollup(root, episode_ids=["2026-01-05-old-a"])

    assert report.archived == ["archive/docs/topics/2026-01-05-old-a.md"]
    assert (root / "docs" / "topics" / "2026-01-06-old-b.md").is_file()


def test_unknown_episode_id_is_refused(tmp_path):
    root = _shelf_with_three(tmp_path)
    with pytest.raises(ArchiveError, match="no such episode"):
        _do_rollup(root, episode_ids=["2026-01-05-old-a", "does-not-exist"])


def test_rollup_without_a_selection_is_refused(tmp_path):
    root = _shelf_with_three(tmp_path)
    with pytest.raises(ArchiveError, match="--until"):
        _do_rollup(root)


def test_rollup_matching_nothing_is_refused(tmp_path):
    """Silently writing an empty rollup would leave a lying INDEX entry."""
    root = _shelf_with_three(tmp_path)
    with pytest.raises(ArchiveError, match="nothing selected"):
        _do_rollup(root, until="2020-01-01")


def test_rebuild_stays_idempotent_after_a_rollup(tmp_path):
    root = _shelf_with_three(tmp_path)
    _do_rollup(root, until="2026-06-30")
    assert rebuild(root, check=True).ok is True


# --- retention --------------------------------------------------------------


def _set_retention(path, until):
    text = path.read_text(encoding="utf-8")
    path.write_text(text.replace("mode: live", f"retain_until: {until}\nmode: live"), "utf-8")


def test_purge_is_a_dry_run_by_default(tmp_path):
    root = _shelf_with_three(tmp_path)
    _set_retention(root / "docs" / "topics" / "2026-01-05-old-a.md", "2026-02-01")

    report = purge(root, today="2026-07-31")

    assert report.expired == ["docs/topics/2026-01-05-old-a.md"]
    assert report.deleted == []
    assert (root / "docs" / "topics" / "2026-01-05-old-a.md").is_file()


def test_purge_apply_deletes_and_reindexes(tmp_path):
    root = _shelf_with_three(tmp_path)
    _set_retention(root / "docs" / "topics" / "2026-01-05-old-a.md", "2026-02-01")

    report = purge(root, today="2026-07-31", apply=True)

    assert report.deleted == ["docs/topics/2026-01-05-old-a.md"]
    assert not (root / "docs" / "topics" / "2026-01-05-old-a.md").exists()
    ledger = (root / "ledger.tsv").read_text(encoding="utf-8")
    assert "2026-01-05-old-a" not in ledger
    assert "old-a" not in (root / "INDEX.md").read_text(encoding="utf-8")


def test_purge_reaches_into_the_archive(tmp_path):
    """Retention that stops at the archive means "kept forever, out of sight"."""
    root = _shelf_with_three(tmp_path)
    _do_rollup(root, until="2026-06-30")
    _set_retention(root / "archive" / "docs" / "topics" / "2026-01-06-old-b.md", "2026-02-01")

    report = purge(root, today="2026-07-31", apply=True)

    assert report.deleted == ["archive/docs/topics/2026-01-06-old-b.md"]
    assert not (root / "archive" / "docs" / "topics" / "2026-01-06-old-b.md").exists()
    assert "2026-01-06-old-b" not in (root / "ledger.tsv").read_text(encoding="utf-8")


def test_episodes_without_retention_are_never_touched(tmp_path):
    """Retention is opt-in: no `retain_until` means keep, not "keep by default"."""
    root = _shelf_with_three(tmp_path)
    _set_retention(root / "docs" / "topics" / "2026-01-05-old-a.md", "2027-01-01")

    report = purge(root, today="2026-07-31", apply=True)

    assert report.expired == []
    assert len(list((root / "docs" / "topics").glob("*.md"))) == 3


def test_purge_report_states_the_git_history_caveat(tmp_path):
    root = _shelf_with_three(tmp_path)
    note = purge(root, today="2026-07-31").as_dict()["note"]
    assert "git history" in note
    assert "filter-repo" in note
