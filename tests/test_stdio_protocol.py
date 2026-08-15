"""End-to-end tests over the real stdio transport, with a real MCP client.

Everything else in this suite talks to ``memshelf_mcp.tools`` directly, and
``test_server.py`` only imports the module and checks that the decorated
functions exist. That leaves the transport itself untested: "the module
imports" has been standing in for "the server answers".

The gap matters most exactly when it is most expensive to discover — a change
to how the server is *declared*, such as the SDK major bump these tests were
written for. After the two-line port to ``MCPServer`` every in-process test
stayed green, which says nothing about whether a client can still talk to the
thing.

Kept deliberately few and slow-but-shallow: one handshake with the tool
roster, one tool call whose result must reflect real shelf state, one proof
that a silent server fails instead of hanging. Depth belongs in the fast
in-process tests; this file exists to prove the wire.

Driven with ``asyncio.run`` inside ordinary sync tests on purpose: the client
API is async, but adding ``pytest-asyncio`` to dev deps for three tests would
be a dependency for punctuation.
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
from pathlib import Path

import pytest

pytest.importorskip("mcp", reason="the MCP client SDK is needed to drive the transport")

from docshelf_mcp.core.shelf import Shelf  # noqa: E402
from mcp import ClientSession, StdioServerParameters  # noqa: E402
from mcp.client.stdio import stdio_client  # noqa: E402

from memshelf_mcp import __version__  # noqa: E402
from memshelf_mcp.core.shelve import shelve  # noqa: E402

# A cold interpreter plus the handshake is seconds, not milliseconds, and CI
# runs this on more than one OS. The cap is generous on purpose: a flaky
# timeout here would teach people to rerun the job, which is worse than the
# gap this file closes.
#
# Seconds as a float — mcp 2.x types ``read_timeout_seconds`` that way, and a
# leftover ``timedelta`` is not rejected at the call site: it raises TypeError
# deep inside anyio on the first request.
WIRE_TIMEOUT = 60.0


def _flatten_exception(exc: BaseException) -> list[BaseException]:
    """Every exception in the tree: the group, its members, and their causes."""
    seen: list[BaseException] = []

    def walk(node: BaseException) -> None:
        if any(node is known for known in seen):
            return
        seen.append(node)
        for member in getattr(node, "exceptions", None) or ():
            walk(member)
        if node.__cause__ is not None:
            walk(node.__cause__)

    walk(exc)
    return seen


def _looks_like_timeout(exc: BaseException) -> bool:
    """True for "the wait ran out", whatever type the SDK wraps it in.

    Matched on the message rather than a concrete class on purpose: the class
    lives in the SDK and pinning it here would break this file on a rename
    that changes nothing about the behaviour under test.
    """
    return isinstance(exc, TimeoutError) or "timed out" in str(exc).lower()


def _server(shelf_root: Path) -> StdioServerParameters:
    """Spawn the server the way a desktop client does — the module entry point.

    ``sys.executable -m memshelf_mcp`` rather than the ``memshelf-mcp``
    script: the console script may not be on PATH in a bare checkout, and the
    module path exercises the same ``main()``.
    """
    return StdioServerParameters(
        command=sys.executable, args=["-m", "memshelf_mcp"], env=dict(os.environ)
    )


def _seeded_shelf(tmp_path: Path) -> Path:
    """A shelf with exactly one episode, whose title the assertions look for."""
    root = tmp_path / "shelf"
    Shelf(root).init(name="Protocol shelf", default_categories=["topics", "sessions"])
    shelve(
        root,
        slug="2026-08-03-protocol-fixture",
        kind="topic",
        digest=(
            "Фикстура протокольного теста: эпизод существует затем, чтобы вызов "
            "инструмента по проводу возвращал состояние конкретной полки, а не "
            "пустой, но корректно оформленный ответ. Проверяющий запрашивает этот "
            "эпизод через memshelf_recall и убеждается, что сервер читал именно "
            "тот каталог, который ему передали параметром."
        ),
        sections={"Decisions": "Полка засеяна одним эпизодом для проверки провода."},
        date="2026-08-03",
    )
    return root


def _run(coro):
    return asyncio.run(coro)


def test_client_completes_the_handshake_and_sees_every_tool(tmp_path: Path):
    """The wire test: a client connects, initializes, and gets the tool list.

    Asserted as a superset rather than an exact set — this file is about the
    transport, and pinning the roster here would make it a second place to
    update whenever a tool is added. ``test_server.py`` owns the exact list.
    """

    async def scenario():
        async with stdio_client(_server(_seeded_shelf(tmp_path))) as (read, write):
            async with ClientSession(read, write, read_timeout_seconds=WIRE_TIMEOUT) as session:
                init = await session.initialize()
                assert init.server_info.name

                # `serverInfo.version` is invisible from inside the process: the
                # SDK defaults it to "" and every in-process test stays green on
                # the empty string, so the handshake is the only place this can
                # be caught (#83). Compared against the package version rather
                # than merely being non-empty — "some version" and "this build's
                # version" are different claims, and the second is the one a host
                # relies on once the same code ships five ways.
                assert init.server_info.version == __version__, init.server_info

                names = {tool.name for tool in (await session.list_tools()).tools}
                assert {
                    "memshelf_shelve",
                    "memshelf_recall",
                    "memshelf_index",
                    "memshelf_doctor",
                } <= names, sorted(names)

    _run(scenario())


def test_tool_call_over_the_wire_returns_real_shelf_state(tmp_path: Path):
    """A call must come back with this shelf's content, not merely succeed.

    An empty-but-well-formed response would satisfy "the transport works"
    while telling us nothing, so the assertion is on text that only the
    seeded episode contains.

    ``memshelf_recall`` rather than ``memshelf_index``: since #58 the index is
    a *derived* file rendered by the bot on main, so a freshly shelved episode
    is deliberately absent from it — the first draft of this test asserted on
    the index and failed for exactly that reason. Recall reads the episode
    itself, which is what "the server sees this shelf" should mean.
    """
    root = _seeded_shelf(tmp_path)

    async def scenario():
        async with stdio_client(_server(root)) as (read, write):
            async with ClientSession(read, write, read_timeout_seconds=WIRE_TIMEOUT) as session:
                await session.initialize()
                result = await session.call_tool(
                    "memshelf_recall",
                    {
                        "params": {
                            "shelf_path": str(root),
                            "episode_id": "2026-08-03-protocol-fixture",
                        }
                    },
                )
                assert not result.is_error, result.content
                payload = "".join(
                    block.text for block in result.content if getattr(block, "text", None)
                )
                assert "Полка засеяна одним эпизодом" in payload, payload

    _run(scenario())


def test_a_server_that_never_answers_fails_instead_of_hanging():
    """The cap above must be load-bearing, not a comment.

    A server that starts and then says nothing is the failure this file exists
    to catch, and it is the one an unarmed timeout punishes hardest: the job
    would sit until GitHub's six-hour limit and come back red with no
    assertion in it.

    The assertion names a *timeout* rather than accepting any exception. The
    weaker form ("something was raised") stays green while the cap is dead,
    because a wrong argument type raises too. Elapsed time does not separate
    the two either — measured on the sibling port, a dead cap returned in
    2.02s and a live one in 4.03s, both far from instant, since spawning the
    child dominates. The leaf exception does: "timed out" against TypeError.
    """
    silent = StdioServerParameters(
        command=sys.executable, args=["-c", "import time; time.sleep(3600)"]
    )
    started = time.monotonic()

    async def scenario():
        async with stdio_client(silent) as (read, write):
            async with ClientSession(read, write, read_timeout_seconds=2.0) as session:
                await session.initialize()

    with pytest.raises(BaseException) as caught:
        _run(scenario())
    elapsed = time.monotonic() - started

    raised = _flatten_exception(caught.value)
    assert not any(isinstance(exc, AssertionError) for exc in raised)
    assert any(_looks_like_timeout(exc) for exc in raised), (
        "nothing in the failure says the request timed out, so the cap was not "
        "what stopped it: " + "; ".join(f"{type(exc).__name__}: {exc}" for exc in raised)
    )
    assert elapsed < 60, f"waited {elapsed:.0f}s — the cap did not fire at all"
