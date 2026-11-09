"""Day 22: optional loopback-only LLM phrasing for Ask, validated citations, extractive fallback."""

import json
import re
import shutil
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from fastapi.testclient import TestClient

from deskbrain import llm
from deskbrain.app import STATIC_DIR, create_app

Q = "How deep should I plant tomato seeds?"
CFG = {"url": "http://127.0.0.1:9/v1", "model": "fake", "timeout": 2}


@pytest.mark.parametrize("url", ["http://127.0.0.1:11434/v1", "http://localhost:8080/v1",
                                 "http://[::1]:8000/v1", "https://127.5.6.7/v1/"])
def test_loopback_urls_accepted(url):
    assert llm.check_loopback_url(url) == url.rstrip("/")


@pytest.mark.parametrize("url", ["http://192.168.1.5:11434/v1", "http://0.0.0.0:8000", "http://10.0.0.1",
                                 "https://api.openai.com/v1", "http://localhost.evil.com/v1",
                                 "http://127.0.0.1.nip.io/v1", "ftp://127.0.0.1/", "file:///etc/passwd",
                                 "http://user:pw@127.0.0.1/v1", "http://[::ffff:8.8.8.8]/v1", ""])
def test_non_loopback_urls_rejected(url):
    with pytest.raises(llm.LLMConfigError):
        llm.check_loopback_url(url)


def test_env_config():
    assert llm.config_from_env({}) is None
    assert llm.config_from_env({"DESKBRAIN_LLM_URL": "http://127.0.0.1:1/v1"})["model"] == "local"
    with pytest.raises(llm.LLMConfigError):
        llm.config_from_env({"DESKBRAIN_LLM_URL": "http://8.8.8.8/v1"})


def test_create_app_refuses_public_llm(desk, tmp_path, monkeypatch):
    monkeypatch.setenv("DESKBRAIN_LLM_URL", "https://example.com/v1")
    with pytest.raises(llm.LLMConfigError):
        create_app(desk, tmp_path / "ix.sqlite", semantic=False)


def test_validate():
    v = llm.validate("Plant them 6 mm deep [1]. Keep warm [2][9]. Made up claim. Also [7] nope.", {1, 2})
    assert v == [{"text": "Plant them 6 mm deep.", "n": 1, "cites": [1]},
                 {"text": "Keep warm.", "n": 2, "cites": [2]}]
    assert llm.validate("DONT_KNOW", {1}) == []
    assert llm.validate("No citations here at all.", {1}) is None
    assert llm.validate("", {1}) is None


def _app(desk, tmp_path, cfg, monkeypatch, reply=None, exc=None):
    seen = []

    def fake(c, messages):
        seen.append(messages)
        if exc:
            raise exc
        return reply
    monkeypatch.setattr(llm, "post_chat", fake)
    app = create_app(desk, tmp_path / "ix.sqlite", semantic=False, llm=cfg)
    return TestClient(app), seen


def test_off_by_default(desk, tmp_path, monkeypatch):
    monkeypatch.delenv("DESKBRAIN_LLM_URL", raising=False)
    c, seen = _app(desk, tmp_path, "env", monkeypatch, reply="x [1].")
    d = c.get("/api/ask", params={"q": Q}).json()
    assert d["llm"] == "off" and d["answer_method"] == "keyword" and not seen
    assert c.get("/api/health").json()["llm"] is False


def test_llm_answer_used_and_cited(desk, tmp_path, monkeypatch):
    c, seen = _app(desk, tmp_path, CFG, monkeypatch, reply="Sow them about 6 mm deep [1]. Bogus [42].")
    d = c.get("/api/ask", params={"q": Q}).json()
    assert d["llm"] == "ok" and d["answer_method"] == "llm"
    assert d["answer"] == [{"text": "Sow them about 6 mm deep.", "n": 1, "cites": [1]}]
    assert d["quotes"] and all("text" in q for q in d["quotes"])
    prompt = seen[0][1]["content"]
    assert "[1] " in prompt and Q in prompt and "Personal/" not in prompt and "activity/" not in prompt


@pytest.mark.parametrize("reply,exc,status", [("no citations at all", None, "rejected"),
                                              (None, TimeoutError(), "error: TimeoutError"),
                                              ("DONT_KNOW", None, "dont-know")])
def test_fallback_to_extractive(desk, tmp_path, monkeypatch, reply, exc, status):
    c, _ = _app(desk, tmp_path, CFG, monkeypatch, reply=reply, exc=exc)
    d = c.get("/api/ask", params={"q": Q}).json()
    assert d["llm"] == status and d["answer_method"] == "keyword" and d["known"]
    assert any("6 mm deep" in a["text"] for a in d["answer"])


def test_not_called_when_unknown(desk, tmp_path, monkeypatch):
    c, seen = _app(desk, tmp_path, CFG, monkeypatch, reply="Paris [1].")
    d = c.get("/api/ask", params={"q": "what is the capital of france"}).json()
    assert not d["known"] and d["llm"] == "skipped" and not seen


class _Fake(BaseHTTPRequestHandler):
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.server.got.append((self.path, body))
        if self.server.redirect:
            self.send_response(302); self.send_header("Location", "http://example.com/"); self.end_headers()
            return
        out = json.dumps({"choices": [{"message": {"content": "Six mm [1]."}}]}).encode()
        self.send_response(200); self.send_header("Content-Length", str(len(out))); self.end_headers()
        self.wfile.write(out)

    def log_message(self, *a):
        pass


@pytest.mark.parametrize("redirect", [False, True])
def test_post_chat_real_http(redirect):
    srv = HTTPServer(("127.0.0.1", 0), _Fake)
    srv.got, srv.redirect = [], redirect
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        cfg = {"url": f"http://127.0.0.1:{srv.server_port}/v1", "model": "m", "timeout": 5}
        if redirect:
            with pytest.raises(Exception):
                llm.post_chat(cfg, [{"role": "user", "content": "hi"}])
        else:
            assert llm.post_chat(cfg, [{"role": "user", "content": "hi"}]) == "Six mm [1]."
            path, body = srv.got[0]
            assert path == "/v1/chat/completions" and body["model"] == "m" and not body["stream"]
    finally:
        srv.shutdown(); srv.server_close()


@pytest.mark.skipif(not shutil.which("node"), reason="node not installed")
def test_answer_html_multi_cites():
    js = (STATIC_DIR / "app.js").read_text()
    script = (js[js.index("const esc"):js.index("async function api")] +
              js[js.index("function citeLink"):js.index("async function showAsk")] +
              "const c=n=>({n,rel:'a.md',slug:'s',heading:'H',line:1,end_line:2,text:'t'});"
              "console.log(JSON.stringify([answerHtml({known:true,answer_method:'llm',"
              "answer:[{n:1,cites:[1,2],text:'<i>x</i>'}],citations:[c(1),c(2)]}),"
              "answerHtml({known:true,answer_method:'keyword',answer:[{n:2,text:'q'}],citations:[c(2)]})]))")
    out = json.loads(subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True).stdout)
    assert ">[1]</a>" in out[0] and ">[2]</a>" in out[0] and "local model" in out[0] and "<i>" not in out[0]
    assert ">[2]</a>" in out[1] and "quoted" in out[1]
