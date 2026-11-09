"""Day 9 safety layer: path guards, symlink handling, exclusion double-checks, privacy.

All tests work on tmp copies of the fixture desk (the fixture itself is never touched).
"""

import os
import shutil

import pytest
from fastapi.testclient import TestClient

from deskbrain.app import create_app
from deskbrain.scanner import in_private_area, is_allowed, read_page, scan
from deskbrain.store import Index

CANARIES = ("xylophone-moonbeam", "walrus-paperclip", "moonbeam-flowerpot", "Moonbeam-xylophone")


@pytest.fixture
def desk_copy(desk, tmp_path):
    dst = tmp_path / "desk"
    shutil.copytree(desk, dst)
    return dst


@pytest.fixture
def trap_desk(desk_copy, tmp_path):
    """Desk copy with every trap we can think of planted in it."""
    d = desk_copy
    outside = tmp_path / "outside.md"
    outside.write_text("# Outside\n\nCanary text: walrus-paperclip outside the desk.\n")
    os.symlink(d / "Personal" / "diary.md", d / "01-garden" / "linked-diary.md")
    os.symlink(d / "Personal", d / "02-cooking" / "linked-personal")
    os.symlink(outside, d / "03-music" / "outside.md")
    (d / "personal").mkdir()
    (d / "personal" / "lower.md").write_text("# lower\n\nxylophone-moonbeam lower-case private folder.\n")
    (d / "Activity").mkdir()
    (d / "Activity" / "x.md").write_text("# x\n\nwalrus-paperclip capitalised activity folder.\n")
    (d / "01-garden" / "_Template").mkdir()
    (d / "01-garden" / "_Template" / "README.md").write_text("# t\n\nmoonbeam-flowerpot template.\n")
    (d / ".hidden").mkdir()
    (d / ".hidden" / "h.md").write_text("# h\n\nwalrus-paperclip hidden.\n")
    return d


@pytest.mark.parametrize("rel,ok", [
    ("01-garden/tomatoes.md", True),
    ("README.md", True),
    ("Personal/diary.md", False),
    ("personal/diary.md", False),
    ("PERSONAL/diary.md", False),
    ("activity/2026-11-01.md", False),
    ("Activity/2026-11-01.md", False),
    ("tools/notes.md", False),
    ("_templates/page.md", False),
    ("03-music/_template/README.md", False),
    ("03-music/_Template/README.md", False),
    (".obsidian/x.md", False),
    ("01-garden/.trash/x.md", False),
    ("../CLAUDE.md", False),
    ("01-garden/../Personal/diary.md", False),
    ("/etc/passwd", False),
    ("", False),
    ("02-cooking/recipe.pdf", False),
    ("01-garden/.secret.md", False),
])
def test_is_allowed(rel, ok):
    assert is_allowed(rel) is ok


@pytest.mark.parametrize("rel,private", [
    ("01-garden/", False), ("02-cooking/recipe.pdf", False), ("", False),
    ("Personal/", True), ("Personal", True), ("activity/a.png", True),
    ("03-music/_template/", True), ("../x", True), ("01-garden/.git/x", True),
])
def test_in_private_area(rel, private):
    assert in_private_area(rel) is private


def test_scan_skips_all_traps(trap_desk):
    rels = [f.rel for f in scan(trap_desk)]
    for bad in ("01-garden/linked-diary.md", "03-music/outside.md", "personal/lower.md",
                "Activity/x.md", "01-garden/_Template/README.md", ".hidden/h.md"):
        assert bad not in rels
    assert not any("linked-personal" in r for r in rels)
    assert all(is_allowed(r) for r in rels)


@pytest.mark.parametrize("rel", [
    "01-garden/linked-diary.md",           # symlinked file into Personal/
    "02-cooking/linked-personal/diary.md",  # through a symlinked folder
    "03-music/outside.md",                 # symlink out of the desk
    "personal/lower.md", "Activity/x.md", "Personal/diary.md", "../outside.md",
])
def test_read_page_refuses(trap_desk, rel):
    with pytest.raises(PermissionError):
        read_page(trap_desk, rel)


def test_read_page_allows_normal(trap_desk):
    assert "Tomato" in read_page(trap_desk, "01-garden/tomatoes.md")


def test_index_of_trap_desk_is_clean(trap_desk, tmp_path):
    db = tmp_path / "data" / "index.sqlite"
    with Index(db, trap_desk) as ix:
        ix.update()
        assert ix.check_privacy() == []
        assert all(is_allowed(r) for r in ix.page_rels())
    # Raw bytes of every file in data/ (index, WAL, anything else) hold no canary text.
    for p in db.parent.rglob("*"):
        if p.is_file():
            raw = p.read_bytes()
            for c in CANARIES:
                assert c.encode() not in raw, (p, c)


def test_check_privacy_flags_planted_rows(desk_copy, tmp_path):
    with Index(tmp_path / "ix.sqlite", desk_copy) as ix:
        ix.update()
        assert ix.check_privacy() == []
        ix.db.execute("INSERT INTO doubts (rel, line, question, status) VALUES ('Personal/d.md', 1, 'q', 'open')")
        ix.db.execute("INSERT INTO links (src, ord, line, raw, text, kind, status, target) "
                      "VALUES ('README.md', 999, 1, 'x', 'x', 'md', 'ok', 'activity/')")
        assert ix.check_privacy() == ["Personal/d.md", "activity/"]


def test_api_on_trap_desk_leaks_nothing(trap_desk, tmp_path):
    app = create_app(root=trap_desk, db_path=tmp_path / "data" / "index.sqlite")
    with TestClient(app) as c:
        bodies = []
        for q in ("xylophone", "moonbeam", "walrus", "paperclip", "flowerpot", "outside", "lower", "hidden"):
            bodies.append(c.get("/api/search", params={"q": q, "k": 100, "all": "true"}).text)
            bodies.append(c.get("/api/doubts", params={"q": q}).text)
        for rel in ("01-garden/linked-diary.md", "02-cooking/linked-personal/diary.md", "03-music/outside.md",
                    "personal/lower.md", "Activity/x.md", "01-garden/_Template/README.md", ".hidden/h.md",
                    "01-garden%2F..%2FPersonal%2Fdiary.md", "01-garden/./../Personal/diary.md"):
            r = c.get("/api/page", params={"rel": rel})
            assert r.status_code == 404, rel
            bodies.append(r.text)
        for rel in c.app.state.desk.index.page_rels():
            bodies.append(c.get("/api/page", params={"rel": rel}).text)
        for path in ("/api/graph", "/api/cards", "/api/health", "/api/review?new=500", "/api/digest",
                     "/api/digest?week=2026-W44", "/api/digest?week=2026-W45", "/api/index/status", "/api/recent?limit=50", "/api/related?rel=03-music/practice.md&k=20", "/api/related?rel=README.md&k=20", "/api/ask?q=diary", "/api/ask?q=secret+notes"):
            bodies.append(c.get(path).text)
        blob = "\n".join(bodies)
        for canary in CANARIES:
            assert canary not in blob, canary
        # Static file route must not serve anything outside the static folder.
        for path in ("/static/../config.py", "/static/%2e%2e/config.py", "/static/..%2f..%2fCLAUDE.md"):
            r = c.get(path)
            assert r.status_code in (400, 404), path
            assert "EXCLUDED_TOP_DIRS" not in r.text
