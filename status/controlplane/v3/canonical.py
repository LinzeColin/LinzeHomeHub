# STATUS_AGENT_V3_MANAGED: v0.0.0.3
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import re
from typing import Any, Mapping

_HEX64 = re.compile(r"^[0-9a-f]{64}$")


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def canonical_bytes(value: Any) -> bytes:
    return (canonical_json(value) + "\n").encode("utf-8")


def digest(value: Any) -> str:
    data = value if isinstance(value, bytes) else canonical_json(value).encode("utf-8")
    return sha256(data).hexdigest()


def file_digest(path: Path) -> str:
    return sha256(Path(path).read_bytes()).hexdigest()


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def require_text(mapping: Mapping[str, Any], key: str) -> str:
    value = str(mapping.get(key) or "").strip()
    if not value:
        raise ValueError(f"missing {key}")
    return value


def require_sha256(value: Any, key: str) -> str:
    text = str(value or "").strip().lower()
    if not _HEX64.fullmatch(text):
        raise ValueError(f"{key} must be sha256 hex")
    return text


def is_relative_to(path: Path, root: Path) -> bool:
    try:
        Path(path).resolve().relative_to(Path(root).resolve())
        return True
    except ValueError:
        return False


def atomic_write(path: Path, data: bytes, mode: int = 0o600) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    with temp.open("wb") as handle:
        handle.write(data)
        handle.flush()
        import os
        os.fsync(handle.fileno())
    temp.chmod(mode)
    temp.replace(path)
