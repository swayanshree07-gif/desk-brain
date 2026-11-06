"""Flashcard generation (deskbrain/cards.py), /api/cards and the CLI on the fixture desk."""

import shutil

import pytest
from fastapi.testclient import TestClient

from deskbrain.__main__ import main
from deskbrain.app import create_app
from deskbrain.cards import BLANK, build_cards, finding_card, list_cards, skip_section
from deskbrain.doubts import doubt_id, list_doubts
from deskbrain.store import Index


@pytest.fixture
def index(desk, tmp_path):
    with Index(tmp_path / "ix.sqlite", desk) as ix:
        ix.update()
        yield ix


def test_card_count_and_kinds(index):
    r = list_cards(index)
    assert r["total"] == 8
    assert r["counts"] == {"qa": 3, "lead": 1, "cloze": 1, "recall": 3}
    assert r["areas"] == ["01-garden", "02-cooking", "03-music"]
    assert [(c["rel"], c["line"]) for c in r["cards"]] == sorted((c["rel"], c["line"]) for c in r["cards"])


def test_ids_unique_and_stable(index, desk, tmp_path):
    a = [c["id"] for c in build_cards(index)]
    assert len(set(a)) == len(a)
    with Index(tmp_path / "other.sqlite", desk) as ix2:   # fresh index → same ids
        ix2.update()
        assert [c["id"] for c in build_cards(ix2)] == a
    # exact ids pinned so an accidental change of the id scheme is caught
    assert "q-" + doubt_id("03-music/guitar.md", "What is a capo for?") in a
    assert all(i.startswith(("q-", "f-")) and len(i) == 14 for i in a)


def test_qa_cards_match_answered_doubts(index):
    answered = [d for d in list_doubts(index, status="answered")["doubts"] if d["answer"]]
    qa = {c["id"]: c for c in build_cards(index) if c["kind"] == "qa"}
    assert {d["id"] for d in answered} == {i[2:] for i in qa}
    capo = qa["q-" + doubt_id("03-music/guitar.md", "What is a capo for?")]
    assert capo["front"] == "What is a capo for?" and capo["back"].startswith("It clamps across the frets")
    assert capo["anchor"] == "q-what-is-a-capo-for" and capo["date"] == "2026-10-28 20:05"
    assert capo["page_title"] == "Guitar" and capo["area"] == "03-music"


def test_finding_kinds(index):
    by_kind = {}
    for c in build_cards(index):
        by_kind.setdefault(c["kind"], []).append(c)
    lead, = by_kind["lead"]
    assert lead["front"] == "Salt slows the yeast" and lead["back"].startswith("**Salt slows the yeast:**")
    cloze, = by_kind["cloze"]
    assert cloze["front"] == f"Bake at {BLANK} with steam for the first 20 minutes." and cloze["back"] == "230 °C"
    rec = {c["back"]: c for c in by_kind["recall"]}
    assert rec["Seedlings need 14 hours of light."]["front"] == "Tomatoes › 🔍 Findings, 2026-10-05: what did you find?"
    # findings in a log section give no card
    assert not any("Third bake" in c["back"] for c in build_cards(index))


@pytest.mark.parametrize("text,kind,front,back", [
    ("**Key idea:** the rest of the sentence is long enough.", "lead", "Key idea", None),
    ("**Short** x", "cloze", f"{BLANK} x", "Short"),               # too little after the lead → cloze
    ("Use `objdump -d` and **always** check.", "cloze", f"Use `objdump -d` and {BLANK} check.", "always"),
    ("Run `ls` to list.", "cloze", f"Run {BLANK} to list.", "ls"),
    ("Plain sentence, `x` too short.", "recall", "", "Plain sentence, `x` too short."),
])
def test_finding_card_rules(text, kind, front, back):
    k, f, b = finding_card(text)
    assert (k, f) == (kind, front)
    assert b == (back if back is not None else text)


@pytest.mark.parametrize("section,skip", [
    ("🔍 Findings", False), ("Key facts", False), ("📅 Daily log", True), ("Baking Log", True),
    ("🎯 Now", True), ("📓 Notes", True), ("To-do", True), ("Next steps", True), ("Knowledge", False),
    ("Logging setup", False), ("", False),
])
def test_skip_section(section, skip):
    assert skip_section(section) is skip


def test_edits(desk, tmp_path):
    d = tmp_path / "desk"
    shutil.copytree(desk, d)
    with Index(tmp_path / "ix.sqlite", d) as ix:
        ix.update()
        before = {c["id"]: c for c in build_cards(ix)}
        p = d / "03-music/guitar.md"
        t = p.read_text()
        # answer edited + lines moved: qa id stays, content hash changes
        t = t.replace("Notes on learning", "\n\nNotes on learning").replace("higher key.", "higher key. Handy!")
        # the open doubt gets an answer → a new qa card
        t = t.replace("**Context:** strings sound dull after a month",
                      "**Context:** strings sound dull after a month\n**A:** Every three months.").replace(
                      "### 🔴 Q: How often", "### 🟢 Q: How often")
        p.write_text(t)
        ix.update()
        after = {c["id"]: c for c in build_cards(ix)}
    capo = "q-" + doubt_id("03-music/guitar.md", "What is a capo for?")
    assert capo in after and after[capo]["hash"] != before[capo]["hash"]
    assert set(after) - set(before) == {"q-" + doubt_id("03-music/guitar.md", "How often should I change the strings?")}
    unchanged = set(before) - {capo}
    assert all(after[i]["hash"] == before[i]["hash"] for i in unchanged)


def test_duplicate_findings_get_suffix(desk, tmp_path):
    d = tmp_path / "desk"
    shutil.copytree(desk, d)
    p = d / "01-garden/tomatoes.md"
    p.write_text(p.read_text().replace("- **2026-10-05** · Seedlings need 14 hours of light.",
                                       "- **2026-10-05** · Seedlings need 14 hours of light.\n"
                                       "- **2026-10-05** · Seedlings need 14 hours of light."))
    with Index(tmp_path / "ix.sqlite", d) as ix:
        ix.update()
        ids = [c["id"] for c in build_cards(ix)]
    assert len(ids) == len(set(ids)) == 9
    assert sum(i.endswith("-1") for i in ids) == 1


def test_api_cards(desk, tmp_path):
    with TestClient(create_app(desk, tmp_path / "ix.sqlite", semantic=False)) as c:
        r = c.get("/api/cards").json()
        assert r["total"] == 8 and len(r["cards"]) == 8
        assert {"id", "kind", "rel", "front", "back", "hash", "anchor"} <= set(r["cards"][0])
        assert [x["kind"] for x in c.get("/api/cards", params={"kind": "qa"}).json()["cards"]] == ["qa"] * 3
        assert {x["rel"] for x in c.get("/api/cards", params={"area": "02-cooking"}).json()["cards"]} == {"02-cooking/bread.md"}
        assert c.get("/api/cards", params={"kind": "bogus"}).status_code == 422
        body = c.get("/api/cards").text
        for private in ("activity/", "Personal/", "_templates", "tools/"):
            assert private not in body


def test_cli_cards(desk, tmp_path, capsys):
    assert main(["--desk", str(desk), "cards", "--db", str(tmp_path / "ix.sqlite")]) == 0
    out = capsys.readouterr().out
    assert "8 of 8 cards; qa=3, lead=1, cloze=1, recall=3" in out
