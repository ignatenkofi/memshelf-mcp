import subprocess

import pytest

from memshelf_mcp.core.chart import render_chart_svg, write_chart

_HEADER = "date\tepisode_id\tmode\tapprox_tokens_in\tdigest_tokens\tnotes\n"


def _ledger(root, rows):
    (root / "ledger.tsv").write_text(_HEADER + rows, encoding="utf-8")


def test_no_ledger_yields_none(tmp_path):
    assert render_chart_svg(tmp_path) is None
    assert write_chart(tmp_path) is None


def test_chart_has_both_series_and_ratio(tmp_path):
    _ledger(
        tmp_path,
        "2026-07-13\tep-a\tlive\t100000\t200\t\n2026-07-14\tep-b\tlive\t150000\t300\t\n",
    )
    svg = render_chart_svg(tmp_path)
    assert svg.startswith("<svg")
    assert "without memshelf: 250K" in svg  # cumulative expected
    assert "on the shelf:" in svg
    assert "saved 500.0:1" in svg  # 250000 / 500
    assert "07-13" in svg and "07-14" in svg  # date labels


def test_shelve_redraws_chart(tmp_path):
    pytest.importorskip("docshelf_mcp")
    from docshelf_mcp.core.shelf import Shelf

    from memshelf_mcp.core.shelve import shelve

    Shelf(tmp_path).init(name="t", default_categories=["topics", "research", "sessions"])
    subprocess.run(["git", "-C", str(tmp_path), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.email", "t@t.test"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.name", "t"], check=True)

    shelve(
        tmp_path,
        slug="2026-07-22-one",
        kind="topic",
        digest="The first topic chose X; Y was rejected. Open: none.",
        sections={"Decisions": "X"},
        approx_tokens=50000,
        date="2026-07-22",
    )
    chart = tmp_path / "stats.svg"
    assert chart.is_file()
    first = chart.read_text(encoding="utf-8")
    assert "07-22" in first

    shelve(
        tmp_path,
        slug="2026-07-23-two",
        kind="topic",
        digest="The second topic chose Z; W was rejected. Open: none.",
        sections={"Decisions": "Z"},
        approx_tokens=70000,
        date="2026-07-23",
    )
    second = chart.read_text(encoding="utf-8")
    assert second != first  # redrawn
    assert "07-23" in second
    assert "without memshelf: 120K" in second  # 50K + 70K cumulative

    # the chart travels inside the shelve commit
    tracked = subprocess.run(
        ["git", "-C", str(tmp_path), "ls-files", "stats.svg"], capture_output=True, text=True
    ).stdout.strip()
    assert tracked == "stats.svg"
