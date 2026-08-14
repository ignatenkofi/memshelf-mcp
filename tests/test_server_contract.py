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
"""

from __future__ import annotations

import json
import types
from typing import Any, get_type_hints

import pytest

from memshelf_mcp import server


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
