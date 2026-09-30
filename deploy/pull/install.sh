#!/usr/bin/env bash
# 在服务器上以 root 运行：把 deploy/pull 里的脚本、unit、<app>.env 装到位并启用 timer。
#   sudo bash deploy/pull/install.sh <app>            # 安装/更新并启用
#   sudo bash deploy/pull/install.sh <app> --check    # 只比对（服务器与仓库不一致则退出码 1）
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
APP=${1:?用法: install.sh <app> [--check]}
MODE=${2:-install}
[ -f "$HERE/$APP.env" ] || { echo "缺 $HERE/$APP.env" >&2; exit 64; }
[ "$(id -u)" = 0 ] || { echo "需要 root" >&2; exit 77; }

declare -A DEST=(
  ["linze-pull-deploy.sh"]=/usr/local/bin/linze-pull-deploy.sh
  ["linze-pull-deploy@.service"]=/etc/systemd/system/linze-pull-deploy@.service
  ["linze-pull-deploy@.timer"]=/etc/systemd/system/linze-pull-deploy@.timer
  ["$APP.env"]=/etc/linze-pull-deploy/$APP.env
)
if [ "$MODE" = "--check" ]; then
  rc=0
  for f in "${!DEST[@]}"; do
    if cmp -s "$HERE/$f" "${DEST[$f]}"; then echo "一致  ${DEST[$f]}"; else echo "不一致 ${DEST[$f]}"; rc=1; fi
  done
  exit $rc
fi
[ "$MODE" = install ] || { echo "未知参数 $MODE" >&2; exit 64; }
bash -n "$HERE/linze-pull-deploy.sh"
mkdir -p /etc/linze-pull-deploy
install -m 0755 "$HERE/linze-pull-deploy.sh" "${DEST[linze-pull-deploy.sh]}"
install -m 0644 "$HERE/linze-pull-deploy@.service" "${DEST[linze-pull-deploy@.service]}"
install -m 0644 "$HERE/linze-pull-deploy@.timer" "${DEST[linze-pull-deploy@.timer]}"
install -m 0644 "$HERE/$APP.env" "${DEST[$APP.env]}"
systemctl daemon-reload
systemctl enable --now "linze-pull-deploy@$APP.timer"
systemctl list-timers "linze-pull-deploy@$APP.timer" --no-pager | sed -n '1,3p'
