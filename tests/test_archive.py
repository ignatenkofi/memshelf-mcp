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


def test_rollup_mode_stays_within_the_spec(tmp_path):
    """shelf-spec v0 allows exactly live|import — a third value would fail the
    shelves' own validator on the episode meant to tidy them up."""
    root = _shelf_with_three(tmp_path)
    _do_rollup(root, until="2026-06-30")

    text = (root / "docs" / "topics" / "2026-Q1-rollup.md").read_text(encoding="utf-8")
    assert "mode: live" in text
    assert "tags: [rollup]" in text
    ledger_row = [
        line
        for line in (root / "ledger.tsv").read_text(encoding="utf-8").splitlines()
        if "2026-Q1-rollup" in line
    ][0]
    assert ledger_row.split("\t")[2] == "live"


def test_purge_refuses_a_path_that_is_not_a_directory(tmp_path):
    """ "I did not look" must not read like "there was nothing to find".

    Before the guard, `purge` on a misspelled shelf path scanned nothing and
    reported `expired: [], count: 0, applied: False` — indistinguishable from
    a healthy shelf with no expired episodes. For a retention sweep that is
    the wrong way round.
    """
    with pytest.raises(FileNotFoundError, match="not a shelf directory"):
        purge(tmp_path / "typo-in-path")


def test_a_failed_archive_index_is_reported_not_swallowed(tmp_path, monkeypatch):
    """A rollup whose archive INDEX did not render must say so.

    `rebuild_archive_index` used to swallow the failure with a bare
    ``except Exception: pass`` and return ``None``. Every other field of the
    report still looked like success — episode written, count right — so the
    caller had no way to learn that the file it points readers at is stale.

    `rebuild()` already funnels the identical failure into `report.warnings`;
    the archive path was the odd one out.
    """
    root = _shelf_with_three(tmp_path / "shelf")

    import memshelf_mcp.core.archive as archive_mod

    class _Exploding:
        def __init__(self, *a, **kw):
            pass

        def rebuild_index(self):
            raise RuntimeError("docshelf blew up rendering the archive INDEX")

    monkeypatch.setattr("docshelf_mcp.core.shelf.Shelf", _Exploding)

    report = archive_mod.rollup(
        root, slug="2026-q1-rollup", digest=ROLLUP_DIGEST, until="2026-01-31"
    )

    assert report.warnings, "отказ рендера архивного INDEX не попал в отчёт"
    assert any("archive/INDEX.md" in w for w in report.warnings), report.warnings
    assert any("blew up" in w for w in report.warnings), (
        "текст исходного исключения потерян — по отчёту не понять, что случилось"
    )
    # Всё остальное обязано отработать: гард сообщает, а не отменяет роллап.
    assert report.archived, "роллап не выполнен — предупреждение не должно его отменять"


def test_resolve_does_not_claim_a_stale_archive_index_as_regenerated(tmp_path, monkeypatch):
    """`.is_file()` answers "a file is there", not "we wrote it".

    With the rebuild failing and a **stale** ``archive/INDEX.md`` left by an
    earlier run, the old code appended it to ``regenerated`` — the caller's one
    field for "what actually happened" said the opposite of the truth.
    """
    from memshelf_mcp.core.archive import archive_root, rebuild_archive_index

    root = _shelf_with_three(tmp_path / "shelf")
    rollup(root, slug="2026-q1-rollup", digest=ROLLUP_DIGEST, until="2026-01-31")

    stale = archive_root(root) / "INDEX.md"
    assert stale.is_file(), "фикстура не воспроизводит случай: архивного INDEX нет"
    stale.write_text("# устаревший INDEX\n", encoding="utf-8")

    class _Exploding:
        def __init__(self, *a, **kw):
            pass

        def rebuild_index(self):
            raise RuntimeError("nope")

    monkeypatch.setattr("docshelf_mcp.core.shelf.Shelf", _Exploding)

    warnings = rebuild_archive_index(root)

    assert warnings, "отказ не отражён в возвращённых предупреждениях"
    # Файл на месте и НЕ обновлён — ровно та ситуация, в которой `.is_file()`
    # раньше давала «regenerated».
    assert stale.read_text(encoding="utf-8") == "# устаревший INDEX\n"


def test_the_rollup_link_to_the_archive_actually_resolves(tmp_path):
    """The rollup's pointer to the archived originals must be a live path.

    The episode lands at `docs/topics/<slug>.md`; the archive sits at the shelf
    root. A bare `archive/INDEX.md` therefore resolved to
    `docs/topics/archive/INDEX.md` — dead in every rollup ever produced. This is
    the one link that carries the whole mechanic: a rollup is only acceptable
    because the originals stay reachable, and that claim is made *by this link*.

    Asserted by resolving the href from the episode's own directory, not by
    string-matching the expected prefix — a test that only checked for `../../`
    would pass on a link that is wrong in some new way.
    """
    import re

    root = _shelf_with_three(tmp_path / "shelf")
    report = rollup(root, slug="2026-q1-rollup", digest=ROLLUP_DIGEST, until="2026-01-31")

    episode = root / report.address
    body = episode.read_text(encoding="utf-8")
    hrefs = re.findall(r"\[`archive/INDEX\.md`\]\(([^)]+)\)", body)
    assert hrefs, f"ссылка на архивный INDEX пропала из тела роллапа:\n{body}"

    target = (episode.parent / hrefs[0]).resolve()
    assert target.is_file(), (
        f"ссылка {hrefs[0]!r} из {report.address} ведёт в {target}, которого нет"
    )
    # И ведёт именно в архивный INDEX, а не в какой-нибудь другой файл.
    assert target == (root / "archive" / "INDEX.md").resolve(), target


def test_rollup_reports_a_failed_PARENT_index_too(tmp_path, monkeypatch):
    """Не только архивный INDEX: отказ родительского обязан доехать в отчёт.

    `rebuild()` уже складывает этот отказ в свой `report.warnings`, но rollup
    и purge звали его как `rebuild(root)` — без присваивания, — и выбрасывали
    результат. То есть предупреждение о том, что INDEX.md полки (тот, что
    едет в КАЖДУЮ сессию) не пересобрался, терялось ровно в тех отчётах,
    которые для того и научили нести warnings.
    """
    root = _shelf_with_three(tmp_path / "shelf")

    class _Exploding:
        def __init__(self, *a, **kw):
            pass

        def rebuild_index(self):
            raise RuntimeError("родительский INDEX не собрался")

    monkeypatch.setattr("docshelf_mcp.core.shelf.Shelf", _Exploding)

    report = rollup(root, slug="2026-q1-rollup", digest=ROLLUP_DIGEST, until="2026-01-31")

    joined = " | ".join(report.warnings)
    assert "INDEX.md not rebuilt" in joined, f"отказ родительского INDEX потерян: {report.warnings}"
    assert "родительский INDEX не собрался" in joined, joined
    # И архивный тоже — два разных предупреждения, не одно.
    assert sum("INDEX" in w for w in report.warnings) >= 2, report.warnings
