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
# text. The helpers pair runs the way CommonMark does (by equal length; these
# inputs carry no backslash escapes), and are written apart from the code
# under test.

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


def _code_spans_of(line):
    """``(start, end)`` of each code span in ``line``; unpaired runs stay text."""
    runs = [(m.start(), m.end()) for m in re.finditer(r"`+", line)]
    spans, i = [], 0
    while i < len(runs):
        start, end = runs[i]
        closer = next(
            (j for j in range(i + 1, len(runs)) if runs[j][1] - runs[j][0] == end - start),
            None,
        )
        if closer is None:
            i += 1
            continue
        spans.append((start, runs[closer][1]))
        i = closer + 1
    return spans


def _file_name_renders_as_code(description):
    """Whether the INDEX line built around ``description`` ends in the file name
    as a code span of its own."""
    line = f"- **2026-10-07-probe-span** — {description} — `{FILE_NAME}`"
    spans = _code_spans_of(line)
    return bool(spans) and line[spans[-1][0] : spans[-1][1]] == f"`{FILE_NAME}`"


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
        ("see ``a`b…", "see ``a`b``…"),
        # The closer must not touch a backtick, or the two runs merge into one.
        ("see ``a`…", "see ``a` ``…"),
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
