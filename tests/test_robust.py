"""Day 27 robustness: odd Markdown, huge files, non-UTF-8, unreadable files, error pages.

Every test works on a tmp copy of the fixture desk (the fixture is never touched).
"""

import os
import random
import shutil

import pytest
from fastapi.testclient import TestClient

from deskbrain.app import create_app
from deskbrain.parser import parse
from deskbrain.render import render_page
from deskbrain.store import Index

ODD = {
    "odd/empty.md": b"",
    "odd/only-ws.md": b"   \n\n\t\n",
    "odd/bom.md": "\ufeff# BOM title\n\nText.\n".encode("utf-8"),
    "odd/crlf.md": b"# CRLF\r\n\r\n### \xf0\x9f\x94\xb4 Q: crlf doubt?\r\n- Context: x\r\n",
    "odd/latin1.md": "# Caf\u00e9 latin1\n\nna\u00efve r\u00e9sum\u00e9\n".encode("latin-1"),
    "odd/binaryish.md": b"# bin\n\x00\x01\x02\xff\xfe garbage \x00\n",
    "odd/unclosed-fence.md": b"# Fence\n\n```python\nprint('never closed')\n## not a heading\n",
    "odd/deep.md": ("".join(f"{'#' * (i % 6 + 1)} H{i}\n" for i in range(300))).encode(),
    "odd/weird-links.md": b"# L\n\n[a]() [b](<>) [c](../../../etc/passwd) [d](#) [e]([[x]]) [[]] [[|]] [f](%zz.md)\n",
    "odd/nested.md": (b"> " * 200) + b"quote\n" + (b"- " * 200) + b"list\n",
    "odd/html.md": b"# H\n\n<script>alert(1)</script><img src=x onerror=alert(2)>\n",
    "odd/q-no-body.md": "### 🔴 Q:\n### 🟢 Q: \n- Answer:\n".encode(),
    "odd/name with spaces & ü.md": "# Spaces ü\n\nhello\n".encode(),
}


@pytest.fixture
def odd_desk(desk, tmp_path):
    d = tmp_path / "desk"
    shutil.copytree(desk, d)
    for rel, data in ODD.items():
        p = d / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
    # A ~3 MB page (many sections).
    big = "".join(f"## Section {i}\n\n" + ("lorem ipsum tomato " * 40) + "\n\n" for i in range(3000))
    (d / "odd" / "huge.md").write_text("# Huge\n\n" + big)
    return d


@pytest.fixture
def client(odd_desk, tmp_path):
    return TestClient(create_app(odd_desk, tmp_path / "data" / "index.sqlite", semantic=False))


def test_index_survives_odd_files(odd_desk, tmp_path):
    idx = Index(tmp_path / "i.sqlite", odd_desk)
    idx.update()
    rels = {r[0] for r in idx.db.execute("SELECT rel FROM pages")}
    for rel in ODD:
        assert rel in rels, rel
    assert "odd/huge.md" in rels


def test_every_page_renders(client):
    for rel in list(ODD) + ["odd/huge.md"]:
        r = client.get("/api/page", params={"rel": rel})
        assert r.status_code == 200, (rel, r.text[:200])
        html = r.json()["html"]
        assert "<script" not in html and "<img" not in html


def test_bom_and_latin1(client):
    assert client.get("/api/page", params={"rel": "odd/bom.md"}).json()["title"] == "BOM title"
    r = client.get("/api/search", params={"q": "latin1"}).json()
    assert any(h["rel"] == "odd/latin1.md" for h in r["hits"])


def test_crlf_doubt_found(client):
    qs = [d["question"] for d in client.get("/api/doubts").json()["doubts"]]
    assert any("crlf doubt" in q for q in qs)


def test_all_endpoints_ok(client):
    for url in ["/api/health", "/api/graph", "/api/doubts", "/api/review", "/api/digest",
                "/api/index/status", "/api/search?q=tomato", "/api/search?q=%00%ff",
                "/api/search?q=" + "a" * 500, "/api/search?q=*?[(\"'"]:
        r = client.get(url)
        assert r.status_code == 200, (url, r.text[:200])


@pytest.mark.parametrize("rel", ["", "/", "../x.md", "/etc/passwd", "odd/missing.md", "odd", "a\x00b.md",
                                 "odd/../Personal/diary.md", "%2e%2e/x.md", "x" * 1001])
def test_bad_page_requests_are_4xx(client, rel):
    r = client.get("/api/page", params={"rel": rel})
    assert 400 <= r.status_code < 500, (rel, r.status_code)


def test_unknown_api_and_page_404(client):
    assert client.get("/api/nope").status_code == 404
    r = client.get("/no/such/thing")
    assert r.status_code == 404


def test_unreadable_file_skipped(odd_desk, tmp_path):
    if os.geteuid() == 0:
        pytest.skip("root can read anything")
    p = odd_desk / "odd" / "locked.md"
    p.write_text("# locked\n")
    p.chmod(0)
    try:
        c = TestClient(create_app(odd_desk, tmp_path / "d2" / "i.sqlite", semantic=False))
        assert c.get("/api/health").status_code == 200
        assert c.get("/api/page", params={"rel": "odd/locked.md"}).status_code == 404
        assert c.post("/api/reindex").status_code == 200
    finally:
        p.chmod(0o644)


def test_file_vanishes_between_index_and_read(client, odd_desk):
    (odd_desk / "odd" / "bom.md").unlink()
    assert client.get("/api/page", params={"rel": "odd/bom.md"}).status_code == 404


def test_parse_and_render_fuzz():
    rnd = random.Random(1234)
    alphabet = ["#", "##", "###", " ", "\n", "-", "*", "`", "```", "[", "]", "(", ")", "[[", "]]", "|",
                "🔴 Q:", "🟢 Q:", "- Answer:", "- Context:", "**2026-11-01**", ">", "<", "&", "\t",
                "\r\n", "x", "é", "\x00", "{", "}", "#a", "<!--", "-->", "$", "\\"]
    for _ in range(400):
        text = "".join(rnd.choice(alphabet) for _ in range(rnd.randint(0, 120)))
        parse(text, "fuzz/x.md")
        html = render_page(text, "fuzz/x.md", [])
        assert "<script" not in html.lower()


def test_page_turning_unreadable_keeps_old_row(odd_desk, tmp_path):
    if os.geteuid() == 0:
        pytest.skip("root can read anything")
    idx = Index(tmp_path / "i.sqlite", odd_desk)
    idx.update()
    p = odd_desk / "odd" / "bom.md"
    p.write_text("# Changed\n\nmore text\n")
    p.chmod(0)
    try:
        st = idx.update()
        assert st.skipped == ["odd/bom.md"] and "odd/bom.md" not in st.removed
        assert idx.page("odd/bom.md")["title"] == "BOM title"
    finally:
        p.chmod(0o644)
    st = idx.update()
    assert st.changed == ["odd/bom.md"] and idx.page("odd/bom.md")["title"] == "Changed"
