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


# ---------------------------------------------------------------------------
# Day 20: extractive answer.
#
# Each cited section is split into sentences (per line, then on . ! ? followed by
# a space). Headings, HTML comments, code fences/blocks and table rules are skipped;
# list markers, ``**A:**``-style labels and date stamps are trimmed off the front,
# but what is kept is always an exact substring of the section text.
# Score per sentence:
#   kw  = idf-weighted share of the question's stems found in the sentence (0..1)
#   sim = cosine(question, sentence) with the MiniLM model (semantic modes only)
#   score = kw (keyword mode) or 0.5*kw + 0.5*sim
# The best ``ANSWER_MAX`` sentences scoring >= ``REL_KEEP`` x best are returned in
# citation order, each tagged with its citation number. If the best score is under
# ``MIN_KW`` / ``MIN_HYBRID`` the answer is "don't know" (citations still listed).
# Thresholds picked by probing the fixture and the real desk (Day 20).
# ---------------------------------------------------------------------------

import re

from .bm25 import tokenize

ANSWER_MAX, REL_KEEP = 3, 0.6
MIN_KW, MIN_HYBRID = 0.34, 0.40
MIN_WORDS = 3
_SPLIT = re.compile(r"(?<=[.!?])\s+(?=\S)")
_LEAD = re.compile(r"^(?:\s*(?:[-*+]|\d+[.)])\s+)?(?:\[[ xX]\]\s+)?"
                   r"(?:\*\*[^*]{1,40}\*\*\s*(?:[·:—-]\s*)?)?(?:[A-Za-z]{1,12}:\s+)?")


def sentences(text: str) -> list[str]:
    out, fence, comment = [], False, False
    for line in text.splitlines():
        s = line.strip()
        if s.startswith(("```", "~~~")):
            fence = not fence
            continue
        if fence:
            continue
        if comment or s.startswith("<!--"):
            comment = "-->" not in s
            continue
        if not s or s.startswith("#") or re.fullmatch(r"[|:\-\s]+", s) or line.startswith("    "):
            continue
        s = s.strip("| ").strip() if s.startswith("|") else s
        s = s[_LEAD.match(s).end():].strip() if _LEAD.match(s) else s
        for part in _SPLIT.split(s):
            part = part.strip()
            if len(re.findall(r"\w+", part)) >= MIN_WORDS:
                out.append(part)
    return out


def answer(searcher: HybridSearcher, out: dict) -> dict:
    """Add ``answer`` (list of {text, n, score}) and ``known`` to a ``retrieve`` result."""
    q = out["question"].strip()
    qterms = list(dict.fromkeys(tokenize(q)))
    bm = searcher.bm25
    weights = {t: (bm.idf(t) if bm is not None else 1.0) for t in qterms}
    total = sum(weights.values()) or 1.0
    cands = []
    for c in out["citations"]:
        for s in sentences(c["text"]):
            st = set(tokenize(s))
            kw = sum(w for t, w in weights.items() if t in st) / total
            cands.append({"text": s, "n": c["n"], "kw": kw})
    semantic = out["mode"] != "keyword" and searcher.available and bool(cands)
    if semantic:
        import numpy as np
        qv = searcher._qvec(q)
        sv = searcher.embedder.embed([x["text"] for x in cands])
        for x, sim in zip(cands, (sv @ np.asarray(qv)).tolist()):
            x["sim"] = round(sim, 4)
            x["score"] = 0.5 * x["kw"] + 0.5 * sim
    else:
        for x in cands:
            x["score"] = x["kw"]
    for x in cands:
        x["kw"], x["score"] = round(x["kw"], 4), round(x["score"], 4)
    cands.sort(key=lambda x: (-x["score"], x["n"]))
    best = cands[0]["score"] if cands else 0.0
    floor = MIN_HYBRID if semantic else MIN_KW
    known = best >= floor
    picked, seen = [], set()
    if known:
        for x in cands:
            if x["score"] < REL_KEEP * best or len(picked) >= ANSWER_MAX:
                break
            if x["text"] not in seen:
                seen.add(x["text"])
                picked.append(x)
        picked.sort(key=lambda x: x["n"])
    out.update(known=known, answer=picked, answer_method="hybrid" if semantic else "keyword",
               best_score=round(best, 4))
    return out
