"""Entry point of the standalone bundle — shipped as ``server/main.py``.

The bundle carries its own interpreter and its own ``server/lib``, so all this
does is point Python at that directory and hand over to the real server. It
stays import-light and syntax-plain on purpose: whatever goes wrong here has to
be able to reach the host's log, not die in a traceback about f-strings.
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
LIB = os.path.join(HERE, "lib")

if not os.path.isdir(LIB):
    sys.stderr.write("memshelf: bundle is incomplete - " + LIB + " is missing\n")
    raise SystemExit(1)

sys.path.insert(0, LIB)

# The host substitutes ${user_config.shelf_path} into this variable. When the
# user leaves the setting empty, some hosts pass the empty string and some pass
# the template through unexpanded — either way there is no shelf, and an unset
# variable is what the server expects to see in that case.
_shelf = os.environ.get("MEMSHELF_SHELF_PATH", "")
if not _shelf.strip() or "${" in _shelf:
    os.environ.pop("MEMSHELF_SHELF_PATH", None)

from memshelf_mcp.server import main  # noqa: E402 — sys.path is set up above

main()
