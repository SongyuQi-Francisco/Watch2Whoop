#!/usr/bin/env bash
# Run once after Open Wearables first starts (safe to re-run): create the S3 bucket; generate an OW API key for Body Engine
# and write it to secrets/open_wearables_api_key (not regenerated if it exists; the plaintext is never printed).
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
OW="$ROOT/deploy/open-wearables/ow.sh"
SCRIPT="$ROOT/deploy/open-wearables/bootstrap.py"
KEY_FILE="$ROOT/secrets/open_wearables_api_key"

"$OW" exec -T app python - bucket < "$SCRIPT"

if [[ -s "$KEY_FILE" ]]; then
  echo "API key 已存在：secrets/open_wearables_api_key"
else
  umask 077
  "$OW" exec -T app python - api-key body-engine < "$SCRIPT" > "$KEY_FILE.tmp"
  mv "$KEY_FILE.tmp" "$KEY_FILE"
  echo "API key 已写入：secrets/open_wearables_api_key"
fi
