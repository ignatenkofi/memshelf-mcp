# Contributing to memshelf-mcp

Thanks for considering a contribution. Bug reports, experience reports from
running memshelf on your own shelves, doc fixes and focused PRs are all
welcome.

## Where things are decided

- [`docs/DECISIONS.md`](docs/DECISIONS.md) — the decision log. If your PR
  changes a design decision, add a row.
- [GitHub Issues](https://github.com/ignatenkofi/memshelf-mcp/issues) —
  the triaged backlog. Start there before proposing something big.
- [`docs/ROADMAP.md`](docs/ROADMAP.md) — milestones with exit criteria.

## Reporting a problem

Open an issue with what you ran (the exact `memshelf …` command or MCP tool
call), what you expected, and what happened — the JSON the tool printed or
the traceback. Add your Python version (`python --version`) and the
`memshelf --version` output. A shelf that reproduces it (with secrets and
third parties redacted) is the best attachment.

## Suggesting a feature

Open an issue with `feature:` in the title. Describe the use case first,
then the proposed solution.

## Submitting a pull request

1. Fork the repo and create a topic branch off `main`.
2. Install the dev environment (Python ≥ 3.10):

   ```bash
   pip install -e ".[dev]"
   ```

3. Make your change. Keep PRs focused — one concern per PR.
4. Run what CI runs (`.github/workflows/ci.yml`):

   ```bash
   ruff check .
   ruff format --check .
   python -c "import docshelf_mcp, mcp"
   pytest -q
   ```

   The import line is part of the check on purpose: many test files start
   with `pytest.importorskip(...)` for the hard dependencies, so on a broken
   install `pytest` reads green having tested nothing. CI runs the same at
   both ends of the supported Python range, and also builds and starts the
   desktop bundle ([`adapters/claude-desktop/`](adapters/claude-desktop/)).

   CI also runs `pii-mcp verify . --baseline tests/pii-baseline.json`
   (job `pii-self-verify`): the tree obeys the same no-real-PII policy the
   tool enforces on shelves. A new synthetic identity in a test or fixture
   is a new baseline entry (fingerprint from the report, a note saying
   where and why) — never a real one. `pii-mcp` is a private repository;
   install it from a checkout (`pip install -e ../pii-mcp`) to run this
   locally.
5. Update `CHANGELOG.md` under `[Unreleased]` and, if you changed a design
   decision, `docs/DECISIONS.md`.
6. Open the PR. Reference the issue it addresses, if any.

PRs that touch `src/` should add or update tests under `tests/`. The bar is
"this regression would have been caught".

`shelf/` is the project's own memory shelf. If your PR adds an episode there,
render the derived files in the same commit (`memshelf rebuild --shelf shelf`):
the `shelf-pr-guard` workflow runs `memshelf doctor` and
`memshelf rebuild --check` on every change under `shelf/`.

## Coding style

- `ruff` is the source of truth for lint and formatting (`ruff format`).
- Type hints where they help the reader; public functions get a short
  docstring.
- No new runtime dependencies without discussion in an issue first.

## Releases

Maintainer-only. The version lives in **one place** for the package —
`src/memshelf_mcp/__init__.py:__version__` (`pyproject.toml` reads it via
hatch's dynamic version) — and is repeated in `server.json` for the MCP
Registry (`version` and `packages[0].version`). To cut a release:

1. Bump `__version__`, both `server.json` fields and the examples in
   `adapters/claude-desktop/README.md`; turn the `[Unreleased]` section of
   `CHANGELOG.md` into the release section.
2. Merge that, then tag the merge commit with the version the package
   declares and push the tag:

   ```bash
   V=$(python -c "import memshelf_mcp; print(memshelf_mcp.__version__)")
   git tag "v$V"
   git push origin "v$V"
   ```

`release.yml` takes it from there: a gate (tag = `__version__` =
`server.json`, lint, tests) → PyPI via trusted publishing → the MCP Registry
via GitHub OIDC → the desktop bundles attached to the GitHub release. No
stored secrets are involved; a failed late step can be retried by pushing
the same tag again.

## Code of conduct

Be kind. See [`CODE_OF_CONDUCT.md`](CODE_OF_CONDUCT.md).
