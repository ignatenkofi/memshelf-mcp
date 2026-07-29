"""Resolve the multi-writer conflict class: union append files, regenerate
derived ones, then doctor (issue #58, bridge option (в)).

Two sessions shelving on parallel branches collide in exactly four files —
``INDEX.md``, ``ledger.tsv``, ``docs/*/.meta.json``, ``stats.svg`` — none of
which carries hand-written content: the TSVs are append-only, the meta files
are per-episode key→value maps, and INDEX/stats are rendered from ``docs/``.
So the resolution is mechanical, and this module automates the manual
procedure of 2026-07-29 (sqst-memshelf#46/#47): union the appends, merge the
metas, rebuild the derived files, run doctor.

What it deliberately does NOT touch: conflicting *episodes* (two branches
editing the same ``docs/**/*.md``). That is a content conflict — it is
reported as unresolved, and the derived files are not rebuilt until it is
gone (a rebuild would bake conflict markers into INDEX).

The full fix — derived files committed only by a bot on main, the atlas
ADR 0007 pattern — is the M2 candidate (issue #58 option (а)); this command
stays useful there as the bot's building block.
"""

from __future__ import annotations

import fnmatch
import json
from dataclasses import dataclass, field
from pathlib import Path

from memshelf_mcp.core.doctor import check_shelf
from memshelf_mcp.core.recall import RECALL_LOG_HEADER
from memshelf_mcp.core.shelve import LEDGER_HEADER, _git, git_commit

APPEND_FILES = {
    "ledger.tsv": LEDGER_HEADER,
    "recall-log.tsv": RECALL_LOG_HEADER,
}
META_PATTERN = "docs/*/.meta.json"
DERIVED_FILES = ("INDEX.md", "stats.svg")

CONFLICT_OURS = "<<<<<<<"
CONFLICT_BASE = "|||||||"
CONFLICT_SEP = "======="
CONFLICT_THEIRS = ">>>>>>>"


@dataclass
class ResolveResult:
    resolved: list[str] = field(default_factory=list)  # conflicts united by rule
    regenerated: list[str] = field(default_factory=list)  # derived files rebuilt
    unresolved: list[str] = field(default_factory=list)  # left for a human/agent
    notes: list[str] = field(default_factory=list)
    in_merge: bool = False
    committed: bool = False
    commit: str | None = None
    doctor: dict | None = None

    @property
    def ok(self) -> bool:
        doctor_errors = (self.doctor or {}).get("errors", 0)
        return not self.unresolved and doctor_errors == 0

    def as_dict(self) -> dict:
        return {
            "status": "ok" if self.ok else "attention",
            "resolved": self.resolved,
            "regenerated": self.regenerated,
            "unresolved": self.unresolved,
            "notes": self.notes,
            "in_merge": self.in_merge,
            "committed": self.committed,
            "commit": self.commit,
            "doctor": self.doctor,
        }


def _is_git(root: Path) -> bool:
    return (root / ".git").exists()


def _unmerged_paths(root: Path) -> list[str]:
    """Paths with unmerged index stages, [] outside a git conflict."""
    if not _is_git(root):
        return []
    out = _git(root, "ls-files", "-u").stdout
    paths: list[str] = []
    for line in out.splitlines():
        # "<mode> <sha> <stage>\t<path>"
        _, _, path = line.partition("\t")
        if path and path not in paths:
            paths.append(path)
    return paths


def _stage_text(root: Path, rel: str, stage: int) -> str | None:
    """One side of an unmerged path (2=ours, 3=theirs); None if that side
    does not exist (add/delete conflicts)."""
    proc = _git(root, "show", f":{stage}:{rel}")
    return proc.stdout if proc.returncode == 0 else None


def _split_marker_sides(text: str) -> tuple[str, str]:
    """Reconstruct (ours, theirs) from conflict-marker text — the fallback
    when the working tree carries markers but git stages are gone. Handles
    diff3 style (the ``|||||||`` base block is dropped from both sides)."""
    ours: list[str] = []
    theirs: list[str] = []
    state = "common"
    for line in text.splitlines(keepends=True):
        if line.startswith(CONFLICT_OURS):
            state = "ours"
        elif line.startswith(CONFLICT_BASE) and state == "ours":
            state = "base"
        elif line.startswith(CONFLICT_SEP) and state in ("ours", "base"):
            state = "theirs"
        elif line.startswith(CONFLICT_THEIRS) and state == "theirs":
            state = "common"
        elif state == "common":
            ours.append(line)
            theirs.append(line)
        elif state == "ours":
            ours.append(line)
        elif state == "theirs":
            theirs.append(line)
        # state == "base": dropped
    return "".join(ours), "".join(theirs)


def _has_markers(text: str) -> bool:
    return any(line.startswith((CONFLICT_OURS, CONFLICT_THEIRS)) for line in text.splitlines())


def _union_tsv(ours: str | None, theirs: str | None, header: str) -> str:
    """Header + ours rows + theirs rows missing from ours.

    Append-only files never rewrite history, so a whole-row set union loses
    nothing; ours keeps its order (minimal diff), the other branch's new rows
    land at the tail. Duplicate identical rows collapse — two sessions cannot
    legitimately produce byte-identical ledger rows for different episodes.
    """
    header_line = header.strip("\n")
    rows: list[str] = []
    seen: set[str] = set()
    for side in (ours, theirs):
        for line in (side or "").splitlines():
            if not line.strip() or line == header_line:
                continue
            if line not in seen:
                seen.add(line)
                rows.append(line)
    return header + "".join(row + "\n" for row in rows)


def _union_meta(ours: str | None, theirs: str | None) -> str:
    """Merge two .meta.json maps; on a same-key collision ours wins (the
    branch running the resolve is the one being actively worked on)."""

    def _load(text: str | None) -> dict:
        if not text:
            return {}
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return {}
        return data if isinstance(data, dict) else {}

    merged = {**_load(theirs), **_load(ours)}
    return json.dumps(merged, indent=2, ensure_ascii=False) + "\n"


def _classify(rel: str) -> str:
    if rel in APPEND_FILES:
        return "append"
    if fnmatch.fnmatch(rel, META_PATTERN):
        return "meta"
    if rel in DERIVED_FILES:
        return "derived"
    return "other"


def resolve_shelf(shelf_root: str | Path, *, commit: bool = False) -> ResolveResult:
    """Resolve the shelf's mechanical merge conflicts and rebuild derived files.

    Safe to run outside a conflict too — it degrades to "rebuild INDEX/stats,
    run doctor". With ``commit=True`` the resolution is committed (inside a
    merge: ``git commit --no-edit`` completes the merge); the default leaves
    staged changes for the caller to review and commit.
    """
    root = Path(shelf_root).expanduser().resolve()
    result = ResolveResult()
    git = _is_git(root)
    result.in_merge = git and (root / ".git" / "MERGE_HEAD").exists()

    # 1) Unmerged paths from git stages — the reliable source.
    for rel in _unmerged_paths(root):
        kind = _classify(rel)
        if kind == "append":
            merged = _union_tsv(
                _stage_text(root, rel, 2), _stage_text(root, rel, 3), APPEND_FILES[rel]
            )
        elif kind == "meta":
            merged = _union_meta(_stage_text(root, rel, 2), _stage_text(root, rel, 3))
        elif kind == "derived":
            continue  # rebuilt below; the rebuild + git add clears the stage
        else:
            result.unresolved.append(rel)
            continue
        (root / rel).write_text(merged, encoding="utf-8")
        _git(root, "add", "--", rel)
        result.resolved.append(rel)

    # 2) Fallback: conflict markers left in the working tree (stages gone —
    # e.g. a half-finished manual resolution). Same classes, same unions.
    candidates = [Path(rel) for rel in APPEND_FILES] + sorted(
        p.relative_to(root) for p in root.glob(META_PATTERN)
    )
    for rel_path in candidates:
        rel = rel_path.as_posix()
        if rel in result.resolved:
            continue
        path = root / rel_path
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        if not _has_markers(text):
            continue
        ours, theirs = _split_marker_sides(text)
        if rel in APPEND_FILES:
            merged = _union_tsv(ours, theirs, APPEND_FILES[rel])
        else:
            merged = _union_meta(ours, theirs)
        path.write_text(merged, encoding="utf-8")
        if git:
            _git(root, "add", "--", rel)
        result.resolved.append(rel)

    # 3) Derived files: never merged, always rebuilt — but only on a shelf
    # whose episodes are conflict-free, or the markers get baked into INDEX.
    if result.unresolved:
        result.notes.append(
            "derived files not rebuilt: resolve the conflicting episodes above, "
            "then run resolve again"
        )
    else:
        from docshelf_mcp.core.shelf import Shelf  # heavy dep, imported lazily

        Shelf(root).rebuild_index()
        result.regenerated.append("INDEX.md")
        try:
            from memshelf_mcp.core.chart import write_chart

            if write_chart(root) is not None:
                result.regenerated.append("stats.svg")
        except Exception as exc:  # noqa: BLE001 — cosmetic layer, like shelve's
            result.notes.append(f"stats.svg not redrawn: {exc}")
        if git:
            for rel in DERIVED_FILES:
                if (root / rel).exists():
                    _git(root, "add", "--", rel)

    # 4) Doctor — the same gate the shelve rule mandates before a push.
    result.doctor = check_shelf(root).as_dict()

    # 5) Optional commit. Inside a merge this *completes the merge* with git's
    # own prepared message; outside, it commits the rebuilt derived files.
    if commit and git and not result.unresolved:
        if result.in_merge:
            proc = _git(root, "commit", "--no-edit")
            if proc.returncode == 0:
                result.committed = True
                result.commit = _git(root, "rev-parse", "HEAD").stdout.strip()
            else:
                result.notes.append(f"merge commit failed: {proc.stderr.strip()}")
        else:
            result.committed, result.commit = git_commit(
                root, "resolve: union appends + rebuild derived files"
            )

    return result
