#!/usr/bin/env bash
set -Eeuo pipefail
umask 027
SOURCE_REPO="${SOURCE_REPO:-$(git rev-parse --show-toplevel)}"
SOURCE_STATUS="$SOURCE_REPO/status"
TARGET_STATUS="${TARGET_STATUS:-/srv/linze/apps/status}"
RELEASE_ROOT="${RELEASE_ROOT:-/srv/linze/releases/status}"
ENV_FILE="${STATUS_ENV_FILE:-$TARGET_STATUS/.secrets/control-plane.env}"
LOCK_FILE="${STATUS_DEPLOY_LOCK:-/run/lock/linze-status-deploy.lock}"
for command in git rsync docker sha256sum python3 flock find sort xargs; do command -v "$command" >/dev/null || { echo "缺少命令: $command" >&2; exit 69; }; done
mkdir -p "$(dirname "$LOCK_FILE")" "$TARGET_STATUS" "$RELEASE_ROOT" "$TARGET_STATUS/runtime"
exec 9>"$LOCK_FILE"
flock -n 9 || { echo '已有 status 部署正在执行' >&2; exit 75; }
[[ -d "$SOURCE_STATUS" ]] || { echo '源仓缺少 status/' >&2; exit 66; }
[[ -f "$ENV_FILE" ]] || { echo "缺少受保护环境文件: $ENV_FILE" >&2; exit 78; }
CANDIDATE_COMMIT="$(git -C "$SOURCE_REPO" rev-parse HEAD)"
CANDIDATE_TREE="$(git -C "$SOURCE_REPO" rev-parse 'HEAD^{tree}')"
[[ -z "$(git -C "$SOURCE_REPO" status --porcelain)" ]] || { echo '部署候选存在未提交改动' >&2; exit 65; }
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
BACKUP="$RELEASE_ROOT/$STAMP-$CANDIDATE_COMMIT"
mkdir -p "$BACKUP"
if [[ -d "$TARGET_STATUS" ]]; then
  rsync -a --delete \
    --exclude data/ --exclude private/ --exclude runtime/ --exclude .secrets/ \
    "$TARGET_STATUS/" "$BACKUP/previous/"
fi
rsync -a --delete \
  --exclude data/ --exclude private/ --exclude runtime/ --exclude .secrets/ \
  "$SOURCE_STATUS/" "$TARGET_STATUS/"
# ★ chmod 之外必须 chown 到容器 uid。admin 容器以 1000:1000 跑,
#   而这个目录被 root 建出来是 root:root 0750 —— uid 1000 连进都进不去,
#   容器起来就 sqlite3.OperationalError: unable to open database file,
#   进无限重启循环。实测踩过一次:线上 /admin 因此挂掉。
#   0750 + 属主对齐 = 容器能写、其他用户读不到。
STATUS_RUNTIME_UID="${STATUS_RUNTIME_UID:-1000}"
STATUS_RUNTIME_GID="${STATUS_RUNTIME_GID:-1000}"
chown -R "$STATUS_RUNTIME_UID:$STATUS_RUNTIME_GID" "$TARGET_STATUS/runtime"
chmod 0750 "$TARGET_STATUS/runtime"
export PYTHONPATH="$TARGET_STATUS${PYTHONPATH:+:$PYTHONPATH}"
/usr/bin/python3 -m controlplane migrate --repo "$(dirname "$TARGET_STATUS")"
/usr/bin/python3 -m controlplane import-legacy-prices --repo "$(dirname "$TARGET_STATUS")"
docker compose --env-file "$ENV_FILE" \
  -f "$TARGET_STATUS/deploy/docker-compose.yml" \
  -f "$TARGET_STATUS/deploy/docker-compose.control-plane.yml" config --quiet
docker compose --env-file "$ENV_FILE" \
  -f "$TARGET_STATUS/deploy/docker-compose.yml" \
  -f "$TARGET_STATUS/deploy/docker-compose.control-plane.yml" up -d --build --remove-orphans
"$TARGET_STATUS/deploy/control-plane/doctor.sh"
/usr/bin/python3 -m controlplane collect --repo "$(dirname "$TARGET_STATUS")"
ARTIFACT_DIGEST="$(find "$TARGET_STATUS" -type f \
  ! -path "$TARGET_STATUS/data/*" ! -path "$TARGET_STATUS/private/*" \
  ! -path "$TARGET_STATUS/runtime/*" ! -path "$TARGET_STATUS/.secrets/*" \
  -print0 | sort -z | xargs -0 sha256sum | sha256sum | awk '{print $1}')"
python3 - "$TARGET_STATUS/runtime/deployment-subject.json" "$CANDIDATE_COMMIT" "$CANDIDATE_TREE" "$ARTIFACT_DIGEST" "$BACKUP" <<'PY'
import json,sys
from datetime import datetime,timezone
from pathlib import Path
path,commit,tree,digest,rollback=sys.argv[1:]
value={"schema_version":1,"taskpack_version":"v0.0.0.1","candidate_commit":commit,"candidate_tree":tree,"deployment_artifact_digest":"sha256:"+digest,"deployed_at":datetime.now(timezone.utc).replace(microsecond=0).isoformat(),"rollback_source":rollback,"runtime_agent_dependency":False,"runtime_llm_calls":0,"runtime_token_budget":0}
Path(path).write_text(json.dumps(value,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
PY
ln -sfn "$BACKUP" "$RELEASE_ROOT/previous"
echo "DEPLOY_PASS candidate=$CANDIDATE_COMMIT artifact=sha256:$ARTIFACT_DIGEST"
