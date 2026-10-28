#!/usr/bin/env bash
# Copies the Flutter app's native sync files into the SwiftUI app, byte for byte (#340 N0, docs/ios-native-app.md 2.3).
# While A2–A4 (#219) still change them in apps/watch2whoop, the native app takes them as they are:
#   apps/ios/scripts/sync_flutter_sources.sh          copy
#   apps/ios/scripts/sync_flutter_sources.sh --check  fail if a copy differs (CI)
# Never edit the copies in apps/ios: change the originals, then copy. N8 ends this (native API, shim removed).
set -euo pipefail
here="$(cd "$(dirname "$0")/.." && pwd)"
src="$here/../watch2whoop/ios/Runner"
dst="$here/Watch2Whoop/HealthSync"
files=(SyncEngine.swift RawSync.swift Backfill.swift)
check=0
[ "${1:-}" = "--check" ] && check=1
status=0
for f in "${files[@]}"; do
  if [ "$check" = 1 ]; then
    if ! cmp -s "$src/$f" "$dst/$f"; then
      echo "differs: apps/ios/Watch2Whoop/HealthSync/$f (run apps/ios/scripts/sync_flutter_sources.sh)"
      status=1
    fi
  else
    cp "$src/$f" "$dst/$f"
    echo "copied $f"
  fi
done
exit "$status"
