#!/usr/bin/env bash
# Start ./run.sh under `timeout`, curl the main endpoints, stop the server. Exit 1 on failure.
# Usage: scripts/smoke_server.sh [--desk PATH]
set -uo pipefail
cd "$(dirname "$0")/.."
URL=http://127.0.0.1:8765
timeout 30 ./run.sh "$@" > /tmp/deskbrain-smoke.log 2>&1 &
PID=$!
trap 'kill $PID 2>/dev/null; wait $PID 2>/dev/null' EXIT  # timeout forwards SIGTERM to the server
for _ in $(seq 1 50); do curl -sf -o /dev/null $URL/api/health && break; sleep 0.2; done
fail=0
check() { local name=$1 want=$2; shift 2; local got; got=$(curl -s -o /dev/null -w "%{http_code}" "$@")
  if [ "$got" = "$want" ]; then echo "ok   $name ($got)"; else echo "FAIL $name (got $got, want $want)"; fail=1; fi; }
check health        200 $URL/api/health
check search        200 "$URL/api/search?q=test"
check home          200 $URL/
check app.js        200 $URL/static/app.js
check private-page  404 "$URL/api/page?rel=Personal/x.md"
check traversal     404 "$URL/api/page?rel=../CLAUDE.md"
check foreign-host  400 -H "Host: evil.example" $URL/api/health
if ss -ltn 2>/dev/null | grep -q "0.0.0.0:8765\|\[::\]:8765"; then echo "FAIL listening beyond loopback"; fail=1; else echo "ok   loopback only"; fi
exit $fail
