#!/usr/bin/env bash
# Watch2Whoop Quick Start — see quickstart/run.py or ./quickstart.sh --help.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
PY="$(command -v python3 || true)"
[[ -n "$PY" ]] || { echo "python3 is required" >&2; exit 1; }
exec "$PY" quickstart/run.py "$@"
