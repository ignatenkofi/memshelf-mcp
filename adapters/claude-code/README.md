# Claude Code adapter — installing the M0 kit

Three ways to get `/shelve` + the recall rule into your sessions, by how you
work. They compose — use several at once.

## 1. Self-instrumenting shelf (recommended; works everywhere)

Put the kit **inside the shelf repo itself**:

```text
my-memory-shelf/
├── CLAUDE.md                     ← recall + shelve + git rules
├── .claude/skills/shelve/SKILL.md
├── INDEX.md, POLICY.md, ledger.tsv, docs/…
```

Wherever the shelf is attached — a Cowork mount, a remote/web session via
`add_repo`, a plain local clone — Claude Code loads the repo's `CLAUDE.md`
and skills automatically. **Zero per-machine installation; the shelf
travels with its own instructions.** This is how `sqst-memshelf` is set up.

Best fit: users who don't keep projects on disk and work in Cowork or
remote GitHub sessions.

## 2. Personal (machine-level) install

Copy once on the machine where Cowork / Claude Code runs:

```bash
mkdir -p ~/.claude/skills/shelve
cp skills/shelve/SKILL.md ~/.claude/skills/shelve/
```

The skill is then available in **every** session on that machine, even when
no shelf repo is attached. Optionally add a one-liner to `~/.claude/CLAUDE.md`:

> If a memory shelf is attached to this session (repo with INDEX.md +
> POLICY.md + ledger.tsv), follow its CLAUDE.md recall/shelve rules.

Limitation: does not follow you into remote/web sessions — those get skills
only from attached repos (path 1) or plugins (path 3).

## 3. Plugin (the M1 answer)

This directory **is** an installable Claude Code plugin (`memshelf`). It bundles
the `/shelve` skill plus two hooks:

- **`SessionStart`** → injects the shelf `INDEX.md` as context, so recall works
  from the first turn (`hooks/session-start-index.sh`).
- **`SessionEnd` + `PreCompact`** → push the shelf so committed episodes survive
  an ephemeral container (`hooks/autopush.sh`) — **opt-in** via
  `MEMSHELF_AUTOPUSH=1`.

Install (local):

```bash
claude --plugin-dir /path/to/memshelf-mcp/adapters/claude-code
```

Configure via env:

- `MEMSHELF_ROOT` — path to the shelf (else the hooks fall back to the cwd when
  it looks like a shelf: `INDEX.md` + `ledger.tsv`).
- `MEMSHELF_AUTOPUSH=1` — enable the durability push. Set it in ephemeral cloud
  sessions; leave it unset on a persistent host, where you push manually.

**What the hooks deliberately do NOT do.** A hook is a shell command — it can't
run the model. So "shelve closed topics before compaction" and "write a session
digest" are **not** hooks: they need the LLM and stay the agent's job (the
`/shelve` skill + the recall rule in [`CLAUDE-md-snippet.md`](CLAUDE-md-snippet.md)).
`PreCompact` can't inject context and `SessionEnd` runs after the agent stops,
so those hooks are limited to the mechanical push. See
[`../../docs/DECISIONS.md`](../../docs/DECISIONS.md) and
[`../../docs/ROADMAP.md`](../../docs/ROADMAP.md).

## PII/secret pre-commit guard (recommended for every shelf)

The shelve tool redacts on its own write path, but a **hand edit, a stray agent
write, or a fix-up commit** reaches git with no check at all — on a repo whose
entire content is conversation memory, where git history makes a leak sticky.
`hooks/pre-commit` closes that gap: nothing lands in a commit without a scan.

Install it into a shelf (one line, from the shelf root):

```bash
# self-instrumenting shelf (the kit is vendored under <shelf>/hooks/):
ln -sf ../../hooks/pre-commit .git/hooks/pre-commit
# or point straight at the plugin checkout:
ln -sf /path/to/memshelf-mcp/adapters/claude-code/hooks/pre-commit .git/hooks/pre-commit
```

After that a commit is **refused** if a staged file carries a token- or
email-shaped string — whether it was written via `/shelve` or by hand (the
issue-#32 acceptance test). Two layers:

1. **Built-in shapes** (always on, no dependency): email / phone / token /
   env-secret, plus your shelf's `POLICY.patterns` (the same machine-readable
   pack the redaction pass and `doctor` read) and any `MEMSHELF_PII_PACK_DIR`.
2. **Name PII** via a pluggable scanner (`pii-mcp`). Names can't be caught by
   shapes, so if `pii-mcp` is **not installed the hook fails loud** rather than
   passing silently — it never depends on `pii-mcp` being deployed to be safe.

| Env var | Effect |
|---|---|
| `MEMSHELF_PII_SKIP=1` | Skip the guard once (git's `--no-verify` also works) |
| `MEMSHELF_PII_BUILTIN_ONLY=1` | Ephemeral-session downgrade: shapes only, names unchecked (loud) |
| `MEMSHELF_PII_PACK_DIR=DIR` | Load extra `*.patterns` packs (`kind<ws>regex`) |
| `MEMSHELF_PII_CMD="pii-mcp verify"` | Override the layer-2 scanner invocation |

Exit contract: `0` clean · `1` findings · `2` config error / scanner missing.
bash-3.2 compatible (macOS). Scaffolding this into `memshelf init` (a
`--with-hook` flag that installs it and sets `core.hooksPath`) is a planned
follow-up — it needs the adapter files shipped as package data.

## Chat projects (Claude Desktop / web)

No hooks there: paste the block from
[`CLAUDE-md-snippet.md`](CLAUDE-md-snippet.md) into the project's custom
instructions and attach the shelf's `INDEX.md`. Shelving is manual
(user-confirmed) on that surface.
