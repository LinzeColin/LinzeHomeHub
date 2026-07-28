#!/usr/bin/env bash
set -Eeuo pipefail
# 安装 Agent 开发治理投影的 systemd service + timer。
#
# ★ 原版写死 REPO_ROOT=/srv/linze-home-hub —— 那是任务包设想的部署根,本机生产是
#   /srv/linze/apps,该目录根本不存在,装上去 ExecStart 直接找不到文件。
#   现在从脚本自身位置推导,并且在装之前**先校验单元文件里的路径与本次部署根一致** ——
#   不一致就拒绝安装,而不是装一个每次触发都失败的 timer。

for command in install systemctl id; do
  command -v "$command" >/dev/null || { echo "缺少命令: $command" >&2; exit 69; }
done
[[ "$(id -u)" -eq 0 ]] || { echo '必须以 root 运行' >&2; exit 77; }

SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="${REPO_ROOT:-$(cd "$SELF_DIR/../../.." && pwd)}"
UNIT_DIR="$REPO_ROOT/status/deploy/systemd"
SERVICE="$UNIT_DIR/status-agent-governance-project.service"
TIMER="$UNIT_DIR/status-agent-governance-project.timer"

for f in "$SERVICE" "$TIMER"; do
  [[ -f "$f" ]] || { echo "缺少单元文件: $f" >&2; exit 66; }
done

# ── 装之前先校验,别装一个必然失败的 timer ──────────────────────────
EXEC="$(sed -n 's/^ExecStart=//p' "$SERVICE" | head -1)"
[[ -x "$EXEC" ]] || { echo "★ ExecStart 指向的脚本不存在或不可执行: $EXEC" >&2; exit 70; }
WORKDIR="$(sed -n 's/^WorkingDirectory=//p' "$SERVICE" | head -1)"
[[ -d "$WORKDIR" ]] || { echo "★ WorkingDirectory 不存在: $WORKDIR" >&2; exit 70; }
[[ "$EXEC" == "$REPO_ROOT"/* ]] || {
  echo "★ ExecStart($EXEC) 不在本次部署根($REPO_ROOT)下 —— 单元与部署漂移了" >&2; exit 70; }
RUN_USER="$(sed -n 's/^User=//p' "$SERVICE" | head -1)"
if [[ -n "$RUN_USER" ]]; then
  id "$RUN_USER" >/dev/null 2>&1 || { echo "★ User=$RUN_USER 不存在" >&2; exit 70; }
fi
for p in $(sed -n 's/^ReadWritePaths=//p' "$SERVICE" | head -1); do
  [[ -d "$p" ]] || { echo "★ ReadWritePaths 里的 $p 不存在" >&2; exit 70; }
done

install -m 0644 "$SERVICE" "$TIMER" /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now status-agent-governance-project.timer
# 立刻跑一次,失败就当场知道,而不是等下一个周期
systemctl start status-agent-governance-project.service
systemctl --no-pager --full status status-agent-governance-project.service || true
systemctl list-timers --all --no-pager status-agent-governance-project.timer
