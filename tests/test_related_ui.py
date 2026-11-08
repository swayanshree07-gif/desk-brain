"""Day 18: related notes shown in the page view (sidebar on desktop, below the page on phones)."""

import json
import re
import shutil
import subprocess

import pytest
from fastapi.testclient import TestClient

from deskbrain.app import STATIC_DIR, create_app

JS = (STATIC_DIR / "app.js").read_text()
CSS = (STATIC_DIR / "style.css").read_text()


def test_assets_served_with_related_ui(desk, tmp_path):
    app = create_app(desk, tmp_path / "ix.sqlite", semantic=False)
    with TestClient(app) as c:
        js = c.get("/static/app.js").text
        assert "/api/related?rel=" in js and 'class="related"' in js
        assert "data-for" in js  # stale responses are ignored
        assert ".related" in c.get("/static/style.css").text
    app.state.desk.close()


def test_api_shape_for_ui(desk, tmp_path):
    app = create_app(desk, tmp_path / "ix.sqlite", semantic=False)
    with TestClient(app) as c:
        rels = [p["rel"] for p in c.get("/api/recent?limit=50").json()["pages"]]
        for rel in rels:
            r = c.get(f"/api/related?rel={rel}&k=6").json()
            assert set(r) >= {"rel", "method", "related"} and len(r["related"]) <= 6
            for it in r["related"]:
                assert {"rel", "title", "score", "linked"} <= set(it)
                assert it["rel"] != rel and not re.match(r"(activity|Personal)/", it["rel"])
                assert isinstance(it["title"], str) and it["title"]
    app.state.desk.close()


def test_css_sidebar_desktop_and_stacked_phone():
    m = re.search(r"@media \(min-width: 69em\) \{(.*?)\n\}", CSS, re.S)
    assert m and "grid-template-columns" in m.group(1) and ".related" in m.group(1)
    assert ".related a" in CSS  # phone tap targets
    assert re.search(r"\.page-layout \{ display: block; \}", CSS)  # stacked by default


@pytest.mark.skipif(not shutil.which("node"), reason="node not installed")
def test_related_html_escapes_and_handles_empty():
    body = JS[JS.index("function relatedHtml"):JS.index("async function loadRelated")]
    script = ("const esc=s=>String(s).replace(/[&<>\"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;'}[c]));"
              "const pageHref=r=>'#/page/'+encodeURIComponent(r);" + body +
              "console.log(JSON.stringify([relatedHtml({related:[]}), relatedHtml(null),"
              "relatedHtml({method:'keyword',related:[{rel:'a/<x>.md',title:'<b>T</b>',linked:true}]})]))")
    out = json.loads(subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True).stdout)
    assert out[0] == "" and out[1] == ""
    assert "&lt;b&gt;T" in out[2] and "<b>T" not in out[2] and "link" in out[2]
