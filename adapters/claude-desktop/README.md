# Claude Desktop adapter — memshelf as an `.mcpb` extension

Claude Desktop installs MCP servers as **MCP Bundles**: a `.mcpb` file (a zip
with a `manifest.json` at the root) that the user drops into
*Settings → Extensions*. This directory builds one for memshelf.

The Claude Code plugin lives next door in [`../claude-code`](../claude-code);
this adapter is the desktop equivalent, and the two are independent.

## Two bundles, and which one to install

Python is the whole difficulty: Claude Desktop ships a Node runtime, not a
Python one, and macOS's own `python3` is 3.9 — below memshelf's floor of 3.10.
So the builder produces two answers to that problem.

| | `memshelf-<version>-uv.mcpb` | `memshelf-<version>-macos-arm64.mcpb` |
|---|---|---|
| size | ~85 KB | ~23 MB |
| manifest | 0.4, `server.type: "uv"` | 0.3, `server.type: "python"` |
| Python on the machine | not needed — the host provides it | not needed — one is inside the bundle |
| first launch | resolves and caches dependencies | starts immediately |
| requires | a Desktop that understands `type: "uv"` (upstream still calls it experimental) | any Desktop that reads manifest 0.3 |
| platform | any | the one it was built for |

**Install the uv bundle first.** If Claude Desktop refuses it — an older build
will not recognise the type — install the standalone one, which carries a
[python-build-standalone](https://github.com/astral-sh/python-build-standalone)
interpreter and every dependency, and therefore cares about nothing on the
machine except the CPU architecture.

## Signing

A release signs its bundles when the repository holds a code-signing
certificate in the secrets `MCPB_SIGNING_CERT` and `MCPB_SIGNING_KEY` (PEM;
`MCPB_SIGNING_INTERMEDIATES` for a chain). Without them the bundles ship
unsigned and the release run says so in a notice — it never fakes a signature.

Only a certificate the operating system's trust store vouches for changes
anything. `mcpb verify` (2.1.2) reports a bundle signed with any other
certificate — a self-signed one included — as "not signed", so the install
warning would stay; the signing step therefore runs `verify` after `sign` and
fails the release on a certificate the verifier rejects. Put the certificate in
place with:

```bash
gh secret set MCPB_SIGNING_CERT < cert.pem
```

```bash
gh secret set MCPB_SIGNING_KEY < key.pem
```

Both read the PEM from a file, so nothing lands in the shell history.

## The default shelf

Every memshelf tool takes a `shelf_path`, which is awkward in a chat where
nobody wants to paste a path each time. The extension exposes **Default shelf**
in its settings; the bundle passes it to the server as `MEMSHELF_SHELF_PATH`,
and a call that omits `shelf_path` lands there.

The precedence is the useful part:

- a call that **names** a `shelf_path` uses it — so a project can pin its own
  shelf by saying so in the project's instructions;
- a call that names none falls back to **Default shelf** — the global one;
- neither is a plain error naming the setting, not a write to `""`.

Point it at a shelf that already exists; ask Claude to run `memshelf_init` on an
empty folder to create one.

## Building

```sh
python3 adapters/claude-desktop/build.py --out dist
```

Both bundles land in `dist/`. Needs network: PyPI for the dependencies, and the
interpreter tarball for the standalone bundle (cached in `.build-cache/`, so a
rebuild is quick). `--variant uv|standalone|both` and `--target` narrow the job.

Two things the builder does that are worth knowing:

- **docshelf is installed without its dependency tree.** It declares
  `pymupdf4llm`, which drags in pymupdf, onnxruntime, numpy and sympy — roughly
  200 MB of PDF ingestion that a memory shelf never calls. memshelf touches one
  docshelf module, `core.shelf`, which imports none of it. The uv bundle does
  the same thing through a `[tool.uv] override-dependencies` marker that cannot
  be satisfied.
- **The manifest is generated, never hand-edited.** Version comes from
  `__init__.py` and the tool roster is parsed out of `server.py`, so a tool
  added or renamed shows up in the next bundle without anyone remembering to
  update a list.

Adding a platform is a `Target` entry in `build.py`: the interpreter URL (uv's
own download table is a good source), its sha256, and the pip platform tags.

## Checking a bundle before shipping it

`mcpb validate` reads the manifest and stops there. This does not:

```sh
python3 adapters/claude-desktop/try_bundle.py dist/memshelf-0.3.0-uv.mcpb
```

It unpacks the bundle, expands the manifest's own `${__dirname}` and
`${user_config.*}` templates, spawns exactly the command `mcp_config` declares,
and then talks MCP to it: handshake, `tools/list`, create a shelf, and — the
point of the exercise — read the INDEX **without** passing `shelf_path`, to
prove the configured default arrived. Then it starts the bundle a second time
with the setting left empty, and requires the failure to be a legible message
naming `MEMSHELF_SHELF_PATH` rather than a crash.

That catches what validation cannot: an over-pruned interpreter, a launcher that
cannot find `lib`, vendored wheels built for the wrong ABI, and `mcp_config`
paths pointing at files that are not in the zip.

## A server the host never greets

Claude Desktop has been seen spawning a server process it then never speaks
to (#115): the pipe stays open, `initialize` never arrives, and the process
sits there for hours. The server therefore waits `MEMSHELF_HANDSHAKE_TIMEOUT`
seconds (default 60) for the handshake and exits with status 3 and one
stderr line if none comes. A session that did say hello is never affected,
however long it idles. Set the variable to `0` in the host's environment to
switch the deadline off; `find-orphan-servers.sh` still lists whatever is
left.

A bundle can only be started on the platform it targets. `linux-x86_64` exists
as a build target for exactly this reason — CI and the build machine can run the
standalone path there, and a mistake in the shared machinery fails the same way
on both.

## Layout of what gets built

```
memshelf-0.3.0-uv.mcpb            memshelf-0.3.0-macos-arm64.mcpb
├── manifest.json                 ├── manifest.json
├── pyproject.toml                ├── runtime/            <- CPython 3.12, pruned
└── src/                          └── server/
    ├── server.py                     ├── main.py         <- sets sys.path, starts the server
    └── memshelf_mcp/                 └── lib/            <- dependencies + memshelf_mcp
```
