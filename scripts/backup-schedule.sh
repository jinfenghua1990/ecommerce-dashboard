#!/usr/bin/env bash
# macOS launchd：安装/卸载本项目的自动备份与恢复演练计划。
# 不会在代码拉取后自动启用；只有显式执行 install 才写入 ~/Library/LaunchAgents。
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ACTION="${1:-status}"
UID_NOW="$(id -u)"
LAUNCH_DIR="$HOME/Library/LaunchAgents"
BACKUP_LABEL="com.gino.ecommerce-dashboard.backup"
RESTORE_LABEL="com.gino.ecommerce-dashboard.restore-check"
BACKUP_PLIST="$LAUNCH_DIR/$BACKUP_LABEL.plist"
RESTORE_PLIST="$LAUNCH_DIR/$RESTORE_LABEL.plist"

# 可通过环境变量覆盖，但默认避开白天业务时间。
BACKUP_HOUR="${BACKUP_HOUR:-2}"
BACKUP_MINUTE="${BACKUP_MINUTE:-30}"
RESTORE_WEEKDAY="${RESTORE_WEEKDAY:-0}"   # launchd: 0 = Sunday
RESTORE_HOUR="${RESTORE_HOUR:-3}"
RESTORE_MINUTE="${RESTORE_MINUTE:-30}"
PATH_VALUE="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"

write_backup_plist() {
  cat > "$BACKUP_PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$BACKUP_LABEL</string>
  <key>ProgramArguments</key><array>
    <string>/bin/bash</string><string>-lc</string>
    <string>cd '$ROOT' &amp;&amp; make backup</string>
  </array>
  <key>StartCalendarInterval</key><dict>
    <key>Hour</key><integer>$BACKUP_HOUR</integer>
    <key>Minute</key><integer>$BACKUP_MINUTE</integer>
  </dict>
  <key>EnvironmentVariables</key><dict>
    <key>PATH</key><string>$PATH_VALUE</string>
  </dict>
  <key>StandardOutPath</key><string>/tmp/ecom_backup.log</string>
  <key>StandardErrorPath</key><string>/tmp/ecom_backup.err</string>
</dict></plist>
EOF
}

write_restore_plist() {
  cat > "$RESTORE_PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$RESTORE_LABEL</string>
  <key>ProgramArguments</key><array>
    <string>/bin/bash</string><string>-lc</string>
    <string>cd '$ROOT' &amp;&amp; make restore-check</string>
  </array>
  <key>StartCalendarInterval</key><dict>
    <key>Weekday</key><integer>$RESTORE_WEEKDAY</integer>
    <key>Hour</key><integer>$RESTORE_HOUR</integer>
    <key>Minute</key><integer>$RESTORE_MINUTE</integer>
  </dict>
  <key>EnvironmentVariables</key><dict>
    <key>PATH</key><string>$PATH_VALUE</string>
  </dict>
  <key>StandardOutPath</key><string>/tmp/ecom_restore_check.log</string>
  <key>StandardErrorPath</key><string>/tmp/ecom_restore_check.err</string>
</dict></plist>
EOF
}

bootout_if_loaded() {
  local label="$1"
  launchctl bootout "gui/$UID_NOW/$label" >/dev/null 2>&1 || true
}

case "$ACTION" in
  install)
    mkdir -p "$LAUNCH_DIR"
    bootout_if_loaded "$BACKUP_LABEL"
    bootout_if_loaded "$RESTORE_LABEL"
    write_backup_plist
    write_restore_plist
    plutil -lint "$BACKUP_PLIST" >/dev/null
    plutil -lint "$RESTORE_PLIST" >/dev/null
    launchctl bootstrap "gui/$UID_NOW" "$BACKUP_PLIST"
    launchctl bootstrap "gui/$UID_NOW" "$RESTORE_PLIST"
    echo "已安装自动备份：每天 $(printf '%02d:%02d' "$BACKUP_HOUR" "$BACKUP_MINUTE")"
    echo "已安装恢复演练：每周日 $(printf '%02d:%02d' "$RESTORE_HOUR" "$RESTORE_MINUTE")"
    echo "日志：/tmp/ecom_backup.log /tmp/ecom_restore_check.log"
    ;;
  uninstall)
    bootout_if_loaded "$BACKUP_LABEL"
    bootout_if_loaded "$RESTORE_LABEL"
    rm -f "$BACKUP_PLIST" "$RESTORE_PLIST"
    echo "已卸载自动备份与恢复演练计划。"
    ;;
  status)
    echo "==> $BACKUP_LABEL"
    launchctl print "gui/$UID_NOW/$BACKUP_LABEL" 2>/dev/null | sed -n '1,25p' || echo "未安装/未加载"
    echo "==> $RESTORE_LABEL"
    launchctl print "gui/$UID_NOW/$RESTORE_LABEL" 2>/dev/null | sed -n '1,25p' || echo "未安装/未加载"
    ;;
  *)
    echo "用法：$0 {install|uninstall|status}"
    exit 2
    ;;
esac
