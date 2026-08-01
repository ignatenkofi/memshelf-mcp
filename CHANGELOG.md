# Changelog

All notable changes to memshelf-mcp will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project will adhere to [Semantic Versioning](https://semver.org/spec/v2.0.0.html)
once code ships.

## [Unreleased]

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
