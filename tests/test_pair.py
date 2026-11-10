"""Day 25: loopback-only pairing page."""
import pytest
from fastapi.testclient import TestClient

from deskbrain.app import create_app

TOKEN = "p" * 10 + "Pair-T0ken_abcdefghijk"
LAN = "192.168.50.7"


@pytest.fixture
def app(desk, tmp_path):
    a = create_app(desk, tmp_path / "ix.sqlite", semantic=False, llm=None,
                   lan={"addr": LAN, "token": TOKEN, "port": 8799})
    yield a
    a.state.desk.close()


def test_pair_on_loopback(app):
    r = TestClient(app, base_url="http://127.0.0.1:8765").get("/pair")
    assert r.status_code == 200
    assert f"http://{LAN}:8799/?token={TOKEN}" in r.text
    assert r.headers["cache-control"] == "no-store"
    assert r.headers["referrer-policy"] == "no-referrer"


@pytest.mark.parametrize("hdr", [{}, {"Authorization": f"Bearer {TOKEN}"}])
def test_pair_hidden_on_lan(app, hdr):
    r = TestClient(app, base_url=f"http://{LAN}:8765", follow_redirects=False).get("/pair", headers=hdr)
    assert r.status_code in (401, 404)
    assert TOKEN not in r.text


def test_pair_hidden_on_lan_with_cookie_and_spoofed_host(app):
    c = TestClient(app, base_url=f"http://{LAN}:8765", follow_redirects=False)
    assert c.get(f"/?token={TOKEN}").status_code == 303
    r = c.get("/pair")
    assert r.status_code == 404 and TOKEN not in r.text
    r = c.get("/pair", headers={"Host": "127.0.0.1"})
    assert TOKEN not in r.text and r.status_code in (401, 404)


def test_pair_404_without_lan(desk, tmp_path):
    a = create_app(desk, tmp_path / "ix.sqlite", semantic=False, llm=None)
    try:
        assert TestClient(a, base_url="http://127.0.0.1:8765").get("/pair").status_code == 404
    finally:
        a.state.desk.close()
