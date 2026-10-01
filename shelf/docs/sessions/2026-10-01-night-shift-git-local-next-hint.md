# 2026-10-01-night-shift-git-local-next-hint

---
id: 2026-10-01-night-shift-git-local-next-hint
kind: session
span: 2026-10-01
date: 2026-10-01
display_title: "memshelf-mcp: ночная смена 01→02.10 — подсказка next на git-local полке и заголовок fork --no-index (#167)"
description: "Draft-PR #179: next больше не велит пушить полку без remote; заголовок fork --no-index; ROADMAP и ссылки доков."
tags: [night-shift, shelve, next-hint, docs]
approx_tokens: 40000
approx_tokens_source: estimate
mode: live
notes: "approx_tokens — оценка доли смены по этому репо, не замер токенизатором."
---

## Digest
Ночная смена 01→02.10 по memshelf-mcp, хвосты эпика M3 #167 после докового #176: draft-PR #179, коммит c14960c, hosted CI 5 из 5 зелёный. Поле next ответа shelve на git-local полке (git без remote, умолчание init) велело пушить по sync.hint, которого у неё нет и быть не может; теперь оно называет шаг такой полки — rebuild и отдельный коммит производных. На полке с remote next ссылается на sync.hint, только когда он непуст. Заголовок fork --no-index называет только следующие блоки. Решено: два теста, кодифицировавшие несогласованность, получили remote в фикстурах. Открыто: v0.3.0 на PyPI печатает прежний текст — оговорено в chat-projects.md; exit criterion M3 и вопрос 6 ARCHITECTURE.

## Decisions
- **#179 — часть #167, не Closes.** Exit criterion M3 (прогон не автором) и открытый вопрос 6 ARCHITECTURE остаются открытыми.
- **`next` на git-local полке называет её шаг, а не push.** Полка с git и без remote (умолчание `init`) получала «episode committed locally, not pushed — push it (see sync.hint)», хотя `sync.hint` у неё всегда пуст, а сниппет CLAUDE.md и скилл shelve говорят обратное. Теперь — rebuild и отдельный коммит производных; файл бот-воркфлоу без remote ничего не рисует.
- **На полке с remote `next` ссылается на `sync.hint`, только когда он есть в ответе.** Без sync, на отцепленном HEAD и нерождённой ветке hint пуст — цитировать пустое нельзя.
- **Два теста кодифицировали несогласованность** — их фикстуры получили remote, чтобы проверять ветку «с remote», а не старый текст.
- **Заголовок `fork --no-index`** говорил «the shelf INDEX, then the episodes» и без блока INDEX — теперь называет только следующие блоки.
- **Доки догнаны:** `docs/index.md` называет `tools.md`, README — `chat-projects.md`, ROADMAP вычеркнул пункт chat-project surface (#176), `chat-projects.md` предупреждает, что до v0.3.0 включительно `next` на git-local полке всё ещё велит пушить.
- **Красный `test_shelve_account_mirror`** — зависимость от хоста из эпизода 2026-10-01-night-shift-chat-projects-docs-pyjwt-sca, не регрессия этого PR; hosted CI зелёный.

## Timeline
1. 01.10 21:59Z — риск-чек `memshelf-167-tail`: значимые грабли — `ruff format --check` в CI этого репо (сверено `scripts/repo-checks.sh`), гарды вне вызова инструмента (имена джоб прочитаны); план не сменён.
2. Правка `tools.py`, `core/gitsync.py`, `core/reuse.py`; тесты `test_shelve_report.py`, `test_reuse.py` (5 новых тестовых функций); CHANGELOG, ROADMAP, ARCHITECTURE, README, `docs/index.md`, `docs/chat-projects.md`.
3. Коммит c14960c, draft-PR #179, подписка.
4. 22:09Z — hosted CI 5 из 5 зелёный (light · library, desktop bundle, conformance, lint · tests на py3.10 и py3.13); метка night-shift снята с #167 и проверена чтением.
5. Закрытие смены — этот эпизод вторым коммитом той же ветки.

## Artifacts
- **PR #179** (draft) — Refs #167; 11 файлов, 127+/11− до эпизода.
- **CHANGELOG** — три записи под Unreleased: поле `next`, заголовок fork, доки.

## Open threads
- Релиз v0.3.0 на PyPI печатает прежний текст `next` — уйдёт со следующим тегом (тег — владелец).
- Exit criterion M3 (прогон не автором) и вопрос 6 ARCHITECTURE.
- Кандидат DX, не взятый ночью: CHANGELOG копит merge-конфликты «keep both» — `merge=union` для него вопрос владельцу, не правка в чужом PR смены.
