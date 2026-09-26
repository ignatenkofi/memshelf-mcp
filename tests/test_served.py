"""The served-code verdict rides on the tool's own answer (#125, #158).

`doctor` has reported `served-code-differs` since #125, and both incidents
behind that finding were still found sideways, hours late, because nobody runs
doctor when a tool merely looks buggy: the served copy lagged, a merged fix did
not act, and the symptom read as a defect of the tool. These tests hold the
server to saying it in the response — first key, the same code doctor uses,
both hashes, both paths — and to the two outcomes that must NOT become a
per-call warning: «same» adds nothing, and «unknown» is said once, in the
handshake, with the way to make it known.

Every fixture is a fake shelf with a fake checkout beside it, so each test can
fail: the checkout is a copy of the served package with one line added, an
exact copy, or absent.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import time
from pathlib import Path

import pytest

pytest.importorskip("mcp")

from docshelf_mcp.core.shelf import Shelf  # noqa: E402

from memshelf_mcp import served, server  # noqa: E402
from memshelf_mcp.core import freshness  # noqa: E402


def _shelf(tmp_path: Path) -> Path:
    root = tmp_path / "shelf"
    Shelf(root).init(name="t", default_categories=["topics"])
    return root


def _copy_of_served(target: Path, *, one_line_off: bool) -> Path:
    shutil.copytree(
        served.served_dir(), target, ignore=shutil.ignore_patterns("__pycache__", "*.pyc")
    )
    if one_line_off:
        init = target / "__init__.py"
        init.write_text(
            init.read_text(encoding="utf-8") + "# one line the served copy does not have\n",
            encoding="utf-8",
        )
    return target


def _checkout_beside(root: Path, *, one_line_off: bool) -> Path:
    """A memshelf-mcp checkout in the documented place: next to the shelf."""
    return _copy_of_served(
        root.parent / "memshelf-mcp" / "src" / "memshelf_mcp", one_line_off=one_line_off
    )


def _call(tool: str, arguments: dict) -> tuple[dict, str]:
    """Call over the boundary, the way the transport does; return (payload, raw text)."""
    result = asyncio.run(server.mcp.call_tool(tool, arguments))
    text = "".join(block.text for block in result.content if getattr(block, "text", None))
    return json.loads(text), text


@pytest.fixture(autouse=True)
def _no_ambient_checkout(monkeypatch):
    """The developer's own `$MEMSHELF_CHECKOUT` must not decide these tests."""
    monkeypatch.delenv("MEMSHELF_CHECKOUT", raising=False)
    monkeypatch.delenv("MEMSHELF_SHELF_PATH", raising=False)


# --- differs -----------------------------------------------------------------


def test_a_checkout_one_line_off_puts_the_warning_first(tmp_path):
    root = _shelf(tmp_path)
    checkout = _checkout_beside(root, one_line_off=True)

    payload, text = _call("memshelf_index", {"shelf_path": str(root)})

    assert payload["status"] == "ok", payload  # the answer itself is untouched…
    assert list(payload)[0] == served.KEY, list(payload)  # …and the warning comes first
    warning = payload[served.KEY]
    assert warning.startswith(f"{served.CODE}:"), warning
    assert served.served_sha()[:12] in warning, warning
    assert freshness.package_sha(checkout)[:12] in warning, warning
    assert str(served.served_dir()) in warning and str(checkout) in warning, warning
    # At most one such warning per response.
    assert text.count(served.CODE) == 1, text


def test_the_error_envelope_carries_the_warning_too(tmp_path, monkeypatch):
    """A stale copy hides best behind an error — #158's disguise was a tool "bug"."""
    root = _shelf(tmp_path)
    _checkout_beside(root, one_line_off=True)

    failed, _ = _call(
        "memshelf_recall", {"shelf_path": str(root), "episode_id": "2026-01-01-nothing"}
    )
    assert failed["status"] == "error", failed
    assert list(failed)[0] == served.KEY, list(failed)

    # The other error path: validation refused the call before any wrapper ran,
    # so the shelf is taken from the environment, as the tools themselves do.
    monkeypatch.setenv("MEMSHELF_SHELF_PATH", str(root))
    refused, _ = _call("memshelf_index", {"shelf_path": 5})
    assert refused["type"] == "ValidationError", refused
    assert list(refused)[0] == served.KEY, list(refused)


def test_doctor_keeps_its_own_finding_and_the_envelope_gets_the_same_code(tmp_path):
    """Doctor semantics are unchanged; the envelope only repeats the verdict."""
    root = _shelf(tmp_path)
    _checkout_beside(root, one_line_off=True)

    payload, _ = _call("memshelf_doctor", {"shelf_path": str(root)})

    finding = next(f for f in payload["findings"] if f["code"] == "served-code-differs")
    assert finding["level"] == "error"
    assert payload[served.KEY].startswith("served-code-differs:")
    assert served.CODE == finding["code"]


def test_the_explicit_checkout_wins_as_it_does_for_doctor(tmp_path):
    root = _shelf(tmp_path)
    elsewhere = _copy_of_served(tmp_path / "elsewhere" / "src" / "memshelf_mcp", one_line_off=True)
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("MEMSHELF_CHECKOUT", str(elsewhere))
        payload, _ = _call("memshelf_index", {"shelf_path": str(root)})
    assert str(elsewhere) in payload[served.KEY], payload[served.KEY]


# --- same ---------------------------------------------------------------------


def test_an_identical_checkout_adds_nothing(tmp_path):
    root = _shelf(tmp_path)
    _checkout_beside(root, one_line_off=False)

    payload, text = _call("memshelf_index", {"shelf_path": str(root)})

    assert served.KEY not in payload, payload
    assert served.CODE not in text
    assert served.judge(str(root)).outcome == "same"


# --- unknown ------------------------------------------------------------------


def test_no_checkout_is_silent_per_call_and_named_at_initialize(tmp_path):
    root = _shelf(tmp_path)  # nothing beside it

    payload, text = _call("memshelf_index", {"shelf_path": str(root)})
    assert served.KEY not in payload, payload
    assert served.CODE not in text

    verdict = served.judge(str(root))
    assert verdict.outcome == "unknown"
    line = served.instructions(str(root))
    assert "UNKNOWN" in line, line
    assert "MEMSHELF_CHECKOUT" in line, line  # how to make it known
    assert str(root.parent / "memshelf-mcp") in line, line  # where it was looked for


def test_a_failed_judgement_is_unknown_not_a_failed_call(tmp_path, monkeypatch):
    root = _shelf(tmp_path)
    _checkout_beside(root, one_line_off=True)

    def _broken(*args, **kwargs):
        raise PermissionError("no read access to the checkout")

    monkeypatch.setattr(freshness, "package_sha", _broken)

    payload, _ = _call("memshelf_index", {"shelf_path": str(root)})
    assert payload["status"] == "ok", payload
    assert served.KEY not in payload, payload
    verdict = served.judge(str(root))
    assert verdict.outcome == "unknown"
    assert "PermissionError" in verdict.reason


# --- opt-out ------------------------------------------------------------------


def test_the_opt_out_silences_the_call_and_is_named_at_initialize(tmp_path, monkeypatch):
    root = _shelf(tmp_path)
    _checkout_beside(root, one_line_off=True)
    # Positive control: without the opt-out this fixture warns.
    assert served.KEY in _call("memshelf_index", {"shelf_path": str(root)})[0]

    monkeypatch.setenv(served.WARNING_ENV, "0")

    payload, _ = _call("memshelf_index", {"shelf_path": str(root)})
    assert served.KEY not in payload, payload
    assert served.judge(str(root)).outcome == "disabled"
    line = served.instructions(str(root))
    assert "OFF" in line and served.WARNING_ENV in line, line


# --- cost: hash once, not per call -----------------------------------------------


@pytest.fixture
def counted_package_sha(monkeypatch):
    calls: list[Path] = []
    real = freshness.package_sha

    def _counting(package_dir):
        calls.append(Path(package_dir))
        return real(package_dir)

    monkeypatch.setattr(freshness, "package_sha", _counting)
    return calls


def test_the_served_package_is_hashed_once_per_process(counted_package_sha):
    served._reset_for_tests()
    first = served.served_sha()
    assert served.served_sha() == first
    assert len(counted_package_sha) == 1


def test_the_reference_is_hashed_once_until_head_moves_or_the_ttl_passes(
    tmp_path, counted_package_sha
):
    root = _shelf(tmp_path)
    checkout = _checkout_beside(root, one_line_off=True)
    git_dir = root.parent / "memshelf-mcp" / ".git"
    git_dir.mkdir()
    head = git_dir / "HEAD"
    head.write_text("ref: refs/heads/main\n", encoding="utf-8")
    served._reset_for_tests()

    _call("memshelf_index", {"shelf_path": str(root)})
    assert len(counted_package_sha) == 2  # served + reference, once each
    _call("memshelf_index", {"shelf_path": str(root)})
    _call("memshelf_search", {"shelf_path": str(root), "query": "anything"})
    assert len(counted_package_sha) == 2, counted_package_sha  # the second call recomputed nothing

    # HEAD moves (a checkout, a pull): the reference is read again.
    head.write_text("ref: refs/heads/some-other-branch\n", encoding="utf-8")
    served.reference_sha(checkout)
    assert len(counted_package_sha) == 3, counted_package_sha

    # Nothing moved, but the TTL has passed: read again, and not before.
    served.reference_sha(checkout, now=time.monotonic() + served.REFERENCE_TTL_S - 1)
    assert len(counted_package_sha) == 3, counted_package_sha
    served.reference_sha(checkout, now=time.monotonic() + served.REFERENCE_TTL_S + 1)
    assert len(counted_package_sha) == 4, counted_package_sha


def test_a_worktree_checkout_is_fingerprinted_through_its_gitdir_file(tmp_path):
    """`.git` may be a file pointing at the real git dir (git worktree)."""
    root = _shelf(tmp_path)
    checkout = _checkout_beside(root, one_line_off=False)
    real_git = tmp_path / "main-repo" / ".git" / "worktrees" / "x"
    real_git.mkdir(parents=True)
    (real_git / "HEAD").write_text("ref: refs/heads/x\n", encoding="utf-8")
    (root.parent / "memshelf-mcp" / ".git").write_text(f"gitdir: {real_git}\n", encoding="utf-8")

    assert served._git_dir(checkout) == real_git
    fingerprint = served._head_fingerprint(real_git)
    assert fingerprint and fingerprint[0][0] == "HEAD" and fingerprint[0][1] is not None
