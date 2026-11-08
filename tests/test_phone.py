"""Day 15: /api/recent and the phone-friendly home view (search, recent notes, open doubts)."""

import os
import re
import shutil
import time

import pytest
from fastapi.testclient import TestClient

from deskbrain.app import create_app


@pytest.fixture
def desk_copy(desk, tmp_path):
    d = tmp_path / "desk"
    shutil.copytree(desk, d)
    return d


def _client(root, tmp_path):
    app = create_app(root, tmp_path / "ix.sqlite", semantic=False)
    return app, TestClient(app)


def test_recent_newest_first_and_limit(desk_copy, tmp_path):
    now = time.time()
    newest = desk_copy / "02-cooking" / "bread.md"
    assert newest.exists()
    for i, p in enumerate(sorted(desk_copy.rglob("*.md"))):
        os.utime(p, (now - 1000 - i, now - 1000 - i))
    os.utime(newest, (now, now))
    app, c = _client(desk_copy, tmp_path)
    with c:
        pages = c.get("/api/recent?limit=3").json()["pages"]
        assert len(pages) == 3
        assert pages[0]["rel"] == "02-cooking/bread.md"
        assert [p["mtime"] for p in pages] == sorted((p["mtime"] for p in pages), reverse=True)
        assert set(pages[0]) >= {"rel", "title", "mtime"}
        assert c.get("/api/recent?limit=0").status_code == 422
        assert c.get("/api/recent?limit=51").status_code == 422
    app.state.desk.close()


def test_recent_never_lists_private(desk_copy, tmp_path):
    # Freshest files on disk are private ones: they must still never appear.
    now = time.time() + 10
    for sub in ("activity", "Personal", "tools", "_templates"):
        f = desk_copy / sub / "fresh.md"
        f.parent.mkdir(exist_ok=True)
        f.write_text("# fresh private\n")
        os.utime(f, (now, now))
    app, c = _client(desk_copy, tmp_path)
    with c:
        rels = [p["rel"] for p in c.get("/api/recent?limit=50").json()["pages"]]
        assert rels
        for r in rels:
            assert r.split("/")[0] not in ("activity", "Personal", "tools", "_templates")
    app.state.desk.close()


def test_home_uses_recent_and_open_doubts(desk, tmp_path):
    app, c = _client(desk, tmp_path)
    with c:
        js = c.get("/static/app.js").text
        assert "/api/recent" in js and "/api/doubts?status=open" in js
        html = c.get("/").text
        assert 'name="viewport"' in html and "width=device-width" in html
        open_d = c.get("/api/doubts?status=open").json()
        assert all(d["status"] == "open" for d in open_d["doubts"])
    app.state.desk.close()


def test_css_has_phone_rules(desk, tmp_path):
    app, c = _client(desk, tmp_path)
    with c:
        css = c.get("/static/style.css").text
    app.state.desk.close()
    m = re.search(r"@media \(max-width: 600px\) \{(.*)\n\}", css, re.S)
    assert m, "phone media block missing"
    block = m.group(1)
    assert "flex-wrap: wrap" in block               # header wraps instead of overflowing
    assert re.search(r"#q \{[^}]*flex: 1 1 100%", block)  # search box gets a full row
    assert "font-size: 16px" in block                # no iOS zoom on focus
    assert "min-height: 40px" in block               # tap targets
    assert "overflow-x: hidden" in css and ".page-body table { display: block" in css
    # No fixed widths wider than a 375px screen anywhere in the stylesheet.
    for w in re.findall(r"(?<![-\w])(?:min-)?width:\s*(\d+)px", css):
        assert int(w) <= 375
