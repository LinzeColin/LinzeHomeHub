#!/usr/bin/env bash
set -Eeuo pipefail
command -v docker >/dev/null || { echo '缺少命令: docker' >&2; exit 69; }
TARGET_STATUS="${TARGET_STATUS:-/srv/linze/apps/status}"
ENV_FILE="${STATUS_ENV_FILE:-$TARGET_STATUS/.secrets/control-plane.env}"
[[ -f "$ENV_FILE" ]] || { echo "缺少环境文件: $ENV_FILE" >&2; exit 78; }
docker compose --env-file "$ENV_FILE" \
  -f "$TARGET_STATUS/deploy/docker-compose.yml" \
  -f "$TARGET_STATUS/deploy/docker-compose.control-plane.yml" stop
