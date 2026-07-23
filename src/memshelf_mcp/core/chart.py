"""The shelf's living savings chart — ``stats.svg`` at the shelf root.

Two cumulative step-lines over the ledger's dates: **without memshelf**
(expected — Σ ``approx_tokens_in``, what would ride in context or be
re-derived) vs **with memshelf** (actual — Σ ``digest_tokens``, what the
digests actually cost). The y-axis is logarithmic — the series sit three
orders of magnitude apart, and that gap *is* the story.

Pure stdlib string assembly (no plotting dependency): the file is meant to
live inside a git shelf, be regenerated on every shelve, and render on
GitHub / in any browser. Neutral grays + one blue; readable on light and
dark grounds.
"""

from __future__ import annotations

import math
from pathlib import Path

from memshelf_mcp.core.stats import _int, _rows

W, H = 800, 340
PLOT = {"left": 64, "right": 640, "top": 40, "bottom": 290}

_BLUE = "#2a78d6"
_GRAY = "#9a978c"
_INK = "#666666"
_GRID = "#d8d6cc"


def _cumulative_by_date(shelf_root: Path) -> list[tuple[str, int, int]]:
    """Ledger → sorted [(date, cum_expected, cum_actual)], one point per date."""
    per_date: dict[str, list[int]] = {}
    for cols in _rows(shelf_root / "ledger.tsv"):
        if len(cols) < 5:
            continue
        mass, digest = _int(cols[3]), _int(cols[4])
        if mass is None or digest is None:
            continue
        bucket = per_date.setdefault(cols[0], [0, 0])
        bucket[0] += mass
        bucket[1] += digest
    points: list[tuple[str, int, int]] = []
    exp = act = 0
    for date in sorted(per_date):
        exp += per_date[date][0]
        act += per_date[date][1]
        points.append((date, exp, act))
    return points


def _fmt(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.2f}M".rstrip("0").rstrip(".") + ("" if n >= 1_000_000 else "")
    if n >= 1_000:
        return f"{round(n / 1_000)}K"
    return str(n)


def render_chart_svg(shelf_root: str | Path) -> str | None:
    """Return the SVG text, or ``None`` when the ledger has no usable rows."""
    root = Path(shelf_root).expanduser().resolve()
    points = _cumulative_by_date(root)
    if not points:
        return None

    lo_v = (
        min(min(a for _, _, a in points if a > 0), 100) if any(a > 0 for _, _, a in points) else 100
    )
    hi_v = max(e for _, e, _ in points)
    lo, hi = math.log10(max(lo_v, 1)), math.log10(max(hi_v, lo_v * 10))

    def x(i: int) -> float:
        n = len(points)
        if n == 1:
            return (PLOT["left"] + PLOT["right"]) / 2
        return PLOT["left"] + i * (PLOT["right"] - PLOT["left"]) / (n - 1)

    def y(v: int) -> float:
        v = max(v, 1)
        frac = (math.log10(v) - lo) / (hi - lo)
        return PLOT["bottom"] - frac * (PLOT["bottom"] - PLOT["top"])

    def poly(series: list[int], color: str) -> str:
        pts = " ".join(f"{x(i):.1f},{y(v):.1f}" for i, v in enumerate(series))
        dots = "".join(
            f'<circle cx="{x(i):.1f}" cy="{y(v):.1f}" r="3" fill="{color}"/>'
            for i, v in enumerate(series)
        )
        return (
            f'<polyline points="{pts}" fill="none" stroke="{color}" '
            f'stroke-width="2" stroke-linejoin="round"/>' + dots
        )

    expected = [e for _, e, _ in points]
    actual = [a for _, _, a in points]
    last_date, last_e, last_a = points[-1]
    ratio = round(last_e / last_a, 1) if last_a else 0.0

    # Decade gridlines within range.
    grid = []
    d = math.ceil(lo)
    while d <= math.floor(hi):
        v = 10**d
        gy = y(v)
        grid.append(
            f'<line x1="{PLOT["left"]}" x2="{PLOT["right"]}" y1="{gy:.1f}" y2="{gy:.1f}" '
            f'stroke="{_GRID}" stroke-width="1"/>'
            f'<text x="{PLOT["left"] - 8}" y="{gy + 4:.1f}" text-anchor="end" '
            f'font-size="11" fill="{_INK}">{_fmt(v)}</text>'
        )
        d += 1

    # X labels: first + last always; middles only when few points.
    xlabels = []
    label_idx = (
        range(len(points)) if len(points) <= 8 else sorted({0, len(points) // 2, len(points) - 1})
    )
    for i in label_idx:
        xlabels.append(
            f'<text x="{x(i):.1f}" y="{PLOT["bottom"] + 18}" text-anchor="middle" '
            f'font-size="10" fill="{_INK}">{points[i][0][5:]}</text>'
        )

    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" font-family="system-ui, sans-serif">
  <title>memshelf token economy — cumulative, log scale</title>
  <text x="{PLOT["left"]}" y="20" font-size="14" font-weight="600" fill="{_INK}">Token economy — cumulative (log scale)</text>
  {"".join(grid)}
  <line x1="{PLOT["left"]}" x2="{PLOT["right"]}" y1="{PLOT["bottom"]}" y2="{PLOT["bottom"]}" stroke="{_GRAY}" stroke-width="1"/>
  {poly(expected, _GRAY)}
  {poly(actual, _BLUE)}
  <text x="{PLOT["right"] + 8}" y="{y(last_e) + 4:.1f}" font-size="12" fill="{_GRAY}">without memshelf: {_fmt(last_e)}</text>
  <text x="{PLOT["right"] + 8}" y="{y(last_a) + 4:.1f}" font-size="12" fill="{_BLUE}">on the shelf: {_fmt(last_a)}</text>
  <text x="{PLOT["right"] + 8}" y="{(y(last_e) + y(last_a)) / 2 + 4:.1f}" font-size="12" font-weight="600" fill="{_INK}">saved {ratio}:1</text>
  {"".join(xlabels)}
  <text x="{PLOT["left"]}" y="{H - 10}" font-size="10" fill="{_GRAY}">digests only; INDEX adds a small flat cost per session · chars/4 estimate · redrawn on every shelve · as of {last_date}</text>
</svg>
"""


def write_chart(shelf_root: str | Path, filename: str = "stats.svg") -> str | None:
    """Render and write the chart; return the relative path, or None if no data."""
    root = Path(shelf_root).expanduser().resolve()
    svg = render_chart_svg(root)
    if svg is None:
        return None
    (root / filename).write_text(svg, encoding="utf-8")
    return filename
