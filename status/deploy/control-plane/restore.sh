#!/usr/bin/env bash
# 恢复验证(TaskPack v0.0.0.1,按 owner 授权的 DA-004 修订版实现)
#
# 用法:
#   restore.sh primary <object> <expected_sha256>   从 GitHub Release 资产恢复并验证
#   restore.sh offsite <object> <expected_sha256>   OCI 单向腿 —— 明确拒绝,并留痕
#   兼容别名:r2 -> primary,oci -> offsite(冻结手册里的写法仍可直接跑)
#
# ★ offsite 之所以「拒绝」而不是「跳过」:OCI PAR 是只写预授权链接,
#   结构上读不回来。把它静默跳过会让调用方以为两条腿都验过了 ——
#   那正是「备份存在冒充恢复已验证」的假绿。它必须响一声。
set -Eeuo pipefail
umask 077
for command in curl sha256sum git python3 mktemp openssl; do
  command -v "$command" >/dev/null || { echo "缺少命令: $command" >&2; exit 69; }
done

SOURCE="${1:-primary}"
OBJECT="${2:-}"
EXPECTED_HASH="${3:-}"
[[ -n "$OBJECT" && -n "$EXPECTED_HASH" ]] || {
  echo '用法: restore.sh primary|offsite object expected_sha256' >&2; exit 64; }

case "$SOURCE" in
  primary|r2)
    [[ "$SOURCE" == "r2" ]] && echo '注意:本环境无 R2;别名 r2 已映射到主通道 GitHub Release 资产' >&2
    SOURCE="primary" ;;
  offsite|oci)
    [[ "$SOURCE" == "oci" ]] && echo '注意:别名 oci 已映射到单向异地腿' >&2
    SOURCE="offsite" ;;
  *) echo 'source 只能是 primary|offsite(兼容别名 r2|oci)' >&2; exit 64 ;;
esac

STATUS_ROOT="${STATUS_ROOT:-/srv/linze/apps/status}"
mkdir -p "$STATUS_ROOT/private" "$STATUS_ROOT/runtime"

write_status() {  # $1=verified $2=reason $3=refcount
  python3 - "$STATUS_ROOT/private/restore-status.json" "$SOURCE" "$OBJECT" \
    "$EXPECTED_HASH" "$1" "$2" "$3" <<'PY'
import json,sys
from datetime import datetime,timezone
from pathlib import Path
path,source,obj,digest,verified,reason,refs=sys.argv[1:]
value={"schema_version":2,"source":source,"object":obj,"sha256":digest,
       "restore_verified":verified=="true","reason":reason,
       "ref_count":int(refs) if refs.isdigit() else None,
       "verified_at":datetime.now(timezone.utc).replace(microsecond=0).isoformat()}
Path(path).write_text(json.dumps(value,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
PY
}

if [[ "$SOURCE" == "offsite" ]]; then
  write_status false "OCI PAR 为只写预授权链接,结构上无法回读,故本通道永远不能构成恢复证据" ""
  echo 'RESTORE_NOT_POSSIBLE source=offsite reason=one_way_par_channel' >&2
  echo '若需异地可恢复,必须新增可读的异地对象存储凭据(属新增云资源,需 owner 决定)' >&2
  exit 70
fi

# ── 主通道:下载 -> 摘要 -> 解密 -> bundle 校验 -> fsck -> refs ──────────
: "${LINZE_BACKUP_GH_TOKEN_FILE:?缺少 LINZE_BACKUP_GH_TOKEN_FILE}"
[[ -r "$LINZE_BACKUP_GH_TOKEN_FILE" ]] || { echo 'GitHub 令牌文件不可读' >&2; exit 78; }
: "${STATUS_BACKUP_ENCRYPTION_KEY_FILE:?缺少 STATUS_BACKUP_ENCRYPTION_KEY_FILE}"
[[ -r "$STATUS_BACKUP_ENCRYPTION_KEY_FILE" ]] || { echo '加密密钥文件不可读' >&2; exit 78; }
BACKUP_REPO="${LINZE_BACKUP_GH_REPO:-LinzeColin/Governance}"
BACKUP_TAG="${LINZE_BACKUP_GH_TAG:-status-cold-backup}"
GH_TOKEN_VALUE="$(cat "$LINZE_BACKUP_GH_TOKEN_FILE")"

TMP="$(mktemp -d "$STATUS_ROOT/runtime/restore.XXXXXX")"
trap 'rm -rf "$TMP"' EXIT
ENC="$TMP/object.enc"
BUNDLE="$TMP/private-database.bundle"

ASSET_ID="$(curl -fsS -H "Authorization: Bearer $GH_TOKEN_VALUE" \
  "https://api.github.com/repos/$BACKUP_REPO/releases/tags/$BACKUP_TAG" \
  | python3 -c "
import sys,json
want=sys.argv[1]
for a in json.load(sys.stdin).get('assets',[]):
    if a['name']==want:
        print(a['id']); break
" "$OBJECT")"
[[ -n "$ASSET_ID" ]] || { write_status false "在 $BACKUP_REPO@$BACKUP_TAG 找不到对象 $OBJECT" ""; \
  echo "找不到对象 $OBJECT" >&2; exit 74; }

curl -fsSL -H "Authorization: Bearer $GH_TOKEN_VALUE" \
  -H 'Accept: application/octet-stream' \
  "https://api.github.com/repos/$BACKUP_REPO/releases/assets/$ASSET_ID" -o "$ENC"

ACTUAL="$(sha256sum "$ENC" | awk '{print $1}')"
if [[ "$ACTUAL" != "$EXPECTED_HASH" ]]; then
  write_status false "digest 不一致:期望 $EXPECTED_HASH 实得 $ACTUAL" ""
  echo 'restore digest mismatch' >&2; exit 74
fi

openssl enc -d -aes-256-cbc -pbkdf2 -in "$ENC" -out "$BUNDLE" \
  -pass file:"$STATUS_BACKUP_ENCRYPTION_KEY_FILE" || {
  write_status false "解密失败" ""; echo '解密失败' >&2; exit 74; }

git bundle verify "$BUNDLE" >/dev/null || {
  write_status false "git bundle 校验失败" ""; echo 'bundle 校验失败' >&2; exit 74; }
git init --bare "$TMP/recovered.git" >/dev/null
git -C "$TMP/recovered.git" fetch "$BUNDLE" '+refs/*:refs/*' >/dev/null
git -C "$TMP/recovered.git" fsck --full --strict >/dev/null || {
  write_status false "fsck 失败" ""; echo 'fsck 失败' >&2; exit 74; }
REF_COUNT="$(git -C "$TMP/recovered.git" for-each-ref --format='%(refname)' | wc -l | tr -d ' ')"
[[ "$REF_COUNT" -gt 0 ]] || { write_status false "恢复仓没有 refs" "0"; \
  echo '恢复仓没有 refs' >&2; exit 74; }

write_status true "主通道当轮实跑:下载+摘要+解密+bundle 校验+fsck+refs 全过" "$REF_COUNT"
echo "RESTORE_VERIFIED source=primary object=$OBJECT sha256=$ACTUAL refs=$REF_COUNT"
