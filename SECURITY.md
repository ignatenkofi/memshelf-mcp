# Security Policy

## Supported versions

`memshelf-mcp` is in the design phase — no code has shipped yet. Security
fixes will apply to the latest minor release once `0.1` exists.

| Version | Supported |
|---------|-----------|
| design docs / M0 kit | ✅ (report anyway — prompt-level issues count) |

## Reporting a vulnerability

**Please do NOT open a public GitHub issue for security reports.**

Report it privately via GitHub's **"Report a vulnerability"** button under
the [Security tab](https://github.com/ignatenkofi/memshelf-mcp/security/advisories/new)
of this repository. If that doesn't work, use the contact on the
maintainer's GitHub profile ([ignatenkofi](https://github.com/ignatenkofi))
with `memshelf-mcp security` in the subject line.

### In scope even before code ships

Memory systems have prompt-level attack surface. Reports are welcome on:

- **Prompt injection via recall** — ways stored episode content could
  direct an agent's behavior despite the data-envelope rule
  (see `docs/ARCHITECTURE.md` → Failure modes).
- **PII/secret leakage paths** in the M0 kit's capture/redaction flow
  (`adapters/claude-code/skills/shelve/SKILL.md`).
- **Exfiltration routes** enabled by shelf storage-mode misconfiguration
  (`git-remote` visibility, raw-URL opt-in).

### What to include

- A clear description of the issue and its impact.
- Steps to reproduce, ideally with a minimal transcript or shelf layout.
