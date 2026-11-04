"""Hybrid / semantic / keyword search (deskbrain/search.py) and the /api/search modes.

Tests that need the embedding model are skipped if it isn't downloaded yet.
"""

import shutil

import pytest
from fastapi.testclient import TestClient

from deskbrain.app import create_app
from deskbrain.bm25 import BM25Index
from deskbrain.embed import model_cached
from deskbrain.search import HybridSearcher, rrf, vectors_base
from deskbrain.store import Index

needs_model = pytest.mark.skipif(not model_cached(), reason="embedding model not downloaded")


def _searcher(root, tmp_path, semantic=None):
    with Index(tmp_path / "ix.sqlite", root) as ix:
        ix.update()
        chunks = ix.chunks()
    hs = HybridSearcher(vectors_base(tmp_path / "ix.sqlite"), semantic=semantic)
    hs.update(BM25Index(chunks), chunks)
    return hs


# --- no model needed -------------------------------------------------------

def test_rrf_math():
    f = rrf([["a", "b", "c"], ["b", "d"]], k=60)
    assert f["b"] == pytest.approx(1 / 62 + 1 / 61)
    assert f["a"] == pytest.approx(1 / 61) and f["d"] == pytest.approx(1 / 62)
    assert max(f, key=f.get) == "b"           # in both lists beats first in one


def test_vectors_base_next_to_db(tmp_path):
    assert vectors_base(tmp_path / "index.sqlite") == tmp_path / "index-vectors"


def test_keyword_only_fallback(desk, tmp_path):
    hs = _searcher(desk, tmp_path, semantic=False)
    assert not hs.available and hs.default_mode() == "keyword"
    assert hs.resolve_mode("semantic") == "keyword" and hs.resolve_mode("hybrid") == "keyword"
    hits = hs.search("sourdough starter", mode="hybrid")
    assert hits[0].chunk.rel == "02-cooking/bread.md" and hits[0].match == "keyword"
    assert not (tmp_path / "ix-vectors.npy").exists()     # nothing embedded


def test_api_keyword_only_app(desk, tmp_path):
    app = create_app(desk, tmp_path / "ix.sqlite", semantic=False)
    with TestClient(app) as c:
        h = c.get("/api/health").json()
        assert h["semantic"] is False and h["default_mode"] == "keyword"
        j = c.get("/api/search", params={"q": "sourdough", "mode": "semantic"}).json()
        assert j["mode"] == "keyword" and j["hits"][0]["match"] == "keyword"
        assert c.get("/api/search", params={"q": "x", "mode": "bogus"}).status_code == 422
    app.state.desk.close()


# --- with the model --------------------------------------------------------

@pytest.fixture(scope="module")
def hs(tmp_path_factory):
    from tests.conftest import FIXTURE_DESK
    return _searcher(FIXTURE_DESK, tmp_path_factory.mktemp("hs"))


PARAPHRASES = [
    ("my loaf stays flat and does not grow", "02-cooking/bread.md", "dough not rise"),
    ("leaves going pale on the tomato plant", "01-garden/tomatoes.md", "yellow"),
    ("how much light do young plants need", "01-garden/tomatoes.md", "Findings"),
]


@needs_model
@pytest.mark.parametrize("q, rel, head", PARAPHRASES)
@pytest.mark.parametrize("mode", ["semantic", "hybrid"])
def test_paraphrase_top_hit(hs, mode, q, rel, head):
    top = hs.search(q, mode=mode, k=5)[0]
    assert top.chunk.rel == rel and head in top.chunk.heading, (top.chunk.rel, top.chunk.heading)


@needs_model
def test_hybrid_beats_keyword_on_paraphrase(hs):
    """Keyword search latches onto a stray word; hybrid and semantic find the bread doubt."""
    q = PARAPHRASES[0][0]
    assert hs.search(q, mode="keyword")[0].chunk.rel != "02-cooking/bread.md"
    assert hs.search(q, mode="hybrid")[0].chunk.rel == "02-cooking/bread.md"


@needs_model
def test_exact_keyword_query_still_first_in_hybrid(hs):
    for q, rel in (("sourdough starter", "02-cooking/bread.md"), ("guitar chords", "03-music/guitar.md")):
        top = hs.search(q, mode="hybrid")[0]
        assert top.chunk.rel == rel and top.match == "both"


@needs_model
@pytest.mark.parametrize("q", ["zzqqxx", "asdf qwerty", "quantum chromodynamics lattice"])
def test_gibberish_gives_nothing_in_hybrid(hs, q):
    assert hs.search(q, mode="hybrid") == []


@needs_model
def test_hit_fields_and_per_page_cap(hs):
    hits = hs.search("tomato plants", mode="hybrid", k=50)
    per = {}
    for h in hits:
        per[h.chunk.rel] = per.get(h.chunk.rel, 0) + 1
        assert h.match in ("keyword", "semantic", "both")
        assert (h.bm25 is not None) == (h.match != "semantic")
        assert (h.similarity is not None) == (h.match != "keyword")
    assert max(per.values()) <= 2
    assert len(hs.search("tomato plants", mode="hybrid", k=50, per_page=None)) >= len(hits)
    scores = [h.score for h in hits]
    assert scores == sorted(scores, reverse=True)


@needs_model
def test_semantic_hits_highlight_shared_words(hs):
    top = hs.search("leaves going pale on the tomato plant", mode="semantic")[0]
    assert "leav" in top.terms or "tomato" in top.terms


@needs_model
def test_api_modes(desk, tmp_path):
    app = create_app(desk, tmp_path / "ix.sqlite")
    with TestClient(app) as c:
        h = c.get("/api/health").json()
        assert h["semantic"] is True and h["default_mode"] == "hybrid"
        for mode in ("", "hybrid", "keyword", "semantic"):
            j = c.get("/api/search", params={"q": "my loaf stays flat", "mode": mode}).json()
            assert j["mode"] == (mode or "hybrid")
            if mode == "keyword":
                assert j["hits"] == []          # no shared words: only meaning search finds it
                continue
            assert j["hits"][0]["rel"] == "02-cooking/bread.md", mode
            assert {"match", "bm25", "similarity", "snippet"} <= set(j["hits"][0])
        j = c.get("/api/search", params={"q": "my loaf stays flat and does not grow"}).json()
        assert j["hits"][0]["rel"] == "02-cooking/bread.md"
        assert c.get("/api/search", params={"q": "zzqqxx"}).json()["hits"] == []
    app.state.desk.close()


@needs_model
def test_reindex_embeds_only_changed_sections(desk, tmp_path):
    d = tmp_path / "desk"
    shutil.copytree(desk, d)
    app = create_app(d, tmp_path / "ix.sqlite")
    state = app.state.desk
    n = state.index.counts()["chunks"]
    assert state.last_embed == {"embedded": n, "kept": 0, "removed": 0}
    with TestClient(app) as c:
        c.post("/api/reindex")
        assert state.last_embed == {"embedded": n, "kept": 0, "removed": 0}   # nothing changed: no sync
        p = d / "02-cooking" / "bread.md"
        p.write_text(p.read_text() + "\n\n## Wombat corner\n\nA small marsupial that digs burrows.\n")
        c.post("/api/reindex")
        assert state.last_embed["embedded"] == 1 and state.last_embed["removed"] == 0
        # The new section is found by meaning alone.
        j = c.get("/api/search", params={"q": "burrowing australian animal", "mode": "semantic"}).json()
        assert j["hits"][0]["section_slug"] == "wombat-corner"
    state.close()
    # A new app over the same data re-embeds nothing (vectors are on disk).
    app2 = create_app(d, tmp_path / "ix.sqlite")
    assert app2.state.desk.last_embed["embedded"] == 0
    app2.state.desk.close()
