"""Plot query latency from scripts/perf.py --json output (run on the fixture desk).
Usage: .venv/bin/python scripts/perf.py tests/fixtures/desk --json > perf.json
       .venv/bin/python tools/perf_chart.py perf.json docs/media/latency.png"""
import json, sys
import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt
d = json.load(open(sys.argv[1]))
rows = [(m, d["query"][m]) for m in ("keyword", "semantic", "hybrid")] + [("ask", d["ask"]), ("related", d["related"])]
names = [r[0] for r in rows]; med = [r[1]["median_ms"] for r in rows]; p95 = [r[1]["p95_ms"] for r in rows]
fig, ax = plt.subplots(figsize=(6.4, 3.2), dpi=100)
y = range(len(rows))
ax.barh([i + 0.2 for i in y], med, 0.38, color="#3b6fd4", label="median")
ax.barh([i - 0.2 for i in y], p95, 0.38, color="#e3913a", label="p95")
for i, (a, b) in enumerate(zip(med, p95)):
    ax.text(a + 0.05, i + 0.2, f"{a} ms", va="center", fontsize=8); ax.text(b + 0.05, i - 0.2, f"{b} ms", va="center", fontsize=8)
ax.set_yticks(list(y), names); ax.invert_yaxis(); ax.set_xlabel("milliseconds")
ax.set_title(f"Query latency · fixture desk ({d['pages']} pages, {d['chunks']} sections, CPU)", fontsize=10)
for s in ("top", "right"): ax.spines[s].set_visible(False)
ax.legend(frameon=False, fontsize=8, loc="lower right"); fig.tight_layout(); fig.savefig(sys.argv[2])
