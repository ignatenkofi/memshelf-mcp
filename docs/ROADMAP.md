# memshelf — Roadmap

Milestones are deliberately thin. M0 validates the pattern with **zero new
code**; every later milestone must justify itself against what M0 already
achieves.

## M0 — Pattern validation, no code — **complete (2026-07-13 → 2026-07-22)**

Exit criteria met: recall test 5/5, ledger + recall-cost numbers written down
([`demo.md`](demo.md)), and the annoyance log became the M1 backlog verbatim.
Case B closed 2026-07-22 (33 episodes, zero loss; verdict episode on the shelf).

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

- ~~Token-budget monitor that *proposes* shelving (never forces).~~ —
  **done** (#14): the advisor takes the budget and reports headroom; the
  proposal, not the action, is the output.
- ~~**Context advisor**~~ (the "where did my window go?" feature, MANIFEST
  hero scenario 2) — **done** (#14): `memshelf advise` / `memshelf_advise`
  reports static overhead vs memshelf's own cost vs live topics vs
  reclaimable, and ranks shelve/drop/rollup proposals net of what each one
  costs. Host-agnostic as decided in ARCHITECTURE open question 7: the window
  breakdown is a caller input, and the tool contributes what a self-assessment
  cannot — measured overhead, verification of "already shelved" claims against
  the episodes, and a deterministic ranking. Called with no occupants it is
  the first-run view of the shelf, and says so rather than reporting a clean
  window. The exit criterion below (proposals accepted, not overridden) stays
  open — it is a dogfood measurement, not a code deliverable.
- ~~Retention: `retain_until`, purge tool, reindex after purge.~~ — **done**
  (#15): opt-in `retain_until`, `memshelf purge` dry-run by default, sweeps
  `docs/` and `archive/`, states the git-history caveat instead of implying
  erasure.
- ~~Rollups: consolidate old episodes into digest-of-digests, archive
  category.~~ — **done** (#15): `memshelf rollup` moves a period's episodes
  into the `archive/` sub-shelf behind one digest-of-digests. Navigation
  shrinks; recall, search, ledger and stats are untouched.
- Configurable PII/secret pattern packs per shelf.
- ~~**Derived files rendered by a bot, not by `shelve`**~~ (#58, decided
  2026-07-31) — **done**: `date`/`notes`/`display_title`/`description` moved
  into the episode frontmatter, `shelve` writes and stages only the episode,
  and `memshelf rebuild` renders `ledger.tsv`/`INDEX.md`/`stats.svg`/`.meta.json`
  from `docs/`. Two parallel shelves now merge cleanly by construction;
  `memshelf resolve` stays as the fallback for a real same-slug collision or a
  shelf without the bot. Bot + PR-guard workflows: `adapters/shelf-repo/`.
  Rollups (below) build on the same regeneration path.

**Exit criteria:** a shelf with 100+ episodes keeps INDEX **within its budget**
— `INDEX_BASE_TOKENS + INDEX_TOKENS_PER_ENTRY × listed episodes`, i.e. the
price of a *line* stays flat as the shelf grows — and recall precision doesn't
degrade (re-run the M0 question set); the advisor's shelve proposals are
accepted (not overridden) most of the time in dogfood use.

> **Revised 2026-08-21.** This used to read "100+ episodes and INDEX under
> ~10 KB", and the two clauses contradicted each other. INDEX lists episodes,
> so its size is O(episodes) by construction; a fixed ceiling is therefore
> unreachable past some shelf size, and the only mechanism that lowers the
> number afterwards is `rollup` — which buys compliance by archiving live
> memory. Measured on the author's 113-episode shelf: the structural floor,
> with every description deleted and the link de-duplicated, is ~3800 tokens
> (~15 KB), so "100+ episodes under 10 KB" was not merely tight but arithmetically
> impossible. The derived constant `INDEX_BUDGET_TOKENS = 2500` inherited the
> contradiction and added a unit error — "~10 KB at chars/4" holds only if one
> character is one byte, and this shelf's Cyrillic runs ~1.42 bytes per
> character, making the two clauses two different budgets (10 KB ≈ 1800 tokens;
> 2500 tokens ≈ 14 KB). The budget is now linear in shelf size, which puts the
> check on the quantity formatting can actually control. Rollups stay in M2,
> triggered by INDEX's share of the context window (`INDEX_CONTEXT_SHARE`)
> rather than by a threshold that growth alone would breach.

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
