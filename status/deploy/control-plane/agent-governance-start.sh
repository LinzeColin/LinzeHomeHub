#!/usr/bin/env bash
set -euo pipefail
systemctl daemon-reload
systemctl enable --now status-agent-governance-project.timer
systemctl start status-agent-governance-project.service
systemctl --no-pager --full status status-agent-governance-project.service
