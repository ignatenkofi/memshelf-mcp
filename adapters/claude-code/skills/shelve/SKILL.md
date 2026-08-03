---
name: shelve
description: Offload a closed conversation topic (or a whole imported dialog) to the memory shelf as a Markdown episode with a validated digest. Use when a topic is finished, when context grows heavy, before compaction, or when the user asks to shelve/archive part of the conversation. M0 prompt-only version — the agent does the work, no memshelf server required.
---

# /shelve — offload an episode to the memory shelf (M0, prompt-only)

> **Prefer the tool when it is installed.** `memshelf shelve --shelf … --slug …
> --kind … --digest … --section …` does everything below in one call —
> redaction, the digest contract, composition, the episode write and the
> auto-commit — and cannot drift from the contract the way a prompt can. These
> steps are the fallback for hosts without `memshelf`; keep them in sync with
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
- Shelf write path: docshelf-mcp MCP tools if attached, otherwise the Python
  library fallback in step 4.
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
   tags: [..]
   approx_tokens: <estimate>        # what this cost in-window (chars/4)
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

   Write the skeleton frontmatter-first as above, but note the **stored** file
   differs: docshelf `add_document` prepends `# <id>` when the content doesn't
   start with `#`, so on disk the episode is H1-first (`# <id>`, a blank line,
   then this frontmatter). See ARCHITECTURE → Layer 2 (shelf-spec v0 § 5.1).

3. **Redaction & PII pass — before anything touches disk.**
   - Replace credential-shaped strings (tokens, keys, `.env` assignments,
     bearer headers) with `«redacted:<kind>»`.
   - Apply the shelf's PII policy. Example (sqst shelves): no student names,
     nicks, emails, or any identifiers — roles and codes only («студент»,
     C1..C7, S1..S15).
   - Report in your reply what was redacted, so false positives get caught.

4. **Validate the digest yourself** (M0 has no tool to do it):
   ≤120 words; states what was decided, what was rejected and why, what
   artifacts exist, what is still open; readable by someone with zero
   session context (named referents — no bare "we"/"it"); no secrets.

5. **Write to the shelf.** Preferred — docshelf MCP:
   `docshelf_add_document(path=<temp .md>, category=<kind-mapped>,
   title="<id>", description="<digest first sentence>")`.
   Fallback — Python:

   ```bash
   python3 -c "
   from docshelf_mcp import Shelf
   s = Shelf('$MEMSHELF_ROOT')
   s.add_document('<temp .md>', category='<kind-mapped>', title='<id>',
                  description='<digest first sentence>')"
   ```

   Category mapping: `topic → topics`, `research → research`,
   `session → sessions`.

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
2. Then run steps 2–8 per episode, `mode: import` in the ledger.
3. The raw transcript is input only — it is never copied into the shelf and
   never committed anywhere.
