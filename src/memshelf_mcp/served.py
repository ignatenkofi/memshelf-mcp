"""Served-code freshness, said in the tool's own answer (#125, #158).

`doctor` already knows when the code answering a call is not the code in the
memshelf-mcp checkout next to the shelf — its ``served-code-differs`` finding
(#125). But a doctor finding is read by whoever *runs doctor*, and neither
incident behind it was found that way: a merged fix did not act, the symptom
looked like a bug in the tool, and the gap surfaced hours later, sideways. The
dogfood shelf's open thread put it plainly (2026-09-10): the lag «is visible
only to whoever ran `memshelf freshness`; the idea is to report
served-code-differs at the moment the tool is called (serverInfo or the first
warning of the envelope), so a stale bundle (#125) does not pass for a tool
bug». #158 is the same gap one release later, with a false
``digest-body-mismatch`` as the disguise.

So the verdict rides on the response, and the three outcomes are never folded:

* **differs** — the envelope opens with one ``warning`` carrying the same
  ``served-code-differs`` code doctor emits, both short hashes and where each
  lives, so the agent reads it before it trusts the result;
* **same** — nothing is added; the common case costs nothing to read;
* **unknown** — no checkout to compare with, or the comparison could not be
  made: no per-call warning, because a warning on every call on a machine with
  no sources is noise with no fix attached, but the ``initialize``
  instructions say so once, with the way to make it known
  (``$MEMSHELF_CHECKOUT``).

A host where the served copy is *meant* to differ from the checkout switches
the per-call warning off with ``MEMSHELF_FRESHNESS_WARNING=0``; the
instructions then say that it is off — which is still not «ok».

Cost is held down by two caches: the served package is hashed once per process
(it cannot change under a running interpreter without a restart), and each
reference checkout once per resolved path, re-hashed only when its git HEAD
moves or after ``REFERENCE_TTL_S``. Measured in the PR that added this.

Nothing here refuses work: a judgement that fails is ``unknown``, never a
failed call.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from memshelf_mcp import __version__
from memshelf_mcp.core import freshness
from memshelf_mcp.core.doctor import find_reference_checkout
from memshelf_mcp.tools import default_shelf_path

logger = logging.getLogger(__name__)

#: Set to "0"/"off"/"false"/"no" to keep the per-call warning out of every
#: envelope — for a host where the served copy is meant to differ.
WARNING_ENV = "MEMSHELF_FRESHNESS_WARNING"

#: The same code `doctor` emits for the same gap; one vocabulary, two carriers.
CODE = "served-code-differs"

#: The key the warning travels under. Placed first in the envelope. Singular on
#: purpose: `warnings` already means three different things across the tools
#: (a list of strings on shelve, `{code, message}` dicts on lint_digest, a
#: count on doctor), so a uniform slot had to be a new one.
KEY = "warning"

#: How long a reference checkout's hash is trusted before it is re-read even
#: when its HEAD has not moved — an uncommitted edit in the checkout does not
#: move HEAD, so time is the fallback.
REFERENCE_TTL_S = 300.0

#: What a fingerprint of a checkout's git state is read from. `HEAD` changes
#: on checkout and detached moves; `logs/HEAD` is appended on every ref update
#: the reflog records (commit, pull, reset, rebase).
_HEAD_MARKERS = ("HEAD", "logs/HEAD")

_served_sha: str | None = None
_git_dirs: dict[Path, Path | None] = {}


@dataclass
class _Reference:
    sha: str
    head: tuple
    computed_at: float


_references: dict[Path, _Reference] = {}


def enabled() -> bool:
    return os.environ.get(WARNING_ENV, "").strip().lower() not in {"0", "off", "false", "no"}


@lru_cache(maxsize=1)
def served_dir() -> Path:
    """The package directory this process imports — what actually answers."""
    return Path(__file__).resolve().parent


def served_sha() -> str:
    """Hash of the code answering this call. Computed once per process."""
    global _served_sha
    if _served_sha is None:
        _served_sha = freshness.package_sha(served_dir())
    return _served_sha


def _git_dir(checkout: Path) -> Path | None:
    """The git directory that owns ``checkout``, worktrees included, or None."""
    for parent in (checkout, *checkout.parents):
        dot = parent / ".git"
        if dot.is_dir():
            return dot
        if dot.is_file():
            try:
                text = dot.read_text(encoding="utf-8")
            except OSError:
                return None
            for line in text.splitlines():
                if line.startswith("gitdir:"):
                    target = Path(line.split(":", 1)[1].strip())
                    return target if target.is_absolute() else (parent / target).resolve()
            return None
    return None


def _head_fingerprint(git_dir: Path | None) -> tuple:
    """Cheap witness that a checkout's HEAD has moved: two stats, no git spawn."""
    if git_dir is None:
        return ()
    out = []
    for name in _HEAD_MARKERS:
        try:
            st = (git_dir / name).stat()
            out.append((name, st.st_mtime_ns, st.st_size))
        except OSError:
            out.append((name, None, None))
    return tuple(out)


def reference_sha(checkout: Path, *, now: float | None = None) -> str:
    """Hash of the checkout to judge against, cached per resolved path.

    Re-read when the checkout's HEAD fingerprint changes or the entry is older
    than ``REFERENCE_TTL_S``. ``now`` is the test seam for the clock.
    """
    now = time.monotonic() if now is None else now
    if checkout not in _git_dirs:
        _git_dirs[checkout] = _git_dir(checkout)
    head = _head_fingerprint(_git_dirs[checkout])
    cached = _references.get(checkout)
    if cached is not None and cached.head == head and now - cached.computed_at < REFERENCE_TTL_S:
        return cached.sha
    sha = served_sha() if checkout == served_dir() else freshness.package_sha(checkout)
    _references[checkout] = _Reference(sha, head, now)
    return sha


@dataclass(frozen=True)
class Verdict:
    """One of four outcomes; ``same`` is the only one that adds nothing anywhere."""

    outcome: str  # "same" | "differs" | "unknown" | "disabled"
    served_dir: Path
    served_sha: str | None = None
    checkout: Path | None = None
    reference_sha: str | None = None
    reason: str = ""

    @property
    def _served(self) -> str:
        return self.served_sha[:12] if self.served_sha else "unhashed"

    def warning(self) -> str | None:
        """The per-call warning — only when the code differs."""
        if self.outcome != "differs":
            return None
        assert self.reference_sha is not None and self.checkout is not None
        return (
            f"{CODE}: the code answering this call is {self._served} at {self.served_dir}, "
            f"the memshelf-mcp checkout at {self.checkout} is {self.reference_sha[:12]} — a "
            "merged fix may not be serving (#125, #158); weigh this answer accordingly. "
            "Resync the consumer (reinstall the package / refresh the extension) or update "
            "the checkout; `memshelf freshness` probes every installed consumer; "
            f"{WARNING_ENV}=0 silences this where the difference is intended."
        )

    def instructions_line(self) -> str:
        """One line for the `initialize` instructions: the outcome, said aloud."""
        head = f"memshelf-mcp {__version__}: served code {self._served} at {self.served_dir}"
        if self.outcome == "differs":
            assert self.reference_sha is not None
            return (
                f"{head} DIFFERS from the memshelf-mcp checkout at {self.checkout} "
                f"({self.reference_sha[:12]}) — a merged fix may not be serving (#125, #158). "
                f"Every tool response opens with a `{KEY}` saying so until the two match."
            )
        if self.outcome == "same":
            return f"{head} matches the memshelf-mcp checkout at {self.checkout}."
        if self.outcome == "disabled":
            return (
                f"{head}; the served-code freshness warning is OFF ({WARNING_ENV}=0), so "
                "this code is not compared with any checkout."
            )
        return (
            f"{head}; served-code freshness is UNKNOWN — {self.reason}. To make it known, "
            "clone memshelf-mcp next to the shelf or set "
            "MEMSHELF_CHECKOUT=/path/to/memshelf-mcp/src/memshelf_mcp; `memshelf_doctor` "
            "reports the same state as `freshness-unknown`."
        )


def judge(shelf_path: str | None = None, *, now: float | None = None) -> Verdict:
    """Compare the served code with the checkout that goes with ``shelf_path``.

    The checkout is resolved exactly as `doctor` resolves it
    (``find_reference_checkout``): ``$MEMSHELF_CHECKOUT`` first, then
    ``memshelf-mcp`` next to the shelf. With no shelf named the fallback is
    ``$MEMSHELF_SHELF_PATH``; with neither, only the explicit override can
    answer. Never raises.
    """
    here = served_dir()
    if not enabled():
        return Verdict("disabled", here, reason=f"{WARNING_ENV}=0")
    try:
        mine = served_sha()
        shelf = (shelf_path or "").strip() or default_shelf_path()
        root = Path(shelf).expanduser().resolve() if shelf else None
        checkout = find_reference_checkout(root)
        if checkout is None:
            if root is None:
                reason = "no shelf named on this call or in $MEMSHELF_SHELF_PATH, and $MEMSHELF_CHECKOUT unset"
            else:
                reason = (
                    f"no memshelf-mcp checkout at {root.parent / 'memshelf-mcp'} (next to the "
                    "shelf) and $MEMSHELF_CHECKOUT unset"
                )
            return Verdict("unknown", here, served_sha=mine, reason=reason)
        theirs = reference_sha(checkout, now=now)
    except Exception as exc:  # noqa: BLE001 — a failed judgement must not fail the call
        logger.debug("served-code freshness could not be judged: %s", exc)
        return Verdict("unknown", here, reason=f"{type(exc).__name__}: {exc}")
    outcome = "same" if mine == theirs else "differs"
    return Verdict(outcome, here, served_sha=mine, checkout=checkout, reference_sha=theirs)


def annotate(payload: Any, shelf_path: str | None = None) -> Any:
    """Open a tool's envelope with the warning when — and only when — code differs.

    The single place the server composes a response passes through, success and
    error alike. A non-dict payload is returned untouched: the warning has no
    slot there and must not turn a documented shape into another.
    """
    if not isinstance(payload, dict):
        return payload
    text = judge(shelf_path).warning()
    if text is None:
        return payload
    return {KEY: text, **{k: v for k, v in payload.items() if k != KEY}}


def instructions(shelf_path: str | None = None) -> str:
    """The one line the `initialize` response carries about served-code freshness."""
    return judge(shelf_path).instructions_line()


def _reset_for_tests() -> None:
    """Forget per-process memoisation. Tests only."""
    global _served_sha
    _served_sha = None
    _git_dirs.clear()
    _references.clear()
    served_dir.cache_clear()
