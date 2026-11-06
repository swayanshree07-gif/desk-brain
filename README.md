# Desk Brain

A local web app (in progress, 30-day project) that runs on top of the Markdown knowledge base
in `~/Personal-Desk`. Working now: keyword + semantic (meaning) + hybrid search, a read-only
page view, a knowledge-graph view of pages and links, and a doubts board (`### 🔴 Q:` open /
`### 🟢 Q:` answered), and flashcards built automatically from answered doubts and dated
findings (API + CLI so far). Planned: spaced-repetition review UI, and a weekly digest. It will serve on `127.0.0.1:8765` only, started with `./run.sh`.

**Privacy rules:** the desk is read-only to this app. `activity/`, `Personal/`, `tools/`,
`_templates/` (and any `_template/` folder or hidden folder) are never indexed or shown.
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
- `GET /api/page?rel=…` — rendered page (no raw HTML; links rewritten to in-app routes), headings, links, backlinks
- `POST /api/reindex` — incremental re-index

## UI
- `#/search?q=…` — search results; `#/page/<path>` — page view with backlinks
- `#/graph` — knowledge graph (canvas): colour = area, size = links, red ring = has bad links, dashed ring = orphan;
  area filter, folder/orphan toggles, highlight box; drag to pan, wheel to zoom, click a page to open it
- `#/doubts` — open / answered columns, area filter, search within doubts; each card links to the doubt in its page

## Checks
```bash
.venv/bin/pytest -q                      # all tests (embedding tests skip until the model is downloaded)
scripts/smoke_server.sh                  # starts ./run.sh under timeout, curls endpoints, stops it
.venv/bin/python scripts/check_pages.py  # renders every page; fails on unsafe hrefs / private paths
.venv/bin/python scripts/ui_preview.py data/x.html /api/health /api/graph   # snapshot page for headless screenshots
```

## Layout
- `deskbrain/config.py` — paths and exclusion lists
- `deskbrain/scanner.py` — walks the desk (no symlinks, excluded dirs pruned), guarded `read_page`
- `deskbrain/parser.py` — title, headings, links, doubts, dated findings; ignores HTML comments and code fences
- `deskbrain/links.py` — link resolver (relative, folder → README, `#anchors`, `[[wikilinks]]`, assets); links into private areas are `excluded` and never followed
- `deskbrain/chunker.py` — heading-based sections with stable ids, line ranges and breadcrumbs
- `deskbrain/store.py` — SQLite index in `data/` with incremental re-index by mtime/size + content hash
- `deskbrain/bm25.py` — tokenizer + light stemmer, BM25 ranking, highlighted snippets
- `deskbrain/embed.py` — fastembed MiniLM embeddings, vector store that only embeds changed sections
- `deskbrain/search.py` — keyword / semantic / hybrid (reciprocal-rank fusion) search with gibberish cut-offs
- `deskbrain/graph.py` — knowledge-graph nodes and edges from the index
- `deskbrain/doubts.py` — doubts board data (stable ids, filters, heading anchors)
- `deskbrain/cards.py` — flashcard generation (Q→A from 🟢 doubts; lead / cloze / recall cards from findings)
- `deskbrain/render.py` — safe Markdown → HTML for the page view
- `deskbrain/app.py` — FastAPI app; `deskbrain/static/` — plain HTML/JS/CSS frontend (`graph.js` = graph view)
- `tests/` — pytest suite and the fixture desk; `scripts/` — scripted checks

## Status
Day 7 of 30 (ahead of plan, through planned Day 19): indexing, keyword/semantic/hybrid search,
read-only page view (`./run.sh`), privacy/safety tests, incremental embeddings, the graph view
(area filter, orphan and bad-link highlighting), the doubts board (API + UI) and flashcard
generation (API + CLI, no review UI yet); 167 tests.
Next: spaced repetition (SM-2) state in `data/flashcards.sqlite` and a due queue (planned Day 20).
See `PLAN.md` for the roadmap and `PROGRESS.md` for the daily log.
