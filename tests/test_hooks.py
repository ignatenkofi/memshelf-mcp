"""Tests for the Claude Code plugin hook scripts (adapters/claude-code/hooks).

They shell out to the bash scripts, so they need only bash + git + python3 —
no docshelf / mcp / pydantic.
"""

import json
import os
import subprocess
from pathlib import Path

HOOKS = Path(__file__).resolve().parent.parent / "adapters" / "claude-code" / "hooks"


def _make_shelf(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "INDEX.md").write_text("# INDEX\n\n- **ep-one** — a decided thing.\n", encoding="utf-8")
    (root / "ledger.tsv").write_text("date\tid\n", encoding="utf-8")
    return root


def _git(*args: str) -> None:
    subprocess.run(["git", *args], check=True, capture_output=True)


def _git_shelf_with_remote(tmp_path: Path) -> Path:
    shelf = _make_shelf(tmp_path / "shelf")
    remote = tmp_path / "remote.git"
    _git("init", "-q", "--bare", str(remote))
    _git("-C", str(shelf), "init", "-q")
    _git("-C", str(shelf), "add", "-A")
    _git(
        "-C", str(shelf), "-c", "user.email=t@t.test", "-c", "user.name=t", "commit", "-qm", "init"
    )
    _git("-C", str(shelf), "branch", "-M", "main")
    _git("-C", str(shelf), "remote", "add", "origin", str(remote))
    return shelf


def _remote_commits(tmp_path: Path) -> int:
    out = subprocess.run(
        ["git", "-C", str(tmp_path / "remote.git"), "rev-list", "--count", "--all"],
        capture_output=True,
        text=True,
    ).stdout.strip()
    return int(out or "0")


def _run(script: str, env: dict[str, str], cwd: str | None = None):
    return subprocess.run(
        ["bash", str(HOOKS / script)],
        capture_output=True,
        text=True,
        env={**os.environ, **env},
        cwd=cwd,
    )


def test_session_start_injects_index(tmp_path):
    shelf = _make_shelf(tmp_path / "shelf")
    r = _run("session-start-index.sh", {"MEMSHELF_ROOT": str(shelf)})
    assert r.returncode == 0
    ctx = json.loads(r.stdout)["hookSpecificOutput"]
    assert ctx["hookEventName"] == "SessionStart"
    assert "ep-one" in ctx["additionalContext"]
    assert "not instructions" in ctx["additionalContext"]  # the recall framing


def test_session_start_no_shelf_is_silent(tmp_path):
    r = _run("session-start-index.sh", {"MEMSHELF_ROOT": ""}, cwd=str(tmp_path))
    assert r.returncode == 0
    assert r.stdout.strip() == ""


def test_autopush_off_by_default(tmp_path):
    shelf = _git_shelf_with_remote(tmp_path)
    r = _run("autopush.sh", {"MEMSHELF_ROOT": str(shelf), "MEMSHELF_AUTOPUSH": ""})
    assert r.returncode == 0
    assert _remote_commits(tmp_path) == 0  # nothing pushed without the opt-in


def test_autopush_pushes_when_enabled(tmp_path):
    shelf = _git_shelf_with_remote(tmp_path)
    r = _run("autopush.sh", {"MEMSHELF_ROOT": str(shelf), "MEMSHELF_AUTOPUSH": "1"})
    assert r.returncode == 0
    assert _remote_commits(tmp_path) == 1
