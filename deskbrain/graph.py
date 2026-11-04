"""Knowledge-graph data from the index: page and folder nodes, link and folder edges.

Nodes
  page    id = rel path; title, area (top-level folder, "" for desk root), folder,
          in/out/degree (distinct linked pages, self-links not counted), counts of
          doubts/open doubts/findings/bad links, ``orphan`` = no page links in or out.
  folder  id = "dir:<path>"; one per folder that holds an indexed page (plus parents).
Edges
  link    page -> page, one per (src, target) pair, ``weight`` = number of links. Links
          with a bad anchor still point at a real page, so they count (``bad_anchor``).
  folder  folder -> page and parent folder -> child folder ("contains").
Broken links (broken / bad-anchor / outside) are listed separately for highlighting.
Links into private areas (status ``excluded``) are never part of the graph.
"""

from __future__ import annotations

from collections import Counter
from pathlib import PurePosixPath

from .scanner import is_allowed

EDGE_STATUSES = ("ok", "bad-anchor")
BAD_STATUSES = ("broken", "bad-anchor", "outside")


def _area(rel: str) -> str:
    parts = PurePosixPath(rel).parts
    return parts[0] if len(parts) > 1 else ""


def _folder(rel: str) -> str:
    p = str(PurePosixPath(rel).parent)
    return "" if p == "." else p


def build_graph(index, folders: bool = True) -> dict:
    db = index.db
    pages = {r["rel"]: r["title"] for r in db.execute("SELECT rel, title FROM pages ORDER BY rel")
             if is_allowed(r["rel"])}

    weight: Counter = Counter()
    bad_anchor: set[tuple[str, str]] = set()
    for r in db.execute("SELECT src, target, status FROM links WHERE status IN (?, ?)", EDGE_STATUSES):
        s, t = r["src"], r["target"]
        if s in pages and t in pages and s != t:
            weight[(s, t)] += 1
            if r["status"] == "bad-anchor":
                bad_anchor.add((s, t))

    broken = [{"src": r["src"], "line": r["line"], "raw": r["raw"], "status": r["status"],
               "target": r["target"]}
              for r in db.execute("SELECT src, line, raw, status, target FROM links "
                                  f"WHERE status IN ({','.join('?' * len(BAD_STATUSES))}) ORDER BY src, ord",
                                  BAD_STATUSES)
              if r["src"] in pages]

    def per_page(sql: str) -> Counter:
        return Counter({r[0]: r[1] for r in db.execute(sql)})

    n_doubts = per_page("SELECT rel, COUNT(*) FROM doubts GROUP BY rel")
    n_open = per_page("SELECT rel, COUNT(*) FROM doubts WHERE status='open' GROUP BY rel")
    n_find = per_page("SELECT rel, COUNT(*) FROM findings GROUP BY rel")
    n_bad = Counter(b["src"] for b in broken)
    outd = Counter(s for s, _ in weight)
    ind = Counter(t for _, t in weight)

    nodes = [{
        "id": rel, "type": "page", "title": title, "area": _area(rel), "folder": _folder(rel),
        "in": ind[rel], "out": outd[rel], "degree": ind[rel] + outd[rel],
        "doubts": n_doubts[rel], "open_doubts": n_open[rel], "findings": n_find[rel],
        "bad_links": n_bad[rel], "orphan": ind[rel] + outd[rel] == 0,
    } for rel, title in pages.items()]
    edges = [{"source": s, "target": t, "kind": "link", "weight": w, "bad_anchor": (s, t) in bad_anchor}
             for (s, t), w in sorted(weight.items())]

    if folders:
        dirs: set[str] = set()
        for rel in pages:
            f = _folder(rel)
            while f:
                dirs.add(f)
                f = _folder(f)
        for d in sorted(dirs):
            nodes.append({"id": f"dir:{d}", "type": "folder", "title": PurePosixPath(d).name + "/",
                          "area": d.split("/")[0], "folder": _folder(d)})
            parent = _folder(d)
            if parent:
                edges.append({"source": f"dir:{parent}", "target": f"dir:{d}", "kind": "folder", "weight": 1})
        for rel in pages:
            f = _folder(rel)
            if f:
                edges.append({"source": f"dir:{f}", "target": rel, "kind": "folder", "weight": 1})

    page_nodes = [n for n in nodes if n["type"] == "page"]
    return {
        "nodes": nodes, "edges": edges, "broken": broken,
        "areas": sorted({n["area"] for n in page_nodes}),
        "stats": {"pages": len(page_nodes), "folders": len(nodes) - len(page_nodes),
                  "link_edges": len(weight), "orphans": sum(n["orphan"] for n in page_nodes),
                  "broken": len(broken)},
    }
