# Account skill mirror — a source of record, not an install

`shelve` also exists as an **account skill on claude.ai**
(`skill_015TSeYxhyLywwZUunpKRZtS`, `creatorType: user`). The account
distributes it, not this repository: claude.ai pushes it to every signed-in
host — Claude Desktop lays it down inside the `anthropic-skills` plugin
bundle, agent containers under `~/.claude/skills/synced/<bucket>/`. Nothing in
git produced that text, and until 2026-09-14 nothing in git could be compared
with it (`claude-bus#45`).

`skills/shelve/SKILL.md` here is that copy, byte for byte, as measured below.
It is a **mirror of record**: it exists so the account copy has something to
be compared against.

| | |
|---|---|
| sha256 | `f45c70be04b51b0e67c4b3713a4b0c25ad4922c3d83eeb24cd2d60179dcb33c9` |
| bytes | 14038 |
| account `updatedAt` | `2026-08-22T05:59:45.907200Z` |
| taken from | `~/Library/Application Support/Claude/local-agent-mode-sessions/skills-plugin/58452a70-…/c953fc0c-…/skills/shelve/SKILL.md` (Claude Desktop, macOS, measured 2026-09-14) |

The same digest came out of an agent container on 2026-09-10, which is what
makes the two measurements one fact rather than two copies.

## What this is not

- **Not installable.** No plugin manifest lives here on purpose: two skills
  named `shelve` in one bundle is a collision. The installable copies are
  [`adapters/claude-code/skills/shelve/`](../claude-code/skills/shelve/) — the
  prompt-only fallback for hosts without the memshelf server — and the shelf
  repository's own `.claude/skills/shelve/`.
- **Not the upstream.** Editing this file does not reach claude.ai; the
  account copy is uploaded by hand there. A mirror going stale is precisely
  the failure the tact below turns from silent into loud.

## The tact

- `tests/test_shelve_account_mirror.py` pins the sha256, and on a host where
  the account copy is materialised it compares the two byte for byte.
- `adapters/claude-code/check-shelve-copies.sh --discover` now reaches the
  Desktop bundle, so the account copy is rule-checked where it lives instead
  of being reported as `none`.
- `claude-bus/tools/skill-copies` names this file as the copy's source in git.

## Refreshing it after an upload to claude.ai

The mirror follows the account copy, never the other way round:

```bash
cp ~/Library/Application\ Support/Claude/local-agent-mode-sessions/skills-plugin/*/*/skills/shelve/SKILL.md adapters/claude-account/skills/shelve/SKILL.md
shasum -a 256 adapters/claude-account/skills/shelve/SKILL.md
```

Put the new digest into `tests/test_shelve_account_mirror.py` in the same
commit. The constant is the record of what was uploaded; a commit that moves
the file without moving the constant is the drift it exists to catch.
