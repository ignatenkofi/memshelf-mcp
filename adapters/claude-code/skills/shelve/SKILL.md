---
name: shelve
description: Offload a closed conversation topic (or a whole imported dialog) to the memory shelf as a Markdown episode with a validated digest. Use when a topic is finished, when context grows heavy, before compaction, or when the user asks to shelve/archive part of the conversation. Prefers the `memshelf shelve` CLI or tool, which needs no server (`uvx --from memshelf-mcp memshelf shelve` runs it), and keeps the manual steps for hosts without it.
---

# /shelve — offload an episode to the memory shelf

> **Prefer the tool when it is installed.** `memshelf shelve --shelf … --slug …
> --kind … --digest … --section …` does everything below in one call —
> redaction, the digest contract, composition, the episode write and the
> auto-commit — and cannot drift from the contract the way a prompt can. It
> needs no server, and where `uv` is installed it needs no install either:
> `uvx --from memshelf-mcp memshelf shelve …` runs it from PyPI. These steps
> are the fallback for hosts without `memshelf`; keep them in sync with
> `core/shelve.py` when the contract changes.

> **Required sections by kind.** The contract is enforced *before* anything is
> written — by the tool and by the fallback alike — so a missing section costs
> a failed call, not a broken episode. `Digest` is always required; on top of
> it:
>
> | `kind` | required besides `Digest` |
> |---|---|
> | `topic` | `Decisions` |
> | `session` | `Timeline`, `Open threads` |
> | `research` | any one non-empty body section |
>
> Section names are matched exactly. Known sections render in this order:
> `Decisions` → `Timeline` → `Artifacts` → `Open threads` → `Raw excerpts`;
> anything else keeps insertion order after them. Source of truth —
> `_REQUIRED_SECTIONS` in `core/episode.py`.
>
> This lives here, not only in step 2, because the tool path skips the body of
> this skill: you read the pointer above, call `memshelf shelve`, and meet the
> contract as an error. That happened on 2026-08-03 with a `topic` episode
> written without `Decisions`.

## Prerequisites

- `MEMSHELF_ROOT` env var (or an explicit path given by the user) points to
  an initialized shelf: a docshelf shelf with categories
  `topics`, `research`, `sessions` and `provider: none`.
- Shelf write path: docshelf's Python library run through `uvx`, or the
  docshelf-mcp MCP tools if attached (step 5).
- Read the shelf's PII/redaction policy first if `POLICY.md` exists in the
  shelf root — it overrides the generic rules below.

## Steps

1. **Pick the cut.** If the user named a topic, shelve that. Otherwise
   propose candidates: topics that are *closed* (conclusion reached, no
   activity for a while) with a rough token weight each (chars/4), and let
   the user confirm. Never shelve the currently active topic uninvited.

2. **Compose the episode** as Markdown with this exact skeleton
   (empty sections omitted):

   ```markdown
   ---
   id: YYYY-MM-DD-<slug>            # today's date + short latin slug
   kind: topic                      # topic | research | session
   session: <ref>                   # optional: opaque ref for the session that produced this
   span: YYYY-MM-DD..YYYY-MM-DD     # when the work actually happened
   date: YYYY-MM-DD                 # the shelve date: the ledger's date column
   display_title: "<title>"         # optional: free-form INDEX title, any script
   description: "<≤120 chars>"      # the INDEX line, capped (step 5)
   tags: [..]
   approx_tokens: <estimate>        # what this cost in-window (chars/4)
   mode: live                       # live | import (see Import mode)
   notes: "<one tab-free line>"     # optional: the ledger's last column
   ---

   ## Digest
   ## Decisions        # decision → reason; rejected alternative → reason
   ## Timeline         # compressed narrative, in order
   ## Artifacts        # PRs, files, commands that worked
   ## Open threads     # undone / undecided
   ## Raw excerpts     # ONLY verbatim fragments painful to reconstruct
   ```

   `## Digest` + `## Decisions` are mandatory for `kind: topic`;
   `research` needs Digest + one body section;
   `session` needs Digest + Timeline + Open threads.

   The ledger row and the INDEX line are rendered from this frontmatter
   (step 6), so a value that is not here is not on the shelf: `date`, `mode`
   and `notes` fill the row, `display_title` and `description` the INDEX line.
   Without `date` the render falls back to the id's date prefix and warns.
   Free-text values go in double quotes — a `: ` inside an unquoted one is
   invalid YAML to the shelf's validator.

   Write the skeleton frontmatter-first as above, but note the **stored** file
   differs: docshelf `add_document` prepends `# <id>` when the content doesn't
   start with `#`, so on disk the episode is H1-first (`# <id>`, a blank line,
   then this frontmatter). See ARCHITECTURE → Layer 2 (shelf-spec v0 § 5.1).

3. **Redaction & PII pass — before anything touches disk.**

   **Primary, when pii-mcp is attached (its MCP tools) or `pii-mcp` is on
   PATH:** `pii_scan` the composed episode (the `<temp .md>` from step 2); on
   findings, `pii_redact` it — `strategies={surname: alias, nickname: alias}`,
   `alias_scope=<the episode id>` so a label like «студент-А» stays stable
   for that scope (secrets already default to the `kind` strategy) —
   dry-run first (the default), read the diff, then re-run with
   `apply=true`. Right before step 5, `pii_verify` the result: its `verdict`
   is the write gate, so proceed only on `clean`. **`config-error` (exit 2 —
   usually no pattern packs loaded, or `PII_PATTERN_DIR` unset) is not
   "clean"** — say so in your reply, then fall through to the manual pass
   below for this episode. No MCP tools attached but the binary is on PATH:
   the same three calls as the CLI, `pii-mcp scan|redact|verify <temp .md>`.

   Manual pass — still the second line of defense after a clean verify (the
   engine catches shapes and vocabulary, not meaning), and the whole check
   when pii-mcp is neither attached nor on PATH:
   - Replace credential-shaped strings (tokens, keys, `.env` assignments,
     bearer headers) with `«redacted:<kind>»`.
   - Apply the shelf's PII policy. Example (sqst shelves): no student names,
     nicks, emails, or any identifiers — roles and codes only («студент»,
     C1..C7, S1..S15).
   - Report in your reply what was redacted, so false positives get caught.

4. **Validate the digest.** `memshelf lint-digest --strict --digest-file
   <digest.txt>` (or `--digest '<text>'`; without an install,
   `uvx --from memshelf-mcp memshelf lint-digest …`) runs the validator
   `shelve` runs, needs no server and writes nothing; `--strict` also fails
   on the `thin` and `referent-bare` warnings. Where it cannot run, check by
   hand: ≤120 words; states what was decided, what was rejected and why, what
   artifacts exist, what is still open; readable by someone with zero
   session context (named referents — no bare "we"/"it"); no secrets.

5. **Write to the shelf — the episode file and nothing else.** docshelf's
   `add_document` splits a long episode into H2 section files, rebuilds
   `INDEX.md` and rewrites the category's `.meta.json` by default; all of
   that is derived (step 6), and step 7 commits the episode alone. So turn
   the first two off and put `.meta.json` back. Python, through `uvx` so the
   bare `python3` need not have docshelf installed:

   ```bash
   uvx --from docshelf-mcp python -c "
   from pathlib import Path
   from docshelf_mcp import Shelf
   root = Path('$MEMSHELF_ROOT')
   meta = root / 'docs' / '<kind-mapped>' / '.meta.json'
   before = meta.read_bytes() if meta.is_file() else None
   Shelf(root).add_document('<temp .md>', category='<kind-mapped>', title='<id>',
                            split=False, rebuild_index=False)
   meta.write_bytes(before) if before is not None else meta.unlink(missing_ok=True)"
   ```

   Or the docshelf MCP tool, if attached:
   `docshelf_add_document(source_path=<temp .md>, category=<kind-mapped>,
   title="<id>", split=false, shelf_path=<shelf>)`. It has no
   `rebuild_index` switch: it always rewrites `INDEX.md` as well as the
   `.meta.json`, so put both back before step 7 (on a git shelf; a
   `.meta.json` git does not know yet is one the call created):

   ```bash
   git -C "$MEMSHELF_ROOT" checkout -- INDEX.md
   git -C "$MEMSHELF_ROOT" checkout -- docs/<kind-mapped>/.meta.json 2>/dev/null ||
     rm -f "$MEMSHELF_ROOT/docs/<kind-mapped>/.meta.json"
   ```

   Its reply also suggests committing with a blanket stage: do not follow
   that, step 7 stages the episode by path. Either way, `git status
   --porcelain` should show no change from this step but the new episode —
   no `INDEX.md`, no `.meta.json`, no section directory.

   Category mapping: `topic → topics`, `research → research`,
   `session → sessions`.

   **Cap the description at 120 characters yourself.** It is the
   frontmatter `description` of step 2 — `.meta.json` and the INDEX line are
   rendered from it, not from anything passed to `add_document`. The tool
   applies `MAX_DESCRIPTION_CHARS` when it writes the episode; nothing in this
   fallback does, so an uncapped description here reproduces exactly what the
   cap exists to stop.
   The digest's first sentence is a starting point, not the answer — on a real
   shelf it ran to 420 characters, and descriptions alone reached 43% of
   INDEX.md, which is paid for in every session by every reader who only
   wanted to know which file to open. Cut at a word boundary and end with `…`.
   The full account belongs in `## Digest`, which is what recall fetches.

6. **Do NOT write the ledger by hand.** Since #58 `ledger.tsv` — like
   `INDEX.md`, `stats.svg` and each category's `.meta.json` — is a **derived**
   file: it is rendered from the episodes' frontmatter, not appended to. A
   hand-written row is at best redundant and at worst the merge conflict the
   split exists to remove, because two sessions shelving in parallel would
   again touch the same file.

   Everything the row needs therefore goes into the frontmatter of step 2
   (`date`, `mode`, `approx_tokens`, `notes`), and the file itself is produced
   by whichever of these applies:

   - `memshelf rebuild --shelf <shelf>` if the CLI is available;
   - the shelf's bot on `main`, if the shelf adopted #58 (then the file
     legitimately lags on a branch — that is not a defect to fix by hand).

   `notes` still must contain **no tab characters** (shelf-spec v0 § 4.4): it
   becomes the last ledger column, so a tab shifts the field count for every
   reader and a newline forges an entire bogus row. Keep it to one tab-free
   line.

7. **Commit, then push if the container is ephemeral — shelf repo only.**
   Stage **the episode file alone** — `git add <shelf>/docs/<category>/<id>.md`,
   not `git add -A` — and commit with message `shelve: <id>`; never write
   outside the shelf directory. Staging everything would sweep in the derived
   files of step 6 and recreate the collision #58 removed. Whether to push depends on the
   shelf's storage mode and the session:
   - `git-local` / `plain` (no remote): nothing to push — the commit is the
     durable record.
   - `git-remote` in an **ephemeral cloud session** (web/remote Claude Code,
     a Cowork container reclaimed at session end): `git push` **immediately
     after the commit**. A committed-but-unpushed episode dies with the
     container — the exact loss mode M0 exists to prevent (`docs/M0.md`).
     Until the M1 `SessionEnd`/`PreCompact` hooks (#11) automate it, this
     push is a required manual step of every shelve.
   - `git-remote` on a **persistent host** (durable local clone): push stays
     deliberate — on the user's confirmation, not automatic.

8. **Replace in context.** End your reply with the digest and the episode's
   shelf path. From this point on refer to the topic ONLY by that address;
   do not re-expand its content unless explicitly recalled.

## Import mode (whole-dialog backfill)

When the user hands you an exported transcript to retro-shelve:

1. Read it and propose a segmentation: one episode per coherent topic/arc,
   plus one `kind: session` digest for the whole dialog. Show the list
   (id + one-line scope + rough tokens) and get confirmation.
2. Then run steps 2–8 per episode, with `mode: import` in the frontmatter
   (the ledger row is rendered from it, step 6).
3. The raw transcript is input only — it is never copied into the shelf and
   never committed anywhere.
