"""Regenerate every derived file on the shelf from the episodes (#58).

Four files on a shelf are **derived**: ``ledger.tsv``, ``INDEX.md``, each
category's ``.meta.json``, and ``stats.svg``. Until now ``shelve`` wrote all
four inline, which made every concurrent shelve a conflict: two sessions
closing two topics touch the same four files in the same places, and git has
no way to merge an appended ledger row against another appended ledger row
without a human. The 2026-07-30 shelf collision (an add/add on the episode
*plus* four derived-file conflicts) is the shape of that class.

Decision (owner, 2026-07-31, variant (a) of #58) — the pattern already proven
in project-atlas ADR 0007: **an episode is the source, everything else is
output.** ``shelve`` writes only the episode; a bot regenerates the derived
files on ``main``; a PR guard refuses diffs that touch derived paths. Two
sessions then conflict only if they shelve episodes with the same slug — a
real collision, not a bookkeeping one.

For that to work, every column of every derived file has to live in the
episode. Hence the four frontmatter fields added alongside this module:
``date`` (the shelve date — ``span`` is the conversation's, not the write's),
``notes`` (the ledger's only free-text column), ``display_title`` and
``description`` (what ``.meta.json`` overrides). ``digest_tokens`` stays
computed, since the digest is right there in the file.

``rebuild`` is idempotent and total: it never merges with what is on disk, it
replaces. That is what makes ``--check`` meaningful as a guard — a
regeneration that diffed clean proves the committed artifacts match the
episodes, and a regeneration that diffs proves they do not.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from memshelf_mcp.core.episode import CATEGORY_BY_KIND
from memshelf_mcp.core.frontmatter import parse_frontmatter
from memshelf_mcp.core.shelve import LEDGER_HEADER

__all__ = [
    "adopt",
    "DERIVED_PATHS",
    "EpisodeRecord",
    "RebuildReport",
    "collect_episodes",
    "render_ledger",
    "render_meta",
    "rebuild",
    "derived_paths",
]

#: Repo-relative paths a shelf's bot owns. A PR touching any of them is
#: rewriting output instead of input — the guard exists to say so out loud.
DERIVED_PATHS = (
    "ledger.tsv",
    "INDEX.md",
    "stats.svg",
    "docs/topics/.meta.json",
    "docs/research/.meta.json",
    "docs/sessions/.meta.json",
)

CHARS_PER_TOKEN = 4


@dataclass
class EpisodeRecord:
    """One episode as the derived files see it."""

    id: str
    category: str
    filename: str
    date: str
    mode: str
    approx_tokens: int
    digest_tokens: int
    notes: str
    display_title: str
    description: str


@dataclass
class RebuildReport:
    written: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    drifted: list[str] = field(default_factory=list)
    episodes: int = 0
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """True when nothing had drifted — the verdict ``--check`` reports."""
        return not self.drifted

    def as_dict(self) -> dict:
        return {
            "episodes": self.episodes,
            "written": self.written,
            "unchanged": self.unchanged,
            "drifted": self.drifted,
            "warnings": self.warnings,
            "ok": self.ok,
        }


def _digest_of(body: str) -> str:
    """The text under ``## Digest`` up to the next H2 (or the end)."""
    lines = body.splitlines()
    try:
        start = next(i for i, line in enumerate(lines) if line.strip() == "## Digest")
    except StopIteration:
        return ""
    out: list[str] = []
    for line in lines[start + 1 :]:
        if line.startswith("## "):
            break
        out.append(line)
    return "\n".join(out).strip()


def _int(value: str, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def collect_episodes(root: Path) -> tuple[list[EpisodeRecord], list[str]]:
    """Read every episode on the shelf. Returns ``(records, warnings)``.

    Sorted by ``(date, id)`` so the derived files are a pure function of the
    episode set — two machines regenerating the same shelf produce identical
    bytes, which is what makes the CI guard's diff trustworthy.
    """
    records: list[EpisodeRecord] = []
    warnings: list[str] = []
    for category in sorted(set(CATEGORY_BY_KIND.values())):
        directory = root / "docs" / category
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.md")):
            text = path.read_text(encoding="utf-8")
            fields, body = parse_frontmatter(text)
            if not fields.get("id"):
                warnings.append(f"docs/{category}/{path.name}: нет frontmatter id — пропущен")
                continue
            digest = _digest_of(body)
            records.append(
                EpisodeRecord(
                    id=fields["id"],
                    category=category,
                    filename=path.name,
                    date=fields.get("date") or fields.get("span") or "",
                    mode=fields.get("mode", "live"),
                    approx_tokens=_int(fields.get("approx_tokens", "0")),
                    digest_tokens=len(digest) // CHARS_PER_TOKEN,
                    notes=fields.get("notes", ""),
                    display_title=fields.get("display_title", ""),
                    description=fields.get("description", ""),
                )
            )
    records.sort(key=lambda r: (r.date, r.id))
    return records, warnings


def render_ledger(records: list[EpisodeRecord]) -> str:
    rows = [
        "\t".join(
            [r.date, r.id, r.mode, str(r.approx_tokens), str(r.digest_tokens), r.notes]
        )
        for r in records
    ]
    return LEDGER_HEADER + "".join(row + "\n" for row in rows)


def render_meta(records: list[EpisodeRecord], category: str) -> str | None:
    """The category's ``.meta.json``, or None when no episode overrides anything.

    docshelf reads this file to show a free-form title next to a latin
    filename; an episode that wants neither a title nor a description has no
    business creating an entry.
    """
    data = {
        r.filename: {"title": r.display_title or r.id, "description": r.description}
        for r in records
        if r.category == category and (r.display_title or r.description)
    }
    if not data:
        return None
    import json

    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


def derived_paths(root: Path) -> list[str]:
    """Derived paths that currently exist on this shelf."""
    return [p for p in DERIVED_PATHS if (root / p).exists()]


def _apply(root: Path, rel: str, content: str | None, *, check: bool, report: RebuildReport) -> None:
    """Write ``content`` to ``rel`` (or delete it when None), or record drift."""
    path = root / rel
    current = path.read_text(encoding="utf-8") if path.is_file() else None
    if current == content:
        report.unchanged.append(rel)
        return
    if check:
        report.drifted.append(rel)
        return
    if content is None:
        path.unlink(missing_ok=True)
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    report.written.append(rel)


def rebuild(shelf_root: str | Path, *, check: bool = False) -> RebuildReport:
    """Regenerate (or, with ``check``, verify) every derived file on the shelf.

    ``check=True`` writes nothing and reports which files would change — the
    PR guard and the bot's own idempotency proof use the same code path, so
    the guard cannot pass on logic the bot does not run.
    """
    root = Path(shelf_root).expanduser().resolve()
    records, warnings = collect_episodes(root)
    report = RebuildReport(episodes=len(records), warnings=warnings)

    _apply(root, "ledger.tsv", render_ledger(records), check=check, report=report)
    for category in sorted(set(CATEGORY_BY_KIND.values())):
        if not (root / "docs" / category).is_dir():
            continue
        _apply(
            root,
            f"docs/{category}/.meta.json",
            render_meta(records, category),
            check=check,
            report=report,
        )

    # INDEX.md and stats.svg are rendered by their own writers rather than
    # compared as strings: docshelf owns the INDEX format, and the chart is
    # cosmetic. Both are still derived, so a check run must not invoke them.
    if not check:
        try:
            from docshelf_mcp.core.shelf import Shelf

            Shelf(root).rebuild_index()
            report.written.append("INDEX.md")
        except Exception as exc:  # noqa: BLE001 — a shelf without docshelf still rebuilds the rest
            report.warnings.append(f"INDEX.md not rebuilt: {exc}")
        try:
            from memshelf_mcp.core.chart import write_chart

            write_chart(root)
            report.written.append("stats.svg")
        except Exception as exc:  # noqa: BLE001 — cosmetic layer
            report.warnings.append(f"stats.svg not redrawn: {exc}")

    return report


# ----------------------------------------------------------------- adoption


def _insert_frontmatter_fields(text: str, additions: dict[str, str]) -> str | None:
    """Add missing ``key: value`` lines to an episode's frontmatter block.

    Returns the new text, or None when there is no block to extend (doctor
    reports those separately — adoption must not invent a frontmatter).
    Insertion is at the end of the block: order inside the block carries no
    meaning, and appending keeps the diff to exactly the added lines.
    """
    if not additions:
        return None
    lines = text.splitlines(keepends=True)
    fence = [i for i, line in enumerate(lines) if line.strip() == "---"]
    if len(fence) < 2:
        return None
    closing = fence[1]
    added = [f"{key}: {value}\n" for key, value in additions.items()]
    return "".join(lines[:closing] + added + lines[closing:])


def adopt(shelf_root: str | Path) -> dict:
    """Move the derived-only fields into the episodes, once (#58 migration).

    Shelves written before #58 keep the shelve date, ledger notes and display
    title *only* in ``ledger.tsv`` and ``.meta.json``. Regenerating from such
    episodes would silently drop all three, so a shelf has to adopt them first:
    read what the current derived files say, write it into the frontmatter,
    and from then on the episode is genuinely the source.

    Idempotent — an episode that already carries a field is left alone.
    """
    import json

    root = Path(shelf_root).expanduser().resolve()

    ledger: dict[str, dict[str, str]] = {}
    ledger_path = root / "ledger.tsv"
    if ledger_path.is_file():
        for i, line in enumerate(ledger_path.read_text(encoding="utf-8").splitlines()):
            if i == 0 or not line.strip():
                continue
            cells = line.split("\t")
            if len(cells) >= 2:
                # Later rows win: a re-shelved episode's last row is current.
                ledger[cells[1]] = {
                    "date": cells[0],
                    "digest_tokens": cells[4] if len(cells) > 4 else "",
                    "notes": cells[5] if len(cells) > 5 else "",
                }

    meta: dict[str, dict] = {}
    for category in sorted(set(CATEGORY_BY_KIND.values())):
        path = root / "docs" / category / ".meta.json"
        if not path.is_file():
            continue
        try:
            meta[category] = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            meta[category] = {}

    adopted: list[str] = []
    skipped: list[str] = []
    for category in sorted(set(CATEGORY_BY_KIND.values())):
        directory = root / "docs" / category
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.md")):
            text = path.read_text(encoding="utf-8")
            fields, _ = parse_frontmatter(text)
            episode_id = fields.get("id")
            if not episode_id:
                skipped.append(f"docs/{category}/{path.name}")
                continue
            entry = meta.get(category, {}).get(path.name, {})
            row = ledger.get(episode_id, {})
            additions: dict[str, str] = {}
            if "date" not in fields and row.get("date"):
                additions["date"] = row["date"]
            if "notes" not in fields and row.get("notes"):
                additions["notes"] = row["notes"]
            if "display_title" not in fields and entry.get("title"):
                additions["display_title"] = " ".join(str(entry["title"]).split())
            if "description" not in fields and entry.get("description"):
                additions["description"] = " ".join(str(entry["description"]).split())
            updated = _insert_frontmatter_fields(text, additions)
            if updated is None:
                continue
            path.write_text(updated, encoding="utf-8")
            adopted.append(f"docs/{category}/{path.name}")

    # digest_tokens is the one ledger column that stays computed rather than
    # stored: it is the episode's standing cost *today*, and the digest is
    # right there in the file. On a pre-#58 shelf the recorded value can
    # disagree with it — the working shelf had 30 of 60 rows off on
    # 2026-07-31, an M0/M1-transition residue. Regenerating restates them, so
    # adoption reports exactly which numbers move instead of quietly changing
    # accounting the shelf has published.
    records, _ = collect_episodes(root)
    restated = [
        {
            "id": r.id,
            "from": _int(ledger[r.id]["digest_tokens"], -1),
            "to": r.digest_tokens,
        }
        for r in records
        if r.id in ledger
        and _int(ledger[r.id].get("digest_tokens", ""), -1) not in (-1, r.digest_tokens)
    ]
    return {
        "adopted": adopted,
        "skipped": skipped,
        "count": len(adopted),
        "restated_digest_tokens": restated,
    }
