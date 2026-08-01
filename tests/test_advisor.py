"""The context advisor (#14) — proposals, and the honesty of the arithmetic.

Two properties carry most of these tests. First, the advisor never proposes
something that costs more than it saves: shelving adds a digest and an INDEX
line to every later session, and the numbers it reports are net of that.
Second, it never proposes dropping content it could not find on the shelf —
that is the one failure mode here that destroys work, and it is exactly the
check a self-assessing model cannot perform on itself.
"""

import json
import shutil
import subprocess
from datetime import date, timedelta

import pytest

pytest.importorskip("docshelf_mcp")

from docshelf_mcp.core.shelf import Shelf  # noqa: E402

from memshelf_mcp.cli import main  # noqa: E402
from memshelf_mcp.core.advisor import (  # noqa: E402
    EPISODE_STANDING_COST,
    MIN_PROPOSAL_TOKENS,
    Occupant,
    advise,
)
from memshelf_mcp.core.doctor import INDEX_BUDGET_TOKENS  # noqa: E402
from memshelf_mcp.core.rebuild import rebuild  # noqa: E402
from memshelf_mcp.core.shelve import shelve  # noqa: E402

DIGEST = (
    "The auth refactor moved token checks into middleware; the decided approach "
    "is JWT with a shared secret. The cookie-session alternative was rejected "
    "for cross-service calls. Open: rotating the shared secret."
)


def _init(root):
    Shelf(root).init(name="t", default_categories=["topics", "research", "sessions"])
    subprocess.run(["git", "-C", str(root), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.email", "t@t.test"], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.name", "t"], check=True)
    return root


def _shelve(root, slug, *, title=None, description=None, tokens=30_000):
    shelve(
        root,
        slug=slug,
        kind="topic",
        digest=DIGEST,
        sections={"Decisions": "Recorded."},
        display_title=title,
        description=description,
        approx_tokens=tokens,
        date=slug[:10],
    )


def _actions(advice):
    return [(p.action, p.target) for p in advice.proposals]


def test_no_occupants_reports_the_shelf_and_says_the_window_is_missing(tmp_path):
    """Silence about the window must not be reported as an empty window."""
    root = _init(tmp_path / "shelf")
    _shelve(root, "2026-07-01-auth")

    advice = advise(root)

    assert advice.episodes_on_shelf == 1
    assert advice.memory_overhead > 0  # the shelf's own standing cost, measured
    assert any("no window breakdown supplied" in note for note in advice.notes)
    assert not [p for p in advice.proposals if p.action in ("shelve", "drop")]


def test_closed_topic_is_proposed_net_of_what_shelving_costs(tmp_path):
    root = _init(tmp_path / "shelf")

    advice = advise(
        root,
        occupants=[Occupant(label="auth refactor", approx_tokens=30_000, state="closed")],
    )

    (proposal,) = advice.proposals
    assert proposal.action == "shelve"
    # Not 30_000: the digest and INDEX line ride in every later session.
    assert proposal.tokens == 30_000 - EPISODE_STANDING_COST
    assert advice.breakdown["reclaimable"] == 30_000


def test_live_topic_is_never_proposed(tmp_path):
    root = _init(tmp_path / "shelf")

    advice = advise(
        root, occupants=[Occupant(label="current work", approx_tokens=50_000, state="live")]
    )

    assert advice.proposals == []
    assert advice.breakdown["live"] == 50_000


def test_unstated_state_counts_as_live_and_the_report_says_so(tmp_path):
    """Conservative by design: an advisor that over-proposes gets ignored."""
    root = _init(tmp_path / "shelf")

    advice = advise(root, occupants=[Occupant(label="something", approx_tokens=40_000)])

    assert advice.proposals == []
    assert advice.breakdown["live"] == 40_000
    assert any("neither state nor idleness" in note for note in advice.notes)


def test_idle_turns_make_an_unstated_occupant_stale(tmp_path):
    root = _init(tmp_path / "shelf")

    advice = advise(
        root,
        occupants=[Occupant(label="dead topic", approx_tokens=30_000, idle_turns=12)],
        stale_after_turns=10,
    )

    (proposal,) = advice.proposals
    assert proposal.action == "shelve"
    assert "untouched for 12 turns" in proposal.why


def test_small_topics_are_not_worth_an_episode(tmp_path):
    """Below the floor, the standing cost of the digest eats the saving."""
    root = _init(tmp_path / "shelf")

    advice = advise(
        root,
        occupants=[
            Occupant(label="tiny", approx_tokens=MIN_PROPOSAL_TOKENS - 1, state="closed"),
            Occupant(label="big enough", approx_tokens=MIN_PROPOSAL_TOKENS, state="closed"),
        ],
    )

    assert _actions(advice) == [("shelve", "big enough")]
    assert any("not worth an episode" in note for note in advice.notes)


def test_instructions_are_static_overhead_not_a_shelve_candidate(tmp_path):
    root = _init(tmp_path / "shelf")

    advice = advise(
        root,
        occupants=[
            Occupant(
                label="CLAUDE.md stack", approx_tokens=12_000, state="closed", kind="instructions"
            )
        ],
    )

    assert advice.proposals == []
    assert advice.breakdown["static"] == 12_000


def test_already_shelved_content_is_proposed_for_dropping_not_reshelving(tmp_path):
    root = _init(tmp_path / "shelf")
    _shelve(root, "2026-07-01-auth")

    advice = advise(
        root,
        occupants=[
            Occupant(
                label="auth refactor",
                approx_tokens=30_000,
                state="live",  # even "live" — durability, not topic state, decides
                episode_id="2026-07-01-auth",
            )
        ],
    )

    (proposal,) = advice.proposals
    assert proposal.action == "drop"
    assert proposal.tokens == 30_000  # nothing added: the episode already exists
    assert "memshelf recall" in proposal.command


def test_a_false_shelved_claim_is_refused_loudly(tmp_path):
    """The one input that destroys work if trusted: 'this is already saved'."""
    root = _init(tmp_path / "shelf")
    _shelve(root, "2026-07-01-auth")

    advice = advise(
        root,
        occupants=[
            Occupant(
                label="research dump",
                approx_tokens=30_000,
                state="closed",
                episode_id="2026-07-01-does-not-exist",
            )
        ],
    )

    (proposal,) = advice.proposals
    assert proposal.action == "shelve"  # NOT drop
    assert any("Dropping it would lose it" in note for note in advice.notes)


def test_a_title_that_looks_shelved_is_a_hint_never_an_auto_drop(tmp_path):
    root = _init(tmp_path / "shelf")
    _shelve(root, "2026-07-01-auth", title="Auth refactor")

    advice = advise(
        root,
        occupants=[Occupant(label="Auth refactor", approx_tokens=30_000, state="closed")],
    )

    (proposal,) = advice.proposals
    assert proposal.action == "shelve"
    assert any("pass episode_id to confirm" in note for note in advice.notes)


def test_archived_episodes_still_count_as_shelved(tmp_path):
    """A rolled-up episode is recallable by id, so carrying it is still waste."""
    from memshelf_mcp.core.archive import rollup

    root = _init(tmp_path / "shelf")
    _shelve(root, "2026-01-05-old")
    _shelve(root, "2026-07-20-new")
    rollup(root, slug="2026-07-21-rollup", digest=DIGEST, until="2026-01-31")

    advice = advise(
        root,
        occupants=[Occupant(label="old work", approx_tokens=30_000, episode_id="2026-01-05-old")],
    )

    assert _actions(advice) == [("drop", "old work")]


@pytest.fixture(scope="module")
def bloated_shelf(tmp_path_factory):
    """A shelf whose INDEX is over budget — 60 episodes, the size the author's
    working shelf reached when the `index-bloat` warning went live."""
    root = _init(tmp_path_factory.mktemp("bloated") / "shelf")
    for n in range(60):
        day = date(2026, 1, 1) + timedelta(days=n)
        _shelve(
            root,
            f"{day.isoformat()}-topic-{n}",
            title=f"Разбор подсистемы авторизации, часть {n}",
            description=(
                "Что решили по middleware и общему секрету, что отвергли "
                f"и почему, что осталось открытым после итерации {n}."
            ),
        )
    rebuild(root)
    return root


def test_index_over_budget_proposes_a_rollup_with_a_concrete_until(bloated_shelf):
    advice = advise(bloated_shelf)

    (proposal,) = [p for p in advice.proposals if p.action == "rollup"]
    assert advice.index_tokens > INDEX_BUDGET_TOKENS
    assert "--until 2026-0" in proposal.command
    assert "--digest" in proposal.command  # the synthesis stays the caller's
    assert proposal.tokens > 0


def test_the_rollup_it_proposes_actually_gets_index_under_budget(bloated_shelf, tmp_path):
    """The estimate has to survive being executed, not just look plausible."""
    from memshelf_mcp.core.archive import rollup

    root = tmp_path / "shelf"
    shutil.copytree(bloated_shelf, root)
    before = advise(root)
    (proposal,) = [p for p in before.proposals if p.action == "rollup"]
    until = proposal.command.split("--until ")[1].split()[0]

    rollup(root, slug=f"{until}-rollup", digest=DIGEST, until=until)

    after = advise(root)
    assert after.index_tokens <= INDEX_BUDGET_TOKENS
    # The claimed reclaim is the reason to accept the proposal, so it has to be
    # close to what actually happened — within a fifth, not merely the right sign.
    actual = before.index_tokens - after.index_tokens
    assert abs(actual - proposal.tokens) <= proposal.tokens // 5
    assert [p for p in after.proposals if p.action == "rollup"] == []


def test_a_pre_adopt_shelf_is_warned_before_it_is_told_to_roll_up(bloated_shelf, tmp_path):
    """`rollup` regenerates .meta.json from the episodes; on a shelf written
    before #58 the titles live only in .meta.json and would be stripped."""
    root = tmp_path / "shelf"
    shutil.copytree(bloated_shelf, root)
    for episode in root.glob("docs/*/*.md"):
        episode.write_text(
            "\n".join(
                line
                for line in episode.read_text(encoding="utf-8").splitlines()
                if not line.startswith("display_title:")
            )
            + "\n",
            encoding="utf-8",
        )

    advice = advise(root)

    assert [p for p in advice.proposals if p.action == "rollup"]
    assert any("predates #58" in note and "--adopt" in note for note in advice.notes)


def test_an_adopted_shelf_is_not_warned(bloated_shelf):
    advice = advise(bloated_shelf)

    assert [p for p in advice.proposals if p.action == "rollup"]
    assert not any("predates #58" in note for note in advice.notes)


def test_no_rollup_proposal_while_index_fits(tmp_path):
    root = _init(tmp_path / "shelf")
    _shelve(root, "2026-07-01-auth")

    advice = advise(root)

    assert [p for p in advice.proposals if p.action == "rollup"] == []


def test_proposals_are_ranked_and_deterministic(tmp_path):
    root = _init(tmp_path / "shelf")
    occupants = [
        Occupant(label="small", approx_tokens=5_000, state="closed"),
        Occupant(label="huge", approx_tokens=90_000, state="closed"),
        Occupant(label="medium", approx_tokens=20_000, state="closed"),
    ]

    first = advise(root, occupants=occupants)
    second = advise(root, occupants=list(reversed(occupants)))

    assert [p.target for p in first.proposals] == ["huge", "medium", "small"]
    assert first.as_dict() == second.as_dict()


def test_budget_arithmetic_counts_memshelf_itself(tmp_path):
    root = _init(tmp_path / "shelf")
    _shelve(root, "2026-07-01-auth")
    occupants = [Occupant(label="work", approx_tokens=10_000, state="live")]

    with_self = advise(root, occupants=occupants, budget_tokens=50_000)
    without_self = advise(
        root, occupants=occupants, budget_tokens=50_000, include_memory_overhead=False
    )

    assert with_self.memory_overhead > 0
    assert with_self.accounted_tokens == 10_000 + with_self.memory_overhead
    assert with_self.headroom_tokens == 50_000 - with_self.accounted_tokens
    assert without_self.accounted_tokens == 10_000


def test_over_budget_headroom_goes_negative(tmp_path):
    root = _init(tmp_path / "shelf")

    advice = advise(
        root,
        occupants=[Occupant(label="everything", approx_tokens=180_000, state="live")],
        budget_tokens=100_000,
    )

    assert advice.headroom_tokens < 0


def test_unknown_state_is_rejected_rather_than_guessed(tmp_path):
    root = _init(tmp_path / "shelf")

    with pytest.raises(ValueError, match="unknown state"):
        advise(root, occupants=[Occupant(label="x", approx_tokens=1, state="maybe")])


def test_cli_advise_parses_the_compact_occupant_form(tmp_path, capsys):
    root = _init(tmp_path / "shelf")
    _shelve(root, "2026-07-01-auth")

    code = main(
        [
            "advise",
            "--shelf",
            str(root),
            "--occupant",
            "auth refactor=30000,closed",
            "--occupant",
            "old dump=40000,idle=20",
            "--occupant",
            "carried=15000,live,episode=2026-07-01-auth",
        ]
    )

    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert {(p["action"], p["target"]) for p in payload["proposals"]} == {
        ("shelve", "auth refactor"),
        ("shelve", "old dump"),
        ("drop", "carried"),
    }


def test_cli_advise_summary_is_one_line(tmp_path, capsys):
    root = _init(tmp_path / "shelf")

    code = main(["advise", "--shelf", str(root), "--summary", "--occupant", "dead=30000,closed"])

    assert code == 0
    out = capsys.readouterr().out.strip()
    assert "\n" not in out
    assert "reclaimable" in out


def test_cli_advise_rejects_a_malformed_occupant(tmp_path):
    root = _init(tmp_path / "shelf")

    with pytest.raises(SystemExit, match="not a token count"):
        main(["advise", "--shelf", str(root), "--occupant", "broken=lots"])
    with pytest.raises(SystemExit, match="unknown attribute"):
        main(["advise", "--shelf", str(root), "--occupant", "x=1000,colour=red"])


def test_cli_advise_reads_occupants_from_json(tmp_path, capsys):
    root = _init(tmp_path / "shelf")
    spec = tmp_path / "occupants.json"
    spec.write_text(
        json.dumps([{"label": "dump", "approx_tokens": 25_000, "state": "closed"}]),
        encoding="utf-8",
    )

    code = main(["advise", "--shelf", str(root), "--occupants-json", str(spec)])

    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["proposals"][0]["target"] == "dump"


def test_advise_writes_nothing(tmp_path):
    """Proposals, never actions — asserted against the shelf, not the docs."""
    root = _init(tmp_path / "shelf")
    _shelve(root, "2026-07-01-auth")
    before = {p: p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}

    advise(root, occupants=[Occupant(label="dead", approx_tokens=30_000, state="closed")])

    after = {p: p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}
    assert after == before
