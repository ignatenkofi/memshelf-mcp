# memshelf — Roadmap

Milestones are deliberately thin. M0 validates the pattern with **zero new
code**; every later milestone must justify itself against what M0 already
achieves.

## M0 — Pattern validation, no code

Prove the loop works with docshelf as-is plus conventions.

- Create one real memory shelf (private repo) for one real project.
- A `/shelve` **skill** (prompt-only) that instructs the agent to: cut a
  closed topic, write the episode in the RFC's format, call
  `docshelf_add_document`, commit, and replace the topic with the digest.
- Project prompt / CLAUDE.md snippet with the recall rule (INDEX first,
  fetch section, don't guess about past decisions).
- Manual measurement: token cost of answering 5 "what did we decide about X"
  questions via recall vs keeping history in-window (reuse the methodology of
  `benchmarks/token_savings.py`).

**Exit criteria:** the 5 questions answered correctly via recall; measured
savings written down; a list of everything that was annoying enough to need
actual tooling. That list *is* the M1 backlog.

## M1 — `memshelf-mcp` thin server

Only what M0 proved annoying, expected:

- `memshelf_shelve` with the digest contract validation + redaction pass +
  auto-commit (the three things a prompt-only skill can't guarantee).
- `memshelf_recall` / `memshelf_search` / `memshelf_index` as thin wrappers.
- Episode frontmatter schema + `memshelf_doctor` checks.
- Claude Code adapter: hooks `PreCompact` (shelve before lossy compaction),
  `SessionEnd` (session digest), `SessionStart` (inject INDEX) — adapter
  code only; core stays host-agnostic (ARCHITECTURE → Portability model).
- CLI mirroring the MCP tools (`memshelf shelve|recall|search|index`) — the
  portability surface for hosts without MCP.
- Repo bootstrap: `memshelf init` → docshelf `init_shelf` with memory
  conventions (`provider: none`, fixed categories, `storage: git-local` —
  auto-commit, **no remote**; `plain` via flag).

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
