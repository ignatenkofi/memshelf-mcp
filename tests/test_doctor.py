import subprocess

import pytest

pytest.importorskip("docshelf_mcp")

from docshelf_mcp.core.shelf import Shelf  # noqa: E402

from memshelf_mcp.core.doctor import check_shelf  # noqa: E402
from memshelf_mcp.core.shelve import shelve  # noqa: E402


def _init(root):
    Shelf(root).init(name="t", default_categories=["topics", "research", "sessions"])
    subprocess.run(["git", "-C", str(root), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.email", "t@t.test"], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.name", "tester"], check=True)
    return root


def _codes(report):
    return {f.code for f in report.findings}


def _write_raw(root, category, name, text):
    cat = root / "docs" / category
    cat.mkdir(parents=True, exist_ok=True)
    (cat / f"{name}.md").write_text(text, encoding="utf-8")
    Shelf(root).rebuild_index()


def test_clean_shelf_is_healthy(tmp_path):
    root = _init(tmp_path)
    shelve(
        root,
        slug="2026-07-22-ok",
        kind="topic",
        digest="The plan chose X; the Y alternative was rejected. Open: nothing.",
        sections={"Decisions": "X over Y"},
        date="2026-07-22",
    )
    report = check_shelf(root)
    assert report.ok  # info/warnings allowed; no errors
    assert report.episodes_checked == 1


def test_secret_at_rest_flagged(tmp_path):
    root = _init(tmp_path)
    _write_raw(
        root,
        "topics",
        "2026-07-22-leak",
        "# 2026-07-22-leak\n\n---\nid: 2026-07-22-leak\nkind: topic\n---\n\n"
        "## Digest\nA decided change; nothing open.\n\n"
        "## Decisions\npasted token ghp_" + "a" * 36 + "\n",
    )
    report = check_shelf(root)
    assert not report.ok
    assert "secret-at-rest" in _codes(report)


def test_missing_required_section_flagged(tmp_path):
    root = _init(tmp_path)
    _write_raw(
        root,
        "topics",
        "2026-07-22-bad",
        "# 2026-07-22-bad\n\n---\nid: 2026-07-22-bad\nkind: topic\n---\n\n"
        "## Digest\nA decided change; nothing open.\n",  # topic without ## Decisions
    )
    report = check_shelf(root)
    assert not report.ok
    assert "missing-section" in _codes(report)


def test_tool_shelved_env_secret_stays_healthy(tmp_path):
    # An env-secret goes in, shelve() masks the value, and doctor must NOT
    # re-flag the stored `KEY=«redacted:env-secret»` (idempotence).
    root = _init(tmp_path)
    shelve(
        root,
        slug="2026-07-22-env",
        kind="topic",
        digest="The runbook decision: keep tokens in env files. Open: nothing.",
        sections={"Decisions": "SONAR_TOKEN=squ_someval moved to ~/.sqst-env"},
        date="2026-07-22",
    )
    report = check_shelf(root)
    assert report.ok, [f.code for f in report.findings]
    assert "secret-at-rest" not in _codes(report)


def test_orphan_ledger_row_flagged(tmp_path):
    root = _init(tmp_path)
    shelve(
        root,
        slug="2026-07-22-ok",
        kind="topic",
        digest="The plan chose X; the Y alternative was rejected. Open: nothing.",
        sections={"Decisions": "X"},
        date="2026-07-22",
    )
    with (tmp_path / "ledger.tsv").open("a", encoding="utf-8") as fh:
        fh.write("2026-07-22\t2026-07-22-ghost\tlive\t100\t20\t\n")
    report = check_shelf(root)
    assert "orphan-ledger-row" in _codes(report)
