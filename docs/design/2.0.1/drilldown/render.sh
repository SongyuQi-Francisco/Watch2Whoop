#!/bin/bash
# Render the drill-down mockups to PNG with headless Chrome (macOS). Run from anywhere:
#   bash docs/design/2.0.1/drilldown/render.sh            every screen and board into ./png
#   bash docs/design/2.0.1/drilldown/render.sh a-today    only the files whose name starts with the argument
# Screens: 402 x 874 pt @3x (the kit's standard), plus the first screens at 375 x 667 (?size=se) and in the light theme.
set -euo pipefail
cd "$(dirname "$0")"
CH="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
OUT="${DD_OUT:-png}"; mkdir -p "$OUT"; ONLY="${1:-}"
shot(){ # <page[?query]> <out name> <width pt> <height pt> <scale>
  case "$2" in "$ONLY"*) ;; *) return 0;; esac
  "$CH" --headless=new --disable-gpu --hide-scrollbars --force-device-scale-factor="$5" --window-size="$3","$4" \
    --virtual-time-budget=4000 --screenshot="$OUT/$2.png" "file://$PWD/$1" >/dev/null 2>&1
  echo "$OUT/$2.png"
}
for o in a b c; do
  for s in today body day metric; do shot "$o-$s.html" "$o-$s-dark" 402 874 3; done
  for s in today body; do
    shot "$o-$s.html?theme=light" "$o-$s-light" 402 874 3
    shot "$o-$s.html?size=se" "$o-$s-se-dark" 375 667 3
  done
done
shot "c-today.html?opt=ladder,bare" "c-today-cprime-dark" 402 874 3   # variant C': chevrons on the dial and the readings
shot "c-body.html?opt=ladder,bare" "c-body-cprime-dark" 402 874 3
for o in a b c; do shot "$o-board.html" "$o-board" 1840 1010 2; done
shot "compare.html" "compare" 1840 1640 2
