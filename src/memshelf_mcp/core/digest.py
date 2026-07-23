"""Layer-3 digest contract validation.

The digest is written by the agent at shelve time — while the episode is still
in context — and mechanically checked here: length, referents, secrets.
Quality beyond that stays the agent's responsibility (``docs/ARCHITECTURE.md``
→ Layer 3). Every error carries a fix, so a rejected shelve tells the caller
exactly what to change (issue #6 acceptance).

Bilingual on purpose: the shelves this serves hold both English and Russian.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from memshelf_mcp.core.redact import scan

MAX_WORDS = 120

# First-person-plural pronouns assume the reader shared the session — exactly
# what a digest must not do. Hard reject. The Russian possessives are
# enumerated as exact forms rather than the open prefix `наш\w*`, which also
# matched the unrelated verb «нашёл/нашла/нашли» ("found") — a false positive
# hit on the very first dogfooded shelve (#45).
_WE = re.compile(
    r"\b(we|we're|we've|we'd|our|ours|us|мы|нам|нас"
    r"|наш(?:его|ему|ими|их|ем|ею|ей|им|а|е|и|у)?)\b",
    re.IGNORECASE,
)

# A sentence opening on a bare demonstrative usually points back at unnamed
# prior context. Warned, not rejected — plenty of them are fine.
_BARE_OPENER = re.compile(
    r"(?:^|[.!?]\s+)(it|this|that|they|these|those|это|эти|они)\b",
    re.IGNORECASE,
)

# Cheap cue that the digest records a decision / open thread. Absence is only a
# warning ("when applicable" — a pure reference digest legitimately has none).
_SUBSTANCE = re.compile(
    r"\b(?:decid|reject|chose|chosen|open|todo|verdict|"
    r"реш|отклон|выбра|открыт|итог)\w*",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Finding:
    level: str  # "error" | "warning"
    code: str
    message: str


@dataclass
class ValidationResult:
    word_count: int
    findings: list[Finding]

    @property
    def ok(self) -> bool:
        """True when nothing at error level was found (warnings are allowed)."""
        return not any(f.level == "error" for f in self.findings)

    @property
    def errors(self) -> list[Finding]:
        return [f for f in self.findings if f.level == "error"]

    @property
    def warnings(self) -> list[Finding]:
        return [f for f in self.findings if f.level == "warning"]

    def report(self) -> str:
        if not self.findings:
            return f"digest ok ({self.word_count} words)"
        return "\n".join(f"[{f.level}] {f.code}: {f.message}" for f in self.findings)


def _word_count(text: str) -> int:
    return len(text.split())


def validate_digest(digest: str, *, max_words: int = MAX_WORDS) -> ValidationResult:
    """Check a digest against the Layer-3 contract. Errors block a shelve."""
    findings: list[Finding] = []
    text = digest.strip()

    if not text:
        findings.append(Finding("error", "empty", "digest is empty; write 1–120 words."))
        return ValidationResult(0, findings)

    words = _word_count(text)
    if words > max_words:
        findings.append(
            Finding(
                "error",
                "too-long",
                f"digest is {words} words; cap is {max_words}. Cut {words - max_words}.",
            )
        )

    we = sorted({m.group(0).lower() for m in _WE.finditer(text)})
    if we:
        findings.append(
            Finding(
                "error",
                "referent-we",
                f"first-person referents {we} assume shared context; name the "
                "actor instead (a reader has zero session history).",
            )
        )

    secrets = scan(text)
    if not secrets.clean:
        findings.append(
            Finding(
                "error",
                "secret",
                f"digest contains secret-shaped strings ({secrets.summary()}); "
                "remove or redact them.",
            )
        )

    bare = sorted({m.group(1).lower() for m in _BARE_OPENER.finditer(text)})
    if bare:
        findings.append(
            Finding(
                "warning",
                "referent-bare",
                f"sentences open on bare {bare}; consider a named subject.",
            )
        )

    if not _SUBSTANCE.search(text):
        findings.append(
            Finding(
                "warning",
                "thin",
                "no decision / open-thread cue found; confirm this is a pure "
                "reference or research digest.",
            )
        )

    return ValidationResult(words, findings)
