"""Semantic sidecar for ``search`` (#17, ROADMAP M3).

Grep finds what is spelled the way the query spells it. A digest written in
Russian about a fix described in English, or a query that paraphrases, is a
miss — and a missed episode is a deleted one in every way that matters.

The sidecar is an embedding index over the episodes, kept **outside** the
shelf (a shelf is a git repository; a derived binary blob would ride into
every diff) and rebuildable from it at any time. :func:`memshelf_mcp.core.recall.search`
keeps its signature: when the sidecar is usable it fuses the grep ranking
with the semantic one by reciprocal rank; when it is not — no model
installed, no index built, ``$MEMSHELF_SEMANTIC=off`` — the grep path runs
exactly as before, byte for byte.

Model: model2vec static embeddings (``minishlab/potion-multilingual-128M``
by default). No GPU, no service, one process; the model is an optional
extra (``pip install memshelf-mcp[semantic]``) so the base install stays as
light as it is.
"""

from __future__ import annotations

import importlib.util
import json
import logging
import math
import os
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

from memshelf_mcp import instances
from memshelf_mcp.core.reuse import ReuseEpisode, load_episodes

logger = logging.getLogger(__name__)

SEMANTIC_ENV = "MEMSHELF_SEMANTIC"
MODEL_ENV = "MEMSHELF_SEMANTIC_MODEL"
DEFAULT_MODEL = "minishlab/potion-multilingual-128M"
INDEX_VERSION = 1
INDEX_FILENAME = "index.json"
#: One section is one chunk; a section longer than this is embedded by its
#: head. Static embeddings average tokens, so a long tail only blurs.
CHUNK_CHARS = 2000
SNIPPET_CHARS = 160
#: Reciprocal-rank-fusion constant (Cormack et al. 2009 use 60).
RRF_K = 60
_OFF = {"off", "0", "false", "no"}
_H2_SPLIT = re.compile(r"^(?=\#\#[ \t])", re.MULTILINE)
_H2_HEADING = re.compile(r"^\#\#[ \t]+(.+?)[ \t]*$", re.MULTILINE)


class SemanticError(RuntimeError):
    """The sidecar cannot do what was asked (no model, no index)."""


class Embedder(Protocol):
    name: str

    def encode(self, texts: list[str]) -> list[list[float]]: ...


class Model2VecEmbedder:
    def __init__(self, name: str) -> None:
        # A search is not the place for a download progress bar on stderr;
        # the model is cached after the first load anyway.
        os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
        from model2vec import StaticModel  # optional extra

        self.name = name
        self._model = StaticModel.from_pretrained(name)

    def encode(self, texts: list[str]) -> list[list[float]]:
        return [[float(x) for x in row] for row in self._model.encode(texts)]


_EMBEDDERS: dict[str, Embedder] = {}


def available() -> bool:
    """Whether the optional model package is importable."""
    return importlib.util.find_spec("model2vec") is not None


def enabled() -> bool:
    """``$MEMSHELF_SEMANTIC`` is the kill-switch; anything but off/0/false/no is on."""
    return os.environ.get(SEMANTIC_ENV, "").strip().lower() not in _OFF


def model_name() -> str:
    return os.environ.get(MODEL_ENV, "").strip() or DEFAULT_MODEL


def _load_embedder(name: str) -> Embedder:
    """One model per process: loading takes seconds, a search should not."""
    if name not in _EMBEDDERS:
        if not available():
            raise SemanticError(
                "the semantic sidecar needs the model2vec package: pip install 'memshelf-mcp[semantic]'"
            )
        _EMBEDDERS[name] = Model2VecEmbedder(name)
    return _EMBEDDERS[name]


def index_dir(shelf_root: str | Path) -> Path:
    """Under the state directory, keyed by the resolved shelf path — never in the shelf."""
    root = Path(shelf_root).expanduser().resolve()
    return instances.state_root() / "semantic" / instances._shelf_key(str(root))


def index_path(shelf_root: str | Path) -> Path:
    return index_dir(shelf_root) / INDEX_FILENAME


# --- chunks -----------------------------------------------------------------


@dataclass(frozen=True)
class Chunk:
    address: str
    section: str
    text: str

    @property
    def snippet(self) -> str:
        head = " ".join(self.text.split())
        return head if len(head) <= SNIPPET_CHARS else head[:SNIPPET_CHARS].rstrip() + "…"


def chunks_of(episode: ReuseEpisode) -> list[Chunk]:
    """The meta line (title, description, tags) plus one chunk per H2 section."""
    meta = episode.title
    if episode.description:
        meta += f". {episode.description}"
    if episode.tags:
        meta += ". " + ", ".join(episode.tags)
    out = [Chunk(address=episode.relative_path, section="", text=meta)]
    for piece in _H2_SPLIT.split(episode.body):
        m = _H2_HEADING.match(piece)
        if not m:
            continue
        heading = m.group(1)
        body = piece[m.end() :].strip()
        if not body:
            continue
        text = f"{episode.title} — {heading}\n{body[:CHUNK_CHARS]}"
        out.append(Chunk(address=episode.relative_path, section=heading, text=text))
    return out


def _file_stamp(root: Path, relative_path: str) -> dict:
    stat = (root / relative_path).stat()
    return {"mtime": stat.st_mtime, "size": stat.st_size}


def _normalize(vector: list[float]) -> list[float]:
    norm = math.sqrt(sum(x * x for x in vector))
    return [x / norm for x in vector] if norm else vector


# --- index ------------------------------------------------------------------


@dataclass
class Index:
    model: str
    dim: int
    built: str
    shelf: str
    files: dict[str, dict]  # relative_path -> {"mtime", "size"}
    chunks: list[Chunk]
    vectors: list[list[float]]

    def as_dict(self) -> dict:
        return {
            "version": INDEX_VERSION,
            "model": self.model,
            "dim": self.dim,
            "built": self.built,
            "shelf": self.shelf,
            "files": self.files,
            "chunks": [
                {"address": c.address, "section": c.section, "text": c.text} for c in self.chunks
            ],
            "vectors": [[round(x, 5) for x in v] for v in self.vectors],
        }


def load_index(shelf_root: str | Path) -> Index | None:
    path = index_path(shelf_root)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        logger.warning("semantic index at %s unreadable (%s); ignoring it", path, exc)
        return None
    if data.get("version") != INDEX_VERSION:
        logger.warning("semantic index at %s has version %r; rebuild it", path, data.get("version"))
        return None
    return Index(
        model=data["model"],
        dim=data["dim"],
        built=data["built"],
        shelf=data["shelf"],
        files=data["files"],
        chunks=[Chunk(**c) for c in data["chunks"]],
        vectors=data["vectors"],
    )


def usable(shelf_root: str | Path) -> bool:
    """Enabled, model importable, index on disk: the three things a hybrid search needs."""
    return enabled() and available() and index_path(shelf_root).is_file()


def stale_files(shelf_root: str | Path, index: Index) -> list[str]:
    """Episode files changed, added, or removed since the index was built."""
    root = Path(shelf_root).expanduser().resolve()
    current = {e.relative_path: _file_stamp(root, e.relative_path) for e in load_episodes(root)}
    changed = [p for p, stamp in current.items() if index.files.get(p) != stamp]
    removed = [p for p in index.files if p not in current]
    return sorted(changed + removed)


@dataclass
class BuildReport:
    path: str
    model: str
    files: int
    chunks: int
    reused_files: int
    embedded_chunks: int
    seconds: float

    def as_dict(self) -> dict:
        return self.__dict__.copy()


def build(
    shelf_root: str | Path, *, force: bool = False, embedder: Embedder | None = None
) -> BuildReport:
    """(Re)build the sidecar. Incremental: unchanged files keep their vectors.

    "Unchanged" is ``(mtime, size)`` per file under the same model — the same
    test git uses for its index, and cheap enough to run on every build.
    ``force`` re-embeds everything, which is what a model change needs.
    """
    started = time.monotonic()
    root = Path(shelf_root).expanduser().resolve()
    name = embedder.name if embedder else model_name()
    previous = None if force else load_index(root)
    if previous and previous.model != name:
        previous = None

    episodes = load_episodes(root)
    files: dict[str, dict] = {}
    chunks: list[Chunk] = []
    vectors: list[list[float]] = []
    pending: list[Chunk] = []
    reused = 0
    old_by_file: dict[str, list[tuple[Chunk, list[float]]]] = {}
    if previous:
        for chunk, vector in zip(previous.chunks, previous.vectors, strict=True):
            old_by_file.setdefault(chunk.address, []).append((chunk, vector))

    for episode in episodes:
        stamp = _file_stamp(root, episode.relative_path)
        files[episode.relative_path] = stamp
        if previous and previous.files.get(episode.relative_path) == stamp:
            for chunk, vector in old_by_file.get(episode.relative_path, []):
                chunks.append(chunk)
                vectors.append(vector)
            reused += 1
            continue
        for chunk in chunks_of(episode):
            chunks.append(chunk)
            vectors.append([])  # filled below, in one batch
            pending.append(chunk)

    if pending:
        model = embedder or _load_embedder(name)
        fresh = iter(model.encode([c.text for c in pending]))
        for i, vector in enumerate(vectors):
            if not vector:
                vectors[i] = _normalize(next(fresh))
    dim = len(vectors[0]) if vectors else 0

    index = Index(
        model=name,
        dim=dim,
        built=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        shelf=str(root),
        files=files,
        chunks=chunks,
        vectors=vectors,
    )
    path = index_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(index.as_dict(), ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)
    return BuildReport(
        path=str(path),
        model=name,
        files=len(files),
        chunks=len(chunks),
        reused_files=reused,
        embedded_chunks=len(pending),
        seconds=round(time.monotonic() - started, 2),
    )


def drop(shelf_root: str | Path) -> bool:
    """Delete the sidecar. Returns whether there was one."""
    path = index_path(shelf_root)
    existed = path.is_file()
    if existed:
        path.unlink()
    return existed


def status(shelf_root: str | Path) -> dict:
    index = load_index(shelf_root)
    report: dict = {
        "enabled": enabled(),
        "available": available(),
        "model": model_name(),
        "index_path": str(index_path(shelf_root)),
        "index": None,
    }
    if index:
        report["index"] = {
            "model": index.model,
            "dim": index.dim,
            "built": index.built,
            "files": len(index.files),
            "chunks": len(index.chunks),
            "stale_files": len(stale_files(shelf_root, index)),
        }
    report["usable"] = usable(shelf_root)
    return report


# --- query ------------------------------------------------------------------


@dataclass(frozen=True)
class SemanticHit:
    address: str
    section: str
    score: float  # cosine, best chunk of the episode
    snippet: str


def query(
    shelf_root: str | Path, text: str, *, k: int = 10, embedder: Embedder | None = None
) -> list[SemanticHit]:
    """Nearest episodes by cosine over their best chunk. Needs a built index."""
    index = load_index(shelf_root)
    if index is None:
        raise SemanticError(f"no semantic index for {shelf_root}; run `memshelf semantic build`")
    model = embedder or _load_embedder(index.model)
    q = _normalize(model.encode([text])[0])
    best: dict[str, tuple[float, Chunk]] = {}
    for chunk, vector in zip(index.chunks, index.vectors, strict=True):
        score = sum(a * b for a, b in zip(q, vector, strict=True))
        current = best.get(chunk.address)
        if current is None or score > current[0]:
            best[chunk.address] = (score, chunk)
    ranked = sorted(best.items(), key=lambda kv: (-kv[1][0], kv[0]))
    return [
        SemanticHit(
            address=addr, section=chunk.section, score=round(score, 4), snippet=chunk.snippet
        )
        for addr, (score, chunk) in ranked[:k]
    ]


@dataclass
class Fused:
    address: str
    score: int
    snippet: str
    via: str
    sources: list[str] = field(default_factory=list)


def fuse(grep_hits, semantic_hits: list[SemanticHit], *, max_results: int) -> list[Fused]:
    """Reciprocal rank fusion of the two rankings.

    Rank-based on purpose: grep scores are occurrence counts, cosines live in
    ``[-1, 1]``; neither calibrates against the other, ranks do. The fused
    score is ``round(1000 * Σ 1/(RRF_K + rank))`` so it stays an integer like
    the grep score it replaces in the hit.
    """
    scores: dict[str, float] = {}
    snippets: dict[str, str] = {}
    via: dict[str, set[str]] = {}
    for rank, hit in enumerate(grep_hits, start=1):
        scores[hit.address] = scores.get(hit.address, 0.0) + 1.0 / (RRF_K + rank)
        snippets.setdefault(hit.address, hit.snippet)
        via.setdefault(hit.address, set()).add("grep")
    for rank, hit in enumerate(semantic_hits, start=1):
        scores[hit.address] = scores.get(hit.address, 0.0) + 1.0 / (RRF_K + rank)
        snippets.setdefault(hit.address, hit.snippet)
        via.setdefault(hit.address, set()).add("semantic")
    ordered = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))
    fused = []
    for address, score in ordered[:max_results]:
        sources = sorted(via[address])
        fused.append(
            Fused(
                address=address,
                score=round(1000 * score),
                snippet=snippets[address],
                via="both" if len(sources) == 2 else sources[0],
                sources=sources,
            )
        )
    return fused
