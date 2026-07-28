#!/usr/bin/env bash
set -euo pipefail
# REPO_ROOT 从脚本自身位置推导(本文件固定在 <REPO_ROOT>/status/deploy/control-plane/ 下),
# 不再写死 /srv/linze-home-hub —— 那个目录在本机生产上根本不存在。见 agent-governance-project.sh 的注释。
SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="${REPO_ROOT:-$(cd "$SELF_DIR/../../.." && pwd)}"
DB_PATH="${STATUS_DB_PATH:-$REPO_ROOT/status/runtime/status.db}"
OUTPUT="${STATUS_AGENT_DOCTOR:-$REPO_ROOT/status/runtime/agent-doctor.json}"
cd "$REPO_ROOT"
args=(doctor --db "$DB_PATH" --output "$OUTPUT")
if [[ -n "${PRIVATE_DB_CLIENT_PATH:-}" ]]; then
  args+=(--private-db-client "$PRIVATE_DB_CLIENT_PATH")
fi
python3 -m status.controlplane.agent_cli "${args[@]}"
python3 -m json.tool "$OUTPUT" >/dev/null
