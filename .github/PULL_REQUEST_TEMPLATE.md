<!--
Thanks for the PR! A few quick notes to help review go smoothly:
- Keep PRs focused. One concern per PR is much easier to land.
- Link the issue this addresses (if any) below.
- Run what CI runs before pushing:
  ruff check . && ruff format --check . && python -c "import docshelf_mcp, mcp" && pytest -q
-->

## What this PR does

<!-- A short summary of the change. -->

## Why

<!-- The problem you're solving, or the issue this addresses. -->

Closes #

## Notes for the reviewer

<!-- Anything unusual? Trade-offs? Things you'd like extra eyes on? -->

## Checklist

- [ ] `ruff check .` and `ruff format --check .` pass
- [ ] `pytest -q` passes
- [ ] Tests added or updated (if touching `src/`)
- [ ] Internal doc links still resolve
- [ ] `CHANGELOG.md` updated under `[Unreleased]`
- [ ] `docs/DECISIONS.md` updated if this changes a design decision
