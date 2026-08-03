# STATUS_AGENT_V3_MANAGED: v0.0.0.3
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
import tempfile
from typing import Any, Iterable, Mapping, Sequence

_ALLOWED_AREAS = {"Private-KMDatabase", "Private-MetaDatabase", "Private-AgentDatabase"}
_SAFE_PART = re.compile(r"[^A-Za-z0-9._:-]+")


class AuthorityError(RuntimeError):
    pass


def _safe(value: Any, field: str) -> str:
    text = _SAFE_PART.sub("-", str(value).strip()).strip(".-")[:180]
    if not text or text in {".", ".."}:
        raise AuthorityError(f"invalid {field}")
    return text


def _declares_verb(text: str, verb: str) -> bool:
    return re.search(rf"(?<![\w-]){re.escape(verb)}(?![\w-])", text) is not None


def _run(client: Path, args: Sequence[str], timeout: int = 180) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, str(client), *map(str, args)], text=True, capture_output=True, timeout=timeout, check=False)


def validate_client(client_path: Path) -> dict[str, Any]:
    client = Path(client_path).expanduser().resolve()
    if not client.is_file():
        raise AuthorityError("private_db_client.py missing")
    result = _run(client, ["--help"])
    text = f"{result.stdout}\n{result.stderr}".lower()
    commands = {verb: _declares_verb(text, verb) for verb in ("put", "get")}
    if result.returncode != 0 or not all(commands.values()):
        raise AuthorityError("private_db_client.py lacks put/get")
    return {"state": "PASS", "commands": commands, "client_sha256": sha256(client.read_bytes()).hexdigest(), "no_clone": True}


def _relative_path(event: Mapping[str, Any], prefix: str) -> PurePosixPath:
    date = str(event.get("completed_at") or datetime.now(timezone.utc).isoformat())[:10]
    datetime.strptime(date, "%Y-%m-%d")
    return PurePosixPath(prefix.strip("/")) / date / _safe(event.get("fact_type", "status.fact"), "fact_type") / f"{_safe(event.get('event_id'), 'event_id')}.json"


def sync_events(client_path: Path, events: Iterable[Mapping[str, Any]], *, area: str = "Private-AgentDatabase", prefix: str = "facts/status-agent", timeout: int = 180) -> dict[str, Any]:
    if area not in _ALLOWED_AREAS:
        raise AuthorityError("unsupported area")
    client = Path(client_path).expanduser().resolve()
    validate_client(client)
    materialized = [dict(event) for event in events]
    if not materialized:
        return {"schema_version": 3, "state": "NO_NEW_FACT", "sent_event_ids": [], "failed_event_ids": [], "items": []}
    sent: list[str] = []
    failed: list[str] = []
    items: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="status-authority-v3-") as directory:
        root = Path(directory)
        for index, event in enumerate(materialized):
            event_id = str(event.get("event_id") or "")
            try:
                if not event_id:
                    raise AuthorityError("event_id missing")
                payload = (json.dumps(event, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
                payload_sha = sha256(payload).hexdigest()
                source = root / f"source-{index}.json"
                readback = root / f"readback-{index}.json"
                source.write_bytes(payload)
                relative = _relative_path(event, prefix).as_posix()
                put = _run(client, ["put", area, relative, str(source)], timeout)
                if put.returncode != 0:
                    raise AuthorityError("put failed")
                get = _run(client, ["get", area, relative, str(readback)], timeout)
                if get.returncode != 0 or not readback.is_file():
                    raise AuthorityError("get/readback failed")
                readback_sha = sha256(readback.read_bytes()).hexdigest()
                if readback_sha != payload_sha:
                    raise AuthorityError("readback digest mismatch")
                sent.append(event_id)
                items.append({"event_id": event_id, "state": "READBACK_VERIFIED", "relative_path": relative, "payload_sha256": payload_sha, "readback_sha256": readback_sha})
            except Exception as exc:
                failed.append(event_id or f"missing-{index}")
                items.append({"event_id": event_id or f"missing-{index}", "state": "FAILED", "reason": str(exc)[:300]})
    return {"schema_version": 3, "state": "SYNCED" if not failed else "PARTIAL_FAILURE" if sent else "FAILED", "area": area, "sent_event_ids": sent, "failed_event_ids": failed, "items": items}
