# memshelf — Roadmap

Milestones are deliberately thin. M0 validates the pattern with **zero new
code**; every later milestone must justify itself against what M0 already
achieves.

## M0 — Pattern validation, no code — **running since 2026-07-13**

Prove the loop works with docshelf as-is plus conventions. Protocol, kit,
and measurement methodology: [`docs/M0.md`](M0.md); the prompt-only skill and
recall-rule snippet live in `adapters/claude-code/`.

- One real memory shelf (private repo — cloud sessions need a remote).
- **Case A, retro-import**: the author's long sqst homework-review dialog,
  segmented into depersonalized episodes + a session digest (import mode of
  the skill; raw transcript never committed anywhere).
- **Case B, live shelving**: recall rule + `/shelve` during ~a week of
  normal work on a real project.
- Measurement via the **token ledger** (`ledger.tsv`, see ARCHITECTURE →
  Accounting): standing cost vs shelved mass vs recall cost per question,
  docshelf-benchmark methodology.

**Exit criteria:** 5 known-answer recall questions answered correctly from a
fresh session via INDEX navigation; ledger numbers written down; the
annoyance log filled. That log *is* the M1 backlog.

## M1 — `memshelf-mcp` thin server

Only what M0 proved annoying, expected:

- `memshelf_shelve` with the digest contract validation + redaction pass +
  auto-commit (the three things a prompt-only skill can't guarantee).
- `memshelf_recall` / `memshelf_search` / `memshelf_index` as thin wrappers.
- Episode frontmatter schema + `memshelf_doctor` checks.
- Claude Code adapter (plugin): `SessionStart` hook injects INDEX;
  a `SessionEnd`/`PreCompact` hook pushes the shelf for durability
  (`MEMSHELF_AUTOPUSH`). Shelving-before-compaction and session digests need
  the LLM, so they stay agent-driven (the `/shelve` skill + recall rule) — a
  hook is a shell command, not the model (DECISIONS 2026-07-22). Adapter code
  only; core stays host-agnostic (ARCHITECTURE → Portability model).
- CLI mirroring the MCP tools (`memshelf shelve|recall|search|index`) — the
  portability surface for hosts without MCP.
- Repo bootstrap: `memshelf init` → docshelf `init_shelf` with memory
  conventions (`provider: none`, fixed categories, `storage: git-local` —
  auto-commit, **no remote**; `plain` via flag).
- `memshelf_stats` over the ledger (standing cost / shelved mass /
  compression ratio) — the transparent-savings feature.
- `memshelf_import` — tool-assisted whole-dialog backfill (M1 candidate;
  confirm need from M0 Case A experience).

**Exit criteria:** dogfooded on two real projects for two weeks; a full
shelve→compact→recall cycle survives without manual repair; `doctor` clean.

## M2 — Policy, hygiene & the context advisor

- Token-budget monitor that *proposes* shelving (never forces).
- **Context advisor** (the "where did my window go?" feature, MANIFEST hero
  scenario 2): report static overhead vs live topics vs stale dumps, flag
  closed-but-unshelved episodes with their token cost, recommend shelve
  actions. Doubles as the onboarding/first-run experience; heuristics-first,
  host-agnostic (see ARCHITECTURE open question 7).
- Retention: `retain_until`, purge tool, reindex after purge.
- Rollups: consolidate old episodes into digest-of-digests, archive category.
- Configurable PII/secret pattern packs per shelf.

**Exit criteria:** a shelf with 100+ episodes keeps INDEX under ~10 KB and
recall precision doesn't degrade (re-run the M0 question set); the advisor's
shelve proposals are accepted (not overridden) most of the time in dogfood
use.

## M3 — Retrieval upgrades, reuse layer & second surface

- Embeddings sidecar behind the same `search` signature (docshelf's
  documented extension point).
- Chat-project surface documented end-to-end (Desktop/web, manual triggers).
- Cross-shelf meta-INDEX experiment (federation open question).
- **Archive-as-raw-material** (MANIFEST hero scenario 3): tag/graph views
  over episodes (frontmatter tags + cross-episode links in Decisions),
  quarter retrospectives, **fork-a-thread** (bootstrap a fresh session from
  INDEX + selected episodes — continue an old discussion branch as a
  sub-thread without its full history).
- **Artifact mirror** experiment: INDEX (± episodes) as private claude.ai
  artifacts for phone-side reading (open question 8).

**Exit criteria:** search-miss rate measurably better than grep baseline on
the dogfood shelves; one non-author user runs the chat-project flow from docs
alone; one real "fork from episode" session succeeds end-to-end.

## Explicitly deferred

- Automatic topic segmentation (needs M0/M1 experience to judge feasibility).
- Any UI.
- Multi-user / shared shelves.
