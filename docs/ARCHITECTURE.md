# memshelf — Architecture (draft)

Read [`MANIFEST.md`](MANIFEST.md) first for the why; this document is the how.

## Concepts

| Term | Meaning |
|---|---|
| **Episode** | The unit of offload: one coherent chunk of session history — a closed topic, an investigation, a research dump, a decision thread. Maps to a docshelf *document*. |
| **Digest** | The short, decision-preserving summary that (a) becomes the episode's INDEX entry and (b) is the only trace of the episode left in live context. |
| **Session digest** | A special end-of-session episode: what happened, what changed, what's open. The chronological journal of the shelf. |
| **Recall** | Fetching an episode — or one H2 section of it — back into context via MCP read (or raw URL on public shelves). |
| **Trigger** | The event that initiates offloading: explicit command, pre-compaction hook, session end, token budget (v2). |

## The loop

```
            live context (window)
 ┌────────────────────────────────────────────┐
 │  system + task + INDEX.md + recent turns   │
 │  + digests of shelved episodes             │
 └───────────────┬────────────────────▲───────┘
                 │ trigger fires      │ recall (section-sized)
                 ▼                    │
 ┌───────────────────────┐   ┌────────┴────────┐
 │  CAPTURE              │   │  RECALL         │
 │  serialize episode →  │   │  INDEX →        │
 │  normalized Markdown  │   │  SUBINDEX →     │
 │  + redaction pass     │   │  section fetch  │
 └───────────┬───────────┘   └────────▲────────┘
             ▼                        │
 ┌───────────────────────┐            │
 │  DIGEST               │            │
 │  agent writes digest; │            │
 │  tool validates the   │            │
 │  contract             │            │
 └───────────┬───────────┘            │
             ▼                        │
 ┌────────────────────────────────────┴───────┐
 │  STORAGE = docshelf shelf                  │
 │  add_document → split (H2) → SUBINDEX      │
 │  → rebuild INDEX.md → auto-commit          │
 └────────────────────────────────────────────┘
```

Four layers. Storage is docshelf unchanged; capture, digest, and policy are
what memshelf adds; recall is a thin convention over docshelf's existing
read/search tools.

## Layer 1 — Storage: a docshelf shelf with conventions

A memory shelf **is** a docshelf shelf. No new on-disk format — only naming
conventions on top:

```text
memory-shelf/
├── .docshelf.json            # provider: none; memory.storage: git-local
├── INDEX.md                  # the ONLY file that lives in agent context
├── POLICY.md                 # per-shelf PII/redaction rules (optional)
├── ledger.tsv                # token accounting: one row per episode (derived)
├── archive/                  # rollup sub-shelf (#15): own INDEX, outside docs/
└── docs/
    ├── topics/               # closed topics & investigations (the bulk)
    │   ├── .meta.json
    │   ├── 2026-07-10-unevie-auth-refactor.md
    │   └── 2026-07-10-unevie-auth-refactor/     # docshelf H2 auto-split
    │       ├── SUBINDEX.md
    │       ├── 001-digest.md
    │       ├── 002-decisions.md
    │       ├── 003-timeline.md
    │       └── 004-raw-excerpts.md
    ├── research/             # bulky one-shot dumps (search results, specs read)
    └── sessions/             # session digests — the chronological journal
        └── 2026-07-13-sqst-l16-planning.md
```

Conventions:

- **Categories** are fixed and few: `topics`, `research`, `sessions`.
  Per-project separation is done with **one shelf per project** (see Open
  questions), not with per-project categories — keeps each INDEX small.
- **File names** are date-prefixed slugs: `YYYY-MM-DD-<slug>.md`. Natural
  sort = chronological order; the date survives title edits.
- `split_threshold_bytes` and the H2 splitter are inherited — a long episode
  becomes section files automatically, and INDEX links to its SUBINDEX.
  This is why the episode format below mandates H2 skeleton headings.

What we reuse from docshelf verbatim: splitter, indexer, SUBINDEX rendering,
`read_document` (with its UTF-8 paging), `search`, `doctor`, URL providers,
`.meta.json` sidecars. What we do **not** use: the PDF/DOCX converters
(episodes are born as Markdown).

**Storage modes** (`memory.storage` in shelf config):

| Mode | What it is | When |
|---|---|---|
| `plain` | Local directory, no git | Zero-ceremony start; users wary of git entirely |
| `git-local` (**default**) | `git init` + auto-commit per shelve, **no remote configured** | History, rollback, drift detection — with nothing to push *to*. Exactly as private as a plain folder |
| `git-remote` | Private remote, explicit opt-in | Multi-machine sync. Push stays manual by default (`autopush: false`); `doctor` fails the shelf if the remote is publicly visible |

Escalation is one-way cheap (`plain → git-local` is `git init`; adding a
remote is one guarded command) — start minimal, upgrade when trust is
earned. docshelf already supports non-git local shelves for search and
read, so `plain` costs nothing to support.

A note on "store it inside Claude" (attachments/artifacts): claude.ai
artifacts are private-by-default and cross-session updatable, which makes
them a fine **read mirror** — e.g. publishing INDEX.md as an artifact for
phone-side browsing (ROADMAP M3). They are not the canonical store: no
file-system/MCP access from other hosts, size limits, vendor-bound — which
would break portability principle 9. Canonical store stays local files.

## Layer 2 — Capture: the episode format

An episode file is normalized Markdown with YAML frontmatter and a fixed H2
skeleton (fixed so that splitting is predictable and recall can target one
section):

```markdown
---
id: 2026-07-10-unevie-auth-refactor        # == filename slug
kind: topic                                 # topic | research | session
session: <opaque session ref, optional>
span: 2026-07-08..2026-07-10                # when the work happened
tags: [unevie, auth, jwt]
approx_tokens: 41000                        # what this episode cost in-window
---

## Digest
<the validated digest — duplicated from INDEX so the file is self-contained>

## Decisions
<decision → reason; rejected alternative → reason. The most-recalled section.>

## Timeline
<compressed narrative of what happened, in order>

## Artifacts
<links/paths to things produced: PRs, files, commands that worked>

## Open threads
<what was left undone or undecided>

## Raw excerpts   (optional, usually the largest)
<verbatim fragments worth keeping: error logs, key quotes, tool output>
```

Empty sections are omitted. `## Digest` and `## Decisions` are mandatory for
`kind: topic`; `kind: research` requires `## Digest` plus at least one body
section; `kind: session` requires `## Digest`, `## Timeline`, `## Open
threads`.

**On-disk placement (docshelf `add_document`).** The skeleton above shows
frontmatter at byte 0, but the M0 write path prepends an H1 title: docshelf's
`add_document` inserts `# {title}` whenever the content doesn't already start
with `#`, and a `---` fence doesn't. So every episode stored through the kit
is **H1-first** — `# 2026-07-10-unevie-auth-refactor`, a blank line, then the
`---`-fenced frontmatter — not frontmatter-at-byte-0. Both placements are
normative: shelf-spec v0 (openshelf, ADR-0005) § 5.1 "frontmatter placement"
legalized this after the drift was found and requires parsers to accept both.

**Parser rule** (for `memshelf_doctor` (#13) and `memshelf_stats` (#8), which
read frontmatter): the frontmatter is the **first `---`-fenced YAML block,
optionally preceded by a single H1 and blank lines**. A byte-0-only parser
(python-frontmatter's default — "YAML block starting at byte 0") finds zero
frontmatter in real episodes; it must be configured or wrapped to skip a
leading H1.

**Redaction pass.** Before write, capture runs a configurable regex pass over
the body: common credential shapes (AWS keys, `squ_…`, bearer/`ghp_` tokens,
`.env`-style assignments) are replaced with `«redacted:<kind>»`. User-defined
patterns extend the list (e.g. a project-level PII denylist). Redaction is
logged in the shelve result so the agent can flag false positives.

## Layer 3 — Digest: the contract

The digest is written by the agent at shelve time — while the episode is
still in its context — and validated by the tool. The contract:

1. ≤ 120 words (hard cap; INDEX must stay kilobytes-sized).
2. Must answer, when applicable: what was **decided**, what was **rejected
   and why**, what **artifacts** exist, what is **still open**.
3. Written for a reader with *zero* session context ("we" and bare "it" are
   rejected by lint heuristics — named referents only).
4. No secrets (redaction pass runs on the digest too).

Validation is intentionally mechanical (length, required frontmatter,
referent lint, forbidden patterns) — quality beyond that is the agent's
responsibility, backed by `memshelf doctor` spot checks (see Failure modes).

The shelve tool returns the digest and the episode address; the calling
convention is that the digest *replaces* the episode content in the live
conversation from that point on.

## Layer 4 — Policy: triggers

v1 surfaces, in priority order:

| Trigger | Mechanism (Claude Code / Cowork) | What it shelves |
|---|---|---|
| **Explicit** | `/shelve [topic]` skill | The named topic, or the agent proposes a cut |
| **Pre-compaction** | `PreCompact` hook | Last chance before lossy compaction: shelve all closed topics, so compaction destroys less |
| **Session end** | `SessionEnd`/`Stop` hook | A `kind: session` digest into `sessions/` |
| **Budget** (v2) | token-count monitor | Proposes (not forces) shelving idle topics when live context exceeds budget |
| **Subagent deposit** (v2) | subagent instruction template | A research subagent writes its full exploration dump as a `research` episode and returns only digest + shelf address — today the full trace dies with the subagent's context |

Chat projects (Claude Desktop / web) are a v1-documented but manual surface:
the project prompt instructs the model to offer shelving at natural
checkpoints; the user confirms. Same tools, no hooks.

Session start is the recall bootstrap: a `SessionStart` hook (or the project
prompt) injects the current `INDEX.md` — the entire standing memory cost.

## MCP tool surface (v1 draft)

| Tool | Wraps | Notes |
|---|---|---|
| `memshelf_shelve` | `Shelf.add_document` + validation | Input: episode frontmatter fields, body sections, digest. Runs redaction → validates contract → writes → reindexes → auto-commits. Returns address + final digest + redaction report. |
| `memshelf_recall` | `Shelf` read path | By id/path, optional `section` (H2 slug). Section-sized by default; whole episode only on request. |
| `memshelf_search` | `Shelf.search` | Grep-level, returns addresses; embeddings later. |
| `memshelf_index` | read `INDEX.md` | Session-start bootstrap and mid-session refresh. |
| `memshelf_doctor` | `docshelf_doctor` + episode checks | Schema drift, missing digests, secret-shaped strings that slipped through, ledger consistency. |
| `memshelf_stats` | `ledger.tsv` | Transparent token accounting: standing cost (INDEX + digests) vs shelved mass, compression ratio, per-episode and cumulative savings — same tokenizer methodology as docshelf's `benchmarks/token_savings.py`. |
| `memshelf_rollup` | episodes → `archive/` + one rollup episode | Collapse a period into a digest-of-digests (#15). INDEX shrinks; recall/search/ledger/stats keep the archive. The digest is the caller's — synthesis needs the model. |
| `memshelf_purge` | `retain_until` → delete + reindex | Retention (#15), dry-run by default. Removes the working-tree file only; real erasure is a filter-repo pass. |
| `memshelf_rebuild` | episodes → derived files | Render `ledger.tsv`, `INDEX.md`, `stats.svg` and each `.meta.json` from `docs/` (#58). `check=true` verifies instead of writing — the shelf's PR guard. |
| `memshelf_advise` | caller-reported occupants + shelf | The context advisor (#14): breakdown of the window (static / memshelf's own cost / live / reclaimable) and ranked shelve/drop/rollup **proposals**. Writes nothing. Verifies any "already shelved" claim against the episodes before proposing a drop. |
| `memshelf_import` (M1 candidate, pending M0) | segmentation + N× shelve | Retro-shelve an exported transcript: agent proposes episode cuts, then capture→digest→shelve per episode + one session digest. The raw transcript is input only — never stored. |

Design rule: every memshelf tool is a thin layer over `docshelf_mcp.Shelf`;
anything generic enough for documents gets upstreamed to docshelf instead of
living here.

**Accounting.** `ledger.tsv` carries one row per episode (`date / episode_id /
mode(live|import) / approx_tokens_in / digest_tokens / notes`). This makes the
project's core claim — saved tokens — measurable on every real shelf, not just
in benchmarks: standing cost of memory vs shelved mass vs recall cost per
question. See `docs/M0.md → Measurement` for the derived numbers.

Since #58 the ledger is **rendered, not appended**: every column lives in the
episode's frontmatter (`date`, `mode`, `approx_tokens`, `notes`) except
`digest_tokens`, which is computed from the digest in the file, and
`memshelf rebuild` regenerates the whole table from `docs/`. Same for
`INDEX.md`, `stats.svg` and each category's `.meta.json` — four derived files
that a shelf's bot owns on `main` while PRs carry episodes. An append-only
ledger written by every shelve was the multi-writer conflict class: two
sessions, two topics, one unmergeable line.

**The intermediate state is normal, and it is not branch-specific** (#80).
Between a `shelve` and the next `rebuild` the shelf legitimately holds an
episode with no ledger row and an `INDEX.md` that does not mention it — so
`doctor` reports `no-ledger-row` and `stale-index` seconds after a perfectly
correct shelve, on `main` exactly as on a branch. The one wrong response is to
regenerate and commit the derived files by hand: that recreates the conflict
class this split exists to remove. Not hypothetical — it happened on
2026-08-08, on `main`, to a reader who had been told those warnings were a
branch phenomenon. Wait for the renderer; on a shelf without a bot, run
`rebuild` as its own commit.

Persisting for a *day* while episodes keep arriving is a different state — the
renderer is stopped, not lagging — and `doctor` separates the two with
`derived-stale` at error severity (#89).

That reclassification changes how a conflict in those files is resolved.
Before #58 they were append-only, so a union lost nothing. After it they are a
pure function of `docs/` ⊕ `archive/docs/`, and merging two versions of a
derived file produces neither side's truth: a rollup deletes `.meta` entries
whose episodes moved into `archive/`, and a rebuild restates `digest_tokens`,
so a union revives the deleted entries and doubles the restated rows (#64,
seen live on 2026-08-01). **The only correct resolution for a derived path is
regeneration** — `rebuild` plus `rebuild_archive_index`, since the archive
sub-shelf keeps an INDEX that `rebuild` does not touch. `memshelf resolve`
does exactly that; the one file it still merges is `recall-log.tsv`, which
nothing regenerates because a recall is an event, not a fact about the
episodes.

The normative on-disk contract for this file is shelf-spec v0 (openshelf,
ADR-0005) § 4.4, not this document — the columns above are memshelf's
`profile: memory` instantiation of it. One spec constraint is load-bearing
and easy to violate by hand: **`notes` must not contain a tab**, because it
is the last field and a tab there shifts the column count for any reader.
`shelve` enforces it by flattening tabs (and newlines, which would forge an
entire extra row) to spaces and reporting a warning; a cosmetic field must
never fail an otherwise-good shelve.

**Deliberate divergence — `memshelf_doctor` finding names.** The spec names
four findings that overlap this tool's checks (`no-ledger`,
`ledger-malformed`, `episode-frontmatter-missing`,
`episode-frontmatter-invalid`). One of them, `ledger-malformed`, `doctor` now
emits under the spec's own name: it validates the register's columns against
§ 4.4 (plus `episode_id` uniqueness, which is memshelf's own invariant and has
no spec counterpart), and a check that exists to keep the two tools' verdicts
aligned should not force a mapping table to prove it (#63, #65, #66). For the
rest `doctor` keeps its own, more granular
vocabulary instead (`no-ledger-row` and `orphan-ledger-row` for the two
distinct ledger/episode mismatches; `no-frontmatter`,
`frontmatter-missing-field`, `bad-approx-tokens`, `bad-kind`, `id-mismatch`,
`missing-section`, `digest-*` where the spec has the coarse
`episode-frontmatter-missing`/`episode-frontmatter-invalid` pair). The spec's
names are a strict generalization, so mapping memshelf → spec is lossless
while the reverse is not. Renaming is therefore an open decision, not an
oversight (#31): the codes are the tool's output contract, and collapsing
them would cost detail that the M1 exit criteria rely on.

Names diverge; **coverage and severity do not** (#56). Everything
`shelf_validate` rejects as an `error`, doctor must also reject as an
`error` — "doctor clean" has to imply "validate green", or the shelf rule
«doctor чистый ⇒ можно пушить» hands out false guarantees. That is why the
SPEC 5.2 required-field checks live in doctor and why `id-mismatch` is an
error, not a warning.

## Portability model

v1 targets Claude Code / Cowork, but the design must not *belong* to it.
Three rings, dependencies pointing strictly inward:

```
┌──────────────────────────────────────────────────────────┐
│ HOST ADAPTERS (thin, per-surface, replaceable)           │
│  Claude Code: hooks (PreCompact/SessionStart/SessionEnd) │
│    + /shelve skill + CLAUDE.md snippet          ← v1     │
│  Chat projects: project-prompt conventions      ← v1 doc │
│  Anthropic memory tool (memory_20250818): the   ← later  │
│    six /memories file verbs backed by the shelf          │
│  Other frameworks: tool defs generated from     ← later  │
│    the same schemas (OpenAI functions, LangChain, …)     │
├──────────────────────────────────────────────────────────┤
│ PROTOCOL SURFACES (LLM-agnostic)                         │
│  MCP server (works in any MCP client)                    │
│  CLI (`memshelf shelve|recall|search|index`) — for hosts │
│    without MCP: anything that can run a shell command    │
├──────────────────────────────────────────────────────────┤
│ CORE (host-agnostic pure library)                        │
│  episode schema · digest contract · redaction ·          │
│  shelf ops (docshelf) · retention/rollups ·              │
│  prompt templates (recall rule, digest instructions)     │
└──────────────────────────────────────────────────────────┘
```

Rules that keep the boundary honest:

1. **Nothing host-specific in core or on disk.** The episode format contains
   no vendor fields; `session` is an opaque string. A shelf written from
   Claude Code is readable, appendable, and recallable from any other host.
2. **Triggers are adapter territory.** Core exposes *operations* (shelve,
   recall, …); adapters decide *when* to invoke them. PreCompact is a Claude
   Code concept and stays in the Claude Code adapter; another host maps its
   own lifecycle events to the same operations.
3. **Prompts are core assets, rendered per adapter.** The recall rule and the
   digest-writing instructions are host-neutral templates; each adapter
   injects them its own way (hook output, project prompt, system message).
4. **The on-disk shelf is the ultimate interop layer.** Plain Markdown + git:
   an LLM with nothing but file access — no MCP, no CLI — can still read
   INDEX.md and open an episode. Every ring above is convenience, not
   lock-in.

## Design decisions

1. **Agent-written digests, not a summarizer service.** The agent at offload
   time has the full context, knows what mattered, and costs nothing extra.
   A post-hoc summarizer reads a transcript it never lived through. Risk —
   quality variance — is mitigated by the mechanical contract + doctor, not
   by adding infrastructure.
2. **Curated Markdown episodes, not JSONL transcripts.** Human-legible
   archive, git-diffable, H2-splittable, and 10–50× smaller than verbatim
   logs. Verbatim material is opt-in per episode (`## Raw excerpts`).
3. **Auto-commit (departure from docshelf).** docshelf stays out of git
   because a human curates the shelf and agents shouldn't push surprise
   commits. A memory shelf inverts this: the agent *is* the curator, sessions
   are ephemeral, and an unpersisted episode is a lost episode. Scope limit:
   auto-commit touches only the shelf's own repo; push remains configurable
   (`autopush: false` default).
4. **Explicit recall, no auto-RAG.** Predictable token spend; index
   navigation is the pattern docshelf already proved models are good at;
   auto-injection reintroduces context pollution the project exists to fight.
5. **One shelf per project, few fixed categories.** Keeps INDEX small and
   recall unambiguous. Cross-project federation is a later concern (see Open
   questions).
6. **Mechanical eviction, LLM effort only on digests.** Moving content to
   the shelf is a move+stub, never a summarize: research shows mechanical
   masking matches LLM summarization at half the cost (see LANDSCAPE →
   Research findings). The one LLM artifact per episode is the digest,
   written once at shelve time.
7. **Injection budget and KV-cache discipline.** Everything memshelf puts
   into context is hard-budgeted (INDEX size is a `doctor`-monitored
   invariant, not a hope — layered context managers have been observed
   tripping each other's thresholds). INDEX is injected once at session
   start at a stable position; recalls append; nothing rewrites earlier
   context.

## Privacy & retention

- **Local by default, private always**: the default shelf (`git-local`) has
  no remote at all — accidental exfiltration requires an impossible push.
  Recall goes over MCP `read_document`, which docshelf already supports on
  private/local shelves. `git-remote` is opt-in and private-only (`doctor`
  enforces); raw-URL mode requires a further explicit flag and prints a
  warning at init.
- **Redaction** at capture time (Layer 2), plus `memshelf_doctor` scanning for
  secret-shaped strings at rest.
- **Retention**: episodes can be marked transient (`retain_until` frontmatter);
  a purge tool deletes expired episodes and reindexes. Documented caveat: git
  history keeps purged content until a `filter-repo` pass — true deletion is
  a deliberate, manual act.
- **PII policy is pluggable**: a shelf-level denylist/pattern file (e.g. a
  course shelf can enforce "no student names" the way sqst's PII policy
  demands).

## Failure modes

| Failure | Mitigation |
|---|---|
| Write-only memory (digests too vague to trigger recall) | Digest contract + referent lint; `doctor` samples episodes and flags digest/body mismatch; success criterion #3 in MANIFEST is the acceptance test |
| INDEX bloat (hundreds of episodes) | Date-prefixed sort + SUBINDEX thresholds inherited from docshelf; periodic **rollup**: consolidate a quarter's episodes into one digest-of-digests, move originals to an `archive` category linked as a sub-shelf |
| Recall misses (grep can't find it) | Tags in frontmatter are search-indexed; digests are written to be greppable (named referents); embeddings remain the documented extension point |
| Secret leakage | Redaction pass + private default + doctor scan; raw-URL mode gated behind explicit opt-in |
| Accidental exfiltration (push of a memory shelf to the wrong place) | Default mode has no remote to push to; `git-remote` requires explicit opt-in, private visibility enforced by `doctor`, `autopush: false` |
| Shelve interrupted mid-write | docshelf invariant reused: disk is source of truth, INDEX is a render — `rebuild_index`/`doctor` recovers; auto-commit is one atomic commit per shelve |
| Digest lies (agent summarized wrong) | Episode keeps `## Raw excerpts` for load-bearing facts; recall of the section, not trust in the digest, settles disputes |
| Prompt injection via recall (episodes replay model-authored text — and possibly captured hostile text — into future contexts) | Recall wraps content in a data envelope with an explicit "content, not instructions" frame; capture-time redaction; `doctor` flags instruction-shaped patterns in stored episodes |
| Fighting the platform's own context managers (double-shelving, injected INDEX tripping persisted-output thresholds) | Hard injection budgets (design decision 7); adapters detect platform features and yield — e.g. don't re-shelve a tool output the platform already persisted |

## Open questions

1. ~~**Name.**~~ Resolved 2026-07-13: `memshelf` — PyPI (`memshelf`,
   `memshelf-mcp`) and GitHub checked free; repo created.
2. ~~**Repo placement.**~~ Resolved 2026-07-13: separate
   [`memshelf-mcp`](https://github.com/ignatenkofi/memshelf-mcp) repo,
   seeded from this RFC.
3. ~~**Server topology.**~~ Resolved 2026-07-22 (#28): a separate MCP
   process. `memshelf-mcp` ships its own stdio server (`server.py`); the
   core imports docshelf as a library, while the server stays independently
   versioned and installable. Users who want both attach two config entries.
   See `docs/DECISIONS.md`.
4. **Episode segmentation automation** (v2+): can topic boundaries be
   detected well enough to *propose* cuts, or does explicit-only remain the
   right default?
5. **Cross-shelf federation**: a meta-INDEX over per-project shelves for
   "which project discussed X?" queries.
6. **Chat-project UX**: how far can the manual surface go without hooks —
   is a project-prompt-driven shelve loop reliable enough to document as
   supported?
7. ~~**Context advisor scope**~~ Resolved 2026-08-01 (#14): **heuristics
   only, and the window breakdown is an input, not something the tool goes
   looking for.** A library cannot see the window it is asked about, and
   parsing a host's `/context` output would work on one host and rot with its
   next release — so the advisor uses the split `shelve` and `rollup` already
   use: the model reports what only the model knows (which topics are in play,
   which are closed, roughly how big), the tool contributes what a
   self-assessment cannot — its own measured overhead, verification of
   "already shelved" claims against the actual episodes, net-of-standing-cost
   arithmetic, and a deterministic ranking. Deeper harness integration stays
   available as a *host adapter* that fills the same input, which is where a
   host-specific parser belongs (portability rule 2).
8. **Artifact mirror** (ROADMAP M3): publish INDEX (and episodes?) as
   private claude.ai artifacts for phone-side reading — worth the adapter,
   or does MCP-everywhere make it moot?
