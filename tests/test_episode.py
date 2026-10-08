import re

import pytest

from memshelf_mcp.core.episode import (
    MAX_DESCRIPTION_CHARS,
    EpisodeError,
    Frontmatter,
    clamp_description,
    compose_episode,
    yaml_scalar,
)


def _fm(kind="topic", **kw):
    return Frontmatter(id="2026-07-22-x", kind=kind, **kw)


def test_compose_is_h1_first_with_frontmatter_and_digest():
    md = compose_episode(
        _fm(tags=("a", "b"), approx_tokens=100), "A decided thing.", {"Decisions": "d"}
    )
    lines = md.splitlines()
    assert lines[0] == "# 2026-07-22-x"
    assert lines[2] == "---"
    assert "id: 2026-07-22-x" in md
    assert "tags: [a, b]" in md
    assert "## Digest" in md and "## Decisions" in md


def test_sections_are_ordered_and_empty_ones_omitted():
    md = compose_episode(
        _fm(),
        "A decided thing.",
        {"Raw excerpts": "raw", "Decisions": "d", "Artifacts": "", "Timeline": "t"},
    )
    # canonical order: Decisions, Timeline, Artifacts(omitted), Raw excerpts
    assert md.index("## Decisions") < md.index("## Timeline") < md.index("## Raw excerpts")
    assert "## Artifacts" not in md


def test_topic_requires_decisions():
    with pytest.raises(EpisodeError):
        compose_episode(_fm("topic"), "digest only", {})


def test_session_requires_timeline_and_open_threads():
    with pytest.raises(EpisodeError):
        compose_episode(_fm("session"), "digest", {"Timeline": "t"})  # missing Open threads


def test_research_requires_one_body_section():
    with pytest.raises(EpisodeError):
        compose_episode(_fm("research"), "digest only", {})
    ok = compose_episode(_fm("research"), "digest", {"Findings": "f"})
    assert "## Findings" in ok


def test_unknown_kind_rejected():
    with pytest.raises(EpisodeError):
        compose_episode(_fm("journal"), "digest", {"Decisions": "d"})


def test_missing_digest_rejected():
    with pytest.raises(EpisodeError):
        compose_episode(_fm("topic"), "   ", {"Decisions": "d"})


# --- an amend keeps the order of the episode it rewrites (#205) -------------


def _headings(md):
    return re.findall(r"^## (.+)$", md, re.MULTILINE)


def test_order_keeps_the_sections_an_episode_has_where_they_are():
    md = compose_episode(
        _fm("session"),
        "A decided thing.",
        {"Decisions": "d", "Timeline": "t", "Open threads": "o", "Findings": "f", "Notes": "n"},
        order=["Digest", "Decisions", "Timeline", "Findings", "Open threads"],
    )
    # Notes is new, and every section there ranks before it.
    assert _headings(md) == ["Digest", "Decisions", "Timeline", "Findings", "Open threads", "Notes"]


def test_a_canonical_order_composes_as_a_new_episode_would():
    """An episode the tool wrote is in canonical order already, so a known
    section an amend adds goes where a new episode has it, not to the end."""
    sections = {"Decisions": "d", "Timeline": "t", "Artifacts": "a", "Open threads": "o"}
    fresh = compose_episode(_fm("session"), "A decided thing.", sections)

    amended = compose_episode(
        _fm("session"),
        "A decided thing.",
        sections,
        order=["Digest", "Decisions", "Timeline", "Open threads"],
    )

    assert amended == fresh
    assert _headings(amended) == ["Digest", "Decisions", "Timeline", "Artifacts", "Open threads"]


@pytest.mark.parametrize(
    "sections,stored,expected",
    [
        # A section the canonical order does not know ranks after every known
        # one: inserted before the first section ranked after it, Artifacts
        # went above Context, to the top.
        (
            ["Context", "Decisions", "Timeline", "Open threads", "Artifacts"],
            ["Digest", "Context", "Decisions", "Timeline", "Open threads"],
            ["Digest", "Context", "Decisions", "Timeline", "Artifacts", "Open threads"],
        ),
        # The issue's layout, Findings moved above Open threads: Raw excerpts
        # went above Findings.
        (
            ["Decisions", "Timeline", "Findings", "Open threads", "Raw excerpts"],
            ["Digest", "Decisions", "Timeline", "Findings", "Open threads"],
            ["Digest", "Decisions", "Timeline", "Findings", "Open threads", "Raw excerpts"],
        ),
    ],
    ids=["context-first", "issue-layout"],
)
def test_a_new_section_goes_after_the_last_one_ranked_before_it(sections, stored, expected):
    md = compose_episode(
        _fm("session"),
        "A decided thing.",
        {name: name.lower() for name in sections},
        order=stored,
    )
    assert _headings(md) == expected


def test_a_heading_the_file_repeats_is_written_once_at_its_first_place():
    """A hand-edit can leave a heading twice; the section is one entry of the
    call, so it is written once, where the file has it first."""
    md = compose_episode(
        _fm("session"),
        "A decided thing.",
        {"Decisions": "d", "Timeline": "t", "Open threads": "o"},
        order=["Digest", "Timeline", "Decisions", "Open threads", "Timeline"],
    )
    assert _headings(md) == ["Digest", "Timeline", "Decisions", "Open threads"]


def test_order_drops_what_was_not_passed_and_keeps_the_rest_in_place():
    md = compose_episode(
        _fm("session"),
        "A decided thing.",
        {"Timeline": "t", "Open threads": "o", "Findings": "f", "Artifacts": ""},
        order=["Findings", "Decisions", "Open threads", "Artifacts", "Timeline"],
    )
    assert _headings(md) == ["Digest", "Findings", "Open threads", "Timeline"]


@pytest.mark.parametrize(
    "stored,written",
    [
        ("Long  description ", '"Long  description "'),
        (" leading", '" leading"'),
        ("two\nlines", '"two lines"'),
        ("a\ttab", '"a tab"'),
    ],
)
def test_description_keeps_its_spaces_and_flattens_other_whitespace(stored, written):
    """An amend keeps the stored description as it is (#205), a trailing or
    doubled space included. Other whitespace is flattened as before: a line
    break would end the field."""
    from memshelf_mcp.core.frontmatter import parse_frontmatter

    md = compose_episode(_fm(description=stored), "A decided thing.", {"Decisions": "d"})

    assert f"description: {written}" in md.splitlines()
    assert parse_frontmatter(md)[0]["description"] == written[1:-1]


# --- frontmatter must be valid YAML, not just parseable by us ---------------


def test_title_with_a_colon_survives_a_round_trip():
    """The defect this guards: `display_title: Охота: Грузия` parses fine with
    memshelf's own splitter and is a YAML syntax error for a real loader —
    shelf-spec's validator then reports the episode as having NO frontmatter."""
    from memshelf_mcp.core.frontmatter import parse_frontmatter

    fm = Frontmatter(
        id="2026-07-31-x",
        kind="topic",
        span="2026-07-31",
        display_title="Охота на X1 Carbon: Грузия/Армения",
        description="Один конфиг: сербский, ~2910 €",
        notes="chat-2: fragment",
    )
    text = compose_episode(fm, "Digest text.", {"Decisions": "X"})
    fields, _ = parse_frontmatter(text)
    assert fields["display_title"] == "Охота на X1 Carbon: Грузия/Армения"
    assert fields["description"] == "Один конфиг: сербский, ~2910 €"
    assert fields["notes"] == "chat-2: fragment"


def test_frontmatter_block_loads_as_yaml():
    """Belt and braces: parse the block with a real YAML loader, the way the
    shelves' advisory validator does."""
    yaml = pytest.importorskip("yaml")

    fm = Frontmatter(
        id="2026-07-31-x",
        kind="topic",
        span="2026-07-31",
        display_title='Заголовок с "кавычками" и: двоеточием',
        notes="a\\backslash",
    )
    text = compose_episode(fm, "Digest text.", {"Decisions": "X"})
    block = text.split("---\n")[1]
    loaded = yaml.safe_load(block)
    assert loaded["display_title"] == 'Заголовок с "кавычками" и: двоеточием'
    assert loaded["notes"] == "a\\backslash"
    assert loaded["id"] == "2026-07-31-x"


# --- clamp_description: the degenerate inputs found in review ---------------


@pytest.mark.parametrize(
    "text",
    [
        "- " + "х" * 200,  # no space after the first two chars
        "Итог: " + "y" * 200,  # one space, very early
        "See https://example.com/" + "a" * 200 + " end",  # a long unbroken URL
        "а" * 200,  # no space at all
        " " * 119 + "слово" * 40,  # only leading spaces in the head
        "хвост, " + "z" * 200,  # head would `rstrip` down to nothing
    ],
)
def test_clamp_keeps_most_of_the_budget_whatever_the_input(text):
    """A word-boundary cut must not collapse the value.

    Cutting at the *last* space in the head unconditionally turned
    "Итог: yyyy…" into "Итог…" and could `rstrip` a head down to a bare
    ellipsis — a description destroyed in the name of tidiness, silently, on
    every rebuild. Found in review; the cut now falls back to a hard one when
    the clean boundary would keep too little.
    """
    kept, warning = clamp_description(text)

    assert warning is not None
    assert len(kept) <= MAX_DESCRIPTION_CHARS
    assert len(kept) >= MAX_DESCRIPTION_CHARS * 2 // 3
    assert kept.endswith("…")
    assert kept.strip("… ")  # never a bare ellipsis


def test_clamp_reports_the_length_it_actually_produced():
    """The warning said "cut to 120" while returning a single character."""
    kept, warning = clamp_description("Итог: " + "y" * 200)

    assert f"cut to {len(kept)}" in warning


@pytest.mark.parametrize(
    "text,expected",
    [
        (None, ""),
        ("", ""),
        ("   ", ""),
        ("x" * MAX_DESCRIPTION_CHARS, "x" * MAX_DESCRIPTION_CHARS),
    ],
)
def test_clamp_leaves_anything_within_budget_untouched(text, expected):
    kept, warning = clamp_description(text)
    assert (kept, warning) == (expected, None)


def test_clamp_survives_the_frontmatter_round_trip():
    """The clamped value is written into `description:`, so it has to come back
    out of the parser as the same string."""
    from memshelf_mcp.core.frontmatter import parse_frontmatter

    kept, _ = clamp_description('Он сказал "да": и вот # что {из} [этого] — ' + "ы" * 200)
    body = (
        f"# t\n\n---\nid: t\nkind: topic\nspan: 2026-08-21\ndescription: {yaml_scalar(kept)}\n---\n"
    )
    fields, _ = parse_frontmatter(body)

    assert fields["description"] == kept


# --- clamp_description: code spans (#190) ------------------------------------
#
# INDEX prints the episode's file name in backticks right after the
# description, so a run the description leaves open pairs with the file name's
# backtick: the span swallows the separator and the file name renders as plain
# text. The helpers pair runs the way CommonMark does (by equal length, a
# backslash escaping outside a span only; these inputs carry no autolinks or
# HTML), and are written apart from the code under test.

FILE_NAME = "2026-10-07-probe-span.md"

ISSUE_190 = (
    "Merged nineteen pull requests in one evening and cleaned up the branch list "
    "afterwards with `gh pr merge --squash --delete-branch`"
)

#: What the cap before #190 stored for ISSUE_190 — the text already on disk.
CUT_INSIDE_A_SPAN = (
    "Merged nineteen pull requests in one evening and cleaned up the branch list "
    "afterwards with `gh pr merge --squash…"
)


def _runs_of(line):
    """``(start, end)`` of each backtick run that opens in ``line``: ``end`` is
    where the span it opens ends, or None when nothing closes it."""
    runs, i = [], 0
    while i < len(line):
        if line[i] == "\\":
            i += 2
        elif line[i] == "`":
            run = re.match(r"`+", line[i:]).group()
            closer = re.compile(rf"(?<!`){run}(?!`)").search(line, i + len(run))
            runs.append((i, closer.end() if closer else None))
            i = closer.end() if closer else i + len(run)
        else:
            i += 1
    return runs


def _code_spans_of(line):
    """``(start, end)`` of each code span in ``line``."""
    return [(start, end) for start, end in _runs_of(line) if end is not None]


def _file_name_renders_as_code(description):
    """Whether the INDEX line built around ``description`` ends in the file name
    as a code span of its own, in CommonMark and on GitHub. GitHub's renderer
    loses it as well when another span follows a run nothing closes."""
    line = f"- **2026-10-07-probe-span** — {description} — `{FILE_NAME}`"
    runs = _runs_of(line)
    spans = [start for start, end in runs if end is not None]
    first_open = next((start for start, end in runs if end is None), len(line))
    return (
        bool(spans)
        and line[spans[-1] :].startswith(f"`{FILE_NAME}`")
        and not any(first_open < start for start in spans[:-1])
    )


def test_the_issue_line_was_broken_before_the_fix():
    """The helper itself: it calls the INDEX line of #190 broken."""
    assert not _file_name_renders_as_code(CUT_INSIDE_A_SPAN)
    assert _file_name_renders_as_code("a description with `code` in it")


def test_clamp_moves_a_cut_out_of_a_code_span():
    kept, warning = clamp_description(ISSUE_190)

    assert kept == (
        "Merged nineteen pull requests in one evening and cleaned up the branch list "
        "afterwards with…"
    )
    assert warning is not None
    assert _file_name_renders_as_code(kept)


def test_clamp_keeps_a_span_that_ends_exactly_at_the_cut():
    text = "a" * 95 + " `gh pr list` " + "x" * 30
    kept, _ = clamp_description(text)

    assert kept == "a" * 95 + " `gh pr list`…"
    assert _file_name_renders_as_code(kept)


def test_clamp_pairs_backtick_runs_by_length_not_by_count():
    """A double-backtick span may hold a single backtick. Counting backticks for
    parity calls this span open after the cut and closes it wrongly."""
    text = "p" * 100 + " ``a`b c d e f g h i j`` tail"
    kept, _ = clamp_description(text)

    assert kept == "p" * 100 + "…"
    assert _file_name_renders_as_code(kept)


def test_clamp_closes_a_span_it_cannot_cut_before():
    """Cutting before a span that opens the description would keep less than
    the floor, so the span is closed before the ellipsis instead."""
    kept, _ = clamp_description("`" + "q" * 150 + "` tail")

    assert kept == "`" + "q" * 116 + "`…"
    assert len(kept) <= MAX_DESCRIPTION_CHARS
    assert _file_name_renders_as_code(kept)


def test_clamp_does_not_close_a_span_on_a_run_the_cut_split():
    """Found by fuzzing against markdown-it-py. The span's content holds a
    double backtick, and the cut keeps one of the two: a run as long as the
    opener, which closes the span early and leaves the added closer to pair
    with the file name's backtick."""
    kept, _ = clamp_description("`" + "q" * 115 + "``" + "q" * 30 + "`")

    assert kept == "`" + "q" * 115 + "`…"
    assert _file_name_renders_as_code(kept)


@pytest.mark.parametrize(
    "on_disk,expected",
    [
        (CUT_INSIDE_A_SPAN, CUT_INSIDE_A_SPAN[:-1] + "`…"),
        # The run of two is literal; the single backtick after it is the open
        # one, and the span that closes it follows the run of two, which is
        # escaped for GitHub's sake (see the next test).
        ("see ``a`b…", "see \\`\\`a`b`…"),
        ("see ``a`…", "see ``a…"),
        # The closer must not touch a backtick, or the two runs merge into one.
        ("a ` b ``", "a ` b `` `"),
        ("press the ` key", "press the ` key`"),
        ("nothing after it `", "nothing after it"),
    ],
)
def test_clamp_balances_an_unpaired_run_within_the_cap(on_disk, expected):
    """Descriptions an earlier cap cut inside a span are on disk already, within
    the cap; the render path has to repair them without an edit."""
    kept, warning = clamp_description(on_disk)

    assert kept == expected
    assert warning is not None and "unpaired" in warning
    assert _file_name_renders_as_code(kept)
    # A fixed point: rebuild clamps again what shelve already clamped.
    assert clamp_description(kept) == (kept, None)


@pytest.mark.parametrize(
    "description",
    [
        # CommonMark gives the backtick to the autolink or the tag that starts
        # first, so it delimits nothing.
        "Spec lives at <https://example.com/a`b> and nowhere else",
        'Tooltip <abbr title="`">bt</abbr> marks the key',
        # A run of two nothing closes stays literal, and the file name's single
        # backtick cannot close it either; no span follows it.
        "Ends with a double run ``",
        "a `x` span, then a literal ``",
    ],
)
def test_clamp_leaves_alone_a_value_that_renders_right(description):
    """Found in review with markdown-it-py: each of these rendered the file name
    as code before #190, and the first version of the repair broke the line
    (the autolink and the tag) or dropped a literal run. Within the cap, the
    value before #190 was the value itself."""
    assert clamp_description(description) == (description, None)


@pytest.mark.parametrize(
    "description",
    [
        "Use `` around a literal backtick; the `x` span stays code",
        "a `` b `c` d",
    ],
)
def test_clamp_escapes_an_unpaired_run_a_code_span_follows(description):
    """The run of two is literal in CommonMark, and markdown-it-py renders the
    file name as code. GitHub's renderer does not: checked with its
    ``POST /markdown`` on 2026-10-08, the INDEX line of each value as written
    ends in a plain-text file name, and with the run escaped in code. An
    escaped backtick renders as the literal run did."""
    kept, warning = clamp_description(description)

    assert kept == description.replace("``", "\\`\\`", 1)
    assert warning is not None and "unpaired run" in warning
    assert not _file_name_renders_as_code(description)
    assert _file_name_renders_as_code(kept)
    assert clamp_description(kept) == (kept, None)


def test_clamp_reads_an_autolink_before_the_cut_as_one_piece():
    text = "See <https://example.com/a`b> for " + "word " * 25
    kept, warning = clamp_description(text)

    assert kept.startswith("See <https://example.com/a`b> for word")
    assert kept.endswith("word…")
    assert "cut to" in warning
    assert clamp_description(kept) == (kept, None)


def test_clamp_cut_through_an_autolink_does_not_free_its_backtick():
    """The cut leaves the autolink without its `>`, so it is no autolink any
    more and its backtick is a run: the cut moves before that backtick."""
    text = "x" * 70 + " <https://example.com/a`b" + "c" * 60 + ">"
    kept, _ = clamp_description(text)

    assert kept == "x" * 70 + " <https://example.com/a…"
    assert _file_name_renders_as_code(kept)
    assert clamp_description(kept) == (kept, None)


def test_rebuild_repairs_a_description_cut_inside_a_span_without_an_edit(tmp_path):
    pytest.importorskip("docshelf_mcp")
    from docshelf_mcp.core.shelf import Shelf

    from memshelf_mcp.core.rebuild import rebuild

    Shelf(tmp_path).init(name="probe", default_categories=["topics", "research", "sessions"])
    episode = tmp_path / "docs" / "topics" / FILE_NAME
    episode.write_text(
        compose_episode(
            Frontmatter(
                id="2026-10-07-probe-span",
                kind="topic",
                span="2026-10-07",
                date="2026-10-07",
                description=CUT_INSIDE_A_SPAN,
            ),
            "The probe episode checks how the INDEX line renders a description cut "
            "inside a code span.",
            {"Decisions": "none"},
        ),
        encoding="utf-8",
    )
    before = episode.read_bytes()

    rebuild(tmp_path)

    line = next(
        line
        for line in (tmp_path / "INDEX.md").read_text(encoding="utf-8").splitlines()
        if "probe-span" in line
    )
    assert line.endswith(f" — `{FILE_NAME}`"), line
    spans = _code_spans_of(line)
    assert line[spans[-1][0] : spans[-1][1]] == f"`{FILE_NAME}`", line
    assert "`gh pr merge --squash`…" in line, line
    assert episode.read_bytes() == before
