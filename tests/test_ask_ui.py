"""Day 21: Ask the desk UI (#/ask): answer with clickable [n] citations, sources, privacy."""

import json
import re
import shutil
import subprocess

import pytest
from fastapi.testclient import TestClient

from deskbrain.app import STATIC_DIR, create_app

JS = (STATIC_DIR / "app.js").read_text()
PRIVATE = re.compile(r"(^|/)(activity|Personal)/")


def test_assets_have_ask_page(desk, tmp_path):
    app = create_app(desk, tmp_path / "ix.sqlite", semantic=False)
    with TestClient(app) as c:
        assert 'href="#/ask"' in c.get("/").text
        js = c.get("/static/app.js").text
        assert 'path === "/ask"' in js and "/api/ask?q=" in js and "function answerHtml" in js
        assert ".answer" in c.get("/static/style.css").text
    app.state.desk.close()


def test_citation_slugs_exist_as_heading_ids(desk, tmp_path):
    app = create_app(desk, tmp_path / "ix.sqlite", semantic=False)
    with TestClient(app) as c:
        for q in ["How deep should I plant tomato seeds?", "guitar strings", "bread dough", "watering"]:
            d = c.get("/api/ask", params={"q": q}).json()
            for cit in d["citations"]:
                assert not PRIVATE.search(cit["rel"])
                html = c.get("/api/page", params={"rel": cit["rel"]}).json()["html"]
                assert f'id="{cit["slug"]}"' in html, (cit["rel"], cit["slug"])
    app.state.desk.close()


def test_private_text_never_in_answers(desk, tmp_path):
    secrets = ["xylophone-moonbeam", "walrus-paperclip", "moonbeam-flowerpot", "Moonbeam-xylophone"]  # fixture canaries
    app = create_app(desk, tmp_path / "ix.sqlite", semantic=False)
    with TestClient(app) as c:
        for q in secrets + ["xylophone", "moonbeam", "walrus paperclip", "private diary", "personal", "activity log", "what did I do today"]:
            d = c.get("/api/ask", params={"q": q}).json()
            d.pop("question")  # the echoed question is the caller's own text
            body = json.dumps(d, ensure_ascii=False)
            assert "activity/" not in body and "Personal/" not in body
            for s in secrets:
                assert s.lower() not in body.lower()
    app.state.desk.close()


@pytest.mark.skipif(not shutil.which("node"), reason="node not installed")
def test_answer_and_sources_html_escape_and_link():
    body = JS[JS.index("function citeLink"):JS.index("async function showAsk")]
    script = (JS[JS.index("const esc"):JS.index("async function api")] + body +
              "const c={n:1,rel:'a/<x>.md',slug:'q-a',heading:'<i>H</i>',line:3,end_line:5,text:'<script>t</script>'};"
              "console.log(JSON.stringify([answerHtml({known:true,answer:[{n:1,text:'<b>yes</b>'}],citations:[c]}),"
              "answerHtml({known:false,answer:[],citations:[]}), sourcesHtml({citations:[c]},true), sourcesHtml({citations:[]}), answerHtml(null)]))")
    out = json.loads(subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True).stdout)
    assert "&lt;b&gt;yes" in out[0] and "<b>" not in out[0]
    assert 'href="#/page/a/%3Cx%3E.md?h=q-a"' in out[0] and ">[1]</a>" in out[0]
    assert "know" in out[1]
    assert "<script>" not in out[2] and "&lt;i&gt;H" in out[2] and "open" in out[2] and "lines 3–5" in out[2]
    assert out[3] == "" and out[4] == ""
