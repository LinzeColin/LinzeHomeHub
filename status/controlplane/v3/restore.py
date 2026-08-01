# STATUS_AGENT_V3_MANAGED: v0.0.0.3
from __future__ import annotations

from hashlib import sha256
from pathlib import Path
import subprocess
import tempfile
from typing import Any, Mapping

from .canonical import digest, utc_now


class RestoreError(RuntimeError):
    pass


def _run(argv: list[str], timeout: int = 300) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, text=True, capture_output=True, timeout=timeout, check=False)


def copy_and_restore(*, rclone_binary: str, r2_remote_path: str, oci_remote_path: str, expected_sha256: str) -> dict[str, Any]:
    copy = _run([rclone_binary, "copyto", r2_remote_path, oci_remote_path])
    if copy.returncode != 0:
        raise RestoreError("R2 to OCI copy failed")
    with tempfile.TemporaryDirectory(prefix="status-oci-empty-restore-") as directory:
        root = Path(directory)
        restored = root / "restored.bin"
        get = _run([rclone_binary, "copyto", oci_remote_path, str(restored)])
        if get.returncode != 0 or not restored.is_file():
            raise RestoreError("OCI independent restore failed")
        actual = sha256(restored.read_bytes()).hexdigest()
        if actual != expected_sha256:
            raise RestoreError("OCI restored digest mismatch")
    body = {"schema_version": 3, "state": "RESTORE_VERIFIED", "source_mode": "EMPTY_ENVIRONMENT", "r2_remote_path": r2_remote_path, "oci_remote_path": oci_remote_path, "restored_sha256": actual, "verified_at": utc_now()}
    body["restore_receipt_id"] = "oci-restore:" + digest(body)
    return body


def verify_pair(r2_receipt: Mapping[str, Any], oci_receipt: Mapping[str, Any]) -> dict[str, Any]:
    expected = str(r2_receipt.get("plaintext_sha256") or r2_receipt.get("readback_sha256") or "")
    actual = str(oci_receipt.get("restored_sha256") or "")
    failures = []
    if r2_receipt.get("state") != "READBACK_VERIFIED": failures.append("R2_NOT_VERIFIED")
    if oci_receipt.get("state") != "RESTORE_VERIFIED": failures.append("OCI_NOT_VERIFIED")
    if oci_receipt.get("source_mode") != "EMPTY_ENVIRONMENT": failures.append("OCI_NOT_INDEPENDENT")
    if not expected or expected != actual: failures.append("DIGEST_MISMATCH")
    return {"state": "PASS" if not failures else "BLOCKED", "failures": failures, "sha256": expected if not failures else None}
