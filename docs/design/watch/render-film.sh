#!/bin/bash
# Render one scripted film of film.html to a 60 fps mp4 and a 30 fps gif: one headless-Chrome screenshot per frame
# (?t=ms), then ffmpeg. usage: render-film.sh sets|meal <length_ms> <out_dir> [jobs]
set -euo pipefail
CH="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"; HERE="$(cd "$(dirname "$0")" && pwd)"
if [ "$1" = "--frame" ]; then  # internal: one frame
  i="$2"; t=$((i*1000/60)); P="$TMP/p$i"
  "$CH" --headless=new --disable-gpu --hide-scrollbars --force-device-scale-factor=2 --window-size=520,660 --user-data-dir="$P" \
    --screenshot="$TMP/$(printf %05d "$i").png" "file://$HERE/film.html?film=$FILM&t=$t" >/dev/null 2>&1; rm -rf "$P"; exit 0
fi
FILM="$1"; LEN="$2"; OUT="$(cd "$3" && pwd)"; JOBS="${4:-6}"; TMP="$(mktemp -d)"; N=$((LEN*60/1000)); export TMP FILM
seq 0 $((N-1)) | xargs -P "$JOBS" -n 1 "$0" --frame
echo "frames: $(ls "$TMP"/*.png | wc -l) of $N"
ffmpeg -loglevel error -y -framerate 60 -i "$TMP/%05d.png" -c:v libx264 -pix_fmt yuv420p -crf 18 -movflags +faststart "$OUT/watch-$FILM.mp4"
ffmpeg -loglevel error -y -framerate 60 -i "$TMP/%05d.png" -vf "fps=30,scale=520:-1:flags=lanczos,split[a][b];[a]palettegen=max_colors=128[p];[b][p]paletteuse=dither=bayer:bayer_scale=4" "$OUT/watch-$FILM.gif"
rm -rf "$TMP"; ls -la "$OUT/watch-$FILM.mp4" "$OUT/watch-$FILM.gif"
