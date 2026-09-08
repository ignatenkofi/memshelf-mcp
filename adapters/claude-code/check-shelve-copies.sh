#!/usr/bin/env bash
# check-shelve-copies.sh — prove that a copy of the shelve skill stages by path.
#
# Four copies of the shelve skill drifted for sixteen days on one defect —
# `git add -A` in step 7 — because nothing compared them (claude-bus#21). The
# acceptance there was first written as "grep must find no `git add -A`", and
# that form colours exactly the repaired copies red: they name the command in
# order to forbid it. So this asserts two things that survive that trap:
#
#   1. no sentence PRESCRIBES a blanket stage — `git add -A|--all|-u|--update|.`
#      or `git commit -a|-am|--all` — inside a fenced code block (always a
#      command) or in prose without a negation in the same sentence
#      (never / not / no / don't / instead of / никогда / нельзя / не / запрещ…);
#   2. at least one line stages the episode by path: `git add [--] …docs/…`.
#
# Usage: check-shelve-copies.sh <SKILL.md>...
#        check-shelve-copies.sh --discover [<SKILL.md>...]
#   exit 0 — every copy passes; 1 — a copy fails or is missing (the report
#   names the file and the line); 2 — nothing to compare (no arguments, or
#   `--discover` on a host that exposes no copy at all).
#
# `--discover` exists because the acceptance in claude-bus#21 asks for a
# comparison "you can run with a command", and the command that takes paths
# demands the very knowledge that was missing: where the copies are. The issue
# itself could not locate the fourth one — `~/.claude/plugins/marketplaces` was
# empty on the owner's machine while the skill was live in the session. It is
# materialised in agent containers under `~/.claude/skills/synced/<id>/`, which
# no one thought to look at. So the search list lives here, in code, and every
# location it tried is printed on stderr: "found none" must be readable as
# "this host exposes none", never as "clean".
set -u

usage() {
  echo "usage: $0 <SKILL.md>..." >&2
  echo "       $0 --discover [<SKILL.md>...]" >&2
}

discover=0
paths=""

# add_path <path> — append once, physical when the directory resolves.
# A path whose directory does NOT exist is kept verbatim: someone named it and
# expects a verdict on it, and normalising it away would turn "no such file"
# into silence.
add_path() {
  _ap="$1"
  _ap_dir="$(cd "$(dirname "$_ap")" 2>/dev/null && pwd -P)"
  [ -n "$_ap_dir" ] && _ap="$_ap_dir/$(basename "$_ap")"
  if printf '%s' "$paths" | grep -qxF -- "$_ap"; then return 0; fi
  paths="$paths$_ap
"
  return 0
}

# `"$@"` under `set -u` with no positional parameters is a known bash 3.2
# trap, and 3.2 is the shell this repo's siblings are written against.
if [ "$#" -gt 0 ]; then
  for arg in "$@"; do
    case "$arg" in
      --discover) discover=1 ;;
      -h|--help) usage; exit 2 ;;
      -*) echo "unknown option: $arg" >&2; usage; exit 2 ;;
      *) add_path "$arg" ;;
    esac
  done
fi

if [ "$discover" -eq 1 ]; then
  self_dir="$(cd "$(dirname "$0")" && pwd -P)"
  # Where copies of this skill actually turn up. The packaged one goes first
  # so its absence is loud when the script runs from a stray checkout.
  locations="$self_dir/skills/shelve/SKILL.md
${HOME:-}/.claude/skills/shelve/SKILL.md
${HOME:-}/.claude/skills/synced/*/shelve/SKILL.md
${HOME:-}/.claude/plugins/*/skills/shelve/SKILL.md
${HOME:-}/.claude/plugins/*/*/skills/shelve/SKILL.md
${HOME:-}/.claude/plugins/marketplaces/*/*/skills/shelve/SKILL.md
$PWD/.claude/skills/shelve/SKILL.md"
  [ -n "${MEMSHELF_ROOT:-}" ] && locations="$locations
$MEMSHELF_ROOT/.claude/skills/shelve/SKILL.md"

  # IFS stays on newline for the whole block, and globbing is off while the
  # list is split: a home directory may contain a space, and default splitting
  # would tear such a location in half — first here, then again in the inner
  # `for`, which is why the restore comes after both loops and not between
  # them. Pathname expansion yields one word per match either way; that part
  # does not depend on IFS.
  _old_ifs="$IFS"
  IFS='
'
  set -f
  set -- $locations
  set +f

  echo "looked in:" >&2
  for pattern in "$@"; do
    [ -n "$pattern" ] || continue
    hits=0
    for match in $pattern; do
      if [ -f "$match" ]; then
        hits=$((hits + 1))
        add_path "$match"
        echo "  found $match" >&2
      fi
    done
    [ "$hits" -eq 0 ] && echo "  none  $pattern" >&2
  done
  IFS="$_old_ifs"
fi

if [ -z "$paths" ]; then
  if [ "$discover" -eq 1 ]; then
    echo "nothing to compare: this host exposes no copy of the skill." >&2
    echo "An empty search is not a clean verdict." >&2
  else
    usage
  fi
  exit 2
fi

# Split the collected list once, on newlines, then hand it to the loop as
# positional parameters: IFS is back to normal before any command inside runs.
_old_ifs="$IFS"
IFS='
'
set -- $paths
IFS="$_old_ifs"

rc=0
for path in "$@"; do
  if [ ! -f "$path" ]; then
    echo "FAIL: $path — no such file"
    rc=1
    continue
  fi
  report=$(awk '
    function trim(s) { sub(/^[[:space:]]+/, "", s); sub(/[[:space:]]+$/, "", s); return s }
    function negated(s,    l) {
      l = tolower(s)
      if (l ~ /(^|[^a-z])(never|not|no|don.t|do not|instead of|rather than|forbidden|banned)([^a-z]|$)/) return 1
      if (index(s, "никогда") || index(s, "Никогда") || index(s, "нельзя") || index(s, "Нельзя")) return 1
      if (index(s, "запрещ") || index(s, "Запрещ") || index(s, " не ") || substr(s, 1, 3) == "Не ") return 1
      return 0
    }
    function blanket(s) { return match(s, BLANKET_RE) > 0 }
    function flush_para(    n, i, parts, s) {
      if (para == "") return
      s = para
      gsub(/git add[[:space:]]+\./, "git add DOTARG", s)   # `git add .` is not a sentence end
      n = split(s, parts, /[;:!?]|\.([[:space:]]|$)/)
      for (i = 1; i <= n; i++)
        if (blanket(parts[i]) && !negated(parts[i])) {
          print "  " para_line ": blanket stage prescribed — " trim(parts[i])
          bad = 1
        }
      para = ""
    }
    BEGIN {
      BLANKET_RE = "git (add[[:space:]]+(-A|--all|-u|--update|[.]|DOTARG)|commit[[:space:]]+(-a|-am|--all))([^A-Za-z0-9_./-]|$)"
      fence = 0; bad = 0; paths = 0; para = ""; para_line = 0
    }
    /^[[:space:]]*(```|~~~)/ { flush_para(); fence = !fence; next }
    {
      if ($0 ~ /git add[[:space:]]+(--[[:space:]]+)?[^[:space:]]*docs\//) paths++
      if (fence) {
        if (blanket($0)) { print "  " NR ": blanket stage in a code block — " trim($0); bad = 1 }
        next
      }
      if ($0 ~ /^[[:space:]]*$/) { flush_para(); next }
      if (para == "") para_line = NR
      para = para " " $0
    }
    END {
      flush_para()
      if (!paths) { print "  no `git add [--] ...docs/...` line: the episode is not staged by path"; bad = 1 }
      exit bad
    }' "$path")
  if [ $? -eq 0 ]; then
    echo "ok: $path"
  else
    echo "FAIL: $path"
    printf '%s\n' "$report"
    rc=1
  fi
done
exit $rc
