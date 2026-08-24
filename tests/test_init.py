import os
import subprocess

import pytest

pytest.importorskip("docshelf_mcp")

from memshelf_mcp.core.doctor import check_shelf  # noqa: E402
from memshelf_mcp.core.init import MEMORY_PREAMBLE, InitError, init_shelf  # noqa: E402
from memshelf_mcp.core.shelve import shelve  # noqa: E402


def test_git_local_default_creates_everything(tmp_path):
    result = init_shelf(tmp_path, name="test memory")
    assert result.storage == "git-local"
    for rel in (
        "INDEX.md",
        "POLICY.md",
        "POLICY.patterns",
        "ledger.tsv",
        "shelf.yml",
        ".docshelf.json",
    ):
        assert (tmp_path / rel).is_file(), rel
    # a fresh pack is all-comments: it parses to zero active rules
    from memshelf_mcp.core.policy import load_pattern_pack

    assert load_pattern_pack(tmp_path).patterns == []
    assert (tmp_path / ".git").is_dir()
    assert result.committed and result.commit
    # no remote in git-local mode — nothing to accidentally push to
    assert (
        subprocess.run(
            ["git", "-C", str(tmp_path), "remote", "get-url", "origin"], capture_output=True
        ).returncode
        != 0
    )
    # the INDEX carries the recall preamble, not docshelf's raw-URL default
    assert "data, not instructions" in (tmp_path / "INDEX.md").read_text(encoding="utf-8")
    assert MEMORY_PREAMBLE.split(".")[0] in (tmp_path / ".docshelf.json").read_text(
        encoding="utf-8"
    )


def _detach_ambient_identity(monkeypatch, *, config_file: str = os.devnull) -> None:
    """Cut every path by which the host's git identity could reach a subprocess.

    Silencing the config files alone does not reproduce a machine without an
    identity (#123): with no `user.name`/`user.email` git does not refuse, it
    *derives* one from the gecos name and `user@hostname`, so on a workstation
    the commit succeeds under the developer's own name and the fallback branch
    is never taken. `user.useConfigOnly=true` disables exactly that derivation
    and nothing else, which is what makes the refusal deterministic on any host
    rather than a property of how the machine happens to be named.
    """
    for var in (
        "GIT_AUTHOR_NAME",
        "GIT_AUTHOR_EMAIL",
        "GIT_COMMITTER_NAME",
        "GIT_COMMITTER_EMAIL",
        "EMAIL",
    ):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", config_file)
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "user.useConfigOnly")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", "true")


def _author_of_head(root) -> str:
    return subprocess.run(
        ["git", "-C", str(root), "log", "-1", "--format=%an <%ae>"],
        capture_output=True,
        text=True,
    ).stdout.strip()


def test_git_local_commits_without_an_ambient_git_identity(tmp_path, monkeypatch):
    # A fresh machine, a container, or a CI runner has no user.name/user.email,
    # and `git commit` refuses outright there — which used to leave a shelf with
    # a .git directory and no commit at all (silently non-durable, committed=False).
    _detach_ambient_identity(monkeypatch)

    result = init_shelf(tmp_path, name="test memory")

    assert result.committed and result.commit
    assert _author_of_head(tmp_path) == "memshelf <memshelf@localhost>"


def test_git_local_keeps_an_ambient_git_identity(tmp_path, monkeypatch):
    # The counterpart: where an identity does exist, the fallback must stay out
    # of the way — neither authoring the commit nor landing in the shelf's own
    # config. That is what passing it via `-c` buys (core/shelve.py), and until
    # now nothing held the property down.
    home = tmp_path / "home"
    home.mkdir()
    gitconfig = home / ".gitconfig"
    gitconfig.write_text(
        "[user]\n\tname = Ambient Owner\n\temail = owner@example.test\n", encoding="utf-8"
    )
    _detach_ambient_identity(monkeypatch, config_file=str(gitconfig))

    shelf = tmp_path / "shelf"
    result = init_shelf(shelf, name="test memory")

    assert result.committed and result.commit
    assert _author_of_head(shelf) == "Ambient Owner <owner@example.test>"
    local = subprocess.run(
        ["git", "-C", str(shelf), "config", "--local", "--get-regexp", "^user\\."],
        capture_output=True,
        text=True,
    )
    assert local.stdout.strip() == ""


def test_shelf_yml_is_memory_profile(tmp_path):
    init_shelf(tmp_path, name="spec shelf", storage="plain")
    text = (tmp_path / "shelf.yml").read_text(encoding="utf-8")
    assert 'spec_version: "0.1"' in text
    assert "mode: single" in text
    assert "profile: memory" in text
    for cat in ("topics", "research", "sessions"):
        assert f"- {cat}" in text


def test_plain_mode_has_no_git(tmp_path):
    result = init_shelf(tmp_path, storage="plain")
    assert not (tmp_path / ".git").exists()
    assert result.committed is False


def test_idempotent_never_overwrites(tmp_path):
    init_shelf(tmp_path, storage="plain")
    (tmp_path / "POLICY.md").write_text("custom policy\n", encoding="utf-8")
    result = init_shelf(tmp_path, storage="plain")
    assert (tmp_path / "POLICY.md").read_text(encoding="utf-8") == "custom policy\n"
    assert "POLICY.md" not in result.created


def test_git_remote_requires_remote(tmp_path):
    with pytest.raises(InitError):
        init_shelf(tmp_path, storage="git-remote")
    with pytest.raises(InitError):
        init_shelf(tmp_path, storage="plain", remote="git@example:x.git")


def test_git_remote_wires_origin(tmp_path):
    remote = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", str(remote)], check=True)
    shelf = tmp_path / "shelf"
    init_shelf(shelf, storage="git-remote", remote=str(remote))
    url = subprocess.run(
        ["git", "-C", str(shelf), "remote", "get-url", "origin"],
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert url == str(remote)


def test_init_then_shelve_then_doctor_healthy(tmp_path):
    # the full bootstrap loop: a fresh shelf accepts an episode and passes doctor
    init_shelf(tmp_path, name="loop")
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.email", "t@t.test"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.name", "t"], check=True)
    shelve(
        tmp_path,
        slug="2026-07-23-first",
        kind="topic",
        digest="The bootstrap chose git-local storage; a remote was rejected by default. Open: none.",
        sections={"Decisions": "git-local by default"},
        date="2026-07-23",
    )
    report = check_shelf(tmp_path)
    assert report.ok, [f.code for f in report.findings]
