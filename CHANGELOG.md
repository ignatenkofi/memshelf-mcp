# Changelog

All notable changes to memshelf-mcp will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project will adhere to [Semantic Versioning](https://semver.org/spec/v2.0.0.html)
once code ships.

## [Unreleased] — design phase

### Added
- Design package seeded from docshelf-mcp RFC-0001: manifest, architecture
  (episode format, digest contract, storage modes, portability model),
  prior-art landscape, roadmap M0–M3, decision log, worked examples.
- M0 prompt-only kit (`adapters/claude-code/`): `/shelve` skill with live
  and import modes; recall-rule CLAUDE.md snippet; install guide (three
  paths, self-instrumenting shelf recommended).
- M0 protocol and results (`docs/M0.md`): Case A closed — 17 episodes
  imported on a live private shelf, recall test 5/5, INDEX 1,370 tokens,
  query 1,765 tokens (~97% cheaper than conversational source), annoyance
  log ×10 = the M1 backlog (issues #6–#20).
- Community files, ASCII logo, MIT license.

### Fixed
- `/shelve` skill and recall snippet now push `git-remote` shelves in
  ephemeral cloud sessions right after the commit; `docs/M0.md` states the
  push is not optional in M0 (was: commit-only, so committed episodes could
  die with the container) (#22).
- `/shelve` Python fallback computes `category` from `kind` (`<kind-mapped>`)
  instead of hardcoding `topics`, so `research`/`session` episodes no longer
  misfile into `topics/` (#23).
- README status softened from "M0 validated" to "M0 in progress: Case A
  closed, Case B running", matching `docs/M0.md` and `docs/ROADMAP.md` (#24).
- Documented the real on-disk episode shape (H1 title first, frontmatter
  second, per docshelf `add_document`) and the frontmatter parser rule in
  ARCHITECTURE Layer 2, the worked example, and the skill (#30).

### Notable design decisions (see `docs/DECISIONS.md`)
- Storage is local-first: `plain` / `git-local` (default, no remote) /
  `git-remote` (opt-in, private-only).
- Import mode is first-class; raw transcripts are input-only, never stored.
- Token accounting (`ledger.tsv`) is built into the core loop.
- Repository made public 2026-07-13; the dogfood shelf stays private.
