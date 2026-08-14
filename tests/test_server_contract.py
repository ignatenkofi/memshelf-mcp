"""Every MCP tool must *return* its failure, never raise it.

`server.py` is fourteen thin wrappers of the same shape: validate, call the
typed entry point in `tools.py`, serialize. Each one wraps the call in
``try/except`` and turns a failure into ``{"status": "error", ...}`` — because
an exception escaping a tool becomes a protocol-level error, and the caller
gets a transport failure instead of the structured payload the tool documents.

Nothing checked that contract. `test_server.py` asserts the functions exist,
and the stdio tests drive two of the fourteen; a wrapper that lost its
``except`` — a plausible copy-paste slip in a file of near-identical blocks —
would surface only to whoever called that one tool.

The roster is discovered, not listed: a tool added without the wrapper is
caught the day it lands rather than the day someone updates this file.

The wrappers are only half the boundary, though. Input validation runs *before*
a wrapper is entered, so until #85 a malformed call left as plain text — and
this file could not see it: ``_placeholder`` only ever builds **valid** inputs,
so the tests above pass while the whole invalid-input class stays uncovered. The
last two tests drive ``call_tool`` instead of the functions, which is the only
path where validation happens at all.
"""

from __future__ import annotations

import asyncio
import json
import types
from typing import Any, get_type_hints

import pytest

from memshelf_mcp import server


def _call_over_the_boundary(tool_name: str, arguments: dict[str, Any]) -> str:
    """Call a tool the way the transport does, and return the text it answered.

    ``server.mcp.call_tool`` rather than the wrapper function: validation lives
    between the two, and it is the thing under test here.
    """
    result = asyncio.run(server.mcp.call_tool(tool_name, arguments))
    return "".join(block.text for block in result.content if getattr(block, "text", None))


def _tool_functions() -> dict[str, types.FunctionType]:
    """Every ``memshelf_*`` callable the server module exposes."""
    return {
        name: obj
        for name, obj in vars(server).items()
        if name.startswith("memshelf_") and callable(obj)
    }


def _placeholder(model, shelf_path: str):
    """Build the tool's input model with a value for every required field.

    The point is to reach the wrapper, not to make a meaningful call — the
    values are deliberately implausible so the underlying operation fails and
    the error path is what gets exercised.
    """
    values: dict[str, Any] = {}
    for field_name, field in model.model_fields.items():
        # `shelf_path` is optional (it falls back to $MEMSHELF_SHELF_PATH), but
        # this test must aim every call at a shelf that is not there — the whole
        # point is a doomed call. So it is filled in before the required check.
        if field_name == "shelf_path":
            values[field_name] = shelf_path
            continue
        if not field.is_required():
            continue
        annotation = field.annotation
        if annotation is int:
            values[field_name] = 1
        elif annotation is bool:
            values[field_name] = False
        elif annotation is list or getattr(annotation, "__origin__", None) is list:
            values[field_name] = []
        else:
            # `kind`, `method` and friends are constrained strings; a value
            # outside the allowed set would fail validation before reaching the
            # wrapper, so use one the models accept.
            values[field_name] = {
                "kind": "topic",
                "method": "discover",
            }.get(field_name, "placeholder")
    return model(**values)


def test_the_roster_is_not_empty():
    """Guard the guard: an empty discovery would make every test below vacuous."""
    tools = _tool_functions()
    assert len(tools) >= 14, sorted(tools)


@pytest.mark.parametrize("tool_name", sorted(_tool_functions()))
def test_tool_returns_a_json_envelope_instead_of_raising(tool_name: str, tmp_path):
    """A doomed call must come back as JSON, not as an exception."""
    func = _tool_functions()[tool_name]
    hints = get_type_hints(func)
    model = hints["params"]
    missing_shelf = str(tmp_path / "no-such-shelf")

    try:
        raw = func(_placeholder(model, missing_shelf))
    except Exception as exc:  # noqa: BLE001 — that is exactly what must not happen
        pytest.fail(f"{tool_name} raised {type(exc).__name__} instead of returning: {exc}")

    assert isinstance(raw, str), f"{tool_name} returned {type(raw).__name__}, not a JSON string"
    payload = json.loads(raw)
    assert isinstance(payload, dict), f"{tool_name} returned a JSON {type(payload).__name__}"


def test_the_error_envelope_carries_a_diagnosis(tmp_path):
    """`status: error` is not enough — the caller needs to know what broke.

    Checked on one tool rather than all fourteen: the envelope is built in a
    single helper (`_error_response`), so one call proves its shape, while the
    parametrized test above proves every wrapper reaches it.
    """
    from memshelf_mcp.tools import RecallInput

    raw = server.memshelf_recall(
        RecallInput(shelf_path=str(tmp_path / "absent"), episode_id="2026-01-01-nothing")
    )
    payload = json.loads(raw)

    assert payload["status"] == "error", payload
    assert payload["error"], "error text is empty — nothing to act on"
    assert payload["type"], "exception type missing — nothing to branch on"


@pytest.mark.parametrize(
    ("label", "arguments"),
    [
        # A field of the wrong type: the shape of every malformed call a model
        # makes.
        ("wrong type", {"params": {"shelf_path": 5}}),
        # No shelf anywhere — what a fresh desktop install hits first, since the
        # model-level check for "neither argument nor $MEMSHELF_SHELF_PATH" is
        # itself a validation error.
        ("no shelf configured", {}),
    ],
)
def test_invalid_input_returns_the_envelope_too(label: str, arguments: dict[str, Any], monkeypatch):
    """Validation failures are failures: they leave through the same envelope.

    Before #85 these came back as plain text (``Error executing tool …: 1
    validation error …``), so a caller written against the documented envelope —
    the desktop bundle check, for one — got a ``JSONDecodeError`` instead of a
    diagnosis. The parametrized test above cannot reach this: its inputs are
    valid by construction.
    """
    monkeypatch.delenv("MEMSHELF_SHELF_PATH", raising=False)

    raw = _call_over_the_boundary("memshelf_index", arguments)

    payload = json.loads(raw)  # the assertion is that this line does not raise
    assert payload["status"] == "error", payload
    assert payload["error"], f"{label}: error text is empty — nothing to act on"
    assert payload["type"] == "ValidationError", payload


def test_flat_arguments_are_accepted_as_well_as_nested(tmp_path, monkeypatch):
    """The obvious call shape must work, not merely fail informatively (#84).

    Every tool's published schema nests its arguments under ``params``, which is
    not what most MCP servers publish and not what the tool description implies;
    a caller who writes them flat used to get an error about a field that appears
    nowhere in the documented interface.

    Asserted against the *same* answer the nested form gives, rather than merely
    "no crash": a flat call that quietly reached a different shelf — or none —
    would satisfy a weaker assertion.
    """
    monkeypatch.delenv("MEMSHELF_SHELF_PATH", raising=False)
    missing = str(tmp_path / "no-such-shelf")

    flat = json.loads(_call_over_the_boundary("memshelf_index", {"shelf_path": missing}))
    nested = json.loads(
        _call_over_the_boundary("memshelf_index", {"params": {"shelf_path": missing}})
    )

    assert flat == nested, (flat, nested)
    assert missing in flat["error"], flat
