"""Day 17: /api/related — semantic (or keyword fallback) neighbours per page, privacy-filtered."""

import pytest
from fastapi.testclient import TestClient

from deskbrain.app import create_app
from deskbrain.embed import model_cached
from deskbrain.related import link_neighbours
from deskbrain.scanner import is_allowed

PAGES = ["01-garden/tomatoes.md", "01-garden/greenhouse/README.md", "02-cooking/bread.md",
         "03-music/guitar.md", "03-music/practice.md", "README.md"]
# page -> a page that must rank in its top 3
EXPECT = {"01-garden/greenhouse/README.md": "01-garden/tomatoes.md",
          "03-music/guitar.md": "03-music/practice.md",
          "02-cooking/bread.md": "01-garden/tomatoes.md"}


def _client(desk, tmp_path, semantic):
    return TestClient(create_app(desk, tmp_path / "ix.sqlite", semantic=semantic))


@pytest.fixture(params=["keyword", "semantic"])
def client(request, desk, tmp_path):
    if request.param == "semantic" and not model_cached():
        pytest.skip("embedding model not downloaded")
    return request.param, _client(desk, tmp_path, request.param == "semantic")


def test_related_excludes_self_and_private(client):
    mode, c = client
    for rel in PAGES:
        r = c.get("/api/related", params={"rel": rel, "k": 20})
        assert r.status_code == 200, rel
        d = r.json()
        assert d["method"] == mode
        rels = [x["rel"] for x in d["related"]]
        assert rel not in rels
        assert all(is_allowed(x) for x in rels)
        assert not any(x.startswith(("activity/", "Personal/", "tools/", "_templates/")) for x in rels)
        scores = [x["score"] for x in d["related"]]
        assert scores == sorted(scores, reverse=True)
        assert all(x["title"] for x in d["related"])


def test_expected_neighbour_top3(client):
    _, c = client
    for rel, want in EXPECT.items():
        top = [x["rel"] for x in c.get("/api/related", params={"rel": rel}).json()["related"][:3]]
        assert want in top, (rel, top)


def test_k_limit_and_errors(desk, tmp_path):
    c = _client(desk, tmp_path, False)
    assert len(c.get("/api/related", params={"rel": "03-music/practice.md", "k": 1}).json()["related"]) == 1
    for bad in ["Personal/diary.md", "activity/2026-11-01.md", "nope.md", "../etc/passwd", "tools/notes.md"]:
        assert c.get("/api/related", params={"rel": bad}).status_code == 404, bad
    assert c.get("/api/related").status_code == 422
    assert c.get("/api/related", params={"rel": "README.md", "k": 0}).status_code == 422


def test_link_neighbours_undirected_and_pages_only():
    nb = link_neighbours([("a.md", "b.md"), ("a.md", "a.md"), ("a.md", "x.pdf"), ("c.md", None)])
    assert nb["a.md"] == {"b.md"} and nb["b.md"] == {"a.md"} and "c.md" not in nb
