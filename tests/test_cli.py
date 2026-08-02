import json
import subprocess

import pytest

pytest.importorskip("docshelf_mcp")

from docshelf_mcp.core.shelf import Shelf  # noqa: E402

from memshelf_mcp.cli import main  # noqa: E402


def test_cli_import_discover_then_extract(tmp_path, capsys):
    export = tmp_path / "conversations.json"
    export.write_text(
        json.dumps(
            [
                {
                    "uuid": "c1",
                    "name": "misc",
                    "chat_messages": [
                        {"sender": "human", "content": [{"type": "text", "text": "reconcile Q3?"}]},
                        {
                            "sender": "assistant",
                            "content": [
                                {"type": "text", "text": "The quarterly reconciliation is done."},
                                {"type": "tool_use", "name": "run", "input": {"c": "NOISE"}},
                            ],
                        },
                    ],
                }
            ]
        ),
        encoding="utf-8",
    )
    assert main(["import", "discover", "--path", str(export), "--marker", "reconciliation"]) == 0
    assert json.loads(capsys.readouterr().out)["matched"] == 1

    out_file = tmp_path / "clean.md"
    code = main(
        ["import", "extract", "--path", str(export), "--select", "c1", "--out", str(out_file)]
    )
    assert code == 0
    result = json.loads(capsys.readouterr().out)
    assert result["stripped_blocks"] == 1
    assert "NOISE" not in out_file.read_text(encoding="utf-8")


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
    # The ledger the banner reads is derived now (#58) — the CLI renders it.
    assert main(["rebuild", "--shelf", str(tmp_path)]) == 0
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


# ── #71: lint-digest and --amend on the CLI ───────────────────────────────


def test_cli_lint_digest_accepts_a_good_digest(tmp_path, capsys):
    rc = main(
        [
            "lint-digest",
            "--digest",
            "The guard was rewritten: the decided approach asserts the advisory id, "
            "not the exit code. The rc-only check was rejected. Open: a second ecosystem.",
        ]
    )
    assert rc == 0
    assert "digest ok" in capsys.readouterr().out


def test_cli_lint_digest_rejects_and_names_the_fix(capsys):
    rc = main(["lint-digest", "--digest", "we decided to keep it"])
    assert rc == 1
    out = capsys.readouterr().out
    assert "referent-we" in out
    assert "name the actor" in out


def test_cli_lint_digest_warning_passes_unless_strict(capsys):
    thin = "The backup script ran on three devices and uploaded files to the share overnight."
    assert main(["lint-digest", "--digest", thin]) == 0
    assert "thin" in capsys.readouterr().out
    # --strict is what a pre-commit-style caller wants; the default must not
    # block, or a legitimate reference digest becomes unwritable.
    assert main(["lint-digest", "--strict", "--digest", thin]) == 1


def test_cli_lint_digest_reads_a_file(tmp_path, capsys):
    f = tmp_path / "d.txt"
    f.write_text("we shipped it", encoding="utf-8")
    assert main(["lint-digest", "--digest-file", str(f)]) == 1
    assert "referent-we" in capsys.readouterr().out


def _shelf(tmp_path):
    Shelf(tmp_path).init(name="cli shelf", default_categories=["topics", "research", "sessions"])
    subprocess.run(["git", "-C", str(tmp_path), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.email", "t@t.test"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.name", "tester"], check=True)
    return tmp_path


CLI_DIGEST = (
    "The retry policy moved into the client; the decided approach is capped "
    "exponential backoff. The fixed-interval alternative was rejected. Open: the cap."
)


def _shelve_argv(root, *extra):
    return [
        "shelve",
        "--shelf",
        str(root),
        "--slug",
        "2026-08-02-retry",
        "--kind",
        "topic",
        "--digest",
        CLI_DIGEST,
        "--section",
        "Decisions=backoff chosen",
        *extra,
    ]


def test_cli_shelve_twice_names_the_flag_that_fixes_it(tmp_path, capsys):
    """The old failure pointed at a Python kwarg the CLI user cannot pass."""
    root = _shelf(tmp_path)
    assert main(_shelve_argv(root)) == 0
    capsys.readouterr()
    assert main(_shelve_argv(root)) == 1
    assert "--amend" in capsys.readouterr().err


def test_cli_amend_rewrites_in_place(tmp_path, capsys):
    root = _shelf(tmp_path)
    assert main(_shelve_argv(root)) == 0
    capsys.readouterr()
    assert main(_shelve_argv(root, "--amend", "--approx-tokens", "9000")) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["amended"] is True
    episode = (tmp_path / "docs" / "topics" / "2026-08-02-retry.md").read_text(encoding="utf-8")
    assert "approx_tokens: 9000" in episode


def test_cli_amend_of_a_missing_slug_fails_without_writing(tmp_path, capsys):
    root = _shelf(tmp_path)
    assert main(_shelve_argv(root, "--amend")) == 1
    assert "no episode" in capsys.readouterr().err
    assert not (tmp_path / "docs" / "topics" / "2026-08-02-retry.md").exists()
