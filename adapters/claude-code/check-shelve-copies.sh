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
#   exit 0 — every copy passes; 1 — a copy fails or is missing (the report
#   names the file and the line); 2 — no paths given.
#
# Run it over every copy you can reach — the packaged one next to this script,
# a shelf's `.claude/skills/shelve/SKILL.md`, an account sync under
# `~/.claude/skills/` — so a divergence is a red line, not a memory.
set -u

if [ "$#" -eq 0 ]; then
  echo "usage: $0 <SKILL.md>..." >&2
  exit 2
fi

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
