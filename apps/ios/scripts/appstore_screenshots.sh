#!/usr/bin/env bash
# App Store screenshots from the iOS Simulator, with SYNTHETIC data only (docs/appstore/README.md).
#
#   apps/ios/scripts/appstore_screenshots.sh                      # 6.9" iPhone, light mode, Chinese + English
#   APP_NAME="<final name>" apps/ios/scripts/appstore_screenshots.sh
#   LOCALES=zh SCREENS="01-today 05-jarvis" apps/ios/scripts/appstore_screenshots.sh
#   WITH_65=1 apps/ios/scripts/appstore_screenshots.sh            # also a 6.5" set (resized from 6.9")
#
# What it does: builds the Debug app for the simulator (the launch switches below exist in Debug builds only), starts a
# throw-away Body Engine with made-up data (appstore_demo_server.py: its own Postgres container on port 55433, never a
# real server) and the fake Jarvis server (fake_jarvis_server.py), boots the simulator, and for every locale and screen
# launches the app signed in to the demo server (W2W_DEV_SERVER / W2W_DEV_TOKEN, README "模拟器自动登录"), waits for the
# first sync, and saves a PNG. The status bar is set to 9:41, full battery and signal. Output (git-ignored):
#   apps/ios/build/appstore-screenshots/<size>/<locale>/<NN-name>.png
# The app's name on the home screen / in screenshots is W2W_DISPLAY_NAME; APP_NAME overrides it for this build only.
#
# Sizes (assumed current App Store Connect rules): 6.9" (1320x2868, iPhone 17 Pro Max) is required for iPhone apps;
# 6.5" (1284x2778) is only needed if no 6.9" set is uploaded (Apple scales the 6.9" set down), so it is off by default and,
# with WITH_65=1, made by resizing, not from a 6.5" simulator (iOS 26 has none).
# Cleans up after itself: demo server and container, simulator status-bar override, derived data (KEEP_DD=1 keeps it).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
IOS="$ROOT/apps/ios"
export DEVELOPER_DIR="${DEVELOPER_DIR:-/Applications/Xcode.app/Contents/Developer}"
OUT="${OUT:-$IOS/build/appstore-screenshots}"
DD="${W2W_SHOTS_DD:-/tmp/w2w-appstore-dd}"
DEVICE="${DEVICE_69:-iPhone 17 Pro Max}"
LOCALES="${LOCALES:-zh en}"
BUNDLE="org.watch2whoop.app"
FAKE_PORT=18766
WAIT_FIRST="${WAIT_FIRST:-30}"
WAIT="${WAIT:-12}"

# name | extra launch environment (SIMCTL_CHILD_ prefix added here) | server: real or fake (Jarvis)
SCREEN_TABLE=(
  "01-today|W2W_DEV_SKIP_HEALTH=1|real"
  "02-sleep|W2W_DEV_SKIP_HEALTH=1 W2W_DEV_SLEEP=1|real"
  "03-trends|W2W_DEV_SKIP_HEALTH=1 W2W_DEV_TAB=trends|real"
  "04-training|W2W_DEV_SKIP_HEALTH=1 W2W_DEV_TAB=record W2W_DEV_RECORD=training|real"
  "05-jarvis|W2W_DEV_SKIP_HEALTH=1 W2W_JARVIS_DEMO=chat|fake"
  "06-circles|W2W_DEV_SKIP_HEALTH=1 W2W_DEV_TAB=circles W2W_DEV_CIRCLES=feed:@CID|real"
  "07-leaderboard|W2W_DEV_SKIP_HEALTH=1 W2W_DEV_TAB=circles W2W_DEV_CIRCLES=board:@CID|real"
  "08-food|W2W_DEV_SKIP_HEALTH=1 W2W_DEV_TAB=record W2W_DEV_RECORD=food|real"
  "09-journal|W2W_DEV_SKIP_HEALTH=1 W2W_DEV_TAB=record W2W_DEV_RECORD=journal|real"
)

say() { printf '\033[1m==>\033[0m %s\n' "$*" >&2; }

avail_gb="$(df -g / | awk 'NR==2 {print $4}')"
if [ "${avail_gb:-0}" -lt 15 ]; then
  echo "only ${avail_gb}G free on /, need at least 15G for a build (docs/ios-native-app.md section 10)" >&2
  exit 1
fi

UDID="$(xcrun simctl list devices available | grep -F "$DEVICE (" | head -1 | sed -E 's/.*\(([0-9A-F-]{36})\).*/\1/')"
if [ -z "$UDID" ]; then echo "no simulator named '$DEVICE'" >&2; exit 1; fi

FAKE_PID=""
cleanup() {
  set +e
  [ -n "$FAKE_PID" ] && kill "$FAKE_PID" 2>/dev/null
  python3 "$IOS/scripts/appstore_demo_server.py" down >/dev/null 2>&1
  xcrun simctl status_bar "$UDID" clear >/dev/null 2>&1
  xcrun simctl shutdown "$UDID" >/dev/null 2>&1
  if [ "${KEEP_DD:-0}" != 1 ]; then rm -rf "$DD"; fi
}
trap cleanup EXIT

say "building the Debug app ($DD)"
( cd "$IOS" && xcodegen generate >/dev/null )
BUILD_ARGS=(CODE_SIGN_IDENTITY=- CODE_SIGN_STYLE=Manual W2W_TEAM_ID=FAKETEAMID)
[ -n "${APP_NAME:-}" ] && BUILD_ARGS+=("W2W_DISPLAY_NAME=$APP_NAME")
xcodebuild -project "$IOS/Watch2Whoop.xcodeproj" -scheme Watch2Whoop -configuration Debug \
  -destination "platform=iOS Simulator,id=$UDID" -derivedDataPath "$DD" "${BUILD_ARGS[@]}" build >"$DD.build.log" 2>&1 \
  || { tail -30 "$DD.build.log" >&2; exit 1; }
APP="$DD/Build/Products/Debug-iphonesimulator/Watch2Whoop.app"

say "starting the demo server (synthetic data) and the fake Jarvis server"
INFO="$(python3 "$IOS/scripts/appstore_demo_server.py" up)"
SERVER="$(printf '%s' "$INFO" | python3 -c 'import json,sys; print(json.load(sys.stdin)["server"])')"
TOKEN="$(printf '%s' "$INFO" | python3 -c 'import json,sys; print(json.load(sys.stdin)["token"])')"
CID="$(printf '%s' "$INFO" | python3 -c 'import json,sys; print(json.load(sys.stdin)["circle_id"])')"
python3 "$IOS/scripts/fake_jarvis_server.py" "$FAKE_PORT" >/dev/null 2>&1 &
FAKE_PID=$!

say "booting $DEVICE ($UDID)"
xcrun simctl boot "$UDID" 2>/dev/null || true
xcrun simctl bootstatus "$UDID" -b >/dev/null
xcrun simctl ui "$UDID" appearance light
xcrun simctl status_bar "$UDID" override --time 9:41 --batteryState charged --batteryLevel 100 \
  --cellularMode active --cellularBars 4 --wifiMode active --wifiBars 3 --operatorName ""

for loc in $LOCALES; do
  case "$loc" in
    zh) LANGARGS=(-AppleLanguages "(zh-Hans)" -AppleLocale zh_CN) ;;
    en) LANGARGS=(-AppleLanguages "(en)" -AppleLocale en_US) ;;
    *) echo "unknown locale $loc" >&2; exit 1 ;;
  esac
  dir="$OUT/6.9/$loc"
  mkdir -p "$dir"
  xcrun simctl uninstall "$UDID" "$BUNDLE" 2>/dev/null || true   # a fresh app database per locale
  xcrun simctl install "$UDID" "$APP"
  first=1
  for row in "${SCREEN_TABLE[@]}"; do
    IFS='|' read -r name vars which <<<"$row"
    if [ "${SCREENS:-all}" != all ] && [[ " $SCREENS " != *" $name "* ]]; then continue; fi
    srv="$SERVER"; tok="$TOKEN"
    if [ "$which" = fake ]; then srv="http://127.0.0.1:$FAKE_PORT"; tok="demo"; fi
    envs=("SIMCTL_CHILD_W2W_DEV_SERVER=$srv" "SIMCTL_CHILD_W2W_DEV_TOKEN=$tok" "SIMCTL_CHILD_W2W_DEV_LANG=$loc")
    for v in $vars; do envs+=("SIMCTL_CHILD_${v//@CID/$CID}"); done
    xcrun simctl terminate "$UDID" "$BUNDLE" 2>/dev/null || true
    say "$loc $name"
    env "${envs[@]}" xcrun simctl launch "$UDID" "$BUNDLE" "${LANGARGS[@]}" >/dev/null
    if [ $first = 1 ]; then sleep "$WAIT_FIRST"; first=0; else sleep "$WAIT"; fi
    xcrun simctl io "$UDID" screenshot --type=png "$dir/$name.png" >/dev/null 2>&1
  done
done

if [ "${WITH_65:-0}" = 1 ]; then
  say "6.5\" set (resized from 6.9\")"
  for loc in $LOCALES; do
    mkdir -p "$OUT/6.5/$loc"
    for f in "$OUT/6.9/$loc"/*.png; do
      sips -z 2778 1284 "$f" --out "$OUT/6.5/$loc/$(basename "$f")" >/dev/null
    done
  done
fi

say "done: $OUT"
find "$OUT" -name '*.png' | sort | while read -r f; do
  printf '%s  %s\n' "$(sips -g pixelWidth -g pixelHeight "$f" | awk '/pixel/ {printf "%s ", $2}')" "${f#$OUT/}"
done
