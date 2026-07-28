#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
for command in git rclone sha256sum python3 flock realpath; do command -v "$command" >/dev/null || { echo "缺少命令: $command" >&2; exit 69; }; done
: "${PRIVATE_DATABASE_WORKTREE:?缺少 PRIVATE_DATABASE_WORKTREE}"
: "${LINZE_R2_REMOTE:?缺少 LINZE_R2_REMOTE；必须是已配置的 R2 或 rclone crypt remote}"
: "${LINZE_OCI_REMOTE:?缺少 LINZE_OCI_REMOTE}"
: "${STATUS_BACKUP_ENCRYPTION_PROFILE:?缺少 STATUS_BACKUP_ENCRYPTION_PROFILE}"
[[ "$STATUS_BACKUP_ENCRYPTION_PROFILE" == "rclone-crypt" ]] || { echo '仅允许 rclone-crypt 加密配置' >&2; exit 78; }
[[ "${LINZE_R2_REMOTE_IS_CRYPT:-false}" == "true" ]] || { echo 'LINZE_R2_REMOTE 必须是已核验的 rclone crypt remote' >&2; exit 78; }
[[ "${LINZE_OCI_REMOTE_IS_CRYPT:-false}" == "true" ]] || { echo 'LINZE_OCI_REMOTE 必须是独立的 rclone crypt remote' >&2; exit 78; }
STATUS_ROOT="${STATUS_ROOT:-/srv/linze/apps/status}"
TMP_ROOT="${STATUS_BACKUP_TMP:-$STATUS_ROOT/runtime/backup-tmp}"
LOCK_FILE="${STATUS_BACKUP_LOCK:-/run/lock/linze-status-backup.lock}"
mkdir -p "$TMP_ROOT" "$STATUS_ROOT/private" "$(dirname "$LOCK_FILE")"
exec 9>"$LOCK_FILE"; flock -n 9 || { echo '已有备份正在执行' >&2; exit 75; }
WORKTREE="$(realpath "$PRIVATE_DATABASE_WORKTREE")"
[[ "$(git -C "$WORKTREE" rev-parse --is-inside-work-tree)" == true ]] || { echo 'Private-Database worktree 无效' >&2; exit 66; }
HEAD="$(git -C "$WORKTREE" rev-parse HEAD)"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
PREFIX="${STATUS_BACKUP_PREFIX:-private-database}"
OBJECT="$PREFIX/$STAMP-$HEAD.bundle"
LOCAL="$TMP_ROOT/$STAMP-$HEAD.bundle"
R2_READBACK="$TMP_ROOT/r2-$STAMP-$HEAD.bundle"
OCI_READBACK="$TMP_ROOT/oci-$STAMP-$HEAD.bundle"
trap 'rm -f "$LOCAL" "$R2_READBACK" "$OCI_READBACK"' EXIT
git -C "$WORKTREE" bundle create "$LOCAL" --all
git bundle verify "$LOCAL" >/dev/null
LOCAL_HASH="$(sha256sum "$LOCAL" | awk '{print $1}')"
rclone copyto "$LOCAL" "${LINZE_R2_REMOTE%/}/$OBJECT" --checksum --immutable
rclone copyto "${LINZE_R2_REMOTE%/}/$OBJECT" "$R2_READBACK"
[[ "$(sha256sum "$R2_READBACK" | awk '{print $1}')" == "$LOCAL_HASH" ]] || { echo 'R2 readback digest mismatch' >&2; exit 74; }
rclone copyto "$R2_READBACK" "${LINZE_OCI_REMOTE%/}/$OBJECT" --checksum --immutable
rclone copyto "${LINZE_OCI_REMOTE%/}/$OBJECT" "$OCI_READBACK"
[[ "$(sha256sum "$OCI_READBACK" | awk '{print $1}')" == "$LOCAL_HASH" ]] || { echo 'OCI readback digest mismatch' >&2; exit 74; }
python3 - "$STATUS_ROOT/private/backup-status.json" "$HEAD" "$OBJECT" "$LOCAL_HASH" <<'PY'
import json,sys
from datetime import datetime,timezone
from pathlib import Path
path,head,obj,digest=sys.argv[1:]
value={"schema_version":1,"source":"Private-Database","encryption_profile":"rclone-crypt","source_commit":head,"object":obj,"sha256":digest,"r2_readback_verified":True,"oci_readback_verified":True,"backup_exists":True,"restore_verified":False,"observed_at":datetime.now(timezone.utc).replace(microsecond=0).isoformat()}
Path(path).write_text(json.dumps(value,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
PY
echo "BACKUP_READBACK_PASS object=$OBJECT sha256=$LOCAL_HASH"
