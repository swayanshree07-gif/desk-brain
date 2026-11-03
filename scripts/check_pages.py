"""Render every indexed page through the API and check the output is safe.

Usage: .venv/bin/python scripts/check_pages.py [DESK]     (default: $DESK_PATH or ~/Personal-Desk)
Read-only on the desk; uses data/index.sqlite. Exit code 1 on any problem.
Checks: every page returns 200; every href is an in-app route (#/page/...), http(s) or mailto;
no <script>/raw HTML from the desk; no private folder names in the HTML.
"""
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from fastapi.testclient import TestClient  # noqa: E402

from deskbrain.app import create_app  # noqa: E402

HREF = re.compile(r'href="([^"]*)"')
SAFE = re.compile(r"^(#/page/|https?:|mailto:)")
PRIVATE = re.compile(r'(href|src)="[^"]*(activity/|Personal/|tools/|_templates/)')

app = create_app(Path(sys.argv[1]) if len(sys.argv) > 1 else None)
problems, classes, n = [], Counter(), 0
with TestClient(app) as c:
    for rel in app.state.desk.index.page_rels():
        r = c.get("/api/page", params={"rel": rel})
        n += 1
        if r.status_code != 200:
            problems.append(f"{rel}: HTTP {r.status_code}")
            continue
        h = r.json()["html"]
        for href in HREF.findall(h):
            if not SAFE.match(href):
                problems.append(f"{rel}: unsafe href {href!r}")
        if "<script" in h.lower() or PRIVATE.search(h):
            problems.append(f"{rel}: script or private path in html")
        classes.update(re.findall(r'class="(link-[a-z]+)"', h))
print(f"{n} pages rendered; link classes: {dict(classes)}")
for p in problems:
    print("PROBLEM", p)
print("OK" if not problems else f"{len(problems)} problems")
sys.exit(1 if problems else 0)
