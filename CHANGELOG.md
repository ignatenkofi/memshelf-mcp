# Changelog

All notable changes to memshelf-mcp will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project will adhere to [Semantic Versioning](https://semver.org/spec/v2.0.0.html)
once code ships.

## [Unreleased]

### Added

- **`shelve --publish` / MCP `publish`: эпизод покидает контейнер веткой
  (#118).** Режим для полки, чей `main` принимает людей только через PR:
  отказ такого push — политика, он приходит после конца эфемерной сессии, и
  повтор #108 умирает той же смертью. Publish пушит коммит эпизода как
  `HEAD:refs/heads/shelve/<slug>` — чекаут веток не переключает, recall
  продолжает отвечать с этого клона; отчёт несёт ветку, sha после push и
  compare-ссылку в один клик. Приёмка — на настоящем bare-origin с
  pre-receive-хуком, отвергающим `main`. Autopush-хук адаптера claude-code
  при отказе прямого push падает в спас-ветку `shelve/autopush-<utc>`;
  `MEMSHELF_AUTOPUSH_MODE=branch` не пробует обречённый push вовсе.
- **Свежесть — находки `doctor` (#125, закрывает открытый выбор пробы; #103).**
  `served-code-differs` (error) — каталог, из которого исполняется этот
  doctor, хеширован in-process и не совпал с эталонным чекаутом
  (`$MEMSHELF_CHECKOUT`, иначе `memshelf-mcp` рядом с полкой);
  `not-a-release` (warning) — обслуживающий вызов потребитель собран с
  локальным сегментом PEP 440; `freshness-unknown` — новый уровень находок
  `unknown` со своим счётчиком в отчёте: «не знаю» не сворачивается в «ok».
  Разрыв (a) «смержено, но не выпущено» в doctor не попадает (20 суток
  непрерывного шума о нормальном состоянии — по замеру) и прибит тестом;
  осознанная проба доступна как `memshelf freshness`. Цена: ~2 мс на фоне
  ~0.5 с `check_shelf`.
- **`doctor` различает «эпизод не запушен» и «бот встал»
  (main-memshelf#154, вариант 3).** Неучтённые эпизоды делятся по
  upstream-рефу: не присутствующие на `origin/<branch>` получают warning
  `episode-unpushed` с починкой (push → fetch → повторный doctor) и не
  питают `derived-stale` — рендерер судится только по тому, что мог видеть.
  Ложный вердикт 2026-08-21 воспроизведён тестом.
- **Контракт слага получил точку принуждения (#101).** Недатированный слаг
  нового эпизода отвергается до записи (`SlugContractError`) с форматом и
  готовой исправленной формой; префикс обязан быть правдоподобной датой.
  Amend легаси-эпизода под недатированным именем работает — контракт
  стережёт новые имена, не доступ к старым.
- **Выдача `shelve` описывает полку, которая есть (#98, #99).**
  `shelf_totals` называет источник (`as_of: last-rebuild`), кладёт рядом
  правду диска (`episodes_on_disk`) и признак расхождения (`derived_stale`);
  поле `next` называет одно действие, доводящее производные до эпизода
  (push на полке с ботом / rebuild отдельным коммитом без бота). Докстринг
  `memshelf_shelve` больше не обещает запись строки реестра — и это прибито
  тестом.

### Changed

- **`docshelf-mcp` — пол 0.4.1.** Его перечисление перестало считать
  сплит-файлы документами и само репортит сплит-каталоги; расхождение
  INDEX от легаси-сплита вылечено апстримом, и тест #109-мира переписан
  под вылеченное поведение (старый ассерт `stale-index` падал на любой
  свежей установке — CI ставит зависимости свежим резолвом).
- **`recall` логирует по умолчанию (#112, п. 4).** MCP `log=true`, CLI
  `--log` включён (`--no-log` — чтение без следа): без счётчика нельзя
  узнать, взялась ли привычка чтения. Попутно обезврежена мина, жившая и
  под опциональным флагом: `recall-log.tsv` трекается полкой, и грязный лог
  блокировал следующий `shelve` через preflight — dirty-гард теперь
  исключает ровно этот файл (append-only телеметрия, рендерер её не
  трогает), контрольный тест держит гард для всего остального.
- **CI гоняет объявленные концы диапазона (3.10 и 3.13), релиз прикладывает
  `.mcpb`-бандлы к GitHub-релизу (#87, частично).** uv-бандл и
  linux-standalone собираются и linux-вариант запускается тем же живым
  чек-скриптом, что на PR; macOS-standalone кросс-собирается без запуска.
  Подпись бандлов остаётся открытой в #87 (нужен сертификат — решение
  владельца). Первый прогон job'а покажет только следующий тег: release.yml
  на PR не запускается.

### Fixed

- **`shelve --amend` достаёт архивные эпизоды (#117).** Слаг за роллапом —
  эпизод, а не опечатка: правка идёт на месте в `archive/docs/` тем же
  конвейером; обычный shelve поверх архивного слага отвергается с
  архивным путём (дверь «один слаг в двух местах» закрыта); смена kind у
  архивного — явный отказ, а не молчаливый переезд через границу архива.

### Added

- **Проба свежести потребителей: `core/freshness.py` (#125).** Дважды за один
  день правка была смержена и не действовала — сначала полку обслуживал старый
  бинарь из pipx, потом расширение Claude Desktop, отставшее от main на 121
  строку, — и оба раза это нашлось случайно, при разборе постороннего симптома.
  Номер версии молчал: у расширения и у пакета он одинаковый и не двигался с
  момента сборки.

  Проба спрашивает потребителя его же интерпретатором и не про номер, а про
  каталог: что импортируется, каков хеш обслуживаемого кода (по исходникам,
  мимо `__pycache__`, так что пропавший файл виден так же, как правка внутри
  файла), какие версии дистрибутивов установлены. Разрывов три и называются они
  порознь: `(a)` смержено, но не выпущено — коммиты в `src/` после последнего
  тега; `(b)` выпущено, но не установлено — `not-a-release` на локальном
  сегменте PEP 440, которым помечается ручная сборка; `(c)` установлено, но не
  обслуживается — `served-code-differs` по хешу против эталонного дерева.

  Третий исход обязателен и не сворачивается: потребитель, которого не удалось
  опросить, — `consumer-unprobed`, репозиторий без единого тега — `UNKNOWN`, и
  `UNKNOWN` бросает `TypeError` при попытке привести его к `bool`, чтобы «не
  знаю» нельзя было молча прочитать как «всё хорошо». Пустой список
  потребителей — тоже не «все свежие»: `main()` возвращает 2.

  Модуль только читает: ни установок, ни записи, ни сети. Запускается руками —
  `python -m memshelf_mcp.core.freshness <репозиторий>`. Чем именно он должен
  срабатывать сам (находка `doctor`, движущаяся версия расширения, релизный
  ритуал) — открытый выбор в #125.

### Fixed

- **`shelve` no longer lets docshelf split an episode into section files, and
  `prune-splits` removes the ones older versions left (#109).** docshelf splits
  any document past 50 KiB with two or more H2 headings into
  `docs/<category>/<slug>/NNN-*.md`; `shelve` stages the episode path alone, so
  those files never entered the repository. The shelf's derived layer stopped
  being a function of its committed episodes: this working copy rendered
  `INDEX.md` with a section block the bot's checkout could not produce, so
  `doctor` reported `stale-index` — correctly, permanently, and through every
  successful bot run. Measured on a shelf with a 53 KB episode: the warning
  survived regeneration, the prescribed `run rebuild_index` wrote ten INDEX
  lines pointing at three paths with zero tracked files (broken links, reverted
  by the bot on its next pass), and `search` returned
  `docs/sessions/<slug>/003-decisions.md` — an address `recall --id` rejects
  and no other machine has. Reproduced from scratch on a shelf whose
  `.gitignore` has no rule for split directories: the ignore rule was never the
  cause, the single-path commit was.

  Nothing read those files. `recall --section` slices the section out of the
  episode (`core/recall.py::_slice_section`), and an A/B on copies of a real
  183-episode shelf, with and without the directory, returned byte-identical
  output from `recall`, `recall --section`, `index` and `stats`; only `doctor`
  and `search` differed, and both differed for the worse. So `shelve` now passes
  `split=False` and one episode stays one file.

  For shelves that already carry such directories: `memshelf prune-splits`
  (CLI only, dry run by default, `--apply` to delete). It removes only a
  directory that sits beside an episode of the same stem, on a git shelf, with
  nothing inside it tracked — a shelf that committed its sections is coherent
  and is reported instead of touched. `doctor` reports the untracked ones as
  `local-split-dir` and names the command.

  The `fix: "run rebuild_index"` hint that publishes those broken links lives
  in docshelf and is filed there.

### Changed

- **The INDEX budget is a function of shelf size, not a constant — and
  `index-bloat` no longer prescribes a rollup.** `INDEX_BUDGET_TOKENS = 2500`
  is replaced by `index_budget(entries) = INDEX_BASE_TOKENS +
  INDEX_TOKENS_PER_ENTRY × entries` (200 + 80×N).

  The old constant could not be met. INDEX lists episodes, so its size is
  O(episodes) by construction, and a fixed ceiling stops being reachable once a
  shelf passes it — at ~30 episodes here. The only mechanism that lowers the
  number afterwards is `rollup`, and `doctor`'s advice said exactly that ("roll
  up old episodes"), so a shelf past thirty episodes was permanently in
  violation with archiving live memory as its only compliant move. Measured on
  the author's 113-episode shelf: INDEX 9365 tokens against a budget of 2500,
  with a structural floor of ~3800 even with every description deleted and the
  link de-duplicated. What was actually wrong was per-line: descriptions were
  43% of the file.

  The constant also mis-converted its own source. ROADMAP M2's "~10 KB at
  chars/4 = 2500 tokens" holds only where one character is one byte; this
  shelf's Cyrillic runs ~1.42 bytes per character, so the two clauses named two
  different budgets (10 KB ≈ 1800 tokens, 2500 tokens ≈ 14 KB).

  On a linear budget, over budget can only mean *fat entries*, so the finding
  now reports the per-entry price against its allowance, and its fix is to trim
  and `rebuild`. A rollup removes entries and their allowance together and
  cannot move that price — asserted in
  `test_a_rollup_does_not_buy_headroom_it_did_not_earn`.

- **`rollup` is proposed for size, on its own trigger.** The advisor fires it
  at `INDEX_CONTEXT_SHARE` (3%) of the caller's stated window rather than off
  `index-bloat` — a share, so it scales with the host instead of repeating the
  fixed-constant mistake. When a shelf has both problems the advisor says so
  and says the rollup will not fix the other one. `advise` now also reports
  `index_budget_tokens` and `index_entry_tokens`.

  One term of the per-entry allowance is not ours to shrink: docshelf renders
  each entry's filename twice, as the link label and inside the path, which is
  ~1500 tokens of pure duplication on a 112-entry shelf. Filed as
  docshelf-mcp#96; when it lands, `INDEX_TOKENS_PER_ENTRY` drops from 80 to
  ~67.

### Fixed

- **The description cap applied to the branch nobody used.** `shelve` computed
  `description if description is not None else _first_sentence(digest)`, and
  only `_first_sentence` truncated (at 200 chars) — so an explicitly passed
  description, which is what callers pass, was written through unmeasured. The
  author's shelf carried 15 descriptions past 200 characters, the longest 420.
  There is now one cap, `MAX_DESCRIPTION_CHARS = 120`, applied by
  `clamp_description` on both the write path and the render path
  (`rebuild.render_meta`). Capping on render as well is what lets a shelf of
  already-oversized descriptions come back under budget from one `rebuild`,
  without rewriting an episode: the episode stays the source and keeps its
  text, the derived line is what is paid for. Truncation is word-aware, marked
  with an ellipsis, and reported as a `shelve` warning.

### Documentation

- **Said in the docs what only the code comments knew: `no-ledger-row` and
  `stale-index` right after a `shelve` are normal on *every* branch, `main`
  included** (#80). The shelf's own guidance excused them "on a branch", a
  reader on `main` read that literally, rebuilt and committed the derived files
  by hand — and got the merge conflict #58 exists to prevent. README and
  ARCHITECTURE now state the intermediate state, its branch-independence, and
  the one action that must not be taken; `doctor`'s own `fix` line says the same
  where a reader actually meets the finding.

### Added

- **The `derived-stale` threshold is now the shelf's to pick:
  `memshelf doctor --derived-stale-hours N` and `derived_stale_after_hours` on
  the `memshelf_doctor` tool** (#89, main-memshelf#137). The check shipped with
  its day-long default reachable only from Python — `check_shelf` had the seam,
  the CLI and the MCP tool did not — so every shelf was stuck with 24 hours no
  matter how fast its renderer normally answers. A day is exactly the delay
  that hid the 2026-08-13 renderer outage on the dogfood shelf; that shelf now
  records 6 hours, and other shelves can pick their own number without editing
  the library. Default unchanged.

- **`doctor` tells a lagging renderer from a stopped one: `derived-stale`**
  (#89). `no-ledger-row` meant two things — *the renderer has not run yet*
  (normal one second after a shelve, resolves itself) and *the renderer cannot
  run* (nothing resolves it, and every shelve adds to the pile). A warning that
  means both means neither, and it is self-concealing: the shelf's rules
  correctly say the fresh case must not be hand-fixed, so the documented
  response to the only visible symptom of a dead renderer was to ignore it.
  Nine episodes accumulated that way on the dogfood shelf before anyone looked.

  Episodes uncounted **while `ledger.tsv` itself has not been rewritten for
  `DERIVED_STALE_AFTER_HOURS`** (a day) is now one shelf-level error naming the
  episodes. The clock is the commit that last touched `ledger.tsv` — not the
  episode dates, which say nothing on an unmerged branch, and not the file
  mtime, which a fresh clone rewrites. The per-episode warnings are unchanged;
  they are right about the fresh case.

  `check_shelf` grows `now=` and `stale_after_hours=` because a guard about
  elapsed time has to be drivable to fail at all. Run against the live shelf it
  was written for: `error derived-stale — 10 episode(s) have no ledger row and
  the derived layer has not been rewritten for 32h`.

- **memshelf installs into Claude Desktop as an `.mcpb` extension** —
  `adapters/claude-desktop/`. Two bundles, because Claude Desktop ships a Node
  runtime and not a Python one, and macOS's own `python3` is 3.9, below this
  project's floor: an ~85 KB bundle on `server.type: "uv"` (manifest 0.4, the
  host provides Python) and an ~23 MB one on `server.type: "python"` (manifest
  0.3) carrying a pruned python-build-standalone interpreter plus every
  dependency. Neither needs anything installed on the machine.

  `build.py` generates the manifests rather than storing them: the version comes
  from `__init__.py` and the tool roster is parsed out of `server.py`, so a tool
  added or renamed cannot go missing from a bundle. docshelf is vendored without
  its dependency tree — it declares `pymupdf4llm`, ~200 MB of PDF ingestion that
  a memory shelf never reaches, and memshelf imports one docshelf module that
  needs none of it.

  `try_bundle.py` checks a built bundle by *starting* it: it unpacks the zip,
  expands the manifest's own `${__dirname}`/`${user_config.*}` templates, spawns
  exactly the command `mcp_config` declares and speaks MCP to it. That is what
  catches an over-pruned interpreter, wheels built for the wrong ABI, or a
  `mcp_config` path pointing at a file that is not in the zip — none of which
  `mcp validate` can see. The `linux-x86_64` build target exists so that path
  can be run on a build machine and in CI.

- **A default shelf: `$MEMSHELF_SHELF_PATH`.** Every shelf-scoped tool input now
  accepts an omitted `shelf_path` and falls back to that variable, so a packaged
  host — which can only configure an extension through the environment — can
  carry one global shelf. Precedence is the point: an explicit `shelf_path`
  always wins, which is how a project pins a shelf of its own while the setting
  covers everything else. Neither present stays an error, now one that names the
  variable to set instead of writing to `""`.

  **The CLI honours it too** (#86). It landed MCP-side first, and an asymmetry
  where the variable works for the tools and is ignored by the CLI reads as a
  bug to whoever sets it — the CLI being the documented portability surface, and
  so the one most likely to be scripted around. `--shelf` is optional on every
  subcommand, resolved through the same `default_shelf_path()`, and an implicit
  shelf announces itself on stderr (`memshelf: shelf from $MEMSHELF_SHELF_PATH:
  …`). The announcement is what makes it safe: the footgun in a script is
  silence, not the fallback. Neither present exits 2 naming both ways to fix it.

## [0.2.0] — 2026-08-04

Cut because **the published 0.1.0 has a dead MCP server entry point.** That
sdist declares `mcp>=1.2.0` with no ceiling and its `server.py` imports
`mcp.server.fastmcp`, which 2.0.0 removed — so a fresh `pip install
memshelf-mcp` resolves mcp 2.x and the `memshelf-mcp` console script dies at
import with `ModuleNotFoundError: No module named 'mcp.server.fastmcp'`. The
`memshelf` CLI kept working, because `cli.py` never imports the server; that is
why a smoke test of the installed package looked fine. The code fix (the SDK
2.x port and the major ceilings below) has been on main since; only the release
was missing.

### Fixed

- **`serverInfo.version` was empty in every handshake** (#83). `MCPServer` takes
  a `version` keyword and nothing passed it, so hosts displayed memshelf without
  a version — and the defect was invisible from inside the process, where the
  SDK's default empty string keeps every test green. The assertion now lives in
  the stdio test that drives a real client, and compares against `__version__`:
  with the same code shipping five ways (PyPI, uvx, plugin, two bundles), "which
  build is this" is the question `serverInfo` exists to answer.

- **`shelve --amend` could not change an episode's `kind`, and blamed the slug
  for it** (#90). The target was resolved from the *new* kind's category and
  looked for only there, so a kind change always read as `AmendTargetMissing:
  no episode … on this shelf`. That is the one correction amend is genuinely
  needed for — `kind` decides which sections `doctor` demands — and the
  workaround that did work (shelve without `--amend`, then delete the old file
  by hand) left the same episode in two categories, and two ledger rows for one
  slug, whenever its second half was skipped.

  A slug is the ledger key for the whole shelf, so the lookup now spans
  categories: a kind change moves the file, stages both ends in the commit, and
  reports `moved_from`. The same lookup makes the non-amend case honest —
  shelving a slug that already exists under a different kind is now refused with
  the path it was found at, instead of quietly writing a second copy.

- **`resolve` counted ledger rows by spelling, not by meaning** (#78). The
  union compared whole strings, so one row written with and without its
  trailing empty `notes` column counted as two — the pair that survived a live
  `resolve` on the dogfood shelf and had to be deleted by hand. Counting now
  runs on the cells with trailing empties dropped, and the **widest** spelling
  is the one written: `ledger.tsv` has six columns (shelf-spec v0 § 4.4) and
  `doctor` calls a five-cell row malformed, so preferring the narrow spelling
  would trade a duplicate-row finding for a malformed-row finding.

  Only trailing empties are normalised — a middle empty cell shifts every
  column after it and stays a real difference. Both that and the #62 invariant
  (identical rows are events, never collapsed) have their own tests.

  The issue's open question — would `doctor` have caught the pair? — is now
  answered by two tests rather than by reasoning: **yes, but not by the rule
  one would expect.** The pair is five cells against six, so the column-count
  check fires and skips that row before the `episode_id` uniqueness check
  (#63) can see it. Repair the column count by hand and the duplicate check
  takes over. Either way the shelf is blocked; the message an operator reads
  differs.

- **The "return errors, never raise" contract did not cover bad input** (#85).
  Validation runs before a wrapper is entered, so a malformed call left as plain
  text (`Error executing tool …: 1 validation error …`) and a caller written
  against the documented envelope met a `JSONDecodeError` — which is how this
  was found, in the desktop bundle check. The class is not exotic: it is every
  bad call a model makes, plus the ordinary "no shelf configured yet" a fresh
  install hits first. Validation failures now leave through the same
  `{"status": "error", …}` envelope, naming `ValidationError` as the type.

  The contract test could not have caught it — `_placeholder` builds only valid
  inputs — so it grew two invalid-input cases that drive `call_tool` rather than
  the wrapper functions. Both fail against the previous code.

- **A flat argument object is accepted as well as the nested one** (#84).
  Callers who wrote arguments the way nearly every other MCP server publishes
  them got an error about a field called `params` that appears nowhere in the
  tool's documented interface. The **published** schema is unchanged — whether
  it should flatten is a wire-contract decision, and #84 stays open for it; this
  only makes guessing wrong survivable.

- **The parent `INDEX.md` failure was still silent — the earlier fix was half
  of one.** `rollup` and `purge` called `rebuild(root)` without binding its
  return value, so the `RebuildReport.warnings` it already collects (including
  `INDEX.md not rebuilt: …`) went on the floor *one line above* the archive
  warnings that were being carefully forwarded. The parent INDEX is the file
  that rides in **every** session, so losing its failure notice mattered more
  than losing the archive one. Both call sites now extend from both rebuilds;
  the regression test demands two distinct INDEX warnings and fails if either
  is dropped. Found by an adversarial read of this branch's own diff.

- **Every rollup ever produced linked to a path that does not exist.** The
  rollup episode lands at `docs/topics/<slug>.md`; the archive sub-shelf sits
  at the shelf root. The body was written with a bare `archive/INDEX.md`, which
  a Markdown reader resolves against the episode's own directory — i.e.
  `docs/topics/archive/INDEX.md`. Dead, in every rollup, since the feature
  shipped.

  Not a cosmetic typo: this pointer *is* the rollup's promise. Collapsing N
  INDEX lines into one is only acceptable because the originals stay
  reachable, and that claim is made by this single link. Now `../../archive/
  INDEX.md`, with the label still showing the shelf-root path because that is
  what a reader would type. The regression test resolves the href from the
  episode's own directory rather than matching the expected prefix — a test
  that only looked for `../../` would pass on a link wrong in some new way.

  Found by a portfolio-wide sweep for relative Markdown links that point at
  nothing (3723 links checked across 28 repositories).


- **`rebuild_archive_index` swallowed its failure, and `resolve` reported the
  resulting stale file as regenerated.** The archive INDEX rebuild was wrapped
  in a bare `except Exception: pass` justified as "a shelf without docshelf
  keeps its files" — but `docshelf-mcp` is a hard dependency here, and eight
  other modules import the same symbol unguarded, so the case it defended
  against cannot occur. What the handler actually caught was every real
  failure, silently.

  The damage showed up one caller away. `resolve` decided what to report with
  `if (root / "archive" / "INDEX.md").is_file()` — which answers "a file is
  there", not "we wrote it". A failed rebuild plus a stale INDEX from an
  earlier run therefore produced `regenerated: ["archive/INDEX.md"]`: the
  caller's one field for *what actually happened* said the opposite of the
  truth, on the conflict-resolution path where the shelf's own working rules
  tell the agent to trust the tool.

  `rebuild()` already funnelled the identical failure into `report.warnings`;
  the archive path was the odd one out. It now returns warnings instead of
  `None`, `RollupReport` and `PurgeReport` carry a `warnings` field through to
  `as_dict()`, and `resolve` claims the file only when the rebuild reported
  nothing wrong. Two regression tests cover both halves; both fail against the
  previous code.

### Changed

- **Ported the server to MCP SDK 2.x.** `FastMCP` (`mcp.server.fastmcp`) became
  `MCPServer` (`mcp.server.mcpserver`) in 2.0.0; the `@mcp.tool` decorator kept
  its `name`/`annotations` keywords, so the port is two lines. The pin moves to
  `mcp>=2.0.0,<3` — a **floor**, not a raised ceiling: a 1.x install now fails
  at import.

  Done in step with `docshelf-mcp`, which ported the same night: memshelf
  depends on it, and a hard floor on one side against a hard ceiling on the
  other is an unsatisfiable pair (`pip install` of both from git returned
  `ResolutionImpossible` before this change).

### Added

- **`tests/test_stdio_protocol.py` — a real client over the real transport.**
  Three tests: handshake plus tool roster, a tool call whose answer must carry
  this shelf's episode, and a silent server that must time out instead of
  hanging.

  They exist because the in-process suite cannot see the server. Measured, not
  assumed: replacing `@mcp.tool` with a no-op decorator — all 14 registrations
  gone, so a client sees an empty server — leaves **253 in-process tests green**
  and fails only these. `test_server.py`'s `assert callable(getattr(server,
  tool))` passes too, because a no-op decorator returns the function unchanged:
  the assertion that reads like "the tools are registered" checks only that the
  functions exist.

- **`python -m memshelf_mcp`** entry point, mirroring docshelf. The console
  script is not always on PATH — bare checkout, unactivated venv, a client
  config that spawns the interpreter — and this is the path the protocol tests
  drive, so what they exercise is what a desktop client uses.

### Fixed

- **`rebuild` on a misspelled path created a second shelf and called it ok.**
  The writers create their parents, so a typo in `--shelf` did not fail: it
  made a fresh tree with an empty ledger and INDEX, answered `ok=True`, and
  left the real shelf untouched — the operator reads "ok" and believes the
  derived files were regenerated. `rebuild` now refuses a path that is not an
  existing directory. Existence only, deliberately: shelves in the wild differ
  (some carry `.docshelf.json`, some only `shelf.yml`), and a marker check
  would reject working shelves to catch a typo.

- **`purge` answered a misspelled path with "nothing expired".** Same shape,
  worse consequence: a retention sweep that never looked reported
  `count: 0, applied: False`, which is indistinguishable from a healthy shelf
  with nothing to drop. Same guard, same reason.

- **The rebuild report claimed a chart it had not drawn.** `write_chart`
  returns `None` when the ledger has no rows, but `"stats.svg"` was appended
  to `written` unconditionally — a small lie in the one field a caller reads
  to find out what happened.

- **The digest contract was checked after the write, and nothing could fix it**
  (#71). `shelve` printed `digest_warnings: ["thin"]` only once the episode was
  written, the ledger row accounted for and the auto-commit made — and then
  offered no way to correct it: re-shelving the same slug raised
  `DocumentExistsError`, and docshelf's `overwrite=True` was never plumbed
  through. All three remaining exits were bad: a new slug duplicates the episode
  and its ledger row, a hand-edit bypasses the redaction pass and the shelf's own
  «write with the tool, not by hand» rule, and leaving a weak digest devalues the
  episode — the digest is the only thing read at recall before fetching the body.

  Same family as #62–#66: the contract is checked after the action, and the
  failed check rolls nothing back.

  - `shelve --amend` / `amend=True` rewrites an episode already on the shelf
    under the same slug. The whole pipeline runs again — redaction, the digest
    contract, composition — so an amended episode is exactly as guarded as a
    fresh one. Since #58 the ledger row is rendered by `rebuild` from the
    frontmatter, so rewriting the one episode recomputes the one row rather than
    adding a second.
  - Amending a slug that is **not** on the shelf is an error, not a create. The
    overwhelmingly likely cause is a mistyped slug, and a silent create leaves
    the author believing they fixed an episode that still carries the old text.
  - A plain `shelve` onto an occupied slug still refuses — amend is opt-in,
    never the default — but the message now names `--amend` instead of pointing
    at a Python kwarg the CLI user cannot pass.

- **The validator was unreachable without writing** (#71). `memshelf lint-digest`
  runs the same Layer-3 check with no side effects, so a digest can be checked
  while it is still being written rather than by way of a throwaway shelve. Reads
  `--digest`, `--digest-file` or stdin; exits non-zero on errors, and `--strict`
  makes warnings count too. The default stays permissive deliberately: a pure
  reference digest legitimately carries no decision cue, and `thin` must not turn
  into a blocking dialog. Also exposed as the `memshelf_lint_digest` MCP tool.

- **`address` could name a file that was never written.** docshelf writes the
  episode to `slugify(slug, max_len=80)`, while `shelve` assembled its returned
  address from the raw slug. For a slug that is not already slug-shaped the two
  part ways: `2026-08-03-Проверка Слага` lands at
  `docs/topics/2026-08-03-проверка-слага.md`, and the caller is handed a path
  that does not exist. The auto-commit stages that non-path, so `git add` finds
  nothing and the episode stays **untracked** while `shelve` returns without
  raising — in an ephemeral session, the episode lost with the container.
  There is now one derivation of the path, feeding the amend guard, the returned
  address and git staging alike.

### Fixed (one family, all five found by running the tool, not by its tests)

Five defects reported over 2026-08-01 share a shape: the tool finishes, reports
success, and leaves an artifact it would itself call broken.

- **`resolve` regenerates derived paths instead of merging them** (#64).
  After #58 `ledger.tsv`, `INDEX.md`, `stats.svg` and `docs/*/.meta.json` are a
  pure function of `docs/` ⊕ `archive/docs/`; a union of two versions is not
  the sum of two truths. The live 2026-08-01 collision revived 16 `.meta`
  entries whose episodes had moved into `archive/` and doubled 30 ledger rows
  whose `digest_tokens` had been restated — and `resolve` answered
  `status: ok`. It now calls `rebuild` plus `rebuild_archive_index` (the
  archive keeps its own INDEX, which `rebuild` does not touch). Regression test
  builds the conflict on a shelf with a non-empty `archive/` — the class the
  old tests could not reach.
- **`_union_tsv` is a three-way multiset union** (#62). It survives only for
  `recall-log.tsv`, the one file nothing regenerates; its rows carry no
  timestamp, so two sessions recalling the same section write byte-identical
  rows and a set union silently undercounted the savings the log measures.
- **`doctor` validates the register itself** (#63, #65, #66): `episode_id`
  uniqueness plus the column format of shelf-spec § 4.4 (header, column count,
  date, mode, numeric columns), emitted under the spec's own
  `ledger-malformed`. 30 duplicate rows and a `span` interval in the date
  column had both passed with 0 errors, which is how they reached `main` — the
  shelf rule is "doctor clean ⇒ safe to push".
- **The ledger's date column is the shelve date, never `span`** (#65, #66).
  `date or span` printed intervals into a spec-constrained field; the fallback
  is now the slug's date prefix, and failing that the column is left empty so
  `doctor` says so out loud. `--adopt` dates every episode that lacks one
  rather than only those with a row in the old ledger — the episode that
  arrived past the migration was the mine.
- **`shelve` restores the `.meta.json` sidecar** (#69). `add_document` wrote
  the category sidecar behind the contract's back, leaving a derived path
  dirty: committing it trips the shelf's own PR guard and puts a latin slug
  where the display title belongs, and not committing it trips generic
  "nothing uncommitted" hooks. A clean tree after a shelve now holds exactly
  one new file.

### Fixed (the digest-grounding guard was measuring the wrong thing)

- **`digest-body-mismatch` compares stems, not whole tokens.** Russian
  inflects by suffix, so exact matching read «партии»/«партия» and
  «студентом»/«студента» as unrelated words: on a Russian shelf the guard
  undercounted grounding systematically, which is a property of the language,
  not of the digest. The live shelf carried four such warnings; on one of them
  eleven digest words had same-root counterparts in the body that exact
  matching threw away. After the change the same shelf reports one warning
  across 63 measured episodes, with a median grounding of 68%. A positive
  control keeps the guard armed: an unrelated digest over the same body still
  scores 8% and still fires.
- **Pure digits stopped counting as shared vocabulary.** The docstring always
  said they were excluded; the filter was missing, so every episode on a dated
  shelf shared `2026` with every other.

### Added (#14 — the context advisor)
- **`memshelf advise` / `memshelf_advise`** — "where did my window go?"
  (MANIFEST hero scenario 2). Reports the breakdown — static overhead,
  memshelf's own standing cost, live topics, reclaimable — and ranks
  `shelve` / `drop` / `rollup` **proposals**. It writes nothing.
- The window breakdown is a caller **input** (ARCHITECTURE open question 7,
  now closed): a library cannot see the window it is asked about, and a
  parser for one host's `/context` output would rot with that host's next
  release. Same split as `shelve` and `rollup` — the model supplies the
  judgement, the tool supplies what a self-assessment cannot:
  - its own overhead, measured (INDEX + digests), instead of leaving itself
    out of the picture;
  - **verification of every "already shelved" claim** against the actual
    episodes — a claimed `episode_id` that isn't on the shelf is refused
    loudly and becomes a shelve candidate, because acting on it would drop
    content nobody stored;
  - arithmetic net of what shelving costs forever (~200 tokens of digest and
    INDEX line per episode), and no proposal at all below 2000 tokens, where
    the trade stops being worth making;
  - a deterministic ranking — the M2 exit criterion ("proposals accepted,
    not overridden") is unmeasurable against a heuristic that reshuffles.
- When `INDEX.md` is itself over `doctor`'s budget, the advisor answers its
  own `index-bloat` warning with a concrete `memshelf rollup --until <date>`,
  computed by walking the oldest episodes and adding up **what each one's
  INDEX line actually costs** — entries differ by more than a factor of two,
  and an average both picks the wrong set and misreports the gain. Doctor and
  the advisor read the same budget constant, so they cannot disagree about
  the threshold.
- That rollup proposal refuses to be silently destructive on a shelf written
  before #58: if display titles still live only in `.meta.json`, a rollup
  would regenerate the derived files and strip the title off every remaining
  entry, so the report says to run `memshelf rebuild --adopt` first. Found by
  executing the advisor's own proposal on a copy of the working shelf — the
  INDEX shrank far more than predicted, and for the wrong reason.
- Called with **no** occupants it is the first-run view of the shelf and says
  the window side is missing — silence about the window is not a clean window.
- Standing cost is read from the episodes, not `ledger.tsv`: since #58 the
  ledger is bot-rendered, so on a branch it can lag or be missing, and an
  advisor reporting zero overhead there would be flattering rather than
  merely silent.

### Fixed (found by validating against shelf-spec, not by the test suite)
- **Free-text frontmatter is now written as quoted YAML.** A display title
  containing `: ` — the shelf has several — parses fine with memshelf's own
  forgiving `key: value` splitter and is a *syntax error* for a real YAML
  loader. shelf-spec's validator (which the shelves run in CI) then reports
  the episode as having **no frontmatter at all**, not as having a bad line:
  running `--adopt` on the working shelf turned a `valid (0 findings)` shelf
  into one with several `episode-frontmatter-missing` errors. `display_title`,
  `description` and `notes` are always double-quoted now, on both the write
  and the adopt path, and the reader unquotes them.
- A rollup episode used `mode: rollup`; shelf-spec v0 § 5.2 (and § 4.4 for the
  ledger column) allows exactly `live | import`. What makes a rollup a rollup
  is its tag, not a third mode value — otherwise the episode meant to tidy the
  shelf up would be the one failing its validator.
- A rollup lists the display titles of the episodes it hid, not just their
  slugs: the list is read by a human deciding whether to open the archive, and
  a column of latin slugs answers nothing. (It also stopped the write-only
  memory guard from flagging the rollup's own digest.)

### Added (#15 — retention and rollups)
- **`memshelf rollup`** / `memshelf_rollup`: collapse a period's episodes into
  one digest-of-digests and move the originals into `archive/`, a sub-shelf at
  the shelf root. Because docshelf only indexes `docs/`, N INDEX lines become
  one — which is the answer to `doctor`'s `index-bloat` warning, and to
  ROADMAP M2's exit criterion (100+ episodes, INDEX under ~10 KB).
- A rollup shrinks navigation and **nothing else**: `recall --id` and `search`
  still reach archived episodes (the archive is searched as a second shelf),
  `ledger.tsv` keeps every row, and `stats` is unchanged — an archived episode
  still holds the mass it saved. The rollup episode names every id it hid, so
  an INDEX line that hides 40 episodes cannot make them unfindable.
- `doctor` learned about `archive/`. Without that it would have reported every
  rolled-up episode as an `orphan-ledger-row` — a rollup would have looked
  like corruption.
- **Retention**: `retain_until` in the frontmatter (`shelve --retain-until`,
  opt-in per episode) plus `memshelf purge` / `memshelf_purge`, dry-run by
  default, sweeping the archive as well as `docs/` — retention that stopped at
  the archive boundary would mean "kept forever, out of sight". The report
  states plainly that purge removes the working-tree file only and that real
  erasure is a deliberate filter-repo pass.

### Changed
- **The episode is now the only thing `shelve` writes (#58).** `ledger.tsv`,
  `INDEX.md`, `stats.svg` and each category's `.meta.json` became derived
  files, rendered from `docs/` by the new `memshelf rebuild` / `memshelf_rebuild`.
  Two sessions closing two topics used to append to the same ledger, rewrite
  the same INDEX and redraw the same chart — a conflict git cannot merge and a
  human had to unpick (the 2026-07-30 collision cost four conflicted files on
  top of the real one). Now each side carries one new episode file, and the
  merge is clean by construction; the test suite asserts exactly that, in the
  place that used to assert the conflict. Owner's decision of 2026-07-31,
  variant (a) — the pattern already proven in project-atlas ADR 0007.
- Episode frontmatter gained `date`, `notes`, `display_title` and
  `description`. A column that lives only in the derived file cannot be
  regenerated, so everything the ledger and `.meta.json` need moved into the
  episode. `date` is the shelve date, deliberately distinct from `span` (what
  the conversation covered).
- `shelve` stages only the episode when it auto-commits, so a shelve commit
  can no longer carry a regenerated INDEX into a PR.

### Added
- `memshelf rebuild --shelf … [--check] [--adopt]` and the MCP tool
  `memshelf_rebuild`. `--check` writes nothing and exits 1 if any derived file
  has drifted from the episodes — that is the shelf's PR guard, running the
  same code path the bot runs, so the guard cannot pass on logic the bot does
  not execute. `--adopt` is the one-shot migration for a pre-#58 shelf: it
  moves date/notes/display title out of `ledger.tsv` and `.meta.json` into the
  episodes.
- `adapters/shelf-repo/` — ready-to-copy workflows for a shared shelf: a bot
  that regenerates derived files on `main`, and a PR guard that refuses diffs
  touching derived paths.
- Adoption reports `restated_digest_tokens`: rows whose recorded
  `digest_tokens` disagrees with the digest actually in the file. On the
  working shelf that was 30 of 60 rows (standing cost 15112 → 15427 tokens,
  compression 344.5:1 → 337.5:1) — an M0/M1-transition residue, surfaced
  rather than silently rewritten, because the shelf has published those
  numbers.

### Fixed
- **`shelve` can no longer produce an episode the spec validator rejects, and
  `doctor` now catches the ones already on disk** (#56). shelf-spec v0 § 5.2
  makes `span` REQUIRED, but the tool's `--span` was optional and passed the
  omission straight through — the episode landed without the field, `doctor`
  reported healthy, and the shelf's own advisory CI (`shelf_validate`) went
  red: exactly the "manual fix" the M1 exit criterion forbids. Two changes:
  `shelve` now defaults `span` to the episode date (`date`/today) — a live
  episode is almost always single-day, and an explicit multi-day span still
  wins; and `doctor` gained the SPEC 5.2 frontmatter checks
  (`no-frontmatter`, `frontmatter-missing-field`, `bad-approx-tokens`), so a
  spec-invalid episode fails the shelf at doctor time, not in CI. As part of
  the same guarantee `id-mismatch` was raised from warning to error —
  `shelf_validate` treats it as an error, and a doctor that stays green on it
  would hand out the same false "safe to push".

## [0.1.0] — 2026-07-25

### Fixed
- **A shelf no longer initialises silently non-durable on a host without a git
  identity.** `init_shelf` ran a plain `git commit`, which refuses outright when
  `user.name`/`user.email` are unset — a fresh machine, a container, an
  ephemeral CI runner. The non-zero exit was discarded, so `git-local` returned
  `committed=False` and a shelf that looked initialised but held no commit at
  all: durability is the one thing that storage mode promises. Commit now falls
  back to a `memshelf <memshelf@localhost>` identity passed via `-c` (never
  written to the user's config, never shadowing a real identity) and raises if
  it still fails. `shelve`'s auto-commit shares the same path, so an episode
  can no longer fail to persist for this reason either.
- **Ledger `notes` can no longer corrupt `ledger.tsv`** (#31). shelf-spec v0
  § 4.4 forbids tabs in `notes`, but nothing enforced it: the field is
  caller-supplied free text joined straight into the TSV row, so a tab shifted
  the column count for every reader and a newline forged an entire extra row —
  silently, in the file that is the evidence base for the saved-tokens claim.
  `shelve` now flattens tabs/newlines to spaces and reports a warning instead
  of raising (a cosmetic field must not fail an otherwise-good shelve).

### Documentation
- shelf-spec v0 § 4.4 is now named as the **normative on-disk contract** for
  `ledger.tsv` in `docs/ARCHITECTURE.md` (memshelf's columns being its
  `profile: memory` instantiation), and the no-tab constraint is stated in
  both places rows are appended by hand — `docs/M0.md` and the adapter's
  `SKILL.md`. `memshelf_doctor`'s divergence from the spec's four finding
  names is recorded as deliberate rather than left implicit. (#31)

### Added
- **`memshelf_import`** (`core/importer.py`, MCP `memshelf_import` + CLI
  `memshelf import discover|extract`) — the transcript backfill tool (#12,
  M0 annoyances #6/#8/#10). Takes a file **path** (an 87 MB export never rides
  in context or MCP transfer); `discover` finds the target conversation by
  **content markers, not title**; `extract` cleans one conversation —
  **stripping tool_use/tool_result blocks** — to a working file outside any
  shelf and returns its path + the noise ratio. Formats: claude.ai
  `conversations.json` and Claude Code session JSONL (streamed). Pure stdlib;
  the raw transcript is input-only, never shelved. 14 tests.
- **Pre-commit PII/secret guard** (`adapters/claude-code/hooks/pre-commit`,
  #32). Closes the gap where a hand edit / stray write reaches git unchecked:
  layer 1 built-in shapes (email/phone/token/env-secret) over staged content,
  extended by the shelf's `POLICY.patterns` and `MEMSHELF_PII_PACK_DIR`;
  layer 2 pluggable name-PII scanner (`pii-mcp`) that **fails loud (exit 2) if
  absent** rather than passing silently — with a conscious
  `MEMSHELF_PII_BUILTIN_ONLY` downgrade and a `MEMSHELF_PII_SKIP` one-off.
  Redaction markers pass. bash-3.2 + BSD-grep safe, shellcheck-clean; exit
  0/1/2. README install line + env table. 8 hook tests.
- **Machine-readable POLICY pattern packs** (`core/policy.py`, #16). A flat
  `POLICY.patterns` file (`<kind> <regex>`, `#` comments) makes a shelf's
  PII/secret rules machine-readable and is consumed by **both** the shelve
  redaction pass and `doctor` (and shares its format with the pre-commit
  guard — one pack, three consumers). `shelve()` auto-layers it onto the
  builtin shapes (malformed pack → warning, never blocks); `doctor` flags
  `policy-pattern-at-rest` (error) and `policy-pattern-invalid` (warning);
  `init` scaffolds an all-comments template and references it from `shelf.yml`.
  9 tests.
- **`memshelf_doctor` — remaining #13 slices**: (1) the **remote-visibility
  gate** (`core/remote.py`) — opt-in (`--check-remote` / `check_remote`),
  provider-agnostic probe of a shelf's git remotes via the unauthenticated git
  smart-HTTP endpoint (public → `public-remote` error; unverifiable →
  `remote-unverified` warning, never a hard block), all network I/O behind one
  injectable seam so doctor stays offline by default (MANIFEST principle 8);
  (2) **digest/body mismatch sampling** — flags an episode whose digest shares
  almost no content vocabulary with its body (write-only-memory guard),
  mechanical + bilingual, warning-level, abstaining on episodes too small to
  judge. 15 tests.
- **Ambient savings visibility** (#49): (1) the plugin's SessionStart hook
  prepends a one-line banner from `memshelf stats --banner` to the injected
  INDEX — every session opens with the number (best-effort: no CLI on PATH →
  no banner); (2) per-action deltas — `memshelf_shelve` returns
  `shelf_totals` + a `summary` line, and a logged `memshelf_recall` returns
  `saved_tokens` + `summary` (CLI prints it to stderr, keeping stdout
  pipeable); (3) **the shelf's living chart** — `core/chart.py` renders
  `stats.svg` at the shelf root (cumulative "without memshelf" vs "on the
  shelf" by ledger date, log scale, pure-stdlib SVG) and `shelve()` redraws it
  into the same commit as each episode; `memshelf stats --chart` redraws on
  demand. A chart failure never fails a shelve (degrades to a warning).
- **Release & distribution wiring** (first public release, `0.1.0`): version
  bump; `server.json` (official MCP Registry manifest,
  `io.github.ignatenkofi/memshelf-mcp`, PyPI package, stdio);
  `.github/workflows/release.yml` — tag `v*` → gate (version-sync check, ruff,
  pytest) → PyPI via Trusted Publishing (OIDC, no stored secrets) → MCP
  Registry via `mcp-publisher login github-oidc`; `glama.json` +
  `smithery.yaml` directory manifests; README quick-start (Claude Code /
  Claude Desktop / CLI) + the `mcp-name` PyPI-validation marker.
- **`memshelf init`** (`core/init.py`, MCP `memshelf_init` + CLI) — the shelf
  bootstrap (#9): docshelf layout with fixed `topics`/`research`/`sessions`,
  the recall-rule INDEX preamble instead of docshelf's raw-URL default (M0
  annoyance #5), a `POLICY.md` template, the `ledger.tsv` header, and a
  shelf-spec v0 `shelf.yml` (`profile: memory` — the #31 init item). Storage
  modes: `git-local` default (git init + one initial commit, **no remote**),
  `plain`, `git-remote` (wires `origin`; private-visibility enforcement stays
  doctor territory). Idempotent — never overwrites existing files. 7 tests
  incl. the full init→shelve→doctor loop. DECISIONS: server topology recorded
  as "separate MCP process" (closes open question 3 / #28).
- **`docs/assets/case-b-week-report.html`** — the Case B numbers as a one-page
  infographic (English; self-contained, both themes, ledger-styled): the
  236.9:1 closing entry, the week-in-tokens chart, the cost-of-one-question
  comparison, claimed-vs-realized tiles, the doctor's first findings, and the
  M1-in-a-day table. Linked from `docs/demo.md` (#19 follow-up).
- **`docs/demo.md`** — the measured write-up after M0 Case B (mirrors
  docshelf's demo): Case A numbers (recall 5/5; INDEX 1,370 tok; query 1,765 —
  77.9% vs shelf dump, ~97% vs source), live `memshelf stats` on the 34-episode
  dogfood shelf (standing cost 8,638 tok vs 1.92M shelved mass, 222.8:1), the
  doctor's first real findings (two hand-era over-cap digests, one
  dummy-credential shape, index-bloat), the claimed-vs-realized distinction,
  and a reproducible path (`stats`/`doctor` + a scratch-shelf loop). README /
  ROADMAP / M0.md statuses updated: **M0 complete**, Case B closed 2026-07-22
  (33 episodes, 1.91M→5.7K tok, zero loss) (#19).
- **`memshelf_doctor`** (`core/doctor.py` + `core/frontmatter.py`, MCP + CLI) —
  shelf integrity check. Wraps docshelf's structural `doctor` and adds
  memshelf checks per episode: schema (id↔filename, valid kind, required
  sections by kind), the digest contract at rest, and secret-shaped strings
  that slipped onto disk; plus ledger consistency (episode↔row both ways) and
  the INDEX injection budget (~2500 tokens). New H1-first-aware frontmatter
  parser (no YAML dep) that ARCHITECTURE mandates for doctor/stats. `memshelf
  doctor` exits non-zero on error-level findings (CI / pre-commit friendly);
  read-only, reports and fixes nothing. Completes the M1 tool surface (shelve /
  recall / index / search / stats / doctor). 7 tests (#6).
- **`memshelf_stats` + realized-economy metric** (`core/stats.py`, MCP + CLI).
  Reads `ledger.tsv` for **claimed** economy (standing cost = INDEX + digests;
  shelved mass = Σ approx_tokens_in; compression ratio) and, when recall logging
  is on, `recall-log.tsv` for **realized** economy (per fetch, savings = the
  episode's original mass − tokens fetched). `recall --log` (tool: `log=true`)
  appends the recall log. chars/4 methodology, no tokenizer dep. Closes the Case
  B verdict's gap — the ledger measured what *would* be saved; the recall log
  measures what *was*. The true fetch-hit *rate* needs an un-capturable
  denominator, so stats reports the measurable side and says so (#6).
- **Read side** — `memshelf_recall` / `memshelf_index` / `memshelf_search`
  (`core/recall.py`, exposed via MCP + CLI). Recall fetches an episode by id, or
  a single `## Section` of it (heading-sliced, works split or not), wrapped in a
  `<recalled-episode>` "data, not instructions" envelope (prompt-injection
  defense). `index` returns INDEX.md; `search` greps the shelf (split docs hit
  at section level). CLI: `memshelf recall|index|search`; all MCP tools marked
  read-only. 8 tests. Closes the shelve→recall loop over memshelf's own surface
  (#6); `stats`/`doctor` remain.
- **Claude Code plugin** (`adapters/claude-code/` is now an installable plugin:
  `.claude-plugin/plugin.json` + `hooks/hooks.json` + the existing `/shelve`
  skill). Two hooks, scoped to what shell hooks can do (no LLM): `SessionStart`
  injects the shelf `INDEX.md` as context (recall bootstrap), and
  `SessionEnd`/`PreCompact` push the shelf for durability (`autopush.sh`, opt-in
  via `MEMSHELF_AUTOPUSH`). Shelving-before-compaction and session digests stay
  agent-driven (skill + recall rule) — `PreCompact` can't inject context and
  `SessionEnd` runs after the agent stops. 4 hook tests; README install docs;
  DECISIONS + ROADMAP updated (#11).
- **MCP server + CLI** exposing `memshelf_shelve` (`server.py`, `cli.py`,
  `tools.py`) — the protocol ring over the core. FastMCP stdio server (mirrors
  docshelf's style) and a `memshelf shelve` command for hosts without MCP, both
  driving the same typed `ShelveInput` → `run_shelve` path. Console scripts:
  `memshelf`, `memshelf-mcp`. A contract violation returns an actionable error
  (CLI exit 1) without writing. `mcp>=1.2.0` + `pydantic>=2.6` added as deps.
  6 tests (tools validation + CLI end-to-end + server import). Recall / index /
  search / stats land in later slices (#6).
- **`shelve()` orchestration** (`core/shelve.py` + `core/episode.py`) — one
  call turns an in-context topic into a durable episode: redact → validate the
  digest contract → compose the H1-first episode → write through docshelf →
  append the ledger row → auto-commit (commit only, never push). `display_title`
  keeps a latin slug filename while giving INDEX a free-form (e.g. Cyrillic)
  title via a `.meta.json` override. Closes M0 annoyances #1 (slug↔title) and
  #2 (ledger by hand); reuses the #3 validator. 12 tests (7 pure + 5 integration
  against a temp docshelf shelf + git). `docshelf-mcp>=0.2` is now a runtime
  dependency; the Layer-2/3 modules stay import-light (#6).
- **First M1 code** — host-agnostic enforcement core (`src/memshelf_mcp/core/`):
  Layer-2 redaction (`redact.py` — masks credential shapes to
  `«redacted:<kind>»` with a per-kind report, pluggable per-shelf patterns)
  and the Layer-3 digest-contract validator (`digest.py` — ≤120 words,
  first-person-referent reject EN+RU, secret scan, actionable errors). Package
  scaffold mirrors docshelf (hatchling/ruff/pytest, `src` layout); pure stdlib,
  18 tests. Closes the first toil from the M0 annoyance log (#3, digests
  "validated by agent honor") (#6).
- Design package seeded from docshelf-mcp RFC-0001: manifest, architecture
  (episode format, digest contract, storage modes, portability model),
  prior-art landscape, roadmap M0–M3, decision log, worked examples.
- M0 prompt-only kit (`adapters/claude-code/`): `/shelve` skill with live
  and import modes; recall-rule CLAUDE.md snippet; install guide (three
  paths, self-instrumenting shelf recommended).
- M0 protocol and results (`docs/M0.md`): Case A closed — 17 episodes
  imported on a live private shelf, recall test 5/5, INDEX 1,370 tokens,
  query 1,765 tokens (~97% cheaper than conversational source), annoyance
  log ×10 = the M1 backlog (issues #6–#20).
- Community files, ASCII logo, MIT license.

### Fixed
- digest referent-lint: the Russian possessive check now enumerates exact
  forms (`наш/наша/наших/…`) instead of the open prefix `наш\w*`, which also
  rejected the unrelated verb «нашёл» — a false positive hit on the first
  dogfooded CLI shelve (#45).
- `redact`/`scan`: the `env-secret` rule no longer re-matches already-redacted
  values (`KEY=«redacted:env-secret»`) — without the lookahead, doctor flagged
  every correctly-redacted episode as `secret-at-rest` forever and `redact()`
  was not idempotent. Found by running doctor against the live shelf for the
  demo (#19).
- `/shelve` skill and recall snippet now push `git-remote` shelves in
  ephemeral cloud sessions right after the commit; `docs/M0.md` states the
  push is not optional in M0 (was: commit-only, so committed episodes could
  die with the container) (#22).
- `/shelve` Python fallback computes `category` from `kind` (`<kind-mapped>`)
  instead of hardcoding `topics`, so `research`/`session` episodes no longer
  misfile into `topics/` (#23).
- README status softened from "M0 validated" to "M0 in progress: Case A
  closed, Case B running", matching `docs/M0.md` and `docs/ROADMAP.md` (#24).
- Documented the real on-disk episode shape (H1 title first, frontmatter
  second, per docshelf `add_document`) and the frontmatter parser rule in
  ARCHITECTURE Layer 2, the worked example, and the skill (#30).
- `docs/DECISIONS.md` now cites the three docshelf-mcp origin PRs as full
  cross-repo refs (`ignatenkofi/docshelf-mcp#42`/`#43`/`#44`) instead of bare
  `#42`/`#43`/`#44`, which GitHub auto-linked to this repo's own (wrong or
  nonexistent) issues (#26).
- `session:` frontmatter field is now produced by the M0 kit: added to the
  `/shelve` SKILL.md template and the worked example, aligning them with the
  ARCHITECTURE episode schema that already defined it as optional (#27).

### Notable design decisions (see `docs/DECISIONS.md`)
- Storage is local-first: `plain` / `git-local` (default, no remote) /
  `git-remote` (opt-in, private-only).
- Import mode is first-class; raw transcripts are input-only, never stored.
- Token accounting (`ledger.tsv`) is built into the core loop.
- Repository made public 2026-07-13; the dogfood shelf stays private.

[Unreleased]: https://github.com/ignatenkofi/memshelf-mcp/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/ignatenkofi/memshelf-mcp/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/ignatenkofi/memshelf-mcp/releases/tag/v0.1.0
