"""Machine-readable per-shelf pattern packs (issue #16).

``POLICY.md`` is prose the agent reads; this makes a shelf's PII/secret rules
*machine-readable* so the shelve redaction pass (#6) and ``doctor`` (#13) both
enforce them — e.g. a course shelf that forbids student identifiers and allows
only role codes. The pack is a flat file, one ``kind <regex>`` rule per line, so
the very same file also feeds the bash pre-commit guard (#32); nothing has to
parse YAML.

Reference behavior is the sqst PII discipline: a fixed mask with no length
leak, homograph-safe. memshelf masks a match as ``«redacted:<kind>»`` — fixed
per kind, so length never leaks; writing both the Cyrillic and Latin forms of a
shape (homograph safety) is the pack author's job.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

#: Conventional pack filename at the shelf root (parallel to POLICY.md).
POLICY_PATTERNS_FILENAME = "POLICY.patterns"

# kind = first non-space run; regex = the rest of the line (may contain spaces).
_RULE_RE = re.compile(r"^(?P<kind>\S+)\s+(?P<rx>.+)$")


@dataclass
class PatternPack:
    """A parsed pack: usable ``(kind, regex)`` rules plus any parse errors."""

    patterns: list[tuple[str, str]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def parse_pack(text: str) -> PatternPack:
    """Parse pack text. Blank lines and ``#`` comments are ignored; a malformed
    line or an uncompilable regex becomes an error (the rule is skipped, not
    fatal) so ``doctor`` can report it and shelving still applies the good ones."""
    pack = PatternPack()
    for lineno, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = _RULE_RE.match(line)
        if not m:
            pack.errors.append(f"line {lineno}: expected `<kind> <regex>`, got {raw!r}")
            continue
        kind, regex = m.group("kind"), m.group("rx").strip()
        try:
            re.compile(regex)
        except re.error as exc:
            pack.errors.append(f"line {lineno}: invalid regex for {kind!r}: {exc}")
            continue
        pack.patterns.append((kind, regex))
    return pack


def load_pattern_pack(shelf_root: str | Path) -> PatternPack:
    """Load ``<shelf>/POLICY.patterns``; an absent file is an empty pack."""
    path = Path(shelf_root).expanduser() / POLICY_PATTERNS_FILENAME
    if not path.is_file():
        return PatternPack()
    return parse_pack(path.read_text(encoding="utf-8"))
