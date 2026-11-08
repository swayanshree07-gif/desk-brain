"""Day 16: phone view part 2 — page, doubts, review, digest and graph usable at 375px."""

import json
import re
import shutil
import subprocess

import pytest
from fastapi.testclient import TestClient

from deskbrain.app import STATIC_DIR, create_app
from deskbrain.graph import build_graph
from deskbrain.render import render_page
from deskbrain.store import Index

WIDE = "# Wide\n\n| " + " | ".join(f"column {i}" for i in range(14)) + " |\n|" + "---|" * 14 + "\n| " + \
       " | ".join("x" * 20 for _ in range(14)) + " |\n\n```\n" + "y" * 400 + "\n```\n"


def test_render_wraps_tables_in_scroll_box():
    html = render_page(WIDE, "w.md", [])
    assert html.count('<div class="table-scroll"><table>') == 1
    assert html.count("</table></div>") == 1
    # Raw HTML in a page stays escaped, so it can never fake (or break) the wrapper.
    raw = render_page("<table><tr><td>a</td></tr></table>\n", "r.md", [])
    assert "table-scroll" not in raw and "<table>" not in raw


def test_wide_table_page_over_api(desk, tmp_path):
    d = tmp_path / "desk"
    shutil.copytree(desk, d)
    (d / "01-garden" / "wide.md").write_text(WIDE)
    app = create_app(d, tmp_path / "ix.sqlite", semantic=False)
    with TestClient(app) as c:
        body = json.dumps(c.get("/api/page?rel=01-garden/wide.md").json())
        assert 'table-scroll' in body and "<pre>" in body
    app.state.desk.close()


def _phone_block(css):
    m = re.search(r"@media \(max-width: 600px\) \{(.*)\n\}", css, re.S)
    assert m
    return m.group(1)


def test_css_phone_rules_part2():
    css = (STATIC_DIR / "style.css").read_text()
    assert re.search(r"\.table-scroll \{[^}]*overflow-x: auto", css)
    assert re.search(r"\.page-body pre \{[^}]*overflow-x: auto", css)
    assert re.search(r"\.graph-list \{ display: none; \}", css)        # hidden on desktop
    b = _phone_block(css)
    assert re.search(r"\.graph-wrap, \.graph-legend \{ display: none; \}", b)  # canvas off on phones
    assert re.search(r"\.graph-list \{ display: block; \}", b)
    assert re.search(r"\.fc-grades button \{[^}]*min-height: 44px", b)       # review grade buttons
    for sel in (r"\.backlinks a", r"\.graph-list li a", r"\.graph-bar select"):
        assert re.search(sel + r"[^{]*\{[^}]*min-height: 40px", b), sel
    for w in re.findall(r"(?<![-\w])(?:min-)?width:\s*(\d+)px", css):
        assert int(w) <= 375


def test_graph_js_renders_list_fallback():
    js = (STATIC_DIR / "graph.js").read_text()
    assert 'class="graph-list' in js and "listView(g, state)" in js


@pytest.mark.skipif(not shutil.which("node"), reason="node not installed")
def test_list_view_under_node(desk, tmp_path):
    with Index(tmp_path / "ix.sqlite", desk) as ix:
        ix.update()
        g = build_graph(ix)
    gj = tmp_path / "g.json"
    gj.write_text(json.dumps(g))
    script = ("const G=require(process.argv[1]);const g=JSON.parse(require('fs').readFileSync(process.argv[2]));"
              "console.log(JSON.stringify({all:G.listView(g,{}),no:G.listView(g,{orphans:false}),"
              "a:G.listView(g,{area:'02-cooking'})}))")
    out = json.loads(subprocess.run(["node", "-e", script, str(STATIC_DIR / "graph.js"), str(gj)],
                                    capture_output=True, text=True, timeout=60, check=True).stdout)
    pages = [n for n in g["nodes"] if n["type"] == "page"]
    assert len(out["all"]) == len(pages)
    degs = [n["degree"] for n in out["all"]]
    assert degs == sorted(degs, reverse=True)
    assert not any(n["orphan"] for n in out["no"])
    assert out["a"] and all(n["area"] == "02-cooking" for n in out["a"])
    for n in out["all"]:
        assert n["id"].split("/")[0] not in ("activity", "Personal", "tools", "_templates")
