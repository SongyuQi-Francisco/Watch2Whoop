#!/bin/bash
# Ops entry point: deploy/ops/ops.sh <hourly|nightly|backup|restore-drill|restore-extract|doctor|start-all>
# Uses the system python3 (standard library only), independent of the Body Engine virtualenv.
exec /usr/bin/python3 "$(cd "$(dirname "$0")" && pwd)/ops.py" "$@"
