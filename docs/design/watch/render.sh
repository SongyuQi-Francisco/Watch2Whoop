#!/bin/bash
# Render a watch mockup page to PNG with headless Chrome (macOS). The page sizes itself; the window is made large and the
# PNG is trimmed to the content by reading the body's size first.
# usage: render.sh "<file.html?query>" out.png [scale]
set -euo pipefail
SRC="$1"; Q=""; case "$SRC" in *\?*) Q="?${SRC#*\?}"; SRC="${SRC%%\?*}";; esac
ABS="$(cd "$(dirname "$SRC")" && pwd)/$(basename "$SRC")"; OUT="$2"; SC="${3:-2}"
CH="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
DIM=$("$CH" --headless=new --disable-gpu --virtual-time-budget=800 --window-size=4000,3000 --dump-dom "file://$ABS${Q:-?}${Q:+&}dim=1" 2>/dev/null | grep -o 'data-dim="[0-9]*x[0-9]*"' | head -1 | grep -o '[0-9]*x[0-9]*')
W="${DIM%x*}"; H="${DIM#*x}"
"$CH" --headless=new --disable-gpu --hide-scrollbars --force-device-scale-factor="$SC" --window-size="$W","$H" \
  --virtual-time-budget=800 --screenshot="$OUT" "file://$ABS$Q" >/dev/null 2>&1
sips -g pixelWidth -g pixelHeight "$OUT" | tail -2 | tr '\n' ' '; echo "-> $OUT"
