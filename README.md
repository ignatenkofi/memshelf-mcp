# memshelf-mcp

> Put your agent's memory on a shelf, hand it the index.

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

**Status: design phase → M0 validated.** No code yet — the architecture, manifest, and
roadmap live in [`docs/`](docs/) and are the current deliverable. Sibling
project of [docshelf-mcp](https://github.com/ignatenkofi/docshelf-mcp),
which provides the storage/index layer.

## What this will be

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

## Documents

| Doc | What it covers |
|---|---|
| [`docs/MANIFEST.md`](docs/MANIFEST.md) | Problem, the bet, hero scenarios, principles, non-goals |
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | Episode format, digest contract, storage modes, triggers, MCP tool surface, portability model, privacy, failure modes |
| [`docs/LANDSCAPE.md`](docs/LANDSCAPE.md) | Prior-art survey (2026-07), platform built-ins, positioning, risks |
| [`docs/ROADMAP.md`](docs/ROADMAP.md) | Milestones M0–M3 with exit criteria |
| [`docs/DECISIONS.md`](docs/DECISIONS.md) | Decision log |
| [`docs/M0.md`](docs/M0.md) | M0 experiment protocol (running): cases, token ledger, recall test |
| [`docs/examples/`](docs/examples/) | A worked episode file and a memory-shelf INDEX |
| [`adapters/claude-code/`](adapters/claude-code/) | M0 prompt-only kit: `/shelve` skill (live + import modes), recall-rule snippet |

## Origin

Designed as RFC-0001 in the docshelf-mcp repo
([#42](https://github.com/ignatenkofi/docshelf-mcp/pull/42),
[#43](https://github.com/ignatenkofi/docshelf-mcp/pull/43),
[#44](https://github.com/ignatenkofi/docshelf-mcp/pull/44)); this repo is the
project's home from 2026-07-13 on. The docshelf copy is frozen as a
historical snapshot.

## License

MIT — see [`LICENSE`](LICENSE).
