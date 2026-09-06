"""What the server puts in every session's static prefix, and a cap on it (#111).

MCP tool schemas are not paid for per call. They ride in the static prefix of
every session where the server is connected and are re-read on every turn,
called or not — so a description is charged to sessions that never touch the
shelf. The audit in #111 measured 167 contexts over six days: four memshelf
recall/index calls against 284.66M tokens of prefix re-reads.

#133 acted on that by trimming every tool description to one selection-oriented
sentence and moving the long form to `docs/tools.md`. Measured on the
`tools/list` payload, that move was:

    description chars   5748 -> 2401     (longest tool 890 -> 254)
    whole payload      28340 -> 24911 bytes

Nothing held the decision in place. Descriptions are prose next to the code
that uses them, and prose grows back one helpful clause at a time; the next
tool added would have been written in the old style with nothing to say so.
That is what this file is for — not a budget for the whole payload, which is
mostly not descriptions (see the breakdown below), but a cap on the one thing
#133 actually decided.

Deliberately NOT asserted: a total-payload budget. 70% of the payload is the
derived input schema — `$defs`, the `params` envelope, per-field descriptions,
pydantic's auto-generated `title` for every field — and shrinking that means
choosing which tools stay visible and whether rare operations get grouped.
Those are open questions in #111 and the owner's to answer; a number pinned
here would quietly pre-empt them.

Run this file directly for the breakdown behind those questions::

    python tests/test_prefix_cost.py
"""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path

import pytest

pytest.importorskip("mcp")

from memshelf_mcp import server  # noqa: E402

#: Longest description the one-sentence rule leaves today is 254 chars
#: (`memshelf_shelve`: what it does, what it does not, where the detail is). The cap
#: is that measurement plus room to word a new tool comfortably — not a budget
#: anyone negotiated. It is well under the pre-#133 *median* of 367, so a
#: description written in the old long form fails here rather than landing.
MAX_DESCRIPTION_CHARS = 320

#: Where the detail went when it left the schema. The trim is only honest
#: while this file actually covers every tool.
LONG_FORM = Path(__file__).resolve().parents[1] / "docs" / "tools.md"


def _published_tools() -> list[dict]:
    """The `tools/list` payload as a client receives it, not as we author it.

    Descriptions are authored as docstrings and schemas are derived from the
    type hints, so the only honest place to measure is after the SDK has built
    the payload.
    """
    tools = asyncio.run(server.mcp.list_tools())
    return [t.model_dump(mode="json", exclude_none=True, by_alias=True) for t in tools]


def _compact(payload) -> int:
    return len(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))


def test_every_tool_description_stays_one_selection_sentence():
    too_long = {
        t["name"]: len(t.get("description") or "")
        for t in _published_tools()
        if len(t.get("description") or "") > MAX_DESCRIPTION_CHARS
    }
    assert not too_long, (
        f"tool descriptions over {MAX_DESCRIPTION_CHARS} chars: {too_long}. "
        "A description is re-read in the static prefix of every turn of every "
        f"session the server is connected to, called or not (#111) — move the "
        f"detail to {LONG_FORM.name} and leave the sentence that helps a caller "
        "choose this tool over its neighbours."
    )


def test_the_long_form_covers_exactly_the_published_tools():
    """#133 moved detail out of the prefix and into `docs/tools.md`.

    That trade only holds while the long form is complete: a tool published
    with a one-sentence description and no section anywhere is not "trimmed",
    it is undocumented. The reverse — a section for a tool that no longer
    exists — sends a reader looking for a tool the server does not answer to.

    Deliberately checked against the *published* roster rather than a list in
    this file, so a tool added tomorrow is covered without anyone remembering
    to come back here.
    """
    published = {t["name"] for t in _published_tools()}
    documented = set(re.findall(r"^## `(memshelf_[a-z_]+)`", LONG_FORM.read_text("utf-8"), re.M))
    assert published == documented, (
        f"published but not in {LONG_FORM.name}: {sorted(published - documented)}; "
        f"in {LONG_FORM.name} but not published: {sorted(documented - published)}"
    )


def test_the_payload_is_mostly_schema_not_prose():
    """Guards the docstring above, so the reasoning cannot go stale silently.

    If descriptions ever became the bulk of the payload again, the "do not pin
    a total budget, it is mostly schema" argument would be wrong, and this file
    would be arguing from a measurement that no longer holds.
    """
    payload = _published_tools()
    total = _compact(payload)
    descriptions = sum(_compact(t.get("description") or "") for t in payload)
    assert descriptions * 4 < total, (
        f"descriptions are {descriptions} of {total} payload chars — over a "
        "quarter. Re-read this file's docstring: it claims the payload is "
        "dominated by derived schema, and that is no longer true."
    )


def _breakdown() -> str:
    payload = _published_tools()
    lines = [f"tools/list payload: {_compact(payload)} chars, {len(payload)} tools", "", "by key:"]
    by_key: dict[str, int] = {}
    for tool in payload:
        for key, value in tool.items():
            by_key[key] = by_key.get(key, 0) + _compact(value)
    for key, size in sorted(by_key.items(), key=lambda kv: -kv[1]):
        lines.append(f"  {key:16s} {size:6d}")
    lines += ["", "by tool:"]
    for tool in sorted(payload, key=lambda t: -_compact(t)):
        lines.append(
            f"  {tool['name']:22s} total={_compact(tool):6d} "
            f"desc={len(tool.get('description') or ''):5d}"
        )
    return "\n".join(lines)


if __name__ == "__main__":  # pragma: no cover - the instrument, not the guard
    print(_breakdown())
