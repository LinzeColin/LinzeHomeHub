#!/usr/bin/env bash
set -Eeuo pipefail
for command in install systemctl id; do command -v "$command" >/dev/null || { echo "缺少命令: $command" >&2; exit 69; }; done
[[ "$(id -u)" -eq 0 ]] || { echo '必须以 root 在 OVH 运行' >&2; exit 77; }
SOURCE="$(cd "$(dirname "${BASH_SOURCE[0]}")/../systemd" && pwd)"
install -m 0644 "$SOURCE"/*.service "$SOURCE"/*.timer /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now \
  linze-status-control-plane-collect.timer \
  linze-status-authority-sync.timer \
  linze-status-backup.timer \
  linze-status-selfheal.timer
systemctl list-timers --all --no-pager 'linze-status-*'
