#!/usr/bin/env bash
# Переустановить расширение memshelf в Claude Desktop из ЭТОГО чекаута (#158).
#
# ЗАЧЕМ. После каждого мержа в src расширение Desktop обслуживает старый код,
# и doctor честно говорит `served-code-differs`. Лечение до этого файла было
# ручным и жило в голове: собрать uv-бандл, открыть его, нажать Install,
# перезапустить Desktop, свериться. Номер версии в манифесте при этом не
# двигался (0.3.0 до и после), так что «переустановил» и «не переустановил»
# выглядели одинаково — тот же класс дефекта, что в #125.
#
# ЧТО ДЕЛАЕТ.
#   refresh.sh            собрать uv-бандл с локальной версией 0.3.0+g<sha>
#                         (build.py --local-version), открыть его — Desktop
#                         покажет диалог Install, — и напечатать следующий шаг;
#   refresh.sh --check    ПОСЛЕ перезапуска Desktop: обслуживает ли расширение
#                         код этого чекаута (по хешу каталога, не по номеру);
#   refresh.sh --dry-run  напечатать команды, ничего не делать.
#
# Нажать Install и перезапустить Desktop скрипт не умеет и не пытается:
# у Desktop нет CLI для этого, а имитировать клик — способ однажды
# поставить не то. Поэтому ход двухтактный: сборка+open, потом --check.
#
# КОДЫ ВОЗВРАТА (конвенция портфеля):
#   0 — собрано и открыто; для --check: расширение обслуживает код чекаута;
#   1 — --check: расширение ОТСТАЛО (served-code-differs);
#   2 — ПРОВЕРИТЬ НЕЧЕМ или не собралось: нет расширения, оно ещё не
#       запускалось (нет .venv), нет python3, сборка упала. Отдельный код,
#       а не «свежо»: «не нашёл» и «совпадает» — разные ответы.
#
# Переменные (для фикстур и нестандартных установок):
#   MEMSHELF_PYTHON          интерпретатор для build.py и проверки (python3)
#   MEMSHELF_OPEN            команда вместо `open` (в тестах — заглушка)
#   MEMSHELF_EXTENSIONS_DIR  каталог расширений Desktop (см. freshness.py)
#   MEMSHELF_OUT             куда класть бандл (dist/ чекаута)
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
PY="${MEMSHELF_PYTHON:-python3}"
OPEN="${MEMSHELF_OPEN:-open}"
OUT="${MEMSHELF_OUT:-$REPO/dist}"

MODE=build
for arg in "$@"; do
    case "$arg" in
        --check) MODE=check ;;
        --dry-run) MODE=dry-run ;;
        -h|--help) sed -n '2,40p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "refresh.sh: неизвестный аргумент: $arg" >&2; exit 2 ;;
    esac
done

command -v "$PY" >/dev/null 2>&1 || {
    echo "refresh.sh: нет интерпретатора '$PY' (MEMSHELF_PYTHON) — проверить нечем" >&2
    exit 2
}

# ---------------------------------------------------------------- --check
check() {
    # Проба — той же машинерией, что `memshelf freshness`: спрашиваем у
    # интерпретатора расширения, какой каталог он импортирует, и сравниваем
    # хеш каталога с хешем src/memshelf_mcp чекаута. Номер версии в
    # манифесте печатается для человека, но вердикт на нём не строится.
    PYTHONPATH="$REPO/src" "$PY" - "$REPO" <<'PYEOF'
import json
import sys
from pathlib import Path

from memshelf_mcp.core import freshness

repo = Path(sys.argv[1])
reference = freshness.package_sha(repo / "src" / "memshelf_mcp")
print(f"эталон (чекаут) {repo / 'src' / 'memshelf_mcp'}: {reference[:12]}")

extensions = [c for c in freshness.discover_consumers() if c[1] == "claude-extension"]
if not extensions:
    print(
        "расширение memshelf в Claude Desktop не найдено или ещё не запускалось "
        "(нет .venv/bin/python) — НЕ ЗНАЮ, а не «свежо». Перезапустите Desktop "
        "после Install и повторите --check."
    )
    sys.exit(2)

worst = 0
for name, kind, python in extensions:
    extension_dir = python.parents[2]  # <ext>/.venv/bin/python
    manifest = extension_dir / "manifest.json"
    shown = "?"
    if manifest.is_file():
        try:
            shown = json.loads(manifest.read_text(encoding="utf-8")).get("version", "?")
        except ValueError:
            shown = "? (manifest.json не разобрался)"
    report = freshness.probe_consumer(name, kind, python, reference_sha=reference)
    differs = any(f.rule == "served-code-differs" for f in report.findings)
    if report.served_sha is freshness.UNKNOWN:
        verdict, code = "НЕ ЗНАЮ", 2
    elif differs:
        verdict, code = "ОТСТАЛО", 1
    else:
        verdict, code = "свежо: обслуживает код чекаута", 0
    print(f"\n{name} — {verdict}")
    print(f"  версия в Desktop: {shown}")
    print(f"  обслуживает:      {report.served_dir}")
    sha = report.served_sha
    print(f"  хеш кода:         {sha if sha is freshness.UNKNOWN else sha[:12]}")
    for finding in report.findings:
        print(f"  [{finding.severity}] {finding.rule}: {finding.detail}")
    worst = max(worst, code)
sys.exit(worst)
PYEOF
}

# ------------------------------------------------------------ build+open
build_and_open() {
    local dry=$1 log bundle
    local build_cmd=("$PY" "$HERE/build.py" --variant uv --local-version --out "$OUT")
    if [ "$dry" = yes ]; then
        printf 'would run:'; printf ' %q' "${build_cmd[@]}"; printf '\n'
        printf 'would run: %q %s\n' "$OPEN" "$OUT/memshelf-<version>+g<sha>-uv.mcpb"
        printf 'then: Install в диалоге Desktop → перезапуск Desktop → %q --check\n' "$0"
        return 0
    fi
    log="$(mktemp "${TMPDIR:-/tmp}/memshelf-refresh.XXXXXX")"
    trap 'rm -f "${log:-}"' EXIT
    if ! "${build_cmd[@]}" | tee "$log"; then
        echo "refresh.sh: сборка не удалась — см. вывод выше" >&2
        return 2
    fi
    # build.py печатает пути собранных бандлов последними строками; берём uv.
    bundle="$(awk '/-uv\.mcpb$/ { p = $NF } END { print p }' "$log")"
    if [ -z "$bundle" ] || [ ! -f "$bundle" ]; then
        echo "refresh.sh: build.py не назвал uv-бандл (ожидал строку *-uv.mcpb) — не открываю" >&2
        return 2
    fi
    case "$bundle" in
        *+g*) ;;
        *) echo "refresh.sh: в имени бандла нет локальной версии (+g<sha>): $bundle" >&2; return 2 ;;
    esac
    echo
    echo "открываю $bundle"
    "$OPEN" "$bundle"
    cat <<EON

Дальше руками, у Desktop нет CLI для этого:
  1. в диалоге Claude Desktop нажать Install (версия в диалоге — $(basename "$bundle" | sed 's/^memshelf-//; s/-uv\.mcpb$//'));
  2. перезапустить Claude Desktop;
  3. проверить, что обслуживается код чекаута:
     $0 --check
EON
}

case "$MODE" in
    check) check ;;
    dry-run) build_and_open yes ;;
    build) build_and_open no ;;
esac
