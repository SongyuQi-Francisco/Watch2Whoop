#!/usr/bin/env bash
# Copies the Flutter app's native sync files into the SwiftUI app, byte for byte (#340 N0, docs/ios-native-app.md 2.3),
# and the HealthKit type registry (assets/healthkit_types.json, the server's rawdata/hk_types.engine_json(); N8).
# While A2–A4 (#219) still change them in apps/watch2whoop, the native app takes them as they are:
#   apps/ios/scripts/sync_flutter_sources.sh          copy
#   apps/ios/scripts/sync_flutter_sources.sh --check  fail if a copy differs (CI)
# Never edit the copies in apps/ios: change the originals, then copy. Ends once RawSync.swift has a native API too
# (SyncEngine.swift and Backfill.swift have one since #344) and the FlutterShim package is removed.
set -euo pipefail
here="$(cd "$(dirname "$0")/.." && pwd)"
src="$here/../watch2whoop/ios/Runner"
dst="$here/Watch2Whoop/HealthSync"
files=(SyncEngine.swift RawSync.swift Backfill.swift)
registry_src="$here/../watch2whoop/assets/healthkit_types.json"
registry_dst="$here/Watch2Whoop/Resources/healthkit_types.json"
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
if [ "$check" = 1 ]; then
  if ! cmp -s "$registry_src" "$registry_dst"; then
    echo "differs: apps/ios/Watch2Whoop/Resources/healthkit_types.json (run apps/ios/scripts/sync_flutter_sources.sh)"
    status=1
  fi
else
  cp "$registry_src" "$registry_dst"
  echo "copied healthkit_types.json"
fi
exit "$status"
