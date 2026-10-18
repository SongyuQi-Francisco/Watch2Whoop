#!/bin/bash
# Install / reinstall the scheduled jobs of the body management system (macOS LaunchAgents, active after login, no crontab):
#   org.watch2whoop.body.hourly   at minute 10 of every hour: import-ow, compute-scores (last 3 days), evaluate-rules
#   org.watch2whoop.body.nightly  daily at 03:30 (local time zone = Australia/Sydney): full compute-scores + backup
#   org.watch2whoop.body.doctor   at minute 40 of every hour: health check, macOS notification on problems
#   org.watch2whoop.body.startup  at login (and at install): open Docker Desktop, bring up all services, run one health check
#   org.watch2whoop.body.watchdog every 3 minutes: open Docker if it is not running, restart any service that is down (notify after an automatic recovery)
# Runs missed while the Mac was asleep are made up once by launchd after it wakes.
#
# Once the primary server is Alibaba Cloud Ali (since 2026-10-18, docs/cloud-migration.md section 8), this machine installs only two (BODY_PRIMARY=ali):
#   org.watch2whoop.body.pull-ali daily at 04:30: have Ali dump the three databases, pull them back together with Ali's secrets/ and the Shortcut archive into restic
#   org.watch2whoop.body.doctor   at minute 40 of every hour: check the pulled backups / restore drill + fetch Ali's doctor result over ssh, notify on problems
# It also stops the local Shortcut receiver (org.watch2whoop.healthsync; the plist is renamed to .disabled, reinstall with health_sync/install.sh to roll back).
#
# Usage: deploy/ops/install.sh               install (Mac is the primary server, points at the repo this script lives in)
#        deploy/ops/install.sh --ali-primary primary server is Ali: install only the two jobs above, remove the rest
#        deploy/ops/install.sh --uninstall   remove everything
# When trying the install from a git worktree, set BODY_ROOT=<main repo> so data, secrets and compose all point at the main repo.
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="${BODY_ROOT:-$(cd "$DIR/../.." && pwd)}"
HEALTH="${BODY_HEALTH_DATA_DIR:-$ROOT/health_data}"
LA="$HOME/Library/LaunchAgents"
PY=/usr/bin/python3
ALL_JOBS="hourly nightly doctor startup watchdog pull-ali"
JOBS="hourly nightly doctor startup watchdog"
PRIMARY=mac
[[ "${1:-}" == "--ali-primary" ]] && { JOBS="pull-ali doctor"; PRIMARY=ali; }
UID_="$(id -u)"

for job in $ALL_JOBS; do
  launchctl bootout "gui/$UID_/org.watch2whoop.body.$job" 2>/dev/null || true
done
if [[ "${1:-}" == "--uninstall" ]]; then
  for job in $ALL_JOBS; do rm -f "$LA/org.watch2whoop.body.$job.plist"; done
  echo "已卸载 $ALL_JOBS"
  exit 0
fi
for job in $ALL_JOBS; do [[ " $JOBS " == *" $job "* ]] || rm -f "$LA/org.watch2whoop.body.$job.plist"; done
if [[ "$PRIMARY" == ali ]]; then
  HS="$LA/org.watch2whoop.healthsync.plist"
  if [[ -f "$HS" ]]; then
    launchctl bootout "gui/$UID_" "$HS" 2>/dev/null || true
    mv "$HS" "$HS.disabled"
    echo "已停掉本机的快捷指令接收器（$HS.disabled；回退：health_sync/install.sh）"
  fi
fi

[[ -x "$PY" ]] || { echo "找不到 $PY"; exit 1; }
command -v restic >/dev/null || [[ -x /opt/homebrew/bin/restic ]] || echo "⚠ 没装 restic，备份会失败：brew install restic"
[[ "$(readlink /etc/localtime)" == *Australia/Sydney ]] || echo "⚠ 本机时区不是 Australia/Sydney，每晚任务会按本机时间 03:30 跑"
mkdir -p "$LA" "$HEALTH/ops"

plist() {  # plist <job> <ops args> <schedule XML>
  local job="$1" args="$2" schedule="$3" argxml=""
  for a in $args; do argxml+="<string>$a</string>"; done
  cat > "$LA/org.watch2whoop.body.$job.plist" <<PL
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>org.watch2whoop.body.$job</string>
  <key>ProgramArguments</key><array><string>$PY</string><string>$DIR/ops.py</string>$argxml</array>
  $schedule
  <key>EnvironmentVariables</key><dict>
    <key>BODY_ROOT</key><string>$ROOT</string>
    <key>BODY_HEALTH_DATA_DIR</key><string>$HEALTH</string>
    <key>PATH</key><string>/opt/homebrew/bin:/usr/local/bin:$HOME/.local/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
    <key>LANG</key><string>en_US.UTF-8</string>
    <key>PYTHONIOENCODING</key><string>utf-8</string>
    <key>BODY_OPS_QUIET</key><string>1</string>
    <key>BODY_PRIMARY</key><string>$PRIMARY</string>
  </dict>
  <key>Nice</key><integer>5</integer>
  <key>StandardOutPath</key><string>$HEALTH/ops/launchd-$job.log</string>
  <key>StandardErrorPath</key><string>$HEALTH/ops/launchd-$job.log</string>
</dict></plist>
PL
  plutil -lint -s "$LA/org.watch2whoop.body.$job.plist"
  launchctl bootstrap "gui/$UID_" "$LA/org.watch2whoop.body.$job.plist"
}

if [[ "$PRIMARY" == ali ]]; then
  plist pull-ali "backup --from-ali" '<key>StartCalendarInterval</key><dict><key>Hour</key><integer>4</integer><key>Minute</key><integer>30</integer></dict>'
  plist doctor   "doctor --notify" '<key>StartCalendarInterval</key><dict><key>Minute</key><integer>40</integer></dict>'
else
plist hourly  "hourly"  '<key>StartCalendarInterval</key><dict><key>Minute</key><integer>10</integer></dict>'
plist nightly "nightly" '<key>StartCalendarInterval</key><dict><key>Hour</key><integer>3</integer><key>Minute</key><integer>30</integer></dict>'
plist doctor  "doctor --notify" '<key>StartCalendarInterval</key><dict><key>Minute</key><integer>40</integer></dict>'
plist startup "start-all" '<key>RunAtLoad</key><true/>'   # runs at login; also once at install (idempotent, does not rebuild containers)
plist watchdog "watchdog" '<key>StartInterval</key><integer>180</integer>'
fi

echo "✅ 已安装（主服务器 $PRIMARY；代码 $DIR，数据 $ROOT）："
for job in $JOBS; do
  printf '  %-34s %s\n' "org.watch2whoop.body.$job" "$(launchctl print "gui/$UID_/org.watch2whoop.body.$job" >/dev/null 2>&1 && echo 已加载 || echo 未加载)"
done
echo "日志：$HEALTH/ops/   手动跑：$DIR/ops.sh hourly | backup | doctor"
echo "立即跑一次：launchctl kickstart gui/$UID_/org.watch2whoop.body.hourly"
