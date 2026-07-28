#!/usr/bin/env bash
set -euo pipefail
if [[ $# -ne 2 ]]; then echo "用法: $0 <snapshot_path> <semantic_contract_json>" >&2; exit 2; fi
: "${LINZE_R2_CRYPT_REMOTE:?缺少 LINZE_R2_CRYPT_REMOTE}"
: "${LINZE_OCI_CRYPT_REMOTE:?缺少 LINZE_OCI_CRYPT_REMOTE}"
[[ "${RCLONE_CRYPT_REMOTE_CONFIRMED:-0}" == "1" ]] || { echo "未确认 rclone crypt remote" >&2; exit 3; }
source_path="$1"; semantic_contract="$2"
python3 -m json.tool "$semantic_contract" >/dev/null
evidence="${STATUS_BACKUP_EVIDENCE:-status/runtime/evidence/backup-restore.json}"
python3 -m status.controlplane.agent_cli backup-verify \
  --source "$source_path" \
  --semantic-contract "$semantic_contract" \
  --r2-prefix "${LINZE_R2_CRYPT_REMOTE%/}/backups/private-database" \
  --oci-prefix "${LINZE_OCI_CRYPT_REMOTE%/}/r2-d1-cold-backup" \
  --output "$evidence"
python3 -m json.tool "$evidence" >/dev/null
