"""Детектор инстансов сервера, оставшихся без клиента (#115).

Проверяется на синтетических процессах: «живой» — тот, чей дескриптор 1
ведёт в лог хоста, «осиротевший» — тот, у кого он в /dev/null. Признак
взят из замера в issue, а не придуман.

Отдельный кейс — на ложное срабатывание: `pgrep -f` матчит полную
командную строку, поэтому шелл, в котором набран однострочник с шаблоном,
попадает в улов сам. Версия детектора из тела issue этого не отсекала, и
на первом же прогоне в контейнере без серверов нашла «двух осиротевших».
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "adapters" / "claude-desktop" / "find-orphan-servers.sh"
MARKER = "fixture-memshelf-server"

pytestmark = pytest.mark.skipif(
    sys.platform.startswith("win") or shutil.which("lsof") is None or shutil.which("pgrep") is None,
    reason="нужны POSIX-инструменты lsof и pgrep",
)


def _spawn(target, marker: str = MARKER):
    """Процесс с маркером в командной строке и заданным stdout."""
    return subprocess.Popen(
        [sys.executable, "-c", f"# {marker}\nimport time; time.sleep(30)"],
        stdout=target,
        stderr=subprocess.DEVNULL,
    )


def _run() -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(SCRIPT)],
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "ORPHAN_PATTERN": MARKER,
             "ORPHAN_LOG_HINT": "mcp-server-memshelf.log"},
    )


def test_script_is_executable():
    assert SCRIPT.is_file(), f"нет {SCRIPT}"
    assert SCRIPT.stat().st_mode & 0o111, "детектор без бита +x — прямой вызов даст permission denied"


def test_no_processes_is_not_clean():
    """«Серверов не запущено» и «серверов без клиента нет» — разные ответы."""
    res = _run()
    assert res.returncode == 2, f"ожидался rc 2, получен {res.returncode}: {res.stdout}{res.stderr}"
    assert "проверять нечем" in res.stderr


def test_orphan_found(tmp_path):
    """Инстанс с stdout в /dev/null назван осиротевшим, с логом — живым."""
    log = (tmp_path / "mcp-server-memshelf.log").open("w")
    live = _spawn(log)
    with open("/dev/null", "w") as devnull:
        orphan = _spawn(devnull)
    try:
        time.sleep(0.5)
        res = _run()
        assert res.returncode == 1, f"осиротевший не найден: rc={res.returncode}\n{res.stdout}"
        assert f"orphan: {orphan.pid}" in res.stdout, res.stdout
        assert f"live:   {live.pid}" in res.stdout, res.stdout
        assert f"kill -TERM -" in res.stdout, "команда снятия не напечатана"
    finally:
        live.kill(); orphan.kill(); log.close()


def test_only_live_is_clean(tmp_path):
    """Контроль, обязанный молчать: один живой инстанс — не находка."""
    log = (tmp_path / "mcp-server-memshelf.log").open("w")
    live = _spawn(log)
    try:
        time.sleep(0.5)
        res = _run()
        assert res.returncode == 0, f"живой инстанс принят за осиротевшего:\n{res.stdout}"
        assert "осиротевших нет" in res.stdout
    finally:
        live.kill(); log.close()


def test_shell_with_pattern_in_argv_is_not_counted():
    """Ложное срабатывание, на котором детектор из тела issue и падал.

    Шелл, в командной строке которого встречается шаблон, — не сервер.
    Отсекается по `comm`: имя исполняемого файла обязано быть
    интерпретатором, а не bash.
    """
    # `; :` в конце обязателен: без него bash делает exec-оптимизацию —
    # единственная внешняя команда заменяет процесс, маркер уходит из argv
    # вместе с ним, и фикстура перестаёт воспроизводить дефект. Поймано
    # мутацией: снятие отсева по `comm` тест не покрасило.
    noise = subprocess.Popen(["bash", "-c", f"# {MARKER}\nsleep 30; :"],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        time.sleep(0.5)
        res = _run()
        assert res.returncode == 2, (
            "bash с шаблоном в argv засчитан за инстанс сервера — "
            f"rc={res.returncode}\n{res.stdout}{res.stderr}"
        )
    finally:
        noise.kill()
