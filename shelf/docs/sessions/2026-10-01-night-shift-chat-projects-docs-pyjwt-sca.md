# 2026-10-01-night-shift-chat-projects-docs-pyjwt-sca

---
id: 2026-10-01-night-shift-chat-projects-docs-pyjwt-sca
kind: session
span: 2026-09-30..2026-10-01
date: 2026-10-01
display_title: "memshelf-mcp: ночная смена 30.09→01.10 — доки chat-project surface (#176), красный SCA от pyjwt и фикс лока (#177)"
description: "#176 доковый, SCA красный от pyjwt 2.13.0 в uv.lock main; #177 поднял лок (pyjwt 2.15.1, mcp 2.2.0); оба смержены."
tags: [night-shift, docs, dependencies, sca]
approx_tokens: 50000
approx_tokens_source: estimate
mode: live
notes: "approx_tokens — оценка доли смены, относящейся к этому репо; не замер токенизатором."
---

## Digest
Ночная смена 30.09→01.10 по memshelf-mcp, задача из эпика M3 #167. Draft-PR #176 — только документация chat-project surface. Его hosted CI покраснел на стадии sca пайплайна light · library: транзитивный pyjwt 2.13.0 в uv.lock на main, 13 свежих GHSA, чисто с 2.15.0. Дифф PR лок не трогал, main красный по той же причине. Один комментарий с патчем и один re-run, затем отдельный PR #177 по просьбе владельца: pyjwt 2.15.1, mcp 2.2.0 и 15 новых пакетов, потому что лок отстал от pyproject. Тесты 578 passed, CI 5/5. Владелец смержил #177, обновил ветку #176 и смержил его 01.10 около 17:07Z. Повтор грабли: код возврата после пайпа. Открыто: три мелочи доков без issues, отставание PyPI 0.3.0 от main.

## Decisions
- **#176 — доковый, часть #167, а не Closes.** PR закрывает пункт scope M3 про chat-project surface, эпик остаётся открытым.
- **Красный SCA не чинится внутри докового PR.** Дифф #176 не трогает `uv.lock`, лок совпадает с `main`, `main` красный по той же причине: OSV 29–30.09 опубликовал 13 GHSA на транзитивный `pyjwt` 2.13.0 (`mcp` → `pyjwt[crypto]`), чисто с 2.15.0. Шаги по правилу «не от этого PR»: один комментарий на PR с патчем `uv lock --upgrade-package pyjwt` и один re-run; он упал так же. Отклонено: поднять лок в #176 — доковый PR потянул бы смену рантайм-зависимостей.
- **Фикс — отдельным PR на `main` (#177), по просьбе владельца.** `uv lock --upgrade-package pyjwt` дал `pyjwt` 2.13.0→2.15.1, но заодно `mcp` 2.0.0→2.2.0 и +15 пакетов: лок уже отстал от `pyproject.toml` (`mcp>=2.1.1`, экстра `semantic`), и любой пересчёт его догоняет. Это сказано в теле #177, а не спрятано.
- **Красное от среды контейнера сверяется тем же прибором на нетронутом `main`.** `test_shelve_account_mirror` зависит от хоста и падает на `main` в этом контейнере так же — исключён из локального прогона с объяснением в PR; hosted CI его гоняет.
- **Три мелочи доков, замеченные по ходу #176, — seen-not-changed.** В PR не правились и issues не заводились: доковый PR не расширяется ночью.
- **Код возврата после пайпа — повтор известной грабли.** `$?` после `cmd | tee` отвечает за `tee`; нужен `PIPESTATUS[0]` сразу после пайпа, без промежуточной команды. Повтор записан в журнал риск-чека главной полки.

## Timeline
1. 01.10 14:55–15:12Z — memshelf-167-chat-projects: риск-чек, правка доков → draft-PR #176 (голова `1b5c3f2`), подписка.
2. Hosted CI #176: тесты зелёные, `pipeline / light · library` красный на `sca` (`pyjwt` 2.13.0).
3. ~16:20Z — комментарий на #176: красное не от PR, патч `uv lock --upgrade-package pyjwt`; один re-run — в 16:22Z упал так же.
4. После закрытия смены, по просьбе владельца — ветка `claude/pyjwt-lock-bump` от `main`, `uv.lock` пересчитан (`8ad683e`); локально 578 passed, 2 skipped; draft-PR #177, CI 5/5.
5. ~17:05Z — владелец смержил #177 (`1a6afe9`), влил `main` в ветку #176 (`3680240`) и в 17:07Z смержил #176 (`65ec884`).
6. Этот эпизод — ветка `claude/night-shift-2026-09-30-shelf-episode` от `origin/main` `65ec884`.

## Artifacts
- **PR #176** — docs chat-project surface, часть #167; голова `1b5c3f2`; смержен 01.10 17:07Z.
- **PR #177** — `uv.lock`: `pyjwt` 2.13.0→2.15.1, `mcp` 2.0.0→2.2.0, +15 пакетов; коммит `8ad683e`; CI 5/5; смержен 01.10 17:05Z.
- **Команды:** `uv lock --upgrade-package pyjwt`; `uv run pytest -q -k 'not test_shelve_account_mirror'`; проверка кода возврата — `cmd | tee log; rc=${PIPESTATUS[0]}`.
- **Точка в CI:** стадия `sca` пайплайна `light · library` сканирует `uv.lock` через OSV — красный появляется без изменения кода, когда в базе выходят новые advisories.

## Open threads
- Три мелочи доков chat-project surface, замеченные в #176, не заведены issues — подобрать при следующей правке доков.
- Релиз на PyPI 0.3.0 отстаёт от `main`: `uvx --from memshelf-mcp memshelf doctor` у полки с клоном рядом даёт `served-code-differs` (класс #125).
- `test_shelve_account_mirror` зависит от хоста — кандидат на изоляцию от окружения.
- `main` между мержами не сканировался с 28.09: SCA ловит новые advisories только на PR — расписанный скан `main` закрыл бы окно.
