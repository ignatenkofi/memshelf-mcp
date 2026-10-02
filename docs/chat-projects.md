# Chat projects: the shelf without hooks

Claude Code gets the memory loop from hooks and a `/shelve` skill
([`adapters/claude-code`](https://github.com/ignatenkofi/memshelf-mcp/tree/main/adapters/claude-code)).
A chat project — Claude Desktop, or claude.ai on the web and the phone — has
neither: nothing injects `INDEX.md` when a chat opens, and nothing shelves
before compaction. There the loop runs on the project's instructions and on
your say-so. This page walks it end to end: setup once per project, then the
four moves of every chat — open with the index, recall, shelve, keep the
index current.

> **Status (2026-10-01).** Assembled from the adapter READMEs. Every
> `memshelf` command in a code block below ran verbatim on a scratch shelf,
> with the CLI installed from `main` at `1ba9f3a` rather than from PyPI.
> Nobody but the author has followed it yet, and no step was driven through
> the Desktop or claude.ai UI — those steps follow the adapter docs and the
> host's help pages. Whether a prompt-driven loop is reliable enough
> to call supported is ARCHITECTURE open question 6, still open; the M3 exit
> criterion is one non-author user running the flow from this page alone.

## Two paths

| | Path A — Claude Desktop with the extension | Path B — claude.ai (web, phone) |
|---|---|---|
| memshelf tools in the chat | yes — the `.mcpb` extension runs the server on your machine | no — the server speaks stdio, so it runs where the shelf is |
| index at chat start | the model calls `memshelf_index` | `INDEX.md` uploaded as project knowledge, a snapshot |
| recall | `memshelf_search`, then `memshelf_recall` | you paste `memshelf fork` output |
| shelve | `memshelf_shelve`, after you say yes | the model drafts, you run `memshelf shelve` |
| git | `memshelf_shelve` commits and can push; everything else in a terminal | all in a terminal |

## Setup, once per project

**1. A shelf on disk.** Path B needs one too: it lives wherever you run the
CLI.

```bash
pip install memshelf-mcp
memshelf init --shelf ~/shelves/pricing --name "Pricing redesign"
```

That is a `git-local` shelf (git, no remote). `--storage plain` drops git;
`--storage git-remote --remote <url>` adds `origin` — keep that repository
private.

**2. Project instructions.** Paste the block from
[`CLAUDE-md-snippet.md`](https://github.com/ignatenkofi/memshelf-mcp/blob/main/adapters/claude-code/CLAUDE-md-snippet.md)
into the project's instructions, with `$MEMSHELF_ROOT` replaced by the
shelf's path. The snippet was written for Claude Code — it names `Read`,
`docshelf_read_document` and `/shelve`, none of which a chat has — so add
this after it:

```markdown
### On this surface (chat project)

- Shelf: ~/shelves/pricing — pass it as `shelf_path` to every memshelf call.
- Start of a chat: `memshelf_index`. Past work: `memshelf_search`, then
  `memshelf_recall` for one episode or one section — never the whole shelf.
- Shelve only after I say yes: show me the slug, digest and sections first,
  then call `memshelf_shelve`.
- No memshelf tools in this chat? Say so, answer from the attached INDEX.md,
  and ask me for an episode by its id instead of guessing. To shelve, give
  me the slug, digest and sections as text.
```

The same instructions serve both paths: where the tools are missing, the last
item takes over.

**3a. Path A — the extension.** Download `memshelf-<version>-uv.mcpb` from the
[latest release](https://github.com/ignatenkofi/memshelf-mcp/releases/latest)
and install it from *Settings → Extensions*; if Desktop refuses the `uv` type,
take the standalone `-macos-arm64` bundle (Apple silicon only). **Default
shelf** in the extension's settings is the fallback; a `shelf_path` named in
the instructions wins
([Desktop adapter](https://github.com/ignatenkofi/memshelf-mcp/blob/main/adapters/claude-desktop/README.md#the-default-shelf)).

**3b. Path B — the index as project knowledge.** Upload the shelf's
`INDEX.md` to the project's knowledge. It is a copy: re-upload it after every
shelve, and remember that uploading hands the digests to the host. What a
digest may contain is the shelf's `POLICY.md`, the same as on every surface.

## Every chat

### Open with the index

Path A: the instructions make `memshelf_index` the first call. It reads the
clone on your disk, so a clone that is behind its remote — another machine
shelved, or the shelf bot rendered after your last push — shows an old index.
`memshelf_sync` fast-forwards it; the v0.3.0 bundles predate that tool, and
there it is `git -C ~/shelves/pricing pull --ff-only` in a terminal.

Path B: the index is already in the project knowledge.

### Recall

Path A: `memshelf_search` with a few tokens, then `memshelf_recall` with the
episode id and, where one section answers, its name. The episode comes back
wrapped as data, not instructions.

Path B: the model has the digests only. For a body, print it and paste it:

```bash
memshelf fork --shelf ~/shelves/pricing --episode 2026-10-01-pricing-page --section Decisions --no-index
```

`fork` wraps the episode in the same data envelope recall uses. Drop
`--no-index` to bring the INDEX along — the bootstrap for a fresh chat
outside the project.

### Shelve

Path A: the model offers at a natural checkpoint — a topic closed, a decision
made. You say yes, it shows slug, digest and sections, you confirm, it calls
`memshelf_shelve`. `push: true` pushes the commit when the shelf has a remote;
the server runs git on your machine, so the push uses that machine's git
credentials. The result's `next` field names the step still owed to the
derived files (next section).

Path B: the model gives you the parts as text. Save the digest as
`digest.txt`, check it, then write the episode:

```bash
memshelf lint-digest --digest-file digest.txt --strict
memshelf shelve --shelf ~/shelves/pricing --slug 2026-10-01-pricing-page --kind topic --digest "$(cat digest.txt)" --section "Decisions=Three plans (Free, Team, Business); yearly billing shows a 20% badge. Per-seat sliders rejected: sales could not quote from them." --section "Open threads=Does Business list SSO or link to a contact form?"
```

A rejected digest writes nothing and prints the fix. A `topic` episode needs
a `Decisions` section.

Path B after the fact: a long chat can be shelved from the account export —
*Settings → Privacy → Export data*; the link arrives by email and expires in
24 hours ([claude.ai help](https://support.claude.com/en/articles/9450526-export-your-claude-data)).
Find the conversation and cut it out:

```bash
memshelf import discover --path ~/conversations.json --marker "pricing"
memshelf import extract --path ~/conversations.json --select c-pricing --out ~/pricing-chat.md
```

`--select` takes the id `discover` printed (`c-pricing` here), a title or an
index. Paste the cleaned transcript into a chat for the slug, digest and
sections, then run the `shelve` above with `--mode import` added.

The `shelve` account skill on claude.ai does not change Path B: it writes
through `memshelf_shelve`, and a claude.ai chat has no memshelf tools
([account mirror](https://github.com/ignatenkofi/memshelf-mcp/blob/main/adapters/claude-account/README.md)).

### Keep the index current

`shelve` writes and stages only the episode (#58). `INDEX.md` and
`ledger.tsv` do not list it until they are rendered — `memshelf doctor`
reports `stale-index` meanwhile. Who renders depends on the shelf:

| shelf | after a shelve |
|---|---|
| `plain` | `memshelf_rebuild` from the chat; nothing to commit |
| `git-local` | `memshelf_rebuild`, then commit the derived files in a terminal |
| remote, no bot | push the episode, `memshelf_rebuild`, then commit and push the derived files in a terminal |
| remote, [shelf bot](https://github.com/ignatenkofi/memshelf-mcp/blob/main/adapters/shelf-repo/README.md) | push the episode; the bot renders on `main`. Do not rebuild by hand — a hand-committed render collides with the bot's (#58) |

Up to v0.3.0 the `next` field on a `git-local` shelf still says to push and
points at a `sync.hint` that is empty there. There is nothing to push; the
table row is the step.

On Path B the rebuild is the CLI verb, `memshelf rebuild --shelf
~/shelves/pricing`. The commit in a terminal:

```bash
git -C ~/shelves/pricing add INDEX.md ledger.tsv stats.svg 'docs/*/.meta.json'
git -C ~/shelves/pricing commit -m "chore: regenerate derived files"
```

**The trap on a shelf with a remote.** `memshelf_rebuild` leaves the derived
files modified, and the next `memshelf_shelve` refuses before writing
anything: *tracked files are modified — INDEX.md, ledger.tsv. Commit or stash
them first* (#108). A chat has no git, so a rebuild from the chat is followed
by the commit above before the next shelve. `sync: false` skips the check,
and with it the fetch that keeps a shelve off a stale base — do not reach for
it. A `git-local` shelf has nothing to sync and does not refuse.

### Read on the phone

```bash
memshelf mirror --shelf ~/shelves/pricing --all --out ~/pricing-shelf.html
```

One self-contained page — no scripts, no remote assets; `--all` carries every
live episode, without it only the INDEX. Hosting it is up to you: a private
claude.ai artifact, the phone's Files app (ARCHITECTURE open question 8).

## What this surface cannot do

- **Nothing fires on its own.** No hook shelves before compaction or at the
  end of a chat; what was not shelved before a long chat compacts is gone.
- **Instructions are a request.** The model can skip `memshelf_index` or
  forget to offer a shelve — that is open question 6.
- **claude.ai cannot reach the server.** Project knowledge is a copy, and it
  goes stale with every shelve.
- **No git from the chat** beyond what `memshelf_shelve` does itself — its
  commit and push: derived commits, pulls and diverged branches are terminal
  work.
- **A stale bundle answers with old code.** When `doctor` reports
  `served-code-differs`, rebuild and reinstall the extension
  ([refresh](https://github.com/ignatenkofi/memshelf-mcp/blob/main/adapters/claude-desktop/README.md#refreshing-the-installed-extension-from-a-checkout)).

## What was checked, and how

Scratch shelves on 2026-10-01, CLI only, `main` at `1ba9f3a`:

- every `memshelf` and `git` command in the code blocks above, verbatim, on
  a fresh `git-local` shelf, `shelve --mode import` included: `lint-digest
  --strict` and `doctor` exit 0, `fork` prints the digest and the one section,
  `mirror --all` writes a page with no `<script>`;
- before `rebuild` the INDEX does not list the new episode, after it does;
- on a shelf with a bare-repository remote and no bot: `shelve --push` →
  `next` asks for a rebuild in a separate commit → `rebuild` →
  the next shelve refuses with the message quoted above → after the commit
  it passes and pushes both commits;
- `import discover` / `extract` on a two-conversation file in the
  claude.ai export format.

Not run: the Desktop UI, the claude.ai UI, the shelf bot (a GitHub workflow).
The release bundles have the dirty-tree refusal and the `next` field;
`memshelf_sync` and the post-push wait for the bot's render arrived after
v0.3.0.
