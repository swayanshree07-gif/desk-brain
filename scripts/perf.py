"""Performance report: index time, query latency per mode, disk usage.

Usage: .venv/bin/python scripts/perf.py [DESK] [--json]   (default: $DESK_PATH or ~/Personal-Desk)

Read-only on the desk. The full (cold) index is built in a temporary folder so the
real data/index.sqlite is untouched; the embedding model is reused from data/models.
Exit code 1 if a limit is exceeded (data/ > 1.5 GB, p95 query > 1 s, no-op re-index > 2 s).
"""
from __future__ import annotations

import json
import os
import statistics
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from deskbrain.bm25 import BM25Index  # noqa: E402
from deskbrain.config import DATA_DIR, desk_path  # noqa: E402
from deskbrain.embed import model_cached  # noqa: E402
from deskbrain.search import HybridSearcher, vectors_base  # noqa: E402
from deskbrain.store import Index  # noqa: E402

QUERIES = ["reverse engineering", "how do I take apart an android app", "open questions",
           "network", "what is a hash", "python", "linux kernel", "encryption keys"]
LIMITS = {"disk_mb": 1536, "query_p95_ms": 1000, "noop_reindex_s": 2.0}


def du_mb(p: Path) -> float:
    total = 0
    for dp, _, fs in os.walk(p):
        for f in fs:
            fp = Path(dp, f)
            if not fp.is_symlink():
                total += fp.stat().st_size
    return total / 2**20


def run(root: Path, semantic: bool | None = None) -> dict:
    out: dict = {"desk_pages": 0}
    with tempfile.TemporaryDirectory(prefix="deskbrain-perf-") as td:
        db = Path(td) / "index.sqlite"
        t = time.perf_counter()
        with Index(db, root) as ix:
            ix.update()
            out["index_full_s"] = round(time.perf_counter() - t, 3)
            t = time.perf_counter()
            st = ix.update()
            out["noop_reindex_s"] = round(time.perf_counter() - t, 3)
            out["noop_changed"] = st.n_changed
            out.update(ix.counts())
            chunks = ix.chunks()
        bm = BM25Index(chunks)
        hs = HybridSearcher(vectors_base(db), semantic=semantic)
        t = time.perf_counter()
        hs.update(bm, chunks)
        out["embed_full_s"] = round(time.perf_counter() - t, 3)
        modes = ["keyword"] + (["semantic", "hybrid"] if hs.available else [])
        if hs.available:
            hs.search("warm up", mode="semantic")   # model load not counted as query time
        lat = {}
        for m in modes:
            ms = []
            for q in QUERIES:
                hs._qcache.clear()                   # measure uncached query embedding
                t = time.perf_counter()
                hs.search(q, mode=m, k=10)
                ms.append((time.perf_counter() - t) * 1000)
            ms.sort()
            lat[m] = {"median_ms": round(statistics.median(ms), 1),
                      "p95_ms": round(ms[min(len(ms) - 1, int(0.95 * len(ms)))], 1)}
        out["query"] = lat
        out["temp_index_mb"] = round(du_mb(Path(td)), 2)
    out["data_dir_mb"] = round(du_mb(DATA_DIR), 1) if DATA_DIR.exists() else 0.0
    out["semantic"] = hs.available
    problems = []
    if out["data_dir_mb"] > LIMITS["disk_mb"]:
        problems.append(f"data/ is {out['data_dir_mb']} MB > {LIMITS['disk_mb']} MB")
    if out["noop_reindex_s"] > LIMITS["noop_reindex_s"]:
        problems.append(f"no-op re-index {out['noop_reindex_s']} s")
    for m, v in lat.items():
        if v["p95_ms"] > LIMITS["query_p95_ms"]:
            problems.append(f"{m} p95 {v['p95_ms']} ms")
    out["problems"] = problems
    return out


def main(argv: list[str]) -> int:
    as_json = "--json" in argv
    args = [a for a in argv if a != "--json"]
    root = (Path(args[0]) if args else desk_path()).expanduser().resolve()
    r = run(root, semantic=None if model_cached() else False)
    if as_json:
        print(json.dumps(r, indent=2))
    else:
        print(f"pages={r['pages']} chunks={r.get('chunks', '?')}  semantic={'on' if r['semantic'] else 'off'}")
        print(f"full index      {r['index_full_s']:.3f} s")
        print(f"full embed      {r['embed_full_s']:.3f} s")
        print(f"no-op re-index  {r['noop_reindex_s']:.3f} s ({r['noop_changed']} changed)")
        for m, v in r["query"].items():
            print(f"query {m:<9} median {v['median_ms']:.1f} ms  p95 {v['p95_ms']:.1f} ms")
        print(f"index files     {r['temp_index_mb']} MB   data/ total {r['data_dir_mb']} MB (limit {LIMITS['disk_mb']} MB)")
        print("OK" if not r["problems"] else "PROBLEMS: " + "; ".join(r["problems"]))
    return 1 if r["problems"] else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
