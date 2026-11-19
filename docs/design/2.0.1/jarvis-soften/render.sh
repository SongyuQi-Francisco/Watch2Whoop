#!/bin/bash
# Renders the boards (png/) and the films (mp4, 60 fps) of the three options. Needs Google Chrome, Node 22+ and ffmpeg.
# usage: render.sh [boards|films|all]
set -euo pipefail
cd "$(dirname "$0")"; mkdir -p png
WHAT="${1:-all}"
if [ "$WHAT" != films ]; then
  for b in a b c compare; do WAIT=4000 node record.mjs still "board.html?b=$b" "png/board-$b.png" auto; echo "png/board-$b.png"; done
  for o in before option-a option-b option-c; do node record.mjs still "$o.html?demo=1&still=1&t=1500" "png/$o-open.png"; done
fi
if [ "$WHAT" != boards ]; then
  for o in a b c; do
    F="$(mktemp -d)"; node record.mjs film "option-$o.html?" "$F" 21 60
    ffmpeg -loglevel error -y -framerate 60 -i "$F/%05d.png" -c:v libx264 -pix_fmt yuv420p -crf 20 -preset slow -movflags +faststart "option-$o.mp4"
    rm -rf "$F"; echo "option-$o.mp4"
  done
fi
