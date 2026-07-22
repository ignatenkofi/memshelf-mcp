# Does memshelf actually pay for itself? — a measured demo

The [README](../README.md) explains the *mechanism* — offload closed topics as
digest-indexed episodes, keep only `INDEX.md` in context, recall one section
when needed. This page measures whether the mechanism pays off, on **the real
dogfood shelf**: `sqst-memshelf`, the private working-memory shelf that ran the
whole M0 experiment ([protocol](M0.md)) — Case A (retro-import of months of
course work) and Case B (a week of live shelve-at-close, 2026-07-13 → 22).

Unlike [docshelf's demo](https://github.com/ignatenkofi/docshelf-mcp/blob/main/docs/demo.md)
shelves, a memory shelf is private by design, so you can't clone this one — but
every number below comes from `memshelf stats` / `memshelf doctor`, which you
can [point at your own shelf](#reproduce-it) unchanged.

## The numbers

| Shelf state | Episodes | `INDEX.md` | Standing cost (INDEX + digests) | Shelved mass | Ratio |
|---|---|---|---|---|---|
| Case A close (2026-07-13) | 17 | 1,370 tok | ~2.7K tok | ~76K conversational (raw sessions far larger) | ~97% cheaper than the source |
| **Today (2026-07-22, `memshelf stats`)** | **34** | **2,704 tok** | **8,638 tok** | **1,924,800 tok** | **222.8 : 1** |

- **One query** at Case A close — `INDEX` + 1 episode — cost **1,765 tokens**:
  77.9% below dumping the 17-episode shelf (7,995), ~97% below the
  conversational source it summarizes.
- **The recall test: 5/5.** A fresh-context agent, given only the INDEX path
  and the recall rule, answered five known-answer questions ("what was decided
  about X and why", "which recurring mistake…") — 6 files read, zero misses,
  zero over-fetch.
- **Case B added 16 live episodes with zero loss** — working sessions of
  160–220K tokens each (audits, fix sprints, an autonomous P3 sweep), each
  closed with a shelve instead of dying with the container.

Two things stand out, same shape as docshelf's result:

- **The mass doesn't fit; the memory does.** 1.9M tokens of shelved history
  overflows any context window — the standing cost (8.6K) rides along every
  session with room to spare.
- **Standing cost grows sublinearly.** Doubling the episode count (17 → 34)
  roughly doubled digests but the *query* cost stays `INDEX + one section`,
  flat by construction.

## What the doctor found (first run on the live shelf)

The M1 tools were pointed at the dogfood shelf the day they were built.
`memshelf doctor` returned `healthy: false` — correctly:

- **Two hand-era digests over the 120-word cap.** Both written during M0, when
  the contract was "validated by agent honor" (annoyance log #3). Honor
  measurably slipped; now the validator rejects those at shelve time.
- **One secret-shaped string at rest** — inspection showed a deliberate dummy
  credential inside a teaching recipe quoted from a student's homework review.
  Shape guards report, humans judge; the finding is the system working.
- **`index-bloat` warning** — INDEX (~2.7K tokens) crossed the injection
  budget, the exact drift the M2 rollup milestone exists for, observed in the
  wild rather than predicted.

## The headline stays the accidental one

The strongest M0 finding needed no benchmark: the actual homework-review-season
transcript (April–June) **no longer exists anywhere** — not in the chat export,
not in rotated session logs. The only trace is what was hand-copied in time.
"Memory that isn't shelved while the context exists is memory lost" stopped
being a slogan on day one ([M0.md](M0.md)); Case B then ran a full week of real
work without losing an episode.

## Methodology — and what is honestly not measured

- **Token counting** is chars/4 (docshelf's
  [`token_savings.py`](https://github.com/ignatenkofi/docshelf-mcp/blob/main/benchmarks/token_savings.py)
  methodology): network-free, and since every measure uses the same counter,
  the *ratios* are estimator-independent; only absolute counts move between
  tokenizers.
- **Claimed vs realized.** The ledger measures *claimed* economy (what would
  otherwise ride in context). Realized economy needs real recalls:
  `memshelf recall --log` appends each fetch to `recall-log.tsv`, and `stats`
  then reports `realized_savings` — on a scratch run, fetching one
  `## Decisions` section cost **11 tokens against a 30,000-token episode**.
  The live shelf's recall log starts accumulating from today.
- **Not measured:** the true fetch-hit *rate* (the share of past-work questions
  a shelf fetch closes vs re-reading the repo or the owner re-explaining) needs
  a denominator no tool can capture; stats reports the measurable side and
  says so ([DECISIONS](DECISIONS.md), 2026-07-22).

## Reproduce it

On any shelf (a docshelf shelf with `topics`/`research`/`sessions` and a
`ledger.tsv`):

```bash
git clone https://github.com/ignatenkofi/memshelf-mcp && pip install -e memshelf-mcp
memshelf stats  --shelf /path/to/shelf
memshelf doctor --shelf /path/to/shelf   # exit 1 on contract violations
```

Or run the full loop on a scratch shelf:

```bash
python3 -c "from docshelf_mcp import Shelf; \
  Shelf('demo-shelf').init(name='demo', default_categories=['topics','research','sessions'])"
git -C demo-shelf init

memshelf shelve --shelf demo-shelf --slug 2026-07-22-demo --kind topic \
  --digest "The demo chose a local shelf; a cloud store was rejected for portability. Open: nothing." \
  --section "Decisions=local shelf over cloud store" --approx-tokens 30000

memshelf recall --shelf demo-shelf --id 2026-07-22-demo --section Decisions --log
memshelf stats  --shelf demo-shelf     # realized_savings is now non-zero
memshelf doctor --shelf demo-shelf
```

Try shelving a digest that starts with "We decided…" — the tool rejects it
with the exact fix and writes nothing. That, plus the two over-cap digests the
doctor caught above, is the difference between a convention and a contract.

---

*Back to the [README](../README.md) · [M0 protocol & results](M0.md) ·
[ROADMAP](ROADMAP.md).*
