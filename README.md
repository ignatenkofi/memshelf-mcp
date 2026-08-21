# memshelf-mcp

> Put your agent's memory on a shelf, hand it the index.

[![PyPI](https://img.shields.io/pypi/v/memshelf-mcp)](https://pypi.org/project/memshelf-mcp/)
[![Python](https://img.shields.io/pypi/pyversions/memshelf-mcp)](https://pypi.org/project/memshelf-mcp/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Docs](https://img.shields.io/badge/docs-ignatenkofi.github.io%2Fmemshelf--mcp-blue.svg)](https://ignatenkofi.github.io/memshelf-mcp/)
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
git repo with **no remote configured**. The tool is public; the memory
never is.

## Measured, not promised

One week of dogfooding on the live shelf — full numbers and methodology in
[`docs/demo.md`](docs/demo.md):

| Measure | Result |
|---|---|
| Episodes on the shelf | 34 |
| Standing cost in every session (INDEX + digests) | ~8.6K tokens |
| Shelved mass those episodes replace | ~1.9M tokens — **≈220 : 1** |
| One question answered from memory | ~1.8K tokens (INDEX + one episode) |
| Recall test: fresh agent, INDEX path only | **5 / 5** — zero misses, zero over-fetch |

Tokens are counted as chars/4 everywhere, so the *ratios* are
estimator-independent; absolute counts move with the tokenizer.

## Quick start

As an **MCP server**:

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

As a **Claude Desktop extension** — [`adapters/claude-desktop/`](adapters/claude-desktop/):
an `.mcpb` bundle installed from *Settings → Extensions*, with a **Default
shelf** setting so calls need not repeat the path. Nothing has to be installed
alongside it — not even Python.

As a **Claude Code plugin** — [`adapters/claude-code/`](adapters/claude-code/):
a `/shelve` skill plus SessionStart / SessionEnd / PreCompact hooks.

Or from the **shell** (`pip install memshelf-mcp`, Python ≥ 3.10) — the same
loop, no MCP:

```bash
memshelf init   --shelf ~/my-shelf --name "My working memory"
memshelf shelve --shelf ~/my-shelf --slug 2026-07-23-topic --kind topic \
  --digest "What was decided, what was rejected and why, what stays open." \
  --section "Decisions=What was decided, and what was rejected instead — one line each."
memshelf recall --shelf ~/my-shelf --id 2026-07-23-topic --section Decisions --log
memshelf stats  --shelf ~/my-shelf   # claimed + realized savings
memshelf doctor --shelf ~/my-shelf   # exit 1 on integrity errors
```

## Tool surface

One verb per job; the same names over MCP (`memshelf_*`) and in the CLI:

| Tool | What it does |
|---|---|
| `init` | Create (or top up) a memory shelf: docshelf layout, fixed categories |
| `shelve` | Offload one closed topic as a durable, indexed episode; `--amend` rewrites in place |
| `lint_digest` | Validate a digest against the contract without touching the shelf |
| `import` | Retro-shelve a whole exported dialog without pulling it through context |
| `index` | Return the shelf INDEX — the small recall entry point |
| `recall` | Fetch an episode by id, or a single `## Section` of it |
| `search` | Grep the shelf; returns matching episodes |
| `stats` | The shelf's token economy: standing cost vs shelved mass, claimed vs realized |
| `advise` | What your context is made of and what you could put down — proposals only |
| `rebuild` | Regenerate every derived file from the episodes |
| `rollup` | Archive a period behind one digest-of-digests |
| `purge` | Drop episodes past `retain_until`, then reindex — dry run by default |
| `resolve` | Settle multi-writer conflicts: regenerate derived, union the recall log |
| `doctor` | Diagnose: episode schema, digest contract at rest, secret shapes, index bloat |

## The rules the tools enforce

**The digest is a contract, not a convention.** It is the only thing read at
recall before fetching a body, so a weak one devalues the whole episode.
`lint_digest` runs the same validator as `shelve` with no side effects
(`--strict` turns warnings into failures); errors block a shelve, warnings do
not — a pure reference digest legitimately carries no decision marker. A
rejected digest is a feature: the tool prints exactly what to fix and writes
nothing.

**`--amend` re-runs the whole pipeline** — redaction, the digest contract,
composition — so an amended episode is exactly as guarded as a fresh one,
which a hand-edit of the file never is. Amending a slug that is not on the
shelf is an error, not a create.

**The episode is the source; everything else is output.** `ledger.tsv`,
`INDEX.md`, `stats.svg` and each category's `.meta.json` are derived:
`shelve` writes and commits the episode alone, `rebuild` renders the rest —
delete all four and `rebuild` restores them byte-identically. That is what
makes two sessions shelving in parallel a non-event: the merge is clean by
construction. On a shared shelf, let a bot own the derived files on `main` —
ready-to-copy workflows in [`adapters/shelf-repo/`](adapters/shelf-repo/);
`rebuild --adopt` migrates an older shelf once, `rebuild --check` is the
CI guard.

Two consequences worth stating plainly, because getting them wrong costs a
merge conflict:

* **`doctor` reports `no-ledger-row` and `stale-index` immediately after a
  correct `shelve` — on every branch, `main` included.** Nothing is broken:
  the episode is written, the derived files are not rendered yet. They clear on
  the next `rebuild` — the bot's run, on a shelf that has one.
* **Do not rebuild and commit the derived files by hand to silence them.**
  That is exactly the conflict class the split removes: a hand-regenerated
  `ledger.tsv`/`INDEX.md`/`stats.svg` meets the bot's, and the merge stops
  being clean by construction. Wait for the renderer; on a shelf without a bot,
  run `memshelf rebuild --shelf .` as its own step.

If those warnings persist for a *day* while episodes keep arriving, that is a
different state — the renderer is not lagging, it is stopped — and `doctor`
says so separately, as `derived-stale` at error severity.

**`advise` proposes, never writes.** It answers the question the project was
founded on — *a dead topic has been occupying 30K tokens for forty minutes*.
The tool cannot see your window, so you tell it what is in there:

```bash
memshelf advise --shelf ~/my-shelf \
  --occupant 'auth refactor=42000,closed' \
  --occupant 'search dump=9000,idle=18' \
  --occupant 'Case B verdict=12000,live,episode=2026-07-22-case-b-verdict'
```

Three things keep it honest: it **counts itself** (INDEX + digests are in the
report, not left out of it), it **verifies** `episode=` claims before
proposing a drop, and it **reports net** — a topic too small to pay for its
own digest is not proposed at all.

**Rollup shrinks navigation and nothing else.** When INDEX grows into a real
share of your window, `rollup` collapses a period into one digest-of-digests
and moves the originals to `archive/` — still reachable by `recall` and
`search`, every ledger row intact. The rollup digest is yours, not the tool's:
synthesizing a quarter is the part a tool cannot do.

**`index-bloat` is not what a rollup is for.** INDEX lists your episodes, so
its size grows with the shelf by design; its budget grows with the shelf too
(`INDEX_BASE_TOKENS + INDEX_TOKENS_PER_ENTRY × listed`). Over budget therefore
means entries are *overpriced*, never that there are too many of them — so
`doctor` reports the cost of one line, and the fix is to trim it and
`rebuild`. A rollup would remove entries and their allowance together and
leave the price where it was. Having the two paired the other way is what made
"archive a third of your memory" the standard way to silence a formatting
problem.

**`purge` deletes the working tree, not history.** Retention is opt-in per
episode (`--retain-until`); `purge` is a dry run until `--apply` — and even
then git history still has the file. Real erasure is a deliberate
`filter-repo` pass over the whole repository, never a side effect of a tool
call, and the purge report says so.

**`resolve` regenerates derived paths, never merges them** — a derived file
has no history, only a current correct value. The one file it unions is
`recall-log.tsv`, because a recall is an event, not a fact about the
episodes. Conflicting *episodes* are content, not mechanics: `resolve`
reports them and steps aside.

The design rationale behind each rule lives in
[`docs/DECISIONS.md`](docs/DECISIONS.md) and
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

## One memory, multiple AIs

The memory is **vendor-portable, and that is a measured fact**, not a design
intention: the same live shelf has been read and cross-written by Claude Code
(Anthropic) and Gemini CLI (Google) through one `shelf-spec` server —
protocol and field notes in [`docs/portability.md`](docs/portability.md).

## Status

M0 complete: the pattern was validated with zero code on a live shelf —
retro-import of months of material, then a week of shelve-at-close
([`docs/M0.md`](docs/M0.md)). M1 shipped the server/CLI that enforces it,
plus the Claude Code plugin. Next milestones with exit criteria:
[`docs/ROADMAP.md`](docs/ROADMAP.md); release history:
[`CHANGELOG.md`](CHANGELOG.md).

## Documents

Rendered site: <https://ignatenkofi.github.io/memshelf-mcp/> — including the
week-report infographic from the dogfood shelf.

| Doc | What it covers |
|---|---|
| [`docs/MANIFEST.md`](docs/MANIFEST.md) | Problem, the bet, hero scenarios, principles, non-goals |
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | Episode format, digest contract, storage modes, triggers, MCP tool surface, portability model, privacy, failure modes |
| [`docs/LANDSCAPE.md`](docs/LANDSCAPE.md) | Prior-art survey (2026-07), platform built-ins, positioning, risks |
| [`docs/ROADMAP.md`](docs/ROADMAP.md) | Milestones M0–M3 with exit criteria |
| [`docs/DECISIONS.md`](docs/DECISIONS.md) | Decision log |
| [`docs/M0.md`](docs/M0.md) | M0 experiment protocol and results: cases, token ledger, recall test |
| [`docs/demo.md`](docs/demo.md) | Measured numbers from the dogfood shelf: compression, recall test, doctor findings |
| [`docs/portability.md`](docs/portability.md) | One memory, multiple AIs: the cross-vendor experiment |
| [`docs/examples/`](docs/examples/) | A worked episode file and a memory-shelf INDEX |
| [`adapters/claude-code/`](adapters/claude-code/) | Claude Code plugin: `/shelve` skill + SessionStart/SessionEnd/PreCompact hooks |
| [`adapters/claude-desktop/`](adapters/claude-desktop/) | Claude Desktop `.mcpb` extension: builder, bundle checker, default-shelf setting |

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
- The dogfood memory shelf is a private repo — by design
  ([MANIFEST](docs/MANIFEST.md) principle 5).

## License

MIT — see [`LICENSE`](LICENSE).

---

mcp-name: io.github.ignatenkofi/memshelf-mcp
