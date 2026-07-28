#!/usr/bin/env bash
set -euo pipefail
systemctl disable --now status-agent-governance-project.timer 2>/dev/null || true
systemctl stop status-agent-governance-project.service 2>/dev/null || true
echo "Agent 治理投影定时器已停止；现有 status 其他服务未被修改。"
