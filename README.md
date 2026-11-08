# Desk Brain

A local web app (in progress, 30-day project) that runs on top of the Markdown knowledge base
in `~/Personal-Desk`. Working now: keyword + semantic (meaning) + hybrid search, a read-only
page view, a knowledge-graph view of pages and links, and a doubts board (`### 🔴 Q:` open /
`### 🟢 Q:` answered), and flashcards built automatically from answered doubts and dated
findings, reviewed with SM-2 spaced repetition in the browser (edited cards keep their
schedule, deleted ones are retired), and a weekly digest (what changed, what was answered,
what is still open). It serves on `127.0.0.1:8765` only, started with `./run.sh`.

**Privacy rules:** the desk is read-only to this app. `activity/`, `Personal/`, `tools/`,
`_templates/` (and any `_template/` folder or hidden folder) are never indexed or shown; these
names are matched case-insensitively, and symlinks are never followed or read.
Index data lives in `data/` (git-ignored); tests use the made-up desk in `tests/fixtures/desk/`.

## Run
```bash
./run.sh                      # creates .venv + installs deps on first run, then serves http://127.0.0.1:8765
./run.sh --desk tests/fixtures/desk    # against the made-up sample desk
```
The server indexes the desk incrementally on start (and when you press **Re-index**), and embeds
new/changed sections if the embedding model is in `data/models/` (first full embed of the real desk
takes ~10 s; afterwards only edited sections). `./run.sh --no-semantic` gives keyword search only.
Without a downloaded model the app falls back to keyword search; run `deskbrain embed` once to fetch it.
It listens on 127.0.0.1 only and rejects requests with a foreign `Host` header.

## Setup by hand
```bash
python3 -m venv .venv
.venv/bin/pip install fastapi uvicorn markdown-it-py fastembed numpy pytest httpx
```

## CLI
```bash
.venv/bin/python -m deskbrain scan      # list indexable pages with heading/link/doubt/finding counts
.venv/bin/python -m deskbrain doubts    # list all doubts, open first
.venv/bin/python -m deskbrain links [--broken]   # resolve all links; summary + bad links
.venv/bin/python -m deskbrain chunks    # sections per page (the units search uses)
.venv/bin/python -m deskbrain index [-v]         # build / incrementally update data/index.sqlite
.venv/bin/python -m deskbrain search "sourdough starter"   # hybrid search (-k N, --all-sections, --mode keyword|semantic|hybrid)
.venv/bin/python -m deskbrain cards [--kind qa|lead|cloze|recall] [--show]   # flashcards from answered doubts + findings
.venv/bin/python -m deskbrain review [--new 20]    # today's study queue (due + new cards); state in data/flashcards.sqlite
.venv/bin/python -m deskbrain digest [--week 2026-W45]   # weekly digest (default: last 7 days; a date picks its ISO week)
.venv/bin/python -m deskbrain embed     # embed new/changed sections into data/index-vectors.* (downloads the ~90 MB model to data/models once)
.venv/bin/python -m deskbrain --desk tests/fixtures/desk scan   # any command against the sample desk
```
The desk path defaults to `~/Personal-Desk`; override with `DESK_PATH` or `--desk`.

## API
- `GET /api/health` — desk name, index counts, `semantic` (model available) and `default_mode`
- `GET /api/search?q=…&k=20&all=false&mode=hybrid|keyword|semantic` — section hits with page, section, line,
  highlighted snippet, `match` (keyword / semantic / both), BM25 score and cosine similarity
- `GET /api/graph?folders=true` — page + folder nodes (degree, doubts, findings, bad links, orphan flag), deduplicated link edges, folder edges, broken-link list
- `GET /api/doubts?status=all|open|answered&area=…&q=…` — doubts board: question, status, asked, context, answer, page, line, heading `anchor`, stable `id`; plus whole-desk counts and areas
- `GET /api/cards?kind=all|qa|lead|cloze|recall&area=…` — flashcards: stable `id`, `kind`, `front`, `back`, page, line, anchor, content `hash`; plus counts per kind and areas
- `GET /api/review?new=20` — today's queue: `due` cards (with their SM-2 `review` state, most overdue first) and up to `new` unseen cards per day; plus counts
- `POST /api/review/grade` with JSON `{"id": card id, "grade": 0–5}` — records a review, returns the new schedule (404 for unknown cards)
- `GET /api/review/retired` — studied cards that are gone from the desk (id, page, kind, schedule; their text is not kept)
- `GET /api/review/card?id=` — one live card's schedule, grade log and lifecycle events (edited / renamed / retired / revived)
- `GET /api/page?rel=…` — rendered page (no raw HTML; links rewritten to in-app routes), headings, links, backlinks
- `GET /api/digest?week=2026-W45` — weekly digest (`week` = ISO week or a date in it; empty = last 7 days): changed / new / removed pages,
  new doubts, doubts answered in the window, dated findings, every still-open doubt with its age; `tracking.since` = when change tracking began
- `GET /api/index/status` — last re-index time, counts, pages changed on the desk since (stat-only; the UI polls it every 30 s and outlines the Re-index button when stale)
- `GET /api/related?rel=&k=5` — pages most like this one (mean section embeddings, or TF-IDF without the model, plus a small bonus for direct/shared links); never the page itself or private pages
- `GET /api/recent?limit=10` — most recently changed pages (rel, title, mtime), newest first; private areas never listed
- `POST /api/reindex` — incremental re-index

## UI
- `#/` — home: recently changed notes and open doubts; phone-friendly (header wraps, full-width search, ≥ 40px tap targets at 375px)
- `#/search?q=…` — search results; `#/page/<path>` — page view with backlinks
- `#/graph` — knowledge graph (canvas): colour = area, size = links, red ring = has bad links, dashed ring = orphan;
  area filter, folder/orphan toggles, highlight box; drag to pan, wheel to zoom, click a page to open it
- `#/doubts` — open / answered columns, area filter, search within doubts; each card links to the doubt in its page
- `#/digest?week=…` — weekly digest: answered, new doubts, findings, pages changed, still open (oldest first); previous/next week links
- `#/review` — flashcard review: front → **Show answer** (space) → grade 0–5 (keys 0–5; 0–2 = forgot, back tomorrow), link to the source section

## Checks
```bash
.venv/bin/pytest -q                      # all tests (embedding tests skip until the model is downloaded)
scripts/smoke_server.sh                  # starts ./run.sh under timeout, curls endpoints, stops it
.venv/bin/python scripts/check_pages.py  # renders every page; fails on unsafe hrefs / private paths
.venv/bin/python scripts/perf.py         # index time, query latency per mode, disk use; exit 1 over limits
.venv/bin/python scripts/ui_preview.py data/x.html /api/health /api/graph   # snapshot page for headless screenshots (--after JS to press keys)
```

## Layout
- `deskbrain/config.py` — paths and exclusion lists
- `deskbrain/scanner.py` — walks the desk (no symlinks, excluded dirs pruned), guarded `read_page`
- `deskbrain/parser.py` — title, headings, links, doubts, dated findings; ignores HTML comments and code fences
- `deskbrain/links.py` — link resolver (relative, folder → README, `#anchors`, `[[wikilinks]]`, assets); links into private areas are `excluded` and never followed
- `deskbrain/chunker.py` — heading-based sections with stable ids, line ranges and breadcrumbs
- `deskbrain/store.py` — SQLite index in `data/` with incremental re-index by mtime/size + content hash, and a change-event log (ids only) for the digest
- `deskbrain/bm25.py` — tokenizer + light stemmer, BM25 ranking, highlighted snippets
- `deskbrain/embed.py` — fastembed MiniLM embeddings, vector store that only embeds changed sections
- `deskbrain/search.py` — keyword / semantic / hybrid (reciprocal-rank fusion) search with gibberish cut-offs
- `deskbrain/graph.py` — knowledge-graph nodes and edges from the index
- `deskbrain/doubts.py` — doubts board data (stable ids, filters, heading anchors)
- `deskbrain/cards.py` — flashcard generation (Q→A from 🟢 doubts; lead / cloze / recall cards from findings)
- `deskbrain/review.py` — SM-2 scheduling, review state + log in `data/flashcards.sqlite`, due queue, card lifecycle (`reconcile`)
- `deskbrain/digest.py` — weekly digest from page mtimes, dated findings/doubts and the index's change `events`
- `deskbrain/render.py` — safe Markdown → HTML for the page view
- `deskbrain/app.py` — FastAPI app; `deskbrain/static/` — plain HTML/JS/CSS frontend (`graph.js` = graph view)
- `tests/` — pytest suite and the fixture desk; `scripts/` — scripted checks

## Status
Day 17 of 30. The original plan was finished early, so Days 15–27 were re-planned (see `PLAN.md`) around a phone view, related notes, 'Ask the desk' with citations and an optional token-protected LAN mode. Built so far: indexing, keyword/semantic/hybrid search,
read-only page view (`./run.sh`), safety layer (path/symlink guards, privacy tests over every
endpoint and the raw `data/` files), incremental embeddings, the graph view (area filter, orphan
and bad-link highlighting), the doubts board (API + UI), flashcard generation, SM-2
spaced-repetition review (API, CLI and UI), the card lifecycle, and the weekly digest
(change-event log, API, CLI and UI page), and index status with a
stale-desk indicator, and a performance report script
(real desk, 58 pages: full index 0.07 s, full embed 11 s, hybrid query p95 2 ms, `data/` 89 MB), and robustness hardening (BOM, non-UTF-8,
NUL bytes, huge pages, unreadable files, fuzzed Markdown), plus a phone-friendly view at 375px (home with recent notes + open doubts via `/api/recent`; page tables/code scroll inside their box; larger tap targets on doubts, review and digest; the graph shows a most-linked-first page list on narrow screens), and a related-notes API (`/api/related`; UI comes on Day 18); 289 tests.
Note: "answered this week" and "new page" need change tracking, which starts at the first index
build; earlier weeks show only what page dates and `Asked`/finding dates reveal.
Next: related notes (17–18), Ask the desk (19–22), LAN mode (23–25); polish, docs and demo on Days 28–30.
See `PLAN.md` for the roadmap and `PROGRESS.md` for the daily log.
