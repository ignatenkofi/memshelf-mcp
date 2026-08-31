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
#
# Two destinations (#118). Default: push the current branch; if that is
# refused — a ruleset that requires PRs on main, a clone the derived bot
# outran — fall back to a rescue branch, because by the time the refusal
# arrives this session is over and a local-only commit dies with the
# container. MEMSHELF_AUTOPUSH_MODE=branch skips the doomed direct push
# entirely (the documented setting for ruleset shelves).
if [ "${MEMSHELF_AUTOPUSH_MODE:-}" != "branch" ]; then
  git -C "$root" push origin HEAD >/dev/null 2>&1 && exit 0
fi
rescue="shelve/autopush-$(date -u +%Y%m%d-%H%M%S)"
git -C "$root" push origin "HEAD:refs/heads/$rescue" >/dev/null 2>&1 || true
exit 0
