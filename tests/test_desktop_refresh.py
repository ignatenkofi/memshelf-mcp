"""`adapters/claude-desktop/refresh.sh` и `build.py --local-version` (#158).

Дефект, ради которого всё это: после мержа в src расширение Desktop
обслуживало старый код, а номер версии в манифесте оставался тем же, так
что переустановка и её отсутствие выглядели одинаково. Проверяется здесь
ровно то, что отличает работающий скрипт от похожего на работающий:

* локальная версия действительно несёт sha коммита и растёт `.dirty` при
  незакоммиченных правках (а не печатает константу);
* `--check` умеет ответить всеми тремя исходами: свежо / ОТСТАЛО /
  НЕ ЗНАЮ — и красный исход достигается порчей одного файла в фикстуре,
  а не подменой вердикта;
* сборка с фальшивым `open` открывает именно собранный uv-бандл с `+g`.
"""

from __future__ import annotations

import importlib.util
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "adapters" / "claude-desktop" / "refresh.sh"
PACKAGE = REPO / "src" / "memshelf_mcp"

pytestmark = pytest.mark.skipif(
    sys.platform.startswith("win") or shutil.which("bash") is None,
    reason="скрипт bash",
)


def _build_module():
    spec = importlib.util.spec_from_file_location(
        "desktop_build", REPO / "adapters" / "claude-desktop" / "build.py"
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    # dataclass под `from __future__ import annotations` ищет свой модуль в
    # sys.modules, чтобы разобрать строковые аннотации; без регистрации падает.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _git_repo(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@t",
    }

    def git(*a):
        subprocess.run(["git", *a], cwd=root, check=True, capture_output=True, env=env)

    git("init", "-q", "-b", "main")
    (root / "src").mkdir()
    (root / "src" / "m.py").write_text("v = 1\n", encoding="utf-8")
    git("add", "-A")
    git("commit", "-q", "-m", "one")
    return root


def _run(*args: str, env: dict[str, str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(SCRIPT), *args],
        capture_output=True,
        text=True,
        env={"PATH": os.environ["PATH"], "HOME": env.get("HOME", "/nonexistent"), **env},
    )


def _fake_extension(root: Path, name: str = "local.mcpb.test.memshelf") -> Path:
    """Расширение как его распаковывает Desktop: manifest.json, src/, .venv/bin/python.

    Интерпретатор — обёртка над текущим python с PYTHONPATH на КОПИЮ пакета
    внутри расширения: проба спрашивает `memshelf_mcp.__file__`, и ответ
    обязан указывать в расширение, а не в чекаут.
    """
    ext = root / name
    shutil.copytree(
        PACKAGE, ext / "src" / "memshelf_mcp", ignore=shutil.ignore_patterns("__pycache__")
    )
    (ext / "manifest.json").write_text('{"version": "0.3.0+gfixture"}\n', encoding="utf-8")
    bindir = ext / ".venv" / "bin"
    bindir.mkdir(parents=True)
    wrapper = bindir / "python"
    wrapper.write_text(
        f'#!/bin/sh\nPYTHONPATH="{ext / "src"}" exec "{sys.executable}" "$@"\n',
        encoding="utf-8",
    )
    wrapper.chmod(0o755)
    return ext


# ------------------------------------------------------- локальная версия


def test_local_segment_names_the_commit_and_notices_dirty(tmp_path):
    build = _build_module()
    repo = _git_repo(tmp_path / "r")
    head = subprocess.run(
        ["git", "rev-parse", "--short=7", "HEAD"], cwd=repo, capture_output=True, text=True
    ).stdout.strip()
    assert build.git_local_segment(repo) == f"+g{head}"
    (repo / "src" / "m.py").write_text("v = 2\n", encoding="utf-8")
    assert build.git_local_segment(repo) == f"+g{head}.dirty", (
        "незакоммиченная правка в src обязана быть видна в версии"
    )


def test_local_segment_is_not_a_release_by_the_freshness_rule(tmp_path):
    from memshelf_mcp.core.freshness import is_release_version

    build = _build_module()
    version = "0.3.0" + build.git_local_segment(_git_repo(tmp_path / "r"))
    assert is_release_version(version) is False


def test_local_segment_refuses_outside_a_checkout(tmp_path):
    build = _build_module()
    with pytest.raises(SystemExit, match="git checkout"):
        build.git_local_segment(tmp_path)


def test_manifest_carries_the_local_version():
    build = _build_module()
    manifest = build.build_manifest(variant="uv", version="0.3.0+gabc1234", target=None)
    assert manifest["version"] == "0.3.0+gabc1234"


# -------------------------------------------------------------- --check


def test_check_without_extension_is_unknown_not_fresh(tmp_path):
    empty = tmp_path / "exts"
    empty.mkdir()
    r = _run("--check", env={"MEMSHELF_EXTENSIONS_DIR": str(empty)})
    assert r.returncode == 2, r.stdout + r.stderr
    assert "НЕ ЗНАЮ" in r.stdout


def test_check_extension_serving_the_checkout_is_fresh(tmp_path):
    _fake_extension(tmp_path / "exts")
    r = _run("--check", env={"MEMSHELF_EXTENSIONS_DIR": str(tmp_path / "exts")})
    assert r.returncode == 0, r.stdout + r.stderr
    assert "свежо" in r.stdout
    assert "0.3.0+gfixture" in r.stdout, "версия из manifest.json показывается человеку"
    assert str(tmp_path / "exts") in r.stdout, "обслуживаемый каталог — внутри расширения"


def test_check_notices_one_changed_line_in_the_extension(tmp_path):
    ext = _fake_extension(tmp_path / "exts")
    target = ext / "src" / "memshelf_mcp" / "__init__.py"
    target.write_text(target.read_text(encoding="utf-8") + "# stale\n", encoding="utf-8")
    r = _run("--check", env={"MEMSHELF_EXTENSIONS_DIR": str(tmp_path / "exts")})
    assert r.returncode == 1, r.stdout + r.stderr
    assert "ОТСТАЛО" in r.stdout
    assert "served-code-differs" in r.stdout


def test_check_without_python_is_unknown(tmp_path):
    r = _run("--check", env={"MEMSHELF_PYTHON": "no-such-python-here"})
    assert r.returncode == 2
    assert "проверить нечем" in r.stderr


# ---------------------------------------------------------- сборка+open


def test_dry_run_prints_the_commands_and_runs_nothing(tmp_path):
    marker = tmp_path / "opened"
    fake_open = tmp_path / "open.sh"
    fake_open.write_text(f'#!/bin/sh\necho "$1" > "{marker}"\n', encoding="utf-8")
    fake_open.chmod(0o755)
    r = _run("--dry-run", env={"MEMSHELF_OPEN": str(fake_open), "MEMSHELF_OUT": str(tmp_path)})
    assert r.returncode == 0, r.stdout + r.stderr
    assert "--local-version" in r.stdout
    assert not marker.exists(), "dry-run ничего не открывает"
    assert not list(tmp_path.glob("*.mcpb")), "dry-run ничего не собирает"


def test_unknown_argument_is_refused():
    r = _run("--yolo", env={})
    assert r.returncode == 2
    assert "неизвестный аргумент" in r.stderr


@pytest.mark.skipif(shutil.which("git") is None, reason="нужен git для локальной версии")
def test_build_opens_the_uv_bundle_with_a_local_version(tmp_path):
    marker = tmp_path / "opened"
    fake_open = tmp_path / "open.sh"
    fake_open.write_text(f'#!/bin/sh\necho "$1" > "{marker}"\n', encoding="utf-8")
    fake_open.chmod(0o755)
    r = _run(env={"MEMSHELF_OPEN": str(fake_open), "MEMSHELF_OUT": str(tmp_path / "dist")})
    assert r.returncode == 0, r.stdout + r.stderr
    opened = marker.read_text(encoding="utf-8").strip()
    assert re.search(r"memshelf-\d+\.\d+\.\d+\+g[0-9a-f]{7}(\.dirty)?-uv\.mcpb$", opened), opened
    assert Path(opened).is_file(), "открыт файл, которого нет"
    assert "--check" in r.stdout, "следующий шаг назван"
