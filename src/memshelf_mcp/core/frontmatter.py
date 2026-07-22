"""Parse an episode's frontmatter, tolerating the H1-first on-disk layout.

The frontmatter is the **first** ``---``-fenced block, optionally preceded by a
single H1 and blank lines — because docshelf's ``add_document`` prepends
``# <id>`` (ARCHITECTURE Layer 2 / shelf-spec v0 §5.1). A byte-0-only parser
finds nothing in a real episode; this one skips the leading H1.

Values are returned as raw strings — enough for the checks ``memshelf_doctor``
needs (``id``, ``kind``). No YAML dependency: the episode frontmatter is a flat
``key: value`` block.
"""

from __future__ import annotations


def parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """Return ``(fields, body)``. A missing/empty block yields ``({}, text)``."""
    lines = text.splitlines()
    i = 0
    while i < len(lines) and not lines[i].strip():  # leading blanks
        i += 1
    if i < len(lines) and lines[i].lstrip().startswith("# "):  # a single H1
        i += 1
        while i < len(lines) and not lines[i].strip():
            i += 1
    if i >= len(lines) or lines[i].strip() != "---":
        return {}, text

    fields: dict[str, str] = {}
    j = i + 1
    while j < len(lines) and lines[j].strip() != "---":
        if ":" in lines[j]:
            key, _, val = lines[j].partition(":")
            fields[key.strip()] = val.strip()
        j += 1
    body = "\n".join(lines[j + 1 :]) if j < len(lines) else ""
    return fields, body
