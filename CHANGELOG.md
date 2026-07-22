# Changelog

All notable changes to memshelf-mcp will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project will adhere to [Semantic Versioning](https://semver.org/spec/v2.0.0.html)
once code ships.

## [Unreleased] — design phase

### Added
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
