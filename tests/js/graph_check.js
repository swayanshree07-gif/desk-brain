// Run by tests/test_graph_js.py: node graph_check.js <graph.js> <graph.json>
// Prints a JSON report about filterGraph + layout on the given graph data.
"use strict";
const fs = require("fs");
const G = require(process.argv[2]);
const g = JSON.parse(fs.readFileSync(process.argv[3], "utf8"));
const out = {};
const all = G.filterGraph(g, {});
out.default_nodes = all.nodes.map((n) => n.id).sort();
out.default_edge_kinds = [...new Set(all.edges.map((e) => e.kind))].sort();
const withF = G.filterGraph(g, { folders: true });
out.folders_nodes = withF.nodes.length;
out.folders_edges = withF.edges.length;
for (const a of [...g.areas.map((x) => x || "(root)")]) {
  const v = G.filterGraph(g, { area: a, folders: true });
  out["area:" + a] = { nodes: v.nodes.map((n) => n.id).sort(),
    edges_ok: v.edges.every((e) => v.nodes.some((n) => n.id === e.source) && v.nodes.some((n) => n.id === e.target)) };
}
out.no_orphans = G.filterGraph(g, { orphans: false }).nodes.map((n) => n.id).sort();
const t0 = Date.now();
const p1 = G.layout(withF.nodes, withF.edges);
out.layout_ms = Date.now() - t0;
const p2 = G.layout(withF.nodes, withF.edges);
out.deterministic = JSON.stringify(p1) === JSON.stringify(p2);
out.finite = Object.values(p1).every((p) => Number.isFinite(p.x) && Number.isFinite(p.y));
out.positions = Object.keys(p1).length;
const d = (a, b) => Math.hypot(p1[a].x - p1[b].x, p1[a].y - p1[b].y);
const links = withF.edges.filter((e) => e.kind === "link");
const linked = new Set(links.map((e) => e.source + "|" + e.target));
const pages = withF.nodes.filter((n) => n.type === "page").map((n) => n.id);
let ls = 0, us = 0, un = 0;
for (const e of links) ls += d(e.source, e.target);
for (const a of pages) for (const b of pages)
  if (a < b && !linked.has(a + "|" + b) && !linked.has(b + "|" + a)) { us += d(a, b); un++; }
out.mean_linked = ls / (links.length || 1);
out.mean_unlinked = us / (un || 1);
let minD = 1e9;
const ids = Object.keys(p1);
for (let i = 0; i < ids.length; i++) for (let j = i + 1; j < ids.length; j++) minD = Math.min(minD, d(ids[i], ids[j]));
out.min_dist = minD;
out.empty_layout = Object.keys(G.layout([], [])).length;
// A big random graph must stay fast enough for the real desk (and beyond).
const big = { nodes: [], edges: [] }, r = G.rng(3);
for (let i = 0; i < 300; i++) big.nodes.push({ id: "n" + i, type: "page" });
for (let i = 0; i < 600; i++) big.edges.push({ source: "n" + Math.floor(r() * 300), target: "n" + Math.floor(r() * 300), kind: "link" });
const t1 = Date.now();
const pb = G.layout(big.nodes, big.edges);
out.big_ms = Date.now() - t1;
out.big_finite = Object.values(pb).every((p) => Number.isFinite(p.x) && Number.isFinite(p.y));
console.log(JSON.stringify(out));
