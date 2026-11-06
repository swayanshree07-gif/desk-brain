"""Web app tests (FastAPI TestClient against the fixture desk; no real server)."""

import shutil

import pytest
from fastapi.testclient import TestClient

from deskbrain.app import create_app

@pytest.fixture
def client(desk, tmp_path):
    app = create_app(desk, tmp_path / "ix.sqlite")
    with TestClient(app) as c:
        yield c
    app.state.desk.close()


def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    j = r.json()
    assert j["status"] == "ok" and j["desk"] == "desk"
    assert j["counts"]["pages"] == 6
    assert j["counts"]["doubts"] >= 1


def test_search_known_query(client):
    j = client.get("/api/search", params={"q": "sourdough starter"}).json()
    assert j["hits"], j
    top = j["hits"][0]
    assert top["rel"] == "02-cooking/bread.md"
    assert "<mark>" in top["snippet"]
    assert top["page_title"]
    assert {"line", "chunk_id", "score", "breadcrumb", "section_slug"} <= set(top)


def test_search_per_page_cap_and_all(client):
    capped = client.get("/api/search", params={"q": "tomato", "k": 50}).json()["hits"]
    per = {}
    for h in capped:
        per[h["rel"]] = per.get(h["rel"], 0) + 1
    assert max(per.values()) <= 2
    full = client.get("/api/search", params={"q": "tomato", "k": 50, "all": "true"}).json()["hits"]
    assert len(full) >= len(capped)


def test_search_empty_and_nohit(client):
    assert client.get("/api/search", params={"q": ""}).json()["hits"] == []
    assert client.get("/api/search", params={"q": "zzqqxx"}).json()["hits"] == []
    assert client.get("/api/search", params={"q": "x", "k": 0}).status_code == 422


def test_snippet_is_escaped(client, desk, tmp_path):
    d = tmp_path / "desk"
    shutil.copytree(desk, d)
    (d / "01-garden" / "xss.md").write_text("# Evil\n\nzebra <script>alert(1)</script> zebra\n")
    app = create_app(d, tmp_path / "ix2.sqlite")
    with TestClient(app) as c:
        hit = c.get("/api/search", params={"q": "zebra"}).json()["hits"][0]
        assert "<script>" not in hit["snippet"] and "&lt;script&gt;" in hit["snippet"]
        page = c.get("/api/page", params={"rel": "01-garden/xss.md"}).json()
        assert "<script>" not in page["html"]
    app.state.desk.close()


def test_page_render_and_links(client):
    j = client.get("/api/page", params={"rel": "03-music/practice.md"}).json()
    h = j["html"]
    assert j["title"] == "Practice Log"
    assert 'id="good-links"' in h
    assert 'href="#/page/03-music/guitar.md"' in h                       # wikilink by file name
    assert 'href="#/page/02-cooking/bread.md?h=findings"' in h            # wikilink by title + anchor
    assert 'href="#/page/01-garden/greenhouse/README.md"' in h            # folder -> README
    assert 'href="#/page/03-music/practice.md?h=bad-links"' in h          # in-page anchor
    assert "link-broken" in h and "link-private" in h
    assert "[[" not in h
    # Nothing in the page view points at a private area or leaks its path.
    assert "activity/" not in h and "Personal/" not in h and "2026-11-01.md" not in h
    for l in j["links"]:
        if l["status"] == "excluded":
            assert l["raw"] is None and l["target"] is None


def test_backlinks(client):
    j = client.get("/api/page", params={"rel": "03-music/guitar.md"}).json()
    assert "03-music/practice.md" in [b["rel"] for b in j["backlinks"]]


@pytest.mark.parametrize("rel", [
    "Personal/diary.md", "activity/2026-11-01.md", "tools/notes.md", "_templates/page.md",
    "03-music/_template/README.md", ".obsidian/cache.md", "../CLAUDE.md", "/etc/passwd",
    "01-garden/../Personal/diary.md", "02-cooking/recipe.pdf", "nope.md",
])
def test_page_refuses_private_and_unknown(client, rel):
    r = client.get("/api/page", params={"rel": rel})
    assert r.status_code == 404
    assert r.json() == {"detail": "not found"}


def test_private_text_never_in_api(client, desk):
    secret_lines = []
    for p in ("Personal/diary.md", "activity/2026-11-01.md", "tools/notes.md"):
        for line in (desk / p).read_text().splitlines():
            if line.strip():
                secret_lines.append(line.strip())
    assert secret_lines
    bodies = [client.get("/api/health").text]
    for q in ("diary", "secret", "private", "day log", "tools", "canary", "xylophone moonbeam", "walrus", "kumquat"):
        for mode in ("keyword", "semantic", "hybrid"):
            bodies.append(client.get("/api/search", params={"q": q, "k": 100, "all": "true", "mode": mode}).text)
    for rel in client.app.state.desk.index.page_rels():
        bodies.append(client.get("/api/page", params={"rel": rel}).text)
    bodies.append(client.get("/api/graph").text)
    bodies.append(client.get("/api/health").text)
    for q in ("", "diary", "secret"):
        bodies.append(client.get("/api/doubts", params={"q": q}).text)
    bodies.append(client.get("/api/cards").text)
    blob = "\n".join(bodies)
    for s in secret_lines:
        assert s not in blob, s


def test_reindex_picks_up_change(desk, tmp_path):
    d = tmp_path / "desk"
    shutil.copytree(desk, d)
    app = create_app(d, tmp_path / "ix.sqlite")
    with TestClient(app) as c:
        assert c.post("/api/reindex").json()["changed"] == []
        assert c.get("/api/search", params={"q": "quokka"}).json()["hits"] == []
        p = d / "02-cooking" / "bread.md"
        p.write_text(p.read_text() + "\n\n## Quokka note\n\nA quokka visited the bakery.\n")
        r = c.post("/api/reindex").json()
        assert r["changed"] == ["02-cooking/bread.md"]
        hits = c.get("/api/search", params={"q": "quokka"}).json()["hits"]
        assert hits and hits[0]["rel"] == "02-cooking/bread.md" and hits[0]["section_slug"] == "quokka-note"
    app.state.desk.close()


def test_frontend_served(client):
    r = client.get("/")
    assert r.status_code == 200 and "Desk Brain" in r.text and "/static/app.js" in r.text
    assert client.get("/static/app.js").status_code == 200
    assert client.get("/static/style.css").status_code == 200


def test_foreign_host_rejected(client):
    assert client.get("/api/health", headers={"host": "evil.example"}).status_code == 400
    assert client.get("/api/health", headers={"host": "127.0.0.1:8765"}).status_code == 200


def test_no_docs_endpoints(client):
    for p in ("/docs", "/openapi.json", "/redoc"):
        assert client.get(p).status_code == 404
