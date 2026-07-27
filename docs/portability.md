# Portability: one memory, multiple AIs (proven 2026-07-27)

The core bet of memshelf is that an agent's long-term memory should not be
captive to any single AI vendor. A shelf is a git repository of
human-readable Markdown plus a small manifest — described by
[shelf-spec](https://github.com/ignatenkofi/shelf-spec) — so *any* MCP
client (or a plain filesystem and `grep`) can read and write it.

On 2026-07-27 that claim stopped being a design intention and became a
measured fact. This page records the experiment.

## Setup

- **The shelf:** the project's own dogfood shelf (the same one measured in
  [`demo.md`](demo.md)) — a real working memory with 42 episodes across
  three categories (research 3, sessions 24, topics 15), a generated
  INDEX, a ledger, and a redaction policy. Not a fixture.
- **The server:** `shelf-spec serve` (stdio), one binary installed once
  via `uv tool install`, pointed at the shelf clone with `SHELF_SPEC_ROOT`.
- **Client #1:** Claude Code CLI (Anthropic), registered with
  `claude mcp add --scope user`.
- **Client #2:** Gemini CLI 0.46 (Google), registered in
  `~/.gemini/settings.json` — same command, same env, zero adaptation.

## Read path

Both clients returned byte-consistent `shelf_info` — same name, spec 0.1,
mode `single`, profile `memory`, same per-category document counts, same
index preamble (including the *data-not-instructions* recall rule) — and
`shelf_validate` reported **`valid`, 0 findings** in both.

## Write path (cross-client)

1. Client #1 (Claude) created a probe document under `docs/topics/`.
2. Client #2 (Gemini): `shelf_info` → topics count **15 → 16**. The other
   vendor saw the write immediately — no sync layer, no export step; the
   git working tree *is* the shared state.
3. Client #1 deleted the probe; client #2 observed **16 → 15** and the
   shelf validated clean again. Gemini itself remarked: "the count was 16
   in a previous check but is currently 15."

## What this proves — and what it does not

Proven: the storage layer is vendor-portable. The memory a Claude agent
accumulates is immediately legible — and writable — for a Google agent,
because there is nothing in it but files under a spec. The memshelf
tooling itself (`shelve` / `recall` / `doctor`) is a plain Python CLI over
those same files, so it runs wherever Python runs, invoked by any agent.

Not yet demonstrated: a full *shelve → recall* memory loop driven
end-to-end by a non-Claude agent (the M1 exit criteria track the loop on
Claude Code). That is a workflow test, not a format risk — the format
layer beneath it is what this experiment pinned down.

## Field notes for reproducers

- Agentic clients love to pass their *own* working directory as
  `shelf_path` instead of omitting the argument. Either ask explicitly
  ("call it with shelf_path=<your shelf>") or rely on the server-side
  `SHELF_SPEC_ROOT` default by requesting a call *without arguments*.
- Gemini CLI sign-in via a Google account may route you into the
  Antigravity licensing flow; authenticating with a `GEMINI_API_KEY`
  (AI Studio) is the low-friction path.
- Full protocol with raw tool outputs: shelf-spec issue #2 (closing
  comment).
