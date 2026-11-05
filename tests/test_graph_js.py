"""Graph view (static/graph.js): pure layout/filter code run under Node, plus static serving.

Skipped when ``node`` is not installed (the app itself needs no Node)."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from deskbrain.app import STATIC_DIR, create_app
from deskbrain.graph import build_graph
from deskbrain.store import Index

NODE = shutil.which("node")
CHECK = Path(__file__).parent / "js" / "graph_check.js"


def _report(desk, tmp_path):
    with Index(tmp_path / "ix.sqlite", desk) as ix:
        ix.update()
        g = build_graph(ix)
    gj = tmp_path / "graph.json"
    gj.write_text(json.dumps(g))
    out = subprocess.run([NODE, str(CHECK), str(STATIC_DIR / "graph.js"), str(gj)],
                         capture_output=True, text=True, timeout=60, check=True)
    return g, json.loads(out.stdout)


@pytest.fixture
def report(desk, tmp_path):
    if not NODE:
        pytest.skip("node not installed")
    return _report(desk, tmp_path)


def test_filter_defaults(report):
    g, r = report
    pages = sorted(n["id"] for n in g["nodes"] if n["type"] == "page")
    assert r["default_nodes"] == pages              # folders hidden by default
    assert r["default_edge_kinds"] == ["link"]
    assert r["folders_nodes"] == len(g["nodes"]) and r["folders_edges"] == len(g["edges"])


def test_filter_by_area(report):
    _, r = report
    assert r["area:(root)"]["nodes"] == ["README.md"]
    assert r["area:01-garden"]["nodes"] == ["01-garden/greenhouse/README.md", "01-garden/tomatoes.md",
                                           "dir:01-garden", "dir:01-garden/greenhouse"]
    assert r["area:03-music"]["nodes"] == ["03-music/guitar.md", "03-music/practice.md", "dir:03-music"]
    assert all(v["edges_ok"] for k, v in r.items() if k.startswith("area:"))


def test_layout_sane(report):
    _, r = report
    assert r["deterministic"] and r["finite"] and r["positions"] == 10
    assert r["mean_linked"] < r["mean_unlinked"]   # springs pull linked pages together
    assert r["min_dist"] > 5                       # repulsion keeps nodes apart
    assert r["empty_layout"] == 0
    assert r["big_finite"] and r["big_ms"] < 5000


def test_orphan_filter(desk, tmp_path):
    if not NODE:
        pytest.skip("node not installed")
    d = tmp_path / "desk"
    shutil.copytree(desk, d)
    (d / "02-cooking" / "lonely.md").write_text("# Lonely\n\nNo links.\n")
    _, r = _report(d, tmp_path)
    assert "02-cooking/lonely.md" in r["default_nodes"]
    assert "02-cooking/lonely.md" not in r["no_orphans"] and len(r["no_orphans"]) == 6


def test_graph_assets_served(desk, tmp_path):
    app = create_app(desk, tmp_path / "ix.sqlite", semantic=False)
    with TestClient(app) as c:
        home = c.get("/").text
        assert '/static/graph.js' in home and 'href="#/graph"' in home
        assert home.index("graph.js") < home.index("app.js")   # app.js uses window.DeskGraph
        js = c.get("/static/graph.js")
        assert js.status_code == 200 and "DeskGraph" in js.text
        assert "#/graph" in c.get("/static/app.js").text
        # graph.js must not inject desk strings unescaped: every ${n.title}-like insert goes through esc()
        assert "innerHTML" in js.text and "esc(n.title)" in js.text
    app.state.desk.close()
