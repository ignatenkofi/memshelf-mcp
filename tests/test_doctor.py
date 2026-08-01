import subprocess

import pytest

pytest.importorskip("docshelf_mcp")

from docshelf_mcp.core.shelf import Shelf  # noqa: E402

from memshelf_mcp.core.doctor import check_shelf  # noqa: E402
from memshelf_mcp.core.rebuild import rebuild  # noqa: E402
from memshelf_mcp.core.shelve import shelve  # noqa: E402


def _init(root):
    Shelf(root).init(name="t", default_categories=["topics", "research", "sessions"])
    subprocess.run(["git", "-C", str(root), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.email", "t@t.test"], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.name", "tester"], check=True)
    return root


def _codes(report):
    return {f.code for f in report.findings}


def _write_raw(root, category, name, text):
    cat = root / "docs" / category
    cat.mkdir(parents=True, exist_ok=True)
    (cat / f"{name}.md").write_text(text, encoding="utf-8")
    Shelf(root).rebuild_index()


def _fm(name, kind="topic", **overrides):
    """A complete, SPEC-5.2-valid frontmatter block for raw fixtures, so tests
    that target one specific finding don't trip the required-field checks."""
    fields = {
        "id": name,
        "kind": kind,
        "span": "2026-07-22",
        "tags": "[]",
        "approx_tokens": "100",
    }
    fields.update(overrides)
    lines = "\n".join(f"{k}: {v}" for k, v in fields.items() if v is not None)
    return f"# {name}\n\n---\n{lines}\n---\n"


def test_clean_shelf_is_healthy(tmp_path):
    root = _init(tmp_path)
    shelve(
        root,
        slug="2026-07-22-ok",
        kind="topic",
        digest="The plan chose X; the Y alternative was rejected. Open: nothing.",
        sections={"Decisions": "X over Y"},
        date="2026-07-22",
    )
    report = check_shelf(root)
    assert report.ok  # info/warnings allowed; no errors
    assert report.episodes_checked == 1


def test_secret_at_rest_flagged(tmp_path):
    root = _init(tmp_path)
    _write_raw(
        root,
        "topics",
        "2026-07-22-leak",
        _fm("2026-07-22-leak") + "\n## Digest\nA decided change; nothing open.\n\n"
        "## Decisions\npasted token ghp_" + "a" * 36 + "\n",
    )
    report = check_shelf(root)
    assert not report.ok
    assert "secret-at-rest" in _codes(report)


def test_missing_required_section_flagged(tmp_path):
    root = _init(tmp_path)
    _write_raw(
        root,
        "topics",
        "2026-07-22-bad",
        _fm("2026-07-22-bad")
        + "\n## Digest\nA decided change; nothing open.\n",  # topic without ## Decisions
    )
    report = check_shelf(root)
    assert not report.ok
    assert "missing-section" in _codes(report)


def test_tool_shelved_env_secret_stays_healthy(tmp_path):
    # An env-secret goes in, shelve() masks the value, and doctor must NOT
    # re-flag the stored `KEY=«redacted:env-secret»` (idempotence).
    root = _init(tmp_path)
    shelve(
        root,
        slug="2026-07-22-env",
        kind="topic",
        digest="The runbook decision: keep tokens in env files. Open: nothing.",
        sections={"Decisions": "SONAR_TOKEN=squ_someval moved to ~/.sqst-env"},
        date="2026-07-22",
    )
    report = check_shelf(root)
    assert report.ok, [f.code for f in report.findings]
    assert "secret-at-rest" not in _codes(report)


def test_orphan_ledger_row_flagged(tmp_path):
    root = _init(tmp_path)
    shelve(
        root,
        slug="2026-07-22-ok",
        kind="topic",
        digest="The plan chose X; the Y alternative was rejected. Open: nothing.",
        sections={"Decisions": "X"},
        date="2026-07-22",
    )
    rebuild(root)  # the ledger is derived now (#58)
    with (tmp_path / "ledger.tsv").open("a", encoding="utf-8") as fh:
        fh.write("2026-07-22\t2026-07-22-ghost\tlive\t100\t20\t\n")
    report = check_shelf(root)
    assert "orphan-ledger-row" in _codes(report)


# --- remote-visibility gate (MANIFEST principle 8) --------------------------


def test_remote_check_off_by_default(tmp_path):
    # A public remote is not probed unless the caller opts in.
    root = _init(tmp_path)
    subprocess.run(
        ["git", "-C", str(root), "remote", "add", "origin", "https://github.com/o/pub.git"],
        check=True,
    )
    report = check_shelf(root)  # no check_remote
    assert "public-remote" not in _codes(report)
    assert "no-remote" not in _codes(report)


def test_public_remote_fails_the_shelf(tmp_path):
    root = _init(tmp_path)
    subprocess.run(
        ["git", "-C", str(root), "remote", "add", "origin", "https://github.com/o/pub.git"],
        check=True,
    )
    report = check_shelf(root, check_remote=True, remote_prober=lambda url: ("public", "is public"))
    assert not report.ok
    assert "public-remote" in _codes(report)


def test_private_remote_passes(tmp_path):
    root = _init(tmp_path)
    subprocess.run(
        ["git", "-C", str(root), "remote", "add", "origin", "git@github.com:o/priv.git"],
        check=True,
    )
    report = check_shelf(root, check_remote=True, remote_prober=lambda url: ("private", "401"))
    assert report.ok
    assert "remote-private" in _codes(report)


def test_unverifiable_remote_warns_not_errors(tmp_path):
    root = _init(tmp_path)
    subprocess.run(
        ["git", "-C", str(root), "remote", "add", "origin", "https://host/o/r.git"],
        check=True,
    )
    report = check_shelf(root, check_remote=True, remote_prober=lambda url: ("unknown", "dns down"))
    assert report.ok  # a flaky network must not block the shelf
    assert "remote-unverified" in _codes(report)


def test_no_remote_is_info(tmp_path):
    root = _init(tmp_path)
    report = check_shelf(root, check_remote=True)
    assert report.ok
    assert "no-remote" in _codes(report)


# --- machine-readable POLICY pattern packs (#16) ----------------------------


def test_policy_pattern_at_rest_flagged(tmp_path):
    root = _init(tmp_path)
    (root / "POLICY.patterns").write_text("student-id  S[0-9]{1,2}\n", encoding="utf-8")
    _write_raw(
        root,
        "topics",
        "2026-07-22-leak",
        _fm("2026-07-22-leak") + "\n## Digest\nA decided change; nothing open.\n\n"
        "## Decisions\nhand-written note mentioning S7 by id\n",
    )
    report = check_shelf(root)
    assert not report.ok
    assert "policy-pattern-at-rest" in _codes(report)


def test_no_policy_pack_means_no_policy_findings(tmp_path):
    root = _init(tmp_path)
    _write_raw(
        root,
        "topics",
        "2026-07-22-plain",
        _fm("2026-07-22-plain")
        + "\n## Digest\nA decided change; nothing open.\n\n## Decisions\nmentions S7 freely\n",
    )
    report = check_shelf(root)
    assert "policy-pattern-at-rest" not in _codes(report)


def test_malformed_policy_pack_is_a_warning(tmp_path):
    root = _init(tmp_path)
    (root / "POLICY.patterns").write_text("broken  [unterminated\n", encoding="utf-8")
    report = check_shelf(root)
    assert "policy-pattern-invalid" in _codes(report)
    assert report.ok  # a broken pack warns; it does not hard-fail the shelf


# --- digest/body mismatch sampling (write-only-memory guard) ----------------


def test_digest_body_mismatch_flagged(tmp_path):
    # A body rich in distinct content words that the digest never touches.
    root = _init(tmp_path)
    body = (
        "Billing migration reshaped invoices, refunds, chargebacks, dunning, webhooks, "
        "reconciliation, ledger, postgres, replication, decoding, slots, scheduler, batches, "
        "cutover, throughput, latency, indexes, vacuum, partitions, sharding, failover, replica, "
        "primary, checkpoint, backpressure, worker, retries, idempotency, deadlock, isolation, "
        "serializable, snapshot, rollback, savepoint, cursor, pagination, throttling, quotas, "
        "metrics, dashboards, alerting, oncall, telemetry."
    )
    _write_raw(
        root,
        "topics",
        "2026-07-22-drift",
        _fm("2026-07-22-drift")
        + "\n## Digest\nThe committee chose lunch options; sandwiches beat salads "
        "after tasting; dessert stays undecided; catering vendor picks Friday.\n\n"
        f"## Decisions\n{body}\n",
    )
    report = check_shelf(root)
    assert "digest-body-mismatch" in _codes(report)
    # a heuristic guard is a warning, never a hard failure
    assert report.ok


def test_grounded_digest_not_flagged(tmp_path):
    root = _init(tmp_path)
    shelve(
        root,
        slug="2026-07-22-grounded",
        kind="topic",
        digest=(
            "The billing migration chose logical decoding slots over trigger "
            "replication; reconciliation of invoices, refunds, and chargebacks was "
            "reworked; the overnight scheduler drains before cutover. Open: dunning webhooks."
        ),
        sections={
            "Decisions": (
                "Billing migration: logical decoding slots replaced trigger replication "
                "because replication lag broke reconciliation. Invoices, refunds, and "
                "chargebacks reconcile nightly; the scheduler drains overnight batches "
                "before the cutover window. Dunning webhooks stay open."
            )
        },
        date="2026-07-22",
    )
    report = check_shelf(root)
    assert "digest-body-mismatch" not in _codes(report)


# --- frontmatter completeness per shelf-spec v0 § 5.2 (#56) ------------------


def test_missing_span_fails_doctor(tmp_path):
    # The #56 regression: an episode without `span` passed doctor as healthy
    # while the shelf's advisory CI (shelf_validate) rejected it.
    root = _init(tmp_path)
    _write_raw(
        root,
        "topics",
        "2026-07-27-no-span",
        _fm("2026-07-27-no-span", span=None)
        + "\n## Digest\nA decided change; nothing open.\n\n## Decisions\nX over Y\n",
    )
    report = check_shelf(root)
    assert not report.ok
    assert "frontmatter-missing-field" in _codes(report)
    detail = next(f for f in report.findings if f.code == "frontmatter-missing-field").detail
    assert "'span'" in detail


def test_non_integer_approx_tokens_flagged(tmp_path):
    root = _init(tmp_path)
    _write_raw(
        root,
        "topics",
        "2026-07-27-bad-tokens",
        _fm("2026-07-27-bad-tokens", approx_tokens="a lot")
        + "\n## Digest\nA decided change; nothing open.\n\n## Decisions\nX over Y\n",
    )
    report = check_shelf(root)
    assert not report.ok
    assert "bad-approx-tokens" in _codes(report)


def test_no_frontmatter_flagged_once(tmp_path):
    root = _init(tmp_path)
    _write_raw(
        root,
        "topics",
        "2026-07-27-bare",
        "# 2026-07-27-bare\n\n## Digest\nA decided change; nothing open.\n\n## Decisions\nX\n",
    )
    report = check_shelf(root)
    assert not report.ok
    assert "no-frontmatter" in _codes(report)
    # the block-level finding replaces five per-field ones
    assert "frontmatter-missing-field" not in _codes(report)


def test_id_mismatch_is_an_error(tmp_path):
    # shelf_validate errors on id != stem, so doctor must too — a warning here
    # would recreate the false "doctor clean, CI red" guarantee.
    root = _init(tmp_path)
    _write_raw(
        root,
        "topics",
        "2026-07-27-actual-name",
        _fm("2026-07-27-actual-name", id="2026-07-27-other-id")
        + "\n## Digest\nA decided change; nothing open.\n\n## Decisions\nX over Y\n",
    )
    report = check_shelf(root)
    assert not report.ok
    assert "id-mismatch" in _codes(report)


def test_tool_shelved_episode_without_span_passes_doctor(tmp_path):
    # End-to-end guarantee for #56: the tool defaults span, doctor stays clean.
    root = _init(tmp_path)
    shelve(
        root,
        slug="2026-07-27-defaulted",
        kind="topic",
        digest="The plan chose X; the Y alternative was rejected. Open: nothing.",
        sections={"Decisions": "X over Y"},
        date="2026-07-27",
    )
    report = check_shelf(root)
    assert report.ok, [f.code for f in report.findings]


# --- the register itself: uniqueness + column format (#63, #65, #66) --------


def _seed_one(root, slug="2026-07-22-ok"):
    shelve(
        root,
        slug=slug,
        kind="topic",
        digest="The plan chose X; the Y alternative was rejected. Open: nothing.",
        sections={"Decisions": "X"},
        date=slug[:10],
    )
    rebuild(root)  # the ledger is derived now (#58)


def test_duplicate_episode_id_in_ledger_is_an_error(tmp_path):
    """The 30-duplicate corruption of 2026-08-01 passed doctor with 0 errors.

    That green signal is what let it reach main: the shelf rule is "doctor
    clean ⇒ safe to push".
    """
    root = _init(tmp_path)
    _seed_one(root)
    ledger = tmp_path / "ledger.tsv"
    rows = ledger.read_text(encoding="utf-8").splitlines(keepends=True)
    ledger.write_text("".join(rows + [rows[-1]]), encoding="utf-8")

    report = check_shelf(root)

    assert not report.ok
    assert "ledger-malformed" in _codes(report)
    detail = next(f.detail for f in report.findings if f.code == "ledger-malformed")
    assert "already recorded on line" in detail


def test_span_interval_in_the_date_column_is_an_error(tmp_path):
    """What `shelf-spec validate` rejected while doctor stayed green (#65/#66).

    doctor's coverage has to be a superset of the spec validator's, or the
    shelf's own gate is weaker than the advisory CI it is supposed to precede.
    """
    root = _init(tmp_path)
    _seed_one(root)
    ledger = tmp_path / "ledger.tsv"
    ledger.write_text(
        ledger.read_text(encoding="utf-8").replace("2026-07-22\t", "2026-07-21..2026-07-22\t", 1),
        encoding="utf-8",
    )

    report = check_shelf(root)

    assert not report.ok
    assert "ledger-malformed" in _codes(report)


@pytest.mark.parametrize(
    "bad_row",
    [
        "2026-07-23\t2026-07-23-x\tlive\t100\n",  # too few columns
        "2026-07-23\t2026-07-23-x\thearsay\t100\t20\t\n",  # mode outside the spec
        "2026-07-23\t2026-07-23-x\tlive\tmany\t20\t\n",  # non-numeric token count
    ],
)
def test_malformed_ledger_rows_are_errors(tmp_path, bad_row):
    root = _init(tmp_path)
    _seed_one(root)
    with (tmp_path / "ledger.tsv").open("a", encoding="utf-8") as fh:
        fh.write(bad_row)

    report = check_shelf(root)

    assert "ledger-malformed" in _codes(report)
    assert not report.ok


def test_bad_ledger_header_is_an_error(tmp_path):
    root = _init(tmp_path)
    _seed_one(root)
    (tmp_path / "ledger.tsv").write_text("date,episode_id\n", encoding="utf-8")

    report = check_shelf(root)

    assert "ledger-malformed" in _codes(report)


def test_regenerated_ledger_passes_the_register_checks(tmp_path):
    root = _init(tmp_path)
    _seed_one(root, "2026-07-22-one")
    _seed_one(root, "2026-07-23-two")

    report = check_shelf(root)

    assert "ledger-malformed" not in _codes(report)
    assert report.ok, [f.code for f in report.findings]
