import re
import subprocess
from datetime import datetime, timezone

import pytest

pytest.importorskip("docshelf_mcp")

from docshelf_mcp.core.shelf import Shelf  # noqa: E402

from memshelf_mcp.cli import main  # noqa: E402
from memshelf_mcp.core.rebuild import rebuild  # noqa: E402
from memshelf_mcp.core.recall import EpisodeNotFound  # noqa: E402
from memshelf_mcp.core.shelve import shelve  # noqa: E402
from memshelf_mcp.tools import (  # noqa: E402
    IndexInput,
    RecallInput,
    SearchInput,
    run_index,
    run_recall,
    run_search,
)


def _shelf_with_episode(root):
    Shelf(root).init(name="t", default_categories=["topics", "research", "sessions"])
    subprocess.run(["git", "-C", str(root), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.email", "t@t.test"], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.name", "tester"], check=True)
    shelve(
        root,
        slug="2026-07-22-auth",
        kind="topic",
        digest="The auth refactor chose JWT; cookie-session was rejected. Open: secret rotation.",
        sections={
            "Decisions": "JWT chosen for cross-service calls.",
            "Open threads": "rotate the shared secret",
        },
        date="2026-07-22",
    )
    rebuild(root)  # INDEX/ledger are derived since #58
    return root


def test_recall_whole_episode(tmp_path):
    root = _shelf_with_episode(tmp_path)
    out = run_recall(RecallInput(shelf_path=str(root), episode_id="2026-07-22-auth"))
    assert out["address"] == "docs/topics/2026-07-22-auth.md"
    assert "recalled-episode" in out["content"]  # data envelope present
    assert "JWT chosen" in out["content"]
    assert "## Decisions" in out["content"]


def test_recall_single_section_only(tmp_path):
    root = _shelf_with_episode(tmp_path)
    out = run_recall(
        RecallInput(shelf_path=str(root), episode_id="2026-07-22-auth", section="Decisions")
    )
    assert out["section"] == "Decisions"
    assert "JWT chosen" in out["content"]
    assert "rotate the shared secret" not in out["content"]  # other sections excluded
    assert "## Open threads" not in out["content"]


def test_recall_unknown_id_raises(tmp_path):
    root = _shelf_with_episode(tmp_path)
    with pytest.raises(EpisodeNotFound):
        run_recall(RecallInput(shelf_path=str(root), episode_id="2026-07-22-missing"))


def test_recall_unknown_section_raises(tmp_path):
    root = _shelf_with_episode(tmp_path)
    with pytest.raises(EpisodeNotFound):
        run_recall(RecallInput(shelf_path=str(root), episode_id="2026-07-22-auth", section="Nope"))


def test_index_returns_index_text(tmp_path):
    root = _shelf_with_episode(tmp_path)
    out = run_index(IndexInput(shelf_path=str(root)))
    assert "2026-07-22-auth" in out["index"]


def test_search_finds_episode(tmp_path):
    root = _shelf_with_episode(tmp_path)
    out = run_search(SearchInput(shelf_path=str(root), query="JWT"))
    assert any("2026-07-22-auth" in hit["address"] for hit in out["hits"])


def test_cli_recall_prints_section(tmp_path, capsys):
    root = _shelf_with_episode(tmp_path)
    code = main(
        ["recall", "--shelf", str(root), "--id", "2026-07-22-auth", "--section", "Decisions"]
    )
    assert code == 0
    out = capsys.readouterr().out
    assert "JWT chosen" in out
    assert "recalled-episode" in out


def test_cli_recall_missing_exits_1(tmp_path, capsys):
    root = _shelf_with_episode(tmp_path)
    code = main(["recall", "--shelf", str(root), "--id", "2026-07-22-nope"])
    assert code == 1
    assert "no episode" in capsys.readouterr().err


_UTC_SECOND = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")


def _utc_now_to_the_second():
    return datetime.now(timezone.utc).replace(microsecond=0)


def test_recall_log_row_carries_the_recall_time(tmp_path):
    """#193: without a time column the realized savings cannot be split by
    period, and a commit time is not a recall time — rows are committed in
    batches."""
    root = _shelf_with_episode(tmp_path)
    log = root / "recall-log.tsv"
    assert not log.exists()

    before = _utc_now_to_the_second()
    run_recall(RecallInput(shelf_path=str(root), episode_id="2026-07-22-auth", section="Decisions"))
    after = _utc_now_to_the_second()

    header, row = log.read_text(encoding="utf-8").splitlines()
    assert header == "episode_id\tsection\tfetched_tokens\tts"
    episode_id, section, fetched, ts = row.split("\t")
    assert (episode_id, section) == ("2026-07-22-auth", "Decisions")
    assert int(fetched) > 0
    assert _UTC_SECOND.fullmatch(ts), ts
    stamp = datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    assert before <= stamp <= after, (before, ts, after)


def test_recall_appends_to_a_log_from_before_the_time_column(tmp_path):
    """An existing three-column log keeps its header and takes the longer
    rows; nothing already in it is rewritten."""
    root = _shelf_with_episode(tmp_path)
    log = root / "recall-log.tsv"
    old = "episode_id\tsection\tfetched_tokens\n2026-07-22-auth\t\t500\n"
    log.write_text(old, encoding="utf-8")

    run_recall(RecallInput(shelf_path=str(root), episode_id="2026-07-22-auth"))

    text = log.read_text(encoding="utf-8")
    assert text.startswith(old)
    new_rows = text[len(old) :].splitlines()
    assert len(new_rows) == 1
    cells = new_rows[0].split("\t")
    assert cells[:2] == ["2026-07-22-auth", ""]
    assert _UTC_SECOND.fullmatch(cells[3]), new_rows
