from memshelf_mcp.core.redact import redact, scan


def test_clean_text_untouched():
    text = "The poll loop retries with backoff; nothing secret here."
    out, report = redact(text)
    assert out == text
    assert report.clean
    assert report.total == 0


def test_github_token_redacted():
    out, report = redact("push with ghp_" + "a" * 36 + " then done")
    assert "ghp_" not in out
    assert "«redacted:github-token»" in out
    assert report.counts["github-token"] == 1


def test_sonar_token_redacted():
    out, report = redact("squ_" + "0" * 40)
    assert out == "«redacted:sonar-token»"
    assert not report.clean


def test_aws_key_redacted():
    out, _ = redact("AKIA" + "A" * 16)
    assert out == "«redacted:aws-access-key»"


def test_bearer_redacted():
    out, report = redact("Authorization: Bearer abcdef0123456789ghij")
    assert "«redacted:bearer»" in out
    assert report.counts["bearer"] == 1


def test_env_secret_keeps_key_masks_value():
    out, report = redact("SONAR_TOKEN=squ_notarealvalue_shouldbemasked")
    assert out.startswith("SONAR_TOKEN=")
    assert "«redacted:env-secret»" in out
    assert not report.clean


def test_plain_assignment_survives():
    # A non-secret assignment must not be touched.
    out, report = redact("retries = 4")
    assert out == "retries = 4"
    assert report.clean


def test_extra_pattern_layered_in():
    out, report = redact(
        "ping nick_ivanov about it",
        extra_patterns=[("nickname", r"nick_\w+")],
    )
    assert "«redacted:nickname»" in out
    assert report.counts["nickname"] == 1


def test_report_summary_lists_kinds():
    out, report = redact("squ_" + "f" * 40 + " and ghp_" + "b" * 36)
    assert "sonar-token" in report.summary()
    assert "github-token" in report.summary()
    assert report.total == 2


def test_scan_reports_without_rewriting():
    report = scan("squ_" + "f" * 40)
    assert not report.clean
    assert report.counts["sonar-token"] == 1


def test_env_secret_redaction_is_idempotent():
    # shelve() stores `KEY=«redacted:env-secret»`; a re-scan (doctor) must not
    # flag it again, and a second redact() pass must change nothing.
    once, first = redact("SONAR_TOKEN=squ_livevalue")
    assert first.counts["env-secret"] == 1
    twice, second = redact(once)
    assert twice == once
    assert second.clean


def test_scan_ignores_already_redacted_values():
    assert scan("SONAR_TOKEN=«redacted:env-secret» in the runbook").clean
