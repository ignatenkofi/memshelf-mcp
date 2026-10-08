import json
import re
import subprocess
from pathlib import Path

import pytest

pytest.importorskip("docshelf_mcp")

from docshelf_mcp.core.shelf import Shelf  # noqa: E402

from memshelf_mcp.core.episode import (  # noqa: E402
    APPROX_TOKENS_SOURCES,
    MAX_DESCRIPTION_CHARS,
    EpisodeError,
)
from memshelf_mcp.core.frontmatter import parse_frontmatter  # noqa: E402
from memshelf_mcp.core.rebuild import rebuild  # noqa: E402
from memshelf_mcp.core.shelve import (  # noqa: E402
    AmendTargetMissing,
    DigestContractError,
    EpisodePathBlocked,
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


def test_span_defaults_to_the_slug_date_without_a_date(tmp_path):
    """#170: a new episode's date (and span, which defaults to it) comes from
    the slug's own ``YYYY-MM-DD-`` prefix, not the wall clock — a session that
    is shelved after midnight must not get tomorrow's date just because the
    machine's clock already turned over. This replaces the previous version of
    this test, which pinned the pre-#170 defect (``span`` == ``date.today()``)
    and would have failed the instant it ran on a day other than the slug's.
    """
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
    assert "date: 2026-07-27" in text
    assert "span: 2026-07-27" in text


def test_new_episode_date_comes_from_the_slug_not_the_clock(tmp_path, monkeypatch):
    """#170: a session that is shelved after midnight must not get tomorrow's
    date just because the machine's clock already turned over — the slug's
    own ``YYYY-MM-DD-`` prefix is the date for a new episode, not
    ``date.today()``. The fake clock below is deliberately a day ahead of the
    slug so the assertion cannot pass by the test happening to run on the
    right day.
    """
    from datetime import date as _date

    import memshelf_mcp.core.shelve as shelve_module

    class _AlreadyTomorrow(_date):
        @classmethod
        def today(cls):
            return _date(2026, 7, 28)

    monkeypatch.setattr(shelve_module, "_date", _AlreadyTomorrow)

    shelve(
        _init_shelf(tmp_path),
        slug="2026-07-27-late-night-session",
        kind="topic",
        digest=GOOD_DIGEST,
        sections={"Decisions": "JWT chosen; cookie-session rejected."},
    )
    text = (tmp_path / "docs" / "topics" / "2026-07-27-late-night-session.md").read_text(
        encoding="utf-8"
    )
    assert "date: 2026-07-27" in text
    assert "span: 2026-07-27" in text
    assert "2026-07-28" not in text


def test_explicit_date_wins_over_amend_inheritance(tmp_path):
    """#170 gates inheritance on 'no explicit --date' — passing --date on an
    amend must still override whatever the shelf already has, exactly like a
    fresh shelve."""
    root = _init_shelf(tmp_path)
    shelve(
        root,
        slug="2026-09-26-night-shift",
        kind="topic",
        digest=GOOD_DIGEST,
        sections={"Decisions": "JWT chosen."},
        date="2026-09-26",
    )
    shelve(
        root,
        slug="2026-09-26-night-shift",
        kind="topic",
        digest=GOOD_DIGEST,
        sections={"Decisions": "JWT chosen; cookie-session rejected."},
        date="2026-09-27",
        amend=True,
    )
    text = (tmp_path / "docs" / "topics" / "2026-09-26-night-shift.md").read_text(encoding="utf-8")
    assert "date: 2026-09-27" in text
    assert "span: 2026-09-27" in text


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


def test_amend_help_schema_and_hint_promise_no_ledger_write(tmp_path, capsys):
    """#192: since #58 `--amend` writes and commits the episode alone — the
    ledger is rendered later. The CLI help, the tool schema and the same-slug
    hint still said "one recomputed ledger row", a write that never happens."""
    from memshelf_mcp.cli import main
    from memshelf_mcp.core.shelve import EpisodeExists
    from memshelf_mcp.tools import ShelveInput

    with pytest.raises(SystemExit):
        main(["shelve", "--help"])
    help_text = " ".join(capsys.readouterr().out.split())
    amend_help = help_text[help_text.rindex("--amend") :]
    schema = ShelveInput.model_json_schema()["properties"]["amend"]["description"]

    root = _amend_setup(tmp_path)
    with pytest.raises(EpisodeExists) as err:
        shelve(
            root,
            slug="2026-08-02-thin",
            kind="topic",
            digest=GOOD_DIGEST,
            sections={"Decisions": "a second write, no --amend"},
            date="2026-08-02",
        )
    hint = " ".join(str(err.value).split())

    for text in (amend_help, schema, hint):
        assert "ledger row" not in text.lower(), text
        assert "only the episode file" in text, text
        assert "rebuild" in text, text


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


def test_amend_without_date_keeps_the_existing_date(tmp_path, monkeypatch):
    """#170: `--amend` without an explicit `--date` must not silently move the
    episode's date (and, downstream, its ledger row) to today — this is the
    exact bug the issue reports: an episode shelved on 2026-09-26 that got
    amended the next morning came back dated 2026-09-27.
    """
    from datetime import date as _date

    import memshelf_mcp.core.shelve as shelve_module

    root = _init_shelf(tmp_path)
    shelve(
        root,
        slug="2026-09-26-night-shift",
        kind="topic",
        digest=GOOD_DIGEST,
        sections={"Decisions": "JWT chosen."},
        date="2026-09-26",
    )

    class _NextMorning(_date):
        @classmethod
        def today(cls):
            return _date(2026, 9, 27)

    monkeypatch.setattr(shelve_module, "_date", _NextMorning)

    shelve(
        root,
        slug="2026-09-26-night-shift",
        kind="topic",
        digest=GOOD_DIGEST,
        sections={"Decisions": "JWT chosen; cookie-session rejected."},
        amend=True,
    )
    text = (tmp_path / "docs" / "topics" / "2026-09-26-night-shift.md").read_text(encoding="utf-8")
    assert "date: 2026-09-26" in text
    assert "span: 2026-09-26" in text
    assert "2026-09-27" not in text


def test_amend_without_date_keeps_the_existing_multi_day_span(tmp_path):
    """#170: span inherits from the existing episode too, not just date — an
    amend that supplies neither must not collapse an imported multi-day span
    down to a single day just because it re-touches the digest."""
    root = _init_shelf(tmp_path)
    shelve(
        root,
        slug="2026-07-24-multi-day-import",
        kind="topic",
        digest=GOOD_DIGEST,
        sections={"Decisions": "JWT chosen."},
        date="2026-07-27",
        span="2026-07-24..2026-07-27",
        mode="import",
    )
    shelve(
        root,
        slug="2026-07-24-multi-day-import",
        kind="topic",
        digest=GOOD_DIGEST,
        sections={"Decisions": "JWT chosen; cookie-session rejected."},
        mode="import",
        amend=True,
    )
    text = (tmp_path / "docs" / "topics" / "2026-07-24-multi-day-import.md").read_text(
        encoding="utf-8"
    )
    assert "date: 2026-07-27" in text
    assert "span: 2026-07-24..2026-07-27" in text


def test_amend_of_an_archived_episode_without_date_keeps_its_date(tmp_path, monkeypatch):
    """#170 names archive/ explicitly: an episode a rollup already moved out of
    docs/ must keep its date on amend the same way a live one does (the #117
    amend-in-archive path)."""
    from datetime import date as _date

    import memshelf_mcp.core.shelve as shelve_module

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

    class _MuchLater(_date):
        @classmethod
        def today(cls):
            return _date(2026, 9, 27)

    monkeypatch.setattr(shelve_module, "_date", _MuchLater)

    shelve(
        root,
        slug="2026-07-06-hw-review-tail",
        kind="topic",
        digest=GOOD_DIGEST,
        sections={"Decisions": "JWT chosen; cookie-session rejected."},
        amend=True,
    )
    text = archived.read_text(encoding="utf-8")
    assert "date: 2026-07-06" in text
    assert "span: 2026-07-06" in text


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


# ── #186 part 3: a directory in the episode's way is a refusal ────────────
#
# docshelf-mcp#115 (merged after 0.5.0) makes `add_document` refuse to write
# beside a directory named like the document that is not a split it wrote:
# `SplitDirConflictError`, a FileExistsError, raised with split=False too and
# whatever `overwrite` says. `shelve` caught only DocumentExistsError, so the
# refusal escaped as a traceback. CI installs docshelf 0.5.0, which has
# neither the guard nor the name, so the tests below stand the guard in with
# a FileExistsError subclass. The last one drives the real guard and runs only
# where the installed docshelf has it.


class _SplitDirConflict(FileExistsError):
    """Stands in for docshelf's SplitDirConflictError, which 0.5.0 does not have."""


def _guard_split_dirs_like_docshelf_main(monkeypatch):
    """Give `Shelf.add_document` the #115 pre-flight: a directory named like the
    document refuses the write before anything is written."""
    original = Shelf.add_document

    def add_document(self, source, *, category, title, **kwargs):
        if (self.root / "docs" / category / title).is_dir():
            raise _SplitDirConflict(
                f"docs/{category}/{title} exists and is not a docshelf split directory"
            )
        return original(self, source, category=category, title=title, **kwargs)

    monkeypatch.setattr(Shelf, "add_document", add_document)


def _commit_count(root):
    return subprocess.run(
        ["git", "-C", str(root), "rev-list", "--all", "--count"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def test_a_split_dir_in_the_way_of_a_new_episode_is_a_refusal(tmp_path, monkeypatch):
    root = _init_shelf(tmp_path)
    topics = root / "docs" / "topics"
    foreign = topics / "2026-10-07-probe"
    foreign.mkdir()
    (foreign / "diagram.png").write_bytes(b"not a section")
    before = sorted(p.name for p in topics.iterdir())
    _guard_split_dirs_like_docshelf_main(monkeypatch)

    with pytest.raises(EpisodePathBlocked) as err:
        shelve(
            root,
            slug="2026-10-07-probe",
            kind="topic",
            digest=GOOD_DIGEST,
            sections={"Decisions": "JWT chosen."},
            date="2026-10-07",
        )

    message = " ".join(str(err.value).split())
    assert "docs/topics/2026-10-07-probe/ is a directory" in message, message
    assert "Move it aside" in message, message
    assert "--amend (CLI) / amend=True does not clear this" in message, message
    assert isinstance(err.value.__cause__, _SplitDirConflict)
    # Nothing was written: no episode, no sidecar, no commit, the directory intact.
    assert sorted(p.name for p in topics.iterdir()) == before
    assert sorted(p.name for p in foreign.iterdir()) == ["diagram.png"]
    assert _commit_count(root) == "0"


def test_a_split_dir_refusal_of_a_kind_change_moves_the_episode_back(tmp_path, monkeypatch):
    """A kind change moves the file before the write (#90), and the guard
    refuses at the write; the refusal must not strand the episode in the new
    category with its old text."""
    root = _init_shelf(tmp_path)
    was = _session_episode(root)
    before = was.read_text(encoding="utf-8")
    (root / "docs" / "topics" / "2026-08-13-recount").mkdir()
    _guard_split_dirs_like_docshelf_main(monkeypatch)

    with pytest.raises(EpisodePathBlocked):
        shelve(
            root,
            slug="2026-08-13-recount",
            kind="topic",
            digest=GOOD_DIGEST,
            sections={"Decisions": "kind corrected"},
            date="2026-08-13",
            amend=True,
        )

    assert was.is_file(), "the refused kind change left the episode moved"
    assert was.read_text(encoding="utf-8") == before
    assert not (root / "docs" / "topics" / "2026-08-13-recount.md").exists()


@pytest.mark.parametrize(
    "writes_first", [False, True], ids=["fails-before-writing", "fails-after-writing"]
)
def test_any_failed_write_of_a_kind_change_moves_the_episode_back(
    tmp_path, monkeypatch, writes_first
):
    """Not only docshelf's refusal: whatever stops the write after the move —
    here a permission error, before or after docshelf wrote the new text —
    leaves the old episode where it was, byte for byte."""
    root = _init_shelf(tmp_path)
    was = _session_episode(root)
    before = was.read_bytes()
    status_before = _porcelain(root)

    def add_document(self, source, *, category, title, **kwargs):
        if writes_first:
            target = self.root / "docs" / category / f"{title}.md"
            target.write_text(Path(source).read_text(encoding="utf-8"), encoding="utf-8")
        raise PermissionError(f"docs/{category} is not writable")

    monkeypatch.setattr(Shelf, "add_document", add_document)

    with pytest.raises(PermissionError):
        shelve(
            root,
            slug="2026-08-13-recount",
            kind="topic",
            digest=GOOD_DIGEST,
            sections={"Decisions": "kind corrected"},
            date="2026-08-13",
            amend=True,
        )

    assert was.is_file(), "the failed kind change left the episode moved"
    assert was.read_bytes() == before
    assert not (root / "docs" / "topics" / "2026-08-13-recount.md").exists()
    assert _porcelain(root) == status_before


def _porcelain(root):
    return subprocess.run(
        ["git", "-C", str(root), "status", "--porcelain", "--untracked-files=all"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout


def test_cli_split_dir_refusal_exits_1_with_the_fix_not_a_traceback(tmp_path, monkeypatch, capsys):
    from memshelf_mcp.cli import main

    root = _init_shelf(tmp_path)
    (root / "docs" / "topics" / "2026-10-07-probe").mkdir()
    _guard_split_dirs_like_docshelf_main(monkeypatch)

    code = main(
        [
            "shelve",
            "--shelf",
            str(root),
            "--slug",
            "2026-10-07-probe",
            "--kind",
            "topic",
            "--digest",
            GOOD_DIGEST,
            "--section",
            "Decisions=JWT chosen.",
            "--date",
            "2026-10-07",
        ]
    )

    assert code == 1
    err = " ".join(capsys.readouterr().err.split())
    assert "docs/topics/2026-10-07-probe/ is a directory" in err, err
    assert "Move it aside" in err, err


def test_the_real_split_dir_guard_ends_in_the_same_refusal(tmp_path):
    """The guard itself, not a stand-in. Skipped until the installed docshelf
    has it (0.5.0 does not); it runs by itself once the floor moves past it."""
    splitter = pytest.importorskip("docshelf_mcp.core.splitter")
    if not hasattr(splitter, "SplitDirConflictError"):
        pytest.skip("the installed docshelf has no SplitDirConflictError (0.5.0 and older)")
    root = _init_shelf(tmp_path)
    foreign = root / "docs" / "topics" / "2026-10-07-probe"
    foreign.mkdir()
    (foreign / "diagram.png").write_bytes(b"not a section")

    with pytest.raises(EpisodePathBlocked) as err:
        shelve(
            root,
            slug="2026-10-07-probe",
            kind="topic",
            digest=GOOD_DIGEST,
            sections={"Decisions": "JWT chosen."},
            date="2026-10-07",
        )

    assert isinstance(err.value.__cause__, splitter.SplitDirConflictError)
    assert not (root / "docs" / "topics" / "2026-10-07-probe.md").exists()
    assert sorted(p.name for p in foreign.iterdir()) == ["diagram.png"]


# ── #205: an amend keeps what is passed back unchanged ────────────────────
#
# `--amend` applied create-time rules to content that already existed: a
# description past the cap came back cut to 119 characters, and the sections
# moved to the canonical order, so a caller that passed every field and
# section back as stored got a diff it had not made. The issue's repro passes
# `--date`, and the amend read the stored episode only without one (#170), so
# the repro runs both ways.

#: The issue's description, as `$(printf 'Long description %.0s' $(seq 20))`
#: builds it: 340 characters, the trailing space included.
LONG_DESCRIPTION = "Long description " * 20
PROBE_SLUG = "2026-10-08-amend-probe"
PROBE_DIGEST = (
    "The amend probe checks what memshelf shelve --amend rewrites. "
    "Decided: probe only. Open: nothing."
)
PROBE_SECTIONS = {"Decisions": "- d", "Timeline": "- t", "Findings": "- f", "Open threads": "- o"}


def _older_episode(root, description=LONG_DESCRIPTION, sections=PROBE_SECTIONS):
    """The issue's repro up to the amend: an episode the way a hand-edit or an
    older memshelf leaves it (the description stored whole, Findings before
    Open threads), committed."""
    shelve(
        root,
        slug=PROBE_SLUG,
        kind="session",
        digest=PROBE_DIGEST,
        sections=sections,
        description=description,
        approx_tokens=100,
    )
    episode = root / "docs" / "sessions" / f"{PROBE_SLUG}.md"
    text = episode.read_text(encoding="utf-8")
    text = re.sub(
        r"^description: .*$",
        lambda _: "description: " + json.dumps(description, ensure_ascii=False),
        text,
        count=1,
        flags=re.M,
    )
    # The last `## Open threads`: a Decisions body may quote one in a fence.
    op, fi = text.rindex("## Open threads\n"), text.index("## Findings\n")
    text = text[:op] + text[fi:].rstrip("\n") + "\n\n" + text[op:fi].rstrip("\n") + "\n"
    episode.write_text(text, encoding="utf-8")
    subprocess.run(["git", "-C", str(root), "commit", "-qam", "older episode"], check=True)
    return episode


def _headings(text):
    return re.findall(r"^## (.+)$", text, re.M)


@pytest.mark.parametrize(
    "date_args", [["--date", "2026-10-08"], []], ids=["with-date", "without-date"]
)
def test_amend_passing_everything_back_rewrites_nothing(tmp_path, capsys, date_args):
    """The issue's repro through the CLI. Before the fix the description came
    back cut to 119 characters and Findings moved below Open threads."""
    from memshelf_mcp.cli import main

    root = _init_shelf(tmp_path)
    episode = _older_episode(root)
    before = episode.read_text(encoding="utf-8")
    assert _headings(before) == ["Digest", "Decisions", "Timeline", "Findings", "Open threads"]

    sections = [a for name, body in PROBE_SECTIONS.items() for a in ("--section", f"{name}={body}")]
    code = main(
        [
            "shelve",
            "--shelf",
            str(root),
            "--slug",
            PROBE_SLUG,
            "--kind",
            "session",
            "--digest",
            PROBE_DIGEST,
            "--amend",
            "--no-commit",
            *sections,
            "--description",
            LONG_DESCRIPTION,
            "--approx-tokens",
            "100",
            *date_args,
        ]
    )

    out = capsys.readouterr()
    assert code == 0, out.err
    assert episode.read_text(encoding="utf-8") == before
    # Kept, and the caller is told what the INDEX line will show instead.
    warnings = json.loads(out.out)["warnings"]
    assert any("kept as stored" in w and "cut to 119" in w for w in warnings), warnings


def test_amend_keeps_a_stored_description_the_cap_would_balance(tmp_path):
    """Since #190 the cap changes values within it too: it closes a single
    backtick left open. Keeping only what is past the cap would still rewrite
    this one, so the value passed back unchanged bypasses the whole cap."""
    root = _init_shelf(tmp_path)
    stored = "Re-ran `memshelf doctor on the probe shelf"
    assert len(stored) <= MAX_DESCRIPTION_CHARS
    episode = _older_episode(root, description=stored)
    before = episode.read_text(encoding="utf-8")
    assert parse_frontmatter(before)[0]["description"] == stored

    result = shelve(
        root,
        slug=PROBE_SLUG,
        kind="session",
        digest=PROBE_DIGEST,
        sections=PROBE_SECTIONS,
        description=stored,
        approx_tokens=100,
        date="2026-10-08",
        amend=True,
        autocommit=False,
    )

    assert episode.read_text(encoding="utf-8") == before
    assert any("kept as stored" in w and "unpaired" in w for w in result.warnings)


@pytest.mark.parametrize(
    "stored,passed",
    [
        (LONG_DESCRIPTION, LONG_DESCRIPTION),
        (LONG_DESCRIPTION, LONG_DESCRIPTION.rstrip()),
        (LONG_DESCRIPTION, " ".join(LONG_DESCRIPTION.split())),
        ("Kept as stored,  spaces and all", "Kept as stored, spaces and all"),
    ],
    ids=["exact", "trimmed", "collapsed", "no-break"],
)
def test_amend_keeps_a_description_passed_back_whitespace_aside(tmp_path, stored, passed):
    """A wrapper that trims or collapses what it read passes back the same
    description; the issue's own ends in a space. It is kept as stored: not
    cut to 119 characters, and not rewritten to the value passed either."""
    root = _init_shelf(tmp_path)
    episode = _older_episode(root, description=stored)
    before = episode.read_text(encoding="utf-8")
    assert parse_frontmatter(before)[0]["description"] == stored

    shelve(
        root,
        slug=PROBE_SLUG,
        kind="session",
        digest=PROBE_DIGEST,
        sections=PROBE_SECTIONS,
        description=passed,
        approx_tokens=100,
        date="2026-10-08",
        amend=True,
        autocommit=False,
    )

    assert episode.read_text(encoding="utf-8") == before


def test_amend_caps_a_description_that_changed(tmp_path):
    """Only the value passed back unchanged is kept; a new one is capped as on
    any shelve."""
    root = _init_shelf(tmp_path)
    episode = _older_episode(root)

    result = shelve(
        root,
        slug=PROBE_SLUG,
        kind="session",
        digest=PROBE_DIGEST,
        sections=PROBE_SECTIONS,
        description=LONG_DESCRIPTION.replace("Long", "Longer"),
        approx_tokens=100,
        date="2026-10-08",
        amend=True,
        autocommit=False,
    )

    written = parse_frontmatter(episode.read_text(encoding="utf-8"))[0]["description"]
    assert len(written) <= MAX_DESCRIPTION_CHARS
    assert written.startswith("Longer description") and written.endswith("…")
    assert any("cut to" in w for w in result.warnings)
    assert not any("kept as stored" in w for w in result.warnings)


def test_amend_keeps_the_stored_section_order_and_slots_a_new_one_in(tmp_path):
    """The stored order wins over the canonical one and over the order passed.
    A section the episode does not have yet goes before the first section the
    canonical order puts after it: Artifacts after Timeline, before Findings."""
    root = _init_shelf(tmp_path)
    episode = _older_episode(root)

    shelve(
        root,
        slug=PROBE_SLUG,
        kind="session",
        digest=PROBE_DIGEST,
        sections={
            "Open threads": "- o",
            "Decisions": "- d",
            "Artifacts": "- a",
            "Timeline": "- t, amended",
            "Findings": "- f",
        },
        description=LONG_DESCRIPTION,
        approx_tokens=100,
        date="2026-10-08",
        amend=True,
        autocommit=False,
    )

    text = episode.read_text(encoding="utf-8")
    assert _headings(text) == [
        "Digest",
        "Decisions",
        "Timeline",
        "Artifacts",
        "Findings",
        "Open threads",
    ]
    assert "- t, amended" in text


@pytest.mark.parametrize("date", ["2026-10-08", None], ids=["with-date", "without-date"])
def test_amend_reads_a_stored_episode_that_is_not_utf8(tmp_path, date):
    """The amend reads the episode on every run now, and a byte that is not
    UTF-8 reads as U+FFFD instead of ending it in a UnicodeDecodeError: with
    `--date`, the amend before #205 wrote over such a file without reading
    it. The rest is kept as for any other episode."""
    root = _init_shelf(tmp_path)
    episode = _older_episode(root)
    raw = episode.read_bytes().replace(b"- f\n", b"- f\xff\n", 1)
    episode.write_bytes(raw)
    subprocess.run(["git", "-C", str(root), "commit", "-qam", "a stray byte"], check=True)

    shelve(
        root,
        slug=PROBE_SLUG,
        kind="session",
        digest=PROBE_DIGEST,
        sections=PROBE_SECTIONS,
        description=LONG_DESCRIPTION,
        approx_tokens=100,
        date=date,
        amend=True,
        autocommit=False,
    )

    expected = raw.decode("utf-8", errors="replace").replace("�", "")
    assert episode.read_text(encoding="utf-8") == expected


@pytest.mark.parametrize(
    "decisions",
    [
        "- Kept the template:\n\n```markdown\n## Open threads\n- none\n```",
        "- Kept the template:\n\n~~~~\n## Open threads\n- none\n~~~~",
        "- Kept the template:\n\n````\n```\n## Open threads\n```\n````",
        "- Kept the template:\n\n~~~\n````\n## Open threads\n````\n~~~",
        "- Kept the template:\n\n```\n```text\n## Open threads\n```",
        "```x` opens no fence: its info string holds a backtick\n```\n## Open threads\n```",
        "- Quoted a template and never closed it:\n\n```markdown\n- none",
    ],
    ids=["backticks", "tildes", "shorter-run", "other-char", "info-string", "no-fence", "unclosed"],
)
def test_amend_reads_past_a_heading_inside_a_fenced_block(tmp_path, decisions):
    """A `## Open threads` line quoted in a fenced block is code. Read as a
    heading, it moved Open threads above Timeline on an amend that passed
    everything back unchanged. A line that only looks like a fence, or a fence
    nothing closes, must not hide the sections after it either."""
    root = _init_shelf(tmp_path)
    sections = {**PROBE_SECTIONS, "Decisions": decisions}
    episode = _older_episode(root, sections=sections)
    before = episode.read_text(encoding="utf-8")
    assert _headings(before)[-3:] == ["Timeline", "Findings", "Open threads"]

    shelve(
        root,
        slug=PROBE_SLUG,
        kind="session",
        digest=PROBE_DIGEST,
        sections=sections,
        description=LONG_DESCRIPTION,
        approx_tokens=100,
        date="2026-10-08",
        amend=True,
        autocommit=False,
    )

    assert episode.read_text(encoding="utf-8") == before


def test_cli_and_schema_take_every_source_an_episode_can_carry(tmp_path, capsys):
    """`--approx-tokens-source` offered estimate|measured while the tool itself
    writes `unmeasured` when no number is passed: a wrapper passing every
    stored field back failed with «invalid choice: 'unmeasured'», and
    `--approx-tokens 0` alone records `estimate` instead."""
    from memshelf_mcp.cli import main
    from memshelf_mcp.tools import ShelveInput

    schema = ShelveInput.model_json_schema()["properties"]["approx_tokens_source"]
    assert tuple(schema["anyOf"][0]["enum"]) == APPROX_TOKENS_SOURCES

    root = _init_shelf(tmp_path)
    shelve(
        root,
        slug="2026-09-01-no-number",
        kind="topic",
        digest=GOOD_DIGEST,
        sections={"Decisions": "JWT chosen."},
        date="2026-09-01",
    )
    episode = tmp_path / "docs" / "topics" / "2026-09-01-no-number.md"
    before = episode.read_text(encoding="utf-8")
    assert "approx_tokens_source: unmeasured" in before

    code = main(
        [
            "shelve",
            "--shelf",
            str(root),
            "--slug",
            "2026-09-01-no-number",
            "--kind",
            "topic",
            "--digest",
            GOOD_DIGEST,
            "--section",
            "Decisions=JWT chosen.",
            "--approx-tokens",
            "0",
            "--approx-tokens-source",
            "unmeasured",
            "--amend",
        ]
    )

    assert code == 0, capsys.readouterr().err
    assert episode.read_text(encoding="utf-8") == before


def test_unmeasured_with_a_number_is_a_contradiction(tmp_path, capsys):
    """The mirror of a source without a number, refused before any write; the
    CLI says so and exits 1 rather than ending in a traceback."""
    from memshelf_mcp.cli import main

    root = _init_shelf(tmp_path)
    with pytest.raises(EpisodeError, match="contradiction"):
        shelve(
            root,
            slug="2026-09-01-contradiction",
            kind="topic",
            digest=GOOD_DIGEST,
            sections={"Decisions": "JWT chosen."},
            approx_tokens=500,
            approx_tokens_source="unmeasured",
            date="2026-09-01",
        )
    assert list((tmp_path / "docs" / "topics").iterdir()) == []

    code = main(
        [
            "shelve",
            "--shelf",
            str(root),
            "--slug",
            "2026-09-01-contradiction",
            "--kind",
            "topic",
            "--digest",
            GOOD_DIGEST,
            "--section",
            "Decisions=JWT chosen.",
            "--approx-tokens",
            "500",
            "--approx-tokens-source",
            "unmeasured",
        ]
    )

    assert code == 1
    assert "contradiction" in capsys.readouterr().err
    assert list((tmp_path / "docs" / "topics").iterdir()) == []
