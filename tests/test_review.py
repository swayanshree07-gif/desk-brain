"""SM-2 scheduling math, review store, due queue, /api/review and the CLI (Day 20)."""

from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from deskbrain.__main__ import main
from deskbrain.app import create_app
from deskbrain.review import MIN_EASE, START_EASE, CardState, ReviewStore, due_queue, sm2

D0 = date(2026, 11, 6)


def test_first_reviews_follow_1_6_then_ease():
    st = sm2(CardState(), 4, D0)
    assert (st.reps, st.interval, st.ease, st.due) == (1, 1, START_EASE, "2026-11-07")
    st = sm2(st, 4, D0 + timedelta(1))
    assert (st.reps, st.interval) == (2, 6)
    st = sm2(st, 4, D0 + timedelta(7))
    assert (st.reps, st.interval) == (3, 15)        # round(6 * 2.5)
    assert st.due == (D0 + timedelta(22)).isoformat()
    assert st.first == D0.isoformat() and st.last == (D0 + timedelta(7)).isoformat()


@pytest.mark.parametrize("grade, delta", [(5, 0.10), (4, 0.0), (3, -0.14)])
def test_ease_change_per_grade(grade, delta):
    assert sm2(CardState(), grade, D0).ease == pytest.approx(START_EASE + delta)


@pytest.mark.parametrize("grade", [0, 1, 2])
def test_lapse_resets_reps_keeps_ease(grade):
    st = CardState(ease=2.2, interval=30, reps=5, lapses=1, first="2026-01-01")
    new = sm2(st, grade, D0)
    assert (new.reps, new.interval, new.lapses, new.ease) == (0, 1, 2, 2.2)
    assert new.due == "2026-11-07" and new.first == "2026-01-01"
    # after the lapse the card climbs again from 1 day
    assert sm2(new, 5, D0 + timedelta(1)).interval == 1


def test_ease_never_below_floor():
    st = CardState()
    for i in range(20):
        st = sm2(st, 3, D0 + timedelta(i))
    assert st.ease == MIN_EASE


def test_interval_grows_monotonically_on_good_grades():
    st, day, ivs = CardState(), D0, []
    for _ in range(8):
        st = sm2(st, 4, day)
        ivs.append(st.interval)
        day += timedelta(st.interval)
    assert ivs == sorted(ivs) and ivs[-1] > 365


@pytest.mark.parametrize("bad", [-1, 6, 2.5, "3", True, None])
def test_bad_grade_rejected(bad):
    with pytest.raises(ValueError):
        sm2(CardState(), bad, D0)


def test_store_persists_and_logs(tmp_path):
    p = tmp_path / "fc.sqlite"
    rs = ReviewStore(p)
    rs.grade("q-a", 5, D0, card_hash="h1")
    rs.grade("q-a", 2, D0 + timedelta(1), card_hash="h1")
    rs.close()
    rs = ReviewStore(p)
    st = rs.get("q-a")
    assert (st.reps, st.lapses, st.interval) == (0, 1, 1)
    assert [h["grade"] for h in rs.history("q-a")] == [5, 2]
    assert rs.get("missing") is None
    rs.close()


def _cards(n):
    return [{"id": f"c{i}", "rel": "a.md", "line": i, "front": f"f{i}"} for i in range(n)]


def test_due_queue(tmp_path):
    rs = ReviewStore(tmp_path / "fc.sqlite")
    cards = _cards(6)
    q = due_queue(cards, rs, today=D0, new_per_day=3)
    assert [c["id"] for c in q["new"]] == ["c0", "c1", "c2"] and q["due"] == []
    assert q["counts"] == {"due": 0, "new": 3, "new_total": 6, "later": 0, "total": 6}
    for cid in ("c0", "c1"):
        rs.grade(cid, 4, D0)
    rs.grade("c2", 1, D0)
    # same day: the 3 new slots are used up (c0, c1, c2)
    q = due_queue(cards, rs, today=D0, new_per_day=3)
    assert q["new"] == [] and q["counts"]["later"] == 3 and q["counts"]["new_total"] == 3
    # next day: all three due (interval 1), new cards allowed again
    q = due_queue(cards, rs, today=D0 + timedelta(1), new_per_day=3)
    assert {c["id"] for c in q["due"]} == {"c0", "c1", "c2"}
    assert [c["id"] for c in q["new"]] == ["c3", "c4", "c5"]
    assert q["due"][0]["review"]["reps"] in (0, 1)
    # a card with state but no longer on the desk is ignored
    q = due_queue(cards[1:], rs, today=D0 + timedelta(1), new_per_day=0)
    assert {c["id"] for c in q["due"]} == {"c1", "c2"} and q["counts"]["total"] == 5
    rs.close()


def test_most_overdue_first(tmp_path):
    rs = ReviewStore(tmp_path / "fc.sqlite")
    rs.grade("c1", 4, D0)               # due D0+1
    rs.grade("c0", 4, D0 - timedelta(5))  # due D0-4
    q = due_queue(_cards(2), rs, today=D0 + timedelta(2))
    assert [c["id"] for c in q["due"]] == ["c0", "c1"]
    rs.close()


def test_api_review_and_grade(desk, tmp_path):
    clock = {"d": D0}
    app = create_app(desk, tmp_path / "ix.sqlite", semantic=False, today=lambda: clock["d"])
    with TestClient(app) as c:
        q = c.get("/api/review").json()
        assert q["today"] == "2026-11-06" and q["counts"]["new"] == 8 and q["due"] == []
        cid = q["new"][0]["id"]
        r = c.post("/api/review/grade", json={"id": cid, "grade": 4})
        assert r.status_code == 200
        assert r.json()["review"]["due"] == "2026-11-07" and r.json()["review"]["interval"] == 1
        assert c.get("/api/review", params={"new": 20}).json()["counts"] == \
            {"due": 0, "new": 7, "new_total": 7, "later": 1, "total": 8}
        clock["d"] = D0 + timedelta(1)
        q = c.get("/api/review").json()
        assert [x["id"] for x in q["due"]] == [cid] and q["due"][0]["review"]["reps"] == 1
        # validation
        assert c.post("/api/review/grade", json={"id": "nope", "grade": 3}).status_code == 404
        assert c.post("/api/review/grade", json={"id": cid, "grade": 6}).status_code == 422
        assert c.post("/api/review/grade", json={"id": cid}).status_code == 422
        assert c.get("/api/review", params={"new": -1}).status_code == 422
    assert (tmp_path / "flashcards.sqlite").exists()
    assert not any(p.name == "flashcards.sqlite" for p in desk.rglob("*"))


def test_cli_review(desk, tmp_path, capsys):
    assert main(["--desk", str(desk), "review", "--new", "5", "--db", str(tmp_path / "ix.sqlite")]) == 0
    out = capsys.readouterr().out
    assert "0 due, 5 new today (8 unseen), 0 scheduled later, 8 cards" in out


def test_review_ui_served(client_fixture_desk):
    c = client_fixture_desk
    assert 'href="#/review"' in c.get("/").text
    js = c.get("/static/app.js").text
    assert '"/review"' in js and "/api/review/grade" in js and "inlineMd" in js


@pytest.fixture
def client_fixture_desk(desk, tmp_path):
    with TestClient(create_app(desk, tmp_path / "ix.sqlite", semantic=False)) as c:
        yield c


def test_inline_md_escapes_under_node():
    import re
    import shutil
    import subprocess
    from deskbrain.app import STATIC_DIR
    if not shutil.which("node"):
        pytest.skip("node not installed")
    js = (STATIC_DIR / "app.js").read_text()
    esc = re.search(r"^const esc = .*?;\n", js, re.M | re.S).group(0)
    md = re.search(r"^const inlineMd = .*?;\n", js, re.M).group(0)
    prog = esc + md + r"""
const out = [inlineMd("**a<b>** and `<script>x</script>` & 'q'"), inlineMd("`**not bold**`"), inlineMd("**x\" onmouseover=\"y**")];
console.log(JSON.stringify(out));"""
    import json
    res = json.loads(subprocess.run(["node", "-e", prog], capture_output=True, text=True, check=True).stdout)
    assert res[0] == "<b>a&lt;b&gt;</b> and <code>&lt;script&gt;x&lt;/script&gt;</code> &amp; &#39;q&#39;"
    assert "<script" not in "".join(res) and 'onmouseover="' not in "".join(res)
    assert res[1].startswith("<code>")
