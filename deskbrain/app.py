"""Local web app: JSON API + static frontend. Serve only on 127.0.0.1 (see run.sh).

Endpoints
  GET  /api/health              status, desk name, index counts
  GET  /api/search?q=&k=&all=&mode=
                                section search; mode = hybrid (default when the
                                embedding model is available) | keyword | semantic
  GET  /api/page?rel=           one page: rendered HTML, headings, links, backlinks
  GET  /api/related?rel=&k=    related pages (semantic or keyword similarity + link bonus)
  GET  /api/graph?folders=      page/folder nodes, link/folder edges, broken links
  GET  /api/doubts?status=&area=&q=
                                doubts board: open/answered doubts with page + anchor
  GET  /api/cards?kind=&area=   flashcards built from answered doubts and findings
  GET  /api/review?new=          today's study queue: due cards + new cards (SM-2)
  POST /api/review/grade        {"id": card id, "grade": 0-5} -> new schedule
  GET  /api/review/retired      cards gone from the desk (review state kept)
  GET  /api/review/card?id=     one card's state, grade log and lifecycle events
  GET  /api/digest?week=        weekly digest: changed pages, new/answered/open doubts,
                                new findings; week = 2026-W45 or a date (default: last 7 days)
  GET  /api/index/status        last re-index time, counts, pages changed since (stat-only)
  POST /api/reindex             incremental re-index of the desk
  GET  /                        the single-page frontend (static/index.html)

The desk is only read. Pages are served only if they are in the index *and* pass
``scanner.is_allowed`` (checked again on every read by ``read_page``).
A Host-header check blocks DNS-rebinding pages from reading the API.
"""

from __future__ import annotations

import html
import threading
import time
from pathlib import Path

from datetime import date
from typing import Callable

from fastapi import Body, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .bm25 import BM25Index, snippet, tokenize
from .config import desk_path
from .cards import list_cards
from .digest import build_digest, window
from .doubts import list_doubts
from .graph import EDGE_STATUSES, build_graph
from .links import slugify
from .related import related_pages
from .render import render_page
from .review import NEW_PER_DAY, ReviewStore, default_review_path, due_queue
from .scanner import is_allowed, read_page
from .search import HybridSearcher, vectors_base
from .store import Index, default_db_path

STATIC_DIR = Path(__file__).parent / "static"
ALLOWED_HOSTS = ["127.0.0.1", "localhost", "testserver"]
HOST, PORT = "127.0.0.1", 8765


def snippet_html(text: str, terms: list[str], width: int = 200) -> str:
    """Escaped snippet with matched words in <mark>."""
    raw = snippet(text, terms, width=width, mark=("\x02", "\x03"))
    return html.escape(raw).replace("\x02", "<mark>").replace("\x03", "</mark>")


class DeskState:
    """Index + in-memory BM25, shared by request threads under one lock."""

    def __init__(self, root: Path, db_path: Path, semantic: bool | None = None,
                 review_path: Path | None = None):
        self.root = Path(root).expanduser().resolve()
        self.lock = threading.RLock()
        self.index = Index(db_path, self.root)
        # Review state (SM-2) lives next to the index: data/flashcards.sqlite.
        self.reviews = ReviewStore(review_path or default_review_path(db_path))
        self.bm25: BM25Index | None = None
        self.last_stats = None
        self.last_embed: dict | None = None
        # Vectors live next to the index (data/index-vectors.* for the real app, tmp dirs in tests).
        self.searcher = HybridSearcher(vectors_base(db_path), semantic=semantic)

    def reindex(self):
        with self.lock:
            st = self.index.update()
            bad = self.index.check_privacy()
            if bad:  # should never happen; refuse to serve rather than leak
                raise RuntimeError(f"privacy check failed: {bad}")
            if st.n_changed or self.bm25 is None:
                chunks = self.index.chunks()
                self.bm25 = BM25Index(chunks)
                self.last_embed = self.searcher.update(self.bm25, chunks)
            self.last_stats = st
            return st

    def close(self) -> None:
        with self.lock:
            self.index.close()
            self.reviews.close()


def create_app(root: Path | str | None = None, db_path: Path | str | None = None,
               semantic: bool | None = None, today: Callable[[], date] = date.today) -> FastAPI:
    """``semantic``: None = use embeddings if the model is downloaded; False = keyword only.
    ``today``: clock for the review queue (tests pass a fake one)."""
    state = DeskState(Path(root) if root else desk_path(), Path(db_path) if db_path else default_db_path(),
                      semantic=semantic)
    state.reindex()

    app = FastAPI(title="Desk Brain", docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=ALLOWED_HOSTS)
    app.state.desk = state

    @app.get("/api/health")
    def health():
        with state.lock:
            counts = state.index.counts()
            st = state.last_stats
        return {"status": "ok", "desk": state.root.name, "counts": counts,
                "last_update": st.summary() if st else None,
                "semantic": state.searcher.available, "default_mode": state.searcher.default_mode()}

    @app.get("/api/search")
    def search(q: str = Query("", max_length=500), k: int = Query(20, ge=1, le=100), all: bool = False,
               mode: str = Query("", pattern="^(|hybrid|keyword|semantic)$")):
        t0 = time.perf_counter()
        with state.lock:
            used = state.searcher.resolve_mode(mode)
            if not q.strip():
                return {"query": q, "mode": used, "hits": [], "ms": 0.0}
            hits = state.searcher.search(q, mode=used, k=k, per_page=None if all else 2)
            titles = {}
            for h in hits:
                rel = h.chunk.rel
                if rel not in titles:
                    p = state.index.page(rel)
                    titles[rel] = p["title"] if p else rel
        qstems = list(dict.fromkeys(tokenize(q)))
        out = [{
            "rel": h.chunk.rel, "page_title": titles[h.chunk.rel], "section": h.chunk.title,
            "breadcrumb": h.chunk.breadcrumb,
            "section_slug": slugify(h.chunk.heading).strip("-") if h.chunk.level else "", "line": h.chunk.start_line, "chunk_id": h.chunk.id,
            "score": round(h.score, 4), "match": h.match,
            "bm25": None if h.bm25 is None else round(h.bm25, 4),
            "similarity": None if h.similarity is None else round(h.similarity, 4),
            "snippet": snippet_html(h.chunk.text, h.terms or qstems),
        } for h in hits if is_allowed(h.chunk.rel)]
        return {"query": q, "mode": used, "hits": out, "ms": round((time.perf_counter() - t0) * 1000, 2)}

    @app.get("/api/page")
    def page(rel: str = Query(..., max_length=1000)):
        if not is_allowed(rel):
            raise HTTPException(404, "not found")
        with state.lock:
            row = state.index.page(rel)
            if row is None:
                raise HTTPException(404, "not found")
            links = state.index.links_from(rel)
            back = state.index.backlinks(rel)
            heads = [dict(r) for r in state.index.db.execute(
                "SELECT level, text, line FROM headings WHERE rel=? ORDER BY ord", (rel,))]
        try:
            text = read_page(state.root, rel)
        except (PermissionError, FileNotFoundError):
            raise HTTPException(404, "not found")
        public_links = [{k: (l[k] if l["status"] != "excluded" or k not in ("raw", "target") else None)
                         for k in ("line", "text", "kind", "status", "target", "anchor", "raw")} for l in links]
        return {"rel": rel, "title": row["title"], "mtime": row["mtime"], "size": row["size"],
                "html": render_page(text, rel, links), "headings": heads,
                "links": public_links, "backlinks": back}

    @app.get("/api/related")
    def related(rel: str = Query(..., max_length=1000), k: int = Query(5, ge=1, le=20)):
        """Pages most like ``rel``; never ``rel`` itself, never private pages."""
        if not is_allowed(rel):
            raise HTTPException(404, "not found")
        with state.lock:
            if state.index.page(rel) is None or state.bm25 is None:
                raise HTTPException(404, "not found")
            links = state.index.db.execute(
                f"SELECT src, target FROM links WHERE status IN ({','.join('?' * len(EDGE_STATUSES))})",
                EDGE_STATUSES).fetchall()
            vs = state.searcher.vs if state.searcher.available else None
            method, items = related_pages(rel, state.bm25, vs, links, k=k)
            for it in items:
                p = state.index.page(it["rel"])
                it["title"] = p["title"] if p else it["rel"]
        return {"rel": rel, "method": method, "related": items}

    @app.get("/api/recent")
    def recent(limit: int = Query(10, ge=1, le=50)):
        """Latest-changed pages for the home / phone view."""
        with state.lock:
            return {"pages": state.index.recent(limit)}

    @app.get("/api/graph")
    def graph(folders: bool = True):
        with state.lock:
            return build_graph(state.index, folders=folders)

    @app.get("/api/doubts")
    def doubts(status: str = Query("all", pattern="^(all|open|answered)$"),
               area: str = Query("", max_length=200), q: str = Query("", max_length=500)):
        with state.lock:
            return list_doubts(state.index, status=status, area=area, q=q)

    @app.get("/api/cards")
    def cards(kind: str = Query("all", pattern="^(all|qa|lead|cloze|recall)$"),
              area: str = Query("", max_length=200)):
        with state.lock:
            return list_cards(state.index, kind=kind, area=area)

    @app.get("/api/review")
    def review(new: int = Query(NEW_PER_DAY, ge=0, le=500)):
        with state.lock:
            cards = list_cards(state.index)["cards"]
        return due_queue(cards, state.reviews, today=today(), new_per_day=new)

    @app.post("/api/review/grade")
    def grade(id: str = Body(..., max_length=100), grade: int = Body(..., ge=0, le=5)):
        with state.lock:
            card = next((c for c in list_cards(state.index)["cards"] if c["id"] == id), None)
        if card is None:
            raise HTTPException(404, "no such card")
        with state.lock:
            cards_now = list_cards(state.index)["cards"]
        state.reviews.reconcile(cards_now, today())
        st = state.reviews.grade(id, grade, today=today(), card=card)
        return {"id": id, "grade": grade, "review": st.__dict__}

    @app.get("/api/review/retired")
    def review_retired():
        """Cards that had review state but are gone from the desk (Day 22)."""
        with state.lock:
            cards_now = list_cards(state.index)["cards"]
        state.reviews.reconcile(cards_now, today())
        items = state.reviews.retired()
        return {"retired": items, "count": len(items)}

    @app.get("/api/review/card")
    def review_card(id: str = Query(..., max_length=100)):
        """Review state, grade log and lifecycle events of one live card (404 for retired)."""
        with state.lock:
            live = any(c["id"] == id for c in list_cards(state.index)["cards"])
        st = state.reviews.get(id)
        if st is None or not live:
            raise HTTPException(404, "no review state")
        return {"id": id, "review": st.__dict__, "grades": state.reviews.history(id),
                "events": state.reviews.events(id)}

    @app.get("/api/digest")
    def digest(week: str = Query("", max_length=20)):
        try:
            start, end = window(week or None, today=today())
        except ValueError:
            raise HTTPException(422, "week must look like 2026-W45 or 2026-11-03")
        with state.lock:
            return build_digest(state.index, start, end, today=today())

    @app.get("/api/index/status")
    def index_status():
        """Cheap poll: is the index behind the desk? (stat-only, nothing is read)"""
        with state.lock:
            pend = state.index.pending()
            last = state.index.meta("last_update")
            counts = state.index.counts()
            st = state.last_stats
        n = sum(len(v) for v in pend.values())
        return {"last_update": float(last) if last else None, "counts": counts, "stale": n > 0,
                "pending": {k: len(v) for k, v in pend.items()}, "pending_pages": pend,
                "last_summary": st.summary() if st else None}

    @app.post("/api/reindex")
    def reindex():
        st = state.reindex()
        return {"added": st.added, "changed": st.changed, "removed": st.removed,
                "summary": st.summary()}

    @app.get("/")
    def home():
        return FileResponse(STATIC_DIR / "index.html")

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    return app


def main() -> None:
    import argparse
    import uvicorn

    ap = argparse.ArgumentParser(prog="deskbrain-serve")
    ap.add_argument("--desk", type=Path, default=None)
    ap.add_argument("--db", type=Path, default=None)
    ap.add_argument("--port", type=int, default=PORT)
    ap.add_argument("--no-semantic", action="store_true", help="keyword search only (no embedding model)")
    args = ap.parse_args()
    app = create_app(args.desk, args.db, semantic=False if args.no_semantic else None)
    # Always loopback only: the desk must never be reachable from the network.
    uvicorn.run(app, host=HOST, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
