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


# --- pre-commit PII/secret guard (issue #32) --------------------------------


def _staged_repo(root: Path, files: dict[str, str]) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    _git("-C", str(root), "init", "-q")
    for name, content in files.items():
        (root / name).write_text(content, encoding="utf-8")
    _git("-C", str(root), "add", "-A")
    return root


def _precommit(root: Path, env: dict[str, str] | None = None):
    return subprocess.run(
        ["bash", str(HOOKS / "pre-commit")],
        capture_output=True,
        text=True,
        env={**os.environ, **(env or {})},
        cwd=str(root),
    )


# By default the scanner is absent in CI; opt into the built-in-only downgrade so
# these tests exercise the shape layer without needing pii-mcp installed.
_BUILTIN = {"MEMSHELF_PII_BUILTIN_ONLY": "1"}


def test_precommit_blocks_github_token(tmp_path):
    repo = _staged_repo(tmp_path / "s", {"ep.md": "pushed with ghp_" + "a" * 36 + " now\n"})
    r = _precommit(repo, _BUILTIN)
    assert r.returncode == 1
    assert "token-github" in r.stderr


def test_precommit_blocks_email(tmp_path):
    repo = _staged_repo(tmp_path / "s", {"ep.md": "ping alice.smith@example.com please\n"})
    r = _precommit(repo, _BUILTIN)
    assert r.returncode == 1
    assert "email" in r.stderr


def test_precommit_fails_loud_when_scanner_missing(tmp_path):
    # Clean content, pii-mcp absent, no override -> must NOT pass silently.
    repo = _staged_repo(tmp_path / "s", {"ep.md": "a clean decided note, nothing secret\n"})
    r = _precommit(repo, {"MEMSHELF_PII_CMD": "pii-mcp-not-installed-xyz verify"})
    assert r.returncode == 2
    assert "not found" in r.stderr


def test_precommit_builtin_only_passes_clean(tmp_path):
    repo = _staged_repo(tmp_path / "s", {"ep.md": "a clean decided note, nothing secret\n"})
    r = _precommit(repo, _BUILTIN)
    assert r.returncode == 0


def test_precommit_skip_escape_hatch(tmp_path):
    repo = _staged_repo(tmp_path / "s", {"ep.md": "ghp_" + "a" * 36 + "\n"})
    r = _precommit(repo, {"MEMSHELF_PII_SKIP": "1"})
    assert r.returncode == 0
    assert "SKIPPED" in r.stderr


def test_precommit_ignores_redaction_marker(tmp_path):
    # A correctly-redacted env-secret must not be re-flagged (idempotence with
    # the shelve redaction pass).
    repo = _staged_repo(
        tmp_path / "s", {"ep.md": "runbook: SONAR_TOKEN=«redacted:env-secret» in env\n"}
    )
    r = _precommit(repo, _BUILTIN)
    assert r.returncode == 0, r.stderr


def test_precommit_policy_patterns_pack(tmp_path):
    # The shelf's own POLICY.patterns (shared with #16) extends the shape scan.
    repo = _staged_repo(
        tmp_path / "s",
        {
            "POLICY.patterns": "# per-shelf pack\nstudent-id   S[0-9]{1,2}\n",
            "ep.md": "reviewed the submission from S7 this week\n",
        },
    )
    r = _precommit(repo, _BUILTIN)
    assert r.returncode == 1
    assert "pack:student-id" in r.stderr


def test_precommit_pluggable_scanner_contract(tmp_path):
    repo = _staged_repo(tmp_path / "s", {"ep.md": "a clean decided note\n"})
    fake = tmp_path / "fakepii"
    fake.write_text("#!/usr/bin/env bash\nexit ${FAKE_RC:-0}\n", encoding="utf-8")
    fake.chmod(0o755)
    cmd = {"MEMSHELF_PII_CMD": f"{fake} verify"}
    assert _precommit(repo, {**cmd, "FAKE_RC": "0"}).returncode == 0
    assert _precommit(repo, {**cmd, "FAKE_RC": "1"}).returncode == 1
    assert _precommit(repo, {**cmd, "FAKE_RC": "2"}).returncode == 2
