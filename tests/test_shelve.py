import json
import subprocess

import pytest

pytest.importorskip("docshelf_mcp")

from docshelf_mcp.core.shelf import Shelf  # noqa: E402

from memshelf_mcp.core.shelve import DigestContractError, shelve  # noqa: E402

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


def test_shelve_writes_episode_ledger_and_commit(tmp_path):
    result = shelve(
        _init_shelf(tmp_path),
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
    assert "2026-07-22-auth-refactor" in (tmp_path / "INDEX.md").read_text(encoding="utf-8")

    ledger = (tmp_path / "ledger.tsv").read_text(encoding="utf-8").splitlines()
    assert ledger[0].startswith("date\t")
    assert ledger[-1].split("\t")[:3] == ["2026-07-22", "2026-07-22-auth-refactor", "live"]

    assert result.committed and result.commit
    assert result.address == "docs/topics/2026-07-22-auth-refactor.md"


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
    # file keeps the latin slug; the display title lands in .meta.json + INDEX
    assert (tmp_path / "docs" / "research" / "2026-07-22-founding.md").is_file()
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
