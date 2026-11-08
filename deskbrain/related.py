"""Related notes: for one page, the other pages most like it (Day 17).

Score per candidate page
  semantic (model available): cosine of the two pages' mean section embeddings
                              (rows of the vector store, re-normalised).
  fallback (no model):        cosine of TF-IDF vectors over BM25 stems of the
                              whole page (BM25 IDF reused).
  plus a link bonus:          +LINK_BONUS if either page links to the other,
                              +SHARED_BONUS per shared link neighbour (max 2).
Only pages that pass ``scanner.is_allowed`` are ever returned, the page itself
never is, and candidates below ``MIN_SCORE`` with no link are dropped.
"""

from __future__ import annotations

import math
from collections import Counter, defaultdict

import numpy as np

from .bm25 import BM25Index, chunk_text_for_index, tokenize
from .scanner import is_allowed

LINK_BONUS, SHARED_BONUS, MIN_SCORE = 0.10, 0.03, 0.05


def link_neighbours(link_rows) -> dict[str, set[str]]:
    """Undirected page graph from (src, target) rows of resolved page links."""
    nb: dict[str, set[str]] = defaultdict(set)
    for src, tgt in link_rows:
        if tgt and src != tgt and tgt.endswith(".md"):
            nb[src].add(tgt)
            nb[tgt].add(src)
    return nb


def _page_vectors_semantic(vs, bm25: BM25Index) -> dict[str, np.ndarray]:
    rows: dict[str, list[int]] = defaultdict(list)
    pos = {cid: i for i, cid in enumerate(vs.ids)}
    for c in bm25.chunks:
        i = pos.get(c.id)
        if i is not None:
            rows[c.rel].append(i)
    out = {}
    for rel, idx in rows.items():
        v = vs.vecs[idx].mean(axis=0)
        n = float(np.linalg.norm(v))
        if n > 1e-9:
            out[rel] = v / n
    return out


def _page_vectors_tfidf(bm25: BM25Index) -> dict[str, dict[str, float]]:
    tf: dict[str, Counter] = defaultdict(Counter)
    for c in bm25.chunks:
        tf[c.rel].update(tokenize(chunk_text_for_index(c)))
    out = {}
    for rel, cnt in tf.items():
        v = {t: (1 + math.log(n)) * bm25.idf(t) for t, n in cnt.items()}
        norm = math.sqrt(sum(x * x for x in v.values())) or 1.0
        out[rel] = {t: x / norm for t, x in v.items()}
    return out


def _sparse_cos(a: dict[str, float], b: dict[str, float]) -> float:
    if len(a) > len(b):
        a, b = b, a
    return sum(x * b.get(t, 0.0) for t, x in a.items())


def related_pages(rel: str, bm25: BM25Index, vs=None, links=None, k: int = 5) -> tuple[str, list[dict]]:
    """(method, [{rel, score, similarity, linked, shared}]) best first."""
    nb = link_neighbours(links or [])
    method = "semantic" if vs is not None and len(vs.ids) else "keyword"
    pages = sorted({c.rel for c in bm25.chunks} | set(nb))
    if method == "semantic":
        pv = _page_vectors_semantic(vs, bm25)
        me = pv.get(rel)
        sim = lambda o: float(me @ pv[o]) if me is not None and o in pv else 0.0  # noqa: E731
    else:
        pv = _page_vectors_tfidf(bm25)
        me = pv.get(rel)
        sim = lambda o: _sparse_cos(me, pv[o]) if me is not None and o in pv else 0.0  # noqa: E731
    mine = nb.get(rel, set())
    out = []
    for o in pages:
        if o == rel or not is_allowed(o):
            continue
        s = sim(o)
        linked = o in mine
        shared = len(mine & nb.get(o, set()) - {rel, o})
        score = s + (LINK_BONUS if linked else 0.0) + SHARED_BONUS * min(shared, 2)
        if score < MIN_SCORE and not linked:
            continue
        out.append({"rel": o, "score": round(score, 4), "similarity": round(s, 4),
                    "linked": linked, "shared": shared})
    out.sort(key=lambda d: (-d["score"], d["rel"]))
    return method, out[:k]
