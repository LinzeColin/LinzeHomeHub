#!/usr/bin/env bash
set -euo pipefail
REPO_ROOT="${REPO_ROOT:-/srv/linze-home-hub}"
DB_PATH="${STATUS_DB_PATH:-$REPO_ROOT/status/runtime/status.db}"
OUTPUT="${STATUS_AGENT_DOCTOR:-$REPO_ROOT/status/runtime/agent-doctor.json}"
cd "$REPO_ROOT"
args=(doctor --db "$DB_PATH" --output "$OUTPUT")
if [[ -n "${PRIVATE_DB_CLIENT_PATH:-}" ]]; then
  args+=(--private-db-client "$PRIVATE_DB_CLIENT_PATH")
fi
python3 -m status.controlplane.agent_cli "${args[@]}"
python3 -m json.tool "$OUTPUT" >/dev/null
