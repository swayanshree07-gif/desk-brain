"""Day 22: card lifecycle — edits keep history, changed ids carry state over, gone cards retire."""

import shutil
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from deskbrain.app import create_app
from deskbrain.cards import build_cards
from deskbrain.review import ReviewStore, due_queue, similarity
from deskbrain.store import Index

D0 = date(2026, 11, 7)
BREAD = "02-cooking/bread.md"
OVERNIGHT = "An overnight cold proof makes the crust darker."


@pytest.fixture
def env(desk, tmp_path):
    d = tmp_path / "desk"
    shutil.copytree(desk, d)
    ix = Index(tmp_path / "data" / "index.sqlite", d)
    ix.update()
    rs = ReviewStore(tmp_path / "data" / "flashcards.sqlite")
    yield d, ix, rs
    rs.close()
    ix.close()


def edit(path, old, new):
    text = path.read_text()
    assert old in text
    path.write_text(text.replace(old, new))


def find(cards, pred):
    hits = [c for c in cards if pred(c)]
    assert len(hits) == 1, hits
    return hits[0]


def overnight(c):
    return c["rel"] == BREAD and c["date"] == "2026-10-29"


def test_finding_edit_carries_state_over(env):
    d, ix, rs = env
    c = find(build_cards(ix), overnight)
    rs.grade(c["id"], 4, D0, card=c)
    rs.grade(c["id"], 5, D0 + timedelta(1), card=c)
    before = rs.get(c["id"])
    edit(d / BREAD, OVERNIGHT, "An overnight cold proof makes the crust a lot darker.")
    ix.update()
    cards = build_cards(ix)
    c2 = find(cards, overnight)
    assert c2["id"] != c["id"]                  # text edit → new id ...
    ch = rs.reconcile(cards, D0 + timedelta(2))
    assert ch["renamed"] == 1 and ch["retired"] == 0
    assert rs.get(c["id"]) is None
    assert rs.get(c2["id"]) == before          # ... but the schedule moved with it
    assert [g["grade"] for g in rs.history(c2["id"])] == [4, 5]
    ev = rs.events(c2["id"])
    assert [e["event"] for e in ev] == ["renamed", "edited"]
    assert ev[0]["old_id"] == c["id"] and OVERNIGHT in ev[1]["back"]
    # idempotent
    assert rs.reconcile(cards, D0 + timedelta(2)) == {"edited": 0, "renamed": 0, "retired": 0, "revived": 0}


def test_answer_edit_same_id_logs_edit(env):
    d, ix, rs = env
    q = find(build_cards(ix), lambda c: c["kind"] == "qa" and c["rel"] == BREAD)
    rs.grade(q["id"], 3, D0, card=q)
    edit(d / BREAD, "feed it for a week first.", "feed it daily for a week first.")
    ix.update()
    cards = build_cards(ix)
    q2 = find(cards, lambda c: c["kind"] == "qa" and c["rel"] == BREAD)
    assert q2["id"] == q["id"] and q2["hash"] != q["hash"]
    assert rs.reconcile(cards, D0 + timedelta(1))["edited"] == 1
    ev = rs.events(q["id"])
    assert [e["event"] for e in ev] == ["edited"] and "feed it for a week" in ev[0]["back"]
    assert rs.get(q["id"]).reps == 1


def test_question_edit_matches_qa(env):
    d, ix, rs = env
    q = find(build_cards(ix), lambda c: c["kind"] == "qa" and c["rel"] == BREAD)
    rs.grade(q["id"], 4, D0, card=q)
    edit(d / BREAD, "Why does my dough not rise?", "Why does my dough not rise enough?")
    ix.update()
    cards = build_cards(ix)
    q2 = find(cards, lambda c: c["kind"] == "qa" and c["rel"] == BREAD)
    assert q2["id"] != q["id"]
    assert rs.reconcile(cards, D0)["renamed"] == 1 and rs.get(q2["id"]).reps == 1


def test_page_move_matches_by_hash(env):
    d, ix, rs = env
    c = find(build_cards(ix), overnight)
    rs.grade(c["id"], 4, D0, card=c)
    (d / "01-garden" / "bread.md").write_text((d / BREAD).read_text())
    (d / BREAD).unlink()
    ix.update()
    cards = build_cards(ix)
    c2 = find(cards, lambda x: x["rel"] == "01-garden/bread.md" and x["date"] == "2026-10-29")
    assert rs.reconcile(cards, D0)["renamed"] >= 1
    assert rs.get(c2["id"]).reps == 1 and rs.retired() == []


def test_rewrite_retires_then_revives(env, tmp_path):
    d, ix, rs = env
    c = find(build_cards(ix), overnight)
    rs.grade(c["id"], 4, D0, card=c)
    original = (d / BREAD).read_text()
    edit(d / BREAD, OVERNIGHT, "Rye flour needs a much wetter dough than wheat.")
    ix.update()
    cards = build_cards(ix)
    q = due_queue(cards, rs, today=D0 + timedelta(1))
    assert q["changes"]["retired"] == 1 and q["counts"]["retired"] == 1
    assert c["id"] not in {x["id"] for x in q["due"] + q["new"]}
    new = find(cards, overnight)
    assert rs.get(new["id"]) is None            # unrelated text → a fresh card, no state
    ret = rs.retired()
    assert [r["id"] for r in ret] == [c["id"]] and ret[0]["reps"] == 1
    assert "front" not in ret[0] and "back" not in ret[0]
    # the text comes back → same id → revived with its schedule
    (d / BREAD).write_text(original)
    ix.update()
    ch = rs.reconcile(build_cards(ix), D0 + timedelta(2))
    assert ch["revived"] == 1 and rs.retired() == [] and rs.get(c["id"]).reps == 1
    assert [e["event"] for e in rs.events(c["id"])] == ["retired", "revived"]


def test_two_edits_on_one_page_pair_correctly(env):
    d, ix, rs = env
    cards = build_cards(ix)
    a = find(cards, overnight)
    b = find(cards, lambda c: c["rel"] == BREAD and c["date"] == "2026-10-31")
    rs.grade(a["id"], 4, D0, card=a)
    rs.grade(b["id"], 2, D0, card=b)
    edit(d / BREAD, "makes the crust darker", "makes the crust much darker")
    edit(d / BREAD, "for the first 20 minutes", "for the first 25 minutes")
    ix.update()
    cards = build_cards(ix)
    a2, b2 = find(cards, overnight), find(cards, lambda c: c["rel"] == BREAD and c["date"] == "2026-10-31")
    assert rs.reconcile(cards, D0)["renamed"] == 2
    assert rs.get(a2["id"]).reps == 1 and rs.get(b2["id"]).lapses == 1


def test_similarity():
    a = {"front": "Salt slows the yeast", "back": "add it after the first mix"}
    assert similarity(a, a) == 1.0
    assert similarity(a, {"front": "Salt slows yeast", "back": "add it after the first mix!"}) > 0.7
    assert similarity(a, {"front": "Seedlings need light", "back": "14 hours"}) < 0.5


def test_moved_into_personal_forgets_text(env, tmp_path):
    """A page moved into Personal/ must not keep its card text in data/ (privacy)."""
    d, ix, rs = env
    c = find(build_cards(ix), overnight)
    rs.grade(c["id"], 4, D0, card=c)
    edit(d / BREAD, "the crust darker", "the crust darker and crisper")   # history holds the old text
    ix.update()
    rs.reconcile(build_cards(ix), D0)
    (d / "Personal" / "bread.md").write_text((d / BREAD).read_text())
    (d / BREAD).unlink()
    ix.update()
    assert rs.reconcile(build_cards(ix), D0)["retired"] >= 1
    rs.close()
    for p in (tmp_path / "data").glob("flashcards.sqlite*"):
        raw = p.read_bytes()
        assert b"overnight cold proof" not in raw, p


def test_api_retired_and_card(desk, tmp_path):
    d = tmp_path / "desk"
    shutil.copytree(desk, d)
    clock = {"d": D0}
    app = create_app(d, tmp_path / "data" / "index.sqlite", semantic=False, today=lambda: clock["d"])
    with TestClient(app) as c:
        cards = c.get("/api/cards").json()["cards"]
        card = find(cards, overnight)
        assert c.post("/api/review/grade", json={"id": card["id"], "grade": 4}).status_code == 200
        j = c.get("/api/review/card", params={"id": card["id"]}).json()
        assert j["review"]["reps"] == 1 and [g["grade"] for g in j["grades"]] == [4] and j["events"] == []
        assert c.get("/api/review/retired").json() == {"retired": [], "count": 0}
        edit(d / BREAD, OVERNIGHT, "Rye flour needs a much wetter dough than wheat.")
        c.post("/api/reindex")
        r = c.get("/api/review/retired").json()
        assert r["count"] == 1 and r["retired"][0]["id"] == card["id"]
        assert "Rye" not in str(r) and "overnight" not in str(r)
        assert c.get("/api/review/card", params={"id": card["id"]}).status_code == 404
        assert c.get("/api/review").json()["counts"]["retired"] == 1
        assert c.get("/api/review/card", params={"id": "nope"}).status_code == 404
