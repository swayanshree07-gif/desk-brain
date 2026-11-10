#!/usr/bin/env bash
# Start Desk Brain on http://127.0.0.1:8765 (loopback only).
# Usage: ./run.sh [--desk PATH] [--port N] [--no-semantic] [--lan [ADDR]]   (desk defaults to $DESK_PATH or ~/Personal-Desk)
# --lan (off by default): also serve on this machine's private LAN address, token required;
#        open http://127.0.0.1:PORT/pair on the laptop for the phone link.
set -euo pipefail
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then
  echo "Creating .venv…"
  python3 -m venv .venv
fi
if ! .venv/bin/python -c "import fastapi, uvicorn, markdown_it, fastembed" 2>/dev/null; then
  echo "Installing dependencies into .venv…"
  .venv/bin/pip install -q fastapi uvicorn markdown-it-py fastembed numpy pytest httpx
fi
exec .venv/bin/python -m deskbrain.app "$@"
