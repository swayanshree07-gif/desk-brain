// Desk Brain frontend: hash routes, no build step.
//   #/                 home (index counts)
//   #/search?q=...&mode=hybrid|keyword|semantic&all=1   search results
//   #/page/<rel>?h=x   one page, scrolled to heading x
//   #/graph?area=&folders=1&orphans=0   knowledge graph (graph.js)
//   #/doubts?area=&q=  doubts board (open / answered columns)
"use strict";

const $ = (s) => document.querySelector(s);
const main = $("#main");
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const pageHref = (rel, h) => "#/page/" + rel.split("/").map(encodeURIComponent).join("/") + (h ? "?h=" + encodeURIComponent(h) : "");

async function api(path, opts) {
  const r = await fetch(path, opts);
  if (!r.ok) throw new Error(`${r.status} ${(await r.json().catch(() => ({}))).detail || r.statusText}`);
  return r.json();
}

function parseHash() {
  const h = location.hash.replace(/^#/, "") || "/";
  const [path, qs] = h.split("?");
  return { path, params: new URLSearchParams(qs || "") };
}

async function route() {
  const { path, params } = parseHash();
  main.classList.toggle("wide", path === "/graph" || path === "/doubts");
  document.querySelectorAll("header nav a").forEach((a) => a.classList.toggle("on", a.getAttribute("href") === "#" + path));
  try {
    if (path === "/graph") {
      await showGraph(params);
    } else if (path === "/doubts") {
      await showDoubts(params);
    } else if (path.startsWith("/page/")) {
      const rel = path.slice(6).split("/").map(decodeURIComponent).join("/");
      await showPage(rel, params.get("h"));
    } else if (path === "/search") {
      $("#q").value = params.get("q") || "";
      $("#all").checked = params.get("all") === "1";
      if (params.get("mode")) $("#mode").value = params.get("mode");
      await showSearch(params.get("q") || "", params.get("all") === "1", $("#mode").value);
    } else {
      await showHome();
    }
  } catch (e) {
    main.innerHTML = `<p class="empty">Error: ${esc(e.message)}</p>`;
  }
}

let health = null;
async function loadHealth() {
  health = await api("/api/health");
  // Without the embedding model only keyword search works: disable the other modes.
  for (const o of $("#mode").options) o.disabled = !health.semantic && o.value !== "keyword";
  if ($("#mode").selectedOptions[0].disabled) $("#mode").value = "keyword";
  return health;
}

async function showHome() {
  const h = await loadHealth();
  const c = h.counts;
  main.innerHTML = `<h2>Desk: ${esc(h.desk)}</h2>
    <p>${c.pages} pages · ${c.chunks} sections · ${c.links} links (${c.bad_links} broken) ·
       ${c.doubts} doubts (${c.open_doubts} open) · ${c.findings} findings</p>
    <p class="muted">Type in the search box to find sections across the desk.
      <b>hybrid</b> mixes word matches and meaning; <b>keyword</b> = exact words; <b>meaning</b> = similar ideas.
      ${h.semantic ? "" : "(Meaning search is off: the embedding model is not downloaded — run <code>.venv/bin/python -m deskbrain embed</code> once.)"}</p>`;
  document.title = "Desk Brain";
}

const BADGE = { keyword: "words", semantic: "meaning", both: "words + meaning" };

async function showSearch(q, all, mode) {
  document.title = q ? `${q} — Desk Brain` : "Desk Brain";
  if (!q.trim()) { main.innerHTML = `<p class="empty">Type something to search.</p>`; return; }
  const r = await api(`/api/search?q=${encodeURIComponent(q)}&k=30&mode=${encodeURIComponent(mode || "")}${all ? "&all=true" : ""}`);
  const note = r.mode !== mode && mode ? ` · ${esc(mode)} unavailable, used ${esc(r.mode)}` : "";
  if (!r.hits.length) { main.innerHTML = `<p class="empty">No results for “${esc(q)}”${note}.</p>`; return; }
  main.innerHTML = `<p class="muted">${r.hits.length} results · ${esc(r.mode)} · ${r.ms} ms${note}</p>` + r.hits.map((h) => `
    <div class="hit">
      <a class="t" href="${pageHref(h.rel, h.section_slug)}" data-line="${h.line}">${esc(h.page_title)}</a>
      <span class="badge b-${esc(h.match)}" title="${h.similarity != null ? "similarity " + h.similarity : ""}">${esc(BADGE[h.match] || h.match)}</span>
      <div class="crumb">${esc(h.rel)} › ${esc(h.breadcrumb.join(" › "))}</div>
      <div class="snip">${h.snippet}</div>
    </div>`).join("");   // snippet is HTML-escaped on the server, with <mark> for matches
}

async function showPage(rel, anchor) {
  const p = await api(`/api/page?rel=${encodeURIComponent(rel)}`);
  document.title = `${p.title} — Desk Brain`;
  const when = new Date(p.mtime * 1000).toLocaleString();
  const back = p.backlinks.length
    ? `<div class="backlinks"><strong>Linked from</strong><ul>${p.backlinks.map((b) =>
        `<li><a href="${pageHref(b.rel)}">${esc(b.title)}</a> <span class="muted">${esc(b.rel)}</span></li>`).join("")}</ul></div>`
    : "";
  main.innerHTML = `<div class="page-meta">${esc(p.rel)} · modified ${esc(when)}</div>
    <article class="page-body">${p.html}</article>${back}`;  // html is rendered server-side without raw HTML
  if (anchor) {
    const el = document.getElementById(anchor);
    if (el) { el.scrollIntoView(); el.classList.add("flash"); }
  } else {
    window.scrollTo(0, 0);
  }
}

let graphData = null;   // cached until the next re-index
async function showGraph(params) {
  document.title = "Graph — Desk Brain";
  if (!graphData) graphData = await api("/api/graph");
  const state = { area: params.get("area") || "", folders: params.get("folders") === "1", orphans: params.get("orphans") !== "0" };
  window.DeskGraph.mount(main, graphData, state, (rel) => { location.hash = pageHref(rel); }, (s) => {
    const qs = new URLSearchParams();
    if (s.area) qs.set("area", s.area);
    if (s.folders) qs.set("folders", "1");
    if (!s.orphans) qs.set("orphans", "0");
    location.hash = "#/graph" + (qs.toString() ? "?" + qs : "");
  });
}

function doubtCard(d) {
  const ctx = d.context ? `<div class="d-ctx"><b>Context:</b> ${esc(d.context)}</div>` : "";
  const ans = d.answer ? `<div class="d-ans"><b>A:</b> ${esc(d.answer)}</div>` : "";
  return `<div class="doubt d-${esc(d.status)}">
    <a class="d-q" href="${pageHref(d.rel, d.anchor)}">${esc(d.question)}</a>
    <div class="muted">${esc(d.page_title)} · ${esc(d.rel)}:${d.line}${d.asked ? " · asked " + esc(d.asked) : ""}</div>${ctx}${ans}</div>`;
}

async function showDoubts(params) {
  document.title = "Doubts — Desk Brain";
  const area = params.get("area") || "", q = params.get("q") || "";
  const r = await api(`/api/doubts?area=${encodeURIComponent(area)}&q=${encodeURIComponent(q)}`);
  const open = r.doubts.filter((d) => d.status === "open"), done = r.doubts.filter((d) => d.status === "answered");
  const col = (title, list, empty) => `<section class="d-col"><h3>${title} <span class="muted">${list.length}</span></h3>${
    list.length ? list.map(doubtCard).join("") : `<p class="empty">${empty}</p>`}</section>`;
  main.innerHTML = `<div class="graph-bar">
      <label>Area <select id="d-area"><option value="">all</option>${r.areas.map((a) =>
        `<option value="${esc(a)}"${a === area ? " selected" : ""}>${esc(a)}</option>`).join("")}</select></label>
      <form id="d-form"><input id="d-q" type="search" placeholder="search in doubts…" value="${esc(q)}" autocomplete="off"></form>
      <span class="muted">${r.counts.open} open · ${r.counts.answered} answered on the whole desk</span></div>
    <div class="d-board">${col("🔴 Open", open, q || area ? "No open doubts match." : "No open doubts. 🎉")}${
      col("🟢 Answered", done, "No answered doubts match.")}</div>`;
  const go = (a, qq) => { const p = new URLSearchParams(); if (a) p.set("area", a); if (qq) p.set("q", qq);
    location.hash = "#/doubts" + (p.toString() ? "?" + p : ""); };
  $("#d-area").onchange = (e) => go(e.target.value, $("#d-q").value.trim());
  $("#d-form").onsubmit = (e) => { e.preventDefault(); go($("#d-area").value, $("#d-q").value.trim()); };
}

$("#search-form").addEventListener("submit", (e) => {
  e.preventDefault();
  const q = $("#q").value.trim();
  location.hash = `#/search?q=${encodeURIComponent(q)}&mode=${$("#mode").value}${$("#all").checked ? "&all=1" : ""}`;
});
$("#all").addEventListener("change", () => $("#search-form").requestSubmit());
$("#mode").addEventListener("change", () => { localStorage.setItem("deskbrain.mode", $("#mode").value); $("#search-form").requestSubmit(); });
$("#reindex").addEventListener("click", async () => {
  $("#status").textContent = "re-indexing…";
  try {
    const r = await api("/api/reindex", { method: "POST" });
    graphData = null;
    $("#status").textContent = `${r.added.length} added, ${r.changed.length} changed, ${r.removed.length} removed`;
    route();
  } catch (e) { $("#status").textContent = "re-index failed: " + e.message; }
});
document.addEventListener("keydown", (e) => {
  if (e.key === "/" && document.activeElement !== $("#q")) { e.preventDefault(); $("#q").focus(); }
});
window.addEventListener("hashchange", route);
$("#mode").value = localStorage.getItem("deskbrain.mode") || "hybrid";
loadHealth().catch(() => {}).finally(route);
