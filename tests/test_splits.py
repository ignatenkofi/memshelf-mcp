"""A shelf must render the same everywhere (#109).

The derived layer is a function of the *committed* episodes — that is what lets
a bot own ``INDEX.md`` on ``main`` while sessions push episodes from anywhere.
An H2 split directory broke it: docshelf wrote one beside any episode past
50 KiB, ``shelve`` committed the episode alone, and from then on the machine
that shelved rendered an INDEX no other checkout could produce. ``doctor``
called that ``stale-index`` — correctly, and (on docshelf < 0.4.1) permanently.

docshelf 0.4.1 cured the divergence upstream: enumeration skips split
directories, so the INDEX rendered next to one is the INDEX every other
checkout renders, and the directory itself is reported by docshelf's own
doctor (``uncommitted-split-dir`` / ``split-out-of-sync``). That is why
pyproject floors the dependency at 0.4.1 — below it these tests would be
asserting a world the installed library no longer produces.

These tests hold the invariant end to end (shelve → bot render → doctor) and
cover the migration for shelves that already carry such a directory: the
directory is still reported (our ``local-split-dir``) and still removable.
"""

import shutil
import subprocess

import pytest

pytest.importorskip("docshelf_mcp")

from memshelf_mcp.core.doctor import check_shelf  # noqa: E402
from memshelf_mcp.core.init import init_shelf  # noqa: E402
from memshelf_mcp.core.rebuild import DERIVED_PATHS, rebuild  # noqa: E402
from memshelf_mcp.core.recall import search  # noqa: E402
from memshelf_mcp.core.shelve import shelve  # noqa: E402
from memshelf_mcp.core.splits import episode_split_dirs, prune_split_dirs  # noqa: E402

DIGEST = (
    "The auth refactor moved token checks into middleware; the decided approach "
    "is JWT with a shared secret. The cookie-session alternative was rejected "
    "for cross-service calls. Open: rotating the shared secret."
)

#: Past docshelf's 50 KiB split threshold — with the Digest heading that is the
#: two H2s `should_split` also requires, so this episode is exactly the case.
BIG_BODY = "\n".join(f"Line {i}: the decision and why it was taken." for i in range(1400))


def _git(root, *args):
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True)


def _init(root):
    """A real shelf, as `memshelf init` makes one: git, one initial commit.

    Note what it does *not* carry — a `.gitignore` rule for `docs/*/*/`. The
    shelf this was diagnosed on had one, and it was never the cause: `shelve`
    stages the episode alone, so the sections stay out of the repository with
    or without it.
    """
    init_shelf(root, name="t", storage="git-local")
    _git(root, "config", "user.email", "t@t.test")
    _git(root, "config", "user.name", "t")
    assert "docs/*/*/" not in (root / ".gitignore").read_text(encoding="utf-8")
    return root


def _shelve_big(root, slug="2026-08-23-big-episode"):
    shelve(
        root,
        slug=slug,
        kind="topic",
        digest=DIGEST,
        sections={"Decisions": BIG_BODY},
        approx_tokens=1000,
        date=slug[:10],
        sync=False,
    )
    return root / "docs" / "topics" / f"{slug}.md"


def _render_like_the_bot(root, tmp_path):
    """Regenerate the derived files the way the bot does — from a fresh clone.

    The clone is the whole point of the test: it carries what was committed and
    nothing else, which is precisely the input the renderer on ``main`` gets.
    """
    checkout = tmp_path / "bot-checkout"
    subprocess.run(["git", "clone", "-q", str(root), str(checkout)], check=True)
    rebuild(checkout)
    copied = []
    for rel in DERIVED_PATHS:
        src = checkout / rel
        if src.is_file():
            (root / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, root / rel)
            copied.append(rel)
    _git(root, "add", "--", *copied)
    _git(root, "commit", "-qm", "chore: regenerate derived files")
    return checkout


def test_shelving_a_big_episode_leaves_no_uncommitted_split(tmp_path):
    """The zero case: shelve → bot render → doctor, and doctor is clean.

    Before the fix this failed on `stale-index`, and no rebuild could clear it:
    the section files exist here and in no other checkout, so the INDEX rendered
    from the working tree and the INDEX rendered from the repository can never
    be the same text.
    """
    root = _init(tmp_path / "shelf")
    episode = _shelve_big(root)
    assert episode.is_file()
    assert episode.stat().st_size > 50 * 1024  # the split threshold, actually crossed

    _render_like_the_bot(root, tmp_path)

    # The reported symptom first: this is the assertion that fails on the code
    # before the fix, and it fails right after the bot has just rendered.
    rules = {f.code for f in check_shelf(root).findings}
    assert "stale-index" not in rules
    assert "local-split-dir" not in rules
    # ...and its cause: nothing beside the episode, nothing left uncommitted.
    assert episode_split_dirs(root) == []
    assert _git(root, "status", "--porcelain").stdout.strip() == ""

    # Same defect, seen from the other end: every address `search` hands out is
    # a path git tracks, so it means the same thing in any other checkout.
    hits = search(root, "decision", max_results=10)
    assert hits
    for hit in hits:
        assert _git(root, "ls-files", "--error-unmatch", "--", hit.address).returncode == 0


def test_a_legacy_split_directory_is_reported_and_removable(tmp_path):
    """The migration: a directory an older version left behind, and its cure."""
    root = _init(tmp_path / "shelf")
    episode = _shelve_big(root)
    _render_like_the_bot(root, tmp_path)

    # Recreate exactly what the old code left: sections beside the episode,
    # written but never staged.
    legacy = episode.with_suffix("")
    legacy.mkdir()
    (legacy / "001-preamble.md").write_text("stale copy\n", encoding="utf-8")
    (legacy / "002-decisions.md").write_text(BIG_BODY, encoding="utf-8")

    findings = {f.code: f for f in check_shelf(root).findings}
    assert "local-split-dir" in findings
    assert findings["local-split-dir"].path == "docs/topics/2026-08-23-big-episode"
    # docshelf >= 0.4.1 skips split directories when enumerating documents, so
    # the INDEX no longer diverges between checkouts — the old `stale-index`
    # symptom must NOT fire. The directory is still loudly reported: by us
    # above, and by docshelf itself (`uncommitted-split-dir`).
    assert "stale-index" not in findings

    dry = prune_split_dirs(root)
    assert dry.local == ["docs/topics/2026-08-23-big-episode"]
    assert dry.deleted == []
    assert legacy.is_dir()  # a dry run writes nothing

    # `search` answers from the split while it is there — an address no other
    # machine can resolve. Not a second bug: the same files, the same cause.
    before = [h.address for h in search(root, "decision", max_results=10)]
    assert any(a.startswith("docs/topics/2026-08-23-big-episode/") for a in before)

    done = prune_split_dirs(root, apply=True)
    assert done.deleted == ["docs/topics/2026-08-23-big-episode"]
    assert not legacy.exists()
    assert episode.is_file()  # the episode is the source; it is never touched

    rules = {f.code for f in check_shelf(root).findings}
    assert "local-split-dir" not in rules
    assert "stale-index" not in rules

    after = [h.address for h in search(root, "decision", max_results=10)]
    assert "docs/topics/2026-08-23-big-episode.md" in after
    assert not any("/2026-08-23-big-episode/" in a for a in after)


def test_a_committed_split_directory_is_left_alone(tmp_path):
    """A shelf that commits its sections is coherent — report, never delete."""
    root = _init(tmp_path / "shelf")
    episode = _shelve_big(root)
    tracked = episode.with_suffix("")
    tracked.mkdir()
    (tracked / "001-preamble.md").write_text("committed section\n", encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "sections on purpose")

    report = prune_split_dirs(root, apply=True)
    assert report.tracked == ["docs/topics/2026-08-23-big-episode"]
    assert report.local == [] and report.deleted == []
    assert tracked.is_dir()
    assert "local-split-dir" not in {f.code for f in check_shelf(root).findings}


def test_a_plain_shelf_is_never_pruned(tmp_path):
    """Without a repository nothing diverges: there is only one renderer."""
    root = tmp_path / "shelf"
    init_shelf(root, name="t", storage="plain")
    shelve(
        root,
        slug="2026-08-23-plain",
        kind="topic",
        digest=DIGEST,
        sections={"Decisions": BIG_BODY},
        date="2026-08-23",
        autocommit=False,
        sync=False,
    )
    legacy = root / "docs" / "topics" / "2026-08-23-plain"
    legacy.mkdir()
    (legacy / "001-preamble.md").write_text("local only\n", encoding="utf-8")

    report = prune_split_dirs(root, apply=True)
    assert report.local == [] and report.deleted == []
    assert legacy.is_dir()
