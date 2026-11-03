"""Command line: python -m deskbrain <command>.

  scan     list indexable pages with counts of headings, links, doubts, findings
  doubts   list doubts (open first)
  chunks   per-page section (chunk) counts and size stats
  links    link summary by status; --broken lists only bad links
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


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="deskbrain")
    ap.add_argument("--desk", type=Path, default=None, help="desk root (default: $DESK_PATH or ~/Personal-Desk)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("scan", help="list indexable pages")
    sub.add_parser("doubts", help="list doubts")
    sub.add_parser("chunks", help="split pages into sections; print counts")
    lp = sub.add_parser("links", help="resolve links, list broken ones")
    lp.add_argument("--broken", action="store_true", help="only print the bad links")
    args = ap.parse_args(argv)
    root = (args.desk or desk_path()).expanduser().resolve()
    if not root.is_dir():
        print(f"desk not found: {root}", file=sys.stderr)
        return 2

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
