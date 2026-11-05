"""Doubts board data (deskbrain/doubts.py) and /api/doubts on the fixture desk."""

import re
import shutil

import pytest
from fastapi.testclient import TestClient

from deskbrain.app import create_app
from deskbrain.doubts import doubt_id, list_doubts
from deskbrain.store import Index


@pytest.fixture
def index(desk, tmp_path):
    with Index(tmp_path / "ix.sqlite", desk) as ix:
        ix.update()
        yield ix


def test_all_doubts_and_order(index):
    r = list_doubts(index)
    qs = [(d["status"], d["question"]) for d in r["doubts"]]
    assert qs == [
        ("open", "How often should I change the strings?"),      # newest asked first
        ("open", "Why do the lower leaves turn yellow?"),
        ("answered", "What is a capo for?"),                     # 2026-10-28 20:05
        ("answered", "Why does my dough not rise?"),             # 2026-10-28 18:00
        ("answered", "How deep should seeds be planted?"),       # asked 2026-10-01 (date taken from its Context line): oldest
    ]
    assert r["counts"] == {"open": 2, "answered": 3} and r["total"] == 5
    assert r["areas"] == ["01-garden", "02-cooking", "03-music"]


def test_fields(index):
    d = {x["question"]: x for x in list_doubts(index)["doubts"]}
    capo = d["What is a capo for?"]
    assert capo["rel"] == "03-music/guitar.md" and capo["page_title"] == "Guitar" and capo["area"] == "03-music"
    assert capo["asked"] == "2026-10-28 20:05" and capo["answer"].startswith("It clamps across the frets")
    assert capo["anchor"] == "-q-what-is-a-capo-for".strip("-")
    seeds = d["How deep should seeds be planted?"]
    assert "6 mm" in seeds["answer"] and "Keep them warm" in seeds["answer"]
    assert seeds["context"].startswith("first sowing")
    yellow = d["Why do the lower leaves turn yellow?"]
    assert yellow["answer"] is None and yellow["line"] == 15
    assert len({x["id"] for x in d.values()}) == 5


def test_filters(index):
    assert [d["status"] for d in list_doubts(index, status="open")["doubts"]] == ["open", "open"]
    assert len(list_doubts(index, status="answered")["doubts"]) == 3
    assert {d["rel"] for d in list_doubts(index, area="01-garden")["doubts"]} == {"01-garden/tomatoes.md"}
    assert list_doubts(index, area="nope")["doubts"] == []
    hits = list_doubts(index, q="strings")["doubts"]               # in the question
    assert [d["question"] for d in hits] == ["How often should I change the strings?"]
    hits = list_doubts(index, q="frets pitch")["doubts"]           # all words, in the answer
    assert [d["question"] for d in hits] == ["What is a capo for?"]
    assert list_doubts(index, q="frets bread")["doubts"] == []
    assert list_doubts(index, q="cherry")["doubts"][0]["question"].startswith("Why do the lower")  # context
    # counts/areas describe the whole board, not the filtered view
    assert list_doubts(index, status="open", q="strings")["counts"] == {"open": 2, "answered": 3}


def test_id_survives_answer_and_line_move(desk, tmp_path):
    d = tmp_path / "desk"
    shutil.copytree(desk, d)
    g = d / "03-music" / "guitar.md"
    text = g.read_text()
    text = text.replace("Notes on learning", "Extra line.\n\nNotes on learning")
    text = text.replace("### 🔴 Q: How often should I change the strings?",
                        "### 🟢 Q: How often should I change the strings?")
    text = text.replace("**Context:** strings sound dull after a month",
                        "**Context:** strings sound dull after a month\n**A:** Every 2–3 months.")
    with Index(tmp_path / "a.sqlite", desk) as ix:
        ix.update()
        before = {x["question"]: x for x in list_doubts(ix)["doubts"]}
    g.write_text(text)
    with Index(tmp_path / "b.sqlite", d) as ix:
        ix.update()
        after = {x["question"]: x for x in list_doubts(ix)["doubts"]}
    q = "How often should I change the strings?"
    assert after[q]["status"] == "answered" and after[q]["answer"] == "Every 2–3 months."
    assert after[q]["line"] == before[q]["line"] + 2
    assert after[q]["id"] == before[q]["id"] == doubt_id("03-music/guitar.md", q)


def test_api_doubts(desk, tmp_path):
    app = create_app(desk, tmp_path / "ix.sqlite", semantic=False)
    with TestClient(app) as c:
        j = c.get("/api/doubts").json()
        assert j["counts"] == {"open": 2, "answered": 3}
        assert len(c.get("/api/doubts", params={"status": "open"}).json()["doubts"]) == 2
        assert c.get("/api/doubts", params={"status": "bogus"}).status_code == 422
        r = c.get("/api/doubts", params={"area": "03-music", "q": "capo"}).json()
        assert [d["question"] for d in r["doubts"]] == ["What is a capo for?"]
        # every anchor exists as a heading id in the rendered page
        for d in j["doubts"]:
            html = c.get("/api/page", params={"rel": d["rel"]}).json()["html"]
            assert re.search(rf'id="{re.escape(d["anchor"])}"', html), d
        assert 'href="#/doubts"' in c.get("/").text and "showDoubts" in c.get("/static/app.js").text
        text = c.get("/api/doubts").text
        for bad in ("activity", "Personal", "diary", "_template", "tools/", "question?"):
            assert bad not in text, bad
    app.state.desk.close()
