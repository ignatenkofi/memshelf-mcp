"""memshelf — a working-memory shelf for AI agents, built on docshelf.

Offload closed conversation topics to a docshelf shelf as digest-indexed
Markdown episodes, then recall them by index instead of re-deriving. See
``docs/`` for the design (MANIFEST, ARCHITECTURE, ROADMAP).
"""

#: Single source of truth for the package version — pyproject.toml reads it
#: via hatch's dynamic version ([tool.hatch.version] path = ...), and the
#: release gate refuses a tag that disagrees with it or with server.json.
__version__ = "0.2.0"
