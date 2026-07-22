#!/usr/bin/env bash
# memshelf SessionEnd / PreCompact hook — push the shelf so committed episodes
# survive an ephemeral container. Opt-in (MEMSHELF_AUTOPUSH), mechanical,
# best-effort. It NEVER writes content: the agent already committed each episode
# via the shelve tool. "Shelve before compaction" and session digests need the
# LLM, so they stay the agent's job (the skill + recall rule), not this hook.
set -u

[ -n "${MEMSHELF_AUTOPUSH:-}" ] || exit 0 # off by default (persistent hosts push manually)

root="${MEMSHELF_ROOT:-}"
if [ -z "$root" ] && [ -f "INDEX.md" ] && [ -f "ledger.tsv" ]; then
  root="$PWD"
fi
if [ -z "$root" ] || [ ! -d "$root/.git" ]; then exit 0; fi
if ! git -C "$root" remote get-url origin >/dev/null 2>&1; then exit 0; fi

# Best-effort: a failed push must never break the session.
git -C "$root" push origin HEAD >/dev/null 2>&1 || true
exit 0
