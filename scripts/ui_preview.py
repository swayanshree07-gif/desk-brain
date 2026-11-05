"""Write a self-contained HTML snapshot of one frontend route, for headless screenshots.

`firefox --screenshot` captures at the load event, before the live app's fetches finish.
This page loads the real static/app.js + graph.js, but `fetch` is stubbed with API
responses captured beforehand through FastAPI's TestClient, so every await resolves as a
microtask and the route is fully rendered when the screenshot is taken.

Usage:
  .venv/bin/python scripts/ui_preview.py [--desk PATH] OUT.html URL...
      URL = each API URL the route requests, exactly as app.js builds it, e.g.
      /api/health /api/doubts?area=&q=
  firefox --headless --no-remote --profile data/tmp/ffprof --window-size=1400,1000 \\
      --screenshot $PWD/data/tmp/x.png "file://$PWD/OUT.html#/doubts"
Write OUT into data/ (git-ignored): it contains desk text.
"""

import argparse
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi.testclient import TestClient  # noqa: E402

from deskbrain.app import STATIC_DIR, create_app  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--desk", type=Path, default=None)
ap.add_argument("out", type=Path)
ap.add_argument("urls", nargs="+")
a = ap.parse_args()
with tempfile.TemporaryDirectory() as td:
    app = create_app(a.desk, Path(td) / "ix.sqlite", semantic=False)
    with TestClient(app) as c:
        data = {u: c.get(u).json() for u in a.urls}
    app.state.desk.close()
blob = json.dumps(data).replace("</", "<\\/")
body = (STATIC_DIR / "index.html").read_text()
body = body.replace('href="/static/', f'href="file://{STATIC_DIR}/').replace('src="/static/', f'src="file://{STATIC_DIR}/')
stub = f"""<script>const SNAP = {blob};
window.fetch = (u) => Promise.resolve(SNAP[u] ? {{ ok: true, json: () => Promise.resolve(SNAP[u]) }}
  : {{ ok: false, status: 404, statusText: "not in snapshot: " + u, json: () => Promise.resolve({{}}) }});</script>"""
a.out.write_text(body.replace("<script", stub + "\n<script", 1))
print(f"wrote {a.out} with {len(data)} API responses")
