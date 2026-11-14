#!/bin/bash
# Render a mockup HTML file to PNG at iPhone 17 Pro size (402x874 pt, @3x = 1206x2622 px).
# usage: render.sh <file.html> [out.png] [height_pt]   (theme via ?theme=dark|light in the URL: pass "file.html?theme=light")
set -euo pipefail
SRC="$1"; Q=""; case "$SRC" in *\?*) Q="?${SRC#*\?}"; SRC="${SRC%%\?*}";; esac
ABS="$(cd "$(dirname "$SRC")" && pwd)/$(basename "$SRC")"
OUT="${2:-${ABS%.html}.png}"; H="${3:-874}"
"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" --headless=new --disable-gpu --hide-scrollbars \
  --force-device-scale-factor=3 --window-size=402,"$H" --virtual-time-budget=1500 \
  --screenshot="$OUT" "file://$ABS$Q" >/dev/null 2>&1
sips -g pixelWidth -g pixelHeight "$OUT" | tail -2 | tr '\n' ' '; echo "-> $OUT"
