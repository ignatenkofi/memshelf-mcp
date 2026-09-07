"""Token accounting over the shelf — the transparent-savings tool.

Reads ``ledger.tsv`` for the **claimed** economy (standing cost vs shelved mass
vs compression) and, when recall logging is on, ``recall-log.tsv`` for the
**realized** economy (what fetching sections actually saved against each
episode's original in-window cost). Same chars/4 methodology as docshelf's
``benchmarks/token_savings.py`` — no tokenizer dependency, ratios are
estimator-independent. See ``docs/M0.md`` → Measurement.

Claimed vs realized is the distinction the Case B verdict flagged: the ledger
measured what *would* be saved; the recall log measures what *was*.

Two quantities used to share one name (#110). ``approx_tokens`` is filled in
practice with the volume of material a session pushed through — which can be
many times the window, since a session reads, discards and reads again. Freed
context is a different thing and is bounded by the window: nothing can have
been costing more in-window than the window holds. Both are reported here, and
the bounded one is what feeds compression and realized savings.
"""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass
from pathlib import Path

CHARS_PER_TOKEN = 4

#: Upper bound applied to one episode's claimed mass before anything is summed.
#:
#: 200K is the standard window of the clients this shelf is written for, and it
#: is also the value #110's own arithmetic used: the issue reports 26.31M -> 13.8M
#: and 718:1 -> 377:1 on a 157-episode shelf, and this cap reproduces that shape
#: (381.2:1 on the same shelf grown to 197 episodes).
#:
#: A default that is too small understates a saving; one that is too large keeps
#: the overstatement #110 is about. Understating is the safer error for a number
#: whose whole job is to claim an economy, so the default errs small — and a
#: client with a bigger window says so with ``context_window=`` or
#: ``$MEMSHELF_CONTEXT_WINDOW``. Which value was used is reported back in the
#: ``context_window`` field rather than left implicit.
DEFAULT_CONTEXT_WINDOW = 200_000

CONTEXT_WINDOW_ENV = "MEMSHELF_CONTEXT_WINDOW"


@dataclass
class Stats:
    episodes: int  # distinct episodes on the shelf
    index_tokens: int  # tokens(INDEX.md) — injected every session
    digest_tokens: int  # Σ standing digest tokens (latest per episode)
    standing_cost: int  # index_tokens + digest_tokens: memory's per-session cost
    shelved_mass: int  # Σ min(approx_tokens_in, window): context that was freed
    work_volume: int  # Σ approx_tokens_in as claimed: material the sessions moved
    capped_episodes: int  # episodes whose claim exceeded the window
    context_window: int  # the bound the two fields above were computed with
    compression_ratio: float  # shelved_mass / standing_cost
    recalls: int  # logged recall calls (0 unless recall logging is on)
    episodes_recalled: int  # distinct episodes actually fetched back
    fetched_tokens: int  # Σ tokens pulled by those recalls
    realized_savings: int  # Σ (episode's freed mass − fetched) over recalls

    def as_dict(self) -> dict:
        return asdict(self)


def resolve_context_window(context_window: int | None = None) -> int:
    """Explicit argument → ``$MEMSHELF_CONTEXT_WINDOW`` → :data:`DEFAULT_CONTEXT_WINDOW`.

    Same resolution shape as ``$MEMSHELF_SHELF_PATH``: an explicit value always
    wins, the variable is the machine-wide default. A non-numeric or
    non-positive variable is ignored rather than honoured as zero — a zero
    window would report every episode as freeing nothing, which is a worse
    failure than the default.
    """
    if context_window is not None and context_window > 0:
        return context_window
    raw = os.environ.get(CONTEXT_WINDOW_ENV, "").strip()
    if raw:
        try:
            value = int(raw)
        except ValueError:
            return DEFAULT_CONTEXT_WINDOW
        if value > 0:
            return value
    return DEFAULT_CONTEXT_WINDOW


def _rows(path: Path) -> list[list[str]]:
    """Tab-split non-header, non-blank lines; missing file -> []."""
    if not path.is_file():
        return []
    out = []
    for i, line in enumerate(path.read_text(encoding="utf-8").splitlines()):
        if i == 0 or not line.strip():  # header / blank
            continue
        out.append(line.split("\t"))
    return out


def _int(value: str) -> int | None:
    try:
        return int(value)
    except ValueError:
        return None


def banner(stats: Stats) -> str:
    """One ambient line for session starts and tool output.

    The mass is labeled an estimate where it is read (#79): the headline
    number is the sum of caller-passed ``approx_tokens`` — chars/4-grade
    judgment calls by the shelf's own M0 methodology, not measurements — and
    nothing in the line said so. ``standing`` and ``realized`` stay unlabeled:
    they are computed from the artifacts directly.

    When the window clipped anything, the line says so and names the raw work
    volume too (#110). Printing only the clipped figure would hide that the
    ledger disagrees with it; printing only the raw one is the overstatement
    the issue is about.
    """
    line = (
        f"memshelf: {stats.episodes} episodes · standing {_human(stats.standing_cost)} tok "
        f"· holds ~{_human(stats.shelved_mass)} est. ({stats.compression_ratio}:1)"
    )
    if stats.capped_episodes:
        line += (
            f" · {stats.capped_episodes} episode(s) clipped to a "
            f"{_human(stats.context_window)} window, work volume ~{_human(stats.work_volume)}"
        )
    if stats.realized_savings:
        line += f" · realized saved {_human(stats.realized_savings)}"
    return line


def _human(n: int) -> str:
    if n >= 1_000_000:
        # Strip on the number, not after the suffix: ``"1.00M".rstrip("0")``
        # ends on "M" and strips nothing, so the trailing-zero trim was dead
        # code and every megatoken figure read "1.00M".
        return f"{n / 1_000_000:.2f}".rstrip("0").rstrip(".") + "M"
    if n >= 1_000:
        return f"{round(n / 1_000)}K"
    return str(n)


def episode_mass(
    shelf_root: str | Path,
    episode_id: str,
    *,
    context_window: int | None = None,
) -> int | None:
    """Freed context for one episode: latest ledger row, clipped to the window.

    Returns ``None`` for an unledgered episode. Clipped rather than raw because
    the one caller — the per-recall ``saved_tokens`` line — states a saving, and
    a saving cannot exceed what the window could hold (#110).
    """
    mass: int | None = None
    for cols in _rows(Path(shelf_root).expanduser().resolve() / "ledger.tsv"):
        if len(cols) >= 4 and cols[1] == episode_id:
            mass = _int(cols[3]) or mass
    if mass is None:
        return None
    return min(mass, resolve_context_window(context_window))


def compute_stats(shelf_root: str | Path, *, context_window: int | None = None) -> Stats:
    root = Path(shelf_root).expanduser().resolve()
    window = resolve_context_window(context_window)

    # Ledger, deduped to the latest row per episode (a re-shelve updates in
    # place rather than double-counting).
    latest: dict[str, tuple[int, int]] = {}
    for cols in _rows(root / "ledger.tsv"):
        if len(cols) < 5:
            continue
        mass, digest = _int(cols[3]), _int(cols[4])
        if mass is None or digest is None:
            continue
        latest[cols[1]] = (mass, digest)

    work_volume = sum(m for m, _ in latest.values())
    shelved_mass = sum(min(m, window) for m, _ in latest.values())
    capped_episodes = sum(1 for m, _ in latest.values() if m > window)
    digest_tokens = sum(d for _, d in latest.values())

    index_path = root / "INDEX.md"
    index_tokens = (
        len(index_path.read_text(encoding="utf-8")) // CHARS_PER_TOKEN
        if index_path.is_file()
        else 0
    )
    standing_cost = index_tokens + digest_tokens
    compression = round(shelved_mass / standing_cost, 1) if standing_cost else 0.0

    # Realized economy: each logged recall fetched `fetched` tokens where the
    # baseline — carrying / re-deriving that episode — was its freed mass, i.e.
    # the clipped one: a recall cannot save context the window never held.
    fetched_tokens = 0
    realized = 0
    recalled_ids: set[str] = set()
    recall_rows = _rows(root / "recall-log.tsv")
    for cols in recall_rows:
        if len(cols) < 3:
            continue
        fetched = _int(cols[2])
        if fetched is None:
            continue
        episode_id = cols[0]
        recalled_ids.add(episode_id)
        fetched_tokens += fetched
        baseline = min(latest.get(episode_id, (0, 0))[0], window)
        realized += max(baseline - fetched, 0)

    return Stats(
        episodes=len(latest),
        index_tokens=index_tokens,
        digest_tokens=digest_tokens,
        standing_cost=standing_cost,
        shelved_mass=shelved_mass,
        work_volume=work_volume,
        capped_episodes=capped_episodes,
        context_window=window,
        compression_ratio=compression,
        recalls=len(recall_rows),
        episodes_recalled=len(recalled_ids),
        fetched_tokens=fetched_tokens,
        realized_savings=realized,
    )
