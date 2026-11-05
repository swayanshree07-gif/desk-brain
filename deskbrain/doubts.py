"""Doubts board data: every 🔴 open / 🟢 answered doubt across the indexed pages.

Each doubt gets
  id      sha1(rel, question)[:12]: stays the same when lines move or the answer is
          added (🔴 -> 🟢), so flashcards (Day 19) can key on it.
  anchor  the heading id the page view gives the doubt's ``### 🔴 Q:`` heading (same
          GitHub slug + duplicate-suffix rule as render.py), for "open in page" links.
Filters: status (open / answered / all), area (top-level folder, "(root)" for desk-root
pages) and a text query; the query matches when every query stem (bm25.tokenize)
appears in the question, context, answer or section.
Order: open first, then newest ``Asked`` date first (undated last), then path and line.
"""

from __future__ import annotations

import hashlib
from collections import Counter
from pathlib import PurePosixPath

from .bm25 import tokenize
from .links import slugify
from .scanner import is_allowed

ROOT_AREA = "(root)"


def doubt_id(rel: str, question: str) -> str:
    return hashlib.sha1(f"{rel}\x00{question.strip()}".encode("utf-8")).hexdigest()[:12]


def area_of(rel: str) -> str:
    parts = PurePosixPath(rel).parts
    return parts[0] if len(parts) > 1 else ROOT_AREA


def heading_anchors(index, rel: str) -> dict[int, str]:
    """line -> rendered heading id for one page (mirrors render.render_page)."""
    seen: Counter = Counter()
    out = {}
    for r in index.db.execute("SELECT text, line FROM headings WHERE rel=? ORDER BY ord", (rel,)):
        base = slugify(r["text"])
        k = seen[base]
        seen[base] += 1
        out[r["line"]] = (base if k == 0 else f"{base}-{k}").strip("-")
    return out


def list_doubts(index, status: str = "all", area: str = "", q: str = "") -> dict:
    db = index.db
    titles = {r["rel"]: r["title"] for r in db.execute("SELECT rel, title FROM pages")}
    rows = [dict(r) for r in db.execute("SELECT * FROM doubts ORDER BY rel, line")
            if r["rel"] in titles and is_allowed(r["rel"])]
    anchors: dict[str, dict[int, str]] = {}
    all_items = []
    for r in rows:
        rel = r["rel"]
        if rel not in anchors:
            anchors[rel] = heading_anchors(index, rel)
        all_items.append({
            "id": doubt_id(rel, r["question"]), "rel": rel, "page_title": titles[rel], "area": area_of(rel),
            "line": r["line"], "question": r["question"], "status": r["status"], "asked": r["asked"],
            "context": r["context"], "answer": r["answer"], "section": r["section"],
            "anchor": anchors[rel].get(r["line"], ""),
        })
    counts = Counter(d["status"] for d in all_items)
    areas = sorted({d["area"] for d in all_items})

    qstems = set(tokenize(q)) if q.strip() else set()
    items = []
    for d in all_items:
        if status in ("open", "answered") and d["status"] != status:
            continue
        if area and d["area"] != area:
            continue
        if qstems:
            words = set(tokenize(" ".join(filter(None, (d["question"], d["context"], d["answer"], d["section"])))))
            if not qstems <= words:
                continue
        items.append(d)
    items.sort(key=lambda d: (d["status"] != "open", -_date_key(d["asked"]), d["rel"], d["line"]))
    return {"doubts": items, "total": len(all_items),
            "counts": {"open": counts["open"], "answered": counts["answered"]}, "areas": areas}


def _date_key(asked: str | None) -> int:
    """'2026-11-02 18:40' -> 202611021840; undated -> 0 (sorts last when negated)."""
    if not asked:
        return 0
    digits = "".join(ch for ch in asked if ch.isdigit())[:12]
    return int(digits.ljust(12, "0")) if len(digits) >= 8 else 0
