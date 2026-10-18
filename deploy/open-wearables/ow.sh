#!/usr/bin/env bash
# docker compose wrapper for Open Wearables: upstream compose + this repo override + config from secrets.
# Usage (from any directory): deploy/open-wearables/ow.sh up -d / ps / logs -f app / down
set -euo pipefail
BODY_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
export BODY_ROOT
ENV_FILE="$BODY_ROOT/secrets/open_wearables.env"
if [[ ! -f "$ENV_FILE" ]]; then
  echo "缺少 $ENV_FILE，先运行：python3 deploy/open-wearables/init-secrets.py" >&2
  exit 1
fi
exec docker compose \
  -p open-wearables \
  --project-directory "$BODY_ROOT/vendor/open-wearables" \
  --env-file "$ENV_FILE" \
  -f "$BODY_ROOT/vendor/open-wearables/docker-compose.yml" \
  -f "$BODY_ROOT/deploy/open-wearables/compose.override.yaml" \
  "$@"
