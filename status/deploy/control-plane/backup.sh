#!/usr/bin/env bash
# Private-Database 冷备(TaskPack v0.0.0.1,按 owner 授权的 DA-004 修订版实现)
#
# 原任务包版本走 rclone crypt remote(R2 主 + OCI 副,两侧都 readback)。
# 实采证明本环境没有 rclone、没有 R2 桶、OCI 只有单向 PAR ——
# 补齐需新建云资源与凭据,命中 STOP_CONDITIONS 第 3 条,owner 选择方案 B:
# 适配现有通道。详见 status/docs/governance/TASKPACK_V0001_ACCEPTANCE_AMENDMENT.md
#
# 通道能力(必须如实分开记录,不许合并成一个绿):
#   主通道  GitHub Release 资产   可回读 -> 支持 readback + digest + 恢复验证
#   异地腿  OCI PAR              只写   -> 只能记投递回执,永远不算恢复已验证
set -Eeuo pipefail
umask 077
for command in git curl sha256sum python3 flock realpath openssl; do
  command -v "$command" >/dev/null || { echo "缺少命令: $command" >&2; exit 69; }
done

: "${PRIVATE_DATABASE_WORKTREE:?缺少 PRIVATE_DATABASE_WORKTREE}"
: "${STATUS_BACKUP_ENCRYPTION_PROFILE:?缺少 STATUS_BACKUP_ENCRYPTION_PROFILE}"
[[ "$STATUS_BACKUP_ENCRYPTION_PROFILE" == "openssl-aes-256-cbc-pbkdf2" ]] || {
  echo '本环境仅支持 openssl-aes-256-cbc-pbkdf2(rclone-crypt 需要未安装的 rclone)' >&2; exit 78; }
: "${STATUS_BACKUP_ENCRYPTION_KEY_FILE:?缺少 STATUS_BACKUP_ENCRYPTION_KEY_FILE}"
[[ -r "$STATUS_BACKUP_ENCRYPTION_KEY_FILE" ]] || { echo '加密密钥文件不可读' >&2; exit 78; }
: "${LINZE_BACKUP_GH_TOKEN_FILE:?缺少 LINZE_BACKUP_GH_TOKEN_FILE}"
[[ -r "$LINZE_BACKUP_GH_TOKEN_FILE" ]] || { echo 'GitHub 令牌文件不可读' >&2; exit 78; }
BACKUP_REPO="${LINZE_BACKUP_GH_REPO:-LinzeColin/Governance}"
BACKUP_TAG="${LINZE_BACKUP_GH_TAG:-status-cold-backup}"

STATUS_ROOT="${STATUS_ROOT:-/srv/linze/apps/status}"
TMP_ROOT="${STATUS_BACKUP_TMP:-$STATUS_ROOT/runtime/backup-tmp}"
LOCK_FILE="${STATUS_BACKUP_LOCK:-/run/lock/linze-status-backup.lock}"
mkdir -p "$TMP_ROOT" "$STATUS_ROOT/private" "$(dirname "$LOCK_FILE")"
TMP_META="$(mktemp "$TMP_ROOT/repo-meta.XXXXXX")"
trap 'rm -f "$TMP_META"' EXIT
exec 9>"$LOCK_FILE"; flock -n 9 || { echo '已有备份正在执行' >&2; exit 75; }

WORKTREE="$(realpath "$PRIVATE_DATABASE_WORKTREE")"
[[ "$(git -C "$WORKTREE" rev-parse --is-inside-work-tree)" == true ]] || {
  echo 'Private-Database worktree 无效' >&2; exit 66; }

# ── 硬约束 1:目的地仓不得等于被备份的仓 ────────────────────────────────
# 把 Private-Database 的 bundle 存进 Private-Database 自己的 Release,
# 等于把备份放在被备份对象内部 —— 该仓一旦丢失,备份与源同归于尽。
SOURCE_URL="$(git -C "$WORKTREE" remote get-url origin 2>/dev/null || echo '')"
SOURCE_REPO="$(printf '%s' "$SOURCE_URL" \
  | sed -E 's#^git@[^:]+:##; s#^https?://[^/]+/##; s#\.git$##')"
if [[ -n "$SOURCE_REPO" && "${SOURCE_REPO,,}" == "${BACKUP_REPO,,}" ]]; then
  echo "备份目的地不得等于被备份的仓(两者都是 $SOURCE_REPO):循环依赖,拒绝执行" >&2
  exit 78
fi

# ── 硬约束 2:目的地仓必须是私有仓 ──────────────────────────────────────
GH_TOKEN_VALUE="$(cat "$LINZE_BACKUP_GH_TOKEN_FILE")"
# ★ 先单独判「查询本身成没成」,再判可见性。
#   第一版把两件事混在一条管道里:令牌无效时 curl 失败 -> python 解析空串崩溃,
#   虽然仍是 fail-closed,但报错是 JSONDecodeError,看的人根本不知道发生了什么。
#   守卫必须自己说话,不能靠崩溃顺带拦住。
REPO_META="$(curl -sS -o "$TMP_META" -w '%{http_code}' \
  -H "Authorization: Bearer $GH_TOKEN_VALUE" \
  "https://api.github.com/repos/$BACKUP_REPO" 2>/dev/null || echo '000')"
if [[ "$REPO_META" != "200" ]]; then
  echo "无法确认备份目的地 $BACKUP_REPO 的可见性(HTTP $REPO_META):查不到就不推,拒绝执行" >&2
  exit 78
fi
VISIBILITY="$(python3 -c 'import sys,json;print("private" if json.load(open(sys.argv[1])).get("private") else "public")' "$TMP_META")"
[[ "$VISIBILITY" == "private" ]] || {
  echo "备份目的地 $BACKUP_REPO 是 $VISIBILITY 仓,拒绝把私有数据推上去" >&2; exit 78; }

HEAD="$(git -C "$WORKTREE" rev-parse HEAD)"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
PREFIX="${STATUS_BACKUP_PREFIX:-private-database}"
OBJECT="$PREFIX-$STAMP-$HEAD.bundle.enc"
PLAIN="$TMP_ROOT/$STAMP-$HEAD.bundle"
LOCAL="$TMP_ROOT/$OBJECT"
READBACK="$TMP_ROOT/readback-$OBJECT"
trap 'rm -f "$TMP_META" "$PLAIN" "$LOCAL" "$READBACK"' EXIT

git -C "$WORKTREE" bundle create "$PLAIN" --all
# ★ verify 也必须 -C 到仓里:`git bundle verify` 要在一个仓的上下文中跑,
#   否则报 "need a repository to verify a bundle"。第一版漏了 -C,
#   在仓目录里手工测时恰好当时 cwd 就是个仓,所以没暴露;
#   由 cron/部署脚本从别的目录调起来时必然失败。
git -C "$WORKTREE" bundle verify "$PLAIN" >/dev/null
openssl enc -aes-256-cbc -pbkdf2 -salt -in "$PLAIN" -out "$LOCAL" \
  -pass file:"$STATUS_BACKUP_ENCRYPTION_KEY_FILE"
rm -f "$PLAIN"
LOCAL_HASH="$(sha256sum "$LOCAL" | awk '{print $1}')"

# ── 主通道:GitHub Release 资产(上传 -> 回读 -> 摘要比对)──────────────
RELEASE_ID="$(curl -fsS -H "Authorization: Bearer $GH_TOKEN_VALUE" \
  "https://api.github.com/repos/$BACKUP_REPO/releases/tags/$BACKUP_TAG" 2>/dev/null \
  | python3 -c 'import sys,json;print(json.load(sys.stdin).get("id",""))' 2>/dev/null || echo '')"
if [[ -z "$RELEASE_ID" ]]; then
  RELEASE_ID="$(curl -fsS -X POST -H "Authorization: Bearer $GH_TOKEN_VALUE" \
    -d "{\"tag_name\":\"$BACKUP_TAG\",\"name\":\"$BACKUP_TAG\",\"body\":\"status cold backup\"}" \
    "https://api.github.com/repos/$BACKUP_REPO/releases" \
    | python3 -c 'import sys,json;print(json.load(sys.stdin)["id"])')"
fi
# ★ 必须**流式**上传,不能用 --data-binary @file:那个选项会把整个文件读进内存,
#   Private-Database 的加密档 1.2 GB,在这台 VPS 上直接
#   `curl: option --data-binary: out of memory`。
#   早先那次 BACKUP_READBACK_PASS 是拿小对象测的,所以没暴露 ——
#   「小样本过了」不等于「真实体积下能跑」,这条路径此前从未在真实大小上验证过。
#   -T 从磁盘流式读,内存占用与文件大小无关;GitHub 的上传端点要 POST,
#   所以配 -X POST 显式指定方法。
TMP_ASSET="$(mktemp "$TMP_ROOT/asset.XXXXXX")"
trap 'rm -f "$TMP_META" "$PLAIN" "$LOCAL" "$READBACK" "$TMP_ASSET"' EXIT
UPLOAD_CODE="$(curl -sS -o "$TMP_ASSET" -w '%{http_code}' \
  -X POST -T "$LOCAL" \
  -H "Authorization: Bearer $GH_TOKEN_VALUE" \
  -H 'Content-Type: application/octet-stream' \
  "https://uploads.github.com/repos/$BACKUP_REPO/releases/$RELEASE_ID/assets?name=$OBJECT" \
  2>/dev/null || echo '000')"
# ★ 先判「上传成没成」再解析 JSON。第一版把两件事挤在一条管道里,
#   上传一失败,python 就对着空输入抛 JSONDecodeError —— 仍然是 fail-closed,
#   但看的人根本不知道发生了什么。守卫必须自己把话说清楚。
if [[ ! "$UPLOAD_CODE" =~ ^2 ]]; then
  echo "上传备份对象失败(HTTP $UPLOAD_CODE):$(head -c 200 "$TMP_ASSET")" >&2
  exit 74
fi
ASSET_ID="$(python3 -c 'import sys,json;print(json.load(open(sys.argv[1]))["id"])' "$TMP_ASSET")"
curl -fsSL -H "Authorization: Bearer $GH_TOKEN_VALUE" \
  -H 'Accept: application/octet-stream' \
  "https://api.github.com/repos/$BACKUP_REPO/releases/assets/$ASSET_ID" -o "$READBACK"
READBACK_HASH="$(sha256sum "$READBACK" | awk '{print $1}')"
if [[ "$READBACK_HASH" != "$LOCAL_HASH" ]]; then
  echo "主通道 readback digest 不一致:期望 $LOCAL_HASH 实得 $READBACK_HASH" >&2; exit 74
fi
PRIMARY_VERIFIED=true

# ── 异地腿:OCI PAR 单向投递(只写,读不回来)──────────────────────────
# ★ 这条腿永远不构成恢复证据。它只回答「这次有没有投出去」。
OFFSITE_DELIVERED=false
OFFSITE_CODE="not_configured"
if [[ -n "${LINZE_OCI_PAR_URL_FILE:-}" && -r "${LINZE_OCI_PAR_URL_FILE}" ]]; then
  PAR_URL="$(cat "$LINZE_OCI_PAR_URL_FILE")"
  OFFSITE_CODE="$(curl -s -o /dev/null -w '%{http_code}' -T "$READBACK" "${PAR_URL}${OBJECT}" || echo '000')"
  [[ "$OFFSITE_CODE" =~ ^2 ]] && OFFSITE_DELIVERED=true
fi

python3 - "$STATUS_ROOT/private/backup-status.json" "$HEAD" "$OBJECT" "$LOCAL_HASH" \
  "$BACKUP_REPO" "$BACKUP_TAG" "$PRIMARY_VERIFIED" "$OFFSITE_DELIVERED" "$OFFSITE_CODE" <<'PY'
import json,sys
from datetime import datetime,timezone
from pathlib import Path
path,head,obj,digest,repo,tag,primary,delivered,code=sys.argv[1:]
value={
  "schema_version":2,
  "source":"Private-Database",
  "encryption_profile":"openssl-aes-256-cbc-pbkdf2",
  "source_commit":head,
  "object":obj,
  "sha256":digest,
  "primary_channel":"github-release-asset",
  "primary_repo":repo,
  "primary_tag":tag,
  "primary_readback_verified":primary=="true",
  # ★ 异地腿如实分开记:投出去了 != 能恢复。
  "offsite_channel":"oci-par-one-way",
  "offsite_delivered":delivered=="true",
  "offsite_http_code":code,
  "offsite_readback_supported":False,
  "offsite_readback_reason":"OCI PAR 为只写预授权链接,结构上无法回读,故永不计入恢复已验证",
  # ★ 备份存在永远不等于恢复已验证 —— 后者只能由 restore.sh 当轮实跑写入。
  "backup_exists":True,
  "restore_verified":False,
  "observed_at":datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
}
Path(path).write_text(json.dumps(value,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
PY
echo "BACKUP_READBACK_PASS object=$OBJECT sha256=$LOCAL_HASH primary=$BACKUP_REPO offsite_code=$OFFSITE_CODE"
