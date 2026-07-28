"""R2 -> restore -> OCI -> restore verification with deterministic manifests."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
from typing import Any, Mapping, Protocol


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _files(root: Path) -> list[Path]:
    if root.is_file():
        return [root]
    return sorted(path for path in root.rglob("*") if path.is_file())


def build_manifest(source: Path, *, encryption_profile: str = "rclone-crypt") -> dict[str, Any]:
    source = Path(source).resolve()
    if not source.exists():
        raise FileNotFoundError(source)
    base = source.parent if source.is_file() else source
    entries = []
    for path in _files(source):
        data = path.read_bytes()
        entries.append({
            "path": path.relative_to(base).as_posix(),
            "size_bytes": len(data),
            "sha256": sha256(data).hexdigest(),
        })
    identity = {
        "schema_version": 1,
        "encryption_profile": encryption_profile,
        "files": entries,
    }
    manifest_id = sha256(json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return {**identity, "manifest_id": manifest_id, "generated_at": _now()}


def verify_restore(manifest: Mapping[str, Any], restored_root: Path) -> dict[str, Any]:
    root = Path(restored_root).resolve()
    expected = {str(item["path"]): item for item in manifest.get("files") or []}
    actual_files = _files(root) if root.exists() else []
    base = root.parent if root.is_file() else root
    actual = {path.relative_to(base).as_posix(): path for path in actual_files}
    missing = sorted(set(expected) - set(actual))
    extra = sorted(set(actual) - set(expected))
    mismatched = []
    for relative in sorted(set(expected) & set(actual)):
        data = actual[relative].read_bytes()
        if len(data) != int(expected[relative]["size_bytes"]) or sha256(data).hexdigest() != expected[relative]["sha256"]:
            mismatched.append(relative)
    state = "RESTORE_VERIFIED" if not missing and not extra and not mismatched else "RESTORE_FAILED"
    return {
        "state": state,
        "manifest_id": manifest.get("manifest_id"),
        "expected_count": len(expected),
        "actual_count": len(actual),
        "missing": missing,
        "extra": extra,
        "mismatched": mismatched,
        "verified_at": _now(),
    }


def _json_pointer(value: Any, pointer: str) -> Any:
    if pointer in {"", "/"}:
        return value
    current = value
    for raw in pointer.lstrip("/").split("/"):
        token = raw.replace("~1", "/").replace("~0", "~")
        if isinstance(current, list):
            current = current[int(token)]
        elif isinstance(current, Mapping):
            current = current[token]
        else:
            raise KeyError(pointer)
    return current


def verify_semantic_contract(restored_root: Path, contract: Mapping[str, Any] | None) -> dict[str, Any]:
    """Verify schema/count/key facts after byte-exact restore.

    The contract is intentionally small and deterministic:
    ``required_paths`` plus ``json_assertions`` entries containing
    ``path``, ``pointer`` and either ``expected`` or ``minimum``.
    """
    if not contract:
        return {"state": "SEMANTIC_NOT_CONFIGURED", "checks": []}
    root = Path(restored_root).resolve()
    checks: list[dict[str, Any]] = []
    failures: list[str] = []
    for relative in contract.get("required_paths") or []:
        exists = (root / str(relative)).is_file()
        checks.append({"kind": "required_path", "path": str(relative), "state": "PASS" if exists else "FAIL"})
        if not exists:
            failures.append(f"missing:{relative}")
    for index, assertion in enumerate(contract.get("json_assertions") or []):
        relative = str(assertion["path"])
        pointer = str(assertion.get("pointer") or "")
        state = "PASS"
        actual: Any = None
        try:
            value = json.loads((root / relative).read_text(encoding="utf-8"))
            actual = _json_pointer(value, pointer)
            if "expected" in assertion and actual != assertion["expected"]:
                state = "FAIL"
            if "minimum" in assertion and (not isinstance(actual, (int, float)) or actual < assertion["minimum"]):
                state = "FAIL"
        except (OSError, ValueError, KeyError, IndexError, TypeError) as exc:
            state = "FAIL"
            actual = f"ERROR:{type(exc).__name__}"
        check = {"kind": "json_assertion", "index": index, "path": relative, "pointer": pointer, "state": state, "actual": actual}
        if "expected" in assertion:
            check["expected"] = assertion["expected"]
        if "minimum" in assertion:
            check["minimum"] = assertion["minimum"]
        checks.append(check)
        if state != "PASS":
            failures.append(f"assertion:{index}")
    return {"state": "SEMANTIC_VERIFIED" if not failures else "SEMANTIC_FAILED", "checks": checks, "failures": failures}


class Transport(Protocol):
    def copy_from_local(self, source: Path, destination: str) -> None: ...
    def copy_to_local(self, source: str, destination: Path) -> None: ...
    def copy_remote(self, source: str, destination: str) -> None: ...


@dataclass
class RcloneTransport:
    executable: str = "rclone"
    timeout: int = 900

    def _run(self, *args: str) -> None:
        completed = subprocess.run(
            [self.executable, *args], text=True, capture_output=True,
            timeout=self.timeout, check=False,
        )
        if completed.returncode != 0:
            raise RuntimeError(completed.stderr.strip() or completed.stdout.strip() or "rclone failed")

    def copy_from_local(self, source: Path, destination: str) -> None:
        self._run("copy", str(source), destination, "--checksum", "--immutable")

    def copy_to_local(self, source: str, destination: Path) -> None:
        destination.mkdir(parents=True, exist_ok=True)
        self._run("copy", source, str(destination), "--checksum")

    def copy_remote(self, source: str, destination: str) -> None:
        self._run("copy", source, destination, "--checksum", "--immutable")


@dataclass
class LocalMirrorTransport:
    """Filesystem transport used only by deterministic tests."""

    root: Path

    def _path(self, value: str) -> Path:
        safe = value.replace(":", "/").strip("/")
        return self.root / safe

    def copy_from_local(self, source: Path, destination: str) -> None:
        target = self._path(destination)
        target.mkdir(parents=True, exist_ok=True)
        if source.is_file():
            shutil.copy2(source, target / source.name)
        else:
            shutil.copytree(source, target, dirs_exist_ok=True)

    def copy_to_local(self, source: str, destination: Path) -> None:
        origin = self._path(source)
        destination.mkdir(parents=True, exist_ok=True)
        shutil.copytree(origin, destination, dirs_exist_ok=True)

    def copy_remote(self, source: str, destination: str) -> None:
        origin = self._path(source)
        target = self._path(destination)
        target.mkdir(parents=True, exist_ok=True)
        shutil.copytree(origin, target, dirs_exist_ok=True)


def backup_replicate_restore(
    source: Path,
    *,
    r2_prefix: str,
    oci_prefix: str,
    transport: Transport,
    evidence_path: Path,
    encryption_profile: str = "rclone-crypt",
    semantic_contract: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    if encryption_profile != "rclone-crypt":
        raise ValueError("backup transport requires rclone-crypt")
    source = Path(source).resolve()
    manifest = build_manifest(source, encryption_profile=encryption_profile)
    object_key = manifest["manifest_id"]
    r2_target = f"{r2_prefix.rstrip('/')}/{object_key}"
    oci_target = f"{oci_prefix.rstrip('/')}/{object_key}"

    with tempfile.TemporaryDirectory(prefix="status-backup-verify-") as temp_dir:
        temp = Path(temp_dir)
        r2_restore = temp / "r2"
        oci_restore = temp / "oci"
        transport.copy_from_local(source, r2_target)
        transport.copy_to_local(r2_target, r2_restore)
        r2_verification = verify_restore(manifest, r2_restore)
        if r2_verification["state"] != "RESTORE_VERIFIED":
            raise RuntimeError("R2 independent restore verification failed")
        r2_semantic = verify_semantic_contract(r2_restore, semantic_contract)
        if semantic_contract and r2_semantic["state"] != "SEMANTIC_VERIFIED":
            raise RuntimeError("R2 semantic restore verification failed")
        transport.copy_remote(r2_target, oci_target)
        transport.copy_to_local(oci_target, oci_restore)
        oci_verification = verify_restore(manifest, oci_restore)
        if oci_verification["state"] != "RESTORE_VERIFIED":
            raise RuntimeError("OCI independent restore verification failed")
        oci_semantic = verify_semantic_contract(oci_restore, semantic_contract)
        if semantic_contract and oci_semantic["state"] != "SEMANTIC_VERIFIED":
            raise RuntimeError("OCI semantic restore verification failed")

    evidence = {
        "schema_version": 1,
        "state": "BACKUP_RESTORE_VERIFIED",
        "manifest": manifest,
        "r2": {"target": r2_target, "verification": r2_verification, "semantic": r2_semantic},
        "oci": {"target": oci_target, "verification": oci_verification, "semantic": oci_semantic},
        "verified_at": _now(),
    }
    evidence_path.parent.mkdir(parents=True, exist_ok=True)
    evidence_path.write_text(json.dumps(evidence, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return evidence
