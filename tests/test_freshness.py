"""Проба свежести потребителей: смерженный фикс, не доехавший до кода,
который отвечает на вызовы, обязан обнаруживаться проверкой.

Три разрыва, каждый из которых уже стоил дня работы:
  (a) смержено, но не выпущено — main впереди последнего тега;
  (b) выпущено, но не установлено — у потребителя версия старее релиза;
  (c) установлено, но не обслуживается — код, который импортирует живой
      процесс, не тот, что объявлен.

Проба обязана иметь третий исход: потребителя, которого не удалось
опросить, нельзя записывать ни в «свежий», ни в «отставший».
"""

import subprocess
from pathlib import Path

import pytest

from memshelf_mcp.core.freshness import (
    UNKNOWN,
    ConsumerReport,
    is_release_version,
    package_sha,
    probe_consumer,
    unreleased_commits,
)

# ---------------------------------------------------------------- (c)


def _pkg(root: Path, body: str) -> Path:
    d = root / "pkg"
    (d / "core").mkdir(parents=True, exist_ok=True)
    (d / "__init__.py").write_text("__version__ = '1.0.0'\n", encoding="utf-8")
    (d / "core" / "mod.py").write_text(body, encoding="utf-8")
    return d


def test_package_sha_ignores_pycache_and_is_stable(tmp_path):
    a = _pkg(tmp_path / "a", "x = 1\n")
    b = _pkg(tmp_path / "b", "x = 1\n")
    (a / "core" / "__pycache__").mkdir()
    (a / "core" / "__pycache__" / "mod.cpython-314.pyc").write_bytes(b"\x00garbage")
    assert package_sha(a) == package_sha(b)


def test_package_sha_notices_a_missing_file(tmp_path):
    """core/splits.py отсутствовал у расширения целиком — ровно этот случай."""
    a = _pkg(tmp_path / "a", "x = 1\n")
    b = _pkg(tmp_path / "b", "x = 1\n")
    (b / "core" / "splits.py").write_text("def prune(): ...\n", encoding="utf-8")
    assert package_sha(a) != package_sha(b)


# ---------------------------------------------------------------- (b)


@pytest.mark.parametrize(
    "version,expected",
    [
        ("0.4.1", True),
        ("0.4.0", True),
        ("0.4.0+gd7b4e77", False),  # ручная сборка, а не релиз
        ("0.4.1.dev3+g1234567", False),
    ],
)
def test_local_segment_is_not_a_release(version, expected):
    assert is_release_version(version) is expected


# ---------------------------------------------------------------- (a)


def _repo(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    env = {
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@t",
    }

    def git(*a):
        subprocess.run(
            ["git", *a],
            cwd=root,
            check=True,
            capture_output=True,
            env={**__import__("os").environ, **env},
        )

    git("init", "-b", "main")
    (root / "src").mkdir()
    (root / "src" / "m.py").write_text("v = 1\n", encoding="utf-8")
    git("add", "-A")
    git("commit", "-m", "one")
    git("tag", "v0.4.0")
    return root


def test_no_unreleased_commits_right_after_a_tag(tmp_path):
    r = _repo(tmp_path / "r")
    assert unreleased_commits(r, "src") == []


def test_a_merged_fix_that_was_never_released_is_named(tmp_path):
    r = _repo(tmp_path / "r")
    (r / "src" / "m.py").write_text("v = 2\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=r, check=True, capture_output=True)
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-m", "fix: the thing"],
        cwd=r,
        check=True,
        capture_output=True,
    )
    out = unreleased_commits(r, "src")
    assert len(out) == 1
    assert "fix: the thing" in out[0]


def test_unreleased_commits_is_unknown_without_any_tag(tmp_path):
    """Ни одного тега — это «не знаю», а не «всё выпущено»."""
    r = tmp_path / "untagged"
    r.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=r, check=True, capture_output=True)
    assert unreleased_commits(r, "src") is UNKNOWN


# ------------------------------------------------- третий исход пробы


def test_unprobeable_consumer_is_unknown_not_fresh(tmp_path):
    """Потребителя, которого не опросить, нельзя записать в свежие."""
    rep = probe_consumer(name="ghost", kind="pipx", python=tmp_path / "nope" / "bin" / "python")
    assert isinstance(rep, ConsumerReport)
    assert rep.served_sha is UNKNOWN
    assert [f.rule for f in rep.findings] == ["consumer-unprobed"]
    assert rep.is_fresh is UNKNOWN


def test_a_probeable_consumer_reports_what_it_serves(tmp_path):
    import sys

    rep = probe_consumer(name="self", kind="pipx", python=Path(sys.executable))
    assert rep.served_dir is not UNKNOWN
    assert rep.served_sha is not UNKNOWN
    assert rep.declared_version is not UNKNOWN
