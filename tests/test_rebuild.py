"""Derived files are a pure function of the episodes (#58).

The point of the split is that ``ledger.tsv``, ``INDEX.md``, ``stats.svg`` and
each category's ``.meta.json`` can be thrown away and rebuilt byte-identically
from ``docs/``. These tests hold that property, plus the migration that makes
it true for shelves written before the split.
"""

import json
import subprocess

import pytest

pytest.importorskip("docshelf_mcp")

from docshelf_mcp.core.shelf import Shelf  # noqa: E402

from memshelf_mcp.core.rebuild import (  # noqa: E402
    DERIVED_PATHS,
    adopt,
    collect_episodes,
    rebuild,
    render_ledger,
)
from memshelf_mcp.core.shelve import shelve  # noqa: E402

DIGEST = (
    "The auth refactor moved token checks into middleware; the decided approach "
    "is JWT with a shared secret. The cookie-session alternative was rejected "
    "for cross-service calls. Open: rotating the shared secret."
)


def _init(root):
    Shelf(root).init(name="t", default_categories=["topics", "research", "sessions"])
    subprocess.run(["git", "-C", str(root), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.email", "t@t.test"], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.name", "t"], check=True)
    return root


def _shelve(root, slug, *, title=None, notes="", tokens=1000, kind="topic"):
    shelve(
        root,
        slug=slug,
        kind=kind,
        digest=DIGEST,
        sections={"Decisions": "Recorded."},
        display_title=title,
        notes=notes,
        approx_tokens=tokens,
        date=slug[:10],
    )


def test_rebuild_renders_every_derived_file(tmp_path):
    root = _init(tmp_path)
    _shelve(root, "2026-07-22-auth", title="Рефактор авторизации", notes="chat-1")
    report = rebuild(root)

    assert report.episodes == 1
    ledger = (root / "ledger.tsv").read_text(encoding="utf-8").splitlines()
    assert ledger[0].startswith("date\t")
    cells = ledger[1].split("\t")
    assert cells[0] == "2026-07-22"
    assert cells[1] == "2026-07-22-auth"
    assert cells[3] == "1000"
    assert cells[5] == "chat-1"

    meta = json.loads((root / "docs" / "topics" / ".meta.json").read_text(encoding="utf-8"))
    assert meta["2026-07-22-auth.md"]["title"] == "Рефактор авторизации"
    assert "Рефактор авторизации" in (root / "INDEX.md").read_text(encoding="utf-8")


def test_rebuild_is_idempotent(tmp_path):
    root = _init(tmp_path)
    _shelve(root, "2026-07-22-auth", title="Первый")
    rebuild(root)
    ledger_once = (root / "ledger.tsv").read_text(encoding="utf-8")

    second = rebuild(root)
    assert (root / "ledger.tsv").read_text(encoding="utf-8") == ledger_once
    assert "ledger.tsv" in second.unchanged


def test_derived_files_survive_deletion(tmp_path):
    """The property the whole split rests on: output is disposable."""
    root = _init(tmp_path)
    _shelve(root, "2026-07-22-auth", title="Первый", notes="n1")
    _shelve(root, "2026-07-23-storage", title="Второй", notes="n2")
    rebuild(root)
    before = {
        p: (root / p).read_text(encoding="utf-8") for p in DERIVED_PATHS if (root / p).is_file()
    }

    for path in before:
        (root / path).unlink()
    rebuild(root)

    after = {p: (root / p).read_text(encoding="utf-8") for p in before}
    assert after == before


def test_check_reports_drift_and_writes_nothing(tmp_path):
    root = _init(tmp_path)
    _shelve(root, "2026-07-22-auth")
    rebuild(root)
    (root / "ledger.tsv").write_text("date\tepisode_id\n", encoding="utf-8")

    report = rebuild(root, check=True)

    assert report.ok is False
    assert "ledger.tsv" in report.drifted
    # nothing was repaired — check only reports
    assert (root / "ledger.tsv").read_text(encoding="utf-8") == "date\tepisode_id\n"


def test_check_is_clean_right_after_a_rebuild(tmp_path):
    root = _init(tmp_path)
    _shelve(root, "2026-07-22-auth", title="Первый")
    rebuild(root)
    assert rebuild(root, check=True).ok is True


def test_rows_are_ordered_by_date_then_id(tmp_path):
    """Deterministic output — otherwise the PR guard's diff means nothing."""
    root = _init(tmp_path)
    _shelve(root, "2026-07-23-b")
    _shelve(root, "2026-07-22-a")
    _shelve(root, "2026-07-22-z", kind="research")

    records, _ = collect_episodes(root)
    assert [r.id for r in records] == ["2026-07-22-a", "2026-07-22-z", "2026-07-23-b"]


def test_episode_without_frontmatter_is_warned_not_swallowed(tmp_path):
    root = _init(tmp_path)
    _shelve(root, "2026-07-22-auth")
    (root / "docs" / "topics" / "stray.md").write_text("# stray\n\nno frontmatter\n", "utf-8")

    report = rebuild(root)

    assert report.episodes == 1
    assert any("stray.md" in w for w in report.warnings)


def test_meta_is_removed_when_no_episode_overrides_anything(tmp_path):
    root = _init(tmp_path)
    _shelve(root, "2026-07-22-auth", title="Заголовок")
    rebuild(root)
    meta = root / "docs" / "topics" / ".meta.json"
    assert meta.is_file()

    # description defaults to the digest's first sentence, so an entry only
    # disappears when the episode carries neither field.
    episode = root / "docs" / "topics" / "2026-07-22-auth.md"
    text = episode.read_text(encoding="utf-8")
    text = "\n".join(
        line
        for line in text.splitlines()
        if not line.startswith(("display_title:", "description:"))
    )
    episode.write_text(text + "\n", encoding="utf-8")

    rebuild(root)
    assert not meta.exists()


def test_notes_with_a_tab_cannot_shift_ledger_columns(tmp_path):
    """The TSV-safety guarantee has to survive the move into the frontmatter."""
    root = _init(tmp_path)
    _shelve(root, "2026-07-22-auth", notes="chat-1\tfragment")
    rebuild(root)

    row = (root / "ledger.tsv").read_text(encoding="utf-8").splitlines()[1]
    assert len(row.split("\t")) == 6
    assert row.split("\t")[5] == "chat-1 fragment"


# --- migration of a pre-#58 shelf ------------------------------------------


def _strip_new_fields(root, slug, category="topics"):
    """Turn an episode back into its pre-#58 shape."""
    path = root / "docs" / category / f"{slug}.md"
    text = path.read_text(encoding="utf-8")
    path.write_text(
        "\n".join(
            line
            for line in text.splitlines()
            if not line.startswith(("date:", "notes:", "display_title:", "description:"))
        )
        + "\n",
        encoding="utf-8",
    )


def test_adopt_moves_derived_only_fields_into_the_episode(tmp_path):
    root = _init(tmp_path)
    _shelve(root, "2026-07-22-auth", title="Рефактор", notes="chat-1")
    rebuild(root)
    _strip_new_fields(root, "2026-07-22-auth")

    # Without adoption the shelf would silently lose title, notes and date.
    assert rebuild(root, check=True).ok is False

    report = adopt(root)
    assert report["count"] == 1
    text = (root / "docs" / "topics" / "2026-07-22-auth.md").read_text(encoding="utf-8")
    assert "display_title: Рефактор" in text
    assert "notes: chat-1" in text
    assert "date: 2026-07-22" in text

    assert rebuild(root, check=True).ok is True


def test_adopt_is_idempotent(tmp_path):
    root = _init(tmp_path)
    _shelve(root, "2026-07-22-auth", title="Рефактор", notes="chat-1")
    rebuild(root)
    _strip_new_fields(root, "2026-07-22-auth")

    adopt(root)
    before = (root / "docs" / "topics" / "2026-07-22-auth.md").read_text(encoding="utf-8")
    second = adopt(root)
    after = (root / "docs" / "topics" / "2026-07-22-auth.md").read_text(encoding="utf-8")

    assert second["count"] == 0
    assert after == before


def test_adopt_reports_restated_digest_tokens(tmp_path):
    """A pre-#58 ledger can disagree with the file; the migration says so."""
    root = _init(tmp_path)
    _shelve(root, "2026-07-22-auth", title="Рефактор")
    rebuild(root)
    ledger = root / "ledger.tsv"
    lines = ledger.read_text(encoding="utf-8").splitlines()
    cells = lines[1].split("\t")
    recorded = int(cells[4])
    cells[4] = str(recorded + 40)
    ledger.write_text(lines[0] + "\n" + "\t".join(cells) + "\n", encoding="utf-8")
    _strip_new_fields(root, "2026-07-22-auth")

    report = adopt(root)

    assert report["restated_digest_tokens"] == [
        {"id": "2026-07-22-auth", "from": recorded + 40, "to": recorded}
    ]


def test_render_ledger_has_a_header_even_with_no_episodes(tmp_path):
    assert render_ledger([]).splitlines() == [
        "date\tepisode_id\tmode\tapprox_tokens_in\tdigest_tokens\tnotes"
    ]
