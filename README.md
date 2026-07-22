# memshelf-mcp

> Put your agent's memory on a shelf, hand it the index.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Status](https://img.shields.io/badge/status-M0%20complete%20%E2%86%92%20M1%20tools%20shipped-blue.svg)](docs/demo.md)
[![MCP](https://img.shields.io/badge/MCP%20server-shelve%20%C2%B7%20recall%20%C2%B7%20index%20%C2%B7%20search%20%C2%B7%20stats%20%C2%B7%20doctor-purple.svg)](src/memshelf_mcp/server.py)
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
