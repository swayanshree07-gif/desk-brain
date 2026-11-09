"""Ask the desk, part 1 (Day 19): question -> cited sections.

``retrieve`` runs the hybrid searcher (keyword fallback without the model) with
the per-page cap lifted, keeps at most ``PER_PAGE`` sections per page and
``MAX_PAGES`` pages, then groups them per page in order of each page's best hit.
Citations are numbered ``1..n`` in that grouped order, so ``[1]`` is always the
best section of the best page. Each citation carries what is needed to open it:
page path, heading breadcrumb, heading slug and start/end line, plus chunk id.
Private pages never get through (``is_allowed``). Day 20 adds the extractive
answer on top of these citations.
"""

from __future__ import annotations

from .scanner import is_allowed
from .search import HybridSearcher

PER_PAGE, MAX_PAGES, POOL = 2, 4, 30


def retrieve(searcher: HybridSearcher, question: str, mode: str = "",
             max_pages: int = MAX_PAGES, per_page: int = PER_PAGE) -> dict:
    used = searcher.resolve_mode(mode)
    q = question.strip()
    if not q:
        return {"question": question, "mode": used, "pages": [], "citations": []}
    hits = [h for h in searcher.search(q, mode=used, k=POOL, per_page=None) if is_allowed(h.chunk.rel)]
    groups: dict[str, list] = {}
    for h in hits:
        g = groups.get(h.chunk.rel)
        if g is None:
            if len(groups) >= max_pages:
                continue
            g = groups[h.chunk.rel] = []
        if len(g) < per_page:
            g.append(h)
    citations, pages = [], []
    for rel, hs in groups.items():
        ids = []
        for h in hs:
            c = h.chunk
            n = len(citations) + 1
            citations.append({"n": n, "rel": rel, "chunk_id": c.id, "heading": c.heading,
                              "breadcrumb": list(c.breadcrumb), "line": c.start_line,
                              "end_line": c.end_line, "score": round(h.score, 4), "match": h.match,
                              "text": c.text})
            ids.append(n)
        pages.append({"rel": rel, "citations": ids})
    return {"question": question, "mode": used, "pages": pages, "citations": citations}
