#!/usr/bin/env bash
# docker compose wrapper for SparkyFitness: compose.yaml + secrets/sparkyfitness.env.
# Usage (from any directory): deploy/sparkyfitness/sf.sh up -d / ps / logs -f sparkyfitness-server / down
# The secrets directory defaults to secrets/ at the repo root; override with BODY_SECRETS_DIR (e.g. when running in a git worktree).
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SECRETS_DIR="${BODY_SECRETS_DIR:-$HERE/../../secrets}"
ENV_FILE="$SECRETS_DIR/sparkyfitness.env"
if [[ ! -f "$ENV_FILE" ]]; then
  echo "缺少 $ENV_FILE，先运行：python3 deploy/sparkyfitness/init-secrets.py" >&2
  exit 1
fi
exec docker compose -p sparkyfitness --env-file "$ENV_FILE" -f "$HERE/compose.yaml" "$@"
