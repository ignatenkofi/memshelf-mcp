"""Entry point of the uv bundle — shipped as ``src/server.py``.

uv resolves the environment from the bundle's ``pyproject.toml`` and installs
the bundled ``memshelf_mcp`` alongside it, so this only has to normalize the
host-supplied setting and start the server.
"""

import os

# See launcher.py: an empty setting can arrive as "" or as the raw template.
_shelf = os.environ.get("MEMSHELF_SHELF_PATH", "")
if not _shelf.strip() or "${" in _shelf:
    os.environ.pop("MEMSHELF_SHELF_PATH", None)

from memshelf_mcp.server import main  # noqa: E402 — after the environment is cleaned

main()
