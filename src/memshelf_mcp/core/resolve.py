"""Resolve the multi-writer conflict class: regenerate derived files, union the
one genuinely append-only log, then doctor (issue #58, bridge option (в)).

Two sessions shelving on parallel branches collide in exactly four files —
``INDEX.md``, ``ledger.tsv``, ``docs/*/.meta.json``, ``stats.svg`` — none of
which carries hand-written content. So the resolution is mechanical, and this
module automates the manual procedure of 2026-07-29 (sqst-memshelf#46/#47).

**Since #58 those four files are derived**, i.e. a pure function of ``docs/``
⊕ ``archive/docs/``. Merging two versions of a derived file does not produce
the sum of two truths — it produces garbage, because a derived file has no
history, only a current correct value. The live collision of 2026-08-01
(memshelf-mcp#64) proved the point: a union revived 16 ``.meta`` entries whose
episodes had moved into ``archive/`` and doubled 30 ledger rows whose
``digest_tokens`` had been restated on one side — and ``resolve`` reported
``status: ok`` on the result. The only correct resolution for a derived path
is therefore **regenerate, never merge**: :func:`~memshelf_mcp.core.rebuild.rebuild`
plus :func:`~memshelf_mcp.core.archive.rebuild_archive_index` (the archive
sub-shelf has its own INDEX, which ``rebuild`` does not touch).

``recall-log.tsv`` is the one file here that really is append-only — nothing
regenerates it, because a recall is an event, not a fact about the episodes.
It keeps a union, and that union is a **three-way multiset** merge: two
sessions recalling the same section of the same episode produce byte-identical
rows, and collapsing them would silently undercount the savings the log exists
to measure.

What it deliberately does NOT touch: conflicting *episodes* (two branches
editing the same ``docs/**/*.md``). That is a content conflict — it is
reported as unresolved, and the derived files are not rebuilt until it is
gone (a rebuild would bake conflict markers into INDEX).

The full fix — derived files committed only by a bot on main, the atlas
ADR 0007 pattern — shipped as ``adapters/shelf-repo`` (issue #58 option (а));
this command stays the bridge for shelves that have not adopted it, and the
bot's own building block.
"""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass, field
from pathlib import Path

from memshelf_mcp.core.doctor import check_shelf
from memshelf_mcp.core.rebuild import DERIVED_PATHS
from memshelf_mcp.core.recall import RECALL_LOG_HEADER
from memshelf_mcp.core.shelve import _git, git_commit

#: Files nothing regenerates — a union is the only way to keep both sides.
APPEND_FILES = {
    "recall-log.tsv": RECALL_LOG_HEADER,
}
META_PATTERN = "docs/*/.meta.json"

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


def _rows(text: str | None, header_line: str) -> list[str]:
    return [line for line in (text or "").splitlines() if line.strip() and line != header_line]


def _union_tsv(ours: str | None, theirs: str | None, header: str, base: str | None = None) -> str:
    """Three-way multiset union of an append-only log.

    An append-only log only ever grows, so each side is ``base`` plus its own
    tail and the merge is ``base + ours_tail + theirs_tail``. Counting rather
    than de-duplicating matters: ``recall-log.tsv`` rows are
    ``episode_id/section/tokens`` with no timestamp, so two sessions recalling
    the same section legitimately write byte-identical rows — a set union would
    drop one and undercount the realized savings the log exists to measure.

    Without ``base`` (the marker fallback, where git's stage 1 is gone) the
    multiset is approximated by ``max`` of the two counts: still never fewer
    rows than either side had, which a set union could not promise.
    """
    header_line = header.strip("\n")
    base_rows = _rows(base, header_line)
    ours_rows = _rows(ours, header_line)
    theirs_rows = _rows(theirs, header_line)

    def _counts(rows: list[str]) -> dict[str, int]:
        out: dict[str, int] = {}
        for row in rows:
            out[row] = out.get(row, 0) + 1
        return out

    base_counts = _counts(base_rows)
    ours_counts = _counts(ours_rows)
    theirs_counts = _counts(theirs_rows)

    merged: list[str] = []
    emitted: dict[str, int] = {}

    def _target(row: str) -> int:
        if base is None:
            return max(ours_counts.get(row, 0), theirs_counts.get(row, 0))
        # base + (ours - base) + (theirs - base), floored at each side's count.
        return max(
            ours_counts.get(row, 0) + theirs_counts.get(row, 0) - base_counts.get(row, 0),
            ours_counts.get(row, 0),
            theirs_counts.get(row, 0),
        )

    # Ours keeps its order (minimal diff); the other branch's surplus lands at
    # the tail.
    for row in ours_rows + theirs_rows:
        seen = emitted.get(row, 0)
        if seen < _target(row):
            emitted[row] = seen + 1
            merged.append(row)
    return header + "".join(row + "\n" for row in merged)


def _classify(rel: str) -> str:
    if rel in APPEND_FILES:
        return "append"
    if rel in DERIVED_PATHS or fnmatch.fnmatch(rel, META_PATTERN):
        # Derived from the episodes — regenerated below, never merged (#64).
        return "derived"
    return "other"


def resolve_shelf(shelf_root: str | Path, *, commit: bool = False) -> ResolveResult:
    """Resolve the shelf's mechanical merge conflicts and rebuild derived files.

    Safe to run outside a conflict too — it degrades to "regenerate every
    derived file, run doctor". With ``commit=True`` the resolution is committed (inside a
    merge: ``git commit --no-edit`` completes the merge); the default leaves
    staged changes for the caller to review and commit.
    """
    root = Path(shelf_root).expanduser().resolve()
    result = ResolveResult()
    git = _is_git(root)
    result.in_merge = git and (root / ".git" / "MERGE_HEAD").exists()

    # 1) Unmerged paths from git stages — the reliable source.
    conflicted_derived: list[str] = []
    for rel in _unmerged_paths(root):
        kind = _classify(rel)
        if kind == "append":
            merged = _union_tsv(
                _stage_text(root, rel, 2),
                _stage_text(root, rel, 3),
                APPEND_FILES[rel],
                _stage_text(root, rel, 1),
            )
        elif kind == "derived":
            conflicted_derived.append(rel)
            continue  # regenerated below; the rebuild + git add clears the stage
        else:
            result.unresolved.append(rel)
            continue
        (root / rel).write_text(merged, encoding="utf-8")
        _git(root, "add", "--", rel)
        result.resolved.append(rel)

    # 2) Fallback: conflict markers left in the working tree (stages gone —
    # e.g. a half-finished manual resolution). Append files still union; a
    # derived file carrying markers just gets overwritten by the rebuild, so it
    # only has to be *noticed* here, not merged.
    for rel_path in [Path(rel) for rel in APPEND_FILES]:
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
        path.write_text(_union_tsv(ours, theirs, APPEND_FILES[rel]), encoding="utf-8")
        if git:
            _git(root, "add", "--", rel)
        result.resolved.append(rel)

    # 3) Derived files: never merged, always regenerated — but only on a shelf
    # whose episodes are conflict-free, or the markers get baked into INDEX.
    if result.unresolved:
        result.notes.append(
            "derived files not regenerated: resolve the conflicting episodes above, "
            "then run resolve again"
        )
    else:
        from memshelf_mcp.core.archive import rebuild_archive_index
        from memshelf_mcp.core.rebuild import rebuild

        report = rebuild(root)
        result.regenerated.extend(report.written)
        result.regenerated.extend(
            rel for rel in conflicted_derived if rel not in result.regenerated
        )
        result.notes.extend(report.warnings)
        # The archive sub-shelf keeps its own INDEX, which `rebuild` does not
        # touch — forgetting it leaves a rolled-up shelf's archive index right
        # only because a human last committed it (#64).
        rebuild_archive_index(root)
        if (root / "archive" / "INDEX.md").is_file():
            result.regenerated.append("archive/INDEX.md")
        if git:
            for rel in dict.fromkeys(list(DERIVED_PATHS) + conflicted_derived):
                # -A so a .meta.json the rebuild legitimately deleted is staged
                # as a deletion instead of failing the add.
                _git(root, "add", "-A", "--", rel)
            _git(root, "add", "-A", "--", "archive")

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
                root, "resolve: regenerate derived files + union the recall log"
            )

    return result
