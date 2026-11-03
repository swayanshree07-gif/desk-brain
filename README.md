# Desk Brain

A local web app (in progress, 30-day project) that runs on top of the Markdown knowledge base
in `~/Personal-Desk`. Planned features: keyword + semantic search, a knowledge-graph view,
a doubts board (`### 🔴 Q:` open / `### 🟢 Q:` answered), auto-built flashcards with spaced
repetition, and a weekly digest. It will serve on `127.0.0.1:8765` only, started with `./run.sh`.

**Privacy rules:** the desk is read-only to this app. `activity/`, `Personal/`, `tools/`,
`_templates/` (and any `_template/` folder or hidden folder) are never indexed or shown.
Index data lives in `data/` (git-ignored); tests use the made-up desk in `tests/fixtures/desk/`.

## Setup
```bash
python3 -m venv .venv
.venv/bin/pip install pytest
```

## Use (current)
```bash
.venv/bin/python -m deskbrain scan      # list indexable pages with heading/link/doubt/finding counts
.venv/bin/python -m deskbrain doubts    # list all doubts, open first
.venv/bin/python -m deskbrain links     # resolve all links; summary by status + bad links
.venv/bin/python -m deskbrain links --broken   # only broken / bad-anchor / outside links
.venv/bin/python -m deskbrain chunks    # sections per page (the units search will use)
.venv/bin/python -m deskbrain index     # build / incrementally update data/index.sqlite (-v lists changed pages)
.venv/bin/python -m deskbrain search "sourdough starter"   # BM25 keyword search with highlighted snippets (-k N)
.venv/bin/python -m deskbrain --desk tests/fixtures/desk scan   # against the sample desk
.venv/bin/pytest -q                     # run tests
```
The desk path defaults to `~/Personal-Desk`; override with `DESK_PATH` or `--desk`.

## Layout
- `deskbrain/config.py` — paths and exclusion lists
- `deskbrain/scanner.py` — walks the desk (no symlinks, excluded dirs pruned), guarded `read_page`
- `deskbrain/parser.py` — title, headings, links, doubts, dated findings; ignores HTML comments and code fences
- `deskbrain/links.py` — link resolver: relative links, folder → README, `#anchors` (GitHub slugs), `[[wikilinks]]`, assets; broken-link list; links into private areas are marked `excluded` and never followed
- `deskbrain/chunker.py` — splits pages into heading-based sections with stable ids, line ranges and breadcrumbs
- `deskbrain/store.py` — SQLite index in `data/` (pages, headings, chunks, links, doubts, findings) with incremental re-index by mtime/size + content hash
- `deskbrain/bm25.py` — tokenizer + light stemmer, BM25 ranking over chunks, highlighted snippets
- `tests/` — pytest suite and the fixture desk

## Status
Day 3 of 30 (ahead of plan, through planned Day 6): scanner, parser, link resolver, chunker,
SQLite index with incremental updates, and BM25 keyword search from the CLI, all tested (79 tests).
Next: FastAPI app on 127.0.0.1:8765 with `/api/search` and `./run.sh`, then the HTML frontend. See `PLAN.md` for the roadmap and `PROGRESS.md` for the daily log.
