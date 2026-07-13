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

The proper "install into all of Claude at once" is a **plugin**: skill +
hooks (`SessionStart` inject INDEX, `PreCompact` shelve, `SessionEnd`
session digest) packaged and installed once. That's exactly M1's Claude
Code adapter deliverable — tracked in [`../../docs/ROADMAP.md`](../../docs/ROADMAP.md).

## Chat projects (Claude Desktop / web)

No hooks there: paste the block from
[`CLAUDE-md-snippet.md`](CLAUDE-md-snippet.md) into the project's custom
instructions and attach the shelf's `INDEX.md`. Shelving is manual
(user-confirmed) on that surface.
