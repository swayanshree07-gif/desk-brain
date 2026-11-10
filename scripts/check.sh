#!/usr/bin/env bash
# One-command check: unit tests, page-render safety check, server smoke test, LAN smoke test.
# Usage: scripts/check.sh [--real]   (default uses the made-up fixture desk; --real uses ~/Personal-Desk, read-only)
# Every server is started under `timeout` by the smoke scripts and stopped afterwards.
set -uo pipefail
cd "$(dirname "$0")/.."
DESK=tests/fixtures/desk
[ "${1:-}" = "--real" ] && DESK="${DESK_PATH:-$HOME/Personal-Desk}"
[ -x .venv/bin/python ] || { echo "no .venv yet: run ./run.sh once (or see README 'Setup by hand')"; exit 1; }
if [ "$DESK" = tests/fixtures/desk ]; then
  # keep the real index and flashcard state in data/ untouched: fixture runs use a scratch data dir
  export DESKBRAIN_DATA="$PWD/build/check-data"; mkdir -p "$DESKBRAIN_DATA"
  [ -e "$DESKBRAIN_DATA/models" ] || ln -s "$PWD/data/models" "$DESKBRAIN_DATA/models"
fi
fail=0
step() { local name=$1; shift; echo "== $name"; if "$@"; then echo "-- ok   $name"; else echo "-- FAIL $name"; fail=1; fi; }
step pytest      .venv/bin/pytest -q
step check_pages env DESK_PATH="$DESK" .venv/bin/python scripts/check_pages.py
step smoke       timeout 180 scripts/smoke_server.sh --desk "$DESK"
step smoke_lan   timeout 90 scripts/smoke_lan.sh
for p in 8765 8799; do
  if (exec 3<>/dev/tcp/127.0.0.1/$p) 2>/dev/null; then echo "FAIL port $p still in use"; fail=1; fi
done
[ $fail = 0 ] && echo "ALL CHECKS PASSED" || echo "SOME CHECKS FAILED"
exit $fail
