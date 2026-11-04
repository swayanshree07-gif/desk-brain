"""Graph data (deskbrain/graph.py) and /api/graph on the fixture desk."""

import shutil

from fastapi.testclient import TestClient

from deskbrain.app import create_app
from deskbrain.graph import build_graph
from deskbrain.store import Index

EXPECTED_EDGES = {
    ("01-garden/greenhouse/README.md", "01-garden/tomatoes.md"),
    ("01-garden/tomatoes.md", "02-cooking/bread.md"),
    ("01-garden/tomatoes.md", "01-garden/greenhouse/README.md"),
    ("03-music/practice.md", "03-music/guitar.md"),
    ("03-music/practice.md", "02-cooking/bread.md"),
    ("03-music/practice.md", "01-garden/tomatoes.md"),
    ("03-music/practice.md", "01-garden/greenhouse/README.md"),
    ("03-music/practice.md", "README.md"),
    ("README.md", "01-garden/tomatoes.md"),
    ("README.md", "02-cooking/bread.md"),
}


def _graph(root, tmp_path, **kw):
    with Index(tmp_path / "ix.sqlite", root) as ix:
        ix.update()
        return build_graph(ix, **kw)


def test_link_edges_and_degrees(desk, tmp_path):
    g = _graph(desk, tmp_path)
    links = [e for e in g["edges"] if e["kind"] == "link"]
    assert {(e["source"], e["target"]) for e in links} == EXPECTED_EDGES
    assert len(links) == len(EXPECTED_EDGES)                       # deduplicated
    w = {(e["source"], e["target"]): e for e in links}
    g2 = w[("03-music/practice.md", "03-music/guitar.md")]
    assert g2["weight"] == 3 and g2["bad_anchor"] is True          # 2 good links + 1 bad anchor
    nodes = {n["id"]: n for n in g["nodes"]}
    assert (nodes["01-garden/tomatoes.md"]["in"], nodes["01-garden/tomatoes.md"]["out"]) == (3, 2)
    assert (nodes["03-music/practice.md"]["in"], nodes["03-music/practice.md"]["out"]) == (0, 5)
    assert nodes["02-cooking/bread.md"]["degree"] == 3
    assert nodes["03-music/guitar.md"]["degree"] == 1
    assert g["stats"] == {"pages": 6, "folders": 4, "link_edges": 10, "orphans": 0, "broken": 7}


def test_node_counts_and_areas(desk, tmp_path):
    g = _graph(desk, tmp_path)
    nodes = {n["id"]: n for n in g["nodes"]}
    assert g["areas"] == ["", "01-garden", "02-cooking", "03-music"]
    t = nodes["01-garden/tomatoes.md"]
    assert t["area"] == "01-garden" and t["folder"] == "01-garden" and t["title"] == "Tomatoes"
    assert t["open_doubts"] >= 1 and t["doubts"] >= t["open_doubts"] and t["findings"] >= 1
    assert t["bad_links"] == 1 and nodes["03-music/practice.md"]["bad_links"] == 6
    assert nodes["README.md"]["area"] == ""


def test_folder_nodes_and_edges(desk, tmp_path):
    g = _graph(desk, tmp_path)
    folders = {n["id"] for n in g["nodes"] if n["type"] == "folder"}
    assert folders == {"dir:01-garden", "dir:01-garden/greenhouse", "dir:02-cooking", "dir:03-music"}
    fe = {(e["source"], e["target"]) for e in g["edges"] if e["kind"] == "folder"}
    assert ("dir:01-garden", "dir:01-garden/greenhouse") in fe
    assert ("dir:01-garden/greenhouse", "01-garden/greenhouse/README.md") in fe
    assert ("dir:03-music", "03-music/guitar.md") in fe
    ids = {n["id"] for n in g["nodes"]}
    assert all(e["source"] in ids and e["target"] in ids for e in g["edges"])
    plain = _graph(desk, tmp_path, folders=False)
    assert all(n["type"] == "page" for n in plain["nodes"]) and all(e["kind"] == "link" for e in plain["edges"])


def test_orphan_detection(desk, tmp_path):
    d = tmp_path / "desk"
    shutil.copytree(desk, d)
    (d / "02-cooking" / "lonely.md").write_text("# Lonely\n\nNobody links here and it links nowhere.\n")
    g = _graph(d, tmp_path)
    orphans = [n["id"] for n in g["nodes"] if n.get("orphan")]
    assert orphans == ["02-cooking/lonely.md"] and g["stats"]["orphans"] == 1


def test_broken_list(desk, tmp_path):
    g = _graph(desk, tmp_path)
    by = {(b["src"], b["line"]): b["status"] for b in g["broken"]}
    assert by[("01-garden/tomatoes.md", 32)] == "broken"
    assert by[("03-music/practice.md", 20)] == "bad-anchor"
    assert by[("03-music/practice.md", 22)] == "outside"
    assert all(b["status"] != "excluded" for b in g["broken"])


def test_api_graph_private_free(desk, tmp_path):
    app = create_app(desk, tmp_path / "ix.sqlite", semantic=False)
    with TestClient(app) as c:
        r = c.get("/api/graph")
        assert r.status_code == 200
        j = r.json()
        assert j["stats"]["pages"] == 6
        for bad in ("activity", "Personal", "tools/", "_templates", "_template", ".obsidian", "diary.md"):
            assert bad not in r.text, bad
        assert c.get("/api/graph", params={"folders": "false"}).json()["stats"]["folders"] == 0
    app.state.desk.close()
