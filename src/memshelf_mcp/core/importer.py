"""Transcript import: the mechanical half of retro-shelving a whole dialog.

``memshelf_import`` (issue #12) prepares an exported conversation for the
shelve loop without ever pulling the raw transcript through model context or
MCP transfer. It does the three things the M0 backfill did by hand and badly
(``docs/M0.md`` annoyances #6/#8/#10):

- **#6 path in, not content in.** The input is a *file path*; an 87 MB export
  never rides in a tool argument or return value. ``extract`` writes the
  cleaned transcript to a working file on disk and returns only its path plus
  counts — the agent reads that file in slices to segment and digest.
- **#8 discover by content, not title.** ``discover`` locates the target
  conversation inside a multi-conversation export by *content markers*
  (substrings that must all appear in the body), because title matching missed
  the target chat entirely in M0.
- **#10 strip tool noise.** tool_use / tool_result blocks (measured ~94% of a
  raw Claude Code session) are dropped before anything is rendered, so what the
  agent reads is conversation, not tool output.

The cleaned working file is a transformation of input, written *outside* any
shelf; it is never committed (POLICY: raw/source material stays input-only).
Supported formats: claude.ai ``conversations.json`` and Claude Code session
JSONL. Pure stdlib — no docshelf, no network.
"""

from __future__ import annotations

import json
import re
import tempfile
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

# Content block types that are conversation (kept). Everything else — tool_use,
# tool_result, thinking, images — is dropped: #10's noise, or not text.
_TEXT_BLOCK = "text"
_STRIPPED_BLOCKS = ("tool_use", "tool_result")

FORMATS = ("claude-json", "claude-code-jsonl")


class TranscriptError(ValueError):
    """The transcript can't be parsed, or a selector matched no conversation."""


def _tok(text: str) -> int:
    """chars/4 — the project-wide token estimate (see ``docs/M0.md``)."""
    return len(text) // 4


@dataclass
class Message:
    role: str
    text: str


@dataclass
class Conversation:
    id: str
    title: str
    messages: list[Message] = field(default_factory=list)
    #: tool_use / tool_result blocks dropped while parsing this conversation.
    stripped_blocks: int = 0

    def body(self) -> str:
        return "\n".join(m.text for m in self.messages)

    @property
    def approx_tokens(self) -> int:
        return _tok(self.body())


# --------------------------------------------------------------- format sniff


def detect_format(path: Path) -> str:
    """Guess the transcript format from extension, then a content sniff."""
    suffix = path.suffix.lower()
    if suffix == ".jsonl":
        return "claude-code-jsonl"
    with path.open(encoding="utf-8", errors="replace") as fh:
        for line in fh:
            stripped = line.strip()
            if not stripped:
                continue
            # A top-level JSON array/object is a claude.ai export; a bare object
            # on its own line that is one of many is JSONL.
            if stripped[0] == "[":
                return "claude-json"
            if stripped[0] == "{":
                # One self-contained object per line -> JSONL; an opening brace
                # of a multi-line document -> claude-json.
                try:
                    json.loads(stripped)
                    return "claude-code-jsonl"
                except json.JSONDecodeError:
                    return "claude-json"
            break
    return "claude-json"


# --------------------------------------------------------------- block parsing


def _blocks_text(content: object) -> tuple[str, int]:
    """Extract conversational text from a message ``content`` value.

    ``content`` is either a plain string or a list of typed blocks. Returns the
    kept text and the count of stripped tool blocks (#10).
    """
    if isinstance(content, str):
        return content.strip(), 0
    if not isinstance(content, list):
        return "", 0
    parts: list[str] = []
    stripped = 0
    for block in content:
        if not isinstance(block, dict):
            continue
        btype = block.get("type")
        if btype == _TEXT_BLOCK:
            text = block.get("text", "")
            if isinstance(text, str) and text.strip():
                parts.append(text.strip())
        elif btype in _STRIPPED_BLOCKS:
            stripped += 1
    return "\n".join(parts), stripped


def _claude_json_message(raw: dict) -> tuple[Message | None, int]:
    """One claude.ai ``chat_messages`` entry -> ``Message`` (tool blocks off)."""
    role = raw.get("sender") or raw.get("role") or "unknown"
    text, stripped = _blocks_text(raw.get("content"))
    if not text:  # older exports carry a flat top-level "text"
        flat = raw.get("text")
        text = flat.strip() if isinstance(flat, str) else ""
    if not text:
        return None, stripped
    return Message(role=role, text=text), stripped


def _iter_claude_json(path: Path) -> Iterator[Conversation]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, dict):
        convs = data.get("conversations", data if "chat_messages" in data else [])
        if isinstance(convs, dict):
            convs = [convs]
    elif isinstance(data, list):
        convs = data
    else:
        raise TranscriptError("unrecognized claude.ai export: top level is not a list or object")
    for i, conv in enumerate(convs):
        if not isinstance(conv, dict):
            continue
        messages: list[Message] = []
        stripped = 0
        for raw in conv.get("chat_messages", conv.get("messages", [])):
            if not isinstance(raw, dict):
                continue
            msg, n = _claude_json_message(raw)
            stripped += n
            if msg:
                messages.append(msg)
        yield Conversation(
            id=str(conv.get("uuid") or conv.get("id") or i),
            title=str(conv.get("name") or conv.get("title") or f"conversation {i}"),
            messages=messages,
            stripped_blocks=stripped,
        )


def _iter_claude_code_jsonl(path: Path) -> Iterator[Conversation]:
    """A Claude Code session file is one conversation, streamed line by line."""
    messages: list[Message] = []
    stripped = 0
    session_id = path.stem
    with path.open(encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(event, dict):
                continue
            session_id = event.get("sessionId") or session_id
            message = event.get("message")
            if not isinstance(message, dict):
                continue
            role = message.get("role") or event.get("type") or "unknown"
            text, n = _blocks_text(message.get("content"))
            stripped += n
            if text:
                messages.append(Message(role=str(role), text=text))
    yield Conversation(
        id=str(session_id), title=path.name, messages=messages, stripped_blocks=stripped
    )


def iter_conversations(path: Path, fmt: str) -> Iterator[Conversation]:
    if fmt == "claude-json":
        return _iter_claude_json(path)
    if fmt == "claude-code-jsonl":
        return _iter_claude_code_jsonl(path)
    raise TranscriptError(f"unknown format {fmt!r}; expected one of {FORMATS}")


def _resolve(path: str | Path, fmt: str) -> tuple[Path, str]:
    p = Path(path).expanduser()
    if not p.is_file():
        raise TranscriptError(f"no such transcript file: {p}")
    resolved = detect_format(p) if fmt == "auto" else fmt
    if resolved not in FORMATS:
        raise TranscriptError(f"unknown format {resolved!r}; expected auto or one of {FORMATS}")
    return p, resolved


# ------------------------------------------------------------------- discover


def _matches_markers(text: str, markers: list[str]) -> bool:
    low = text.lower()
    return all(marker.lower() in low for marker in markers)


def _snippet(text: str, markers: list[str], width: int = 140) -> str:
    """A short context window around the first marker (or the head)."""
    flat = re.sub(r"\s+", " ", text).strip()
    if markers:
        i = flat.lower().find(markers[0].lower())
        if i != -1:
            start = max(0, i - width // 3)
            frag = flat[start : start + width]
            return ("…" if start else "") + frag + ("…" if start + width < len(flat) else "")
    return flat[:width] + ("…" if len(flat) > width else "")


def discover(
    path: str | Path,
    *,
    markers: list[str] | None = None,
    fmt: str = "auto",
    limit: int = 50,
) -> dict:
    """List conversations in the transcript, filtered by content ``markers``.

    Output is metadata only — id, title, message count, token estimate, and a
    snippet — never the full bodies (#6). Match is by body content, not title
    (#8).
    """
    p, resolved = _resolve(path, fmt)
    markers = markers or []
    found: list[dict] = []
    total = 0
    for conv in iter_conversations(p, resolved):
        total += 1
        if markers and not _matches_markers(conv.body(), markers):
            continue
        if len(found) < limit:
            found.append(
                {
                    "id": conv.id,
                    "title": conv.title,
                    "messages": len(conv.messages),
                    "approx_tokens": conv.approx_tokens,
                    "stripped_blocks": conv.stripped_blocks,
                    "snippet": _snippet(conv.body(), markers),
                }
            )
    return {
        "format": resolved,
        "conversations_scanned": total,
        "matched": len(found),
        "truncated": len(found) >= limit,
        "conversations": found,
    }


# -------------------------------------------------------------------- extract


def _slug(text: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "-", text).strip("-").lower()
    return slug[:60] or "conversation"


def _render_markdown(conv: Conversation, fmt: str) -> str:
    lines = [
        f"# {conv.title}",
        "",
        "_memshelf import — cleaned transcript (tool_use/tool_result stripped). "
        "Source is input-only; this working file is not part of any shelf._",
        "",
        f"<!-- conversation: {conv.id} · format: {fmt} · {len(conv.messages)} messages · "
        f"~{conv.approx_tokens} tokens · {conv.stripped_blocks} tool blocks stripped -->",
        "",
    ]
    for i, msg in enumerate(conv.messages, 1):
        lines += [f"## [{i}] {msg.role}", "", msg.text, ""]
    return "\n".join(lines).rstrip() + "\n"


def _select(conv_list: list[Conversation], select: str | None, markers: list[str]) -> Conversation:
    if select is not None:
        for conv in conv_list:
            if conv.id == select or conv.title == select:
                return conv
        if select.isdigit() and int(select) < len(conv_list):
            return conv_list[int(select)]
        raise TranscriptError(f"no conversation matched selector {select!r}; run discover first")
    if markers:
        hits = [c for c in conv_list if _matches_markers(c.body(), markers)]
        if len(hits) == 1:
            return hits[0]
        raise TranscriptError(
            f"markers matched {len(hits)} conversations; add markers or pass an explicit id"
        )
    if len(conv_list) == 1:
        return conv_list[0]
    raise TranscriptError(
        f"{len(conv_list)} conversations in the file; pass select= or markers= (run discover first)"
    )


def _work_dir() -> Path:
    return Path(tempfile.gettempdir()) / "memshelf-import"


def extract(
    path: str | Path,
    *,
    select: str | None = None,
    markers: list[str] | None = None,
    fmt: str = "auto",
    out: str | Path | None = None,
) -> dict:
    """Clean one conversation to a working file and report where it landed.

    Selection is by ``select`` (id/title/index) or by ``markers`` when it picks
    exactly one. The cleaned Markdown (tool blocks stripped, one H2 per turn) is
    written to ``out`` — default a transient file under the system temp dir,
    never inside a shelf. Returns the path and the noise ratio, not the content
    (#6): the agent reads the file itself to segment → digest → shelve.
    """
    p, resolved = _resolve(path, fmt)
    markers = markers or []
    conv = _select(list(iter_conversations(p, resolved)), select, markers)

    out_path = Path(out).expanduser() if out else _work_dir() / f"{_slug(conv.id)}.md"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(_render_markdown(conv, resolved), encoding="utf-8")

    kept_tokens = conv.approx_tokens
    raw_tokens = _tok(p.read_text(encoding="utf-8", errors="replace")) if resolved else kept_tokens
    return {
        "format": resolved,
        "id": conv.id,
        "title": conv.title,
        "output_path": str(out_path),
        "messages": len(conv.messages),
        "stripped_blocks": conv.stripped_blocks,
        "approx_tokens": kept_tokens,
        "source_tokens": raw_tokens,
        "noise_ratio": round(1 - kept_tokens / raw_tokens, 3) if raw_tokens else 0.0,
        "next": (
            "Read output_path in slices, propose an episode segmentation with token "
            "weights, confirm, then shelve each segment (mode=import) plus one session digest."
        ),
    }
