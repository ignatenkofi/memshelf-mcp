import os
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

pytest.importorskip("docshelf_mcp")

from docshelf_mcp.core.shelf import Shelf  # noqa: E402

from memshelf_mcp.cli import main as cli_main  # noqa: E402
from memshelf_mcp.core.doctor import (  # noqa: E402
    _parse_git_timestamp,
    check_shelf,
    index_entries,
)
from memshelf_mcp.core.rebuild import rebuild  # noqa: E402
from memshelf_mcp.core.shelve import shelve  # noqa: E402
from memshelf_mcp.tools import DoctorInput, run_doctor  # noqa: E402


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


# --- grounding is measured on stems, not on whole tokens --------------------


def test_inflected_russian_digest_is_not_a_mismatch(tmp_path):
    """The four false positives on the live shelf, in one case.

    Exact-token overlap reads «партии»/«партия» and «студентом»/«студента» as
    unrelated words, so a Russian digest scores low no matter how grounded it
    is — a property of the language, not of the digest. The episode below
    plainly summarizes its own body; before stemming it scored 11%.
    """
    root = _init(tmp_path)
    _write_raw(
        root,
        "sessions",
        "2026-07-22-inflected",
        _fm("2026-07-22-inflected", kind="session")
        + "\n## Digest\nХвост сезона проверки домашних заданий потока: три партии работ, "
        "финиш программы студентом с подтверждением кандидатуры, закрытие затянувшейся "
        "саги вторым студентом. Импортирован из фрагмента экспорта переписки; ожидается "
        "доимпорт начала сезона. Каждая арка вынесена отдельным эпизодом.\n"
        "\n## Timeline\nПартия первая: финальная тройка заданий студента, повторная "
        "попытка второго студента и прорыв на пятом уроке. Партия вторая: следующее "
        "задание второго студента. Партия третья: финал саги, последняя работа целого "
        "потока. Проверок больше не запланировано, поток завершён. Импорт выполнен из "
        "фрагмента экспортированной переписки, полная выгрузка ожидалась позже. "
        "Подтверждена кандидатура, отмеченная ранее. Программа закрыта.\n"
        "\n## Open threads\nЭпизоды арок хранятся отдельно, доимпорту начал сезон "
        "помешала утрата исходника.\n",
    )

    report = check_shelf(root)

    assert "digest-body-mismatch" not in _codes(report), [
        f.detail for f in report.findings if f.code == "digest-body-mismatch"
    ]


def test_unrelated_digest_still_flagged_after_stemming(tmp_path):
    """Positive control: loosening the match must not disarm the guard.

    A check that cannot fail is not a check — the lesson the isolation step and
    the gitleaks fixture both taught (devsecops-pipeline#19, form 3).
    """
    root = _init(tmp_path)
    _write_raw(
        root,
        "sessions",
        "2026-07-22-unrelated",
        _fm("2026-07-22-unrelated", kind="session")
        + "\n## Digest\nОбсудили выбор красок для веранды, сравнили матовую и глянцевую "
        "фактуру, договорились про освещение террасы и заказ садовой мебели. Решено "
        "брать светлый оттенок, отложить покупку кресел и позвать столяра весной.\n"
        "\n## Timeline\nПоднимали кластер PostgreSQL, настраивали потоковую репликацию, "
        "чинили автоочистку и перекладывали шардирование. Обсуждали журналирование, "
        "контрольные точки и мониторинг задержки реплики. Разбирали восстановление из "
        "базовой копии, проверку целостности после отказа, поведение планировщика "
        "запросов, сбор статистики и построение индексов. Померили просадку записи под "
        "нагрузкой, сравнили синхронный и асинхронный режимы.\n"
        "\n## Open threads\nУчения по восстановлению.\n",
    )

    report = check_shelf(root)

    assert "digest-body-mismatch" in _codes(report)


def test_years_are_not_shared_vocabulary(tmp_path):
    """Pure digits stopped counting as content words.

    On a dated shelf every episode shares `2026` with every other; the
    docstring always excluded digits, the filter did not.
    """
    from memshelf_mcp.core.doctor import _content_words

    words = _content_words("2026 отчёт 07-09 регламент 1234")
    assert words == {"отчёт", "регламент"}


# ── #89: "not rendered yet" vs "not being rendered" ───────────────────────
#
# Both states produced one signal — `warning no-ledger-row` — and the shelf's
# own rules say the fresh one must not be fixed by hand, so the documented
# response to the only visible symptom of a dead renderer was: ignore it. Nine
# episodes piled up that way before anyone looked.
#
# The pair below is the guard: it has to fire on a shelf whose derived layer
# stopped moving, and stay quiet on one shelved a minute ago. Without both
# halves it is another check that cannot fail.


def _shelf_with_an_uncounted_episode(root):
    """A shelf holding one episode that has no ledger row yet.

    Which is the *normal* state right after a shelve, since #58: the episode is
    the input, `ledger.tsv` is the renderer's output.
    """
    _init(root)
    shelve(
        root,
        slug="2026-08-13-uncounted",
        kind="topic",
        digest=(
            "The renderer writes ledger.tsv from the episodes, so a freshly shelved "
            "episode has no row until it runs. The decided approach keeps that warning "
            "as it is. Open: nothing."
        ),
        sections={"Decisions": "kept"},
        approx_tokens=1000,
        date="2026-08-13",
    )
    return root


def _commit_ledger_at(root, when: str, *, regenerate: bool = True):
    """Commit `ledger.tsv` with `when` as its commit date — the shelf's clock.

    ``regenerate=False`` keeps whatever the renderer just wrote, for the case
    where the accounting is complete and only the date is old.
    """
    if regenerate:
        (root / "ledger.tsv").write_text(
            "date\tepisode_id\tmode\tapprox_tokens_in\tdigest_tokens\tnotes\n", encoding="utf-8"
        )
    env = {"GIT_AUTHOR_DATE": when, "GIT_COMMITTER_DATE": when}
    subprocess.run(["git", "-C", str(root), "add", "ledger.tsv"], check=True)
    subprocess.run(
        ["git", "-C", str(root), "commit", "-q", "-m", "chore: regenerate derived files"],
        check=True,
        env={**os.environ, **env},
    )


def test_a_derived_layer_that_stopped_moving_is_an_error(tmp_path):
    root = _shelf_with_an_uncounted_episode(tmp_path)
    _commit_ledger_at(root, "2026-08-10T09:00:00+00:00")

    report = check_shelf(root, now=datetime(2026, 8, 14, 20, 0, tzinfo=timezone.utc))

    assert "derived-stale" in _codes(report), report.as_dict()
    finding = next(f for f in report.findings if f.code == "derived-stale")
    assert finding.level == "error"
    # The episode list is the payload: a diagnosis with no subject is a mood.
    assert "2026-08-13-uncounted" in finding.detail
    # And the old warning stays, because it is right about each episode.
    assert "no-ledger-row" in _codes(report)


def test_a_shelf_shelved_a_minute_ago_stays_a_warning(tmp_path):
    """The other half. A guard that fires here would train people to ignore it."""
    root = _shelf_with_an_uncounted_episode(tmp_path)
    _commit_ledger_at(root, "2026-08-14T19:30:00+00:00")

    report = check_shelf(root, now=datetime(2026, 8, 14, 20, 0, tzinfo=timezone.utc))

    assert "derived-stale" not in _codes(report), report.as_dict()
    assert "no-ledger-row" in _codes(report)
    assert report.as_dict()["errors"] == 0


def test_an_old_but_complete_shelf_is_not_stale(tmp_path):
    """Age alone is not the finding — an uncounted episode is what makes it one.

    A shelf nobody has touched in a month is fine; its accounting is complete.
    Without this the check would red every dormant shelf and mean nothing.
    """
    root = _shelf_with_an_uncounted_episode(tmp_path)
    rebuild(root)  # the renderer catches up: every episode now has a row
    _commit_ledger_at(root, "2026-07-01T09:00:00+00:00", regenerate=False)

    report = check_shelf(root, now=datetime(2026, 8, 14, 20, 0, tzinfo=timezone.utc))

    assert "derived-stale" not in _codes(report), report.as_dict()


def test_the_stale_threshold_is_the_shelfs_to_pick(tmp_path):
    """#89 left the threshold a parameter; a shelf must be able to say "six
    hours of renderer silence is an outage here" without editing the library.
    One fixture read through two thresholds — only the number moves the
    verdict, which is what proves the knob is connected end to end."""
    root = _shelf_with_an_uncounted_episode(tmp_path)
    twelve_hours_ago = (datetime.now(timezone.utc) - timedelta(hours=12)).isoformat()
    _commit_ledger_at(root, twelve_hours_ago)

    lenient = run_doctor(DoctorInput(shelf_path=str(root)))
    strict = run_doctor(DoctorInput(shelf_path=str(root), derived_stale_after_hours=6))

    assert "derived-stale" not in {f["code"] for f in lenient["findings"]}, lenient
    assert "derived-stale" in {f["code"] for f in strict["findings"]}, strict


def test_the_stale_threshold_flag_moves_the_exit_code(tmp_path, capsys):
    """CI and the pre-push habit read nothing but the exit code, so the CLI
    flag has to reach all the way down to it."""
    root = _shelf_with_an_uncounted_episode(tmp_path)
    twelve_hours_ago = (datetime.now(timezone.utc) - timedelta(hours=12)).isoformat()
    _commit_ledger_at(root, twelve_hours_ago)

    assert cli_main(["doctor", "--shelf", str(root)]) == 0
    assert cli_main(["doctor", "--shelf", str(root), "--derived-stale-hours", "6"]) == 1
    capsys.readouterr()  # the JSON reports are not this test's subject


@pytest.mark.parametrize(
    "printed",
    [
        "2026-08-10T09:00:00+00:00",  # what git prints in the dev container
        "2026-08-10T09:00:00Z",  # what git 2.54 printed on the CI runner
        "2026-08-10T11:00:00+02:00",  # and any other offset
    ],
)
def test_git_timestamps_parse_in_both_spellings(printed):
    """Both spellings, because the environment picks one and CI picked the other.

    Python 3.10's `fromisoformat` — this package's floor — rejects the trailing
    `Z`, so the first version of this check raised `ValueError` on every doctor
    run under a UTC-offset environment while the dev container stayed green. An
    end-to-end test cannot cover this: it exercises whatever spelling the local
    git happens to use.
    """
    parsed = _parse_git_timestamp(printed)
    assert parsed is not None, printed
    assert parsed.utctimetuple()[:5] == (2026, 8, 10, 9, 0)


def test_an_unreadable_timestamp_costs_the_finding_not_the_diagnosis():
    """A clock we cannot read must not take doctor down with it."""
    assert _parse_git_timestamp("not a date") is None


def _bloat_shelf(root, *, entries, title_chars):
    """A shelf whose INDEX entries are individually overpriced.

    Long *titles*, because that is the fat that is still reachable: since the
    2026-08-21 fix, descriptions are capped on both the write and the render
    path, so an oversized description can no longer reach INDEX at all.
    """
    _init(root)
    for n in range(entries):
        # Real calendar dates: day 32 of January is not a date, and the slug
        # contract (#101) rightly refuses one.
        day = f"2026-{n // 28 + 1:02d}-{n % 28 + 1:02d}"
        shelve(
            root,
            slug=f"{day}-topic-{n}",
            kind="topic",
            digest="Решение принято, альтернатива отвергнута по замеру. Остаток открыт.",
            sections={"Decisions": "Записано."},
            display_title=("Очень длинный заголовок эпизода " * 20)[:title_chars] + str(n),
            approx_tokens=100,
            date=day,
        )
    rebuild(root)
    return root


def test_index_bloat_reports_the_price_of_a_line_not_just_the_total(tmp_path):
    """A total alone cannot be acted on: a big INDEX on a big shelf is
    navigation working as designed. The number with a fix behind it is the
    per-entry cost, so the finding has to name it."""
    root = _bloat_shelf(tmp_path / "shelf", entries=12, title_chars=400)

    report = check_shelf(root)

    (finding,) = [f for f in report.findings if f.code == "index-bloat"]
    assert "per entry" in finding.detail
    assert "allowance 80" in finding.detail
    assert "12 listed" in finding.detail


def test_index_bloat_no_longer_prescribes_a_rollup(tmp_path):
    """The old advice was "roll up old episodes", and on a size-aware budget
    that is not merely unhelpful but wrong: folding entries removes them and
    their allowance together, so it cannot move the per-entry price. Prescribing
    it made archiving live memory the standard way to pass the check."""
    root = _bloat_shelf(tmp_path / "shelf", entries=12, title_chars=400)

    (finding,) = [f for f in check_shelf(root).findings if f.code == "index-bloat"]

    fix = finding.fix.lower()
    # A rollup may be *mentioned* — ruling it out is half the advice. What it
    # must never be is prescribed, so the only command offered is `rebuild`.
    assert "a rollup drops the budget along with the lines and would not fix it" in fix
    assert "memshelf rollup" not in fix
    assert "memshelf rebuild" in fix
    # And it names the field that is actually over — here the titles, which are
    # the one entry term with no cap.
    assert "titles" in fix and "display_title" in fix


def test_growth_alone_never_trips_index_bloat(tmp_path):
    """The property the absolute 2500 lacked. It went unreachable at ~30
    episodes, and the only lever that lowered the number was archiving."""
    root = _bloat_shelf(tmp_path / "shelf", entries=90, title_chars=60)

    index_tokens = len((root / "INDEX.md").read_text(encoding="utf-8")) // 4
    # The shelf has to be big enough that the *old* constant would have fired,
    # or this test cannot detect the regression it is named after. Caught in
    # review: at 40 entries the fixture rendered 1610 tokens and passed just as
    # happily with `index_budget` reverted to `return 2500`.
    assert index_tokens > 2500, f"fixture too small to be a regression guard ({index_tokens})"
    assert "index-bloat" not in _codes(check_shelf(root))


def test_the_budget_is_counted_off_the_index_not_off_the_episodes(tmp_path):
    """Numerator and denominator must come from the same artifact.

    `doctor` compares the budget against the *size of INDEX.md*, so counting
    episodes on disk instead of lines in the file lets the two disagree — and
    under #58 they disagree by design whenever the renderer is behind. Here the
    disk holds 13 episodes and INDEX lists 7; counting the disk inflates the
    budget by 480 tokens and hides a real overage.
    """
    from memshelf_mcp.core.archive import rollup

    root = _bloat_shelf(tmp_path / "shelf", entries=12, title_chars=400)
    rollup(root, slug="2026-01-06-rollup", digest=DIGEST_FOR_ROLLUP, until="2026-01-06")

    listed = len(index_entries((root / "INDEX.md").read_text(encoding="utf-8")))
    (finding,) = [f for f in check_shelf(root).findings if f.code == "index-bloat"]

    assert f"×{listed} listed" in finding.detail
    assert listed < check_shelf(root).episodes_checked


def test_archived_episodes_earn_no_index_allowance(tmp_path):
    """Entries are counted off the rendered INDEX, so a rolled-up episode stops
    paying and stops being paid for in the same breath. Counting episodes on
    disk instead would hand the shelf 80 tokens of allowance per line it no
    longer carries — and a rollup would then *raise* the budget."""
    from memshelf_mcp.core.advisor import advise
    from memshelf_mcp.core.archive import rollup
    from memshelf_mcp.core.doctor import index_budget

    root = _bloat_shelf(tmp_path / "shelf", entries=20, title_chars=60)
    before = advise(root).index_budget_tokens

    rollup(root, slug="2026-01-10-rollup", digest=DIGEST_FOR_ROLLUP, until="2026-01-10")

    after = advise(root).index_budget_tokens
    listed = len(index_entries((root / "INDEX.md").read_text(encoding="utf-8")))
    assert after == index_budget(listed)
    assert after < before


DIGEST_FOR_ROLLUP = (
    "Период свёрнут: решения по middleware закрыты, альтернатива с cookie-session "
    "отвергнута для межсервисных вызовов, ротация общего секрета осталась открытой."
)


# --- #154 option 3: the renderer is judged only on what it could see --------


def _shelf_with_origin_and_old_ledger(tmp_path):
    """A bot shelf as the 2026-08-21 measurement found it: ledger rendered and
    pushed long ago, and one fresh episode sitting in a local, unpushed commit."""
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True)
    root = _init(tmp_path / "shelf")
    subprocess.run(["git", "-C", str(root), "checkout", "-qb", "main"], check=True)
    subprocess.run(["git", "-C", str(root), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(root), "commit", "-qm", "init shelf"], check=True)
    _commit_ledger_at(root, "2026-08-10T09:00:00+00:00")
    subprocess.run(["git", "-C", str(root), "remote", "add", "origin", str(origin)], check=True)
    subprocess.run(["git", "-C", str(root), "push", "-qu", "origin", "main"], check=True)
    shelve(
        root,
        slug="2026-08-21-unpushed",
        kind="topic",
        digest=(
            "The renderer writes ledger.tsv from the episodes, so a freshly shelved "
            "episode has no row until it runs. The decided approach keeps that warning "
            "as it is. Open: nothing."
        ),
        sections={"Decisions": "kept"},
        approx_tokens=1000,
        date="2026-08-21",
    )
    return origin, root


def test_an_unpushed_episode_is_not_a_stalled_renderer(tmp_path):
    """The 2026-08-21 false verdict, reproduced: doctor blamed the bot for an
    episode the bot could not see, and the fork's literal reading led straight
    into the #58 conflict class (main-memshelf#154)."""
    _origin, root = _shelf_with_origin_and_old_ledger(tmp_path)

    report = check_shelf(root, now=datetime(2026, 8, 21, 20, 0, tzinfo=timezone.utc))

    assert "derived-stale" not in _codes(report), report.as_dict()
    finding = next(f for f in report.findings if f.code == "episode-unpushed")
    assert finding.level == "warning"
    assert "2026-08-21-unpushed" in finding.detail
    assert "origin/main" in finding.detail
    assert "push" in finding.fix and "fetch" in finding.fix
    assert report.as_dict()["errors"] == 0


def test_a_pushed_episode_with_a_stopped_renderer_is_still_an_error(tmp_path):
    """The other half: once the bot could see the episode, silence IS the bot's."""
    _origin, root = _shelf_with_origin_and_old_ledger(tmp_path)
    subprocess.run(["git", "-C", str(root), "push", "-q", "origin", "main"], check=True)
    subprocess.run(["git", "-C", str(root), "fetch", "-q", "origin", "main"], check=True)

    report = check_shelf(root, now=datetime(2026, 8, 21, 20, 0, tzinfo=timezone.utc))

    assert "derived-stale" in _codes(report), report.as_dict()
    assert "episode-unpushed" not in _codes(report)


# --- #125: freshness as doctor findings -------------------------------------


def _served_package_dir():
    import memshelf_mcp

    return Path(memshelf_mcp.__file__).resolve().parent


def _checkout_beside(root, package_dir=None):
    """A memshelf-mcp checkout in the documented place: next to the shelf."""
    import shutil

    target = root.parent / "memshelf-mcp" / "src" / "memshelf_mcp"
    if package_dir is None:
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(
            _served_package_dir(),
            target,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )
    else:
        target.mkdir(parents=True, exist_ok=True)
        (target / "__init__.py").write_text('__version__ = "9.9.9"\n', encoding="utf-8")
    return target


def test_a_whole_install_reports_no_freshness_findings(tmp_path):
    """#125 DoD: the rule is silent while things are whole."""
    root = _init(tmp_path / "shelf")
    _checkout_beside(root)
    codes = _codes(check_shelf(root))
    assert "served-code-differs" not in codes
    assert "freshness-unknown" not in codes


def test_served_code_that_differs_from_the_checkout_is_an_error(tmp_path):
    """#125 DoD: the 18h20m window — a merged fix not serving — must sound."""
    root = _init(tmp_path / "shelf")
    _checkout_beside(root, package_dir="stub")
    report = check_shelf(root)
    finding = next(f for f in report.findings if f.code == "served-code-differs")
    assert finding.level == "error"
    assert not report.ok


def test_no_checkout_is_unknown_not_silence_and_not_ok(tmp_path):
    """#125 DoD positive control: «don't know», said aloud."""
    root = _init(tmp_path / "shelf")
    report = check_shelf(root)
    finding = next(f for f in report.findings if f.code == "freshness-unknown")
    assert finding.level == "unknown"
    assert report.as_dict()["unknowns"] >= 1
    # unknown is not an error — a shelf must stay pushable on a machine that
    # simply has no sources to compare with…
    assert "served-code-differs" not in _codes(report)


def test_the_checkout_can_be_named_explicitly(tmp_path, monkeypatch):
    root = _init(tmp_path / "shelf")
    elsewhere = tmp_path / "elsewhere" / "src" / "memshelf_mcp"
    import shutil

    shutil.copytree(
        _served_package_dir(), elsewhere, ignore=shutil.ignore_patterns("__pycache__", "*.pyc")
    )
    monkeypatch.setenv("MEMSHELF_CHECKOUT", str(elsewhere))
    codes = _codes(check_shelf(root))
    assert "freshness-unknown" not in codes
    assert "served-code-differs" not in codes


def test_merged_but_unreleased_stays_out_of_doctor(tmp_path, monkeypatch):
    """#125 DoD: (a) was true for 20 straight days — doctor must not ask it."""
    from memshelf_mcp.core import freshness

    def _forbidden(*args, **kwargs):
        raise AssertionError("doctor called unreleased_commits — (a) belongs to the CLI probe")

    monkeypatch.setattr(freshness, "unreleased_commits", _forbidden)
    root = _init(tmp_path / "shelf")
    _checkout_beside(root)
    codes = _codes(check_shelf(root))  # must not raise
    assert "served-code-differs" not in codes
