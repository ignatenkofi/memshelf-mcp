import subprocess

import pytest

pytest.importorskip("docshelf_mcp")

from memshelf_mcp.core.doctor import check_shelf  # noqa: E402
from memshelf_mcp.core.init import MEMORY_PREAMBLE, InitError, init_shelf  # noqa: E402
from memshelf_mcp.core.shelve import shelve  # noqa: E402


def test_git_local_default_creates_everything(tmp_path):
    result = init_shelf(tmp_path, name="test memory")
    assert result.storage == "git-local"
    for rel in ("INDEX.md", "POLICY.md", "ledger.tsv", "shelf.yml", ".docshelf.json"):
        assert (tmp_path / rel).is_file(), rel
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
