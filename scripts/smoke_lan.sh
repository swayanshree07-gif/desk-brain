#!/usr/bin/env bash
# LAN-mode smoke test: fixture desk, temp data dir, port 8799, all under `timeout`. Exit 1 on failure.
set -uo pipefail
cd "$(dirname "$0")/.."
PORT=8799
TMP=$(mktemp -d)
DESKBRAIN_DATA=$TMP timeout 40 ./run.sh --desk tests/fixtures/desk --no-semantic --db "$TMP/ix.sqlite" --port $PORT --lan \
  > "$TMP/log" 2>&1 &
PID=$!
trap 'kill $PID 2>/dev/null; wait $PID 2>/dev/null; rm -rf "$TMP"' EXIT
L=http://127.0.0.1:$PORT
for _ in $(seq 1 100); do curl -sf -o /dev/null $L/api/health && break; sleep 0.2; done
ADDR=$(grep -o 'LAN mode: http://[^/]*' "$TMP/log" | sed 's|LAN mode: http://||; s|:[0-9]*$||')
if [ -z "$ADDR" ]; then echo "FAIL no LAN address (log below)"; cat "$TMP/log"; exit 1; fi
TOKEN=$(cat "$TMP/lan-token")
N=http://$ADDR:$PORT
fail=0
check() { local name=$1 want=$2; shift 2; local got; got=$(curl -s -o /dev/null -w "%{http_code}" "$@")
  if [ "$got" = "$want" ]; then echo "ok   $name ($got)"; else echo "FAIL $name (got $got, want $want)"; fail=1; fi; }
echo "info LAN address $ADDR"
[ "$(stat -c %a "$TMP/lan-token")" = 600 ] && echo "ok   token file 0600" || { echo "FAIL token perms"; fail=1; }
check loopback-health   200 $L/api/health
check pair-loopback     200 $L/pair
curl -s $L/pair | grep -qF "token=$TOKEN" && echo "ok   pair page has link" || { echo "FAIL pair link"; fail=1; }
check lan-no-token      401 $N/api/health
check lan-bearer        200 -H "Authorization: Bearer $TOKEN" $N/api/health
check lan-wrong         401 -H "Authorization: Bearer nope$TOKEN" $N/api/health
check lan-query-303     303 "$N/?token=$TOKEN"
check lan-pair-hidden   404 -H "Authorization: Bearer $TOKEN" $N/pair
check lan-bad-host      400 -H "Host: evil.example" -H "Authorization: Bearer $TOKEN" $N/api/health
if ss -ltn | grep -q "0.0.0.0:$PORT\|\[::\]:$PORT"; then echo "FAIL wildcard listen"; fail=1; else echo "ok   no wildcard socket"; fi
exit $fail
