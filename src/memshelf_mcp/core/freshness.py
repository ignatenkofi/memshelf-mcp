"""Свежесть потребителей: доехал ли смерженный фикс до кода, который
отвечает на вызовы.

Дважды за один день правка была смержена и не действовала: сначала полку
обслуживал старый глобальный бинарь из pipx, потом — распакованное
расширение Claude Desktop, отставшее от main на 121 строку. Оба раза это
обнаружилось случайно, при разборе постороннего симптома, и оба раза
номер версии молчал: у расширения и у пакета он одинаковый и не двигался
с момента сборки.

Разрывов три, и каждый обрывает путь от мержа до потребителя в своём
месте:

* **(a) смержено, но не выпущено.** `main` впереди последнего тега.
  Потребитель тянет с индекса, там прежний номер — и код с фиксом и код
  без фикса неразличимы (docshelf-mcp#99).
* **(b) выпущено, но не установлено.** У потребителя версия старее
  выпущенной, либо вообще не релиз: локальный сегмент PEP 440
  (`0.4.0+gd7b4e77`) — это чья-то ручная сборка.
* **(c) установлено, но не обслуживается.** Каталог, который импортирует
  живой процесс, не совпадает с объявленным (memshelf-mcp#125).

Проба обязана иметь **третий исход**. Потребитель, которого не удалось
опросить, не «свежий» и не «отставший» — он `UNKNOWN`; репозиторий без
единого тега — тоже. Класс дефекта, ради которого всё и затевалось:
отсутствие сигнала неотличимо от нормы, поэтому «не знаю» здесь
называется вслух, а не сворачивается в «ок».

Модуль ничего не чинит и ничего не пишет: только читает и рассказывает.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


class _Unknown:
    """Третий исход. Отдельный тип, чтобы `is UNKNOWN` не путался с None."""

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __repr__(self) -> str:
        return "UNKNOWN"

    def __bool__(self) -> bool:
        raise TypeError(
            "UNKNOWN не приводится к bool: «не знаю» нельзя молча прочитать как «всё хорошо»"
        )


UNKNOWN = _Unknown()

SKIP_DIRS = {"__pycache__", ".git", ".mypy_cache", ".ruff_cache", ".pytest_cache"}


@dataclass(frozen=True)
class Finding:
    rule: str
    severity: str
    detail: str


# ------------------------------------------------------------- (c) код


def package_sha(package_dir: Path) -> str:
    """Хеш обслуживаемого кода: только исходники, без следов компиляции.

    Считается по парам (относительный путь, содержимое) в отсортированном
    порядке — так добавленный или пропавший файл меняет хеш так же
    заметно, как правка внутри файла. `core/splits.py` у расширения
    отсутствовал целиком, и именно это должно быть видно.
    """
    h = hashlib.sha256()
    root = Path(package_dir)
    files = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
        for name in sorted(filenames):
            if name.endswith(".py"):
                files.append(Path(dirpath) / name)
    for path in sorted(files, key=lambda p: p.relative_to(root).as_posix()):
        h.update(path.relative_to(root).as_posix().encode("utf-8"))
        h.update(b"\0")
        h.update(path.read_bytes())
        h.update(b"\0")
    return h.hexdigest()


# --------------------------------------------------------- (b) версия


def is_release_version(version: str) -> bool:
    """Выпущенная версия или чья-то сборка.

    Локальный сегмент PEP 440 (`+…`) и `.dev` ставятся именно для того,
    чтобы сборка не притворялась релизом. Читаем это буквально.
    """
    return "+" not in version and ".dev" not in version


# ------------------------------------------------------------ (a) тег


def unreleased_commits(repo: Path, subdir: str = "src") -> list[str] | _Unknown:
    """Коммиты в `subdir` после последнего тега.

    Ни одного тега — `UNKNOWN`: репозиторий, который никогда не
    выпускался, нельзя записать ни в «всё выпущено», ни в «есть
    невыпущенное».
    """
    tag = _git(repo, "describe", "--tags", "--abbrev=0")
    if tag is None:
        return UNKNOWN
    out = _git(repo, "log", "--oneline", f"{tag}..HEAD", "--", subdir)
    if out is None:
        return UNKNOWN
    return [line for line in out.splitlines() if line.strip()]


def _git(repo: Path, *args: str) -> str | None:
    try:
        r = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout.strip() if r.returncode == 0 else None


# ------------------------------------------------------------- потребитель

_PROBE = r"""
import json, sys
out = {}
try:
    import memshelf_mcp
    out["served_dir"] = str(__import__("pathlib").Path(memshelf_mcp.__file__).parent)
    out["declared_version"] = getattr(memshelf_mcp, "__version__", None)
except Exception as e:
    out["error"] = f"{type(e).__name__}: {e}"
dists = {}
import importlib.metadata as md
for name in ("memshelf-mcp", "docshelf-mcp"):
    try:
        dists[name] = md.version(name)
    except Exception:
        dists[name] = None
out["dists"] = dists
sys.stdout.write(json.dumps(out))
"""


@dataclass
class ConsumerReport:
    name: str
    kind: str
    python: str
    served_dir: str | _Unknown = UNKNOWN
    served_sha: str | _Unknown = UNKNOWN
    declared_version: str | _Unknown = UNKNOWN
    dists: dict[str, Any] = field(default_factory=dict)
    findings: list[Finding] = field(default_factory=list)

    @property
    def is_fresh(self) -> bool | _Unknown:
        """Не опрошен — `UNKNOWN`. Иначе: есть ли находки уровня warning."""
        if self.served_sha is UNKNOWN:
            return UNKNOWN
        return not any(f.severity == "warning" for f in self.findings)


def probe_consumer(
    name: str,
    kind: str,
    python: Path,
    reference_sha: str | None = None,
) -> ConsumerReport:
    """Опросить потребителя его же интерпретатором.

    Спрашиваем не «какая версия объявлена», а «какой каталог ты
    импортируешь» — сегодня оба раза врал именно номер, а каталог не мог.
    """
    rep = ConsumerReport(name=name, kind=kind, python=str(python))
    try:
        r = subprocess.run(
            [str(python), "-c", _PROBE],
            capture_output=True,
            text=True,
            timeout=120,
        )
    except (OSError, subprocess.SubprocessError) as e:
        rep.findings.append(Finding("consumer-unprobed", "unknown", f"{type(e).__name__}: {e}"))
        return rep
    if r.returncode != 0 or not r.stdout.strip():
        rep.findings.append(
            Finding(
                "consumer-unprobed",
                "unknown",
                (r.stderr.strip() or "интерпретатор не ответил")[:400],
            )
        )
        return rep
    try:
        data = json.loads(r.stdout)
    except ValueError:
        rep.findings.append(Finding("consumer-unprobed", "unknown", "ответ не разобрался как JSON"))
        return rep
    if "error" in data:
        rep.findings.append(Finding("consumer-unprobed", "unknown", data["error"]))
        return rep

    rep.served_dir = data["served_dir"]
    rep.declared_version = data.get("declared_version") or UNKNOWN
    rep.dists = data.get("dists", {})
    served = Path(rep.served_dir)
    rep.served_sha = package_sha(served) if served.is_dir() else UNKNOWN
    if rep.served_sha is UNKNOWN:
        rep.findings.append(
            Finding(
                "consumer-unprobed",
                "unknown",
                f"каталог не читается: {rep.served_dir}",
            )
        )
        return rep

    for dist, version in rep.dists.items():
        if version and not is_release_version(version):
            rep.findings.append(
                Finding(
                    "not-a-release",
                    "warning",
                    f"{dist} {version}: локальная сборка, а не выпущенная версия",
                )
            )
    if reference_sha is not None and rep.served_sha != reference_sha:
        rep.findings.append(
            Finding(
                "served-code-differs",
                "warning",
                f"обслуживается {rep.served_sha[:12]}, ожидалось {reference_sha[:12]}",
            )
        )
    return rep


# ------------------------------------------------------------------ хост


def discover_consumers() -> list[tuple[str, str, Path]]:
    """Известные потребители на этой машине: (имя, вид, интерпретатор).

    Список явный и правится руками — как allowlist Zealot и по той же
    причине: угадывание потребителей даёт молчаливые пропуски, а пропуск
    здесь и есть весь дефект.
    """
    found: list[tuple[str, str, Path]] = []
    home = Path.home()
    pipx = home / "Library/Application Support/pipx/venvs/memshelf-mcp/bin/python"
    if not pipx.exists():
        pipx = home / ".local/pipx/venvs/memshelf-mcp/bin/python"
    if pipx.exists():
        found.append(("pipx memshelf-mcp", "pipx", pipx))
    exts = home / "Library/Application Support/Claude/Claude Extensions"
    if exts.is_dir():
        for d in sorted(exts.glob("*memshelf*")):
            py = d / ".venv/bin/python"
            if py.exists():
                found.append((d.name, "claude-extension", py))
    return found


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    repo = Path(argv[0]) if argv else Path.cwd()

    print(f"репозиторий: {repo}")
    unreleased = unreleased_commits(repo)
    if unreleased is UNKNOWN:
        print("  (a) невыпущенное: НЕ ЗНАЮ — тега нет или это не репозиторий")
    elif unreleased:
        print(f"  (a) невыпущенное: {len(unreleased)} коммит(ов) после тега")
        for line in unreleased:
            print(f"      {line}")
    else:
        print("  (a) невыпущенное: нет")

    reference = None
    pkg = repo / "src" / "memshelf_mcp"
    if pkg.is_dir():
        reference = package_sha(pkg)
        print(f"  эталон (рабочее дерево) {pkg}: {reference[:12]}")

    consumers = discover_consumers()
    if not consumers:
        print("потребителей не найдено — это НЕ ЗНАЮ, а не «все свежие»")
        return 2

    worst = 0
    for name, kind, python in consumers:
        rep = probe_consumer(name, kind, python, reference_sha=reference)
        fresh = rep.is_fresh
        mark = "НЕ ЗНАЮ" if fresh is UNKNOWN else ("свеж" if fresh else "ОТСТАЛ")
        print(f"\n{name} [{kind}] — {mark}")
        print(f"  интерпретатор: {rep.python}")
        print(f"  обслуживает:   {rep.served_dir}")
        sha = rep.served_sha
        print(f"  хеш кода:      {sha if sha is UNKNOWN else sha[:12]}")
        print(f"  объявляет:     {rep.declared_version}")
        for dist, version in rep.dists.items():
            print(f"  {dist}: {version}")
        for f in rep.findings:
            print(f"  [{f.severity}] {f.rule}: {f.detail}")
        if fresh is UNKNOWN:
            worst = max(worst, 2)
        elif not fresh:
            worst = max(worst, 1)
    return worst


if __name__ == "__main__":
    raise SystemExit(main())
