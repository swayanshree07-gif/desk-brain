# Desk Brain

A local web app (in progress, 30-day project) that runs on top of the Markdown knowledge base
in `~/Personal-Desk`. Planned features: keyword + semantic search, a knowledge-graph view,
a doubts board (`### 🔴 Q:` open / `### 🟢 Q:` answered), auto-built flashcards with spaced
repetition, and a weekly digest. It will serve on `127.0.0.1:8765` only, started with `./run.sh`.

**Privacy rules:** the desk is read-only to this app. `activity/`, `Personal/`, `tools/`,
`_templates/` (and any `_template/` folder or hidden folder) are never indexed or shown.
Index data lives in `data/` (git-ignored); tests use the made-up desk in `tests/fixtures/desk/`.

## Run
```bash
./run.sh                      # creates .venv + installs deps on first run, then serves http://127.0.0.1:8765
./run.sh --desk tests/fixtures/desk    # against the made-up sample desk
```
The server indexes the desk incrementally on start (and when you press **Re-index**).
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
.venv/bin/python -m deskbrain search "sourdough starter"   # BM25 keyword search (-k N, --all-sections)
.venv/bin/python -m deskbrain embed     # embed new/changed sections (downloads the ~90 MB model to data/models once)
.venv/bin/python -m deskbrain --desk tests/fixtures/desk scan   # any command against the sample desk
```
The desk path defaults to `~/Personal-Desk`; override with `DESK_PATH` or `--desk`.

## API
- `GET /api/health` — desk name and index counts
- `GET /api/search?q=…&k=20&all=false` — keyword hits with page, section, line and highlighted snippet
- `GET /api/page?rel=…` — rendered page (no raw HTML; links rewritten to in-app routes), headings, links, backlinks
- `POST /api/reindex` — incremental re-index

## Checks
```bash
.venv/bin/pytest -q                      # all tests (embedding tests skip until the model is downloaded)
scripts/smoke_server.sh                  # starts ./run.sh under timeout, curls endpoints, stops it
.venv/bin/python scripts/check_pages.py  # renders every page; fails on unsafe hrefs / private paths
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
- `deskbrain/render.py` — safe Markdown → HTML for the page view
- `deskbrain/app.py` — FastAPI app; `deskbrain/static/` — plain HTML/JS/CSS frontend
- `tests/` — pytest suite and the fixture desk; `scripts/` — scripted checks

## Status
Day 4 of 30 (ahead of plan, through planned Day 10): indexing, BM25 search, web app with search and
read-only page view (`./run.sh`), privacy/safety tests, and section embeddings (108 tests).
Semantic search is not wired into the API/UI yet. Next: semantic + hybrid search (planned Days 11–12).
See `PLAN.md` for the roadmap and `PROGRESS.md` for the daily log.
