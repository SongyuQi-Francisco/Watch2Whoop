#!/usr/bin/env bash
# docker compose wrapper for Grafana. Usage (from any directory): deploy/grafana/gf.sh up -d / ps / logs -f grafana / down
# The secrets directory defaults to secrets/ at the repo root; override with BODY_SECRETS_DIR (e.g. when running in a git worktree).
# The external address (GF_SERVER_ROOT_URL) is the Tailscale HTTPS name of this Mac + 8445; falls back to http://localhost:8400/ if unavailable.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export BODY_SECRETS_DIR="${BODY_SECRETS_DIR:-$(cd "$HERE/../.." && pwd)/secrets}"
for f in grafana_admin_password grafana_ro_body_password grafana_ro_ow_password sparkyfitness_api_key_owner_ro; do
  if [[ ! -f "$BODY_SECRETS_DIR/$f" ]]; then
    echo "缺少 $BODY_SECRETS_DIR/$f，先运行：python3 deploy/grafana/bootstrap.py" >&2
    exit 1
  fi
done
if [[ -z "${GRAFANA_ROOT_URL:-}" ]]; then
  TS="$(command -v tailscale || echo /Applications/Tailscale.app/Contents/MacOS/Tailscale)"
  NAME="$("$TS" status --json 2>/dev/null | /usr/bin/python3 -c 'import sys,json; print(json.load(sys.stdin)["Self"]["DNSName"].rstrip("."))' 2>/dev/null || true)"
  export GRAFANA_ROOT_URL="${NAME:+https://$NAME:8445/}"
fi
exec docker compose -p grafana -f "$HERE/compose.yaml" "$@"
