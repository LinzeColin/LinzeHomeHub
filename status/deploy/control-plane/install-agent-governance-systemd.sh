#!/usr/bin/env bash
set -euo pipefail
REPO_ROOT="${REPO_ROOT:-/srv/linze-home-hub}"
install -m 0644 "$REPO_ROOT/status/deploy/systemd/status-agent-governance-project.service" /etc/systemd/system/
install -m 0644 "$REPO_ROOT/status/deploy/systemd/status-agent-governance-project.timer" /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now status-agent-governance-project.timer
systemctl start status-agent-governance-project.service
systemctl --no-pager status status-agent-governance-project.service
