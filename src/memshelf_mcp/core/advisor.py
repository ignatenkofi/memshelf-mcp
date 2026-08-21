"""The context advisor — "where did my window go?" (#14).

MANIFEST hero scenario 2, and the annoyance the project was founded on: a dead
topic still occupying 30K tokens forty minutes after it closed. This module
answers two questions — *what is my context made of* and *what can I put down*
— and answers the second as **proposals**. Nothing here writes anything.

**Why the caller supplies the breakdown.** A library cannot see the window it
is being asked about. Parsing a host's ``/context`` output would work on
exactly one host and rot with its next release (ARCHITECTURE open question 7),
so the division of labour is the one ``shelve`` and ``rollup`` already use: the
model reports what only the model knows — which topics are in play, which are
closed, roughly how big they are — and the tool does what a prompt cannot:

- **Measure its own overhead.** ``INDEX.md`` plus the standing digests are what
  memshelf itself takes out of every window. An advisor that hides its own cost
  is selling something.
- **Verify against the shelf.** An occupant the caller *believes* is already
  shelved is checked against the actual episodes. If the episode is not there,
  the advisor says so instead of proposing a drop — that is the one failure in
  this feature that silently destroys work.
- **Keep the arithmetic honest.** Shelving reclaims a topic's mass *minus* the
  standing cost it adds — a digest and an INDEX line, in every future session.
  Proposals report the net, and a topic too small to pay for its own digest is
  not proposed at all.
- **Be deterministic.** Same input, same proposals, same order. A heuristic
  that reshuffles between calls cannot be judged against the M2 exit criterion
  ("proposals accepted, not overridden, most of the time").

The advisor is also the first-run experience: called with no occupants at all
it still reports the shelf side — and says plainly that it was told nothing
about the window, rather than reporting a clean one.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

from memshelf_mcp.core.doctor import (
    INDEX_BASE_TOKENS,
    INDEX_TOKENS_PER_ENTRY,
    index_budget,
    index_entries,
)
from memshelf_mcp.core.rebuild import collect_episodes
from memshelf_mcp.core.stats import CHARS_PER_TOKEN

__all__ = [
    "DEFAULT_BUDGET_TOKENS",
    "EPISODE_STANDING_COST",
    "INDEX_CONTEXT_SHARE",
    "MIN_PROPOSAL_TOKENS",
    "STALE_AFTER_TURNS",
    "Advice",
    "Occupant",
    "Proposal",
    "advise",
]

#: A common context window, used only when the caller states no budget. It is
#: the caller's number, not a memshelf constant — hosts differ and change.
DEFAULT_BUDGET_TOKENS = 200_000

#: What one shelved episode costs in *every* later session: its digest (the
#: contract caps it at 120 words) plus one INDEX line. Shelving is not free,
#: and the advisor's arithmetic says so.
EPISODE_STANDING_COST = 200

#: Don't propose shelving below this. The trade has to be worth making: an
#: episode that reclaims 500 tokens once, in exchange for ~200 standing forever,
#: is a bad deal dressed up as hygiene. Ten-to-one is the floor.
MIN_PROPOSAL_TOKENS = 10 * EPISODE_STANDING_COST

#: Turns of silence after which an occupant of unstated state is called stale.
#: Idleness is the founding signal ("dead topic, 40 minutes"), but it is only a
#: signal: a stale occupant is *proposed*, never acted on.
STALE_AFTER_TURNS = 10

#: Share of the caller's stated context window that navigation may take before
#: folding a period is worth proposing.
#:
#: This is the rollup's trigger, and it deliberately is not ``doctor``'s
#: ``index-bloat``. The two ask different questions and only one of them has a
#: rollup for an answer. ``index-bloat`` asks whether a line is too *expensive*
#: — a formatting fault, which a rollup cannot fix, because folding entries
#: removes them and their per-entry allowance together and leaves the price of
#: the remaining lines exactly where it was. A rollup answers the other
#: question: navigation is priced correctly and there is simply a lot of it,
#: and it now costs enough of the window to be worth folding old periods into
#: a digest-of-digests.
#:
#: 3% is ~6K tokens of a 200K window, which at the ~80-token entry allowance is
#: about 75 episodes — near where ROADMAP M2 puts the exercise, and near where
#: this shelf's own owner has historically reached for a rollup (45 episodes
#: folded in July, 15 in the August pass). Expressed as a share rather than a
#: constant so it scales with the host, which is the mistake the fixed 2500
#: made in the other direction.
INDEX_CONTEXT_SHARE = 0.03

#: Occupant classes in the breakdown — ROADMAP M2's "static overhead vs live
#: topics vs stale dumps", plus memshelf's own footprint kept visible.
CLASS_STATIC = "static"
CLASS_MEMORY = "memory"
CLASS_LIVE = "live"
CLASS_RECLAIMABLE = "reclaimable"

_STATES = ("live", "closed", "unknown")
_KINDS = ("topic", "research", "tool-output", "instructions", "other")


@dataclass(frozen=True)
class Occupant:
    """One thing the caller reports as sitting in its context window.

    ``approx_tokens`` is an estimate by definition — the model is eyeballing
    its own history. Estimates are fine: every threshold here is an order of
    magnitude wide, and the report's job is to rank, not to audit.
    """

    label: str
    approx_tokens: int
    #: The model's judgement, and the only one it can make: is this still in play?
    state: str = "unknown"
    kind: str = "topic"
    #: Turns since this was last referenced, when the host can tell.
    idle_turns: int | None = None
    #: Set when the caller believes this content is already on the shelf. The
    #: advisor verifies the claim before proposing anything.
    episode_id: str | None = None


@dataclass
class Proposal:
    """One thing the caller *could* do. The advisor never does it."""

    action: str  # shelve | drop | rollup
    target: str
    tokens: int  # net tokens reclaimed
    why: str
    command: str

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class Advice:
    budget_tokens: int
    accounted_tokens: int
    headroom_tokens: int
    breakdown: dict[str, int]
    memory_overhead: int
    reclaimable_tokens: int
    episodes_on_shelf: int
    index_tokens: int
    #: What INDEX may cost at this shelf's size, and what one entry costs now.
    #: Reported next to the total because the total alone cannot be acted on: a
    #: big INDEX on a big shelf is navigation working as designed, while an
    #: expensive *entry* is a fault with a fix.
    index_budget_tokens: int = 0
    index_entry_tokens: int = 0
    proposals: list[Proposal] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def summary(self) -> str:
        line = (
            f"context: {_human(self.accounted_tokens)} accounted of "
            f"{_human(self.budget_tokens)} · memshelf itself {_human(self.memory_overhead)}"
        )
        if self.proposals:
            line += (
                f" · {_human(self.reclaimable_tokens)} reclaimable "
                f"in {len(self.proposals)} proposal(s)"
            )
        else:
            line += " · nothing to propose"
        return line

    def as_dict(self) -> dict:
        return {
            "budget_tokens": self.budget_tokens,
            "accounted_tokens": self.accounted_tokens,
            "headroom_tokens": self.headroom_tokens,
            "breakdown": self.breakdown,
            "memory_overhead": self.memory_overhead,
            "reclaimable_tokens": self.reclaimable_tokens,
            "episodes_on_shelf": self.episodes_on_shelf,
            "index_tokens": self.index_tokens,
            "index_budget_tokens": self.index_budget_tokens,
            "index_entry_tokens": self.index_entry_tokens,
            "proposals": [p.as_dict() for p in self.proposals],
            "notes": self.notes,
            "summary": self.summary,
        }


def _human(n: int) -> str:
    if abs(n) >= 1_000_000:
        return f"{n / 1_000_000:.2f}M".rstrip("0").rstrip(".")
    if abs(n) >= 1_000:
        return f"{round(n / 1_000)}K"
    return str(n)


def _index_tokens(root: Path) -> int:
    """What the INDEX costs, as it is on disk right now.

    Deliberately not recomputed from the episodes: a branch whose bot has not
    rendered yet injects the *stale* file, and the stale file's size is the
    honest answer to "what is memshelf costing me".
    """
    index = root / "INDEX.md"
    return len(index.read_text(encoding="utf-8")) // CHARS_PER_TOKEN if index.is_file() else 0


def _index_entry_costs(root: Path) -> dict[str, int]:
    """What each episode's INDEX line actually costs, keyed by episode id.

    Per line, not averaged: entries differ by a factor of two or more (titles
    and descriptions vary), so an average picks the wrong set of episodes to
    fold and then misreports what folding them bought. Entries are list items
    naming a ``.md`` file — the shape holds both with a URL provider (a
    Markdown link) and without one (a bare filename in backticks), which is
    what a memory shelf uses.
    """
    index = root / "INDEX.md"
    if not index.is_file():
        return {}
    costs: dict[str, int] = {}
    for line in index.read_text(encoding="utf-8").splitlines():
        if not line.lstrip().startswith("- ") or ".md" not in line:
            continue
        match = re.search(r"([A-Za-z0-9._-]+)\.md", line)
        if match:
            costs[match.group(1)] = max(len(line) // CHARS_PER_TOKEN, 1)
    return costs


def _pre_adopt(root: Path, records: list) -> bool:
    """True when the derived files still hold titles the episodes don't carry.

    A shelf written before the #58 migration keeps its display titles only in
    ``.meta.json``. ``rollup`` regenerates that file from the episodes, so a
    rollup here strips the title off **every remaining entry** — the INDEX
    shrinks far more than the proposal claimed, and for the wrong reason. The
    fix is one command (``memshelf rebuild --adopt``); saying so beats letting
    it be discovered in a diff.
    """
    by_filename = {r.filename: r for r in records if not r.archived}
    for category in sorted({r.category for r in records}):
        meta = root / "docs" / category / ".meta.json"
        if not meta.is_file():
            continue
        try:
            data = json.loads(meta.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(data, dict):
            continue
        for filename, entry in data.items():
            record = by_filename.get(filename)
            if record is None or record.display_title or not isinstance(entry, dict):
                continue
            # A title equal to the id is what `rebuild` writes for an episode
            # with no title of its own — nothing would be lost there.
            if entry.get("title") not in ("", None, record.id, filename):
                return True
    return False


def _classify(
    occupant: Occupant,
    *,
    shelved_ids: set[str],
    stale_after_turns: int,
) -> tuple[str, str]:
    """Return ``(class, reason)`` for one occupant.

    Conservative on purpose: anything not demonstrably closed or idle counts as
    live. An advisor that over-proposes gets ignored, which costs more than the
    proposals it would have gotten right.
    """
    if occupant.kind == "instructions":
        return CLASS_STATIC, "instructions/system overhead — not an episode, not shelvable"
    if occupant.episode_id and occupant.episode_id in shelved_ids:
        return CLASS_RECLAIMABLE, f"already on the shelf as {occupant.episode_id}"
    if occupant.episode_id:
        # The claim failed verification. Fall through to the state rules — this
        # content is NOT durable, whatever the caller thought.
        pass
    if occupant.state == "closed":
        return CLASS_RECLAIMABLE, "topic reported closed"
    if occupant.state == "live":
        return CLASS_LIVE, "still in play"
    if occupant.idle_turns is not None and occupant.idle_turns >= stale_after_turns:
        return CLASS_RECLAIMABLE, f"untouched for {occupant.idle_turns} turns"
    return CLASS_LIVE, "no state reported — counted as live"


def _shelve_command(shelf_root: str, occupant: Occupant) -> str:
    return (
        f"memshelf shelve --shelf {shelf_root} --slug <YYYY-MM-DD>-<slug> "
        f"--kind topic --approx-tokens {occupant.approx_tokens} --digest '<your digest>'"
    )


def _rollup_proposal(
    root: Path,
    *,
    index_tokens: int,
    records: list,
    budget_tokens: int,
    listed: int,
) -> tuple[Proposal | None, list[str]]:
    """Propose folding the oldest episodes when navigation has grown large.

    Triggered by ``INDEX_CONTEXT_SHARE`` of the caller's window — *not* by
    ``doctor``'s ``index-bloat``. It used to be the other half of that warning,
    and that pairing was the defect: the warning fired on an absolute budget
    that a growing shelf could not meet by any formatting, so the advisor's
    only remedy for a fat line was to archive episodes. On the author's shelf
    that read as "fold 75 of 113 episodes" for what was a description-length
    problem. A rollup is now proposed for size and only for size; fat lines are
    doctor's business and are fixed by trimming them.
    """
    notes: list[str] = []
    live = [r for r in records if not r.archived]
    if not live:
        return None, notes

    target = int(budget_tokens * INDEX_CONTEXT_SHARE)
    if index_tokens <= target:
        return None, notes

    # Say plainly when the shelf has both problems at once, so the rollup is
    # not mistaken for the fix to the other one. Folding lines that are each
    # overpriced carries the overprice into whatever is left.
    if listed and index_tokens > index_budget(listed):
        notes.append(
            "INDEX also exceeds its per-entry budget "
            f"(~{round((index_tokens - INDEX_BASE_TOKENS) / listed)} "
            f"tokens per entry against {INDEX_TOKENS_PER_ENTRY}); that is doctor's "
            "`index-bloat` and this rollup will not fix it — trim the long "
            "descriptions and `memshelf rebuild` first, then re-check whether the "
            "rollup is still worth making."
        )

    excess = index_tokens - target
    costs = _index_entry_costs(root)
    # A shelf whose INDEX has not been rendered yet (derived files are the
    # bot's since #58) gives no per-line data; fall back to a flat average.
    average = (
        max(sum(costs.values()) // len(costs), 1) if costs else max(index_tokens // len(live), 1)
    )

    # Walk oldest-first until the lines removed cover the excess. The rollup
    # episode adds an INDEX line of its own, so it has to pay for itself first.
    running = 0
    need = 0
    for record in live:
        need += 1
        running += costs.get(record.id, average)
        if running - average >= excess:
            break
    if need < 2 or need > len(live):
        return None, notes
    until = live[need - 1].date
    # Fold whole days: --until is a date, and leaving half of one day's
    # episodes behind would archive more than the count says.
    covered = [r for r in live if r.date and r.date <= until]
    reclaimed = max(sum(costs.get(r.id, average) for r in covered) - average, 0)
    if len(covered) >= len(live):
        notes.append(
            f"INDEX is ~{index_tokens} tokens against a target of {target} "
            f"({INDEX_CONTEXT_SHARE:.0%} of a {_human(budget_tokens)} window); folding "
            "every episode still may not reach it — the preamble is a floor."
        )
    if _pre_adopt(root, records):
        notes.append(
            "this shelf predates #58: display titles live in .meta.json, not in the "
            "episodes. A rollup regenerates the derived files, which would strip the "
            "titles off every remaining entry. Run `memshelf rebuild --shelf "
            f"{root} --adopt` first."
        )
    return (
        Proposal(
            action="rollup",
            target=f"{len(covered)} episode(s) through {until}",
            tokens=reclaimed,
            why=(
                f"INDEX is ~{index_tokens} tokens — {index_tokens / budget_tokens:.0%} of the "
                f"window, past the {INDEX_CONTEXT_SHARE:.0%} worth folding for — and it rides "
                "in every session; folding the oldest episodes shrinks navigation only — "
                "recall, search and the ledger keep them"
            ),
            command=(
                f"memshelf rollup --shelf {root} --slug {until}-rollup "
                f"--until {until} --digest '<your synthesis of the period>'"
            ),
        ),
        notes,
    )


def advise(
    shelf_root: str | Path,
    *,
    occupants: list[Occupant] | tuple[Occupant, ...] = (),
    budget_tokens: int = DEFAULT_BUDGET_TOKENS,
    stale_after_turns: int = STALE_AFTER_TURNS,
    include_memory_overhead: bool = True,
) -> Advice:
    """Report where the window went and what could be put down.

    ``occupants`` is the caller's account of its own context. Empty is a valid
    call — the shelf side is reported and the notes say the window side is
    missing, which is the honest answer to "show me what's eating my context"
    when nothing was supplied.
    """
    if budget_tokens <= 0:
        raise ValueError(f"budget_tokens must be positive, got {budget_tokens}")
    root = Path(shelf_root).expanduser().resolve()
    records, _ = collect_episodes(root)
    index_tokens = _index_tokens(root)
    # Counted off the rendered INDEX rather than off the episodes, for the
    # reason in `doctor.index_entries`: the budget has to describe the same
    # artifact whose size it is being compared to, and under #58 those two
    # sources disagree whenever the renderer is behind. Counting episodes here
    # and lines in doctor also made the two tools report different budgets for
    # the same shelf (760 vs 680, found in review).
    index_path = root / "INDEX.md"
    listed = (
        len(index_entries(index_path.read_text(encoding="utf-8"))) if index_path.is_file() else 0
    )
    # Same standing-cost model `memshelf stats` reports (INDEX + Σ digests), but
    # read from the episodes instead of `ledger.tsv`: since #58 the ledger is a
    # bot-rendered artifact, so on a branch it can lag or be absent entirely,
    # and an advisor that reported zero overhead there would be worse than
    # silent — it would be flattering.
    standing_cost = index_tokens + sum(r.digest_tokens for r in records)
    # Archived episodes stay recallable by id, so content shelved into the
    # archive is just as durable as content in docs/ — both count as "shelved".
    shelved_ids = {r.id for r in records}
    titles = {r.display_title.strip().lower(): r.id for r in records if r.display_title}
    titles.update({r.id.lower(): r.id for r in records})

    memory_overhead = standing_cost if include_memory_overhead else 0
    breakdown = {
        CLASS_STATIC: 0,
        CLASS_MEMORY: memory_overhead,
        CLASS_LIVE: 0,
        CLASS_RECLAIMABLE: 0,
    }

    proposals: list[Proposal] = []
    notes: list[str] = []
    unstated = 0
    too_small = 0

    for occupant in occupants:
        if occupant.state not in _STATES:
            raise ValueError(f"unknown state {occupant.state!r}; expected one of {list(_STATES)}")
        if occupant.kind not in _KINDS:
            raise ValueError(f"unknown kind {occupant.kind!r}; expected one of {list(_KINDS)}")

        if occupant.episode_id and occupant.episode_id not in shelved_ids:
            # The single most dangerous input this tool takes: acting on it
            # would drop content nobody stored. Say so; propose shelving.
            notes.append(
                f"{occupant.label!r} claims episode {occupant.episode_id!r}, which is not on "
                "this shelf — treated as unshelved. Dropping it would lose it."
            )
        elif not occupant.episode_id and occupant.label.strip().lower() in titles:
            notes.append(
                f"{occupant.label!r} matches shelved episode "
                f"{titles[occupant.label.strip().lower()]!r} by title — pass episode_id to "
                "confirm before dropping it."
            )

        cls, reason = _classify(
            occupant, shelved_ids=shelved_ids, stale_after_turns=stale_after_turns
        )
        breakdown[cls] += occupant.approx_tokens
        if cls == CLASS_LIVE and occupant.state == "unknown" and occupant.idle_turns is None:
            unstated += 1
        if cls != CLASS_RECLAIMABLE:
            continue

        if occupant.episode_id and occupant.episode_id in shelved_ids:
            proposals.append(
                Proposal(
                    action="drop",
                    target=occupant.label,
                    tokens=occupant.approx_tokens,  # already durable: nothing added
                    why=f"{reason} — carrying it duplicates the shelf; recall it if needed",
                    command=(
                        f"memshelf recall --shelf {root} --id {occupant.episode_id} "
                        "--section <Section> --log"
                    ),
                )
            )
            continue

        if occupant.approx_tokens < MIN_PROPOSAL_TOKENS:
            too_small += 1
            continue
        proposals.append(
            Proposal(
                action="shelve",
                target=occupant.label,
                tokens=occupant.approx_tokens - EPISODE_STANDING_COST,
                why=(
                    f"{reason}; net of the ~{EPISODE_STANDING_COST} tokens its digest and "
                    "INDEX line will cost every later session"
                ),
                command=_shelve_command(str(root), occupant),
            )
        )

    rollup, rollup_notes = _rollup_proposal(
        root,
        index_tokens=index_tokens,
        records=records,
        budget_tokens=budget_tokens,
        listed=listed,
    )
    notes.extend(rollup_notes)
    if rollup is not None:
        proposals.append(rollup)

    if not occupants:
        notes.insert(
            0,
            "no window breakdown supplied — this covers the shelf side only. Pass occupants "
            "(what is in your context and roughly how big) for the 'where did my window go' "
            "report.",
        )
    if unstated:
        notes.append(
            f"{unstated} occupant(s) reported neither state nor idleness — counted as live "
            "and not proposed. State them to get a verdict."
        )
    if too_small:
        notes.append(
            f"{too_small} reclaimable occupant(s) below {MIN_PROPOSAL_TOKENS} tokens were not "
            f"proposed: shelving costs ~{EPISODE_STANDING_COST} standing tokens forever, so "
            "small topics are not worth an episode."
        )

    proposals.sort(key=lambda p: (-p.tokens, p.action, p.target))
    accounted = sum(o.approx_tokens for o in occupants) + memory_overhead
    return Advice(
        budget_tokens=budget_tokens,
        accounted_tokens=accounted,
        headroom_tokens=budget_tokens - accounted,
        breakdown=breakdown,
        memory_overhead=memory_overhead,
        reclaimable_tokens=sum(p.tokens for p in proposals),
        episodes_on_shelf=len(records),
        index_tokens=index_tokens,
        index_budget_tokens=index_budget(listed),
        index_entry_tokens=(round((index_tokens - INDEX_BASE_TOKENS) / listed) if listed else 0),
        proposals=proposals,
        notes=notes,
    )
