#!/usr/bin/env bash
set -euo pipefail
REPO_ROOT="${REPO_ROOT:-/srv/linze-home-hub}"
DB_PATH="${STATUS_DB_PATH:-$REPO_ROOT/status/runtime/status.db}"
OUTPUT="${STATUS_AGENT_PROJECTION:-$REPO_ROOT/status/data/agent-governance.json}"
TTL="${STATUS_AGENT_EVIDENCE_TTL_MINUTES:-30}"
cd "$REPO_ROOT"
python3 -m status.controlplane.agent_cli project --db "$DB_PATH" --output "$OUTPUT" --ttl-minutes "$TTL"
python3 -m json.tool "$OUTPUT" >/dev/null
