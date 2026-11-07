"""Command line: python -m deskbrain <command>.

  scan     list indexable pages with counts of headings, links, doubts, findings
  doubts   list doubts (open first)
  chunks   per-page section (chunk) counts and size stats
  links    link summary by status; --broken lists only bad links
  index    build / incrementally update the SQLite index in data/
  search   section search: deskbrain search "query" [-k N] [--all-sections] [--mode hybrid|keyword|semantic]
  cards    update the index, then list flashcards: deskbrain cards [--kind K] [--show]
  review   today's spaced-repetition queue (due + new cards): deskbrain review [--new N]
  digest   weekly digest: deskbrain digest [--week 2026-W45 | --week 2026-11-03] (default: last 7 days)
  embed    update the index, then embed new/changed sections into data/index-vectors.npy
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .config import desk_path
from .chunker import chunk_page
from .links import LinkResolver
from .parser import parse
from .scanner import read_page, scan
from .bm25 import BM25Index, snippet
from .store import Index, default_db_path


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="deskbrain")
    ap.add_argument("--desk", type=Path, default=None, help="desk root (default: $DESK_PATH or ~/Personal-Desk)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("scan", help="list indexable pages")
    sub.add_parser("doubts", help="list doubts")
    sub.add_parser("chunks", help="split pages into sections; print counts")
    lp = sub.add_parser("links", help="resolve links, list broken ones")
    lp.add_argument("--broken", action="store_true", help="only print the bad links")
    ip = sub.add_parser("index", help="build or incrementally update the index in data/")
    ip.add_argument("--db", type=Path, default=None, help="index file (default: data/index.sqlite)")
    ip.add_argument("-v", "--verbose", action="store_true", help="list added/changed/removed pages")
    sp = sub.add_parser("search", help="keyword search (updates the index first)")
    sp.add_argument("query")
    sp.add_argument("-k", type=int, default=10, help="number of hits (default 10)")
    sp.add_argument("--all-sections", action="store_true", help="allow several hits from one page (default: 2 max)")
    sp.add_argument("--db", type=Path, default=None, help="index file (default: data/index.sqlite)")
    sp.add_argument("--color", choices=("auto", "always", "never"), default="auto")
    sp.add_argument("--mode", choices=("hybrid", "keyword", "semantic"), default=None,
                    help="default: hybrid if the embedding model is downloaded, else keyword")
    cp = sub.add_parser("cards", help="list flashcards built from answered doubts and findings")
    cp.add_argument("--kind", choices=("all", "qa", "lead", "cloze", "recall"), default="all")
    cp.add_argument("--show", action="store_true", help="print front and back of each card")
    cp.add_argument("--db", type=Path, default=None, help="index file (default: data/index.sqlite)")
    rp = sub.add_parser("review", help="show today's flashcard queue (state in data/flashcards.sqlite)")
    rp.add_argument("--new", type=int, default=20, help="new cards per day (default 20)")
    rp.add_argument("--db", type=Path, default=None, help="index file (default: data/index.sqlite)")
    dp = sub.add_parser("digest", help="what changed in a week, what is still open")
    dp.add_argument("--week", default=None, help="ISO week (2026-W45) or a date in it; default: last 7 days")
    dp.add_argument("--db", type=Path, default=None, help="index file (default: data/index.sqlite)")
    ep = sub.add_parser("embed", help="embed new/changed sections (model cached in data/models)")
    ep.add_argument("--db", type=Path, default=None, help="index file (default: data/index.sqlite)")
    args = ap.parse_args(argv)
    root = (args.desk or desk_path()).expanduser().resolve()
    if not root.is_dir():
        print(f"desk not found: {root}", file=sys.stderr)
        return 2

    if args.cmd == "index":
        with Index(args.db or default_db_path(), root) as ix:
            st = ix.update()
            print(st.summary())
            if args.verbose:
                for label, rels in (("+", st.added), ("~", st.changed), ("-", st.removed)):
                    for r in rels:
                        print(f"  {label} {r}")
            c = ix.counts()
            print(", ".join(f"{k}={v}" for k, v in c.items()))
            bad = ix.check_privacy()
            if bad:
                print(f"PRIVACY ERROR: {bad}", file=sys.stderr)
                return 3
        return 0

    if args.cmd == "cards":
        from .cards import list_cards
        with Index(args.db or default_db_path(), root) as ix:
            ix.update()
            res = list_cards(ix, kind=args.kind)
        for c in res["cards"]:
            if args.show:
                print(f"[{c['kind']}] {c['id']}  ({c['rel']}:{c['line']})\n  Q: {c['front']}\n  A: {c['back']}\n")
            else:
                print(f"{c['kind']:<7} {c['id']:<16} {c['rel']}:{c['line']}  {c['front'][:70]}")
        print(f"{len(res['cards'])} of {res['total']} cards; "
              + ", ".join(f"{k}={v}" for k, v in res["counts"].items()))
        return 0

    if args.cmd == "review":
        from .cards import list_cards
        from .review import ReviewStore, default_review_path, due_queue
        db = args.db or default_db_path()
        with Index(db, root) as ix:
            ix.update()
            cards = list_cards(ix)["cards"]
        rs = ReviewStore(default_review_path(db))
        try:
            q = due_queue(cards, rs, new_per_day=args.new)
        finally:
            rs.close()
        for c in q["due"]:
            print(f"due {c['review']['due']}  {c['id']:<16} {c['front'][:70]}")
        for c in q["new"]:
            print(f"new             {c['id']:<16} {c['front'][:70]}")
        n = q["counts"]
        print(f"{q['today']}: {n['due']} due, {n['new']} new today ({n['new_total']} unseen), "
              f"{n['later']} scheduled later, {n['total']} cards, {n['retired']} retired")
        ch = q["changes"]
        if any(ch.values()):
            print("lifecycle: " + ", ".join(f"{k}={v}" for k, v in ch.items()))
        return 0

    if args.cmd == "digest":
        from .digest import build_digest, window
        try:
            start, end = window(args.week)
        except ValueError:
            print("--week must look like 2026-W45 or 2026-11-03", file=sys.stderr)
            return 2
        with Index(args.db or default_db_path(), root) as ix:
            ix.update()
            dg = build_digest(ix, start, end)
        c = dg["counts"]
        print(f"Digest {dg['week'] or 'last 7 days'}: {dg['start']} – {dg['end']}")
        print(f"{c['changed']} pages changed ({c['new_pages']} new, {c['removed']} removed), {c['new_doubts']} new doubts, "
              f"{c['answered']} answered, {c['findings']} findings, {c['open']} still open")
        if not dg["tracking"]["complete"]:
            print(f"(answers/new pages tracked since {dg['tracking']['since']})")
        for label, rows in (
                ("Answered", [f"🟢 {d['question']}  ({d['rel']}, {d['answered_day']})" for d in dg["doubts"]["answered"]]),
                ("New doubts", [f"{'🔴' if d['status'] == 'open' else '🟢'} {d['question']}  ({d['rel']})"
                                for d in dg["doubts"]["new"]]),
                ("Findings", [f"{f['date']} {f['text'][:90]}  ({f['rel']})" for f in dg["findings"]]),
                ("Pages changed", [f"{p['day']} {'+' if p['new'] else '~'} {p['rel']}" for p in dg["pages"]["changed"]]
                 + [f"- {r}" for r in dg["pages"]["removed"]]),
                ("Still open", [f"🔴 {d['question']}  ({d['rel']}, "
                                f"{'undated' if d['age_days'] is None else str(d['age_days']) + ' d'})"
                                for d in dg["doubts"]["open"]])):
            if rows:
                print(f"\n{label}:")
                for row in rows:
                    print("  " + row)
        return 0

    if args.cmd == "embed":
        import time
        from .embed import Embedder, VectorStore
        from .search import vectors_base
        t0 = time.perf_counter()
        db = args.db or default_db_path()
        with Index(db, root) as ix:
            ix.update()
            chunks = ix.chunks()
        r = VectorStore(vectors_base(db)).sync(chunks, Embedder())
        print(f"{len(chunks)} sections: {r['embedded']} embedded, {r['kept']} kept, "
              f"{r['removed']} removed; {time.perf_counter() - t0:.1f}s")
        return 0

    if args.cmd == "search":
        from .search import HybridSearcher, vectors_base
        db = args.db or default_db_path()
        with Index(db, root) as ix:
            ix.update()
            chunks = ix.chunks()
        hs = HybridSearcher(vectors_base(db))
        hs.update(BM25Index(chunks), chunks)
        hits = hs.search(args.query, mode=args.mode or "", k=args.k, per_page=None if args.all_sections else 2)
        color = args.color == "always" or (args.color == "auto" and sys.stdout.isatty())
        mark = ("\033[1;33m", "\033[0m") if color else ("**", "**")
        if not hits:
            print(f"no results for {args.query!r}")
            return 1
        for n, h in enumerate(hits, 1):
            c = h.chunk
            print(f"{n:>2}. {c.rel}:{c.start_line}  [{h.score:.3f} {h.match}]  {c.title}")
            print(f"    {snippet(c.text, h.terms, mark=mark)}")
        return 0

    files = scan(root)
    pages = [parse(read_page(root, f.rel), f.rel) for f in files]
    if args.cmd == "scan":
        print(f"{'page':<60} {'head':>4} {'link':>4} {'open':>4} {'ans':>4} {'find':>4}")
        for p in pages:
            o = sum(d.status == "open" for d in p.doubts)
            a = len(p.doubts) - o
            print(f"{p.rel:<60} {len(p.headings):>4} {len(p.links):>4} {o:>4} {a:>4} {len(p.findings):>4}")
        tot_o = sum(d.status == "open" for p in pages for d in p.doubts)
        tot_d = sum(len(p.doubts) for p in pages)
        print(f"\n{len(pages)} pages, {tot_d} doubts ({tot_o} open), "
              f"{sum(len(p.findings) for p in pages)} findings, {sum(len(p.links) for p in pages)} links")
    elif args.cmd == "doubts":
        allds = [(p.rel, d) for p in pages for d in p.doubts]
        allds.sort(key=lambda x: (x[1].status != "open", x[0], x[1].line))
        for rel, d in allds:
            mark = "🔴" if d.status == "open" else "🟢"
            print(f"{mark} {d.question}  ({rel}:{d.line})")
    elif args.cmd == "chunks":
        allc = [c for p in pages for c in chunk_page(p)]
        for p in pages:
            print(f"{p.rel:<60} {sum(c.rel == p.rel for c in allc):>4}")
        sizes = sorted(len(c.text) for c in allc) or [0]
        uniq = len({c.id for c in allc})
        print(f"\n{len(allc)} chunks ({uniq} unique ids) from {len(pages)} pages; "
              f"chars median={sizes[len(sizes) // 2]} max={sizes[-1]}")
    elif args.cmd == "links":
        resolved = LinkResolver(pages, root).resolve_all()
        counts: dict[str, int] = {}
        for r in resolved:
            counts[r.status] = counts.get(r.status, 0) + 1
        bad = [r for r in resolved if r.is_bad]
        if not args.broken:
            print(f"{len(resolved)} links: " + ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))
            print()
        for r in bad:
            print(f"{r.status:<10} {r.src}:{r.line}  -> {r.raw}")
        if not args.broken:
            print(f"\n{len(bad)} bad links")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
