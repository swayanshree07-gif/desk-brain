"""Day 23–24: weekly digest data (change events + digest) and /api/digest, with fake timestamps."""

import os
import shutil
from datetime import date, datetime

import pytest
from fastapi.testclient import TestClient

from deskbrain.app import create_app
from deskbrain.digest import build_digest, window
from deskbrain.store import Index

GUITAR = "03-music/guitar.md"
OPEN_Q = "### 🔴 Q: How often should I change the strings?"
TODAY = date(2026, 11, 7)          # Saturday of ISO week 2026-W45 (Nov 2–8)
W44 = (date(2026, 10, 26), date(2026, 11, 1))
W45 = (date(2026, 11, 2), date(2026, 11, 8))


def ts(y, m, d, h=12):
    return datetime(y, m, d, h).timestamp()


def set_mtime(path, t):
    os.utime(path, (t, t))


@pytest.fixture
def env(desk, tmp_path):
    d = tmp_path / "desk"
    shutil.copytree(desk, d)
    # All pages last edited well before the weeks under test.
    for p in d.rglob("*.md"):
        set_mtime(p, ts(2026, 10, 1))
    ix = Index(tmp_path / "data" / "index.sqlite", d)
    ix.update()
    # Pretend the baseline build happened long ago, so W44/W45 are fully tracked.
    ix.db.execute("UPDATE meta SET value=? WHERE key='history_since'", (str(ts(2026, 10, 1, 0)),))
    ix.db.commit()
    yield d, ix
    ix.close()


def answer_guitar(d, when):
    p = d / GUITAR
    text = p.read_text().replace(OPEN_Q, "### 🟢 Q: How often should I change the strings?")
    text = text.replace("**Context:** strings sound dull after a month",
                        "**Context:** strings sound dull after a month\n**A:** Every 2–3 months when playing daily.")
    p.write_text(text)
    set_mtime(p, when)


def ids(items):
    return [x["question"] for x in items]


def test_window():
    assert window(None, TODAY) == (date(2026, 11, 1), TODAY)
    assert window("2026-W45", TODAY) == W45
    assert window("2026-W44") == W44
    assert window("2026-11-04") == W45            # the ISO week containing that date
    for bad in ("2026-W60", "nope", "2026-13-01"):
        with pytest.raises(ValueError):
            window(bad)


def test_baseline_records_no_events(env):
    d, ix = env
    assert ix.events() == []
    assert ix.meta("history_since") is not None
    st = ix.update()                               # nothing changed: still no events
    assert st.n_changed == 0 and ix.events() == []


def test_answering_a_doubt_is_an_event_at_page_mtime(env):
    d, ix = env
    answer_guitar(d, ts(2026, 11, 5))
    ix.update()
    ev = ix.events()
    assert [(e["kind"], e["rel"]) for e in ev] == [("doubt_answered", GUITAR)]
    assert datetime.fromtimestamp(ev[0]["ts"]).date() == date(2026, 11, 5)
    dg = build_digest(ix, *W45, today=TODAY)
    assert ids(dg["doubts"]["answered"]) == ["How often should I change the strings?"]
    assert dg["doubts"]["answered"][0]["answered_day"] == "2026-11-05"
    assert dg["doubts"]["answered"][0]["answer"].startswith("Every 2–3 months")
    assert "How often should I change the strings?" not in ids(dg["doubts"]["open"])
    assert [p["rel"] for p in dg["pages"]["changed"]] == [GUITAR]
    assert dg["pages"]["changed"][0]["new"] is False
    # Asked 2026-11-02 → also "new" in W45, flagged as answered in the same week.
    new = {x["question"]: x for x in dg["doubts"]["new"]}
    assert new["How often should I change the strings?"]["answered_this_week"] is True
    # The previous week knows nothing of it.
    w44 = build_digest(ix, *W44, today=TODAY)
    assert w44["doubts"]["answered"] == [] and w44["pages"]["changed"] == []


def test_reopen_and_question_edit_events(env):
    d, ix = env
    answer_guitar(d, ts(2026, 11, 3))
    ix.update()
    p = d / GUITAR
    p.write_text(p.read_text().replace("### 🟢 Q: How often should I change the strings?", OPEN_Q)
                 .replace("### 🟢 Q: What is a capo for?", "### 🟢 Q: What is a capo used for?"))
    set_mtime(p, ts(2026, 11, 4))
    ix.update()
    kinds = sorted(e["kind"] for e in ix.events(ts(2026, 11, 4, 0)))
    assert kinds == ["doubt_new", "doubt_removed", "doubt_reopened"]
    dg = build_digest(ix, *W45, today=TODAY)
    # The strings doubt was answered, then re-opened: it is open again, not "answered".
    # The edited capo question is a new doubt that arrived answered, so it is listed.
    assert ids(dg["doubts"]["answered"]) == ["What is a capo used for?"]
    assert "How often should I change the strings?" in ids(dg["doubts"]["open"])


def test_new_and_removed_pages(env):
    d, ix = env
    new = d / "03-music" / "theory.md"
    new.write_text("# Theory\n\n### 🔴 Q: What is a mode?\n**Context:** heard it in a lesson\n\n"
                   "### 🟢 Q: What is an octave?\n**A:** Double the frequency.\n\n"
                   "## Findings\n- **2026-11-06** · **Circle of fifths:** each step adds one sharp.\n")
    set_mtime(new, ts(2026, 11, 6))
    (d / "03-music" / "practice.md").unlink()
    ix.update()
    dg = build_digest(ix, *W45, today=TODAY)
    ch = {p["rel"]: p for p in dg["pages"]["changed"]}
    assert ch["03-music/theory.md"]["new"] is True and ch["03-music/theory.md"]["day"] == "2026-11-06"
    assert dg["pages"]["removed"] == ["03-music/practice.md"]
    # Undated doubts on a new page count as new through the doubt_new event.
    assert {"What is a mode?", "What is an octave?"} <= set(ids(dg["doubts"]["new"]))
    assert "What is an octave?" in ids(dg["doubts"]["answered"])   # arrived already answered
    assert [f["text"] for f in dg["findings"]] == ["**Circle of fifths:** each step adds one sharp."]
    assert dg["counts"]["new_pages"] == 1 and dg["areas"]["03-music"] == 2


def test_fixture_week_44_from_dates_only(env):
    d, ix = env
    dg = build_digest(ix, *W44, today=TODAY)
    assert dg["week"] == "2026-W44"
    # Asked dates in Oct 26 – Nov 1.
    assert sorted(ids(dg["doubts"]["new"])) == ["What is a capo for?", "Why do the lower leaves turn yellow?",
                                                "Why does my dough not rise?"]
    # Findings dated in the week; the 2026-11-01 one sits in "📅 Baking log" and is skipped.
    assert sorted(f["date"] for f in dg["findings"]) == ["2026-10-29", "2026-10-30", "2026-10-31"]
    assert dg["pages"]["changed"] == [] and dg["doubts"]["answered"] == []
    assert dg["tracking"]["complete"] is True


def test_still_open_oldest_first_with_age(env):
    d, ix = env
    dg = build_digest(ix, *W45, today=TODAY)
    op = dg["doubts"]["open"]
    assert ids(op) == ["Why do the lower leaves turn yellow?", "How often should I change the strings?"]
    assert [x["age_days"] for x in op] == [8, 5]
    assert all(x["status"] == "open" for x in op) and all(x["anchor"] for x in op)


def test_tracking_incomplete_before_history(env):
    d, ix = env
    ix.db.execute("UPDATE meta SET value=? WHERE key='history_since'", (str(ts(2026, 11, 4)),))
    assert build_digest(ix, *W45, today=TODAY)["tracking"] == {"since": "2026-11-04", "complete": False}


def test_events_hold_no_desk_text_and_follow_privacy(env):
    d, ix = env
    answer_guitar(d, ts(2026, 11, 5))
    ix.update()
    for e in ix.events():
        assert "strings" not in (e["key"] + e["detail"])     # ids only, never question text
    ix.db.execute("INSERT INTO events (ts, kind, rel) VALUES (1, 'page_removed', 'Personal/diary.md')")
    assert ix.check_privacy() == ["Personal/diary.md"]
    assert "Personal/diary.md" not in build_digest(ix, date(1970, 1, 1), TODAY, today=TODAY)["pages"]["removed"]


def test_new_desk_resets_history(env, tmp_path, desk):
    d, ix = env
    answer_guitar(d, ts(2026, 11, 5))
    ix.update()
    assert ix.events()
    ix.close()
    other = tmp_path / "other"
    shutil.copytree(desk, other)
    ix2 = Index(tmp_path / "data" / "index.sqlite", other)
    try:
        assert ix2.events() == [] and ix2.meta("history_since") is None
    finally:
        ix2.close()


def test_api_digest(env, tmp_path):
    d, ix = env
    ix.close()
    answer_guitar(d, ts(2026, 11, 5))
    app = create_app(root=d, db_path=tmp_path / "data" / "index.sqlite", semantic=False, today=lambda: TODAY)
    with TestClient(app) as c:
        r = c.get("/api/digest", params={"week": "2026-W45"})
        assert r.status_code == 200
        j = r.json()
        assert (j["start"], j["end"], j["week"]) == ("2026-11-02", "2026-11-08", "2026-W45")
        assert j["counts"]["answered"] == 1 and j["counts"]["changed"] == 1
        dflt = c.get("/api/digest").json()
        assert (dflt["start"], dflt["end"], dflt["week"]) == ("2026-11-01", "2026-11-07", None)
        assert c.get("/api/digest", params={"week": "2026-W99"}).status_code == 422
        assert c.get("/api/digest", params={"week": "x" * 30}).status_code == 422


def test_digest_ui_served_and_escapes_under_node(env, tmp_path):
    import json
    import re
    import subprocess
    from deskbrain.app import STATIC_DIR
    d, ix = env
    ix.close()
    with TestClient(create_app(d, tmp_path / "data" / "index.sqlite", semantic=False, today=lambda: TODAY)) as c:
        assert 'href="#/digest"' in c.get("/").text
        assert "/api/digest" in c.get("/static/app.js").text
        real = c.get("/api/digest", params={"week": "2026-W44"}).json()
    if not shutil.which("node"):
        pytest.skip("node not installed")
    js = (STATIC_DIR / "app.js").read_text()
    parts = [re.search(rx, js, re.M | re.S).group(0) for rx in (
        r"^const esc = .*?;\n", r"^const pageHref = .*?;\n", r"^const inlineMd = .*?;\n",
        r"^const addDays = .*?;\n", r"^function digestHtml\(r\) \{.*?\n\}\n")]
    evil = "<script>alert(1)</script>"
    bad = json.loads(json.dumps(real))
    bad["doubts"]["open"].append({"id": "x", "rel": "a/" + evil + ".md", "page_title": evil, "line": 1,
                                  "question": evil, "anchor": '"><img src=x onerror=1>', "age_days": 20})
    bad["findings"].append({"rel": "a.md", "page_title": evil, "line": 2, "date": "2026-10-30",
                            "text": "**" + evil + "**", "section": evil})
    bad["pages"]["removed"].append(evil)
    bad["counts"]["open"] += 1
    bad["counts"]["findings"] += 1
    prog = "".join(parts) + f"console.log(JSON.stringify([digestHtml({json.dumps(real)}), digestHtml({json.dumps(bad)})]));"
    good_html, bad_html = json.loads(subprocess.run(["node", "-e", prog], capture_output=True, text=True,
                                                    check=True).stdout)
    assert "Week 2026-W44" in good_html and "Why does my dough not rise?" in good_html
    assert "previous week" in good_html and 'week=2026-10-19' in good_html and 'week=2026-11-02' in good_html
    assert "<script" not in bad_html and "<img" not in bad_html
    assert 'class="age old">20 d' in bad_html


def test_cli_digest(desk, tmp_path, capsys):
    from deskbrain.__main__ import main
    db = str(tmp_path / "ix.sqlite")
    assert main(["--desk", str(desk), "digest", "--week", "2026-W44", "--db", db]) == 0
    out = capsys.readouterr().out
    assert "Digest 2026-W44: 2026-10-26 – 2026-11-01" in out
    assert "3 new doubts" in out and "3 findings" in out and "Still open:" in out
    assert "Personal" not in out and "diary" not in out
    assert main(["--desk", str(desk), "digest", "--week", "bad", "--db", db]) == 2
