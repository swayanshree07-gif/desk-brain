"""Keyword, semantic and hybrid section search on top of BM25 + the vector store.

Modes
  keyword   BM25 only (bm25.py).
  semantic  cosine similarity between the query embedding and section embeddings.
  hybrid    reciprocal-rank fusion (RRF, k=60) of the top-50 lists from both. Default
            whenever the embedding model is available.

A MiniLM model gives *some* similarity to everything, even to nonsense queries (cosine
around 0.3–0.4 on the real desk for "zzqqxx"). Hard cut-offs, picked by probing the
fixture and real desk (Day 5):
  * semantic hits below ``SEM_MIN`` (0.20) are never shown;
  * in hybrid mode a semantic-only hit needs ``SEM_HYBRID`` (0.30);
  * if BM25 finds nothing at all, semantic hits need ``SEM_ALONE`` (0.45), so a typo
    or gibberish query returns no hits instead of random sections.
Both result lists are first taken without the per-page cap, and the cap is applied
after fusion. Exact fused-score ties are broken by similarity (then path), so for a
paraphrase where BM25 latches onto a stray word the semantically closer section wins.
"""

from __future__ import annotations

from collections import Counter, OrderedDict
from dataclasses import dataclass, field
from pathlib import Path

from .bm25 import BM25Index, tokenize
from .chunker import Chunk
from .embed import Embedder, VectorStore, model_cached

RRF_K = 60
POOL = 50
SEM_MIN, SEM_HYBRID, SEM_ALONE = 0.20, 0.30, 0.45
MODES = ("hybrid", "keyword", "semantic")

_EMBEDDER: Embedder | None = None


def shared_embedder() -> Embedder:
    """One model instance per process (loading it costs ~0.5 s and ~100 MB RAM)."""
    global _EMBEDDER
    if _EMBEDDER is None:
        _EMBEDDER = Embedder()
    return _EMBEDDER


def vectors_base(db_path: Path) -> Path:
    """Vector files belong to one index file: data/index.sqlite -> data/index-vectors.{npy,json}."""
    db_path = Path(db_path)
    return db_path.with_name(db_path.stem + "-vectors")


@dataclass
class SearchHit:
    chunk: Chunk
    score: float                      # the score used for ranking in this mode
    match: str                        # "keyword" | "semantic" | "both"
    bm25: float | None = None
    similarity: float | None = None
    terms: list[str] = field(default_factory=list)   # query stems present in the section


def rrf(rankings: list[list[str]], k: int = RRF_K) -> dict[str, float]:
    """Reciprocal-rank fusion: sum of 1/(k + rank) over lists (rank starts at 1)."""
    out: dict[str, float] = {}
    for ranking in rankings:
        for r, cid in enumerate(ranking, 1):
            out[cid] = out.get(cid, 0.0) + 1.0 / (k + r)
    return out


class HybridSearcher:
    def __init__(self, vec_base: Path, semantic: bool | None = None, embedder: Embedder | None = None):
        """``semantic``: None = on if the model is already downloaded, True = on (may download), False = off."""
        self.available = bool(semantic) or (semantic is None and model_cached())
        self.error: str | None = None
        self.vec_base = Path(vec_base)
        self._embedder = embedder
        self.vs: VectorStore | None = None
        self.bm25: BM25Index | None = None
        self.pos: dict[str, int] = {}
        self._qcache: OrderedDict[str, object] = OrderedDict()

    @property
    def embedder(self) -> Embedder:
        if self._embedder is None:
            self._embedder = shared_embedder()
        return self._embedder

    def default_mode(self) -> str:
        return "hybrid" if self.available else "keyword"

    def resolve_mode(self, mode: str | None) -> str:
        """Requested mode -> mode actually used (semantic modes fall back to keyword)."""
        if not mode:
            return self.default_mode()
        if mode not in MODES:
            raise ValueError(f"unknown mode {mode!r}")
        return mode if (mode == "keyword" or self.available) else "keyword"

    def update(self, bm25: BM25Index, chunks: list[Chunk]) -> dict | None:
        """New BM25 + chunk list (after a re-index). Embeds only new/changed sections."""
        self.bm25 = bm25
        self.pos = {c.id: i for i, c in enumerate(bm25.chunks)}
        if not self.available:
            return None
        try:
            if self.vs is None:
                self.vs = VectorStore(self.vec_base)
            return self.vs.sync(chunks, self.embedder)
        except Exception as e:   # e.g. model missing and no network: keep keyword search working
            self.available, self.error, self.vs = False, f"{type(e).__name__}: {e}", None
            return None

    def _qvec(self, q: str):
        v = self._qcache.get(q)
        if v is None:
            v = self.embedder.embed([q])[0]
            self._qcache[q] = v
            if len(self._qcache) > 64:
                self._qcache.popitem(last=False)
        else:
            self._qcache.move_to_end(q)
        return v

    def _terms(self, cid: str, qterms: list[str]) -> list[str]:
        tf = self.bm25.tf[self.pos[cid]]
        return [t for t in qterms if t in tf]

    def search(self, q: str, mode: str = "", k: int = 20, per_page: int | None = 2) -> list[SearchHit]:
        assert self.bm25 is not None, "update() first"
        mode = self.resolve_mode(mode)
        if not q.strip():
            return []
        if mode == "keyword":
            return [SearchHit(h.chunk, h.score, "keyword", bm25=h.score, terms=h.terms)
                    for h in self.bm25.search(q, k=k, per_page=per_page)]

        qterms = list(dict.fromkeys(tokenize(q)))
        sem = [(cid, s) for cid, s in self.vs.search(self._qvec(q), k=POOL)
               if s >= SEM_MIN and cid in self.pos]
        if mode == "semantic":
            ranked = [(cid, s) for cid, s in sem]
            hits = [SearchHit(self.bm25.chunks[self.pos[cid]], s, "semantic", similarity=s,
                              terms=self._terms(cid, qterms)) for cid, s in ranked]
            return _cap(hits, k, per_page)

        kw = self.bm25.search(q, k=POOL, per_page=None)
        floor = SEM_HYBRID if kw else SEM_ALONE
        sem = [(cid, s) for cid, s in sem if s >= floor]
        kw_by = {h.chunk.id: h for h in kw}
        sim_by = dict(sem)
        fused = rrf([[h.chunk.id for h in kw], [cid for cid, _ in sem]])
        # Exact RRF ties (rank 1 in one list vs rank 1 in the other) go to the closer meaning.
        order = sorted(fused, key=lambda c: (-round(fused[c], 12), -sim_by.get(c, 0.0),
                                             self.bm25.chunks[self.pos[c]].rel, self.pos[c]))
        hits = []
        for cid in order:
            h = kw_by.get(cid)
            s = sim_by.get(cid)
            match = "both" if (h and s is not None) else ("keyword" if h else "semantic")
            hits.append(SearchHit(self.bm25.chunks[self.pos[cid]], fused[cid], match,
                                  bm25=h.score if h else None, similarity=s,
                                  terms=h.terms if h else self._terms(cid, qterms)))
        return _cap(hits, k, per_page)


def _cap(hits: list[SearchHit], k: int, per_page: int | None) -> list[SearchHit]:
    out, per = [], Counter()
    for h in hits:
        if per_page is not None and per[h.chunk.rel] >= per_page:
            continue
        per[h.chunk.rel] += 1
        out.append(h)
        if len(out) >= k:
            break
    return out
