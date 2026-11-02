"""Command line: python -m deskbrain <command>.

  scan     list indexable pages with counts of headings, links, doubts, findings
  doubts   list doubts (open first)
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .config import desk_path
from .parser import parse
from .scanner import read_page, scan


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="deskbrain")
    ap.add_argument("--desk", type=Path, default=None, help="desk root (default: $DESK_PATH or ~/Personal-Desk)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("scan", help="list indexable pages")
    sub.add_parser("doubts", help="list doubts")
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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
