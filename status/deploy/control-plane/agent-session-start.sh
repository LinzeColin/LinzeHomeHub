#!/usr/bin/env bash
set -euo pipefail
if [[ $# -ne 5 ]]; then
  echo "用法: $0 <provider> <run_id> <task_id> <session_id> <intent_json>" >&2
  exit 2
fi
PROVIDER="$1"; RUN_ID="$2"; TASK_ID="$3"; SESSION_ID="$4"; INTENT_JSON="$5"
case "$PROVIDER" in codex|claude) ;; *) echo "provider 仅支持 codex 或 claude" >&2; exit 2;; esac
python3 -m json.tool "$INTENT_JSON" >/dev/null
INTENT_HASH="$(python3 - "$INTENT_JSON" <<'PY2'
import json,sys
with open(sys.argv[1], encoding='utf-8') as handle:
    value=json.load(handle)
print(value['intent_sha256'])
PY2
)"
RUNTIME_ROOT="${STATUS_AGENT_RUNTIME_ROOT:-status/runtime/agent-runs}"
RUN_DIR="$RUNTIME_ROOT/$RUN_ID"
install -d -m 0700 "$RUN_DIR"
cat > "$RUN_DIR/session.env" <<EOF
export STATUS_AGENT_PROJECT_ID=status.linzezhang.com
export STATUS_AGENT_RUN_ID=$RUN_ID
export STATUS_AGENT_TASK_ID=$TASK_ID
export STATUS_AGENT_SESSION_ID=$SESSION_ID
export STATUS_AGENT_INTENT_HASH=$INTENT_HASH
export STATUS_AGENT_PROVIDER=$PROVIDER
EOF
chmod 0600 "$RUN_DIR/session.env"
printf '%s\n' "$RUN_DIR/session.env"
