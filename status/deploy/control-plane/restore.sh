#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
for command in rclone sha256sum git python3 mktemp; do command -v "$command" >/dev/null || { echo "缺少命令: $command" >&2; exit 69; }; done
SOURCE="${1:-r2}"
OBJECT="${2:-}"
EXPECTED_HASH="${3:-}"
[[ -n "$OBJECT" && -n "$EXPECTED_HASH" ]] || { echo '用法: restore.sh r2|oci object expected_sha256' >&2; exit 64; }
case "$SOURCE" in
  r2) : "${LINZE_R2_REMOTE:?缺少 LINZE_R2_REMOTE}"; REMOTE="${LINZE_R2_REMOTE%/}" ;;
  oci) : "${LINZE_OCI_REMOTE:?缺少 LINZE_OCI_REMOTE}"; REMOTE="${LINZE_OCI_REMOTE%/}" ;;
  *) echo 'source 只能是 r2 或 oci' >&2; exit 64 ;;
esac
STATUS_ROOT="${STATUS_ROOT:-/srv/linze/apps/status}"
TMP="$(mktemp -d "$STATUS_ROOT/runtime/restore.XXXXXX")"
trap 'rm -rf "$TMP"' EXIT
BUNDLE="$TMP/private-database.bundle"
rclone copyto "$REMOTE/$OBJECT" "$BUNDLE"
ACTUAL="$(sha256sum "$BUNDLE" | awk '{print $1}')"
[[ "$ACTUAL" == "$EXPECTED_HASH" ]] || { echo 'restore digest mismatch' >&2; exit 74; }
git bundle verify "$BUNDLE" >/dev/null
git init --bare "$TMP/recovered.git" >/dev/null
git -C "$TMP/recovered.git" fetch "$BUNDLE" '+refs/*:refs/*' >/dev/null
git -C "$TMP/recovered.git" fsck --full --strict >/dev/null
REF_COUNT="$(git -C "$TMP/recovered.git" for-each-ref --format='%(refname)' | wc -l | tr -d ' ')"
[[ "$REF_COUNT" -gt 0 ]] || { echo '恢复仓没有 refs' >&2; exit 74; }
python3 - "$STATUS_ROOT/private/restore-status.json" "$SOURCE" "$OBJECT" "$ACTUAL" "$REF_COUNT" <<'PY'
import json,sys
from datetime import datetime,timezone
from pathlib import Path
path,source,obj,digest,refs=sys.argv[1:]
value={"schema_version":1,"source":source,"object":obj,"sha256":digest,"ref_count":int(refs),"restore_verified":True,"verified_at":datetime.now(timezone.utc).replace(microsecond=0).isoformat()}
Path(path).write_text(json.dumps(value,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
PY
echo "RESTORE_VERIFIED source=$SOURCE object=$OBJECT sha256=$ACTUAL refs=$REF_COUNT"
