#!/usr/bin/env bash
set -euo pipefail
if [[ $# -ne 4 ]]; then
  echo "用法: $0 <manifest_json> <semantic_contract_json> <r2_object_prefix> <oci_object_prefix>" >&2
  exit 2
fi
MANIFEST="$1"; CONTRACT="$2"; R2_SOURCE="$3"; OCI_SOURCE="$4"
python3 -m json.tool "$MANIFEST" >/dev/null
python3 -m json.tool "$CONTRACT" >/dev/null
tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
rclone copy "$R2_SOURCE" "$tmp/r2" --checksum
rclone copy "$OCI_SOURCE" "$tmp/oci" --checksum
python3 - "$MANIFEST" "$CONTRACT" "$tmp/r2" "$tmp/oci" <<'PY2'
import json,sys
from pathlib import Path
from status.controlplane.backup_transport import verify_restore, verify_semantic_contract
manifest=json.load(open(sys.argv[1],encoding='utf-8'))
contract=json.load(open(sys.argv[2],encoding='utf-8'))
results={}
for name,path in [('r2',Path(sys.argv[3])),('oci',Path(sys.argv[4]))]:
    exact=verify_restore(manifest,path); semantic=verify_semantic_contract(path,contract)
    results[name]={'exact':exact,'semantic':semantic}
    if exact['state']!='RESTORE_VERIFIED' or semantic['state']!='SEMANTIC_VERIFIED':
        print(json.dumps(results,ensure_ascii=False,indent=2)); raise SystemExit(6)
print(json.dumps({'state':'INDEPENDENT_RESTORE_VERIFIED','results':results},ensure_ascii=False,indent=2))
PY2
