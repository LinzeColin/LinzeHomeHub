#!/usr/bin/env bash
set -Eeuo pipefail
[[ -x /usr/bin/python3 ]] || { echo '缺少 /usr/bin/python3' >&2; exit 69; }
STATUS_ROOT="${STATUS_ROOT:-/srv/linze/apps/status}"
REPO_ROOT="$(dirname "$STATUS_ROOT")"
export PYTHONPATH="$STATUS_ROOT${PYTHONPATH:+:$PYTHONPATH}"
exec /usr/bin/python3 -m controlplane doctor --repo "$REPO_ROOT" --output "$STATUS_ROOT/runtime/doctor.json"
