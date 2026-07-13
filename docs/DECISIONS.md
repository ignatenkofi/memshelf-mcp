# memshelf — Decision log

Newest last. Earlier entries were made while the design lived as RFC-0001 in
[docshelf-mcp](https://github.com/ignatenkofi/docshelf-mcp/tree/main/docs/rfc/0001-memshelf).

| Date | Decision | By |
|---|---|---|
| 2026-07-13 | RFC draft created. Form = companion project over docshelf (not a docshelf subpackage, not a prompt-only pattern); primary surface v1 = Claude Code / Cowork. | draft, author-merged (#42) |
| 2026-07-13 | Portability requirement: core/episode format/on-disk shelf contain nothing host-specific; three-ring model (core → MCP+CLI surfaces → host adapters); triggers are adapter territory; prompts are core assets. | author note, merged (#43) |
| 2026-07-13 | Prior-art survey (`LANDSCAPE.md`): niche confirmed open — no project combines episodes+digests+git+INDEX navigation. Design amendments: mechanical eviction, injection budgets/KV-cache discipline, prompt-injection-on-recall defense, platform-collision defense, subagent-deposit trigger (v2), memory-tool adapter ring. Direction kept, scope sharpened. | draft, author-merged (#44) |
| 2026-07-13 | Name + placement resolved: `memshelf`, repo `ignatenkofi/memshelf-mcp` (PyPI `memshelf` / `memshelf-mcp` checked free). Repo created by author; seeded from the RFC. | author |
| 2026-07-13 | Storage defaults reworked per author's git concerns: modes `plain` / `git-local` (default — auto-commit, **no remote configured**) / `git-remote` (opt-in, private-only, `doctor`-enforced, `autopush: false`). Accidental-exfiltration threat handled by absence of a remote, not absence of git. "Store inside Claude" (artifacts/attachments) assessed: unsuitable as canonical store (breaks portability), adopted as optional read-mirror idea (M3 experiment). | author + draft |
| 2026-07-13 | Positioning: hero scenarios added to MANIFEST — (1) the weeks-long dialog that must not die (primary wedge), (2) context advisor: surface where the window goes, flag shelvable episodes (promoted into M2; also the onboarding moment), (3) archive as raw material: tags/graph/retrospectives/fork-a-thread (M3). Many-repos overload noted as advisor diagnosis territory, not a direct memshelf fix. | author + draft |
