import json

import pytest

from memshelf_mcp.core.importer import (
    TranscriptError,
    detect_format,
    discover,
    extract,
)

# --- fixtures ---------------------------------------------------------------

# A tool_use payload and a tool_result payload that MUST NOT survive stripping.
_TOOL_INPUT = "SELECT_SECRET_XYZ"
_TOOL_OUTPUT = "HUGE_TOOL_OUTPUT_NOISE"

_CLAUDE_JSON = [
    {
        "uuid": "u1",
        "name": "Trip planning",  # title does NOT contain the marker
        "chat_messages": [
            {"sender": "human", "content": [{"type": "text", "text": "How do we reconcile?"}]},
            {
                "sender": "assistant",
                "content": [
                    {"type": "text", "text": "Here is the plan."},
                    {"type": "tool_use", "name": "run", "input": {"cmd": _TOOL_INPUT}},
                    {"type": "tool_result", "content": _TOOL_OUTPUT},
                    {"type": "text", "text": "We finalized the quarterly reconciliation approach."},
                ],
            },
        ],
    },
    {
        "uuid": "u2",
        "name": "quarterly reconciliation",  # marker only in the TITLE
        "chat_messages": [
            {"sender": "human", "content": [{"type": "text", "text": "Book flights to Lisbon."}]}
        ],
    },
]

_CLAUDE_CODE_JSONL = [
    {"type": "user", "message": {"role": "user", "content": "Start the audit please"}},
    {
        "type": "assistant",
        "message": {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "Running the audit."},
                {"type": "tool_use", "name": "bash", "input": {"command": "ls -la /etc"}},
            ],
        },
    },
    {
        "type": "user",
        "message": {
            "role": "user",
            "content": [{"type": "tool_result", "content": "file1\nfile2\nfile3"}],
        },
    },
    {
        "type": "assistant",
        "message": {
            "role": "assistant",
            "content": [
                {"type": "thinking", "thinking": "internal-reasoning-noise"},
                {"type": "text", "text": "Audit complete: 3 files."},
            ],
        },
    },
    {"type": "summary", "summary": "a rotated session"},
]


@pytest.fixture
def claude_json(tmp_path):
    p = tmp_path / "conversations.json"
    p.write_text(json.dumps(_CLAUDE_JSON), encoding="utf-8")
    return p


@pytest.fixture
def claude_code(tmp_path):
    p = tmp_path / "session.jsonl"
    p.write_text("\n".join(json.dumps(e) for e in _CLAUDE_CODE_JSONL), encoding="utf-8")
    return p


# --- format detection -------------------------------------------------------


def test_detect_format(claude_json, claude_code):
    assert detect_format(claude_json) == "claude-json"
    assert detect_format(claude_code) == "claude-code-jsonl"


# --- discovery by content marker (#8) ---------------------------------------


def test_discover_matches_content_not_title(claude_json):
    # The marker is in u1's BODY and in u2's TITLE only — discovery must pick u1.
    result = discover(claude_json, markers=["quarterly reconciliation"])
    assert result["matched"] == 1
    assert result["conversations"][0]["id"] == "u1"
    assert result["conversations_scanned"] == 2


def test_discover_lists_all_without_markers(claude_json):
    result = discover(claude_json)
    assert result["matched"] == 2
    # metadata only — no full body leaks through (snippet is bounded)
    assert all(len(c["snippet"]) <= 160 for c in result["conversations"])


def test_discover_requires_all_markers(claude_json):
    assert discover(claude_json, markers=["reconcile", "Lisbon"])["matched"] == 0


# --- extraction + tool-block stripping (#10) --------------------------------


def test_extract_strips_tool_blocks(claude_json, tmp_path):
    out = tmp_path / "clean.md"
    result = extract(claude_json, select="u1", out=str(out))
    assert result["stripped_blocks"] == 2
    text = out.read_text(encoding="utf-8")
    # conversational text survives...
    assert "We finalized the quarterly reconciliation approach." in text
    assert "Here is the plan." in text
    # ...tool payloads do not (#10)
    assert _TOOL_INPUT not in text
    assert _TOOL_OUTPUT not in text
    assert result["messages"] == 2


def test_extract_default_path_outside_shelf(claude_json):
    result = extract(claude_json, select="u1")
    # default working file lives under the temp dir, never in a shelf
    assert "memshelf-import" in result["output_path"]
    assert result["output_path"].endswith(".md")


def test_extract_does_not_mutate_source(claude_json):
    before = claude_json.read_text(encoding="utf-8")
    extract(claude_json, select="u1")
    assert claude_json.read_text(encoding="utf-8") == before  # raw input untouched (#6)


def test_extract_reports_noise_ratio(claude_code, tmp_path):
    result = extract(claude_code, out=str(tmp_path / "s.md"))
    assert result["source_tokens"] >= result["approx_tokens"]
    assert 0.0 <= result["noise_ratio"] <= 1.0


# --- claude code jsonl ------------------------------------------------------


def test_jsonl_single_conversation_strips_tools(claude_code, tmp_path):
    out = tmp_path / "clean.md"
    result = extract(claude_code, out=str(out))  # single conversation -> no selector needed
    text = out.read_text(encoding="utf-8")
    assert result["messages"] == 3  # 2 user/assistant text turns + the final assistant turn
    assert result["stripped_blocks"] == 2  # one tool_use + one tool_result
    assert "Audit complete: 3 files." in text
    assert "internal-reasoning-noise" not in text  # thinking dropped
    assert "ls -la /etc" not in text
    assert "file1" not in text


# --- selection + error paths ------------------------------------------------


def test_extract_ambiguous_selection_raises(claude_json):
    with pytest.raises(TranscriptError, match="conversations in the file"):
        extract(claude_json)  # 2 conversations, no select / markers


def test_extract_by_marker_when_unique(claude_json, tmp_path):
    result = extract(claude_json, markers=["quarterly reconciliation"], out=str(tmp_path / "m.md"))
    assert result["id"] == "u1"


def test_missing_file_raises(tmp_path):
    with pytest.raises(TranscriptError, match="no such transcript"):
        discover(tmp_path / "nope.json")


def test_unknown_selector_raises(claude_json):
    with pytest.raises(TranscriptError, match="no conversation matched"):
        extract(claude_json, select="does-not-exist")
