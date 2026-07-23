"""Layer-2 redaction: scrub credential-shaped strings from episode text.

Pure text-in / text-out plus a report — no file, git, or network I/O — so both
``memshelf_shelve`` (#6) and the digest validator can reuse it, and tests need
nothing external. Design: ``docs/ARCHITECTURE.md`` → Layer 2 (Redaction pass).

The pass is a safety net, not a guarantee: it catches common credential
*shapes*. Project-specific denylists (e.g. a PII pack) are layered in via
``extra_patterns``.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass


def _mask(kind: str) -> str:
    return f"«redacted:{kind}»"


def _env_secret_rewrite(m: re.Match[str]) -> str:
    # Keep the key name visible, mask only the value — the report stays legible
    # and the surrounding prose still reads ("SONAR_TOKEN=«redacted:env-secret»").
    return f"{m.group('key')}={_mask('env-secret')}"


@dataclass(frozen=True)
class _Rule:
    kind: str
    pattern: re.Pattern[str]
    # How to rewrite one match. Default swaps the whole match for the mask.
    rewrite: Callable[[re.Match[str]], str] | None = None

    def apply(self, m: re.Match[str]) -> str:
        return self.rewrite(m) if self.rewrite else _mask(self.kind)


# Order matters: most specific shapes first. Word boundaries keep ordinary
# prose ("the token bucket") from tripping the token rules.
_BUILTIN: tuple[_Rule, ...] = (
    _Rule("aws-access-key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    _Rule("sonar-token", re.compile(r"\bsqu_[0-9a-f]{40}\b")),
    _Rule("github-token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,255}\b")),
    _Rule("bearer", re.compile(r"\bBearer\s+[A-Za-z0-9._\-]{16,}", re.IGNORECASE)),
    _Rule(
        "env-secret",
        re.compile(
            r"(?P<key>\b[A-Za-z0-9_]*"
            r"(?:TOKEN|SECRET|PASSWORD|PASSWD|PWD|API[_-]?KEY|ACCESS[_-]?KEY)"
            # A value that is already a redaction marker must not re-match:
            # shelve() stores `KEY=«redacted:env-secret»`, and without the
            # lookahead scan()/doctor would flag every correctly-redacted
            # episode forever (and redact() wouldn't be idempotent).
            r"[A-Za-z0-9_]*)\s*=\s*(?P<val>(?!«redacted:)\S+)",
            re.IGNORECASE,
        ),
        rewrite=_env_secret_rewrite,
    ),
)


@dataclass(frozen=True)
class RedactionReport:
    """What the redaction pass removed, counted per kind."""

    counts: dict[str, int]

    @property
    def total(self) -> int:
        return sum(self.counts.values())

    @property
    def clean(self) -> bool:
        return self.total == 0

    def summary(self) -> str:
        if self.clean:
            return "no secrets found"
        parts = ", ".join(f"{k}×{n}" for k, n in sorted(self.counts.items()))
        return f"redacted {self.total} ({parts})"


def _compile_extra(extra: Iterable[tuple[str, str]]) -> list[_Rule]:
    return [_Rule(kind, re.compile(pattern)) for kind, pattern in extra]


def redact(
    text: str,
    *,
    extra_patterns: Iterable[tuple[str, str]] | None = None,
) -> tuple[str, RedactionReport]:
    """Return ``(redacted_text, report)``.

    ``extra_patterns`` is an iterable of ``(kind, regex)`` — a shelf's own
    denylist layered on top of the built-in credential shapes.
    """
    counts: dict[str, int] = {}
    rules = list(_BUILTIN)
    if extra_patterns:
        rules += _compile_extra(extra_patterns)

    for rule in rules:
        # rule bound as a default arg so the closure captures this iteration's
        # value, not the loop variable (ruff B023).
        def _sub(m: re.Match[str], rule: _Rule = rule) -> str:
            counts[rule.kind] = counts.get(rule.kind, 0) + 1
            return rule.apply(m)

        text = rule.pattern.sub(_sub, text)

    return text, RedactionReport(counts)


def scan(text: str, *, extra_patterns: Iterable[tuple[str, str]] | None = None) -> RedactionReport:
    """Report secret-shaped strings without rewriting.

    For the digest validator and ``memshelf_doctor``, which only need to know
    whether any secrets are present.
    """
    _, report = redact(text, extra_patterns=extra_patterns)
    return report


def scan_patterns(text: str, patterns: Iterable[tuple[str, str]]) -> RedactionReport:
    """Report matches of ONLY the given ``(kind, regex)`` patterns — no builtins.

    ``doctor`` uses this to attribute at-rest findings to a shelf's own policy
    pack (#16) separately from the built-in credential shapes.
    """
    counts: dict[str, int] = {}
    for kind, pattern in patterns:
        n = sum(1 for _ in re.finditer(pattern, text))
        if n:
            counts[kind] = counts.get(kind, 0) + n
    return RedactionReport(counts)
