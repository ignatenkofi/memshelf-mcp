"""Host-agnostic core: episode schema, digest contract, redaction, shelf ops.

Pure library — no MCP, no host lifecycle hooks, no network. The MCP server,
the CLI, and the per-host adapters all sit on top of this (see
``docs/ARCHITECTURE.md`` → Portability model).
"""
