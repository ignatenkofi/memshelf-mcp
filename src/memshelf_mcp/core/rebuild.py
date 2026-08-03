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

import re
from dataclasses import dataclass, field
from pathlib import Path

from memshelf_mcp.core.episode import CATEGORY_BY_KIND, flatten, yaml_scalar
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
    "shelve_date",
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

#: shelf-spec v0 § 4.4 constrains the ledger's first column to a calendar date.
ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


def shelve_date(fields: dict[str, str], episode_id: str) -> tuple[str, str | None]:
    """The ledger's ``date`` column for one episode: ``(date, warning)``.

    Deliberately **not** ``span``. ``span`` is when the *conversation*
    happened — ``A..B`` by design, and on real shelves sometimes carrying a
    parenthetical — while § 4.4 constrains this column to ``YYYY-MM-DD``.
    Before #58 the difference was invisible (``shelve`` appended the row and
    filled the column from its own ``--date``); once the ledger became derived,
    a ``date or span`` fallback started printing intervals into a field the
    spec forbids them in, and only the external validator noticed (#65, #66).

    The fallback is the slug's date prefix: the shelving convention puts the
    date there, and unlike ``span`` it cannot be a range. Failing that the
    column is left empty rather than guessed — ``doctor`` reports an empty
    date as ``ledger-malformed``, which is the loud outcome the silent one
    was missing.
    """
    date = (fields.get("date") or "").strip()
    if ISO_DATE.fullmatch(date):
        return date, None
    prefix = episode_id[:10]
    if ISO_DATE.fullmatch(prefix):
        return prefix, (
            f"нет frontmatter `date` — дата взята из префикса слага ({prefix}); "
            "допишите `date:` в эпизод или прогоните `memshelf rebuild --adopt`"
        )
    return "", (
        "нет frontmatter `date`, и слаг не начинается с даты — колонка ledger "
        "останется пустой, doctor сообщит ledger-malformed"
    )


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
    #: Rolled up into ``archive/`` (#15). Still in the ledger — an archived
    #: episode holds the mass it saved; only its INDEX line is gone.
    archived: bool = False


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
    # ``archive/`` is the rollup sub-shelf (#15). Its episodes are out of the
    # INDEX but not out of the accounting: skipping them here would make a
    # rollup look like the mass had never been shelved.
    roots = [(root / "docs", False)]
    archive_docs = root / "archive" / "docs"
    if archive_docs.is_dir():
        roots.append((archive_docs, True))
    for base, archived in roots:
        for category in sorted(set(CATEGORY_BY_KIND.values())):
            directory = base / category
            if not directory.is_dir():
                continue
            prefix = f"archive/docs/{category}" if archived else f"docs/{category}"
            for path in sorted(directory.glob("*.md")):
                text = path.read_text(encoding="utf-8")
                fields, body = parse_frontmatter(text)
                if not fields.get("id"):
                    warnings.append(f"{prefix}/{path.name}: нет frontmatter id — пропущен")
                    continue
                digest = _digest_of(body)
                date, date_warning = shelve_date(fields, fields["id"])
                if date_warning:
                    warnings.append(f"{prefix}/{path.name}: {date_warning}")
                records.append(
                    EpisodeRecord(
                        id=fields["id"],
                        category=category,
                        filename=path.name,
                        date=date,
                        mode=fields.get("mode", "live"),
                        approx_tokens=_int(fields.get("approx_tokens", "0")),
                        digest_tokens=len(digest) // CHARS_PER_TOKEN,
                        notes=fields.get("notes", ""),
                        display_title=fields.get("display_title", ""),
                        description=fields.get("description", ""),
                        archived=archived,
                    )
                )
    records.sort(key=lambda r: (r.date, r.id))
    return records, warnings


def render_ledger(records: list[EpisodeRecord]) -> str:
    rows = [
        "\t".join([r.date, r.id, r.mode, str(r.approx_tokens), str(r.digest_tokens), r.notes])
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
        if r.category == category
        and not r.archived  # archived episodes are the sub-shelf's business
        and (r.display_title or r.description)
    }
    if not data:
        return None
    import json

    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


def derived_paths(root: Path) -> list[str]:
    """Derived paths that currently exist on this shelf."""
    return [p for p in DERIVED_PATHS if (root / p).exists()]


def _apply(
    root: Path, rel: str, content: str | None, *, check: bool, report: RebuildReport
) -> None:
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
    if not root.is_dir():
        # Without this, a typo in --shelf does not fail: the writers create
        # their parents, so `rebuild` happily makes a fresh tree with an empty
        # ledger and INDEX, answers ok=True, and leaves the real shelf
        # untouched. The operator reads "ok" and believes the derived files
        # were regenerated. Existence only, deliberately — shelves in the wild
        # differ (some carry .docshelf.json, some only shelf.yml), and a
        # marker check would reject working shelves to catch a typo.
        raise FileNotFoundError(f"not a shelf directory: {root}")
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

            # write_chart returns None when there is nothing to draw (no
            # ledger rows yet). Appending unconditionally made the report
            # claim a file that is not on disk — a small lie, but in the one
            # field a caller uses to check what happened.
            if write_chart(root) is not None:
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
            if "date" not in fields:
                # The old ledger first, then the slug prefix. Both are checked
                # against the spec's format: an episode that arrived past the
                # migration (parallel branch, hand-added, imported) is exactly
                # the case that left one `span` interval in the date column of
                # a live shelf (#65), so adoption must not copy a bad value
                # forward — and must not leave the field unset either.
                candidate = next(
                    (
                        value
                        for value in (row.get("date", ""), episode_id[:10])
                        if ISO_DATE.fullmatch(value or "")
                    ),
                    "",
                )
                if candidate:
                    additions["date"] = candidate
            # Free text goes in quoted, exactly as `shelve` writes it: a title
            # with a colon is valid for memshelf's reader and a syntax error
            # for a real YAML loader, and adoption must not plant that.
            if "notes" not in fields and row.get("notes"):
                additions["notes"] = yaml_scalar(flatten(row["notes"]))
            if "display_title" not in fields and entry.get("title"):
                additions["display_title"] = yaml_scalar(flatten(str(entry["title"])))
            if "description" not in fields and entry.get("description"):
                additions["description"] = yaml_scalar(flatten(str(entry["description"])))
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
