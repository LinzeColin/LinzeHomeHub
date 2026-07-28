#!/usr/bin/env bash
set -Eeuo pipefail
umask 027
for command in flock rsync docker; do command -v "$command" >/dev/null || { echo "缺少命令: $command" >&2; exit 69; }; done
TARGET_STATUS="${TARGET_STATUS:-/srv/linze/apps/status}"
RELEASE_ROOT="${RELEASE_ROOT:-/srv/linze/releases/status}"
ENV_FILE="${STATUS_ENV_FILE:-$TARGET_STATUS/.secrets/control-plane.env}"
LOCK_FILE="${STATUS_DEPLOY_LOCK:-/run/lock/linze-status-deploy.lock}"
SOURCE="${1:-$RELEASE_ROOT/previous}"
[[ -f "$ENV_FILE" ]] || { echo "缺少受保护环境文件: $ENV_FILE" >&2; exit 78; }
mkdir -p "$(dirname "$LOCK_FILE")"
exec 9>"$LOCK_FILE"; flock -n 9 || { echo '已有部署或回滚正在执行' >&2; exit 75; }
[[ -d "$SOURCE/previous" ]] || { echo "无可用回滚源: $SOURCE/previous" >&2; exit 66; }
rsync -a --delete --exclude data/ --exclude private/ --exclude runtime/ --exclude .secrets/ \
  "$SOURCE/previous/" "$TARGET_STATUS/"
docker compose --env-file "$ENV_FILE" \
  -f "$TARGET_STATUS/deploy/docker-compose.yml" \
  -f "$TARGET_STATUS/deploy/docker-compose.control-plane.yml" up -d --build --remove-orphans
"$TARGET_STATUS/deploy/control-plane/doctor.sh"
echo "ROLLBACK_PASS source=$SOURCE"
