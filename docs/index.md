---
layout: default
title: memshelf-mcp
description: "Put your agent's memory on a shelf, hand it the index."
---

# memshelf-mcp

> Put your agent's memory on a shelf, hand it the index.

[![PyPI](https://img.shields.io/pypi/v/memshelf-mcp.svg)](https://pypi.org/project/memshelf-mcp/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://github.com/ignatenkofi/memshelf-mcp/blob/main/LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/downloads/)
[![MCP](https://img.shields.io/badge/MCP-compatible-purple.svg)](https://modelcontextprotocol.io/)
[![Sibling: docshelf](https://img.shields.io/badge/sibling-docshelf--mcp-green.svg)](https://ignatenkofi.github.io/docshelf-mcp/)

An [MCP](https://modelcontextprotocol.io/) server that gives a long-running AI
agent an episodic memory it can actually afford: a tiny `INDEX.md` in context,
Markdown episodes on a local git shelf, exact sections recalled on demand.

---

## The problem

Long agent sessions (Claude Code and friends) accumulate history that stops
paying rent:

- ❌ Closed topics keep occupying tens of thousands of tokens, forty minutes
  after anyone needed them.
- ❌ Auto-compaction reclaims the space by silently dropping detail — the
  rejected alternative, the reason, the open thread.
- ❌ Vendor built-in memories are black boxes: nothing to grep, diff, review,
  or carry to another vendor.

The founding incident: a whole season of work transcripts (April–June) turned
out to exist **nowhere** — not in the chat export, not in rotated logs. Memory
that isn't shelved while the context exists is memory lost.

## The fix

**memshelf** applies the [docshelf](https://ignatenkofi.github.io/docshelf-mcp/)
pattern — tiny index in context, bodies fetched on demand — to the agent's own
working memory:

1. **Shelve** — closed topics, research dumps, and bulky tool output become
   Markdown **episodes** on a local shelf (a git repo with no remote by
   default).
2. **Digest** — each episode carries an LLM-written, contract-validated
   digest: decisions, rejected alternatives, artifacts, open threads. A weak
   digest is rejected with the exact fix printed.
3. **Recall** — the agent keeps only `INDEX.md` (kilobytes) + digests in
   context and fetches exact sections via INDEX → SUBINDEX navigation
   over MCP.

Positioning in one sentence: *claude-mem's loop, git's substrate, docshelf's
navigation* — episodic memory you can grep, diff, review, and carry between
hosts. The tool is public; the memory never is.

---

## Measured, not promised

One week of dogfooding on the live shelf:

| Measure | Result |
|---|---|
| Episodes on the shelf | 34 |
| Standing cost in every session (INDEX + digests) | ~8.6K tokens |
| Shelved mass those episodes replace | ~1.9M tokens — **≈220 : 1** |
| One question answered from memory | ~1.8K tokens (INDEX + one episode) |
| Recall test: fresh agent, INDEX path only | **5 / 5** — zero misses, zero over-fetch |

Full numbers and methodology (including the chars/4 caveat): [demo.md](demo.md).
The same week as a one-page infographic:
[case-b-week-report.html](assets/case-b-week-report.html).

---

## Install

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

Or from the shell — same loop, no MCP:

```bash
pip install memshelf-mcp
memshelf init --shelf ~/my-shelf --name "My working memory"
```

Full tool surface (14 verbs: `init` · `import` · `shelve` · `lint_digest` ·
`recall` · `index` · `search` · `stats` · `advise` · `rebuild` · `rollup` ·
`purge` · `resolve` · `doctor`) and the rules they enforce:
[README](https://github.com/ignatenkofi/memshelf-mcp#readme).

---

## One memory, multiple AIs

The memory is vendor-portable, and that is a **measured fact**, not a design
intention: the same live shelf has been read and cross-written by Claude Code
(Anthropic) and Gemini CLI (Google) through one `shelf-spec` server. Protocol
and field notes: [portability.md](portability.md).

---

## Documents

| Doc | What it covers |
|---|---|
| [MANIFEST.md](MANIFEST.md) | Problem, the bet, hero scenarios, principles, non-goals |
| [ARCHITECTURE.md](ARCHITECTURE.md) | Episode format, digest contract, storage modes, MCP tool surface, privacy, failure modes |
| [LANDSCAPE.md](LANDSCAPE.md) | Prior-art survey (2026-07), platform built-ins, positioning, risks |
| [ROADMAP.md](ROADMAP.md) | Milestones M0–M3 with exit criteria |
| [DECISIONS.md](DECISIONS.md) | Decision log |
| [M0.md](M0.md) | The zero-code experiment: cases, token ledger, recall test |
| [demo.md](demo.md) | Measured numbers from the dogfood shelf |
| [portability.md](portability.md) | The cross-vendor experiment |
| [examples](https://github.com/ignatenkofi/memshelf-mcp/tree/main/docs/examples) | A worked episode file and a memory-shelf INDEX |

---

## Related

- **[docshelf-mcp](https://ignatenkofi.github.io/docshelf-mcp/)** — the sibling
  project and storage layer: PDFs/Markdown → document shelves with the same
  index-and-fetch economics. memshelf was born as RFC-0001 in its repo.

Source on GitHub: [ignatenkofi/memshelf-mcp](https://github.com/ignatenkofi/memshelf-mcp) · MIT license.
