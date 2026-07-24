#!/bin/bash
# 异地备份:每日 -> GitHub Release 资产(可轮转,不进 git 历史);每周日 -> OCI(备份的备份)
# 部署位置 /usr/local/bin/linze-offsite-backup.sh,由 root cron 每日 03:40 执行。
set -uo pipefail
TS=$(date -u +%Y%m%d-%H%M%S)
DEST=/srv/linze/backups; STAGE="$DEST/.stage-$TS"
ENCKEY=/srv/linze/secrets/backup_enc.key
PAR=$(cat /srv/linze/secrets/oci_par_url 2>/dev/null)
GH_TOKEN=$(cat /srv/linze/secrets/github_backup_pat 2>/dev/null)
GH_REPO=LinzeColin/Private-Database
GH_TAG=infra-backups
KEEP=30

mkdir -p "$STAGE/db" "$STAGE/config"
for c in $(docker ps --format '{{.Names}} {{.Image}}' | awk '/postgres/{print $1}'); do
  U=$(docker exec "$c" printenv POSTGRES_USER 2>/dev/null); U=${U:-postgres}
  docker exec "$c" pg_dumpall -U "$U" 2>/dev/null | gzip > "$STAGE/db/${c}-${TS}.sql.gz"
done
cp /data/coolify/source/.env "$STAGE/config/coolify-env-${TS}.env" 2>/dev/null || true

TAR="$DEST/linze-backup-${TS}.tar.gz"
tar -czf "$TAR" -C "$STAGE" .
ENC="${TAR}.enc"
openssl enc -aes-256-cbc -pbkdf2 -salt -in "$TAR" -out "$ENC" -pass file:"$ENCKEY"
rm -rf "$TAR" "$STAGE"
SZ=$(stat -c%s "$ENC"); NAME=$(basename "$ENC")

# ---- 主通道:GitHub Release 资产(每日,滚动保留 KEEP 份)----
GH_CODE=000
if [ -n "$GH_TOKEN" ]; then
  RID=$(curl -s -H "Authorization: Bearer $GH_TOKEN" \
        "https://api.github.com/repos/$GH_REPO/releases/tags/$GH_TAG" \
        | python3 -c 'import sys,json;print(json.load(sys.stdin).get("id",""))' 2>/dev/null)
  if [ -n "$RID" ]; then
    GH_CODE=$(curl -s -o /dev/null -w '%{http_code}' \
      -H "Authorization: Bearer $GH_TOKEN" -H "Content-Type: application/octet-stream" \
      --data-binary @"$ENC" \
      "https://uploads.github.com/repos/$GH_REPO/releases/$RID/assets?name=$NAME")
    # 轮转:超过 KEEP 份就删最旧的
    curl -s -H "Authorization: Bearer $GH_TOKEN" \
      "https://api.github.com/repos/$GH_REPO/releases/$RID/assets?per_page=100" \
      | python3 -c "
import sys,json
a=[x for x in json.load(sys.stdin) if x.get('name','').startswith('linze-backup-')]
a.sort(key=lambda x: x['created_at'])
for x in a[:max(0, len(a)-$KEEP)]: print(x['id'])
" 2>/dev/null | while read -r aid; do
        [ -n "$aid" ] && curl -s -o /dev/null -X DELETE -H "Authorization: Bearer $GH_TOKEN" \
          "https://api.github.com/repos/$GH_REPO/releases/assets/$aid"
      done
  fi
fi

# ---- 备通道:OCI 仅每周日(PAR 只写不可删,降低累积压力)----
OCI_CODE=skip
if [ "$(date -u +%u)" = "7" ] && [ -n "$PAR" ]; then
  OCI_CODE=$(curl -s -o /dev/null -w '%{http_code}' -T "$ENC" "${PAR}${NAME}")
fi

echo "$(date -u +%FT%TZ) github=$GH_CODE offsite=$OCI_CODE size=${SZ}B file=$NAME"

# 本地保留 7 份
ls -1t "$DEST"/linze-backup-*.enc 2>/dev/null | tail -n +8 | xargs -r rm -f
