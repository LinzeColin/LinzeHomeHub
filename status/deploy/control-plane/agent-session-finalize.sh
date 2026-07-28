#!/usr/bin/env bash
set -euo pipefail
if [[ $# -ne 3 ]]; then
  echo "用法: $0 <run_id> <safe_events_jsonl> <raw_transcript_file_or_dash>" >&2
  exit 2
fi
RUN_ID="$1"; SAFE_EVENTS="$2"; RAW_FILE="$3"
DB_PATH="${STATUS_DB_PATH:-status/runtime/status.db}"
RUNTIME_ROOT="${STATUS_AGENT_RUNTIME_ROOT:-status/runtime/agent-runs}"
RUN_DIR="$RUNTIME_ROOT/$RUN_ID"
[[ -f "$RUN_DIR/session.env" ]] || { echo "缺少 session.env" >&2; exit 3; }
# shellcheck disable=SC1090
source "$RUN_DIR/session.env"
[[ -f "$SAFE_EVENTS" ]] || { echo "缺少安全事件文件" >&2; exit 3; }
python3 -m status.controlplane.agent_cli ingest-normalized \
  --db "$DB_PATH" --input "$SAFE_EVENTS" --provider "$STATUS_AGENT_PROVIDER" \
  --project-id "$STATUS_AGENT_PROJECT_ID" --run-id "$STATUS_AGENT_RUN_ID" \
  --task-id "$STATUS_AGENT_TASK_ID" --intent-hash "$STATUS_AGENT_INTENT_HASH" \
  --session-id "$STATUS_AGENT_SESSION_ID"

if [[ "$RAW_FILE" != "-" ]]; then
  [[ -f "$RAW_FILE" ]] || { echo "原始会话文件不存在" >&2; exit 4; }
  [[ "${RCLONE_CRYPT_REMOTE_CONFIRMED:-0}" == "1" ]] || { echo "未确认 rclone crypt remote" >&2; exit 5; }
  : "${LINZE_R2_CRYPT_REMOTE:?缺少 LINZE_R2_CRYPT_REMOTE}"
  digest="$(sha256sum "$RAW_FILE" | awk '{print $1}')"
  target="${LINZE_R2_CRYPT_REMOTE%/}/primary-objects/agent-sessions/${digest}.blob"
  rclone copyto "$RAW_FILE" "$target" --immutable
  remote_digest="$(rclone cat "$target" | sha256sum | awk '{print $1}')"
  [[ "$remote_digest" == "$digest" ]] || { echo "R2 回读摘要不一致" >&2; exit 6; }
  rm -f "$RAW_FILE"
  printf '%s\n' "$target" > "$RUN_DIR/raw-object-ref.txt"
fi
python3 -m status.controlplane.agent_cli project --db "$DB_PATH" --output status/data/agent-governance.json
