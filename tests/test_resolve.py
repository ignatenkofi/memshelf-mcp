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

from memshelf_mcp.core.rebuild import rebuild  # noqa: E402
from memshelf_mcp.core.resolve import (  # noqa: E402
    _split_marker_sides,
    _union_meta,
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
    assert "ledger.tsv" in result.resolved
    assert "docs/topics/.meta.json" in result.resolved
    assert "INDEX.md" in result.regenerated

    # both branches' rows survive the union
    ledger = (root / "ledger.tsv").read_text(encoding="utf-8")
    assert "2026-07-29-auth-refactor" in ledger
    assert "2026-07-29-storage-wal" in ledger
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
    assert any("not rebuilt" in n for n in result.notes)
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
    root = _init_shelf(tmp_path)
    _shelve(root, "2026-07-29-auth-refactor", DIGEST_A, "Рефактор авторизации")
    # simulate a half-finished manual resolution: markers on disk, no stages
    ledger = root / "ledger.tsv"
    ours_row = "2026-07-29\tep-ours\tlive\t1000\t50\t"
    theirs_row = "2026-07-29\tep-theirs\tlive\t2000\t60\t"
    ledger.write_text(
        "date\tepisode_id\tmode\tapprox_tokens_in\tdigest_tokens\tnotes\n"
        f"<<<<<<< HEAD\n{ours_row}\n=======\n{theirs_row}\n>>>>>>> session-b\n",
        encoding="utf-8",
    )

    result = resolve_shelf(root)

    text = ledger.read_text(encoding="utf-8")
    assert ours_row in text and theirs_row in text
    assert "<<<<<<<" not in text
    assert "ledger.tsv" in result.resolved


def test_union_tsv_dedups_and_keeps_header():
    header = "date\tid\n"
    ours = header + "2026-07-28\tshared\n2026-07-29\tours\n"
    theirs = header + "2026-07-28\tshared\n2026-07-29\ttheirs\n"
    merged = _union_tsv(ours, theirs, header)
    lines = merged.splitlines()
    assert lines[0] == "date\tid"
    assert lines[1:] == ["2026-07-28\tshared", "2026-07-29\tours", "2026-07-29\ttheirs"]


def test_union_meta_ours_wins_on_collision():
    ours = '{"a.md": {"title": "A-ours"}, "b.md": {"title": "B"}}'
    theirs = '{"a.md": {"title": "A-theirs"}, "c.md": {"title": "C"}}'
    merged = _union_meta(ours, theirs)
    import json

    data = json.loads(merged)
    assert data["a.md"]["title"] == "A-ours"
    assert set(data) == {"a.md", "b.md", "c.md"}


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
