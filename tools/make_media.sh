#!/usr/bin/env bash
# Regenerate docs/media/ screenshots + GIF from the made-up fixture desk (never the real desk).
# Uses scripts/ui_preview.py (real app.js + real API responses) + headless Firefox + ffmpeg.
set -euo pipefail
cd "$(dirname "$0")/.."
D=tests/fixtures/desk; T=data/tmp/media; M=docs/media; mkdir -p "$T/ff" "$M"
shot() { # name width height hash urls...
  local n=$1 w=$2 h=$3 hash=$4; shift 4
  timeout 120 .venv/bin/python scripts/ui_preview.py --desk "$D" "$T/$n.html" "$@" >/dev/null
  timeout 60 firefox --headless --no-remote --profile "$T/ff" --window-size=$w,$h \
    --screenshot "$PWD/$T/$n.png" "file://$PWD/$T/$n.html#$hash" >/dev/null 2>&1
  timeout 60 ffmpeg -loglevel error -y -i "$T/$n.png" -vf "scale='min(1000,iw)':-1" "$M/$n.png"
}
H=(/api/health); HOME_U=("${H[@]}" "/api/recent?limit=8" "/api/doubts?status=open")
shot home 1280 820 / "${HOME_U[@]}"
shot search 1280 820 "/search?q=dough&mode=hybrid" "${H[@]}" "/api/search?q=dough&k=30&mode=hybrid"
shot ask 1280 820 "/ask?q=why%20does%20dough%20not%20rise" "${H[@]}" "/api/ask?q=why%20does%20dough%20not%20rise"
shot graph 1280 820 /graph "${H[@]}" /api/graph
shot doubts 1280 820 "/doubts" "${H[@]}" "/api/doubts?area=&q="
shot review 1280 820 /review "${H[@]}" /api/review
shot digest 1280 820 /digest "${H[@]}" "/api/digest?week="
shot phone 375 760 / "${HOME_U[@]}"
# tour GIF: each screen ~2 s
ls $M/{home,search,ask,graph,doubts,review,digest}.png | sed "s|^|file '$PWD/|;s|$|'\nduration 2|" > $T/list.txt
timeout 120 ffmpeg -loglevel error -y -f concat -safe 0 -i $T/list.txt -vf "scale=640:-2:flags=lanczos,pad=640:410:(ow-iw)/2:0:white,split[a][b];[a]palettegen[p];[b][p]paletteuse" -r 1 $M/tour.gif
rm -rf "$T"
timeout 300 .venv/bin/python scripts/perf.py tests/fixtures/desk --json > data/perf.json 2>/dev/null && timeout 60 .venv/bin/python tools/perf_chart.py data/perf.json docs/media/latency.png
