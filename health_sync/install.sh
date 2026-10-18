#!/bin/bash
# Installs the health-sync receiver as a background service (starts at login).
set -e
DIR="$(cd "$(dirname "$0")" && pwd)"
PY="$(command -v python3)"
[ -z "$PY" ] && { echo "python3 not found"; exit 1; }
PLIST="$HOME/Library/LaunchAgents/org.watch2whoop.healthsync.plist"
mkdir -p "$HOME/Library/LaunchAgents"
cat > "$PLIST" <<PL
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>org.watch2whoop.healthsync</string>
  <key>ProgramArguments</key><array><string>$PY</string><string>$DIR/health_server.py</string></array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardErrorPath</key><string>$DIR/server.err</string>
</dict></plist>
PL
launchctl bootout gui/$(id -u) "$PLIST" 2>/dev/null || true
launchctl bootstrap gui/$(id -u) "$PLIST"
sleep 2
curl -s http://127.0.0.1:8767/ping && echo "✅ 安装成功，Mac 地址：$(scutil --get LocalHostName).local"
