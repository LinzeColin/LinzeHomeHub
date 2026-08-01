#!/usr/bin/env bash
set -euo pipefail
if [[ $# -ne 1 ]]; then echo "用法: $0 <base_commit>" >&2; exit 2; fi
BASE="$1"
git cat-file -e "$BASE^{commit}"
paths=(
  status/controlplane/authority.py status/controlplane/cli.py
  status/controlplane/agent_cli.py status/controlplane/agent_hook.py status/controlplane/agent_projection.py
  status/controlplane/agent_store.py status/controlplane/backup_transport.py status/controlplane/candidate.py
  status/controlplane/capture.py status/controlplane/gate.py status/controlplane/intent.py status/controlplane/redaction.py
  status/controlplane/sql/002_agent_governance.sql status/web/agent-governance.html
  status/web/agent-governance.css status/web/agent-governance.js
  # STATUS_V3_PUBLIC_PROJECTION_PRESERVED: v3 signed public JSON uses its own release rollback contract.
  status/data/agent-governance-v1-legacy.json
)
for path in "${paths[@]}"; do
  if git cat-file -e "$BASE:$path" 2>/dev/null; then git checkout "$BASE" -- "$path"; else rm -f "$path"; fi
done
systemctl daemon-reload 2>/dev/null || true
systemctl restart status-control-plane-collect.service 2>/dev/null || true
echo "已恢复指定路径至 $BASE；请运行现有 status doctor 与生产 HTTP Oracle。"
