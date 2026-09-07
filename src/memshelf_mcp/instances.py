"""Notice a second server instance working the same shelf (#115).

Claude Desktop was observed starting **two** process trees for one extension
launch. Both lived; only one was wired to the host. The orphan sat for 25+
minutes with `stdout` and `stderr` on `/dev/null`, which is what makes the
obvious design wrong: a "warn at startup if someone else is already here"
check is printed *by the newcomer*, and in the observed incident the newcomer
was exactly the process that could not write anywhere. A guard that is mute on
its own red fixture is not a guard.

So the reporting is inverted: every instance **registers** itself, and the
instance that is actually being called — the one the host kept, hence the one
whose descriptors lead to a log — reports the neighbour it finds. Registration
is cheap and silent; the warning rides on the first shelf-scoped call.

Scope, stated because it is not total:

* the record is written when the shelf becomes known — from
  ``$MEMSHELF_SHELF_PATH`` at startup, or from the first call that names a
  shelf. A bare server that is never called registers nothing, and nothing
  can notice it. That is a real hole, not an oversight: with no shelf there
  is no key to group instances by.
* liveness is ``os.kill(pid, 0)``. A recycled pid can therefore read as
  alive; the window is bounded by pruning dead records on every scan, and the
  cost of a false positive is one stderr line, never a refused call.
* the registry lives **outside** the shelf. The shelf is a git repository and
  a stray file there would ride into a diff.

Nothing here refuses work: detection is advisory. Every entry point swallows
its own errors, because a shelf must stay usable on a read-only or unusual
state directory.
"""

from __future__ import annotations

import atexit
import hashlib
import json
import logging
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

#: Overrides where instance records are kept. Tests set it; a host may too.
STATE_DIR_ENV = "MEMSHELF_STATE_DIR"

#: Set to "0"/"off"/"false" to keep the registry from being written at all.
DISABLE_ENV = "MEMSHELF_INSTANCE_REGISTRY"

_registered: set[Path] = set()
_warned: set[tuple[str, frozenset[int]]] = set()


@dataclass(frozen=True)
class Instance:
    """One registered server process, as read back from its record."""

    pid: int
    started_at: str
    shelf: str


def _enabled() -> bool:
    return os.environ.get(DISABLE_ENV, "").strip().lower() not in {"0", "off", "false", "no"}


def state_root() -> Path:
    """Where instance records live — never inside a shelf.

    ``$MEMSHELF_STATE_DIR`` wins; then the XDG state directory; then the
    system temp directory, which is always writable in the environments this
    server runs in.
    """
    override = os.environ.get(STATE_DIR_ENV, "").strip()
    if override:
        return Path(override).expanduser()
    xdg = os.environ.get("XDG_STATE_HOME", "").strip()
    if xdg:
        return Path(xdg).expanduser() / "memshelf-mcp"
    home = Path.home()
    if str(home) not in {"", "/"}:
        return home / ".local" / "state" / "memshelf-mcp"
    return Path(tempfile.gettempdir()) / "memshelf-mcp"


def _shelf_key(shelf: str) -> str:
    """Group instances by the shelf they resolve to, not by the string passed.

    Two hosts naming the same directory through a symlink or a relative path
    are the same shelf and must collide here, or the detector misses exactly
    the case it exists for.
    """
    try:
        resolved = str(Path(shelf).expanduser().resolve())
    except OSError:
        resolved = str(Path(shelf).expanduser())
    return hashlib.sha256(resolved.encode("utf-8")).hexdigest()[:16]


def _record_dir(shelf: str) -> Path:
    return state_root() / "instances" / _shelf_key(shelf)


def _alive(pid: int) -> bool:
    """Whether a pid is still running.

    ``PermissionError`` means the process exists and belongs to someone else —
    alive. Anything else (including Windows, where signal 0 is not a probe) is
    read as alive too: a false "still there" costs one advisory line, while a
    false "gone" would prune a live neighbour and silence the detector.
    """
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return True
    return True


def register(shelf: str) -> Path | None:
    """Record this process as working ``shelf``. Idempotent, never raises."""
    if not shelf or not _enabled():
        return None
    path = _record_dir(shelf) / f"{os.getpid()}.json"
    if path in _registered:
        return path
    payload = {
        "pid": os.getpid(),
        "started_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "shelf": shelf,
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload) + "\n", encoding="utf-8")
    except OSError as exc:
        logger.debug("instance registry unavailable (%s): %s", path, exc)
        return None
    _registered.add(path)
    atexit.register(_unregister, path)
    return path


def _unregister(path: Path) -> None:
    _registered.discard(path)
    try:
        path.unlink()
    except OSError:
        pass


def neighbours(shelf: str) -> list[Instance]:
    """Live *other* instances registered on ``shelf``; prunes dead records."""
    if not shelf:
        return []
    directory = _record_dir(shelf)
    try:
        entries = sorted(directory.glob("*.json"))
    except OSError:
        return []
    found: list[Instance] = []
    for entry in entries:
        try:
            data = json.loads(entry.read_text(encoding="utf-8"))
            pid = int(data["pid"])
        except (OSError, ValueError, KeyError, TypeError):
            continue
        if pid == os.getpid():
            continue
        if not _alive(pid):
            try:
                entry.unlink()
            except OSError:
                pass
            continue
        found.append(
            Instance(
                pid=pid,
                started_at=str(data.get("started_at", "")),
                shelf=str(data.get("shelf", shelf)),
            )
        )
    return found


def observe(shelf: str, *, log: logging.Logger | None = None) -> list[Instance]:
    """Register, then report any neighbour — once per distinct set of pids.

    Called from the shelf-scoped call path, so the process that speaks is the
    one the host is actually talking to. Returns the neighbours it found, for
    tests and for callers that want to act on them.
    """
    if not shelf or not _enabled():
        return []
    register(shelf)
    found = neighbours(shelf)
    if not found:
        return []
    key = (_shelf_key(shelf), frozenset(i.pid for i in found))
    if key in _warned:
        return found
    _warned.add(key)
    (log or logger).warning(
        "another memshelf-mcp instance is working this shelf: %s (shelf=%s, this pid=%d). "
        "A second writer on a git-backed shelf is the class `resolve` exists for; "
        "if the host started it by accident, the extra process can be ended safely.",
        ", ".join(f"pid {i.pid} since {i.started_at}" for i in found),
        shelf,
        os.getpid(),
    )
    return found


def _reset_for_tests() -> None:
    """Forget per-process memoisation. Tests only."""
    _registered.clear()
    _warned.clear()
