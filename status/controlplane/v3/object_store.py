# STATUS_AGENT_V3_MANAGED: v0.0.0.3
from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import subprocess
import tempfile
from typing import Any, Mapping

from .canonical import digest, utc_now


class ObjectStoreError(RuntimeError):
    pass


def _safe_object_key(value: str) -> str:
    key = str(value).strip().strip("/")
    if not key or ".." in Path(key).parts or "\x00" in key:
        raise ObjectStoreError("invalid object key")
    return key


def _run(argv: list[str], timeout: int = 300) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, text=True, capture_output=True, timeout=timeout, check=False)


def require_crypt_remote(rclone_binary: str, remote: str) -> dict[str, Any]:
    result = _run([rclone_binary, "config", "show", remote.rstrip(":" )])
    text = result.stdout.lower()
    if result.returncode != 0 or "type = crypt" not in text:
        raise ObjectStoreError("R2 raw-session remote must be rclone crypt")
    return {"state": "PASS", "remote": remote, "crypt": True}


def upload_and_readback(*, source: Path, rclone_binary: str, crypt_remote: str, object_key: str) -> dict[str, Any]:
    source = Path(source).resolve()
    if not source.is_file():
        raise ObjectStoreError("source missing")
    require_crypt_remote(rclone_binary, crypt_remote)
    object_key = _safe_object_key(object_key)
    remote_path = f"{crypt_remote.rstrip(':')}:{object_key}"
    upload = _run([rclone_binary, "copyto", str(source), remote_path])
    if upload.returncode != 0:
        raise ObjectStoreError("rclone upload failed")
    with tempfile.TemporaryDirectory(prefix="status-r2-readback-") as directory:
        readback = Path(directory) / source.name
        download = _run([rclone_binary, "copyto", remote_path, str(readback)])
        if download.returncode != 0 or not readback.is_file():
            raise ObjectStoreError("rclone readback failed")
        source_sha = sha256(source.read_bytes()).hexdigest()
        readback_sha = sha256(readback.read_bytes()).hexdigest()
        if source_sha != readback_sha:
            raise ObjectStoreError("R2 readback digest mismatch")
    body = {"schema_version": 3, "state": "READBACK_VERIFIED", "object_key": object_key, "remote_path": remote_path, "plaintext_sha256": source_sha, "readback_sha256": readback_sha, "uploaded_at": utc_now()}
    body["object_receipt_id"] = "r2-object:" + digest(body)
    return body
