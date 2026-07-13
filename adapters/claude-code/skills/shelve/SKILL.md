---
name: shelve
description: Offload a closed conversation topic (or a whole imported dialog) to the memory shelf as a Markdown episode with a validated digest. Use when a topic is finished, when context grows heavy, before compaction, or when the user asks to shelve/archive part of the conversation. M0 prompt-only version — the agent does the work, no memshelf server required.
---

# /shelve — offload an episode to the memory shelf (M0, prompt-only)

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
   s.add_document('<temp .md>', category='topics', title='<id>',
                  description='<digest first sentence>')"
   ```

   Category mapping: `topic → topics`, `research → research`,
   `session → sessions`.

6. **Ledger.** Append one line to `<shelf>/ledger.tsv`
   (create with header if missing):

   ```text
   date	episode_id	mode	approx_tokens_in	digest_tokens	notes
   ```

   `mode` = `live` or `import`; `digest_tokens` = digest chars/4.

7. **Commit — shelf repo only.** `git add -A && git commit` inside the shelf
   with message `shelve: <id>`. **Never `git push` from this skill.** Never
   write outside the shelf directory.

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
