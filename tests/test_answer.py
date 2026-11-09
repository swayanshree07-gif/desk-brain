"""Day 20: extractive answer — sentences verbatim in their cited chunk, "don't know" on weak scores."""

import pytest
from fastapi.testclient import TestClient

from deskbrain.app import create_app
from deskbrain.ask import sentences
from deskbrain.embed import model_cached

ANSWERS = {
    "How deep should I plant tomato seeds?": "6 mm deep",
    "how much light do seedlings need": "14 hours of light",
}
UNKNOWN = ["zzqqxx blorf", "what is the capital of france", "quantum chromodynamics lattice",
           "why are the lower leaves yellow"]   # last one: an open doubt, no answer written yet


@pytest.fixture(params=["keyword", "semantic"])
def client(request, desk, tmp_path):
    if request.param == "semantic" and not model_cached():
        pytest.skip("embedding model not downloaded")
    return TestClient(create_app(desk, tmp_path / "ix.sqlite", semantic=request.param == "semantic"))


def test_answers_found_and_verbatim(client):
    for q, want in ANSWERS.items():
        d = client.get("/api/ask", params={"q": q}).json()
        assert d["known"], (q, d["best_score"])
        assert any(want in a["text"] for a in d["answer"]), d["answer"]
        assert 1 <= len(d["answer"]) <= 3


def test_every_sentence_in_its_chunk(client):
    for q in list(ANSWERS) + UNKNOWN + ["bread dough", "guitar practice", "seeds", "water"]:
        d = client.get("/api/ask", params={"q": q}).json()
        by_n = {c["n"]: c for c in d["citations"]}
        ns = [a["n"] for a in d["answer"]]
        assert ns == sorted(ns)
        for a in d["answer"]:
            assert a["n"] in by_n
            assert a["text"] in by_n[a["n"]]["text"]
            assert a["score"] >= 0.6 * d["best_score"] - 1e-9
        assert d["known"] == bool(d["answer"])


def test_dont_know(client):
    for q in UNKNOWN + ["   "]:
        d = client.get("/api/ask", params={"q": q}).json()
        assert d["known"] is False and d["answer"] == [], (q, d.get("best_score"))


def test_sentence_splitter():
    text = ("## Heading here is skipped\n<!-- a comment\nspanning lines -->\n"
            "- **2026-10-05** · Seedlings need 14 hours of light. Water them daily please.\n"
            "```\ncode line inside fence\n```\n**A:** About 6 mm deep.\n| a | b c d | e |\n|---|---|---|\nok\n")
    got = sentences(text)
    assert got == ["Seedlings need 14 hours of light.", "Water them daily please.",
                   "About 6 mm deep.", "a | b c d | e"]
    assert all(s in text for s in got)
