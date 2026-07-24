"""Remote-visibility probe for the memory-shelf ``doctor`` (MANIFEST principle 8).

A memory shelf holds conversation memory; its git remote must never be
*publicly* visible, or a push exfiltrates that memory. ``doctor`` enforces this,
but only on request — resolving visibility needs the network, and the offline
doctor stays deterministic. All network I/O lives here, isolated and injectable,
so the check is unit-testable without a network.

The probe is **provider-agnostic**. Rather than special-casing GitHub/GitLab
APIs, it asks the git smart-HTTP endpoint that every git host exposes:

    GET <repo>.git/info/refs?service=git-upload-pack   (unauthenticated)

A public repository answers ``200`` with the ref advertisement; a private (or
existence-hidden) one answers ``401``/``404``. SSH remotes are normalized to
their ``https`` form for the probe — visibility is a property of the repo, not
of the transport used to push to it. A local/``file:`` remote is not reachable
over the network at all, so it is reported private without a request.
"""

from __future__ import annotations

import re
import subprocess
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass

# Visibility verdicts. "unknown" means the probe could not decide (network
# error, rate limit, an unusual status) — doctor treats it as a warning, never
# a hard failure, so a flaky network can't block a shelf.
PUBLIC = "public"
PRIVATE = "private"
UNKNOWN = "unknown"

_DEFAULT_TIMEOUT = 6.0

# owner/repo out of an https or scp-style git URL, credentials stripped.
_HTTPS_RE = re.compile(r"^https?://(?:[^@/]+@)?(?P<host>[^/]+)/(?P<path>.+?)(?:\.git)?/?$")
_SCP_RE = re.compile(r"^(?:ssh://)?(?:[^@]+@)?(?P<host>[^:/]+)[:/](?P<path>.+?)(?:\.git)?/?$")


@dataclass(frozen=True)
class Remote:
    """A configured git remote of the shelf."""

    name: str
    url: str


def _run_git(root, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True)


def configured_remotes(root) -> list[Remote]:
    """Every remote configured on the shelf's git repo, as ``(name, url)``.

    Empty when the shelf is not a git repo or has no remote — the default
    ``git-local`` posture, which has nothing to exfiltrate *to*.
    """
    listed = _run_git(root, "remote")
    if listed.returncode != 0:
        return []
    remotes: list[Remote] = []
    for name in listed.stdout.split():
        url = _run_git(root, "remote", "get-url", name)
        if url.returncode == 0 and url.stdout.strip():
            remotes.append(Remote(name, url.stdout.strip()))
    return remotes


def to_https_base(url: str) -> str | None:
    """Normalize a git remote URL to ``https://host/owner/repo`` for probing.

    Returns ``None`` for a remote that is not reachable over public HTTP(S) — a
    ``file:`` URL or a bare local path — which is inherently not public.
    """
    url = url.strip()
    if url.startswith(("file:", "/", ".", "~")):
        return None
    m = _HTTPS_RE.match(url)
    if m:
        return f"https://{m.group('host')}/{m.group('path')}"
    m = _SCP_RE.match(url)
    if m and "." in m.group("host"):  # a real hostname, not a local path segment
        return f"https://{m.group('host')}/{m.group('path')}"
    return None


def _http_status(url: str, timeout: float) -> int:
    """GET ``url`` and return the HTTP status, following redirects.

    Isolated so tests can inject a fake in its place (``status_fn``).
    """
    req = urllib.request.Request(url, method="GET", headers={"User-Agent": "memshelf-doctor"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 — https only
            return resp.status
    except urllib.error.HTTPError as exc:
        return exc.code


def remote_visibility(
    url: str,
    *,
    timeout: float = _DEFAULT_TIMEOUT,
    status_fn: Callable[[str, float], int] = _http_status,
) -> tuple[str, str]:
    """Probe one remote URL; return ``(verdict, detail)``.

    ``verdict`` is :data:`PUBLIC`, :data:`PRIVATE`, or :data:`UNKNOWN`.
    ``status_fn`` is the single network seam — the default performs the real
    request; tests pass a fake.
    """
    base = to_https_base(url)
    if base is None:
        return PRIVATE, "non-HTTP remote (local/file) — not reachable over the network"
    probe = f"{base}.git/info/refs?service=git-upload-pack"
    try:
        status = status_fn(probe, timeout)
    except (urllib.error.URLError, OSError) as exc:  # DNS, TLS, timeout, refused
        return UNKNOWN, f"could not reach {base} ({exc})"
    if status == 200:
        return PUBLIC, f"{base} answered the unauthenticated git endpoint (200) — it is public"
    if status in (401, 404):
        return PRIVATE, f"{base} requires authentication ({status}) — not publicly visible"
    return UNKNOWN, f"{base} returned an unexpected status ({status})"
