from memshelf_mcp.core.policy import load_pattern_pack, parse_pack


def test_parse_valid_rules():
    pack = parse_pack("email  a@b\nstudent-id  S[0-9]{1,2}\n")
    assert pack.ok
    assert pack.patterns == [("email", "a@b"), ("student-id", "S[0-9]{1,2}")]


def test_comments_and_blanks_ignored():
    pack = parse_pack("# a comment\n\n   \nstudent-id  S[0-9]\n# trailing\n")
    assert pack.patterns == [("student-id", "S[0-9]")]
    assert pack.ok


def test_regex_may_contain_spaces():
    # Only the first whitespace run splits kind from regex.
    pack = parse_pack("phone  \\+[0-9]{3} [0-9]{3} [0-9]{4}")
    assert pack.patterns == [("phone", "\\+[0-9]{3} [0-9]{3} [0-9]{4}")]


def test_bad_regex_becomes_error_not_crash():
    pack = parse_pack("broken  [unterminated\nok  fine\n")
    assert pack.patterns == [("ok", "fine")]  # the good rule still loads
    assert not pack.ok
    assert any("invalid regex" in e for e in pack.errors)


def test_malformed_line_becomes_error():
    pack = parse_pack("justakind\n")  # no regex
    assert pack.patterns == []
    assert any("expected" in e for e in pack.errors)


def test_load_missing_file_is_empty_pack(tmp_path):
    pack = load_pattern_pack(tmp_path)
    assert pack.patterns == []
    assert pack.ok


def test_load_reads_shelf_file(tmp_path):
    (tmp_path / "POLICY.patterns").write_text("nick  @[a-z]+\n", encoding="utf-8")
    pack = load_pattern_pack(tmp_path)
    assert pack.patterns == [("nick", "@[a-z]+")]
