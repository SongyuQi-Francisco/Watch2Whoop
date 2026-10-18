#!/usr/bin/env bash
# Download the speech recognition model for the asr container (once per machine; safe to re-run).
#   deploy/asr/fetch_model.sh            -> $BODY_ROOT/models/asr/sense-voice-2024-07-17/{model.int8.onnx,tokens.txt,LICENSE}
# SenseVoice-Small (FunAudioLLM, zh / en / ja / ko / yue, punctuation + inverse text normalisation), int8 ONNX exported by
# the sherpa-onnx project. Pinned to a Hugging Face revision and checked by sha256. About 240 MB on disk.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BODY_ROOT="${BODY_ROOT:-$(cd "$HERE/../.." && pwd)}"
DEST="$BODY_ROOT/models/asr/sense-voice-2024-07-17"
REPO="https://huggingface.co/csukuangfj/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17/resolve/2365baeacb507f821a0c8120fcee3d484dba7a07"
declare -A SHA=(
  [model.int8.onnx]=c71f0ce00bec95b07744e116345e33d8cbbe08cef896382cf907bf4b51a2cd51
  [tokens.txt]=f449eb28dc567533d7fa59be34e2abca8784f771850c78a47fb731a31429a1dc
  [LICENSE]=
)
mkdir -p "$DEST"
for f in model.int8.onnx tokens.txt LICENSE; do
  if [[ -n "${SHA[$f]}" && -f "$DEST/$f" ]] && echo "${SHA[$f]}  $DEST/$f" | sha256sum -c --status; then
    echo "ok (already there): $f"; continue
  fi
  curl -fL --retry 3 -o "$DEST/$f.part" "$REPO/$f"
  if [[ -n "${SHA[$f]}" ]]; then echo "${SHA[$f]}  $DEST/$f.part" | sha256sum -c --quiet; fi
  mv "$DEST/$f.part" "$DEST/$f"
  echo "downloaded: $f"
done
chmod 755 "$BODY_ROOT/models" "$BODY_ROOT/models/asr" "$DEST"; chmod 644 "$DEST"/*
du -sh "$DEST"
