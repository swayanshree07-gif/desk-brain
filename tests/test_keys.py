"""Day 27 polish: keyboard shortcuts (static/keys.js under Node) and their wiring/serving."""
import json
import shutil
import subprocess

import pytest
from fastapi.testclient import TestClient

from deskbrain.app import STATIC_DIR, create_app

NODE = shutil.which("node")

CASES = [
    (dict(key="/"), {"focus": "#q"}),
    (dict(key="/", typing=True), None),
    (dict(key="a", typing=True), None),
    (dict(key="Escape", typing=True), {"blur": True}),
    (dict(key="Escape"), {"help": False}),
    (dict(key="?"), {"help": "toggle"}),
    (dict(key="d"), {"go": "#/doubts"}),
    (dict(key="w"), {"go": "#/digest"}),
    (dict(key="h"), {"go": "#/"}),
    (dict(key="d", ctrl=True), None),
    (dict(key="r", meta=True), None),
    (dict(key="x"), None),
    (dict(key="toString"), None),
    (dict(key=" ", path="/review", reviewing=True, shown=False), {"review": "reveal"}),
    (dict(key=" ", path="/review", reviewing=True, shown=True), None),
    (dict(key="3", path="/review", reviewing=True, shown=True), {"review": 3}),
    (dict(key="3", path="/review", reviewing=True, shown=False), None),
    (dict(key="3", path="/"), None),
]


@pytest.mark.skipif(not NODE, reason="node not installed")
def test_key_actions():
    src = (STATIC_DIR / "keys.js").read_text()
    prog = src + "\nconst cases=" + json.dumps([c for c, _ in CASES]) + \
        ";console.log(JSON.stringify(cases.map(keyAction)));"
    out = subprocess.run([NODE, "-e", prog], capture_output=True, text=True, timeout=30, check=True)
    got = json.loads(out.stdout)
    for (case, want), g in zip(CASES, got):
        assert g == want, (case, g)


def test_keys_served_and_wired(desk, tmp_path):
    c = TestClient(create_app(desk, tmp_path / "ix.sqlite"))
    html = c.get("/").text
    assert html.index("/static/keys.js") < html.index("/static/app.js")
    r = c.get("/static/keys.js")
    assert r.status_code == 200 and "keyAction" in r.text
    app_js = (STATIC_DIR / "app.js").read_text()
    assert "keyAction(" in app_js and "KEYS_HELP" in app_js
