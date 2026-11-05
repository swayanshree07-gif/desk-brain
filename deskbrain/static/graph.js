// Desk Brain graph view: vanilla JS force layout on a <canvas>, no build step.
// The layout part (DeskGraph.layout / filterGraph) is pure and also runs under Node,
// so tests/test_graph_js.py can check it without a browser.
"use strict";

(function (root) {
  // ---------- pure part ----------

  // Small deterministic PRNG so the same graph always gets the same picture.
  function rng(seed) {
    let s = seed >>> 0 || 1;
    return () => ((s = (s * 1664525 + 1013904223) >>> 0) / 4294967296);
  }

  // Keep only what the view should draw: pages of `area` ("" = all areas, "(root)" = desk root),
  // optional folder nodes, optional orphans; edges only between kept nodes.
  function filterGraph(g, opts) {
    const o = Object.assign({ area: "", folders: false, orphans: true }, opts || {});
    const keep = new Set();
    for (const n of g.nodes) {
      if (n.type === "folder" && !o.folders) continue;
      if (n.type === "page" && n.orphan && !o.orphans) continue;
      if (o.area) {
        const a = n.area || "(root)";
        if (a !== o.area) continue;
      }
      keep.add(n.id);
    }
    const nodes = g.nodes.filter((n) => keep.has(n.id));
    const edges = g.edges.filter((e) => keep.has(e.source) && keep.has(e.target) &&
                                        (e.kind !== "folder" || o.folders));
    return { nodes, edges };
  }

  // Force-directed layout (repulsion + springs + weak gravity), deterministic.
  // Returns {id: {x, y}} centred on (0, 0).
  function layout(nodes, edges, opts) {
    const o = Object.assign({ iterations: 300, seed: 7, length: 60 }, opts || {});
    const rand = rng(o.seed);
    const idx = new Map(nodes.map((n, i) => [n.id, i]));
    const N = nodes.length;
    const x = new Float64Array(N), y = new Float64Array(N), vx = new Float64Array(N), vy = new Float64Array(N);
    const r0 = o.length * Math.sqrt(N + 1);
    for (let i = 0; i < N; i++) {
      const a = rand() * 2 * Math.PI, r = r0 * Math.sqrt(rand());
      x[i] = r * Math.cos(a); y[i] = r * Math.sin(a);
    }
    const E = edges.map((e) => [idx.get(e.source), idx.get(e.target), e.kind === "folder" ? 0.5 : 1])
                   .filter(([a, b]) => a !== undefined && b !== undefined && a !== b);
    const L = o.length, rep = L * L * 2.5;
    for (let it = 0; it < o.iterations; it++) {
      const t = 1 - it / o.iterations;          // cooling
      const fx = new Float64Array(N), fy = new Float64Array(N);
      for (let i = 0; i < N; i++) {
        for (let j = i + 1; j < N; j++) {
          let dx = x[i] - x[j], dy = y[i] - y[j];
          let d2 = dx * dx + dy * dy;
          if (d2 < 0.01) { dx = rand() - 0.5; dy = rand() - 0.5; d2 = 0.01; }
          if (d2 > 250000) continue;                // ignore far-away pairs
          const f = rep / d2, d = Math.sqrt(d2);
          fx[i] += f * dx / d; fy[i] += f * dy / d; fx[j] -= f * dx / d; fy[j] -= f * dy / d;
        }
      }
      for (const [a, b, s] of E) {
        const dx = x[b] - x[a], dy = y[b] - y[a], d = Math.sqrt(dx * dx + dy * dy) || 0.01;
        const f = s * (d - L) * 0.05;
        fx[a] += f * dx / d; fy[a] += f * dy / d; fx[b] -= f * dx / d; fy[b] -= f * dy / d;
      }
      for (let i = 0; i < N; i++) {
        fx[i] -= x[i] * 0.01; fy[i] -= y[i] * 0.01;   // gravity keeps components together
        vx[i] = (vx[i] + fx[i]) * 0.6; vy[i] = (vy[i] + fy[i]) * 0.6;
        const sp = Math.hypot(vx[i], vy[i]), max = 30 * t + 1;
        if (sp > max) { vx[i] *= max / sp; vy[i] *= max / sp; }
        x[i] += vx[i]; y[i] += vy[i];
      }
    }
    let cx = 0, cy = 0;
    for (let i = 0; i < N; i++) { cx += x[i]; cy += y[i]; }
    cx /= N || 1; cy /= N || 1;
    const pos = {};
    nodes.forEach((n, i) => { pos[n.id] = { x: x[i] - cx, y: y[i] - cy }; });
    return pos;
  }

  const api = { layout, filterGraph, rng };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  root.DeskGraph = api;

  // ---------- browser view ----------
  if (typeof document === "undefined") return;

  const PALETTE = ["#4e79a7", "#f28e2b", "#59a14f", "#b07aa1", "#76b7b2", "#edc948", "#9c755f", "#e15759", "#bab0ac", "#ff9da7"];
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  // Renders the graph into `container`. `go(rel)` opens a page. `state` = {area, folders, orphans}.
  api.mount = function (container, g, state, go, onState) {
    const areas = g.areas.map((a) => a || "(root)");
    const color = {};
    areas.forEach((a, i) => { color[a] = PALETTE[i % PALETTE.length]; });
    const view = filterGraph(g, state);
    const pos = layout(view.nodes, view.edges);
    const st = g.stats;

    container.innerHTML = `
      <div class="graph-bar">
        <label>Area <select id="g-area"><option value="">all</option>${areas.map((a) =>
          `<option value="${esc(a)}"${a === state.area ? " selected" : ""}>${esc(a)}</option>`).join("")}</select></label>
        <label class="opt"><input id="g-folders" type="checkbox"${state.folders ? " checked" : ""}> folders</label>
        <label class="opt"><input id="g-orphans" type="checkbox"${state.orphans ? " checked" : ""}> orphans</label>
        <input id="g-find" type="search" placeholder="highlight pages…" autocomplete="off">
        <span class="muted">${st.pages} pages · ${st.link_edges} links · ${st.orphans} orphans · ${st.broken} bad links</span>
      </div>
      <div class="graph-wrap"><canvas id="g-canvas"></canvas><div id="g-tip" class="graph-tip" hidden></div></div>
      <div class="graph-legend muted">${areas.map((a) =>
        `<span><i style="background:${color[a]}"></i>${esc(a)}</span>`).join("")}
        <span><i class="ring-orphan"></i>orphan (no links)</span><span><i class="ring-bad"></i>has bad links</span>
        <span>· size = links · drag to pan, wheel to zoom, click a page to open it</span></div>
      <div id="g-broken"></div>`;

    const bad = g.broken.filter((b) => !state.area || (b.src.includes("/") ? b.src.split("/")[0] : "(root)") === state.area);
    container.querySelector("#g-broken").innerHTML = bad.length ? `<details><summary>${bad.length} bad links</summary><ul>${
      bad.map((b) => `<li><a href="#/page/${b.src.split("/").map(encodeURIComponent).join("/")}">${esc(b.src)}</a>:${b.line}
        <code>${esc(b.raw)}</code> <span class="badge">${esc(b.status)}</span></li>`).join("")}</ul></details>` : "";

    const set = (patch) => onState(Object.assign({}, state, patch));
    container.querySelector("#g-area").onchange = (e) => set({ area: e.target.value });
    container.querySelector("#g-folders").onchange = (e) => set({ folders: e.target.checked });
    container.querySelector("#g-orphans").onchange = (e) => set({ orphans: e.target.checked });

    const canvas = container.querySelector("#g-canvas"), tip = container.querySelector("#g-tip");
    const ctx = canvas.getContext("2d");
    const nodes = view.nodes.map((n) => Object.assign({}, n, pos[n.id], {
      r: n.type === "folder" ? 4 : 4 + 2.2 * Math.sqrt(n.degree || 0),
      c: color[n.area || "(root)"] || "#888",
    }));
    const byId = new Map(nodes.map((n) => [n.id, n]));
    let find = "", hover = null, cam = { x: 0, y: 0, k: 1 };

    function fit() {
      const w = canvas.clientWidth, h = canvas.clientHeight;
      if (!nodes.length) return;
      const xs = nodes.map((n) => n.x), ys = nodes.map((n) => n.y);
      const bw = Math.max(...xs) - Math.min(...xs) + 80, bh = Math.max(...ys) - Math.min(...ys) + 80;
      cam.k = Math.min(2, w / bw, h / bh);
      cam.x = (Math.max(...xs) + Math.min(...xs)) / 2; cam.y = (Math.max(...ys) + Math.min(...ys)) / 2;
    }
    const toScreen = (n) => [(n.x - cam.x) * cam.k + canvas.clientWidth / 2, (n.y - cam.y) * cam.k + canvas.clientHeight / 2];
    const toWorld = (sx, sy) => [(sx - canvas.clientWidth / 2) / cam.k + cam.x, (sy - canvas.clientHeight / 2) / cam.k + cam.y];

    function draw() {
      const dpr = window.devicePixelRatio || 1, w = canvas.clientWidth, h = canvas.clientHeight;
      if (canvas.width !== w * dpr) { canvas.width = w * dpr; canvas.height = h * dpr; }
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, w, h);
      const dark = matchMedia("(prefers-color-scheme: dark)").matches;
      const fg = dark ? "#e6e6e3" : "#1d1d1f";
      const near = hover ? new Set([hover.id]) : null;
      if (hover) for (const e of view.edges) {
        if (e.source === hover.id) near.add(e.target);
        if (e.target === hover.id) near.add(e.source);
      }
      for (const e of view.edges) {
        const a = byId.get(e.source), b = byId.get(e.target);
        const [x1, y1] = toScreen(a), [x2, y2] = toScreen(b);
        const lit = hover && (e.source === hover.id || e.target === hover.id);
        ctx.strokeStyle = e.kind === "folder" ? (dark ? "#3a3a40" : "#ddd") : lit ? fg : (dark ? "#55555c" : "#b8b8bd");
        ctx.globalAlpha = hover && !lit ? 0.25 : 1;
        ctx.lineWidth = e.kind === "folder" ? 1 : Math.min(4, 0.8 + 0.5 * e.weight);
        ctx.setLineDash(e.bad_anchor ? [4, 3] : e.kind === "folder" ? [2, 3] : []);
        ctx.beginPath(); ctx.moveTo(x1, y1); ctx.lineTo(x2, y2); ctx.stroke();
        if (e.kind === "link") {                 // arrow head at the target's rim
          const ang = Math.atan2(y2 - y1, x2 - x1), rr = b.r * Math.sqrt(cam.k);
          const ax = x2 - Math.cos(ang) * rr, ay = y2 - Math.sin(ang) * rr;
          ctx.setLineDash([]); ctx.beginPath(); ctx.moveTo(ax, ay);
          ctx.lineTo(ax - 7 * Math.cos(ang - 0.4), ay - 7 * Math.sin(ang - 0.4));
          ctx.lineTo(ax - 7 * Math.cos(ang + 0.4), ay - 7 * Math.sin(ang + 0.4));
          ctx.closePath(); ctx.fillStyle = ctx.strokeStyle; ctx.fill();
        }
      }
      ctx.setLineDash([]);
      const q = find.toLowerCase();
      for (const n of nodes) {
        const [sx, sy] = toScreen(n), r = n.r * Math.sqrt(cam.k);
        const match = q && (n.title.toLowerCase().includes(q) || n.id.toLowerCase().includes(q));
        ctx.globalAlpha = (near && !near.has(n.id)) || (q && !match) ? 0.25 : 1;
        ctx.beginPath(); ctx.arc(sx, sy, r, 0, 2 * Math.PI);
        if (n.type === "folder") { ctx.strokeStyle = n.c; ctx.lineWidth = 2; ctx.stroke(); }
        else { ctx.fillStyle = n.c; ctx.fill(); }
        if (n.orphan) { ctx.strokeStyle = "#e0a000"; ctx.lineWidth = 2.5; ctx.setLineDash([3, 2]);
          ctx.beginPath(); ctx.arc(sx, sy, r + 3, 0, 2 * Math.PI); ctx.stroke(); ctx.setLineDash([]); }
        if (n.bad_links) { ctx.strokeStyle = "#c0392b"; ctx.lineWidth = 2;
          ctx.beginPath(); ctx.arc(sx, sy, r + 1, 0, 2 * Math.PI); ctx.stroke(); }
        if (match) { ctx.strokeStyle = fg; ctx.lineWidth = 2;
          ctx.beginPath(); ctx.arc(sx, sy, r + 5, 0, 2 * Math.PI); ctx.stroke(); }
        // Small graphs: label everything; big ones: hubs only until zoomed in (hover/highlight always).
        if (nodes.length < 30 || cam.k * (1 + (n.degree || 0) / 4) > 2.2 || n === hover || match || (near && near.has(n.id))) {
          ctx.fillStyle = fg; ctx.font = "12px system-ui, sans-serif";
          ctx.fillText(n.title, sx + r + 3, sy + 4);
        }
      }
      ctx.globalAlpha = 1;
    }

    function hit(ev) {
      const b = canvas.getBoundingClientRect(), sx = ev.clientX - b.left, sy = ev.clientY - b.top;
      let best = null, bd = 1e9;
      for (const n of nodes) {
        const [nx, ny] = toScreen(n), d = Math.hypot(nx - sx, ny - sy);
        if (d < Math.max(8, n.r * Math.sqrt(cam.k) + 3) && d < bd) { best = n; bd = d; }
      }
      return best;
    }

    let drag = null;
    canvas.onmousedown = (ev) => { drag = { x: ev.clientX, y: ev.clientY, cx: cam.x, cy: cam.y, moved: false }; };
    window.onmouseup = (ev) => {
      if (drag && !drag.moved && ev.target === canvas) {
        const n = hit(ev);
        if (n && n.type === "page") go(n.id);
      }
      drag = null;
    };
    canvas.onmousemove = (ev) => {
      if (drag) {
        const dx = ev.clientX - drag.x, dy = ev.clientY - drag.y;
        if (Math.abs(dx) + Math.abs(dy) > 3) drag.moved = true;
        cam.x = drag.cx - dx / cam.k; cam.y = drag.cy - dy / cam.k; draw(); return;
      }
      const n = hit(ev);
      if (n !== hover) { hover = n; draw(); }
      canvas.style.cursor = n && n.type === "page" ? "pointer" : "grab";
      if (n) {
        const b = canvas.getBoundingClientRect();
        tip.hidden = false;
        tip.style.left = (ev.clientX - b.left + 14) + "px"; tip.style.top = (ev.clientY - b.top + 10) + "px";
        tip.innerHTML = n.type === "folder" ? `<b>${esc(n.id.slice(4))}/</b>` :
          `<b>${esc(n.title)}</b><br><span class="muted">${esc(n.id)}</span><br>
           ${n.out} out · ${n.in} in · ${n.doubts} doubts (${n.open_doubts} open) · ${n.findings} findings
           ${n.bad_links ? `<br><span class="bad">${n.bad_links} bad links</span>` : ""}${n.orphan ? "<br>orphan" : ""}`;
      } else tip.hidden = true;
    };
    canvas.onmouseleave = () => { tip.hidden = true; if (hover) { hover = null; draw(); } };
    canvas.onwheel = (ev) => {
      ev.preventDefault();
      const b = canvas.getBoundingClientRect(), sx = ev.clientX - b.left, sy = ev.clientY - b.top;
      const [wx, wy] = toWorld(sx, sy);
      cam.k = Math.max(0.1, Math.min(6, cam.k * (ev.deltaY < 0 ? 1.15 : 1 / 1.15)));
      cam.x = wx - (sx - canvas.clientWidth / 2) / cam.k; cam.y = wy - (sy - canvas.clientHeight / 2) / cam.k;
      draw();
    };
    container.querySelector("#g-find").oninput = (e) => { find = e.target.value.trim(); draw(); };
    window.onresize = () => { if (document.body.contains(canvas)) draw(); };
    if (!nodes.length) {
      container.querySelector(".graph-wrap").innerHTML = `<p class="empty">No pages to show with these filters.</p>`;
      return;
    }
    fit(); draw();
  };
})(typeof window !== "undefined" ? window : globalThis);
