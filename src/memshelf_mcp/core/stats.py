"""Token accounting over the shelf — the transparent-savings tool.

Reads ``ledger.tsv`` for the **claimed** economy (standing cost vs shelved mass
vs compression) and, when recall logging is on, ``recall-log.tsv`` for the
**realized** economy (what fetching sections actually saved against each
episode's original in-window cost). Same chars/4 methodology as docshelf's
``benchmarks/token_savings.py`` — no tokenizer dependency, ratios are
estimator-independent. See ``docs/M0.md`` → Measurement.

Claimed vs realized is the distinction the Case B verdict flagged: the ledger
measured what *would* be saved; the recall log measures what *was*.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path

CHARS_PER_TOKEN = 4


@dataclass
class Stats:
    episodes: int  # distinct episodes on the shelf
    index_tokens: int  # tokens(INDEX.md) — injected every session
    digest_tokens: int  # Σ standing digest tokens (latest per episode)
    standing_cost: int  # index_tokens + digest_tokens: memory's per-session cost
    shelved_mass: int  # Σ approx_tokens_in: what would otherwise ride in context
    compression_ratio: float  # shelved_mass / standing_cost
    recalls: int  # logged recall calls (0 unless recall logging is on)
    episodes_recalled: int  # distinct episodes actually fetched back
    fetched_tokens: int  # Σ tokens pulled by those recalls
    realized_savings: int  # Σ (episode's original mass − fetched) over recalls

    def as_dict(self) -> dict:
        return asdict(self)


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


def compute_stats(shelf_root: str | Path) -> Stats:
    root = Path(shelf_root).expanduser().resolve()

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

    shelved_mass = sum(m for m, _ in latest.values())
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
    # baseline — carrying / re-deriving that episode — was its original mass.
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
        baseline = latest.get(episode_id, (0, 0))[0]
        realized += max(baseline - fetched, 0)

    return Stats(
        episodes=len(latest),
        index_tokens=index_tokens,
        digest_tokens=digest_tokens,
        standing_cost=standing_cost,
        shelved_mass=shelved_mass,
        compression_ratio=compression,
        recalls=len(recall_rows),
        episodes_recalled=len(recalled_ids),
        fetched_tokens=fetched_tokens,
        realized_savings=realized,
    )
