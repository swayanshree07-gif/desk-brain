"""Day 19: /api/ask retrieval — right pages, citations point at real chunks, privacy."""

import pytest
from fastapi.testclient import TestClient

from deskbrain.app import create_app
from deskbrain.embed import model_cached
from deskbrain.scanner import is_allowed, read_page

QUESTIONS = {
    "How deep should I plant tomato seeds?": "01-garden/tomatoes.md",
    "why are the lower leaves yellow": "01-garden/tomatoes.md",
    "how much light do seedlings need": "01-garden/tomatoes.md",
}


@pytest.fixture(params=["keyword", "semantic"])
def client(request, desk, tmp_path):
    if request.param == "semantic" and not model_cached():
        pytest.skip("embedding model not downloaded")
    return request.param, TestClient(create_app(desk, tmp_path / "ix.sqlite", semantic=request.param == "semantic"))


def test_right_page_first(client):
    _, c = client
    for q, want in QUESTIONS.items():
        d = c.get("/api/ask", params={"q": q}).json()
        assert d["pages"] and d["pages"][0]["rel"] == want, (q, d["pages"])
        assert d["citations"][0]["rel"] == want


def test_citations_real_and_numbered(client, desk):
    _, c = client
    for q in list(QUESTIONS) + ["bread dough", "guitar practice"]:
        d = c.get("/api/ask", params={"q": q}).json()
        cits = d["citations"]
        assert [x["n"] for x in cits] == list(range(1, len(cits) + 1))
        assert sorted(n for p in d["pages"] for n in p["citations"]) == [x["n"] for x in cits]
        assert len(d["pages"]) <= 4 and all(len(p["citations"]) <= 2 for p in d["pages"])
        for x in cits:
            assert is_allowed(x["rel"])
            lines = read_page(desk, x["rel"]).splitlines()
            if x["heading"]:
                assert x["heading"] in lines[x["line"] - 1]
            body = x["text"].strip().splitlines()
            if body:
                assert body[-1].strip() in "\n".join(lines[x["line"] - 1:x["end_line"]])


def test_empty_and_private(client):
    _, c = client
    assert c.get("/api/ask", params={"q": "  "}).json()["citations"] == []
    assert c.get("/api/ask", params={"q": "x", "mode": "bogus"}).status_code == 422
    for q in ["diary", "secret", "canary", "day log"]:
        d = c.get("/api/ask", params={"q": q}).json()
        assert all(not x["rel"].startswith(("Personal/", "activity/", "tools/", "_templates/")) for x in d["citations"])
