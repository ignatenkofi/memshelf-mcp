import json
import subprocess

import pytest

pytest.importorskip("docshelf_mcp")

from docshelf_mcp.core.shelf import Shelf  # noqa: E402

from memshelf_mcp.cli import main  # noqa: E402


def _init(root):
    Shelf(root).init(name="t", default_categories=["topics", "research", "sessions"])
    subprocess.run(["git", "-C", str(root), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.email", "t@t.test"], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.name", "tester"], check=True)
    return root


def test_cli_shelve_writes_episode(tmp_path, capsys):
    code = main(
        [
            "shelve",
            "--shelf",
            str(_init(tmp_path)),
            "--slug",
            "2026-07-22-cli",
            "--kind",
            "research",
            "--digest",
            "A CLI-driven note; the local-first approach was chosen. Open: none.",
            "--section",
            "Findings=works end to end from the shell",
            "--display-title",
            "CLI note",
            "--date",
            "2026-07-22",
        ]
    )
    assert code == 0
    out = json.loads(capsys.readouterr().out)
    assert out["status"] == "ok"
    assert out["address"] == "docs/research/2026-07-22-cli.md"
    assert out["committed"] is True
    assert (tmp_path / "docs" / "research" / "2026-07-22-cli.md").is_file()


def test_cli_contract_violation_exits_1(tmp_path, capsys):
    code = main(
        [
            "shelve",
            "--shelf",
            str(_init(tmp_path)),
            "--slug",
            "2026-07-22-bad",
            "--kind",
            "topic",
            "--digest",
            "We did stuff.",  # first-person referent
            "--section",
            "Decisions=x",
            "--date",
            "2026-07-22",
        ]
    )
    assert code == 1
    assert "referent-we" in capsys.readouterr().err
    assert not (tmp_path / "docs" / "topics" / "2026-07-22-bad.md").exists()


def test_cli_stats_banner_one_line(tmp_path, capsys):
    _init(tmp_path)
    main(
        [
            "shelve",
            "--shelf",
            str(tmp_path),
            "--slug",
            "2026-07-23-b",
            "--kind",
            "research",
            "--digest",
            "A note; the approach was chosen. Open: none.",
            "--section",
            "Findings=f",
            "--approx-tokens",
            "40000",
            "--date",
            "2026-07-23",
        ]
    )
    capsys.readouterr()
    code = main(["stats", "--shelf", str(tmp_path), "--banner"])
    assert code == 0
    out = capsys.readouterr().out.strip()
    assert out.startswith("memshelf: 1 episodes")
    assert "\n" not in out


def test_cli_section_without_equals_errors(tmp_path):
    with pytest.raises(SystemExit):
        main(
            [
                "shelve",
                "--shelf",
                str(tmp_path),
                "--slug",
                "x",
                "--kind",
                "topic",
                "--digest",
                "d",
                "--section",
                "noequals",
            ]
        )
