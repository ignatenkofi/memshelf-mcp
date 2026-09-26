# 2026-09-26-night-shift-roadmap-epics-docs-to-fact

---
id: 2026-09-26-night-shift-roadmap-epics-docs-to-fact
kind: session
span: 2026-09-26
date: 2026-09-26
display_title: "memshelf-mcp: ночная смена 26→27.09 — эпики вех #164–#167, ROADMAP к факту (PR #168), #158 к закрытию владельцем"
description: "Эпики вех #164–#167 по ROADMAP, draft-PR #168 привёл ROADMAP к факту, #158 закрывает владелец; doctor: PyPI vs клон."
tags: [night-shift, roadmap, docs]
approx_tokens: 30000
approx_tokens_source: estimate
mode: live
notes: "approx_tokens — оценка доли смены, относящейся к memshelf-mcp (сабагент полки); не замер токенизатором."
---

## Digest
Ночная смена 26→27.09 по memshelf-mcp. Вехи docs/ROADMAP.md заведены issue-эпиками #164–#167 с меткой roadmap: M0 закрыт completed, M1 — P1, M2 — P2, M3 — P3. Draft-PR #168 привязал эпики в ROADMAP и добавил строку статуса M1: объём отгружен, exit-условие догфуда без записанного вердикта; CI 5/5. Issue #158 размечен enhancement + P2, комментарий доказывает закрытие мержем PR #160; закрывает владелец — смена issues не закрывает. Наблюдение: memshelf doctor из PyPI-установки при клоне memshelf-mcp рядом даёт error served-code-differs, тот же doctor из клона — 0 errors; это не derived-stale. Эпизод смены на главной полке — PR main-memshelf#207; в скилл ночной смены записано правило метки night-shift. Открыто: закрывать ли M1 (#165); docs/chat-projects.md после OQ6 — резерв.

## Decisions
- **Вехи живут в `docs/ROADMAP.md`, задачи — в issues с метками issue-kit, дизайн до кода — в arch-designs-shelf; эпик в issues — трекинг вехи, не замена роадмапа.** Ответ на вопрос владельца «где живут задания по улучшениям и роадмап». По одному эпику на веху: #164 M0 (closed/completed, P3), #165 M1 (P1, текущий период), #166 M2 (P2, следующая), #167 M3 (P3). Тело эпика — контекст, scope с чекбоксами по факту, exit-критерий, строка `Source: docs/ROADMAP.md §Mx @ c7a70f9`. Sub-issues к эпикам этого репо не привязаны: единственный открытый не-эпик — #158, к вехам ROADMAP не относится.
- **ROADMAP приводится к факту доковым draft-PR, а не правкой на `main`** — PR #168 (`docs/ROADMAP.md`, +8/−0): строка `Epic: #16x` под каждой вехой и явная строка статуса M1. Отклонено: закрыть M1 сменой или дописать маркер «complete» в заголовок M1 — объём отгружен целиком (0.1.0, 2026-07-25, плагин с ним), но exit-условие «две недели догфуда на двух полках без ручной починки производных» в репо не зафиксировано, CHANGELOG 0.2.0 записывает одну ручную починку (#56). Вопрос «закрывать ли M1 и переносить P1 на M2» — комментарием владельцу в #165.
- **Смена issues не закрывает** — #158: комментарий с доказательством по коду `main` `c7a70f9` (после мержа PR #160): `adapters/claude-desktop/refresh.sh` и `build.py --local-version` закрывают предложения 1–2, предложение 3 (сигнал merged-but-unreleased в doctor) — осознанное «нет» по решению #125. Закрытие оставлено владельцу; issue размечен `enhancement` + `P2`.
- **`served-code-differs` от PyPI-установки при клоне рядом — не дефект полки и не `derived-stale`.** На главной полке `uvx --from memshelf-mcp memshelf doctor` (0.3.0, package_sha `06603ed`) даёт 1 error `served-code-differs` («the code answering this call … is not the checkout at …/memshelf-mcp/src/memshelf_mcp»), а `uvx --from <клон memshelf-mcp> memshelf doctor` — 0 errors при тех же двух warning'ах `stale-index` и `no-ledger-row`. Правило: полку, рядом с которой лежит клон, проверять кодом клона; `derived-stale` там нет — бот main-memshelf рисует.
- **Правило метки `night-shift`** — задачу, взятую по собственному отбору или по прямой просьбе, смена сама помечает `night-shift` при старте и снимает по готовности draft-PR (решение владельца в чате смены); записано в скилл ночной смены на главной полке (коммит `1c70c72`).
- **Эпизод в проектную полку — с этой же ветки от `origin/main`, производные вторым коммитом.** Бота рендера в memshelf-mcp нет: `shelf-pr-guard.yml` проверяет на PR `doctor` и `rebuild --check`, то есть отставание производных на ветке — дефект, а не норма. Перед `doctor` shallow-клон углублён (`git fetch --deepen=200 origin main`): в shallow-клоне `git log -1 -- ledger.tsv` отвечает граничным коммитом, и вердикт `derived-stale` ничего не стоит (memshelf-mcp#162).

## Timeline
1. ~20:5x UTC — вопрос владельца, где хранятся задания по улучшениям и роадмап; ответ по обходу репо: вехи — `docs/ROADMAP.md`, задачи — issues с метками issue-kit, дизайн — arch-designs-shelf; `docs/ROADMAP.md` memshelf-mcp прочитан на `main` `c7a70f9`. Находка при разборе: заголовок M1 без маркера состояния при отгруженном объёме (M0 подписан «complete»).
2. 21:14 UTC — эпики #164–#167 опубликованы REST-скриптом из сессии главной полки: метка `roadmap`, приоритеты P3/P1/P2/P3, #164 закрыт `completed`; sub-issues в этом репо не привязаны (нечего).
3. 21:18 UTC — #158: метки `enhancement` + `P2`, комментарий-доказательство «PR #160 закрыл предложения 1–2, 3 — осознанное нет»; закрытие — владельцу.
4. 21:42 UTC — draft-PR #168 с ветки `claude/night-2026-09-26-memshelf-roadmap-epics` (head `909b1ca`, база `c7a70f9`); комментарий в #165 с вопросом о закрытии M1; check-runs 5/5 success: lint · tests (py3.10, py3.13), conformance, desktop bundle, pipeline / light.
5. Наблюдение на главной полке: `memshelf doctor` из PyPI-установки — 1 error `served-code-differs`, из клона — 0 errors; те же два warning'а в обоих случаях.
6. Эпизод смены на главной полке `2026-09-26-night-shift-milestone-epics-testflight-wave` — PR main-memshelf#207; правило метки `night-shift` в скилл (`1c70c72`).
7. Этот эпизод (сабагент смены): ветка `claude/night-2026-09-26-memshelf-mcp-shelf-episode` от `origin/main` `c7a70f9`, `--deepen=200`, `doctor` до — 0 errors / 0 warnings / 1 unknown `freshness-unknown`; `shelve --no-sync` инструментом из клона; `rebuild` и второй коммит производных (бота нет); draft-PR на `main`.

## Artifacts
- **Issues:** #164 (M0, closed/completed), #165 (M1, P1), #166 (M2, P2), #167 (M3, P3) — метка `roadmap`, созданы 2026-09-26T21:14Z; #158 — `enhancement`, `P2`, комментарий 2026-09-26T21:18Z; комментарий в #165 2026-09-26T21:42Z.
- **PR #168** (draft, `docs(ROADMAP): link milestone epics, state M1 exit-clause status by fact`): `docs/ROADMAP.md` +8/−0, `mergeable_state: clean`, 5 check-runs success, «Part of #165».
- **Главная полка:** PR main-memshelf#207 (ветка `feature/great-bardeen-5vfov4`), эпизод `2026-09-26-night-shift-milestone-epics-testflight-wave`, коммит правила метки `1c70c72`.
- **Команды, которые сработали:** `git fetch --deepen=200 origin main` перед doctor; `uvx --from <клон> memshelf doctor --shelf shelf --derived-stale-hours 6`; `uvx --from <клон> memshelf lint-digest --strict --digest-file digest.txt`; `uvx --from <клон> memshelf shelve --shelf shelf --kind session … --no-sync`; `uvx --from <клон> memshelf rebuild --shelf shelf`; проверка дублей реестра `cut -f2 shelf/ledger.tsv | tail -n +2 | sort | uniq -d`.
- **Точки в коде:** `src/memshelf_mcp/core/doctor.py` — `_check_served_freshness` и исход `freshness-unknown` (ищет чекаут `memshelf-mcp` рядом с полкой либо `MEMSHELF_CHECKOUT`); `.github/workflows/shelf-pr-guard.yml` — гард полки без бота.

## Open threads
- **Закрывать ли M1 и переносить ли P1 на M2 (#166)** — вопрос владельцу в #165; draft-PR #168 ждёт ревью и мержа.
- **#158** — закрытие владельцем после прогона `refresh.sh` на маке (комментарий 25.09 в issue); смена не закрывает.
- **`docs/chat-projects.md`** — единый end-to-end walkthrough chat-project surface (пункт scope M3, #167) после OQ6 — резерв следующей смены.
- **Sub-issues к эпикам** memshelf-mcp пусты: при появлении новых issues по вехам привязывать их к #165–#167.
- **`freshness-unknown` у doctor на полке внутри чекаута:** полка `shelf/` лежит внутри клона memshelf-mcp, а `doctor` ищет чекаут рядом с полкой (`<полка>/../memshelf-mcp`) либо по `MEMSHELF_CHECKOUT`, поэтому отвечает «не знаю» о свежести обслуживаемого кода даже при запуске из этого же клона. Косметика уровня unknown, не error; issue не заведён — кандидат: смотреть и на родителя полки.
- **`episode-unpushed` на этой полке не показывается — и это не ошибка.** `doctor` считает его только по эпизодам без строки в `ledger.tsv`; после `rebuild` строка есть, и ни `episode-unpushed`, ни `derived-stale` для эпизода не вычисляются — вердикт на ветке до и после push один: 0 errors / 0 warnings / 1 unknown. Соответствие производных эпизоду на PR проверяет `shelf-pr-guard.yml` через `rebuild --check`, а не `doctor`.
