"""Content-addressed backup and deterministic restore verification helpers."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path
from typing import Any, Iterable

from .models import stable_id, utc_now


@dataclass(frozen=True)
class ObjectRecord:
    object_id: str
    relative_path: str
    size: int
    sha256: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "object_id": self.object_id,
            "relative_path": self.relative_path,
            "size": self.size,
            "sha256": self.sha256,
        }


def hash_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def build_manifest(root: Path, files: Iterable[Path], *, encryption_profile: str) -> dict[str, Any]:
    root = root.resolve()
    records = []
    for path in sorted({Path(item).resolve() for item in files}):
        relative = path.relative_to(root)
        if not path.is_file():
            raise ValueError(f"backup input is not a file: {relative}")
        digest = hash_file(path)
        records.append(ObjectRecord(
            object_id=stable_id("object", str(relative), digest),
            relative_path=str(relative),
            size=path.stat().st_size,
            sha256=digest,
        ).to_dict())
    manifest = {
        "schema_version": 1,
        "generated_at": utc_now(),
        "encryption_profile": encryption_profile,
        "objects": records,
    }
    manifest["manifest_sha256"] = sha256(
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return manifest


def verify_restore(manifest: dict[str, Any], restored_root: Path) -> dict[str, Any]:
    restored_root = restored_root.resolve()
    failures = []
    checked = []
    for record in manifest.get("objects", []):
        relative = Path(record["relative_path"])
        path = (restored_root / relative).resolve()
        try:
            path.relative_to(restored_root)
        except ValueError:
            failures.append({"path": str(relative), "reason": "path_escape"})
            continue
        if not path.is_file():
            failures.append({"path": str(relative), "reason": "missing"})
            continue
        actual = hash_file(path)
        if actual != record["sha256"]:
            failures.append({"path": str(relative), "reason": "digest_mismatch", "actual_sha256": actual})
            continue
        if path.stat().st_size != int(record["size"]):
            failures.append({"path": str(relative), "reason": "size_mismatch"})
            continue
        checked.append(str(relative))
    return {
        "state": "RESTORE_VERIFIED" if not failures else "RESTORE_FAILED",
        "verified_at": utc_now(),
        "checked": checked,
        "failures": failures,
        "source_manifest_sha256": manifest.get("manifest_sha256"),
    }
