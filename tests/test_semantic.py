"""Semantic sidecar (#17): index outside the shelf, hybrid search, kill-switch.

No model in CI: a deterministic bag-of-trigrams embedder stands in for
model2vec, so the tests exercise the index, the fusion and the CLI, not the
embedding quality. The one thing a fake cannot show — that paraphrases hit —
is measured on the dogfood shelf and recorded in the PR / ROADMAP.
"""

import hashlib
import json
import os
import subprocess

import pytest

pytest.importorskip("docshelf_mcp")

from docshelf_mcp.core.shelf import Shelf  # noqa: E402

from memshelf_mcp import instances, tools  # noqa: E402
from memshelf_mcp.cli import main  # noqa: E402
from memshelf_mcp.core import recall, semantic  # noqa: E402
from memshelf_mcp.core.rebuild import rebuild  # noqa: E402
from memshelf_mcp.core.shelve import shelve  # noqa: E402

AUTH = "2026-06-22-auth"
ROTATION = "2026-08-03-secret-rotation"
LONE = "2026-09-01-unrelated"
DIM = 64


class FakeEmbedder:
    """Hashed character trigrams, L2-normalised. Similar spelling → similar vector."""

    name = "fake/trigrams"

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def encode(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        out = []
        for text in texts:
            vec = [0.0] * DIM
            padded = f"  {text.lower()}  "
            for i in range(len(padded) - 2):
                h = hashlib.md5(padded[i : i + 3].encode("utf-8")).digest()
                vec[h[0] % DIM] += 1.0
            out.append(vec)
        return out


@pytest.fixture
def fake(monkeypatch):
    embedder = FakeEmbedder()
    monkeypatch.setattr(semantic, "available", lambda: True)
    monkeypatch.setattr(semantic, "_load_embedder", lambda name: embedder)
    monkeypatch.delenv(semantic.SEMANTIC_ENV, raising=False)
    monkeypatch.setenv(semantic.MODEL_ENV, embedder.name)
    return embedder


def _shelf(root):
    Shelf(root).init(name="t", default_categories=["topics", "research", "sessions"])
    subprocess.run(["git", "-C", str(root), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.email", "t@t.test"], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.name", "tester"], check=True)
    shelve(
        root,
        slug=AUTH,
        kind="topic",
        digest="The auth refactor chose JWT over cookie sessions.",
        sections={"Decisions": "JWT chosen.", "Open threads": "rotate the shared secret"},
        display_title="Auth refactor",
        description="JWT over cookie sessions",
        tags=["auth", "jwt"],
        date="2026-06-22",
    )
    shelve(
        root,
        slug=ROTATION,
        kind="session",
        digest="Rotated the signing secret; cadence every thirty days.",
        sections={"Timeline": "one afternoon", "Open threads": "automate the rotation"},
        display_title="Secret rotation",
        description="rotation cadence settled",
        tags=["ops"],
        date="2026-08-03",
    )
    shelve(
        root,
        slug=LONE,
        kind="session",
        digest="Kitchen renovation notes, nothing technical.",
        sections={"Timeline": "n/a", "Open threads": "none"},
        display_title="Kitchen",
        description="tiles and grout",
        date="2026-09-01",
    )
    rebuild(root)
    return root


def _episode_file(root, episode_id):
    return next(p for p in (root / "docs").rglob(f"{episode_id}.md"))


# --- chunks / index placement -------------------------------------------------


def test_chunks_meta_plus_one_per_nonempty_section(tmp_path):
    from memshelf_mcp.core.reuse import load_episodes

    root = _shelf(tmp_path)
    auth = next(e for e in load_episodes(root) if e.id == AUTH)
    chunks = semantic.chunks_of(auth)
    assert chunks[0].section == "" and chunks[0].address == auth.relative_path
    assert "Auth refactor" in chunks[0].text and "auth, jwt" in chunks[0].text
    assert [c.section for c in chunks[1:]] == ["Digest", "Decisions", "Open threads"]
    assert chunks[2].text.startswith("Auth refactor — Decisions\nJWT chosen.")
    assert len(semantic.Chunk("a", "s", "x " * 500).snippet) <= semantic.SNIPPET_CHARS + 1


def test_index_lives_under_state_dir_not_in_shelf(tmp_path, fake):
    root = _shelf(tmp_path)
    path = semantic.index_path(root)
    assert not path.is_relative_to(root)
    assert path.is_relative_to(os.environ[instances.STATE_DIR_ENV])
    assert semantic.index_dir(root) == semantic.index_dir(str(root) + "/")


# --- build / status / drop ----------------------------------------------------


def test_build_status_drop_roundtrip(tmp_path, fake):
    root = _shelf(tmp_path)
    assert semantic.status(root)["usable"] is False
    assert semantic.status(root)["index"] is None

    report = semantic.build(root)
    assert report.files == 3 and report.reused_files == 0
    assert report.chunks == report.embedded_chunks == 3 + 3 + 3 + 3  # meta + 2..3 sections each
    assert report.model == fake.name and len(fake.calls) == 1

    data = json.loads(semantic.index_path(root).read_text(encoding="utf-8"))
    assert data["version"] == semantic.INDEX_VERSION and data["dim"] == DIM
    assert set(data["files"]) == {
        f"docs/topics/{AUTH}.md",
        f"docs/sessions/{ROTATION}.md",
        f"docs/sessions/{LONE}.md",
    }
    st = semantic.status(root)
    assert st["usable"] is True and st["index"]["chunks"] == 12 and st["index"]["stale_files"] == 0

    assert semantic.drop(root) is True
    assert semantic.drop(root) is False
    assert semantic.status(root)["usable"] is False


def test_build_is_incremental_by_file_stamp(tmp_path, fake):
    root = _shelf(tmp_path)
    semantic.build(root)
    fake.calls.clear()

    again = semantic.build(root)
    assert again.reused_files == 3 and again.embedded_chunks == 0 and fake.calls == []

    path = _episode_file(root, LONE)
    path.write_text(path.read_text(encoding="utf-8") + "\n## Artifacts\n\n- a photo\n", "utf-8")
    assert semantic.status(root)["index"]["stale_files"] == 1
    third = semantic.build(root)
    assert third.reused_files == 2 and third.embedded_chunks == 5  # meta + 4 sections
    assert [t for call in fake.calls for t in call][0].startswith("Kitchen")
    assert semantic.status(root)["index"]["stale_files"] == 0

    forced = semantic.build(root, force=True)
    assert forced.reused_files == 0 and forced.embedded_chunks == forced.chunks == 13


def test_build_ignores_index_from_another_model(tmp_path, fake, monkeypatch):
    root = _shelf(tmp_path)
    semantic.build(root)
    monkeypatch.setenv(semantic.MODEL_ENV, "other/model")
    fake.name = "other/model"
    report = semantic.build(root)
    assert report.reused_files == 0 and report.model == "other/model"


def test_unreadable_or_old_index_is_ignored(tmp_path, fake):
    root = _shelf(tmp_path)
    semantic.build(root)
    path = semantic.index_path(root)
    path.write_text("{not json", encoding="utf-8")
    assert semantic.load_index(root) is None
    path.write_text(json.dumps({"version": 99}), encoding="utf-8")
    assert semantic.load_index(root) is None


# --- query / fuse -------------------------------------------------------------


def test_query_returns_best_chunk_per_episode(tmp_path, fake):
    root = _shelf(tmp_path)
    with pytest.raises(semantic.SemanticError, match="semantic build"):
        semantic.query(root, "anything")
    semantic.build(root)
    hits = semantic.query(root, "kitchen renovation tiles", k=2)
    assert [h.address for h in hits][0].endswith(f"{LONE}.md")
    assert len(hits) == 2 and len({h.address for h in hits}) == 2
    assert -1.0 <= hits[0].score <= 1.0 and hits[0].snippet


def test_fuse_reciprocal_rank():
    grep = [recall.SearchHit("a", 5, "ga"), recall.SearchHit("b", 2, "gb")]
    sem = [
        semantic.SemanticHit("b", "", 0.9, "sb"),
        semantic.SemanticHit("c", "", 0.8, "sc"),
        semantic.SemanticHit("a", "", 0.1, "sa"),
    ]
    fused = semantic.fuse(grep, sem, max_results=10)
    k = semantic.RRF_K
    assert [f.address for f in fused] == ["b", "a", "c"]
    assert fused[0].score == round(1000 * (1 / (k + 2) + 1 / (k + 1)))
    assert fused[0].via == "both" and fused[0].snippet == "gb"  # grep snippet wins
    assert fused[2].via == "semantic" and fused[2].snippet == "sc"
    assert [f.address for f in semantic.fuse(grep, sem, max_results=1)] == ["b"]
    assert semantic.fuse([], [], max_results=3) == []


# --- search() integration -----------------------------------------------------


def test_search_is_plain_grep_without_index(tmp_path, fake):
    root = _shelf(tmp_path)
    hits = recall.search(root, "JWT")
    assert hits and all(h.via == "grep" for h in hits)
    with pytest.raises(semantic.SemanticError):
        recall.search(root, "JWT", semantic=True)


def test_search_goes_hybrid_when_index_exists(tmp_path, fake):
    root = _shelf(tmp_path)
    semantic.build(root)
    # A paraphrase grep cannot see: no episode contains "renovating".
    assert recall.search(root, "renovating", semantic=False) == []
    hits = recall.search(root, "renovating a kitchen", max_results=2)
    assert hits[0].address.endswith(f"{LONE}.md") and hits[0].via == "semantic"
    both = recall.search(root, "JWT auth refactor")
    assert both[0].address.endswith(f"{AUTH}.md") and both[0].via == "both"
    assert all(isinstance(h.score, int) for h in both)


def test_kill_switch_ignores_a_built_index(tmp_path, fake, monkeypatch):
    root = _shelf(tmp_path)
    semantic.build(root)
    monkeypatch.setenv(semantic.SEMANTIC_ENV, "off")
    assert semantic.enabled() is False and semantic.usable(root) is False
    assert recall.search(root, "renovating a kitchen") == []
    assert semantic.status(root)["usable"] is False and semantic.status(root)["index"]
    for value in ("0", "false", "NO"):
        monkeypatch.setenv(semantic.SEMANTIC_ENV, value)
        assert semantic.enabled() is False
    monkeypatch.setenv(semantic.SEMANTIC_ENV, "on")
    assert semantic.enabled() is True


def test_run_search_reports_mode_and_via(tmp_path, fake, monkeypatch):
    root = _shelf(tmp_path)
    params = tools.SearchInput(shelf_path=str(root), query="JWT")
    before = tools.run_search(params)
    assert before["mode"] == "grep" and before["hits"][0]["via"] == "grep"
    semantic.build(root)
    after = tools.run_search(params)
    assert after["mode"] == "hybrid" and after["hits"][0]["via"] in {"both", "grep"}
    assert set(after["hits"][0]) == {"address", "score", "snippet", "via"}
    monkeypatch.setenv(semantic.SEMANTIC_ENV, "off")
    assert tools.run_search(params)["mode"] == "grep"


def test_load_embedder_without_package_is_a_clean_error(monkeypatch):
    monkeypatch.setattr(semantic, "available", lambda: False)
    semantic._EMBEDDERS.pop("nope/model", None)
    with pytest.raises(semantic.SemanticError, match="memshelf-mcp\\[semantic\\]"):
        semantic._load_embedder("nope/model")


# --- CLI ----------------------------------------------------------------------


def test_cli_semantic_build_status_drop(tmp_path, fake, capsys):
    root = str(_shelf(tmp_path))
    assert main(["semantic", "status", "--shelf", root]) == 0
    assert json.loads(capsys.readouterr().out)["usable"] is False
    assert main(["semantic", "build", "--shelf", root]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["files"] == 3 and report["embedded_chunks"] == 12
    assert main(["semantic", "build", "--shelf", root, "--force"]) == 0
    assert json.loads(capsys.readouterr().out)["reused_files"] == 0
    assert main(["semantic", "status", "--shelf", root]) == 0
    assert json.loads(capsys.readouterr().out)["index"]["chunks"] == 12
    assert main(["semantic", "drop", "--shelf", root]) == 0
    assert json.loads(capsys.readouterr().out)["dropped"] is True


def test_cli_semantic_build_without_model_fails_cleanly(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(semantic, "available", lambda: False)
    monkeypatch.setenv(semantic.MODEL_ENV, "nope/model")
    root = str(_shelf(tmp_path))
    assert main(["semantic", "build", "--shelf", root]) == 1
    assert "memshelf-mcp[semantic]" in capsys.readouterr().err


def test_cli_search_bench(tmp_path, fake, capsys):
    root = str(_shelf(tmp_path))
    queries = tmp_path / "q.tsv"
    queries.write_text(
        "# query<TAB>expected\n\n"
        f"JWT\t{AUTH}\n"
        f"renovating a kitchen\t{LONE}\n"
        f"zzz nothing matches this\t{ROTATION}\n",
        encoding="utf-8",
    )
    assert main(["search-bench", "--shelf", root, "--queries", str(queries)]) == 1
    assert "semantic build" in capsys.readouterr().err
    semantic.build(root)
    assert main(["search-bench", "--shelf", root, "--queries", str(queries), "--verbose"]) == 0
    out = capsys.readouterr().out
    assert "queries: 3  k: 5" in out
    grep_line = next(line for line in out.splitlines() if line.startswith("grep"))
    hybrid_line = next(line for line in out.splitlines() if line.startswith("hybrid"))
    assert grep_line.split()[-1] == "2"  # paraphrase + nonsense missed
    assert hybrid_line.split()[-1] in {"0", "1"}  # the fake may or may not rescue the nonsense
    assert "miss[grep]\trenovating a kitchen" in out

    bad = tmp_path / "bad.tsv"
    bad.write_text("no tab here\n", encoding="utf-8")
    with pytest.raises(SystemExit, match="query<TAB>expected-id"):
        main(["search-bench", "--shelf", root, "--queries", str(bad)])
    empty = tmp_path / "empty.tsv"
    empty.write_text("# only a comment\n", encoding="utf-8")
    assert main(["search-bench", "--shelf", root, "--queries", str(empty)]) == 2


@pytest.mark.skipif(
    not os.environ.get("MEMSHELF_TEST_REAL_MODEL"), reason="set MEMSHELF_TEST_REAL_MODEL=1"
)
def test_real_model_finds_a_paraphrase(tmp_path, monkeypatch):
    pytest.importorskip("model2vec")
    monkeypatch.delenv(semantic.SEMANTIC_ENV, raising=False)
    monkeypatch.delenv(semantic.MODEL_ENV, raising=False)
    root = _shelf(tmp_path)
    semantic.build(root)
    hits = recall.search(root, "ремонт кухни плитка", max_results=1)
    assert hits and hits[0].address.endswith(f"{LONE}.md")
