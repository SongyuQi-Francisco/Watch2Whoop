#!/bin/bash
# Since 2026-10-18 the butler runs on Alibaba Cloud Ali (deploy/ali/butler/) and the Mac LaunchAgent is disabled;
# this script is only for falling back to the Mac.
# Install / reinstall the body butler LaunchAgent (starts after login, restarts on crash, no crontab):
#   org.watch2whoop.body.butler  runs claude remote-control in the assistant/ directory (deploy/assistant/butler.py run)
# Usage: deploy/assistant/install.sh             install (points at the repo this script lives in)
#        deploy/assistant/install.sh --uninstall remove (the session stays on claude.ai; reinstalling within 4 hours reattaches to it)
#        deploy/assistant/install.sh --disable   disable but keep the plist (renamed .disabled; to fall back, reinstall without arguments)
# When trying the install from a git worktree, set BODY_ROOT=<main repo> so data and secrets point at the main repo (butler directory and code come from the worktree).
# Keep variables like ANTHROPIC_API_KEY out of the process environment: Remote Control only accepts the claude.ai subscription login (butler.py clears them again at startup).
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
CODE="$(cd "$DIR/../.." && pwd)"
ROOT="${BODY_ROOT:-$CODE}"
HEALTH="${BODY_HEALTH_DATA_DIR:-$ROOT/health_data}"
SECRETS="${BODY_SECRETS_DIR:-$ROOT/secrets}"
LABEL=org.watch2whoop.body.butler
LA="$HOME/Library/LaunchAgents"
PLIST="$LA/$LABEL.plist"
PY=/usr/bin/python3
UID_="$(id -u)"

launchctl bootout "gui/$UID_/$LABEL" 2>/dev/null || true
if [[ "${1:-}" == "--disable" ]]; then
  [[ -f "$PLIST" ]] && mv "$PLIST" "$PLIST.disabled"
  echo "已停用 $LABEL（plist 改名 $PLIST.disabled；回退：deploy/assistant/install.sh）"
  exit 0
fi
rm -f "$PLIST.disabled"
if [[ "${1:-}" == "--uninstall" ]]; then
  rm -f "$PLIST"
  echo "已卸载 $LABEL"
  exit 0
fi

[[ -x "$PY" ]] || { echo "找不到 $PY"; exit 1; }
[[ -f "$CODE/assistant/CLAUDE.md" ]] || { echo "找不到 $CODE/assistant/CLAUDE.md"; exit 1; }
# The butler connects to the primary server's (Ali) remote MCP with a dedicated AI token (butler.py puts it into BODY_AI_TOKEN when starting claude; it never goes into the plist)
[[ -f "$SECRETS/body_ai_token_butler" ]] || echo "⚠ 没有 $SECRETS/body_ai_token_butler：ssh Ali 'cd /opt/body-management && deploy/ali/engine.sh ai-token-create --user-id 1 --client butler'，第一行写进这个文件（600）"
mkdir -p "$LA" "$HEALTH/assistant"

# When trying the install from a worktree, the MCP server (body_engine) must know the main repo's secrets; installing from the main repo uses the default paths
EXTRA=""
if [[ "$ROOT" != "$CODE" ]]; then
  EXTRA="<key>BODY_DB_PASSWORD_FILE</key><string>$SECRETS/body_db_password</string>
    <key>BODY_SECRETS_DIR</key><string>$SECRETS</string>
    <key>BODY_ENGINE_TOKEN_FILE</key><string>$SECRETS/body_engine_token_butler</string>"
fi

cat > "$PLIST" <<PL
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key><array><string>$PY</string><string>$DIR/butler.py</string><string>run</string></array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ThrottleInterval</key><integer>30</integer>
  <key>ProcessType</key><string>Interactive</string>
  <key>WorkingDirectory</key><string>$CODE/assistant</string>
  <key>EnvironmentVariables</key><dict>
    <key>BODY_ROOT</key><string>$ROOT</string>
    <key>BODY_HEALTH_DATA_DIR</key><string>$HEALTH</string>
    <key>PATH</key><string>/opt/homebrew/bin:/usr/local/bin:$HOME/.local/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
    <key>LANG</key><string>en_US.UTF-8</string>
    <key>PYTHONIOENCODING</key><string>utf-8</string>
    $EXTRA
  </dict>
  <key>StandardOutPath</key><string>$HEALTH/assistant/launchd-butler.log</string>
  <key>StandardErrorPath</key><string>$HEALTH/assistant/launchd-butler.log</string>
</dict></plist>
PL
plutil -lint -s "$PLIST"
if plutil -convert json -o - "$PLIST" | grep -q -E 'ANTHROPIC_API_KEY|ANTHROPIC_AUTH_TOKEN|ANTHROPIC_BASE_URL|CLAUDE_CODE_OAUTH_TOKEN'; then
  echo "✗ plist 里出现了 API key / token 变量，停止"; exit 1
fi
launchctl bootstrap "gui/$UID_" "$PLIST"

"$PY" "$DIR/butler.py" check || true
echo "✅ 已安装 $LABEL（管家目录 $CODE/assistant，数据 $ROOT）"
echo "看状态：$DIR/butler.py status    日志：$HEALTH/assistant/butler-*.log"
echo "重启：launchctl kickstart -k gui/$UID_/$LABEL"
