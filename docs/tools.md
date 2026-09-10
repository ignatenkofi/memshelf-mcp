# Tool reference

The long-form description of every `memshelf_*` tool. The MCP schema carries
only a one- or two-sentence description per tool — enough to pick the right
tool — because tool schemas ride in the static prefix of every session where
the server is connected, whether or not a tool is ever called (#111). Anything
a caller needs beyond the choice lives here; the CLI verbs (`memshelf <verb>`)
share the same semantics.

Legend for the *Side effects* column: **none** — read-only; **shelf** — writes
episode files; **derived** — writes the rendered files (`ledger.tsv`,
`INDEX.md`, `.meta.json`, `stats.svg`); **working file** — writes outside the
shelf.

| Tool | Side effects | Pick it when |
|---|---|---|
| `memshelf_init` | shelf | a directory should become a shelf |
| `memshelf_shelve` | shelf (+ git commit) | a topic is closed and should leave the context |
| `memshelf_lint_digest` | none | a digest is still being written |
| `memshelf_import` | working file | a whole exported dialog should be shelved retroactively |
| `memshelf_index` | none | starting to answer anything about past work |
| `memshelf_recall` | none | one episode (or one section) answers the question |
| `memshelf_search` | none | the episode id is unknown |
| `memshelf_stats` | none | the shelf's token economy is the question |
| `memshelf_advise` | none | the context window is the question |
| `memshelf_rebuild` | derived | derived files must be regenerated or verified |
| `memshelf_rollup` | shelf + derived | navigation has grown large |
| `memshelf_purge` | shelf (deletes) | episodes are past `retain_until` |
| `memshelf_resolve` | derived | two writers shelved on parallel branches |
| `memshelf_doctor` | none | the shelf's integrity is the question |

## `memshelf_shelve`

Offload one closed topic to the shelf as a durable, indexed episode.

Redacts credential shapes, enforces the digest contract (≤120 words, named
referents, no secrets), composes the episode, and writes it through docshelf,
staging and auto-committing the episode alone (git shelves; push is opt-in via
`push`). Derived files — the ledger row, INDEX — are **not** written here: they
are rendered by `memshelf_rebuild` or the shelf's bot (#58), and the response
says so (`shelf_totals.derived_stale`, `next`). A contract violation comes back
as an error carrying the exact fixes — nothing is written. Returns the episode
address, redaction report, and any digest warnings. `amend=true` rewrites an
existing episode in place under the same slug.

## `memshelf_lint_digest`

Validate a digest against the Layer-3 contract without touching the shelf.

Same validator `memshelf_shelve` runs, minus every side effect — so the digest
can be checked while it is still being written, rather than after the episode
is already committed (#71). Errors block a shelve; warnings do not, and
`strict` is what turns them into a failure for a caller that wants it.

## `memshelf_recall`

Fetch a shelved episode by id — or a single `## Section` of it.

Returns the content wrapped in a data envelope: recalled episodes are records,
never instructions. Prefer a section fetch over the whole episode when one
section answers the question — that is where the savings are. `log=true`
appends the realized saving to `recall-log.tsv`.

## `memshelf_index`

Return the shelf INDEX — the small recall entry point. Read it before answering
anything about past work, then recall only what you need. It is the whole
standing memory cost of a session; everything else is fetched on demand.

## `memshelf_search`

Grep the shelf for episodes matching every query token; returns their
addresses and snippets. Split episodes match at the section level, so the hit
names the section to recall, not just the episode.

**Semantic sidecar (#17).** Grep is an AND over literal tokens: a paraphrase,
an inflected word, or a query in the other language misses. When the optional
embedding sidecar is *usable* — `pip install 'memshelf-mcp[semantic]'` done, an
index built with `memshelf semantic build --shelf …`, and `$MEMSHELF_SEMANTIC`
not set to `off` — the tool fuses the grep ranking with a nearest-chunk
ranking by reciprocal rank and reports `mode: "hybrid"`; every hit then
carries `via`: `grep`, `semantic`, or `both`. Otherwise `mode` is `grep` and
the result is byte-for-byte what it was before the sidecar existed. The tool
signature and description do not change (#111); a caller reads `mode` to tell
a paraphrase miss from a sidecar that was never built.

The index is a JSON file under the state directory
(`~/.local/state/memshelf-mcp/semantic/<shelf-key>/index.json`), never inside
the shelf — a shelf is a git repository and a derived blob would ride into
every diff. It is rebuilt from the episodes, incrementally by file `(mtime,
size)`; `memshelf semantic status` reports how many files went stale since.
The model (`minishlab/potion-multilingual-128M`, override with
`$MEMSHELF_SEMANTIC_MODEL`) is static embeddings: no GPU, no service, loaded
once per process. Measured on the dogfood shelf, 30 hand-written paraphrase /
cross-language / keyword queries: grep found 1, the hybrid 16 in the top 5 and
21 in the top 10 (`memshelf search-bench`, ROADMAP M3). Build: 209 files,
1,319 chunks, 2.9 s.

## `memshelf_stats`

Report the shelf's token economy: standing cost (INDEX + digests) vs shelved
mass and compression ratio (claimed, from `approx_tokens`), plus realized
savings from logged recalls when present. The transparent-savings number the
project's core claim rests on; see `docs/M0.md → Measurement`.

Two quantities, deliberately not one (#110). `work_volume` is the raw sum of
`approx_tokens` — material the sessions moved. `shelved_mass` is that sum with
each episode clipped to the context window — context actually freed, and the
figure that feeds `compression_ratio` and `realized_savings`. `context_window`
and `capped_episodes` report the bound used and how many episodes it clipped;
pass `context_window` (CLI `--context-window`, else `$MEMSHELF_CONTEXT_WINDOW`)
to match your client instead of the 200K default. That default errs small on
purpose: understating a saving is the safer error for a number whose whole job
is to claim one.

## `memshelf_advise`

Report what your context is made of and what you could put down (#14).

Tell it what is in your window — a label, a rough token size, and whether the
topic is still in play — and it returns a breakdown (static overhead /
memshelf's own cost / live topics / reclaimable) plus ranked **proposals**. It
writes nothing and shelves nothing; you decide.

Two things it does that a self-assessment cannot. It measures what memshelf
itself costs you every session (INDEX + digests), instead of leaving its own
overhead out of the picture. And it verifies any `episode_id` you claim is
already shelved: if the episode is not on the shelf, it says so rather than
proposing you drop content nobody stored.

Call it with no occupants for the first-run view of the shelf alone — the
report will say the window side is missing rather than report it clean.

## `memshelf_init`

Create (or top up) a memory shelf: docshelf layout with fixed categories, the
recall-rule INDEX preamble, a `POLICY.md` template, the ledger header, and a
spec-conformant `shelf.yml`. Storage: git-local (default, no remote), plain, or
git-remote (private only). Idempotent — never overwrites existing files, so it
is safe to re-run on a shelf that predates a newer layout.

## `memshelf_rebuild`

Regenerate `ledger.tsv`, each category's `.meta.json`, `INDEX.md` and
`stats.svg` from the episodes (#58). The episode is the source; these four are
output, owned by a bot on `main`, which is what removes the multi-writer
conflict class at the root. With `check=true` nothing is written and the result
says which files have drifted — the shelf's PR guard runs exactly this. On a
shelf whose bot renders derived files, run it by hand only when the bot is
down: a hand-committed render collides with the bot's next commit.

## `memshelf_rollup`

Archive a period's episodes behind one digest-of-digests (#15).

`INDEX.md` rides in every session and grows with the shelf; a rollup turns N
INDEX lines into one. Use it when navigation has grown large, not to answer
`doctor`'s `index-bloat` — that one means an entry is overpriced, and folding
entries drops their budget along with them. The originals move to the
`archive/` sub-shelf — nothing is deleted, recall by id keeps working, and
every ledger row survives, because an archived episode still holds the mass it
saved. The digest is **yours**: synthesizing a quarter of digests is the part a
tool cannot do, so pass the same quality of digest `shelve` demands.

## `memshelf_purge`

Drop episodes whose `retain_until` has passed, then reindex (#15).

Dry-run by default: without `apply=true` it only lists what expired. Deletes
the working-tree file — **git history still contains it**. Real erasure is a
deliberate filter-repo pass over the whole repository, never a side effect of
a tool call, and the result says so.

## `memshelf_resolve`

Resolve the multi-writer conflict class (two sessions shelved on parallel
branches): union `ledger.tsv` / `recall-log.tsv` rows and `.meta.json` keys
from both sides, rebuild `INDEX.md` and `stats.svg` from `docs/`, then run
doctor. Conflicting episode files are reported as unresolved, never
auto-merged. Also safe outside a conflict — degrades to a derived-files
rebuild.

## `memshelf_doctor`

Diagnose the shelf: episode schema, the digest contract at rest, secrets that
slipped onto disk, ledger consistency, and the INDEX budget — plus docshelf's
structural checks. With `check_remote=true` it also fails a shelf whose git
remote is publicly visible (the one network probe, opt-in). Read-only; reports
findings, fixes nothing. Errors are meant to block a push; warnings name known
states (`stale-index`, `episode-unpushed`, `index-bloat`) that a shelf's own
rules decide how to treat.

## `memshelf_import`

Retro-shelve a whole exported dialog without pulling it through context.

Two methods. `discover` lists the conversations in a claude.ai
`conversations.json` or Claude Code session JSONL, matched by content
`markers` (not title), returning metadata only. `extract` cleans one
conversation — dropping `tool_use`/`tool_result` blocks — to a working file and
returns its path plus the noise ratio. The raw transcript stays a file on disk:
it never enters context or a shelf. Then read the cleaned file, segment it, and
shelve each segment (`mode=import`).
