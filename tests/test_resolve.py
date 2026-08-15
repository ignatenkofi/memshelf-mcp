"""``resolve`` — the bridge over the multi-writer conflict class (#58).

Two sessions shelving on parallel branches used to collide on four derived
files at once. Since #58 removed derived files from ``shelve``'s write set,
that merge is clean by construction — the first test here asserts exactly
that, because the absence of the conflict is the change's whole point.

``resolve`` stays for the shelves that have not adopted the bot yet, for
hand-edited derived files, and for the residual real collision: the same
episode written on both sides. Those are what the rest of this file covers,
so the conflicts below are constructed rather than produced by ``shelve``."""

import subprocess

import pytest

pytest.importorskip("docshelf_mcp")

from docshelf_mcp.core.shelf import Shelf  # noqa: E402

from memshelf_mcp.core.archive import rollup  # noqa: E402
from memshelf_mcp.core.doctor import check_shelf  # noqa: E402
from memshelf_mcp.core.rebuild import rebuild  # noqa: E402
from memshelf_mcp.core.resolve import (  # noqa: E402
    _split_marker_sides,
    _union_tsv,
    resolve_shelf,
)
from memshelf_mcp.core.shelve import shelve  # noqa: E402

DIGEST_A = (
    "The auth refactor moved token checks into middleware; the decided approach "
    "is JWT with a shared secret. The cookie-session alternative was rejected "
    "for cross-service calls. Open: rotating the shared secret."
)
DIGEST_B = (
    "The storage layer moved to sqlite with a migrations table; the decided "
    "approach is WAL mode. The flat-file alternative was rejected for "
    "concurrent readers. Open: backup rotation."
)


def _git(root, *args, check=True):
    return subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, text=True, check=check
    )


def _init_shelf(root):
    Shelf(root).init(name="test shelf", default_categories=["topics", "research", "sessions"])
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "t@t.test")
    _git(root, "config", "user.name", "tester")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "init shelf")
    return root


def _shelve(root, slug, digest, title):
    shelve(
        root,
        slug=slug,
        kind="topic",
        digest=digest,
        sections={"Decisions": "Recorded."},
        display_title=title,
        approx_tokens=1000,
        date=slug[:10],
    )


def _two_parallel_shelves(root):
    """Two branches, one shelve each, then merge both into main."""
    _git(root, "checkout", "-q", "-b", "session-a")
    _shelve(root, "2026-07-29-auth-refactor", DIGEST_A, "Рефактор авторизации")
    _git(root, "checkout", "-q", "main")
    _git(root, "checkout", "-q", "-b", "session-b")
    _shelve(root, "2026-07-29-storage-wal", DIGEST_B, "Хранилище на WAL")
    _git(root, "checkout", "-q", "main")
    _git(root, "merge", "-q", "session-a")
    return _git(root, "merge", "session-b", check=False)


def test_parallel_shelves_no_longer_conflict(tmp_path):
    """#58 in one assertion: the scenario that needed `resolve` now just merges.

    Before the split, each shelve appended to ledger.tsv, rewrote INDEX.md and
    .meta.json and redrew stats.svg — so two independent topics collided on
    four files that git cannot merge. Now each side carries one new episode
    file and nothing else.
    """
    root = _init_shelf(tmp_path)
    merge = _two_parallel_shelves(root)

    assert merge.returncode == 0, f"unexpected conflict:\n{merge.stdout}{merge.stderr}"
    assert _git(root, "ls-files", "-u").stdout == ""

    # Both episodes are on main, and one rebuild renders both into the ledger
    # and the INDEX — with the display titles that rode in their frontmatter.
    rebuild(root)
    ledger = (root / "ledger.tsv").read_text(encoding="utf-8")
    assert "2026-07-29-auth-refactor" in ledger and "2026-07-29-storage-wal" in ledger
    index = (root / "INDEX.md").read_text(encoding="utf-8")
    assert "Рефактор авторизации" in index and "Хранилище на WAL" in index


def _make_derived_conflict(root):
    """A ledger/meta conflict as a shelf without the bot still produces one.

    Constructed by hand now that ``shelve`` no longer writes derived files:
    each branch regenerates them after its own shelve, which is what a shelf
    that has not adopted the bot workflow does.
    """
    _git(root, "checkout", "-q", "-b", "session-a")
    _shelve(root, "2026-07-29-auth-refactor", DIGEST_A, "Рефактор авторизации")
    rebuild(root)
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "a: derived")
    _git(root, "checkout", "-q", "main")
    _git(root, "checkout", "-q", "-b", "session-b")
    _shelve(root, "2026-07-29-storage-wal", DIGEST_B, "Хранилище на WAL")
    rebuild(root)
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "b: derived")
    _git(root, "checkout", "-q", "main")
    _git(root, "merge", "-q", "session-a")
    merge = _git(root, "merge", "session-b", check=False)
    assert merge.returncode != 0, "expected the derived-file conflict"
    return merge


def test_live_conflict_resolved_end_to_end(tmp_path):
    root = _init_shelf(tmp_path)
    _make_derived_conflict(root)

    unmerged = _git(root, "ls-files", "-u").stdout
    assert "ledger.tsv" in unmerged and "INDEX.md" in unmerged

    result = resolve_shelf(root)

    assert result.unresolved == []
    # Derived paths are regenerated, never merged (#64) — including the ledger
    # and the category .meta.json, which used to be union'd.
    assert result.resolved == []
    for rel in ("ledger.tsv", "docs/topics/.meta.json", "INDEX.md"):
        assert rel in result.regenerated

    # both branches' episodes are in the regenerated ledger, exactly once each
    ledger = (root / "ledger.tsv").read_text(encoding="utf-8")
    ids = [line.split("\t")[1] for line in ledger.splitlines()[1:] if line.strip()]
    assert sorted(ids) == ["2026-07-29-auth-refactor", "2026-07-29-storage-wal"]
    assert ledger.startswith("date\t")

    # both display titles survive the meta merge and reach the rebuilt INDEX
    index = (root / "INDEX.md").read_text(encoding="utf-8")
    assert "Рефактор авторизации" in index and "Хранилище на WAL" in index
    assert "<<<<<<<" not in index

    # everything is staged — no unmerged paths remain
    assert _git(root, "ls-files", "-u").stdout == ""
    assert result.in_merge and not result.committed
    assert result.doctor["errors"] == 0
    assert result.ok


def test_commit_completes_the_merge(tmp_path):
    root = _init_shelf(tmp_path)
    _make_derived_conflict(root)

    result = resolve_shelf(root, commit=True)

    assert result.committed and result.commit
    assert not (root / ".git" / "MERGE_HEAD").exists()
    # merge commit has both parents
    parents = _git(root, "log", "-1", "--format=%P").stdout.split()
    assert len(parents) == 2


def test_conflicting_episode_left_alone(tmp_path):
    root = _init_shelf(tmp_path)
    episode = tmp_path / "docs" / "topics" / "note.md"

    _shelve(root, "2026-07-28-note", DIGEST_A, "Заметка")
    _git(root, "checkout", "-q", "-b", "edit-a")
    target = root / "docs" / "topics" / "2026-07-28-note.md"
    target.write_text(target.read_text(encoding="utf-8") + "\nA-side edit.\n", encoding="utf-8")
    _git(root, "commit", "-aqm", "a edit")
    _git(root, "checkout", "-q", "main")
    target.write_text(target.read_text(encoding="utf-8") + "\nB-side edit.\n", encoding="utf-8")
    _git(root, "commit", "-aqm", "b edit")
    merge = _git(root, "merge", "edit-a", check=False)
    assert merge.returncode != 0

    result = resolve_shelf(root)

    assert result.unresolved == ["docs/topics/2026-07-28-note.md"]
    # derived files must NOT be rebuilt over a conflicted episode
    assert result.regenerated == []
    assert any("not regenerated" in n for n in result.notes)
    assert not result.ok
    del episode


def test_no_conflict_degrades_to_rebuild(tmp_path):
    root = _init_shelf(tmp_path)
    _shelve(root, "2026-07-29-auth-refactor", DIGEST_A, "Рефактор авторизации")

    result = resolve_shelf(root)

    assert result.resolved == [] and result.unresolved == []
    assert "INDEX.md" in result.regenerated
    assert result.doctor["errors"] == 0
    assert result.ok


def test_marker_fallback_without_stages(tmp_path):
    """Markers on disk with no git stages — the half-finished manual resolution.

    ``recall-log.tsv`` is the file this path still merges: nothing regenerates
    a recall log, so both sides' rows have to survive.
    """
    root = _init_shelf(tmp_path)
    _shelve(root, "2026-07-29-auth-refactor", DIGEST_A, "Рефактор авторизации")
    log = root / "recall-log.tsv"
    ours_row = "2026-07-29-auth-refactor\tDigest\t50"
    theirs_row = "2026-07-29-auth-refactor\tDecisions\t60"
    log.write_text(
        "episode_id\tsection\tfetched_tokens\n"
        f"<<<<<<< HEAD\n{ours_row}\n=======\n{theirs_row}\n>>>>>>> session-b\n",
        encoding="utf-8",
    )

    result = resolve_shelf(root)

    text = log.read_text(encoding="utf-8")
    assert ours_row in text and theirs_row in text
    assert "<<<<<<<" not in text
    assert "recall-log.tsv" in result.resolved


def test_marker_in_derived_file_is_regenerated_not_merged(tmp_path):
    """A derived file carrying markers is overwritten, not reconciled (#64).

    The rows in the markers name episodes that do not exist; a union kept them
    (and doctor then called them orphan rows), a regeneration drops them.
    """
    root = _init_shelf(tmp_path)
    _shelve(root, "2026-07-29-auth-refactor", DIGEST_A, "Рефактор авторизации")
    ledger = root / "ledger.tsv"
    ledger.write_text(
        "date\tepisode_id\tmode\tapprox_tokens_in\tdigest_tokens\tnotes\n"
        "<<<<<<< HEAD\n2026-07-29\tep-ours\tlive\t1000\t50\t\n"
        "=======\n2026-07-29\tep-theirs\tlive\t2000\t60\t\n>>>>>>> session-b\n",
        encoding="utf-8",
    )

    result = resolve_shelf(root)

    text = ledger.read_text(encoding="utf-8")
    assert "<<<<<<<" not in text
    assert "ep-ours" not in text and "ep-theirs" not in text
    assert "2026-07-29-auth-refactor" in text
    assert "ledger.tsv" in result.regenerated
    assert result.doctor["errors"] == 0


def test_conflict_on_a_shelf_with_a_non_empty_archive(tmp_path):
    """The live 2026-08-01 collision (#64): rollup on one side, shelve on main.

    A union brought back the ``.meta`` entries of episodes that had moved into
    ``archive/`` — 16 ``stale-meta-entry`` findings on a resolve that reported
    ``status: ok``. Regeneration must leave the archived episodes out of the
    parent's meta, keep their ledger rows, and refresh ``archive/INDEX.md``.
    """
    root = _init_shelf(tmp_path)
    _shelve(root, "2026-07-20-old-one", DIGEST_A, "Старый эпизод")
    rebuild(root)  # a bot-less shelf commits its own derived files
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "seed")

    # branch: roll the old episode into archive/ and regenerate
    _git(root, "checkout", "-q", "-b", "session-a")
    rollup(
        root,
        until="2026-07-25",
        slug="2026-07-25-rollup",
        digest=DIGEST_B,
        date="2026-07-25",
    )
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "a: rollup")

    # main: an unrelated shelve, derived files regenerated as a bot-less shelf does
    _git(root, "checkout", "-q", "main")
    _shelve(root, "2026-07-29-storage-wal", DIGEST_B, "Хранилище на WAL")
    rebuild(root)
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "main: shelve")

    merge = _git(root, "merge", "session-a", check=False)
    assert merge.returncode != 0, "expected the derived-file conflict"

    result = resolve_shelf(root)

    assert result.unresolved == []
    assert result.doctor["errors"] == 0
    # The archived episode is out of the parent's meta …
    meta = (root / "docs" / "topics" / ".meta.json").read_text(encoding="utf-8")
    assert "2026-07-20-old-one" not in meta
    assert [f for f in result.doctor["findings"] if f["code"] == "stale-meta-entry"] == []
    # … but still in the ledger, exactly once, with the rollup and the shelve.
    ledger = (root / "ledger.tsv").read_text(encoding="utf-8")
    ids = [line.split("\t")[1] for line in ledger.splitlines()[1:] if line.strip()]
    assert sorted(ids) == [
        "2026-07-20-old-one",
        "2026-07-25-rollup",
        "2026-07-29-storage-wal",
    ]
    assert len(ids) == len(set(ids)), "no duplicate ledger rows"
    # … and the archive's own INDEX is refreshed, which `rebuild` does not do.
    assert "archive/INDEX.md" in result.regenerated
    assert "2026-07-20-old-one" in (root / "archive" / "INDEX.md").read_text(encoding="utf-8")


def test_union_tsv_keeps_duplicate_rows_and_header():
    """Three-way multiset union: identical rows are events, not noise (#62).

    Two sessions recalling the same section write byte-identical rows; a set
    union would collapse them and undercount the log's whole purpose.
    """
    header = "episode_id\tsection\ttokens\n"
    base = header + "ep-a\tDigest\t50\n"
    ours = base + "ep-b\tDigest\t60\n"
    theirs = base + "ep-b\tDigest\t60\n"
    merged = _union_tsv(ours, theirs, header, base)
    lines = merged.splitlines()
    assert lines[0] == "episode_id\tsection\ttokens"
    assert lines[1:] == ["ep-a\tDigest\t50", "ep-b\tDigest\t60", "ep-b\tDigest\t60"]


LEDGER_HEADER = "date\tepisode_id\tmode\tapprox_tokens_in\tdigest_tokens\tnotes\n"
LEDGER_ROW = "2026-08-05\t2026-08-05-atlas-native-first-session\tlive\t700\t184"


def test_union_collapses_rows_that_differ_only_by_a_trailing_empty_column():
    """#78: the same row spelled with and without the trailing tab is one row.

    The live pair from the shelf: an empty ``notes`` written by two writers,
    one of which ended the line after the fifth cell. Whole-row comparison
    counted two rows for one episode, and the duplicate came out of the ledger
    by hand.
    """
    ours = LEDGER_HEADER + LEDGER_ROW + "\t\n"
    theirs = LEDGER_HEADER + LEDGER_ROW + "\n"

    rows = _union_tsv(ours, theirs, LEDGER_HEADER).splitlines()[1:]

    assert len(rows) == 1, rows
    # The six-column spelling wins: shelf-spec v0 § 4.4 has six columns and
    # doctor calls a five-cell row malformed, so collapsing to the narrow one
    # would trade one finding for another.
    assert rows[0] == LEDGER_ROW + "\t", rows


def test_a_middle_empty_cell_is_still_a_real_difference():
    """Guard on the guard: only *trailing* empties are spelling, not content.

    An empty cell in the middle shifts every column after it — collapsing that
    pair would merge two genuinely different rows.
    """
    header = "a\tb\tc\n"
    ours = header + "1\t\t3\n"
    theirs = header + "1\t2\t3\n"

    rows = _union_tsv(ours, theirs, header).splitlines()[1:]

    assert sorted(rows) == ["1\t\t3", "1\t2\t3"], rows


def test_repeated_recalls_still_count_twice_after_normalisation():
    """The #62 invariant must survive the #78 fix: identical rows are events."""
    header = "episode_id\tsection\ttokens\n"
    ours = header + "ep-a\tDigest\t50\nep-a\tDigest\t50\n"
    theirs = header + "ep-a\tDigest\t50\n"

    rows = _union_tsv(ours, theirs, header).splitlines()[1:]

    assert rows.count("ep-a\tDigest\t50") == 2, rows


def test_doctor_catches_the_pair_the_union_used_to_leave(tmp_path):
    """The issue asks whether doctor would have caught it. It would — but not
    by the rule one would expect, and the difference is worth pinning.

    The pair is *five* cells against six, so the column-count check fires first
    and that row is skipped — the ``episode_id`` uniqueness check added in #63
    never sees it. Both findings are errors and both name ``ledger.tsv``, so
    the shelf is blocked either way; what an operator reads, though, is "wrong
    number of columns", not "duplicate episode".
    """
    root = _init_shelf(tmp_path)
    (root / "ledger.tsv").write_text(
        LEDGER_HEADER + LEDGER_ROW + "\t\n" + LEDGER_ROW + "\n", encoding="utf-8"
    )

    report = check_shelf(root)

    assert not report.ok
    malformed = [f for f in report.findings if f.code == "ledger-malformed"]
    assert malformed, [f.code for f in report.findings]
    assert "expected 6 tab-separated columns" in malformed[0].detail, malformed[0].detail


def test_doctor_catches_the_duplicate_once_both_rows_are_well_formed(tmp_path):
    """And the other half: repair the column count by hand and the duplicate
    check takes over, rather than the pair becoming invisible."""
    root = _init_shelf(tmp_path)
    (root / "ledger.tsv").write_text(
        LEDGER_HEADER + LEDGER_ROW + "\t\n" + LEDGER_ROW + "\t\n", encoding="utf-8"
    )

    report = check_shelf(root)

    assert not report.ok
    detail = next(f for f in report.findings if f.code == "ledger-malformed").detail
    assert "already recorded" in detail, detail


def test_union_tsv_without_base_never_loses_a_side():
    header = "episode_id\tsection\ttokens\n"
    ours = header + "ep-a\tDigest\t50\nep-a\tDigest\t50\n"
    theirs = header + "ep-a\tDigest\t50\nep-b\tDigest\t60\n"
    merged = _union_tsv(ours, theirs, header)
    lines = merged.splitlines()[1:]
    assert lines.count("ep-a\tDigest\t50") == 2  # ours' count, not collapsed to 1
    assert "ep-b\tDigest\t60" in lines


def test_split_marker_sides_diff3():
    text = (
        "common\n"
        "<<<<<<< HEAD\nours-line\n||||||| base\nbase-line\n=======\ntheirs-line\n"
        ">>>>>>> branch\n"
        "tail\n"
    )
    ours, theirs = _split_marker_sides(text)
    assert ours == "common\nours-line\ntail\n"
    assert theirs == "common\ntheirs-line\ntail\n"


def test_resolve_does_not_report_a_stale_archive_index_as_regenerated(tmp_path, monkeypatch):
    """Половина фикса жила без теста: откат resolve.py оставлял сьюту зелёной.

    `resolve` решал, что писать в `regenerated`, по `.is_file()` — а это ответ
    на вопрос «файл есть», не «мы его записали». Устаревший `archive/INDEX.md`
    от прошлого прогона проходил эту проверку, и единственное поле, по которому
    вызывающий узнаёт, ЧТО произошло, сообщало обратное правде — на пути
    разбора конфликтов, где рабочие правила полки велят доверять инструменту.

    Здесь тот же сценарий, что в соседнем тесте с непустым архивом, но рендер
    архивного INDEX сломан: файл на диске остаётся (старый), а в regenerated
    его быть не должно — зато предупреждение обязано попасть в notes.
    """
    root = _init_shelf(tmp_path)
    _shelve(root, "2026-07-20-old-one", DIGEST_A, "Старый эпизод")
    rebuild(root)
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "seed")

    _git(root, "checkout", "-q", "-b", "session-a")
    rollup(root, until="2026-07-25", slug="2026-07-25-rollup", digest=DIGEST_B, date="2026-07-25")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "a: rollup")

    _git(root, "checkout", "-q", "main")
    _shelve(root, "2026-07-29-storage-wal", DIGEST_B, "Хранилище на WAL")
    rebuild(root)
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "main: shelve")

    merge = _git(root, "merge", "session-a", check=False)
    assert merge.returncode != 0, "expected the derived-file conflict"

    stale = root / "archive" / "INDEX.md"
    assert stale.is_file(), "фикстура не воспроизводит случай: архивного INDEX нет"
    stale.write_text("# устаревший INDEX\n", encoding="utf-8")

    from pathlib import Path as _Path

    real_shelf = Shelf

    class _ExplodingArchiveShelf:
        """Настоящий Shelf для родителя, взрыв — только на архивной под-полке."""

        def __init__(self, path, *a, **kw):
            self._path = _Path(path)
            self._inner = real_shelf(path, *a, **kw)

        def __getattr__(self, name):
            return getattr(self._inner, name)

        def rebuild_index(self):
            if self._path.name == "archive":
                raise RuntimeError("архивный INDEX не собрался")
            return self._inner.rebuild_index()

    monkeypatch.setattr("docshelf_mcp.core.shelf.Shelf", _ExplodingArchiveShelf)

    result = resolve_shelf(root)

    assert "archive/INDEX.md" not in result.regenerated, (
        f"устаревший архивный INDEX объявлен перезаписанным: {result.regenerated}"
    )
    assert any("archive/INDEX.md not rebuilt" in n for n in result.notes), result.notes
    assert stale.read_text(encoding="utf-8") == "# устаревший INDEX\n", "файл всё же переписан"
