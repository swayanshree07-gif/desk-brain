// Desk Brain frontend: hash routes, no build step.
//   #/                 home (index counts)
//   #/search?q=...&mode=hybrid|keyword|semantic&all=1   search results
//   #/page/<rel>?h=x   one page, scrolled to heading x
//   #/graph?area=&folders=1&orphans=0   knowledge graph (graph.js)
//   #/doubts?area=&q=  doubts board (open / answered columns)
//   #/review           flashcard review: due + new cards, reveal, grade 0–5 (SM-2)
//   #/ask?q=           ask the desk: extractive answer with [n] citations
//   #/digest?week=     weekly digest (week = 2026-W45 or a date; none = last 7 days)
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
    } else if (path === "/review") {
      await showReview();
    } else if (path === "/ask") {
      await showAsk(params.get("q") || "", params.get("mode") || "");
    } else if (path === "/digest") {
      await showDigest(params.get("week") || "");
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
  const [h, rec, od] = await Promise.all([loadHealth(), api("/api/recent?limit=8"), api("/api/doubts?status=open")]);
  const c = h.counts;
  const recent = rec.pages.length ? `<ul class="home-list">${rec.pages.map((p) => `<li><a href="${pageHref(p.rel)}">${esc(p.title)}</a>
      <span class="muted">${esc(p.rel)} · ${ago(p.mtime)}</span></li>`).join("")}</ul>` : `<p class="empty">No pages indexed yet. Press <b>Re-index</b> to scan the desk.</p>`;
  const open = od.doubts.slice(0, 8);
  const doubts = open.length ? `<ul class="home-list">${open.map((d) => `<li><a href="${pageHref(d.rel, d.anchor)}">${esc(d.question)}</a>
      <span class="muted">${esc(d.page_title)}${d.asked ? " · asked " + esc(d.asked) : ""}</span></li>`).join("")}</ul>
      ${od.doubts.length > open.length ? `<a href="#/doubts">All ${od.doubts.length} open doubts →</a>` : ""}` : `<p class="empty">No open doubts. 🎉</p>`;
  main.innerHTML = `<p class="muted home-counts">${esc(h.desk)}: ${c.pages} pages · ${c.chunks} sections · ${c.links} links (${c.bad_links} broken) ·
       ${c.doubts} doubts (${c.open_doubts} open) · ${c.findings} findings</p>
    <div class="home-grid">
      <section><h3>Recently changed</h3>${recent}</section>
      <section><h3>🔴 Open doubts</h3>${doubts}</section>
    </div>
    <p class="muted">Search: <b>hybrid</b> mixes word matches and meaning; <b>keyword</b> = exact words; <b>meaning</b> = similar ideas.
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
    <div class="page-layout"><div class="page-main"><article class="page-body">${p.html}</article>${back}</div>
    <aside class="related" id="related" data-for="${esc(p.rel)}" hidden></aside></div>`;  // html is rendered server-side without raw HTML
  loadRelated(rel);
  if (anchor) {
    const el = document.getElementById(anchor);
    if (el) { el.scrollIntoView(); el.classList.add("flash"); }
  } else {
    window.scrollTo(0, 0);
  }
}

// Day 21: Ask the desk. answerHtml/sourcesHtml are pure (tested under Node).
function citeLink(c) {
  return `<a class="cite" href="${pageHref(c.rel, c.slug)}" title="${esc(c.rel + (c.heading ? " › " + c.heading : ""))}">[${c.n}]</a>`;
}

function answerHtml(r) {
  if (!r) return "";
  const by = Object.fromEntries((r.citations || []).map((c) => [c.n, c]));
  if (!r.known || !(r.answer || []).length) {
    return `<div class="answer unknown"><p><strong>I don't know.</strong> The desk has nothing that clearly answers this.</p>
      ${(r.citations || []).length ? `<p class="muted">Closest notes are listed under “Sources”.</p>` : ""}</div>`;
  }
  const cite = (n) => by[n] ? citeLink(by[n]) : `[${esc(n)}]`;
  const llm = r.answer_method === "llm";
  return `<div class="answer"><p>${r.answer.map((a) => `${esc(a.text)} ${(a.cites || [a.n]).map(cite).join("")}`).join(" ")}</p>
    <p class="muted">${llm ? "Phrased by your local model from the cited notes only — check the sources" :
      "Sentences quoted from your notes"} · ${esc(r.answer_method || r.mode || "")}</p></div>`;
}

function sourcesHtml(r, open) {
  const cites = (r && r.citations) || [];
  if (!cites.length) return "";
  return `<details class="sources"${open ? " open" : ""}><summary>Sources (${cites.length})</summary><ol>${cites.map((c) => `
    <li value="${c.n}">${citeLink(c)} <a href="${pageHref(c.rel, c.slug)}">${esc(c.heading || c.rel)}</a>
      <span class="muted">${esc(c.rel)} · lines ${esc(c.line)}–${esc(c.end_line)}</span>
      <pre class="src-text">${esc(c.text)}</pre></li>`).join("")}</ol></details>`;
}

async function showAsk(q, mode) {
  document.title = q ? `Ask: ${q} — Desk Brain` : "Ask — Desk Brain";
  const form = `<form id="ask-form" class="ask-form"><input id="ask-q" type="search" placeholder="Ask your desk a question…"
      value="${esc(q)}" autocomplete="off" maxlength="500"><button type="submit">Ask</button></form>`;
  main.innerHTML = form + `<div id="ask-out">${q.trim() ? `<p class="muted">Thinking…</p>` : `<p class="empty">Ask a question; answers quote your notes with numbered sources.</p>`}</div>`;
  $("#ask-form").addEventListener("submit", (e) => {
    e.preventDefault();
    location.hash = "#/ask?q=" + encodeURIComponent($("#ask-q").value.trim());
  });
  if (!q.trim()) { $("#ask-q").focus(); return; }
  const r = await api(`/api/ask?q=${encodeURIComponent(q)}${mode ? "&mode=" + encodeURIComponent(mode) : ""}`);
  if (parseHash().params.get("q") !== q) return;   // user asked something else meanwhile
  $("#ask-out").innerHTML = answerHtml(r) + sourcesHtml(r, !r.known) + `<p class="muted">${esc(r.mode)} · ${r.ms} ms</p>`;
}

// Day 18: related notes — sidebar on desktop, section below the page on phones (CSS decides).
function relatedHtml(r) {
  if (!r || !r.related || !r.related.length) return "";
  return `<strong>Related notes</strong> <span class="muted">(${esc(r.method)})</span><ul>${r.related.map((it) =>
    `<li><a href="${pageHref(it.rel)}">${esc(it.title)}</a>${it.linked ? ' <span class="badge" title="linked">link</span>' : ""}
      <div class="muted crumb">${esc(it.rel)}</div></li>`).join("")}</ul>`;
}
async function loadRelated(rel) {
  let r;
  try { r = await api(`/api/related?rel=${encodeURIComponent(rel)}&k=6`); } catch (e) { return; }
  const box = document.getElementById("related");
  if (!box || box.dataset.for !== rel) return;   // user moved on to another page meanwhile
  const html = relatedHtml(r);
  if (html) { box.innerHTML = html; box.hidden = false; }
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

// ---- flashcard review (#/review) ----
const GRADES = [["0", "blank"], ["1", "wrong"], ["2", "almost"], ["3", "hard"], ["4", "good"], ["5", "easy"]];
const KIND_LABEL = { qa: "question", lead: "finding", cloze: "fill the gap", recall: "recall" };
// Escape first, then turn **bold** and `code` into tags (input is already escaped, so this is safe).
const inlineMd = (t) => esc(t).replace(/`([^`\n]+)`/g, "<code>$1</code>").replace(/\*\*(.+?)\*\*/g, "<b>$1</b>");
let session = null;   // { queue: [...cards], pos, shown, done: {grade: n}, today }

async function showReview() {
  document.title = "Review — Desk Brain";
  const r = await api("/api/review");
  session = { queue: r.due.concat(r.new), pos: 0, shown: false, done: {}, counts: r.counts, today: r.today };
  drawReview();
}

function drawReview() {
  const s = session, c = s.queue[s.pos];
  const head = `<p class="muted">${esc(s.today)} · ${s.counts.due} due · ${s.counts.new} new today ·
    ${s.counts.later} scheduled later · ${s.counts.total} cards` +
    (s.counts.retired ? ` · <span title="cards you studied that are gone from the desk (schedule kept)">${s.counts.retired} retired</span>` : "") +
    `</p>`;
  if (!c) {
    const n = Object.values(s.done).reduce((a, b) => a + b, 0);
    const lapses = (s.done[0] || 0) + (s.done[1] || 0) + (s.done[2] || 0);
    main.innerHTML = head + `<p class="empty">${n ? `Done: ${n} card${n === 1 ? "" : "s"} reviewed` +
      (lapses ? `, ${lapses} to see again tomorrow` : "") + "." : "Nothing to review today. 🎉"}</p>`;
    return;
  }
  const kind = c.review ? `review · every ${c.review.interval} d` : "new";
  main.innerHTML = head + `<div class="fc" data-id="${esc(c.id)}">
    <div class="muted">${s.pos + 1} / ${s.queue.length} · ${esc(KIND_LABEL[c.kind] || c.kind)} · ${esc(kind)}</div>
    <div class="fc-front">${inlineMd(c.front)}</div>
    ${s.shown ? `<div class="fc-back">${inlineMd(c.back)}</div>
      <div class="muted">from <a href="${pageHref(c.rel, c.anchor)}">${esc(c.page_title)}</a>${c.section ? " › " + esc(c.section) : ""}</div>
      <div class="fc-grades">${GRADES.map(([g, label]) =>
        `<button type="button" data-g="${g}" class="g${g}">${g} <small>${label}</small></button>`).join("")}</div>
      <p class="muted">Keys 0–5 grade · 0–2 = forgot (back tomorrow), 3–5 = remembered.</p>`
    : `<button type="button" id="fc-show">Show answer <small>(space)</small></button>`}</div>`;
  if (!s.shown) $("#fc-show").onclick = revealCard;
  else main.querySelectorAll(".fc-grades button").forEach((b) => { b.onclick = () => gradeCard(+b.dataset.g); });
}

function revealCard() { if (session && !session.shown) { session.shown = true; drawReview(); } }

async function gradeCard(g) {
  const s = session, c = s && s.queue[s.pos];
  if (!c || !s.shown || s.busy) return;
  s.busy = true;
  try {
    await api("/api/review/grade", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id: c.id, grade: g }) });
    s.done[g] = (s.done[g] || 0) + 1;
    s.pos += 1; s.shown = false;
    drawReview();
  } catch (e) { $("#status").textContent = "grading failed: " + e.message; }
  finally { s.busy = false; }
}

// ---- weekly digest (#/digest) ----
const addDays = (iso, n) => { const d = new Date(iso + "T00:00:00Z"); d.setUTCDate(d.getUTCDate() + n); return d.toISOString().slice(0, 10); };

function digestHtml(r) {
  const c = r.counts;
  const title = r.week ? `Week ${esc(r.week)}` : "Last 7 days";
  const nav = `<div class="graph-bar">
      <a href="#/digest?week=${addDays(r.start, -7)}">← previous week</a>
      <a href="#/digest">last 7 days</a>
      <a href="#/digest?week=${esc(r.today)}">this week</a>
      ${r.end < r.today ? `<a href="#/digest?week=${addDays(r.start, 7)}">next week →</a>` : ""}</div>`;
  const sec = (head, n, body, empty) => `<section class="dg-sec"><h3>${head} <span class="muted">${n}</span></h3>${
    n ? body : `<p class="empty dg-empty">${empty}</p>`}</section>`;
  const where = (x) => `<span class="muted">${esc(x.page_title)} · ${esc(x.rel)}:${x.line}</span>`;
  const answered = r.doubts.answered.map((d) => `<li><a href="${pageHref(d.rel, d.anchor)}">${esc(d.question)}</a>
      <span class="muted">answered ${esc(d.answered_day)}</span>${d.answer ? `<div class="dg-ans">${inlineMd(d.answer)}</div>` : ""}</li>`).join("");
  const asked = r.doubts.new.map((d) => `<li>${d.status === "open" ? "🔴" : "🟢"} <a href="${pageHref(d.rel, d.anchor)}">${esc(d.question)}</a>
      ${where(d)}${d.asked ? ` <span class="muted">· asked ${esc(d.asked)}</span>` : ""}</li>`).join("");
  const finds = r.findings.map((f) => `<li><span class="muted">${esc(f.date)}</span> ${inlineMd(f.text)}
      <div><a href="${pageHref(f.rel)}">${esc(f.page_title)}</a>${f.section ? ` <span class="muted">› ${esc(f.section)}</span>` : ""}</div></li>`).join("");
  const pages = r.pages.changed.map((p) => `<li><a href="${pageHref(p.rel)}">${esc(p.title)}</a>
      ${p.new ? `<span class="badge b-both">new</span>` : ""} <span class="muted">${esc(p.rel)} · ${esc(p.day)}</span></li>`).join("")
    + r.pages.removed.map((rel) => `<li class="muted"><s>${esc(rel)}</s> removed</li>`).join("");
  const open = r.doubts.open.map((d) => `<li><a href="${pageHref(d.rel, d.anchor)}">${esc(d.question)}</a>
      ${where(d)} <span class="age${d.age_days >= 14 ? " old" : ""}">${d.age_days == null ? "undated" : d.age_days + " d"}</span></li>`).join("");
  const track = r.tracking.complete ? "" : `<p class="muted">Answers and new pages are tracked from ${esc(r.tracking.since || "the next re-index")}
      (first index build); earlier changes only show through page dates.</p>`;
  return `<h2>${title} <span class="muted">${esc(r.start)} – ${esc(r.end)}</span></h2>${nav}
    <p class="dg-sum">${c.changed} pages changed (${c.new_pages} new${c.removed ? `, ${c.removed} removed` : ""}) ·
      ${c.new_doubts} new doubts · ${c.answered} answered · ${c.findings} findings · <b>${c.open} still open</b></p>${track}
    <div class="dg-grid">
      ${sec("🟢 Answered", c.answered, `<ul>${answered}</ul>`, "No doubts answered in this window.")}
      ${sec("❓ New doubts", c.new_doubts, `<ul>${asked}</ul>`, "No new doubts.")}
      ${sec("🔍 Findings", c.findings, `<ul>${finds}</ul>`, "No dated findings in this window.")}
      ${sec("📝 Pages changed", c.changed + c.removed, `<ul>${pages}</ul>`, "No pages changed.")}
    </div>
    ${sec("🔴 Still open", c.open, `<ul class="dg-open">${open}</ul>`, "No open doubts. 🎉")}`;
}

async function showDigest(week) {
  document.title = "Digest — Desk Brain";
  const r = await api(`/api/digest?week=${encodeURIComponent(week)}`);
  main.innerHTML = digestHtml(r);
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
    $("#reindex").classList.remove("stale");
    route();
  } catch (e) { $("#status").textContent = "re-index failed: " + e.message; }
});
function toggleHelp(show) {
  let box = $("#keys-help");
  if (show === false) { if (box) box.hidden = true; return; }
  if (!box) {
    box = document.createElement("div"); box.id = "keys-help"; box.className = "keys-help"; box.hidden = true;
    box.setAttribute("role", "dialog"); box.setAttribute("aria-label", "Keyboard shortcuts");
    box.innerHTML = `<h3>Keyboard shortcuts</h3><dl>${KEYS_HELP.map(([k, v]) => `<dt><kbd>${esc(k)}</kbd></dt><dd>${esc(v)}</dd>`).join("")}</dl>`;
    box.addEventListener("click", () => { box.hidden = true; });
    document.body.appendChild(box);
  }
  box.hidden = !box.hidden;
}
document.addEventListener("keydown", (e) => {
  const el = document.activeElement;
  const typing = /^(INPUT|SELECT|TEXTAREA)$/.test(el.tagName) || el.isContentEditable;
  const a = keyAction({ key: e.key, ctrl: e.ctrlKey, meta: e.metaKey, alt: e.altKey, typing,
    path: parseHash().path, reviewing: !!session, shown: !!(session && session.shown) });
  if (!a) return;
  e.preventDefault();
  if (a.blur) el.blur();
  else if (a.focus) $(a.focus).focus();
  else if ("help" in a) toggleHelp(a.help === "toggle" ? undefined : false);
  else if (a.go) { toggleHelp(false); location.hash = a.go; }
  else if (a.review === "reveal") revealCard();
  else if (typeof a.review === "number") gradeCard(a.review);
});
// Index status: poll cheaply (stat-only on the server) and nudge when the desk changed.
function ago(ts) {
  const s = Math.max(0, Date.now() / 1000 - ts);
  return s < 60 ? "just now" : s < 3600 ? `${Math.round(s / 60)} min ago` : s < 86400 ? `${Math.round(s / 3600)} h ago` : `${Math.round(s / 86400)} d ago`;
}
function statusText(s) {
  const when = s.last_update ? `indexed ${ago(s.last_update)}` : "not indexed";
  const p = s.pending, n = p.added + p.changed + p.removed;
  return s.stale ? `${when} · ${n} page${n === 1 ? "" : "s"} changed on desk` : `${when} · up to date`;
}
async function pollStatus() {
  if (document.hidden || $("#status").textContent === "re-indexing…") return;
  try {
    const s = await api("/api/index/status");
    $("#status").textContent = statusText(s);
    $("#reindex").classList.toggle("stale", s.stale);
  } catch (e) { /* server gone: leave the last text */ }
}
setInterval(pollStatus, 30000);
document.addEventListener("visibilitychange", pollStatus);
window.addEventListener("hashchange", route);
$("#mode").value = localStorage.getItem("deskbrain.mode") || "hybrid";
loadHealth().catch(() => {}).finally(() => { route(); pollStatus(); });
