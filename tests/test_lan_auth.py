"""Day 24: LAN mode part 2 — token auth middleware, host allow-list, loopback stays token-free."""
import pytest
from fastapi.testclient import TestClient

from deskbrain.app import create_app
from deskbrain.auth import COOKIE, cookie_value

TOKEN = "t" * 10 + "S3cr3t-T0ken_abcdefghij"
LAN = "192.168.50.7"


@pytest.fixture
def app(desk, tmp_path):
    a = create_app(desk, tmp_path / "ix.sqlite", semantic=False, llm=None,
                   lan={"addr": LAN, "token": TOKEN})
    yield a
    a.state.desk.close()


def lan_client(app, **kw):
    return TestClient(app, base_url=f"http://{LAN}:8765", follow_redirects=False, **kw)


ROUTES = ["/", "/api/health", "/api/search?q=frida", "/api/doubts", "/api/graph", "/static/app.js"]


@pytest.mark.parametrize("path", ROUTES)
def test_no_token_401(app, path):
    r = lan_client(app).get(path)
    assert r.status_code == 401
    assert "frida" not in r.text.lower() and r.headers.get("set-cookie") is None


@pytest.mark.parametrize("hdr", [{"Authorization": f"Bearer {TOKEN}"}, {"X-Desk-Token": TOKEN}])
def test_header_token_ok(app, hdr):
    c = lan_client(app)
    for p in ROUTES:
        assert c.get(p, headers=hdr).status_code == 200, p
    assert c.post("/api/index/update", headers=hdr).status_code != 401


@pytest.mark.parametrize("bad", ["", "x", TOKEN[:-1], TOKEN + "x", TOKEN.upper(), cookie_value(TOKEN)])
def test_wrong_token_401(app, bad):
    c = lan_client(app)
    assert c.get("/api/health", headers={"Authorization": f"Bearer {bad}"}).status_code == 401
    assert c.get("/api/health", headers={"X-Desk-Token": bad}).status_code == 401
    assert c.get(f"/api/health?token={bad}").status_code == 401
    if bad == cookie_value(TOKEN):
        return  # that IS the valid cookie value; it is only refused as a token
    c.cookies.set(COOKIE, bad)
    assert c.get("/api/health").status_code == 401


def test_query_token_to_cookie(app):
    c = lan_client(app)
    r = c.get(f"/api/search?q=frida&token={TOKEN}")
    assert r.status_code == 303
    assert r.headers["location"] == "/api/search?q=frida"
    sc = r.headers["set-cookie"]
    assert "HttpOnly" in sc and "SameSite=Strict" in sc and TOKEN not in sc
    assert c.get(r.headers["location"]).status_code == 200      # cookie now carries auth
    assert c.get("/api/health").status_code == 200
    assert lan_client(app).get("/api/health").status_code == 401  # fresh client: no cookie


def test_query_token_only_for_get(app):
    assert lan_client(app).post(f"/api/index/update?token={TOKEN}").status_code == 401


def test_wrong_host_rejected(app):
    c = TestClient(app, base_url="http://evil.example:8765")
    assert c.get("/api/health", headers={"X-Desk-Token": TOKEN}).status_code == 400
    c = TestClient(app, base_url="http://192.168.50.8:8765")
    assert c.get("/api/health", headers={"X-Desk-Token": TOKEN}).status_code == 400


def test_loopback_token_free(app):
    # TestClient puts the URL host into scope["server"]; real uvicorn puts the socket's IP there.
    for base in ("http://127.0.0.1:8765",):
        c = TestClient(app, base_url=base)
        assert c.get("/api/health").status_code == 200
        assert c.get("/").status_code == 200


def test_loopback_host_header_on_lan_socket_needs_token(app):
    # Spoofing Host: 127.0.0.1 over the LAN socket must not skip auth: we trust the socket, not the header.
    c = lan_client(app)
    assert c.get("/api/health", headers={"Host": "127.0.0.1"}).status_code == 401


def test_default_has_no_auth_and_refuses_lan_host(desk, tmp_path):
    a = create_app(desk, tmp_path / "ix.sqlite", semantic=False, llm=None)
    try:
        assert TestClient(a).get("/api/health").status_code == 200
        assert TestClient(a, base_url=f"http://{LAN}").get("/api/health").status_code == 400
    finally:
        a.state.desk.close()


def test_lan_config_refuses_public(desk, tmp_path):
    with pytest.raises(ValueError):
        create_app(desk, tmp_path / "ix.sqlite", semantic=False, llm=None,
                   lan={"addr": "8.8.8.8", "token": TOKEN})
    with pytest.raises(ValueError):
        create_app(desk, tmp_path / "ix2.sqlite", semantic=False, llm=None,
                   lan={"addr": LAN, "token": "short"})
