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
- `tests/` — pytest suite and the fixture desk

## Status
Day 2 of 30: scanner, parser, link resolver and chunker done and tested (48 tests).
Next: SQLite store with incremental re-indexing, then BM25 search. See `PLAN.md` for the roadmap and `PROGRESS.md` for the daily log.
