# memshelf-mcp

> Put your agent's memory on a shelf, hand it the index.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Status](https://img.shields.io/badge/status-M0%20complete%20%E2%86%92%20M1%20tools%20shipped-blue.svg)](docs/demo.md)
[![MCP](https://img.shields.io/badge/MCP%20server-shelve%20%C2%B7%20recall%20%C2%B7%20index%20%C2%B7%20search%20%C2%B7%20stats%20%C2%B7%20resolve%20%C2%B7%20doctor-purple.svg)](src/memshelf_mcp/server.py)
[![Sibling: docshelf](https://img.shields.io/badge/sibling-docshelf--mcp-green.svg)](https://github.com/ignatenkofi/docshelf-mcp)

```text
                              _          _  __
 _ __ ___   ___ _ __ ___  ___| |__   ___| |/ _|
| '_ ` _ \ / _ \ '_ ` _ \/ __| '_ \ / _ \ | |_
| | | | | |  __/ | | | | \__ \ | | |  __/ |  _|
|_| |_| |_|\___|_| |_| |_|___/_| |_|\___|_|_|
  ____________________________________________
 | INDEX >> | E-01 | E-02 | E-03 | E-04 | ... |
 |__________|______|______|______|______|_____|
        memory shelves for AI agents
```

**Status: M0 complete (Cases A + B), M1 tool surface shipped.** The pattern
was validated with zero code on a live shelf — measured numbers in
[`docs/demo.md`](docs/demo.md) — and the M1 server/CLI now enforces it:
`memshelf_shelve` / `recall` / `index` / `search` / `stats` / `doctor`, plus a
Claude Code plugin ([`adapters/claude-code/`](adapters/claude-code/)). Sibling
project of [docshelf-mcp](https://github.com/ignatenkofi/docshelf-mcp),
which provides the storage/index layer.

## What this is

Long-running agent sessions burn tokens re-sending history and lose detail
to lossy auto-compaction. **memshelf** applies the
[docshelf](https://github.com/ignatenkofi/docshelf-mcp) pattern — tiny index
in context, bodies fetched on demand — to the agent's own working memory:

1. Closed conversation topics, research dumps, and bulky tool output are
   offloaded to a local shelf as Markdown **episodes**.
2. Each episode carries an LLM-written, contract-validated **digest** that
   preserves decisions, rejected alternatives, artifacts, and open threads.
3. The agent keeps only `INDEX.md` (kilobytes) + digests in context and
   **recalls** exact sections via INDEX → SUBINDEX navigation over MCP.

Positioning in one sentence: *claude-mem's loop, git's substrate, docshelf's
navigation* — episodic memory you can grep, diff, review, and carry between
hosts. Private and local by default: the standard storage mode is a local
git repo with **no remote configured**.

## Quick start

As an **MCP server** (tools `memshelf_init` / `shelve` / `recall` / `index` /
`search` / `stats` / `rebuild` / `rollup` / `purge` / `resolve` / `doctor`):

```bash
# Claude Code
claude mcp add memshelf -- uvx memshelf-mcp
```

```jsonc
// Claude Desktop (claude_desktop_config.json)
{
  "mcpServers": {
    "memshelf": { "command": "uvx", "args": ["memshelf-mcp"] }
  }
}
```

Or from the **shell** (`pip install memshelf-mcp`) — the same loop, no MCP:

```bash
memshelf init   --shelf ~/my-shelf --name "My working memory"
memshelf shelve --shelf ~/my-shelf --slug 2026-07-23-topic --kind topic \
  --digest "What was decided, what was rejected and why, what stays open." \
  --section "Decisions=..."
memshelf recall  --shelf ~/my-shelf --id 2026-07-23-topic --section Decisions --log
memshelf rebuild --shelf ~/my-shelf  # render the derived files from the episodes
memshelf stats   --shelf ~/my-shelf  # claimed + realized savings
memshelf advise  --shelf ~/my-shelf  # where the window went; proposals only
memshelf doctor  --shelf ~/my-shelf  # exit 1 on integrity errors
```

### The episode is the source; everything else is output

`ledger.tsv`, `INDEX.md`, `stats.svg` and each category's `.meta.json` are
**derived** (#58): `shelve` writes and commits the episode alone, and
`memshelf rebuild` renders the four from `docs/`. That is what makes two
sessions shelving in parallel a non-event — they no longer touch the same four
files in the same places, so the merge is clean by construction. Delete all
four and `rebuild` restores them byte-identically. "Writes the episode alone"
is literal: `shelve` restores the category's `.meta.json` sidecar to the state
it found it in, so a clean tree after a shelve holds exactly one new file
(#69).

On a shared shelf, let a bot own them on `main` and guard PRs against touching
derived paths — ready-to-copy workflows are in
[`adapters/shelf-repo/`](adapters/shelf-repo/). A shelf written before this
split adopts it once:

```bash
memshelf rebuild --shelf ~/my-shelf --adopt   # move date/notes/title into the episodes
memshelf rebuild --shelf ~/my-shelf --check   # the guard: exit 1 if anything drifted
```

### Where did my window go?

`memshelf advise` answers the question the project was founded on — *a dead
topic has been occupying 30K tokens for forty minutes* — and answers it with
**proposals**. It writes nothing.

The tool cannot see your window, so you tell it what is in there; it does the
part you cannot do about yourself:

```bash
memshelf advise --shelf ~/my-shelf \
  --occupant 'CLAUDE.md stack=18000,kind=instructions' \
  --occupant 'auth refactor=42000,closed' \
  --occupant 'current work=51000,live' \
  --occupant 'search dump=9000,idle=18' \
  --occupant 'Case B verdict=12000,live,episode=2026-07-22-case-b-verdict'
```

You get a breakdown — static overhead / **memshelf's own cost** / live /
reclaimable — and ranked proposals: `shelve` the closed topic and the idle
dump, `drop` the one that is already on the shelf (recall it if you need it),
`rollup` when INDEX itself is what got fat. Run it with no occupants at all
for the first-run view of the shelf; it will say the window side is missing
rather than report it clean.

Three things keep it honest:

- **It counts itself.** INDEX + digests are what memshelf takes out of every
  session, and that number is in the report, not left out of it.
- **It verifies "already shelved".** Claim an `episode=` that isn't on the
  shelf and it refuses the drop out loud — that is the one mistake here that
  destroys work.
- **It reports net.** Shelving adds a digest and an INDEX line to every later
  session, so proposals subtract that, and a topic too small to pay for its
  own digest is not proposed at all.

### Keeping INDEX readable as the shelf grows

`INDEX.md` is the one file that rides in *every* session, so it grows with the
episode count while the per-session budget does not. `doctor` warns
(`index-bloat`) once it crosses the budget; two mechanics answer that warning
(#15):

```bash
# Collapse a period into one digest-of-digests; originals move to archive/
memshelf rollup --shelf ~/my-shelf --slug 2026-Q1-rollup --until 2026-03-31 \
  --display-title "Роллап Q1" \
  --digest "What the quarter decided, what it rejected, what stays open."

# Retention is opt-in per episode, and purge is a dry run by default
memshelf shelve --shelf ~/my-shelf ... --retain-until 2027-01-01
memshelf purge  --shelf ~/my-shelf            # list what expired
memshelf purge  --shelf ~/my-shelf --apply    # delete it, then reindex
```

A rollup shrinks **navigation and nothing else**: the originals move into
`archive/` — a sub-shelf with its own INDEX, outside the parent's `docs/` —
so N INDEX lines become one. Nothing is deleted, `recall --id` and `search`
still reach them, and every ledger row survives, because an archived episode
still holds the mass it saved. The rollup episode lists every id it hid.

The rollup *digest* is yours, not the tool's: synthesizing a quarter of
digests is the part a tool cannot do, so it takes the same digest contract
`shelve` enforces.

`purge` deletes the working-tree file — **git history still has it**. Real
erasure is a deliberate `filter-repo` pass over the whole repository, never a
side effect of a tool call, and the purge report says so.

`resolve` stays for what remains genuinely conflicting — the same episode
written on both sides, or a shelf that has not adopted the bot yet:

```bash
memshelf resolve --shelf ~/my-shelf            # regenerate derived, union the recall log, doctor
memshelf resolve --shelf ~/my-shelf --commit   # same + complete the merge commit
```

A conflict in a derived path is resolved by **regenerating** it, never by
merging the two sides: a derived file has no history, only a current correct
value, so a union of two versions is not the sum of two truths (#64). The one
file `resolve` still merges is `recall-log.tsv` — nothing regenerates a recall
log, because a recall is an event, not a fact about the episodes.

Conflicting *episodes* are content, not mechanics — `resolve` reports
them and steps aside.

A rejected digest is a feature: the tool prints exactly what to fix and
writes nothing. Measured results from a week of dogfooding are in
[`docs/demo.md`](docs/demo.md).

The memory is **vendor-portable, and that is now a measured fact**, not a
design intention: the same live shelf has been read and cross-written by
Claude Code (Anthropic) and Gemini CLI (Google) through one `shelf-spec`
server — protocol and field notes in [`docs/portability.md`](docs/portability.md).

## Documents

| Doc | What it covers |
|---|---|
| [`docs/MANIFEST.md`](docs/MANIFEST.md) | Problem, the bet, hero scenarios, principles, non-goals |
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | Episode format, digest contract, storage modes, triggers, MCP tool surface, portability model, privacy, failure modes |
| [`docs/LANDSCAPE.md`](docs/LANDSCAPE.md) | Prior-art survey (2026-07), platform built-ins, positioning, risks |
| [`docs/ROADMAP.md`](docs/ROADMAP.md) | Milestones M0–M3 with exit criteria |
| [`docs/DECISIONS.md`](docs/DECISIONS.md) | Decision log |
| [`docs/M0.md`](docs/M0.md) | M0 experiment protocol and results (complete): cases, token ledger, recall test |
| [`docs/demo.md`](docs/demo.md) | Measured numbers from the dogfood shelf: compression, recall test, doctor findings |
| [`docs/portability.md`](docs/portability.md) | **One memory, multiple AIs** — the 2026-07-27 experiment: the dogfood shelf read and written by Claude Code (Anthropic) and Gemini CLI (Google) through the same shelf-spec server |
| [`docs/examples/`](docs/examples/) | A worked episode file and a memory-shelf INDEX |
| [`adapters/claude-code/`](adapters/claude-code/) | Claude Code plugin: `/shelve` skill + SessionStart/SessionEnd/PreCompact hooks |

## Origin

Designed as RFC-0001 in the docshelf-mcp repo
([#42](https://github.com/ignatenkofi/docshelf-mcp/pull/42),
[#43](https://github.com/ignatenkofi/docshelf-mcp/pull/43),
[#44](https://github.com/ignatenkofi/docshelf-mcp/pull/44)); this repo is the
project's home from 2026-07-13 on. The docshelf copy is frozen as a
historical snapshot.

## Related projects

- **[docshelf-mcp](https://github.com/ignatenkofi/docshelf-mcp)** — the
  sibling project and storage layer: PDFs/Markdown → chat-project-friendly
  document shelves with the same index-and-fetch economics
  ([measured](https://github.com/ignatenkofi/docshelf-mcp/blob/main/docs/demo.md):
  ~3.7K tokens vs 1.2M per question). memshelf was born as
  [RFC-0001](https://github.com/ignatenkofi/docshelf-mcp/tree/main/docs/rfc/0001-memshelf)
  in its repo and reuses its splitter/indexer/read/search verbatim.
- The dogfood memory shelf is a private repo — by design (MANIFEST
  principle 5): the tool is public, the memory never is.

## License

MIT — see [`LICENSE`](LICENSE).

---

mcp-name: io.github.ignatenkofi/memshelf-mcp
