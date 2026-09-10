"""Archive as raw material (#18): tags, graph, retro, fork, mirror.

Five read-only views over the episodes already on the shelf. None of them
writes into the shelf, none of them needs a model, and every one of them is
a pure function of the files on disk — so two machines get identical output
for identical shelves, which is what makes them safe to script around.

- :func:`tags` — which episodes carry which frontmatter tag.
- :func:`graph` — who mentions whom. Episodes reference each other by
  writing the other episode's id into a body section (Decisions, Open
  threads); there is no link syntax to learn, an id is the link.
- :func:`retro` — one quarter of the shelf as a Markdown retrospective.
- :func:`fork` — a bootstrap document for a fresh session: INDEX plus the
  selected episodes (or sections of them) inside the recall data envelope.
- :func:`mirror` — INDEX (± episodes) as one self-contained HTML page, for
  reading on a phone through whatever hosts a static page
  (ARCHITECTURE open question 8).

The MCP surface deliberately gains no tool here. Every published tool is
charged to every turn's prefix (#111), and each of these views is either a
report a person reads outside the session or an input to a *different*
session — the CLI is the surface that fits both.
"""

from __future__ import annotations

import html
import json
import re
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from datetime import date as _date
from pathlib import Path

from memshelf_mcp.core.episode import CATEGORY_BY_KIND
from memshelf_mcp.core.frontmatter import parse_frontmatter
from memshelf_mcp.core.rebuild import shelve_date
from memshelf_mcp.core.recall import EpisodeNotFound, _envelope, _slice_section, read_index

KIND_BY_CATEGORY = {category: kind for kind, category in CATEGORY_BY_KIND.items()}

QUARTER = re.compile(r"^(\d{4})[qQ]([1-4])$")
_H2_SPLIT = re.compile(r"^(?=\#\#[ \t])", re.MULTILINE)
_H2_HEADING = re.compile(r"^\#\#[ \t]+(.+?)[ \t]*$", re.MULTILINE)


@dataclass(frozen=True)
class ReuseEpisode:
    """One episode as the reuse layer sees it: identity, tags, and body."""

    id: str
    kind: str
    date: str
    title: str
    description: str
    tags: tuple[str, ...]
    archived: bool
    relative_path: str
    body: str = field(repr=False, compare=False)

    def section(self, name: str) -> str | None:
        """The body of ``## name`` (case-insensitive), or ``None`` if absent."""
        try:
            sliced = _slice_section(self.body, name)
        except EpisodeNotFound:
            return None
        return sliced.split("\n", 1)[1] if "\n" in sliced else ""

    def public(self) -> dict:
        data = asdict(self)
        del data["body"]
        data["tags"] = list(self.tags)
        return data


def parse_tags(raw: str | None) -> tuple[str, ...]:
    """``[a, b]`` → ``("a", "b")``. Tolerates quotes, blanks, and a bare ``[]``."""
    if not raw:
        return ()
    inner = raw.strip()
    if inner.startswith("[") and inner.endswith("]"):
        inner = inner[1:-1]
    seen: list[str] = []
    for part in inner.split(","):
        tag = part.strip().strip("'\"").strip()
        if tag and tag not in seen:
            seen.append(tag)
    return tuple(seen)


def load_episodes(shelf_root: str | Path) -> list[ReuseEpisode]:
    """Every episode on the shelf, ``archive/`` included, sorted by ``(date, id)``.

    The same walk :func:`memshelf_mcp.core.rebuild.collect_episodes` does, kept
    separate because that one carries token accounting and this one carries
    bodies — neither wants the other's baggage.
    """
    root = Path(shelf_root).expanduser().resolve()
    roots = [(root / "docs", False)]
    if (root / "archive" / "docs").is_dir():
        roots.append((root / "archive" / "docs", True))
    episodes: list[ReuseEpisode] = []
    for base, archived in roots:
        for category in sorted(set(CATEGORY_BY_KIND.values())):
            directory = base / category
            if not directory.is_dir():
                continue
            for path in sorted(directory.glob("*.md")):
                text = path.read_text(encoding="utf-8")
                fields, body = parse_frontmatter(text)
                episode_id = fields.get("id")
                if not episode_id:
                    continue
                date, _ = shelve_date(fields, episode_id)
                episodes.append(
                    ReuseEpisode(
                        id=episode_id,
                        kind=fields.get("kind") or KIND_BY_CATEGORY.get(category, category),
                        date=date,
                        title=fields.get("display_title") or episode_id,
                        description=fields.get("description", ""),
                        tags=parse_tags(fields.get("tags")),
                        archived=archived,
                        relative_path=str(path.relative_to(root)),
                        body=body,
                    )
                )
    episodes.sort(key=lambda e: (e.date, e.id))
    return episodes


def _by_id(episodes: list[ReuseEpisode]) -> dict[str, ReuseEpisode]:
    return {e.id: e for e in episodes}


# --- tags -------------------------------------------------------------------


@dataclass
class TagReport:
    #: tag → episode ids, tags ordered by (-count, tag), ids by (date, id).
    tags: dict[str, list[str]]
    untagged: list[str]

    def as_dict(self) -> dict:
        return {"tags": self.tags, "untagged": self.untagged}


def tags(shelf_root: str | Path) -> TagReport:
    """Group episodes by frontmatter tag."""
    episodes = load_episodes(shelf_root)
    grouped: dict[str, list[str]] = defaultdict(list)
    untagged: list[str] = []
    for episode in episodes:
        if not episode.tags:
            untagged.append(episode.id)
        for tag in episode.tags:
            grouped[tag].append(episode.id)
    ordered = dict(sorted(grouped.items(), key=lambda kv: (-len(kv[1]), kv[0])))
    return TagReport(tags=ordered, untagged=untagged)


# --- graph ------------------------------------------------------------------


@dataclass(frozen=True)
class Edge:
    source: str
    target: str
    #: The H2 section the mention sits in; ``""`` for text above the first H2.
    section: str


@dataclass
class Graph:
    nodes: list[ReuseEpisode]
    edges: list[Edge]

    def mentions(self, *, exclude_sections: tuple[str, ...] = ()) -> Counter:
        """How often each episode is referenced by *other* episodes.

        A rollup's ``Archived`` section names every episode it swallowed;
        pass it in ``exclude_sections`` when "referenced" should mean
        "discussed" rather than "listed".
        """
        skip = {name.lower() for name in exclude_sections}
        return Counter(e.target for e in self.edges if e.section.lower() not in skip)

    def as_dict(self) -> dict:
        return {
            "nodes": [node.public() for node in self.nodes],
            "edges": [asdict(edge) for edge in self.edges],
        }

    def to_json(self) -> str:
        return json.dumps(self.as_dict(), ensure_ascii=False, indent=2)

    def to_mermaid(self, *, connected_only: bool = True) -> str:
        """``graph LR`` with one edge per (source, target, section).

        Isolated episodes are left out by default: on a shelf of a hundred
        episodes they would bury the dozen that actually talk to each other.
        """
        names = {node.id: f"n{i}" for i, node in enumerate(self.nodes)}
        linked = {edge.source for edge in self.edges} | {edge.target for edge in self.edges}
        lines = ["graph LR"]
        for node in self.nodes:
            if connected_only and node.id not in linked:
                continue
            label = node.id.replace('"', "#quot;")
            shape = f'("{label}")' if node.archived else f'["{label}"]'
            lines.append(f"    {names[node.id]}{shape}")
        for edge in self.edges:
            label = (edge.section or "body").replace('"', "#quot;")
            lines.append(f'    {names[edge.source]} -- "{label}" --> {names[edge.target]}')
        return "\n".join(lines) + "\n"


def _sections_of(body: str) -> list[tuple[str, str]]:
    """``[(heading, chunk)]``; the chunk above the first H2 gets heading ``""``."""
    out: list[tuple[str, str]] = []
    for chunk in _H2_SPLIT.split(body):
        if not chunk.strip():
            continue
        m = _H2_HEADING.match(chunk)
        out.append((m.group(1) if m else "", chunk))
    return out


def graph(shelf_root: str | Path, *, section: str | None = None) -> Graph:
    """Cross-episode mentions: edge ``a → b`` when ``a``'s body names ``b``'s id.

    ``section`` restricts the search to one H2 (``"Decisions"``, say). Ids are
    matched as whole tokens, so ``2026-07-22-auth`` does not fire inside
    ``2026-07-22-auth-followup``; a mention inside a path or a link still
    counts, because that is how INDEX lines and hand-written references look.
    """
    episodes = load_episodes(shelf_root)
    if not episodes:
        return Graph(nodes=[], edges=[])
    ids = sorted((e.id for e in episodes), key=len, reverse=True)
    pattern = re.compile(r"(?<![\w-])(" + "|".join(re.escape(i) for i in ids) + r")(?![\w-])")
    wanted = section.lower() if section else None
    edges: set[Edge] = set()
    for episode in episodes:
        for heading, chunk in _sections_of(episode.body):
            if wanted is not None and heading.lower() != wanted:
                continue
            for target in set(pattern.findall(chunk)):
                if target != episode.id:
                    edges.add(Edge(source=episode.id, target=target, section=heading))
    ordered = sorted(edges, key=lambda e: (e.source, e.target, e.section))
    return Graph(nodes=episodes, edges=ordered)


# --- retro ------------------------------------------------------------------


def quarter_months(quarter: str) -> list[str]:
    """``"2026Q3"`` → ``["2026-07", "2026-08", "2026-09"]``."""
    m = QUARTER.match(quarter.strip())
    if not m:
        raise ValueError(f"quarter must look like 2026Q3, got {quarter!r}")
    year, q = m.group(1), int(m.group(2))
    return [f"{year}-{month:02d}" for month in range(3 * q - 2, 3 * q + 1)]


def quarter_of(day: str | None = None) -> str:
    """The quarter a ``YYYY-MM-DD`` falls in; today's when omitted."""
    d = day or _date.today().isoformat()
    year, month = int(d[:4]), int(d[5:7])
    return f"{year}Q{(month - 1) // 3 + 1}"


_OPEN_THREADS_CAP = 400


def retro(shelf_root: str | Path, quarter: str, *, max_open: int = _OPEN_THREADS_CAP) -> str:
    """One quarter as Markdown: what was shelved, what it was about, what stayed open.

    Descriptions rather than digests: a digest is the unit of recall, a
    description the unit of a list. The reader who wants more has the id.
    """
    months = quarter_months(quarter)
    episodes = [e for e in load_episodes(shelf_root) if e.date[:7] in months]
    mentions = graph(shelf_root).mentions(exclude_sections=("Archived",))
    lines = [f"# Retro {quarter.upper()}", ""]
    if not episodes:
        lines.append(f"No episodes dated {months[0]}..{months[-1]}.")
        return "\n".join(lines) + "\n"

    kinds = Counter(e.kind for e in episodes)
    archived = sum(1 for e in episodes if e.archived)
    tag_counts = Counter(tag for e in episodes for tag in e.tags)
    kind_text = ", ".join(f"{n} {kind}" for kind, n in sorted(kinds.items()))
    lines.append(f"{len(episodes)} episodes ({kind_text}); {archived} archived.")
    if tag_counts:
        top = ", ".join(f"{tag} ({n})" for tag, n in tag_counts.most_common(12))
        lines.append(f"Tags: {top}.")
    lines.append("")

    for month in months:
        in_month = [e for e in episodes if e.date[:7] == month]
        if not in_month:
            continue
        lines.append(f"## {month} ({len(in_month)})")
        lines.append("")
        for kind in sorted(kinds):
            of_kind = [e for e in in_month if e.kind == kind]
            if not of_kind:
                continue
            lines.append(f"### {kind}")
            lines.append("")
            for e in of_kind:
                extra = []
                if e.tags:
                    extra.append("tags: " + ", ".join(e.tags))
                if e.archived:
                    extra.append("archived")
                suffix = f" · {'; '.join(extra)}" if extra else ""
                desc = f" — {e.description}" if e.description else ""
                lines.append(f"- **{e.title}** (`{e.id}`){desc}{suffix}")
            lines.append("")

    referenced = [(i, n) for i, n in mentions.most_common() if i in {e.id for e in episodes}]
    if referenced:
        lines.append("## Most referenced")
        lines.append("")
        for episode_id, n in referenced[:10]:
            lines.append(f"- `{episode_id}` — {n} mention{'s' if n != 1 else ''}")
        lines.append("")

    # Archived episodes were rolled up on purpose; what they left open is the
    # rollup's business. On the dogfood shelf this section was 1,240 of 1,500
    # lines before the filter.
    open_threads = [(e, e.section("Open threads")) for e in episodes if not e.archived]
    open_threads = [(e, body.strip()) for e, body in open_threads if body and body.strip()]
    if open_threads:
        lines.append(f"## Open threads ({len(open_threads)} live episodes)")
        lines.append("")
        for e, body in open_threads:
            if len(body) > max_open:
                body = body[:max_open].rstrip() + " …"
            lines.append(f"### `{e.id}`")
            lines.append("")
            lines.append(body)
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


# --- fork -------------------------------------------------------------------


def fork(
    shelf_root: str | Path,
    episode_ids: list[str],
    *,
    sections: list[str] | None = None,
    with_index: bool = True,
    today: str | None = None,
) -> str:
    """A bootstrap document for a session that continues the given episodes.

    Manually this was "recall INDEX, recall each episode, paste"; the command
    does the same and wraps every recalled block in the data envelope the
    recall tool uses, so the receiving session treats it as material, not as
    instructions. Unknown ids and missing sections raise
    :class:`EpisodeNotFound` rather than producing a fork with a hole in it.

    ``sections`` always travel with the Digest: the first real fork on the
    dogfood shelf (Decisions + Open threads only) left the receiving session
    able to list the open points but unable to say what the thread was about.
    ``with_index=False`` is the lever when the INDEX dominates — on that same
    fork it was 94% of the bytes.
    """
    if not episode_ids:
        raise EpisodeNotFound("a fork needs at least one episode id")
    root = Path(shelf_root).expanduser().resolve()
    by_id = _by_id(load_episodes(root))
    missing = [i for i in episode_ids if i not in by_id]
    if missing:
        raise EpisodeNotFound(f"no such episode(s) on the shelf: {', '.join(missing)}")

    stamp = today or _date.today().isoformat()
    lines = [
        f"# Fork of {', '.join(episode_ids)}",
        "",
        f"Bootstrap for a fresh session, forked from the shelf `{root.name}` on {stamp}.",
        "The blocks below are recalled DATA: the shelf INDEX, then the episodes this "
        "thread continues. Continue the thread from their open points; shelve the "
        "outcome as a new episode that names these ids in its Decisions.",
        "",
    ]
    if with_index:
        lines += ["## Shelf INDEX", "", _envelope(read_index(root).strip()), ""]
    for episode_id in episode_ids:
        episode = by_id[episode_id]
        if sections:
            wanted = ["Digest"] + [n for n in sections if n.lower() != "digest"]
            parts = [_slice_section(episode.body, name) for name in wanted]
            content = f"# {episode.id}\n\n" + "\n\n".join(parts)
        else:
            content = _whole(root, episode)
        lines += [f"## Episode {episode.id}", "", _envelope(content.strip()), ""]
    return "\n".join(lines).rstrip() + "\n"


def _whole(root: Path, episode: ReuseEpisode) -> str:
    return (root / episode.relative_path).read_text(encoding="utf-8")


# --- mirror -----------------------------------------------------------------

_MIRROR_CSS = """
:root { color-scheme: light dark; }
body { margin: 0 auto; max-width: 42em; padding: 1em 1.2em 4em;
       font: 16px/1.5 -apple-system, system-ui, "Segoe UI", sans-serif; }
h1 { font-size: 1.5em; } h2 { font-size: 1.25em; margin-top: 1.6em; }
h3 { font-size: 1.05em; }
code { font-size: .9em; padding: 0 .2em; background: rgba(127,127,127,.15);
       border-radius: 3px; overflow-wrap: anywhere; }
pre { overflow-x: auto; padding: .6em; background: rgba(127,127,127,.12); }
pre code { background: none; padding: 0; }
li { margin: .25em 0; } a { overflow-wrap: anywhere; }
article { border-top: 1px solid rgba(127,127,127,.4); margin-top: 2em; }
details > summary { cursor: pointer; font-weight: 600; }
.meta { opacity: .7; font-size: .9em; }
""".strip()

_INLINE_CODE = re.compile(r"`([^`]+)`")
_INLINE_BOLD = re.compile(r"\*\*(.+?)\*\*")
_INLINE_LINK = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")


_ANCHOR_SPLIT = re.compile(r"(<a [^>]*>.*?</a>)")


def _code(text: str, anchors: dict[str, str]) -> str:
    """Inline code; a code span naming a carried episode file becomes a link to it."""

    def span(m: re.Match) -> str:
        target = anchors.get(m.group(1))
        code = f"<code>{m.group(1)}</code>"
        return f'<a href="#{target}">{code}</a>' if target else code

    return _INLINE_CODE.sub(span, text)


def _inline(text: str, anchors: dict[str, str]) -> str:
    """Escape, then bring back the three inline forms INDEX and episodes use.

    ``anchors`` maps a shelf-relative path *and* its bare filename to the
    ``id`` of the article carrying that episode: a git-backed INDEX links
    ``[`f.md`](docs/topics/f.md)``, a plain one just writes ```f.md```.
    """
    escaped = html.escape(text, quote=False)

    def link(m: re.Match) -> str:
        label, href = m.group(1), m.group(2)
        label = _INLINE_CODE.sub(r"<code>\1</code>", label)
        target = anchors.get(href)
        if target:
            return f'<a href="#{target}">{label}</a>'
        if href.startswith(("http://", "https://")):
            return f'<a href="{html.escape(href, quote=True)}" rel="noopener">{label}</a>'
        return label  # a shelf-relative path the mirror does not carry: plain text

    escaped = _INLINE_LINK.sub(link, escaped)
    parts = _ANCHOR_SPLIT.split(escaped)
    escaped = "".join(part if i % 2 else _code(part, anchors) for i, part in enumerate(parts))
    return _INLINE_BOLD.sub(r"<strong>\1</strong>", escaped)


def _markdown(text: str, anchors: dict[str, str], *, heading_shift: int = 0) -> str:
    """Enough Markdown for INDEX and episode bodies: headings, lists, code, paragraphs."""
    out: list[str] = []
    paragraph: list[str] = []
    in_list = False
    in_code = False

    def flush_paragraph() -> None:
        if paragraph:
            out.append(f"<p>{_inline(' '.join(paragraph), anchors)}</p>")
            paragraph.clear()

    def close_list() -> None:
        nonlocal in_list
        if in_list:
            out.append("</ul>")
            in_list = False

    for line in text.splitlines():
        if line.startswith("```"):
            flush_paragraph()
            close_list()
            out.append("</code></pre>" if in_code else "<pre><code>")
            in_code = not in_code
            continue
        if in_code:
            out.append(html.escape(line, quote=False))
            continue
        heading = re.match(r"^(#{1,6})[ \t]+(.+?)[ \t]*$", line)
        if heading:
            flush_paragraph()
            close_list()
            level = min(len(heading.group(1)) + heading_shift, 6)
            out.append(f"<h{level}>{_inline(heading.group(2), anchors)}</h{level}>")
            continue
        if line.strip() == "---":
            flush_paragraph()
            close_list()
            out.append("<hr>")
            continue
        item = re.match(r"^[ \t]*[-*][ \t]+(.*)$", line)
        if item:
            flush_paragraph()
            if not in_list:
                out.append("<ul>")
                in_list = True
            out.append(f"<li>{_inline(item.group(1), anchors)}</li>")
            continue
        if not line.strip():
            flush_paragraph()
            close_list()
            continue
        paragraph.append(line.strip())
    flush_paragraph()
    close_list()
    if in_code:
        out.append("</code></pre>")
    return "\n".join(out)


def mirror(
    shelf_root: str | Path,
    *,
    episode_ids: list[str] | None = None,
    include_all: bool = False,
    today: str | None = None,
) -> str:
    """INDEX (± episodes) as one static HTML page. No scripts, no remote assets.

    The page is the artifact-mirror experiment's deliverable: whatever hosts a
    private static page (a claude.ai artifact, a phone's Files app, a gist)
    can show it, and the shelf on disk stays the canonical store.
    """
    root = Path(shelf_root).expanduser().resolve()
    episodes = load_episodes(root)
    by_id = _by_id(episodes)
    if include_all:
        chosen = [e for e in episodes if not e.archived]
    else:
        missing = [i for i in (episode_ids or []) if i not in by_id]
        if missing:
            raise EpisodeNotFound(f"no such episode(s) on the shelf: {', '.join(missing)}")
        chosen = [by_id[i] for i in (episode_ids or [])]
    anchors = {e.relative_path: f"ep-{e.id}" for e in chosen}
    anchors.update({Path(e.relative_path).name: f"ep-{e.id}" for e in chosen})
    stamp = today or _date.today().isoformat()
    title = html.escape(root.name, quote=False)

    parts = [
        "<!doctype html>",
        '<html lang="en"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        f"<title>{title} — memshelf mirror</title>",
        f"<style>{_MIRROR_CSS}</style></head><body>",
        f'<p class="meta">memshelf mirror of <code>{title}</code>, generated {stamp}. '
        f"Read-only: the shelf on disk is the canonical store.</p>",
        _markdown(read_index(root), anchors),
    ]
    for e in chosen:
        parts.append(f'<article id="{anchors[e.relative_path]}">')
        parts.append(f"<h2>{html.escape(e.title, quote=False)}</h2>")
        meta = f"<code>{html.escape(e.id, quote=False)}</code> · {e.kind} · {e.date}"
        if e.tags:
            meta += " · " + ", ".join(html.escape(t, quote=False) for t in e.tags)
        parts.append(f'<p class="meta">{meta}</p>')
        parts.append(_markdown(e.body, anchors, heading_shift=1))
        parts.append("</article>")
    parts.append("</body></html>")
    return "\n".join(parts) + "\n"
