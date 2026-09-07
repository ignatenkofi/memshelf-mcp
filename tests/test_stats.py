import subprocess

import pytest

from memshelf_mcp.core.rebuild import rebuild  # noqa: E402
from memshelf_mcp.core.stats import compute_stats

_HEADER = "date\tepisode_id\tmode\tapprox_tokens_in\tdigest_tokens\tnotes\n"


def _write_shelf(root, ledger_rows, *, index="# INDEX\n", recall_log=None):
    (root / "ledger.tsv").write_text(_HEADER + ledger_rows, encoding="utf-8")
    (root / "INDEX.md").write_text(index, encoding="utf-8")
    if recall_log is not None:
        (root / "recall-log.tsv").write_text(recall_log, encoding="utf-8")


# --- claimed economy (pure: no docshelf) --------------------------------------


def test_claimed_economy(tmp_path):
    _write_shelf(
        tmp_path,
        "2026-07-22\tep-a\tlive\t10000\t200\t\n2026-07-22\tep-b\tlive\t30000\t100\t\n",
        index="x" * 800,  # 800 chars -> 200 index tokens
    )
    s = compute_stats(tmp_path)
    assert s.episodes == 2
    assert s.shelved_mass == 40000
    assert s.digest_tokens == 300
    assert s.index_tokens == 200
    assert s.standing_cost == 500
    assert s.compression_ratio == 80.0
    assert s.recalls == 0


def test_reshelve_counts_once_latest_wins(tmp_path):
    _write_shelf(
        tmp_path,
        "2026-07-22\tep-a\tlive\t10000\t200\t\n2026-07-23\tep-a\tlive\t12000\t210\t\n",
    )
    s = compute_stats(tmp_path)
    assert s.episodes == 1
    assert s.shelved_mass == 12000  # latest row
    assert s.digest_tokens == 210


def test_realized_economy_from_recall_log(tmp_path):
    _write_shelf(
        tmp_path,
        "2026-07-22\tep-a\tlive\t10000\t200\t\n",
        recall_log="episode_id\tsection\tfetched_tokens\nep-a\tDecisions\t150\nep-a\t\t500\n",
    )
    s = compute_stats(tmp_path)
    assert s.recalls == 2
    assert s.episodes_recalled == 1
    assert s.fetched_tokens == 650
    assert s.realized_savings == (10000 - 150) + (10000 - 500)


def test_banner_line(tmp_path):
    from memshelf_mcp.core.stats import banner

    _write_shelf(tmp_path, "2026-07-22\tep-a\tlive\t100000\t200\t\n", index="x" * 800)
    s = compute_stats(tmp_path)
    line = banner(s)
    assert line.startswith("memshelf: 1 episodes")
    assert "holds ~100K est." in line  # the mass is caller-estimated, and the line says so (#79)
    assert "realized" not in line  # no recalls logged


def test_episode_mass_latest_row_wins(tmp_path):
    from memshelf_mcp.core.stats import episode_mass

    _write_shelf(
        tmp_path,
        "2026-07-22\tep-a\tlive\t10000\t200\t\n2026-07-23\tep-a\tlive\t12000\t210\t\n",
    )
    assert episode_mass(tmp_path, "ep-a") == 12000
    assert episode_mass(tmp_path, "ep-missing") is None


def test_missing_ledger_is_empty(tmp_path):
    s = compute_stats(tmp_path)
    assert s.episodes == 0
    assert s.shelved_mass == 0
    assert s.compression_ratio == 0.0


# --- end to end (needs docshelf) ----------------------------------------------


def test_shelve_recall_stats_end_to_end(tmp_path):
    pytest.importorskip("docshelf_mcp")
    from docshelf_mcp.core.shelf import Shelf

    from memshelf_mcp.core.shelve import shelve
    from memshelf_mcp.tools import RecallInput, StatsInput, run_recall, run_stats

    Shelf(tmp_path).init(name="t", default_categories=["topics", "research", "sessions"])
    subprocess.run(["git", "-C", str(tmp_path), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.email", "t@t.test"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.name", "tester"], check=True)
    shelve(
        tmp_path,
        slug="2026-07-22-x",
        kind="research",
        digest="A note; the local-first approach was chosen. Open: none.",
        sections={"Findings": "body text"},
        approx_tokens=5000,
        date="2026-07-22",
    )

    rebuild(tmp_path)  # the ledger is derived now (#58)
    s0 = run_stats(StatsInput(shelf_path=str(tmp_path)))
    assert s0["episodes"] == 1
    assert s0["shelved_mass"] == 5000
    assert s0["recalls"] == 0
    assert "note" in s0  # nudges toward --log

    recall_out = run_recall(
        RecallInput(shelf_path=str(tmp_path), episode_id="2026-07-22-x", log=True)
    )
    assert (tmp_path / "recall-log.tsv").is_file()
    # per-action delta (issue #49 idea 2)
    assert recall_out["saved_tokens"] > 0
    assert "saved" in recall_out["summary"]

    s1 = run_stats(StatsInput(shelf_path=str(tmp_path)))
    assert s1["recalls"] == 1
    assert s1["episodes_recalled"] == 1
    assert s1["realized_savings"] > 0
    assert "note" not in s1
    assert s1["banner"].startswith("memshelf: 1 episodes")


# --- window cap: work volume is not freed context (#110) ----------------------


def test_claim_above_window_is_clipped(tmp_path):
    """One episode claiming more than any window cannot free more than one."""
    _write_shelf(
        tmp_path,
        "2026-08-05\tep-big\tlive\t2400000\t200\t\n2026-08-06\tep-small\tlive\t50000\t100\t\n",
        index="x" * 800,  # 200 index tokens
    )
    s = compute_stats(tmp_path, context_window=200_000)
    assert s.work_volume == 2_450_000  # what the ledger claims, untouched
    assert s.shelved_mass == 250_000  # 200K clipped + 50K as-is
    assert s.capped_episodes == 1
    assert s.context_window == 200_000
    # compression is computed from the clipped mass, not the claim
    assert s.compression_ratio == round(250_000 / 500, 1)


def test_default_window_actually_bites(tmp_path, monkeypatch):
    """A default that never clips would leave #110 in place; this one clips."""
    from memshelf_mcp.core.stats import DEFAULT_CONTEXT_WINDOW

    monkeypatch.delenv("MEMSHELF_CONTEXT_WINDOW", raising=False)
    assert DEFAULT_CONTEXT_WINDOW == 200_000
    _write_shelf(tmp_path, "2026-08-05\tep-big\tlive\t2400000\t200\t\n")
    s = compute_stats(tmp_path)
    assert s.capped_episodes == 1
    assert s.shelved_mass == 200_000
    assert s.work_volume == 2_400_000  # the claim itself is never rewritten


def test_realized_savings_bounded_by_window(tmp_path):
    """A recall cannot save context the window never held."""
    _write_shelf(
        tmp_path,
        "2026-08-05\tep-big\tlive\t2400000\t200\t\n",
        recall_log="episode_id\tsection\tfetched_tokens\nep-big\tDecisions\t150\n",
    )
    s = compute_stats(tmp_path, context_window=200_000)
    assert s.realized_savings == 200_000 - 150


def test_episode_mass_is_clipped_too(tmp_path):
    """The per-recall saving line reads the same bounded number."""
    from memshelf_mcp.core.stats import episode_mass

    _write_shelf(tmp_path, "2026-08-05\tep-big\tlive\t2400000\t200\t\n")
    assert episode_mass(tmp_path, "ep-big", context_window=200_000) == 200_000
    assert episode_mass(tmp_path, "ep-big", context_window=5_000_000) == 2_400_000


def test_banner_names_the_clip(tmp_path):
    from memshelf_mcp.core.stats import banner

    _write_shelf(tmp_path, "2026-08-05\tep-big\tlive\t2400000\t200\t\n", index="x" * 800)
    line = banner(compute_stats(tmp_path, context_window=200_000))
    assert "holds ~200K est." in line
    assert "1 episode(s) clipped" in line
    assert "work volume ~2.4M" in line
    # and stays quiet when nothing was clipped
    _write_shelf(tmp_path, "2026-08-05\tep-a\tlive\t100000\t200\t\n", index="x" * 800)
    assert "clipped" not in banner(compute_stats(tmp_path, context_window=200_000))


def test_window_resolution_order(tmp_path, monkeypatch):
    from memshelf_mcp.core.stats import DEFAULT_CONTEXT_WINDOW, resolve_context_window

    monkeypatch.delenv("MEMSHELF_CONTEXT_WINDOW", raising=False)
    assert resolve_context_window() == DEFAULT_CONTEXT_WINDOW
    monkeypatch.setenv("MEMSHELF_CONTEXT_WINDOW", "300000")
    assert resolve_context_window() == 300_000
    assert resolve_context_window(150_000) == 150_000  # explicit wins
    # a broken variable falls back rather than reporting a zero-token window
    monkeypatch.setenv("MEMSHELF_CONTEXT_WINDOW", "not-a-number")
    assert resolve_context_window() == DEFAULT_CONTEXT_WINDOW
    monkeypatch.setenv("MEMSHELF_CONTEXT_WINDOW", "0")
    assert resolve_context_window() == DEFAULT_CONTEXT_WINDOW


def test_stats_tool_explains_the_clip(tmp_path, monkeypatch):
    from memshelf_mcp.tools import StatsInput, run_stats

    monkeypatch.delenv("MEMSHELF_CONTEXT_WINDOW", raising=False)
    _write_shelf(tmp_path, "2026-08-05\tep-big\tlive\t2400000\t200\t\n")
    payload = run_stats(StatsInput(shelf_path=str(tmp_path), context_window=200_000))
    assert payload["work_volume"] == 2_400_000
    assert payload["shelved_mass"] == 200_000
    assert "capped_note" in payload
    clean = run_stats(StatsInput(shelf_path=str(tmp_path), context_window=5_000_000))
    assert "capped_note" not in clean


def test_human_trims_trailing_zeros_in_megatokens():
    """``"1.00M".rstrip("0")`` ends on "M" and strips nothing — the trim was dead."""
    from memshelf_mcp.core.advisor import _human as advisor_human
    from memshelf_mcp.core.stats import _human

    assert _human(1_000_000) == "1M"
    assert _human(2_400_000) == "2.4M"
    assert _human(26_310_000) == "26.31M"
    assert _human(1_000) == "1K"
    assert _human(999) == "999"
    # the same formatter lives in advisor.py; it had the same dead trim
    assert advisor_human(1_000_000) == "1M"
    assert advisor_human(2_400_000) == "2.4M"
