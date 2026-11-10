"""Day 26: hardening — security headers, ask rate limit, failed-token lockout, privacy canary sweep."""
import json
import shutil

import pytest
from fastapi.testclient import TestClient

from deskbrain.app import create_app
from deskbrain.harden import RateLimiter

TOKEN = "k" * 10 + "Harden-T0ken_abcdefghij"
LAN = "192.168.50.7"
CANARY = "zebra-teacup-quasar"


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def test_rate_limiter_window():
    c = Clock()
    rl = RateLimiter(3, 10, clock=c)
    assert [rl.hit("a") for _ in range(4)] == [True, True, True, False]
    assert rl.hit("b") and rl.blocked("a") and not rl.blocked("b")
    assert 1 <= rl.retry_after("a") <= 10
    c.t += 10.01
    assert not rl.blocked("a") and rl.hit("a")


def test_rate_limiter_bounded_keys():
    c = Clock()
    rl = RateLimiter(1, 10, clock=c, max_keys=50)
    for i in range(500):
        rl.hit(f"k{i}")
    assert len(rl._ev) <= 50


def test_rate_limiter_rejects_bad_config():
    with pytest.raises(ValueError):
        RateLimiter(0, 1)
    with pytest.raises(ValueError):
        RateLimiter(1, 0)


@pytest.fixture
def canary_desk(desk, tmp_path):
    d = tmp_path / "desk"
    shutil.copytree(desk, d)
    (d / "Personal" / "canary.md").write_text(f"# Tomatoes secret\n\nTomatoes bread guitar {CANARY}.\n")
    (d / "activity" / "2026-11-10.md").write_text(f"# Day\n\nTomatoes and bread {CANARY} practice.\n")
    (d / "tools" / "c.md").write_text(f"# tool\n\nguitar {CANARY}\n")
    # a public page that links to the private one: the link text must not reveal it
    (d / "01-garden" / "links.md").write_text(
        "# Links\n\nTomatoes see [secret](../Personal/canary.md) and [[canary]].\n")
    return d


@pytest.fixture
def app(canary_desk, tmp_path):
    a = create_app(canary_desk, tmp_path / "ix.sqlite", semantic=False, llm=None,
                   lan={"addr": LAN, "token": TOKEN}, ask_rate=(5, 60), auth_fail_rate=(4, 60))
    yield a
    a.state.desk.close()


def local(app):
    return TestClient(app, base_url="http://127.0.0.1:8765", follow_redirects=False)


def lan(app):
    return TestClient(app, base_url=f"http://{LAN}:8765", follow_redirects=False)


@pytest.mark.parametrize("path", ["/", "/static/app.js", "/api/health", "/api/search?q=x", "/pair"])
def test_security_headers_everywhere(app, path):
    r = local(app).get(path)
    assert r.status_code == 200
    h = r.headers
    assert h["x-content-type-options"] == "nosniff"
    assert h["x-frame-options"] == "DENY"
    assert h["referrer-policy"] == "no-referrer"
    assert "frame-ancestors 'none'" in h["content-security-policy"]
    assert "script-src 'self'" in h["content-security-policy"]
    if path.startswith("/api/") or path == "/pair":
        assert h["cache-control"] == "no-store"


def test_headers_on_error_responses(app):
    r401 = lan(app).get("/api/health")
    assert r401.status_code == 401 and r401.headers["x-frame-options"] == "DENY"
    r400 = TestClient(app, base_url="http://evil.example:8765").get("/api/health")
    assert r400.status_code == 400 and r400.headers["x-content-type-options"] == "nosniff"
    r404 = local(app).get("/api/page?rel=Personal/canary.md")
    assert r404.status_code == 404 and r404.headers["cache-control"] == "no-store"


def test_index_html_has_no_inline_script(app):
    # the CSP forbids inline scripts, so the page must not rely on any
    html = local(app).get("/").text
    import re
    for m in re.finditer(r"<script([^>]*)>(.*?)</script>", html, re.S):
        assert "src=" in m.group(1) and not m.group(2).strip()
    assert not re.search(r"\son[a-z]+=", html)


def test_ask_rate_limited(app):
    c = local(app)
    codes = [c.get("/api/ask?q=tomatoes").status_code for _ in range(7)]
    assert codes[:5] == [200] * 5 and codes[5:] == [429, 429]
    r = c.get("/api/ask?q=tomatoes")
    assert int(r.headers["retry-after"]) >= 1
    assert c.get("/api/ask?q=").status_code == 200        # empty question costs nothing
    assert c.get("/api/search?q=tomatoes").status_code == 200   # other routes unaffected


def test_failed_token_lockout(app):
    c = lan(app)
    for _ in range(4):
        assert c.get("/api/health", headers={"X-Desk-Token": "wrong" * 5}).status_code == 401
    r = c.get("/api/health", headers={"X-Desk-Token": TOKEN})   # even the right token waits
    assert r.status_code == 429 and "retry-after" in r.headers
    assert TOKEN not in r.text
    assert local(app).get("/api/health").status_code == 200    # loopback never locked out


def test_plain_visits_dont_count_as_failures(app):
    c = lan(app)
    for _ in range(10):
        assert c.get("/").status_code == 401
    assert c.get("/api/health", headers={"Authorization": f"Bearer {TOKEN}"}).status_code == 200


def test_canary_sweep_new_routes(canary_desk, tmp_path):
    """Private canary must never come out of the Day 15–25 routes, local or over LAN."""
    app = create_app(canary_desk, tmp_path / "ix2.sqlite", semantic=False, llm=None,
                     lan={"addr": LAN, "token": TOKEN})
    paths = ["/api/recent?limit=50", "/api/related?rel=01-garden/tomatoes.md&k=20",
             "/api/related?rel=01-garden/links.md&k=20", "/api/ask?q=tomatoes+bread+guitar",
             f"/api/ask?q={CANARY}", "/api/ask?q=tomatoes+secret", "/api/search?q=" + CANARY,
             "/api/page?rel=01-garden/links.md", "/api/graph", "/api/doubts", "/api/digest"]
    for client, hdr in ((local(app), {}), (lan(app), {"X-Desk-Token": TOKEN})):
        for p in paths:
            r = client.get(p, headers=hdr)
            assert r.status_code == 200, (p, r.status_code)
            data = r.json() if p.startswith("/api/") else {}
            if isinstance(data, dict):     # the question/query is echoed back by design
                data.pop("question", None), data.pop("query", None)
            body = json.dumps(data).lower()
            assert CANARY not in body, p
            assert "personal/" not in body and "activity/" not in body and "tools/c" not in body, p
    for rel in ("Personal/canary.md", "activity/2026-11-10.md", "tools/c.md"):
        assert local(app).get(f"/api/related?rel={rel}").status_code == 404
    app.state.desk.close()
