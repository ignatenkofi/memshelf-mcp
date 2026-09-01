"""Sync the shelf clone with its remote around a shelve (#108).

The bot commits derived files after every push, so a clone that shelved
yesterday is behind origin *by construction*, not by accident
(main-memshelf#146). A shelve that writes onto that stale base produces a
commit whose push is rejected ``(fetch first)`` in the normal state — and a
session that stops at the rejection leaves the episode local-only, which on an
ephemeral runner is the same as losing it.

Two moves, both explicit in the report:

* ``preflight`` — before anything is written: fetch, then fast-forward the
  current branch to its remote counterpart. A dirty *tracked* tree or a
  diverged branch is a refusal with the reason and the executable fix — never
  a silent continuation. A failed fetch (offline runner) does **not** refuse:
  blocking the write would trade a push problem for a lost episode; it is
  recorded loudly instead.
* ``push_with_retry`` — after the shelve commit, when asked: push; on a
  rejection, fetch + rebase and push again **exactly once**; a second
  rejection surfaces git's own words.

A clean run still says so: «pulled 0, retries 0» is a statement, not an
omission — absence of signal must not look like normal (#146 lesson, the
failure class main-memshelf#148 files under the same name).
"""

from __future__ import annotations

import os
import re
import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path


def _git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    # LC_ALL=C so the few messages this module matches on ("couldn't find
    # remote ref") are stable across locales.
    env = {**os.environ, "LC_ALL": "C"}
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, env=env)


def _err(proc: subprocess.CompletedProcess[str]) -> str:
    return (proc.stderr.strip() or proc.stdout.strip()) or f"git exited {proc.returncode}"


class DirtyShelfError(RuntimeError):
    """Tracked files are modified — syncing over them risks the very episode
    the shelve exists to protect, so the shelve refuses before writing."""


class SyncDivergedError(RuntimeError):
    """Local and remote both moved; a fast-forward is impossible. The shelve
    refuses before writing — the fix (``pull --rebase``) is in the message,
    and running it is a deliberate act, not a side effect of a shelve."""


class PushRejectedError(RuntimeError):
    """The push failed even after the one rebase-and-retry — git's own words
    are carried verbatim, because the second refusal is where guessing stops."""


@dataclass
class SyncReport:
    """What the sync around one shelve actually did — including «nothing».

    ``commits_pulled`` and ``push_retries`` are always meaningful once
    ``performed`` is true: a clean run reports ``pulled 0, retries 0``
    explicitly rather than staying silent (#108 acceptance).
    """

    performed: bool = False
    skipped_reason: str | None = None
    remote: str | None = None
    branch: str | None = None
    commits_pulled: int = 0
    push_requested: bool = False
    pushed: bool = False
    push_retries: int = 0
    #: HEAD *after* a successful push — the only sha worth quoting in a
    #: report. The pre-push sha dies with the first rebase (#146, case of
    #: 18.08); callers that cannot push should quote the episode id instead.
    final_sha: str | None = None
    #: The executable catch-up when the commit stayed local: exactly
    #: ``git -C <shelf> pull --rebase <remote> <branch> && git -C … push …``.
    hint: str | None = None
    #: #118 — branch publication. ``published_branch`` is the remote branch
    #: that now carries the shelve commit; ``compare_url`` opens the PR in one
    #: click (None when the remote's URL is not a recognizable web host).
    publish_requested: bool = False
    published_branch: str | None = None
    compare_url: str | None = None

    def line(self) -> str:
        """One human sentence for CLI output and warnings."""
        if self.skipped_reason:
            return f"sync: skipped — {self.skipped_reason}"
        parts = [f"sync: pulled {self.commits_pulled}"]
        if self.push_requested:
            parts.append(f"push retries {self.push_retries}")
            parts.append("pushed" if self.pushed else "not pushed")
        elif self.publish_requested:
            parts.append(
                f"published {self.published_branch}" if self.published_branch else "not published"
            )
        else:
            parts.append("retries 0")
        return ", ".join(parts)


def hint_command(root: Path, remote: str, branch: str) -> str:
    """The catch-up as one copy-pastable command, executable from any cwd."""
    at = shlex.quote(str(root))
    return f"git -C {at} pull --rebase {remote} {branch} && git -C {at} push {remote} {branch}"


def _sync_target(root: Path) -> tuple[tuple[str, str] | None, str | None]:
    """``((remote, branch), None)`` to sync against, or ``(None, why)``.

    The configured upstream wins; without one, ``origin`` (or the only
    remote) plus the current branch. No remote, a detached HEAD or an unborn
    branch are skip reasons, not errors — a git-local shelf (the ``init``
    default) has nothing to sync with and must keep working.
    """
    up = _git(root, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}")
    if up.returncode == 0:
        remote, _, branch = up.stdout.strip().partition("/")
        if remote and branch:
            return (remote, branch), None
    remotes = [r for r in _git(root, "remote").stdout.split() if r]
    if not remotes:
        return None, "no remote configured (git-local shelf)"
    head = _git(root, "rev-parse", "--abbrev-ref", "HEAD")
    branch = head.stdout.strip()
    if head.returncode != 0 or branch == "HEAD":
        return None, "detached HEAD or unborn branch — nothing to sync onto"
    remote = "origin" if "origin" in remotes else remotes[0]
    return (remote, branch), None


#: Tracked files the dirty-tree guard ignores. recall-log.tsv is append-only
#: read telemetry that recall writes on every logged read (on by default since
#: #112) and that no renderer ever touches — so local appends survive a
#: fast-forward, and refusing to *write memory* because someone *read memory*
#: would invert the shelf's priorities. Everything else stays guarded.
_DIRTY_EXEMPT = {"recall-log.tsv"}


def _dirty_tracked(root: Path) -> list[str]:
    """Paths of modified *tracked* files. Untracked scratch does not count —
    it cannot be damaged by a fast-forward and must not block a shelve."""
    out = _git(root, "status", "--porcelain", "--untracked-files=no").stdout
    return [line[3:] for line in out.splitlines() if line.strip() and line[3:] not in _DIRTY_EXEMPT]


def preflight(root: Path) -> SyncReport:
    """Fetch and fast-forward the shelf before anything is written (#108).

    Raises ``DirtyShelfError`` or ``SyncDivergedError`` — the two states
    where writing first and syncing later loses work. Everything else is a
    report: performed with a pulled-count, or skipped with the reason.
    """
    report = SyncReport()
    if not (root / ".git").exists():
        report.skipped_reason = "not a git repository"
        return report
    target, why = _sync_target(root)
    if target is None:
        report.skipped_reason = why
        return report
    report.remote, report.branch = target

    dirty = _dirty_tracked(root)
    if dirty:
        raise DirtyShelfError(
            "shelve refused before writing: tracked files are modified — "
            f"{', '.join(dirty[:5])}{'…' if len(dirty) > 5 else ''}. "
            "Commit or stash them first; syncing over a dirty tree is how "
            "episodes get lost (#108)."
        )

    fetched = _git(root, "fetch", report.remote, report.branch)
    if fetched.returncode != 0:
        message = _err(fetched)
        if "couldn't find remote ref" in message:
            report.skipped_reason = f"remote has no {report.branch!r} yet — nothing to sync from"
        else:
            # Offline is not a reason to refuse writing memory — but a silent
            # failed fetch is indistinguishable from a fresh clone, so it must
            # be loud (#146 / main-memshelf#148 failure class).
            report.skipped_reason = f"fetch failed: {message}"
        return report

    upstream = f"{report.remote}/{report.branch}"
    behind = _git(root, "rev-list", "--count", f"HEAD..{upstream}")
    merged = _git(root, "merge", "--ff-only", upstream)
    if merged.returncode != 0:
        raise SyncDivergedError(
            f"shelve refused before writing: this clone and {upstream} have "
            f"diverged — a fast-forward is impossible ({_err(merged)}). "
            f"Catch up deliberately, then shelve:\n  "
            + hint_command(root, report.remote, report.branch)
        )
    report.performed = True
    report.commits_pulled = int(behind.stdout.strip() or 0)
    return report


def _web_url(remote_url: str) -> str | None:
    """The remote as a browsable https URL, or None when there isn't one.

    ``git@host:owner/repo(.git)`` and ``ssh://git@host/owner/repo`` normalize;
    a filesystem path or an exotic scheme yields None — the publish still
    succeeded, there is just no link to fabricate.
    """
    url = remote_url.strip()
    if url.endswith(".git"):
        url = url[:-4]
    if url.startswith(("https://", "http://")):
        return url
    m = re.match(r"^(?:ssh://)?git@([^:/]+)[:/](.+)$", url)
    if m:
        return f"https://{m.group(1)}/{m.group(2)}"
    return None


def publish_branch(root: Path, report: SyncReport, branch_stem: str) -> None:
    """Publish the shelve commit as a *new* remote branch (#118).

    For a shelf whose ``main`` requires a PR, ``push_with_retry`` cannot help:
    the rejection is policy, not a race, and the retry dies the same death —
    while the session that could fix anything is already over. This path never
    touches the protected branch: the commit stays on the local branch (recall
    keeps answering from it) and goes to origin as ``shelve/<slug>``, pushed
    ``HEAD:refs/heads/…`` so the checkout never switches branches.

    The name is deterministic from the slug — two sessions closing two topics
    the same day cannot collide. The same slug from a second session *should*
    collide (same name = same episode); that rejection is retried exactly once
    under a name qualified by the commit itself, so a half-landed previous
    attempt cannot brick the shelve.

    ``final_sha`` is set only after the push lands, and — unlike the
    rebase-retry path — it equals local HEAD: nothing rewrote history, so the
    sha is stable enough to quote (main-memshelf#146 rule).
    """
    report.publish_requested = True
    if report.remote is None or report.branch is None:
        target, why = _sync_target(root)
        if target is None:
            raise PushRejectedError(f"branch publish requested, but {why}")
        report.remote, report.branch = target

    head = _git(root, "rev-parse", "HEAD").stdout.strip()
    name = f"shelve/{branch_stem}"
    first = _git(root, "push", report.remote, f"HEAD:refs/heads/{name}")
    if first.returncode != 0:
        qualified = f"{name}-{head[:7]}"
        second = _git(root, "push", report.remote, f"HEAD:refs/heads/{qualified}")
        if second.returncode != 0:
            raise PushRejectedError(
                f"branch publish rejected for {name!r} and {qualified!r}; git says:\n"
                f"{_err(second)}\n(first attempt: {_err(first)})"
            )
        name = qualified
    report.published_branch = name
    report.final_sha = head
    remote_url = _git(root, "remote", "get-url", report.remote).stdout
    web = _web_url(remote_url)
    if web is not None:
        report.compare_url = f"{web}/compare/{report.branch}...{name}?expand=1"


def push_with_retry(root: Path, report: SyncReport) -> None:
    """Push the shelve commit; on rejection, rebase and retry exactly once.

    Mutates ``report`` in place: ``push_retries``, ``pushed``,
    ``commits_pulled`` (rebase pulls count too) and ``final_sha`` — the
    post-push HEAD, the only sha a report should quote (#108).
    """
    report.push_requested = True
    if report.remote is None or report.branch is None:
        target, why = _sync_target(root)
        if target is None:
            raise PushRejectedError(f"push requested, but {why}")
        report.remote, report.branch = target

    first = _git(root, "push", report.remote, report.branch)
    if first.returncode != 0:
        # One retry, whatever the rejection: for a non-fast-forward the rebase
        # is the fix; for anything else (auth, protection) the retry is a
        # no-op that ends in the same git message — surfaced verbatim below.
        fetched = _git(root, "fetch", report.remote, report.branch)
        if fetched.returncode != 0:
            raise PushRejectedError(
                f"push rejected ({_err(first)}); the retry's fetch then failed too: {_err(fetched)}"
            )
        upstream = f"{report.remote}/{report.branch}"
        behind = _git(root, "rev-list", "--count", f"HEAD..{upstream}")
        rebased = _git(root, "rebase", upstream)
        if rebased.returncode != 0:
            _git(root, "rebase", "--abort")
            raise PushRejectedError(
                f"push rejected ({_err(first)}); the retry's rebase onto "
                f"{upstream} failed and was aborted: {_err(rebased)}"
            )
        report.commits_pulled += int(behind.stdout.strip() or 0)
        report.push_retries = 1
        second = _git(root, "push", report.remote, report.branch)
        if second.returncode != 0:
            raise PushRejectedError(
                "push rejected twice; after one pull-rebase retry git says:\n" + _err(second)
            )
    report.pushed = True
    report.final_sha = _git(root, "rev-parse", "HEAD").stdout.strip() or None
