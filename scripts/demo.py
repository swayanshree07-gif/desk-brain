"""Final demo: start Desk Brain under `timeout`, exercise every feature via the HTTP API, print a summary.

Usage: .venv/bin/python scripts/demo.py [--real] [--port N]
Default uses the made-up fixture desk with a scratch data dir (build/demo-data), so the real
index and flashcard state are untouched. --real uses ~/Personal-Desk (read-only) and data/.
Prints only counts, timings and page paths, never note text. The server is always stopped.
"""
import argparse, json, os, subprocess, sys, time, urllib.error, urllib.parse, urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PRIVATE = ("activity/", "Personal/", "tools/", "_templates/")


def get(base, path, method="GET", body=None):
    req = urllib.request.Request(base + path, method=method, data=json.dumps(body).encode() if body else None,
                                 headers={"Content-Type": "application/json"})
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            raw = r.read()
            code = r.status
            data = json.loads(raw) if "json" in r.headers.get("Content-Type", "") else None
    except urllib.error.HTTPError as e:
        code, data = e.code, None
    return code, data, (time.perf_counter() - t0) * 1000


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--real", action="store_true")
    ap.add_argument("--port", type=int, default=8797)
    a = ap.parse_args()
    env = dict(os.environ)
    if a.real:
        desk = os.environ.get("DESK_PATH", str(Path.home() / "Personal-Desk"))
    else:
        desk = str(ROOT / "tests/fixtures/desk")
        data = ROOT / "build/demo-data"; data.mkdir(parents=True, exist_ok=True)
        if not (data / "models").exists():
            (data / "models").symlink_to(ROOT / "data/models")
        env["DESKBRAIN_DATA"] = str(data)
    base = f"http://127.0.0.1:{a.port}"
    log = open("/tmp/deskbrain-demo.log", "w")
    proc = subprocess.Popen(["timeout", "300", "./run.sh", "--desk", desk, "--port", str(a.port)],
                            cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
    rows, fail = [], 0
    def show(name, ok, info):
        nonlocal fail
        fail += not ok
        rows.append(f"{'ok  ' if ok else 'FAIL'} {name:<14} {info}")
    try:
        for _ in range(600):
            try:
                if get(base, "/api/health")[0] == 200: break
            except OSError: pass
            time.sleep(0.2)
        else:
            print("FAIL server did not start; see /tmp/deskbrain-demo.log"); return 1
        c, d, ms = get(base, "/api/reindex", "POST")
        show("reindex", c == 200, f"{ms:.0f} ms")
        c, d, _ = get(base, "/api/health")
        show("health", c == 200, json.dumps(d.get("counts")) if d else "")
        for mode in ("keyword", "semantic", "hybrid"):
            c, d, ms = get(base, f"/api/search?q=doubt&mode={mode}")
            show(f"search/{mode}", c == 200, f"{len(d['hits'])} hits, {ms:.0f} ms" if d else "")
        all_rels = []
        c, d, _ = get(base, "/api/graph")
        if d: all_rels = [n.get("id", "") for n in d.get("nodes", [])]
        show("graph", c == 200 and bool(d and d.get("nodes")), f"{len(d['nodes'])} nodes, {len(d.get('edges', d.get('links', [])))} edges" if d else "")
        for st in ("open", "answered"):
            c, d, _ = get(base, f"/api/doubts?status={st}")
            n = len(d.get("doubts", d)) if isinstance(d, (dict, list)) else 0
            show(f"doubts/{st}", c == 200, f"{n} items")
        c, d, _ = get(base, "/api/cards")
        show("cards", c == 200, f"{len(d.get('cards', d)) if d else 0} cards")
        c, d, _ = get(base, "/api/review")
        show("review", c == 200, f"{len(d['due']) if isinstance(d, dict) and isinstance(d.get('due'), list) else '?'} due")
        c, d, _ = get(base, "/api/digest")
        show("digest", c == 200, ", ".join(f"{k}" for k in (d or {}).keys())[:60])
        c, d, _ = get(base, "/api/recent?limit=5")
        pages = (d or {}).get("pages", [])
        show("recent", c == 200, f"{len(pages)} pages")
        if pages:
            rel = pages[0]["rel"]
            c, d, _ = get(base, f"/api/page?rel={urllib.parse.quote(rel)}")
            show("page", c == 200, rel)
            c, d, ms = get(base, f"/api/related?rel={urllib.parse.quote(rel)}")
            show("related", c == 200, f"{len((d or {}).get('related', []))} suggestions, {ms:.0f} ms")
        c, d, ms = get(base, "/api/ask?q=what+are+my+open+doubts")
        cites = (d or {}).get("citations", [])
        show("ask", c == 200, f"{len(cites)} citations, {ms:.0f} ms")
        c, _, _ = get(base, "/api/page?rel=Personal/x.md")
        show("private-404", c == 404, "Personal/ blocked")
        leaked = [r for r in all_rels + [x.get("rel", "") for x in cites] if r.startswith(PRIVATE)]
        show("no-private", not leaked, f"{len(leaked)} private paths in graph/ask")
        c, _, _ = get(base, "/")
        show("web-ui", c == 200, "/")
    finally:
        proc.terminate()
        try: proc.wait(10)
        except subprocess.TimeoutExpired: proc.kill(); proc.wait()
    print(f"Desk Brain demo ({'real desk' if a.real else 'fixture desk'})")
    print("\n".join(rows))
    print("DEMO OK" if not fail else f"DEMO: {fail} FAILED")
    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(main())
