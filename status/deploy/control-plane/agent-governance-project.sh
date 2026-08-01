#!/usr/bin/env bash
set -euo pipefail
# 生成 Agent 开发治理只读投影。
#
# ★ REPO_ROOT 原本写死成 /srv/linze-home-hub —— 那是任务包设想的部署根,本机生产实际是
#   /srv/linze/apps。写死一个不存在的目录,结果是 `cd` 直接失败、timer 每次触发都报错。
#   这里改成**从脚本自身位置推导**:本文件固定在 <REPO_ROOT>/status/deploy/control-plane/ 下,
#   所以往上三层就是 REPO_ROOT。这样无论部署到哪个根都对,而不是把一个硬编码换成另一个。
#   仍然允许用 REPO_ROOT 环境变量显式覆盖(测试与异地部署需要)。
SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="${REPO_ROOT:-$(cd "$SELF_DIR/../../.." && pwd)}"
DB_PATH="${STATUS_DB_PATH:-$REPO_ROOT/status/runtime/status.db}"
# STATUS_V3_LEGACY_PROJECTION_WRITER
LEGACY_OUTPUT="${STATUS_AGENT_LEGACY_PROJECTION:-$REPO_ROOT/status/data/agent-governance-v1-legacy.json}"
if [[ "${LEGACY_OUTPUT##*/}" == "agent-governance.json" ]]; then
  echo "legacy v1 projection may not write protected v3 public path" >&2
  exit 2
fi
TTL="${STATUS_AGENT_EVIDENCE_TTL_MINUTES:-30}"
cd "$REPO_ROOT"
# `python3 -m status.controlplane.agent_cli` 依赖 cwd 上有 status/ 这个包 ——
# cd 到 REPO_ROOT 之后才成立,所以上面那行 cd 不是可有可无的。
python3 -m status.controlplane.agent_cli project --db "$DB_PATH" --output "$LEGACY_OUTPUT" --ttl-minutes "$TTL"
python3 -m json.tool "$LEGACY_OUTPUT" >/dev/null
