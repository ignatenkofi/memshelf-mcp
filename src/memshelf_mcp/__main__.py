"""``python -m memshelf_mcp`` — the same entry point as the console script.

Kept because the console script is not always on PATH: a bare checkout, a
venv that was not activated, or a client config that spawns the interpreter
directly all reach the server this way. It is also what the stdio protocol
tests spawn, so the path they exercise is the one a desktop client uses.
"""

from memshelf_mcp.server import main

if __name__ == "__main__":
    main()
