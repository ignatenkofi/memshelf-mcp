import json
import subprocess

import pytest

pytest.importorskip("docshelf_mcp")

from docshelf_mcp.core.shelf import Shelf  # noqa: E402

from memshelf_mcp.core.episode import (  # noqa: E402
    MAX_DESCRIPTION_CHARS,
    EpisodeError,
)
from memshelf_mcp.core.rebuild import rebuild  # noqa: E402
from memshelf_mcp.core.shelve import (  # noqa: E402
    AmendTargetMissing,
    DigestContractError,
    SlugContractError,
    shelve,
)

GOOD_DIGEST = (
    "The auth refactor moved token checks into middleware; the decided approach "
    "is JWT with a shared secret. The cookie-session alternative was rejected "
    "for cross-service calls. Open: rotating the shared secret."
)


def _init_shelf(root, *, git=True):
    Shelf(root).init(name="test shelf", default_categories=["topics", "research", "sessions"])
    if git:
        subprocess.run(["git", "-C", str(root), "init", "-q"], check=True)
        subprocess.run(["git", "-C", str(root), "config", "user.email", "t@t.test"], check=True)
        subprocess.run(["git", "-C", str(root), "config", "user.name", "tester"], check=True)
    return root


def test_shelve_writes_the_episode_and_commits_only_it(tmp_path):
    """#58: the episode is the whole write. Derived files are the bot's job."""
    root = _init_shelf(tmp_path)
    result = shelve(
        root,
        slug="2026-07-22-auth-refactor",
        kind="topic",
        digest=GOOD_DIGEST,
        sections={"Decisions": "JWT chosen; cookie-session rejected."},
        approx_tokens=4000,
        date="2026-07-22",
    )
    episode = tmp_path / "docs" / "topics" / "2026-07-22-auth-refactor.md"
    assert episode.is_file()
    assert episode.read_text(encoding="utf-8").startswith("# 2026-07-22-auth-refactor")

    # Everything the ledger row needs now rides in the frontmatter.
    text = episode.read_text(encoding="utf-8")
    assert "date: 2026-07-22" in text
    assert "approx_tokens: 4000" in text

    assert result.committed and result.commit
    assert result.address == "docs/topics/2026-07-22-auth-refactor.md"
    committed = subprocess.run(
        ["git", "-C", str(root), "show", "--name-only", "--format=", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    assert committed == ["docs/topics/2026-07-22-auth-refactor.md"]


def test_rebuild_renders_the_ledger_row_shelve_no_longer_writes(tmp_path):
    root = _init_shelf(tmp_path)
    shelve(
        root,
        slug="2026-07-22-auth-refactor",
        kind="topic",
        digest=GOOD_DIGEST,
        sections={"Decisions": "JWT chosen; cookie-session rejected."},
        approx_tokens=4000,
        date="2026-07-22",
    )
    assert not (tmp_path / "ledger.tsv").exists()  # shelve wrote no derived file

    rebuild(root)
    ledger = (tmp_path / "ledger.tsv").read_text(encoding="utf-8").splitlines()
    assert ledger[0].startswith("date\t")
    assert ledger[-1].split("\t")[:3] == ["2026-07-22", "2026-07-22-auth-refactor", "live"]
    assert "2026-07-22-auth-refactor" in (tmp_path / "INDEX.md").read_text(encoding="utf-8")


def test_display_title_override_keeps_latin_filename(tmp_path):
    shelve(
        _init_shelf(tmp_path),
        slug="2026-07-22-founding",
        kind="research",
        digest="A research note on the founding; the local-first store was chosen. Open items remain.",
        sections={"Findings": "Local-first chosen."},
        display_title="Основание memshelf",
        date="2026-07-22",
    )
    # The file keeps the latin slug and the display title now travels in the
    # episode's frontmatter; .meta.json and INDEX are rendered from it.
    episode = tmp_path / "docs" / "research" / "2026-07-22-founding.md"
    assert episode.is_file()
    # Free-text fields are quoted so the block stays valid YAML for a real
    # loader (shelf-spec's validator), not just for memshelf's own reader.
    assert 'display_title: "Основание memshelf"' in episode.read_text(encoding="utf-8")

    rebuild(tmp_path)
    meta = json.loads((tmp_path / "docs" / "research" / ".meta.json").read_text(encoding="utf-8"))
    assert meta["2026-07-22-founding.md"]["title"] == "Основание memshelf"
    assert "Основание memshelf" in (tmp_path / "INDEX.md").read_text(encoding="utf-8")


def test_contract_violation_writes_nothing(tmp_path):
    root = _init_shelf(tmp_path)
    with pytest.raises(DigestContractError):
        shelve(
            root,
            slug="2026-07-22-bad",
            kind="topic",
            digest="We decided stuff.",  # first-person referent -> hard reject
            sections={"Decisions": "x"},
            date="2026-07-22",
        )
    assert not (tmp_path / "docs" / "topics" / "2026-07-22-bad.md").exists()
    assert not (tmp_path / "ledger.tsv").exists()


def test_redaction_scrubs_secret_from_stored_episode(tmp_path):
    result = shelve(
        _init_shelf(tmp_path),
        slug="2026-07-22-leak",
        kind="topic",
        digest="Rotated a leaked credential after the incident; the key was pulled. Open: audit access.",
        sections={"Decisions": "Pulled the key ghp_" + "c" * 36 + " and rotated it."},
        date="2026-07-22",
    )
    stored = (tmp_path / "docs" / "topics" / "2026-07-22-leak.md").read_text(encoding="utf-8")
    assert "ghp_" not in stored
    assert "«redacted:github-token»" in stored
    assert result.redaction.counts["github-token"] == 1


def test_policy_pattern_pack_redacts_domain_pii(tmp_path):
    # A per-shelf POLICY.patterns (#16) is consumed by the redaction pass: a
    # course shelf masking student ids gets them scrubbed from the stored file.
    root = _init_shelf(tmp_path)
    (root / "POLICY.patterns").write_text("student-id  S[0-9]{1,2}\n", encoding="utf-8")
    result = shelve(
        root,
        slug="2026-07-22-review",
        kind="topic",
        digest="The review batch chose to defer S7's rework; the rushed-fix path was rejected. Open: regrade.",
        sections={"Decisions": "Submission from S7 deferred to next batch."},
        date="2026-07-22",
    )
    stored = (tmp_path / "docs" / "topics" / "2026-07-22-review.md").read_text(encoding="utf-8")
    assert "S7" not in stored
    assert "«redacted:student-id»" in stored
    assert result.redaction.counts["student-id"] >= 1


def test_malformed_policy_pack_warns_but_still_shelves(tmp_path):
    root = _init_shelf(tmp_path)
    (root / "POLICY.patterns").write_text("broken  [unterminated\n", encoding="utf-8")
    result = shelve(
        root,
        slug="2026-07-22-ok",
        kind="topic",
        digest="The plan chose X; the Y alternative was rejected. Open: nothing.",
        sections={"Decisions": "X over Y"},
        date="2026-07-22",
    )
    assert (tmp_path / "docs" / "topics" / "2026-07-22-ok.md").is_file()
    assert any("POLICY.patterns" in w for w in result.warnings)


def test_plain_dir_skips_git_cleanly(tmp_path):
    result = shelve(
        _init_shelf(tmp_path, git=False),
        slug="2026-07-22-plain",
        kind="research",
        digest="A plain-mode note; git was skipped by design here. Open: nothing.",
        sections={"Findings": "no git"},
        date="2026-07-22",
    )
    assert result.committed is False
    assert result.commit is None
    assert (tmp_path / "docs" / "research" / "2026-07-22-plain.md").is_file()


def test_ledger_notes_with_tab_cannot_shift_columns(tmp_path):
    """shelf-spec v0 § 4.4: no tabs in `notes`. A tab used to land in the TSV
    verbatim, so a reader counting fields saw seven columns instead of six."""
    result = shelve(
        _init_shelf(tmp_path),
        slug="2026-07-22-tabbed-notes",
        kind="topic",
        digest=GOOD_DIGEST,
        sections={"Decisions": "JWT chosen; cookie-session rejected."},
        approx_tokens=4000,
        date="2026-07-22",
        notes="chat-1\tfragment",
    )
    rebuild(tmp_path)
    row = (tmp_path / "ledger.tsv").read_text(encoding="utf-8").splitlines()[-1]
    assert len(row.split("\t")) == 6
    assert row.split("\t")[5] == "chat-1 fragment"
    assert any("shelf-spec v0 § 4.4" in w for w in result.warnings)


def test_ledger_notes_with_newline_cannot_forge_a_row(tmp_path):
    """A newline in `notes` would otherwise append a second, bogus ledger row."""
    _init_shelf(tmp_path)
    shelve(
        tmp_path,
        slug="2026-07-22-newline-notes",
        kind="topic",
        digest=GOOD_DIGEST,
        sections={"Decisions": "JWT chosen; cookie-session rejected."},
        approx_tokens=4000,
        date="2026-07-22",
        notes="line one\nline two",
    )
    rebuild(tmp_path)
    lines = (tmp_path / "ledger.tsv").read_text(encoding="utf-8").splitlines()
    # header + exactly one row: the newline must not have forged a second one
    assert len(lines) == 2
    assert lines[-1].split("\t")[5] == "line one line two"


def test_clean_notes_are_untouched(tmp_path):
    result = shelve(
        _init_shelf(tmp_path),
        slug="2026-07-22-clean-notes",
        kind="topic",
        digest=GOOD_DIGEST,
        sections={"Decisions": "JWT chosen; cookie-session rejected."},
        approx_tokens=4000,
        date="2026-07-22",
        notes="chat-1 fragment",
    )
    rebuild(tmp_path)
    row = (tmp_path / "ledger.tsv").read_text(encoding="utf-8").splitlines()[-1]
    assert row.split("\t")[5] == "chat-1 fragment"
    assert not any("§ 4.4" in w for w in result.warnings)


# --- span defaults (SPEC 5.2 makes it REQUIRED; #56) -------------------------


def test_span_defaults_to_the_episode_date(tmp_path):
    # A shelve without --span must still produce a spec-valid episode: the
    # shelf's advisory CI (shelf_validate) rejects a missing span outright.
    shelve(
        _init_shelf(tmp_path),
        slug="2026-07-27-no-span",
        kind="topic",
        digest=GOOD_DIGEST,
        sections={"Decisions": "JWT chosen; cookie-session rejected."},
        date="2026-07-27",
    )
    text = (tmp_path / "docs" / "topics" / "2026-07-27-no-span.md").read_text(encoding="utf-8")
    assert "span: 2026-07-27" in text


def test_span_defaults_to_today_without_a_date(tmp_path):
    from datetime import date as _date

    shelve(
        _init_shelf(tmp_path),
        slug="2026-07-27-no-span-no-date",
        kind="topic",
        digest=GOOD_DIGEST,
        sections={"Decisions": "JWT chosen; cookie-session rejected."},
    )
    text = (tmp_path / "docs" / "topics" / "2026-07-27-no-span-no-date.md").read_text(
        encoding="utf-8"
    )
    assert f"span: {_date.today().isoformat()}" in text


def test_explicit_span_wins_over_the_default(tmp_path):
    shelve(
        _init_shelf(tmp_path),
        slug="2026-07-27-multi-day",
        kind="topic",
        digest=GOOD_DIGEST,
        sections={"Decisions": "JWT chosen; cookie-session rejected."},
        span="2026-07-24..2026-07-27",
        date="2026-07-27",
    )
    text = (tmp_path / "docs" / "topics" / "2026-07-27-multi-day.md").read_text(encoding="utf-8")
    assert "span: 2026-07-24..2026-07-27" in text


def test_shelve_leaves_only_the_episode_in_the_working_tree(tmp_path):
    """#69: the contract says shelve writes the episode and nothing else.

    `add_document` also records title/description in the category's
    `.meta.json` — a derived path. Left behind it puts the caller in front of
    two wrong options: commit it (and trip the shelf's own PR guard, with a
    latin slug where the display title belongs) or hand-revert a file they
    never asked for.
    """
    root = _init_shelf(tmp_path)
    subprocess.run(["git", "-C", str(root), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(root), "commit", "-qm", "init"], check=True)

    shelve(
        root,
        slug="2026-08-01-first",
        kind="topic",
        digest=GOOD_DIGEST,
        sections={"Decisions": "X over Y"},
        display_title="Человеческий заголовок",
        date="2026-08-01",
        autocommit=False,
    )

    status = subprocess.run(
        ["git", "-C", str(root), "status", "--porcelain", "-uall"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    assert status == ["??", "docs/topics/2026-08-01-first.md"], status


def test_shelve_preserves_an_existing_sidecar_byte_for_byte(tmp_path):
    """A shelf that already has a rendered sidecar keeps exactly it.

    Restoring, not deleting: shelves without the bot still rely on the file
    between rebuilds, so the second shelve must not cost them their titles.
    """
    root = _init_shelf(tmp_path)
    shelve(
        root,
        slug="2026-08-01-first",
        kind="topic",
        digest=GOOD_DIGEST,
        sections={"Decisions": "X over Y"},
        display_title="Первый",
        date="2026-08-01",
        autocommit=False,
    )
    rebuild(root)
    sidecar = tmp_path / "docs" / "topics" / ".meta.json"
    before = sidecar.read_text(encoding="utf-8")
    assert "Первый" in before

    shelve(
        root,
        slug="2026-08-02-second",
        kind="topic",
        digest=GOOD_DIGEST,
        sections={"Decisions": "X over Y"},
        display_title="Второй",
        date="2026-08-02",
        autocommit=False,
    )

    assert sidecar.read_text(encoding="utf-8") == before
    # …and one rebuild brings the new episode in, with its display title.
    rebuild(root)
    assert "Второй" in sidecar.read_text(encoding="utf-8")


# ── #71: amend ────────────────────────────────────────────────────────────
#
# The digest contract is checked *after* the episode is written, committed and
# accounted for — and until now the tool that reported the problem was also the
# reason it could not be fixed. These cover the fix, not the report.


def _amend_setup(tmp_path, digest=GOOD_DIGEST):
    root = _init_shelf(tmp_path)
    shelve(
        root,
        slug="2026-08-02-thin",
        kind="topic",
        digest=digest,
        sections={"Decisions": "first pass"},
        approx_tokens=1000,
        date="2026-08-02",
    )
    return root


def test_amend_rewrites_the_episode_in_place(tmp_path):
    root = _amend_setup(tmp_path)
    better = (
        "The nightly guard was rewritten: the decided approach asserts the "
        "advisory id, not the exit code. The rc-only check was rejected as "
        "unfalsifiable. Open: whether the fixture should cover a second ecosystem."
    )
    result = shelve(
        root,
        slug="2026-08-02-thin",
        kind="topic",
        digest=better,
        sections={"Decisions": "second pass"},
        approx_tokens=2000,
        date="2026-08-02",
        amend=True,
    )
    episode = (tmp_path / "docs" / "topics" / "2026-08-02-thin.md").read_text(encoding="utf-8")
    assert "second pass" in episode
    assert "first pass" not in episode
    assert "approx_tokens: 2000" in episode
    assert result.amended is True


def test_amend_leaves_exactly_one_episode_and_one_ledger_row(tmp_path):
    """The reason a new slug was the wrong workaround: it doubles the registry."""
    root = _amend_setup(tmp_path)
    shelve(
        root,
        slug="2026-08-02-thin",
        kind="topic",
        digest=GOOD_DIGEST.replace("auth refactor", "guard rewrite"),
        sections={"Decisions": "second pass"},
        approx_tokens=2000,
        date="2026-08-02",
        amend=True,
    )
    rebuild(root)
    episodes = list((tmp_path / "docs" / "topics").glob("*.md"))
    assert len(episodes) == 1, [p.name for p in episodes]

    rows = (tmp_path / "ledger.tsv").read_text(encoding="utf-8").strip().splitlines()[1:]
    ids = [r.split("\t")[1] for r in rows]
    assert ids == ["2026-08-02-thin"], ids
    # Recomputed, not appended: the row carries the amended accounting.
    assert rows[0].split("\t")[3] == "2000"


def test_amend_commits_under_its_own_message(tmp_path):
    root = _amend_setup(tmp_path)
    shelve(
        root,
        slug="2026-08-02-thin",
        kind="topic",
        digest=GOOD_DIGEST,
        sections={"Decisions": "second pass"},
        approx_tokens=2000,
        date="2026-08-02",
        amend=True,
    )
    subject = subprocess.run(
        ["git", "-C", str(root), "log", "-1", "--format=%s"],
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert subject == "shelve: 2026-08-02-thin (amend)"


def test_amend_reruns_redaction(tmp_path):
    """A hand-edit bypasses the redaction pass. An amend must not."""
    root = _amend_setup(tmp_path)
    result = shelve(
        root,
        slug="2026-08-02-thin",
        kind="topic",
        digest=GOOD_DIGEST,
        sections={"Decisions": "token was ghp_" + "d" * 36 + " before rotation"},
        approx_tokens=2000,
        date="2026-08-02",
        amend=True,
    )
    episode = (tmp_path / "docs" / "topics" / "2026-08-02-thin.md").read_text(encoding="utf-8")
    assert "ghp_" not in episode
    assert result.redaction.total >= 1


def test_amend_of_a_missing_episode_is_an_error(tmp_path):
    """Amending what is not there is a typo'd slug, not a create."""
    root = _init_shelf(tmp_path)
    with pytest.raises(AmendTargetMissing) as exc:
        shelve(
            root,
            slug="2026-08-02-never-written",
            kind="topic",
            digest=GOOD_DIGEST,
            date="2026-08-02",
            amend=True,
        )
    assert "2026-08-02-never-written" in str(exc.value)
    assert not (tmp_path / "docs" / "topics" / "2026-08-02-never-written.md").exists()


def test_shelve_without_amend_still_refuses_to_overwrite(tmp_path):
    """The guard stays; amend is opt-in, never the default."""
    root = _amend_setup(tmp_path)
    with pytest.raises(Exception) as exc:
        shelve(
            root,
            slug="2026-08-02-thin",
            kind="topic",
            digest=GOOD_DIGEST,
            sections={"Decisions": "collides"},
            date="2026-08-02",
        )
    assert "amend" in str(exc.value).lower()


def test_amend_still_enforces_the_digest_contract(tmp_path):
    """An amend that would install a rejected digest writes nothing."""
    root = _amend_setup(tmp_path)
    before = (tmp_path / "docs" / "topics" / "2026-08-02-thin.md").read_text(encoding="utf-8")
    with pytest.raises(DigestContractError):
        shelve(
            root,
            slug="2026-08-02-thin",
            kind="topic",
            digest="we decided to keep it",  # first-person plural — hard reject
            date="2026-08-02",
            amend=True,
        )
    after = (tmp_path / "docs" / "topics" / "2026-08-02-thin.md").read_text(encoding="utf-8")
    assert after == before


def test_address_names_the_file_that_was_actually_written(tmp_path):
    """A slug that is not already slug-shaped must not desync path and address.

    docshelf writes to ``slugify(slug, max_len=80)``; ``address`` used to be
    assembled from the raw slug. For «2026-08-03-Проверка Слага» the two part
    ways: the episode lands at ``2026-08-03-проверка-слага.md`` while the
    caller is handed a path that does not exist — and the auto-commit stages
    that non-path, so the episode silently stays uncommitted. In an ephemeral
    session that is the whole episode lost, with the tool having reported an
    address for it.
    """
    root = _init_shelf(tmp_path)
    result = shelve(
        root,
        slug="2026-08-03-Проверка Слага",
        kind="topic",
        digest=GOOD_DIGEST,
        sections={"Decisions": "тело"},
        approx_tokens=100,
    )

    assert (root / result.address).is_file(), (
        f"address {result.address!r} names a file that does not exist"
    )
    assert result.address == "docs/topics/2026-08-03-проверка-слага.md"

    # The commit is the part that failed silently: `git add <нет такого пути>`
    # leaves the episode untracked while shelve() returns without raising.
    # Смотрим именно на эпизод: INDEX.md здесь не отслеживается (шелф ещё без
    # базового коммита), и ассерт по пустому status ловил бы это, а не дефект.
    assert result.committed and result.commit
    committed = subprocess.run(
        ["git", "-C", str(root), "show", "--name-only", "--format=", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert "2026-08-03" in committed and result.address.split("/")[-1][:10] in committed
    dirty = subprocess.run(
        ["git", "-C", str(root), "status", "--porcelain", "--", result.address],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert dirty == "", f"эпизод не доехал в коммит: {dirty!r}"


def test_amend_finds_an_episode_stored_under_its_normalized_name(tmp_path):
    """The amend guard derives the path the way docshelf does, not from the raw slug."""
    root = _init_shelf(tmp_path)
    slug = "2026-08-03-Проверка Слага"
    shelve(
        root,
        slug=slug,
        kind="topic",
        digest=GOOD_DIGEST,
        sections={"Decisions": "первая редакция"},
        approx_tokens=100,
    )

    result = shelve(
        root,
        slug=slug,
        kind="topic",
        digest=GOOD_DIGEST,
        sections={"Decisions": "вторая редакция"},
        approx_tokens=100,
        amend=True,
    )

    assert result.amended
    episode = root / "docs" / "topics" / "2026-08-03-проверка-слага.md"
    assert "вторая редакция" in episode.read_text(encoding="utf-8")
    assert len(list((root / "docs" / "topics").glob("*.md"))) == 1


# ── #90: an amend that changes the kind changes the category ──────────────
#
# `kind` decides which sections doctor demands, so correcting a wrong kind is
# one of the few things amend is genuinely needed for — and it was the one
# thing amend refused, because it resolved the target from the *new* kind and
# looked only there. The manual path (shelve without --amend, then delete the
# old file by hand) is what these tests exist to make unnecessary: skipping its
# second half left one episode in two categories and two ledger rows.


def _session_episode(root, slug="2026-08-13-recount"):
    shelve(
        root,
        slug=slug,
        kind="session",
        digest=GOOD_DIGEST,
        sections={"Timeline": "10:00 started", "Open threads": "none"},
        approx_tokens=1000,
        date="2026-08-13",
    )
    return root / "docs" / "sessions" / f"{slug}.md"


def test_amend_moves_the_episode_when_the_kind_changes(tmp_path):
    root = _init_shelf(tmp_path)
    was = _session_episode(root)
    assert was.is_file()

    result = shelve(
        root,
        slug="2026-08-13-recount",
        kind="topic",
        digest=GOOD_DIGEST,
        sections={"Decisions": "kind corrected"},
        approx_tokens=1000,
        date="2026-08-13",
        amend=True,
    )

    now = root / "docs" / "topics" / "2026-08-13-recount.md"
    assert now.is_file(), "the episode did not land under the new kind"
    assert not was.exists(), "the old file survived — that is the duplicate this prevents"
    assert result.address == "docs/topics/2026-08-13-recount.md"
    assert result.moved_from == "docs/sessions/2026-08-13-recount.md"
    assert "kind: topic" in now.read_text(encoding="utf-8")


def test_the_move_leaves_one_episode_and_one_ledger_row(tmp_path):
    """The reason the move matters: a slug is the ledger key for the whole shelf."""
    root = _init_shelf(tmp_path)
    _session_episode(root)
    shelve(
        root,
        slug="2026-08-13-recount",
        kind="topic",
        digest=GOOD_DIGEST,
        sections={"Decisions": "kind corrected"},
        approx_tokens=1000,
        date="2026-08-13",
        amend=True,
    )

    rebuild(root)
    rows = (root / "ledger.tsv").read_text(encoding="utf-8").splitlines()[1:]
    slugs = [row.split("\t")[1] for row in rows]
    assert slugs.count("2026-08-13-recount") == 1, slugs
    episodes = list((root / "docs").glob("*/2026-08-13-recount.md"))
    assert len(episodes) == 1, episodes


def test_the_move_is_committed_as_a_move(tmp_path):
    """Both ends staged: otherwise history records the duplicate instead."""
    root = _init_shelf(tmp_path)
    _session_episode(root)
    shelve(
        root,
        slug="2026-08-13-recount",
        kind="topic",
        digest=GOOD_DIGEST,
        sections={"Decisions": "kind corrected"},
        approx_tokens=1000,
        date="2026-08-13",
        amend=True,
    )

    changed = subprocess.run(
        ["git", "-C", str(root), "show", "--name-status", "--format=", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    assert "docs/sessions/2026-08-13-recount.md" in changed, changed
    assert "docs/topics/2026-08-13-recount.md" in changed, changed


def test_amend_of_a_slug_absent_from_every_category_still_fails(tmp_path):
    """Widening the lookup must not turn a typo'd slug into a create."""
    root = _init_shelf(tmp_path)
    with pytest.raises(AmendTargetMissing) as exc:
        shelve(
            root,
            slug="2026-08-13-never-written",
            kind="topic",
            digest=GOOD_DIGEST,
            date="2026-08-13",
            amend=True,
        )
    # The message must name where it looked; the old one named one directory and
    # blamed the slug, which is exactly what made a kind change unreadable.
    assert "docs/sessions" in str(exc.value), exc.value


def test_shelving_the_same_slug_under_another_kind_without_amend_is_refused(tmp_path):
    """Without --amend this used to write a second copy and say nothing."""
    root = _init_shelf(tmp_path)
    _session_episode(root)
    with pytest.raises(Exception) as exc:
        shelve(
            root,
            slug="2026-08-13-recount",
            kind="topic",
            digest=GOOD_DIGEST,
            sections={"Decisions": "a second copy"},
            date="2026-08-13",
        )
    assert "amend" in str(exc.value).lower()
    assert not (root / "docs" / "topics" / "2026-08-13-recount.md").exists()


def test_a_refused_amend_does_not_move_the_episode(tmp_path):
    """The move must not outlive a refusal — found by reading this PR's own diff.

    Between deciding the move and writing the file there are two gates that can
    still reject the shelve: redaction/the digest contract, and the section
    contract inside `compose_episode`. A move performed at decision time
    survives both refusals: the caller is told the shelve failed, and the
    episode is meanwhile sitting in the new category carrying its old text —
    which is worse than either outcome the caller can reason about.
    """
    root = _init_shelf(tmp_path)
    was = _session_episode(root)
    before = was.read_text(encoding="utf-8")

    with pytest.raises(EpisodeError):
        shelve(
            root,
            slug="2026-08-13-recount",
            kind="topic",
            digest=GOOD_DIGEST,
            # kind=topic requires ## Decisions; `compose_episode` refuses — and it
            # runs *after* the move would have been decided.
            sections={"Findings": "no Decisions section"},
            date="2026-08-13",
            amend=True,
        )

    assert was.is_file(), "the episode moved despite the shelve being refused"
    assert was.read_text(encoding="utf-8") == before
    assert not (root / "docs" / "topics" / "2026-08-13-recount.md").exists()


def test_an_explicit_description_is_capped_like_a_generated_one(tmp_path):
    """The half-applied cap (#index-bloat diagnosis, 2026-08-21).

    `shelve` read `description if description is not None else
    _first_sentence(digest)`, and only `_first_sentence` truncated. Callers
    almost always pass a description, so the cap was effectively off: the
    author's shelf carried 15 descriptions past 200 chars, the longest 420, and
    descriptions alone were 43% of INDEX.
    """
    root = _init_shelf(tmp_path)
    long_description = (
        "Разобрали, почему проверка токена уехала в middleware, какие два "
        "альтернативных варианта отвергли и по каким именно замерам, что "
        "осталось открытым по ротации общего секрета, и кто это забирает."
    )
    assert len(long_description) > MAX_DESCRIPTION_CHARS

    result = shelve(
        root,
        slug="2026-07-22-auth-refactor",
        kind="topic",
        digest=GOOD_DIGEST,
        sections={"Decisions": "JWT chosen."},
        description=long_description,
        approx_tokens=4000,
        date="2026-07-22",
    )

    text = (tmp_path / "docs" / "topics" / "2026-07-22-auth-refactor.md").read_text(
        encoding="utf-8"
    )
    (line,) = [ln for ln in text.splitlines() if ln.startswith("description:")]
    written = line.split("description:", 1)[1].strip().strip("\"'")
    assert len(written) <= MAX_DESCRIPTION_CHARS
    assert written.endswith("…")
    # Cut at a word boundary, so it reads as truncated rather than as a typo.
    assert not written[:-1].endswith(" ")
    # And the author is told, rather than left believing it was taken as given.
    assert any("cut to" in w for w in result.warnings)


def test_a_short_description_is_left_exactly_alone(tmp_path):
    root = _init_shelf(tmp_path)
    shelve(
        root,
        slug="2026-07-22-auth-refactor",
        kind="topic",
        digest=GOOD_DIGEST,
        sections={"Decisions": "JWT chosen."},
        description="Токен-чек уехал в middleware; cookie-session отвергли.",
        approx_tokens=4000,
        date="2026-07-22",
    )

    text = (tmp_path / "docs" / "topics" / "2026-07-22-auth-refactor.md").read_text(
        encoding="utf-8"
    )
    assert "Токен-чек уехал в middleware; cookie-session отвергли." in text
    assert "…" not in text


def test_rebuild_caps_descriptions_already_on_disk(tmp_path):
    """Capping on write alone would leave every episode already shelved
    oversized until someone rewrote it. INDEX is derived, so the cap has to
    reach it from the render side too — one `rebuild`, no episodes touched."""
    root = _init_shelf(tmp_path)
    shelve(
        root,
        slug="2026-07-22-auth-refactor",
        kind="topic",
        digest=GOOD_DIGEST,
        sections={"Decisions": "JWT chosen."},
        description="short",
        approx_tokens=4000,
        date="2026-07-22",
    )
    episode = tmp_path / "docs" / "topics" / "2026-07-22-auth-refactor.md"
    oversized = "и".join(["очень длинное описание"] * 12)
    assert len(oversized) > MAX_DESCRIPTION_CHARS
    episode.write_text(
        episode.read_text(encoding="utf-8").replace(
            'description: "short"', f'description: "{oversized}"'
        ),
        encoding="utf-8",
    )

    rebuild(root)

    meta = json.loads((tmp_path / "docs" / "topics" / ".meta.json").read_text(encoding="utf-8"))
    rendered = meta["2026-07-22-auth-refactor.md"]["description"]
    assert len(rendered) <= MAX_DESCRIPTION_CHARS
    # The episode is the source and keeps what it carries; the cap governs the
    # derived line, which is the thing that is actually paid for.
    assert oversized in episode.read_text(encoding="utf-8")


def test_an_undated_slug_is_refused_before_anything_is_written(tmp_path):
    """#101: the declared contract (YYYY-MM-DD-slug) gets an enforcement point."""
    root = _init_shelf(tmp_path)
    with pytest.raises(SlugContractError) as err:
        shelve(
            root,
            slug="auth-refactor",
            kind="topic",
            digest=GOOD_DIGEST,
            sections={"Decisions": "JWT chosen."},
            date="2026-07-22",
        )
    # The message carries the format and the exact fixed form.
    assert "YYYY-MM-DD" in str(err.value)
    assert "2026-07-22-auth-refactor" in str(err.value)
    # Nothing was written, nothing was committed.
    assert list((tmp_path / "docs" / "topics").iterdir()) == []
    log = subprocess.run(
        ["git", "-C", str(root), "log", "--oneline"], capture_output=True, text=True
    )
    assert "shelve" not in log.stdout


def test_a_transposed_date_prefix_is_refused(tmp_path):
    """2026-31-08 sorts wrong — exactly what the contract exists to prevent."""
    root = _init_shelf(tmp_path)
    with pytest.raises(SlugContractError):
        shelve(
            root,
            slug="2026-31-08-auth-refactor",
            kind="topic",
            digest=GOOD_DIGEST,
            sections={"Decisions": "JWT chosen."},
            date="2026-08-31",
        )


def test_amend_of_a_legacy_undated_episode_still_works(tmp_path):
    """The contract gates new names, not access to episodes shelved before it."""
    root = _init_shelf(tmp_path)
    shelve(
        root,
        slug="2026-07-22-legacy",
        kind="topic",
        digest=GOOD_DIGEST,
        sections={"Decisions": "JWT chosen."},
        date="2026-07-22",
    )
    # What a pre-contract shelf actually holds: the same episode under an
    # undated file name.
    dated = tmp_path / "docs" / "topics" / "2026-07-22-legacy.md"
    legacy = tmp_path / "docs" / "topics" / "legacy.md"
    dated.rename(legacy)
    subprocess.run(["git", "-C", str(root), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(root), "commit", "-qm", "legacy name"], check=True)

    result = shelve(
        root,
        slug="legacy",
        kind="topic",
        digest=GOOD_DIGEST,
        sections={"Decisions": "JWT chosen; cookie-session rejected."},
        date="2026-07-22",
        amend=True,
    )
    assert result.address == "docs/topics/legacy.md"
    assert legacy.is_file()
    assert "cookie-session rejected" in legacy.read_text(encoding="utf-8")


def _archive_the_episode(root, slug, category="topics"):
    """Mimic what `rollup` does to one file: move it under archive/docs/."""
    src = root / "docs" / category / f"{slug}.md"
    dst = root / "archive" / "docs" / category / f"{slug}.md"
    dst.parent.mkdir(parents=True, exist_ok=True)
    src.rename(dst)
    subprocess.run(["git", "-C", str(root), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(root), "commit", "-qm", "rollup: move"], check=True)
    return dst


def test_amend_reaches_an_archived_episode(tmp_path):
    """#117: a slug behind a rollup is an episode, not a typo."""
    root = _init_shelf(tmp_path)
    shelve(
        root,
        slug="2026-07-06-hw-review-tail",
        kind="topic",
        digest=GOOD_DIGEST,
        sections={"Decisions": "JWT chosen."},
        date="2026-07-06",
    )
    archived = _archive_the_episode(root, "2026-07-06-hw-review-tail")

    result = shelve(
        root,
        slug="2026-07-06-hw-review-tail",
        kind="topic",
        digest=GOOD_DIGEST,
        sections={"Decisions": "JWT chosen; cookie-session rejected."},
        date="2026-07-06",
        amend=True,
    )
    # Rewritten in place, in the archive — no second file under docs/.
    assert result.address == "archive/docs/topics/2026-07-06-hw-review-tail.md"
    assert "cookie-session rejected" in archived.read_text(encoding="utf-8")
    assert not (root / "docs" / "topics" / "2026-07-06-hw-review-tail.md").exists()
    assert result.committed
    committed = subprocess.run(
        ["git", "-C", str(root), "show", "--name-only", "--format=", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    assert committed == ["archive/docs/topics/2026-07-06-hw-review-tail.md"]


def test_amended_archive_episode_keeps_one_ledger_row(tmp_path):
    """The archived row survives the amend: rendered from the same frontmatter."""
    from memshelf_mcp.core.rebuild import rebuild as run_rebuild

    root = _init_shelf(tmp_path)
    shelve(
        root,
        slug="2026-07-06-hw-review-tail",
        kind="topic",
        digest=GOOD_DIGEST,
        sections={"Decisions": "JWT chosen."},
        approx_tokens=4000,
        date="2026-07-06",
    )
    _archive_the_episode(root, "2026-07-06-hw-review-tail")
    shelve(
        root,
        slug="2026-07-06-hw-review-tail",
        kind="topic",
        digest=GOOD_DIGEST,
        sections={"Decisions": "JWT chosen; cookie-session rejected."},
        approx_tokens=4000,
        date="2026-07-06",
        amend=True,
    )
    run_rebuild(root)
    rows = [
        line
        for line in (root / "ledger.tsv").read_text(encoding="utf-8").splitlines()
        if "2026-07-06-hw-review-tail" in line
    ]
    assert len(rows) == 1
    assert "\t4000\t" in rows[0]
    # And the render is a fixed point: nothing drifts after the amend.
    report = run_rebuild(root, check=True)
    assert report.drifted == []


def test_shelving_an_archived_slug_without_amend_is_refused(tmp_path):
    """A plain shelve over an archived slug would put one slug in two places."""
    from memshelf_mcp.core.shelve import EpisodeExists

    root = _init_shelf(tmp_path)
    shelve(
        root,
        slug="2026-07-06-hw-review-tail",
        kind="topic",
        digest=GOOD_DIGEST,
        sections={"Decisions": "JWT chosen."},
        date="2026-07-06",
    )
    _archive_the_episode(root, "2026-07-06-hw-review-tail")
    with pytest.raises(EpisodeExists) as err:
        shelve(
            root,
            slug="2026-07-06-hw-review-tail",
            kind="topic",
            digest=GOOD_DIGEST,
            sections={"Decisions": "JWT chosen."},
            date="2026-07-06",
        )
    assert "archive/docs/topics/2026-07-06-hw-review-tail.md" in str(err.value)
    assert not (root / "docs" / "topics" / "2026-07-06-hw-review-tail.md").exists()


def test_kind_change_of_an_archived_episode_is_refused_with_the_reason(tmp_path):
    """No silent move across the archive boundary — refuse and say why."""
    root = _init_shelf(tmp_path)
    shelve(
        root,
        slug="2026-07-06-hw-review-tail",
        kind="topic",
        digest=GOOD_DIGEST,
        sections={"Decisions": "JWT chosen."},
        date="2026-07-06",
    )
    archived = _archive_the_episode(root, "2026-07-06-hw-review-tail")
    before = archived.read_text(encoding="utf-8")
    with pytest.raises(EpisodeError) as err:
        shelve(
            root,
            slug="2026-07-06-hw-review-tail",
            kind="session",
            digest=GOOD_DIGEST,
            sections={"Timeline": "t", "Open threads": "o"},
            date="2026-07-06",
            amend=True,
        )
    assert "archive" in str(err.value)
    assert archived.read_text(encoding="utf-8") == before  # untouched on refusal


def test_no_approx_tokens_is_recorded_as_unmeasured_not_zero(tmp_path):
    """#113: absence of measurement must not look like a measured zero."""
    root = _init_shelf(tmp_path)
    shelve(
        root,
        slug="2026-09-01-no-number",
        kind="topic",
        digest=GOOD_DIGEST,
        sections={"Decisions": "JWT chosen."},
        date="2026-09-01",
    )
    text = (tmp_path / "docs" / "topics" / "2026-09-01-no-number.md").read_text(encoding="utf-8")
    assert "approx_tokens: 0" in text
    assert "approx_tokens_source: unmeasured" in text


def test_a_passed_number_defaults_to_an_estimate(tmp_path):
    """#79: a caller's number is a judgment call unless they claim otherwise."""
    root = _init_shelf(tmp_path)
    shelve(
        root,
        slug="2026-09-01-eyeballed",
        kind="topic",
        digest=GOOD_DIGEST,
        sections={"Decisions": "JWT chosen."},
        approx_tokens=120000,
        date="2026-09-01",
    )
    text = (tmp_path / "docs" / "topics" / "2026-09-01-eyeballed.md").read_text(encoding="utf-8")
    assert "approx_tokens: 120000" in text
    assert "approx_tokens_source: estimate" in text


def test_measured_is_an_explicit_claim(tmp_path):
    root = _init_shelf(tmp_path)
    shelve(
        root,
        slug="2026-09-01-measured",
        kind="topic",
        digest=GOOD_DIGEST,
        sections={"Decisions": "JWT chosen."},
        approx_tokens=54321,
        approx_tokens_source="measured",
        date="2026-09-01",
    )
    text = (tmp_path / "docs" / "topics" / "2026-09-01-measured.md").read_text(encoding="utf-8")
    assert "approx_tokens_source: measured" in text


def test_a_source_without_a_number_is_a_contradiction(tmp_path):
    root = _init_shelf(tmp_path)
    with pytest.raises(ValueError, match="contradiction"):
        shelve(
            root,
            slug="2026-09-01-contradiction",
            kind="topic",
            digest=GOOD_DIGEST,
            sections={"Decisions": "JWT chosen."},
            approx_tokens_source="measured",
            date="2026-09-01",
        )
    assert list((tmp_path / "docs" / "topics").iterdir()) == []


def test_an_unknown_source_value_is_refused(tmp_path):
    root = _init_shelf(tmp_path)
    with pytest.raises(ValueError, match="must be one of"):
        shelve(
            root,
            slug="2026-09-01-badsource",
            kind="topic",
            digest=GOOD_DIGEST,
            sections={"Decisions": "JWT chosen."},
            approx_tokens=10,
            approx_tokens_source="vibes",
            date="2026-09-01",
        )
