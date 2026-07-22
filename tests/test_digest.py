from memshelf_mcp.core.digest import validate_digest

GOOD = (
    "Case B — the week of live shelving declared at memshelf's founding — "
    "closed on 2026-07-22. The ledger shows 33 episodes and a 334:1 "
    "compression ratio with zero loss. The founding invariant shelve-at-close "
    "held; issue #20 resolved toward multi-shelf. Open: the fetch-hit metric "
    "was never tracked."
)


def test_good_digest_passes():
    result = validate_digest(GOOD)
    assert result.ok, result.report()
    assert not result.errors


def test_empty_rejected():
    result = validate_digest("   ")
    assert not result.ok
    assert result.errors[0].code == "empty"


def test_too_long_rejected():
    result = validate_digest(" ".join(["word"] * 130))
    assert not result.ok
    assert any(f.code == "too-long" for f in result.errors)
    assert "Cut 10" in result.report()  # message is actionable


def test_we_referent_rejected():
    result = validate_digest("We decided to drop the tower plan for the polygon.")
    assert not result.ok
    assert any(f.code == "referent-we" for f in result.errors)


def test_secret_in_digest_rejected():
    result = validate_digest("Rotated the leaked key ghp_" + "b" * 36 + " after the decision.")
    assert not result.ok
    assert any(f.code == "secret" for f in result.errors)


def test_bare_opener_warns_but_passes():
    # A bare "It" opener is a warning, so the digest still passes.
    result = validate_digest("The refactor landed. It replaced the auth layer, a decided change.")
    assert result.ok
    assert any(f.code == "referent-bare" for f in result.warnings)


def test_thin_digest_warns_but_passes():
    result = validate_digest("A catalog of RouterOS CLI commands, for reference.")
    assert result.ok
    assert any(f.code == "thin" for f in result.warnings)


def test_russian_verb_nashel_is_not_a_referent():
    # «нашёл» ("found") shares a prefix with «наш» ("our") but is a verb; the
    # open prefix `наш\w*` used to reject it (#45).
    result = validate_digest(
        "Doctor нашёл реальный дрейф полки; решение зафиксировано, хвостов нет."
    )
    assert result.ok, result.report()
    assert not any(f.code == "referent-we" for f in result.findings)


def test_russian_possessives_still_rejected():
    for text in ("наш план выбран.", "Итог наших решений открыт.", "Наша схема отклонена."):
        result = validate_digest(text)
        assert any(f.code == "referent-we" for f in result.errors), text


def test_russian_digest_passes():
    ru = (
        "Полка закрыта 2026-07-22. Итог: 33 эпизода, сжатие 334:1, потерь ноль. "
        "Подтверждено правило shelve-at-close; #20 решён в пользу мульти-полок. "
        "Открыт вопрос метрики fetch-hit."
    )
    result = validate_digest(ru)
    assert result.ok, result.report()
