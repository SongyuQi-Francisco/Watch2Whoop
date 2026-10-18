#!/bin/bash
# One command to check all services: deploy/ops/doctor.sh (--notify sends a macOS notification on problems, --json is machine-readable)
exec "$(cd "$(dirname "$0")" && pwd)/ops.sh" doctor "$@"
