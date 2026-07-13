# Contributing to memshelf-mcp

Thanks for considering a contribution. The project is currently in the
**design phase** (see [`docs/`](docs/)) — right now the most valuable
contributions are design critique, prior-art pointers, and experience
reports from running the M0 kit on your own shelves.

## Where things are decided

- [`docs/DECISIONS.md`](docs/DECISIONS.md) — the decision log. If your PR
  changes a design decision, add a row.
- [GitHub Issues](https://github.com/ignatenkofi/memshelf-mcp/issues) —
  the triaged backlog, organized by milestone (`M1:` / `M2:` / `M3:`
  title prefixes). Start there before proposing something big.
- [`docs/ROADMAP.md`](docs/ROADMAP.md) — what ships when, with exit
  criteria.

## Reporting a problem

Open an issue. For design-phase problems, describe the scenario the
current design handles badly — concrete workflows beat abstract concerns.
For problems with the M0 prompt-only kit
([`adapters/claude-code/`](adapters/claude-code/)): what you asked the
agent to do, what the skill produced, and what you expected instead.

## Suggesting a feature

Open an issue with `feature:` in the title. Describe the use case first,
then the proposed solution. Check the annoyance log in
[`docs/M0.md`](docs/M0.md) — your pain may already be tracked.

## Submitting a pull request

1. Fork the repo and create a topic branch off `main`.
2. Docs-only for now: keep internal links relative and working; run a
   quick link check over your changes.
3. Update `CHANGELOG.md` under `[Unreleased]` and, if you changed a
   design decision, `docs/DECISIONS.md`.
4. Keep PRs focused — one concern per PR.

Once M1 code lands, this document will grow the usual dev-env / lint /
test instructions (mirroring
[docshelf-mcp](https://github.com/ignatenkofi/docshelf-mcp/blob/main/CONTRIBUTING.md)).

## Code of conduct

Be kind. See [`CODE_OF_CONDUCT.md`](CODE_OF_CONDUCT.md).
