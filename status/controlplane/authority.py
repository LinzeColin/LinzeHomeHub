"""No-clone Private-Database authority adapter with verified readback.

Only completed structured facts are written through the canonical
``private_db_client.py put/get`` contract. The module never clones
Private-Database, never invokes Git, and reports an event as sent only after
byte-for-byte readback succeeds.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
import tempfile
from typing import Any, Callable, Iterable, Mapping, Sequence


class AuthoritySyncError(RuntimeError):
    """Raised when configuration or the authority client contract is invalid."""


_ALLOWED_AREAS = {
    "Private-KMDatabase",
    "Private-MetaDatabase",
    "Private-AgentDatabase",
}
_SAFE_PART = re.compile(r"[^A-Za-z0-9._:-]+")


def _safe_part(value: Any, field: str) -> str:
    raw = str(value).strip()
    safe = _SAFE_PART.sub("-", raw).strip(".-")[:180]
    if not safe or safe in {".", ".."}:
        raise AuthoritySyncError(f"invalid {field}")
    return safe


def _safe_prefix(value: str) -> PurePosixPath:
    prefix = PurePosixPath(str(value).strip("/"))
    if prefix.is_absolute() or not prefix.parts or any(part in {"", ".", ".."} for part in prefix.parts):
        raise AuthoritySyncError("invalid authority prefix")
    return prefix


def _date_path(event: Mapping[str, Any], *, prefix: str) -> PurePosixPath:
    raw = str(event.get("completed_at") or datetime.now(timezone.utc).isoformat())
    date = raw[:10]
    try:
        datetime.strptime(date, "%Y-%m-%d")
    except ValueError as exc:
        raise AuthoritySyncError("invalid completed_at date") from exc
    event_type = _safe_part(event.get("fact_type", "status.fact"), "fact_type")
    if "event_id" not in event:
        raise AuthoritySyncError("event_id missing")
    event_id = _safe_part(event["event_id"], "event_id")
    return _safe_prefix(prefix) / date / event_type / f"{event_id}.json"


def _canonical_bytes(event: Mapping[str, Any]) -> bytes:
    return (
        json.dumps(dict(event), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


@dataclass(frozen=True)
class ClientResult:
    returncode: int
    stdout: str
    stderr: str


Runner = Callable[..., subprocess.CompletedProcess[str]]


def _run_client(
    client_path: Path,
    args: Sequence[str],
    *,
    runner: Runner = subprocess.run,
    timeout: int = 180,
) -> ClientResult:
    command = [sys.executable, str(client_path), *map(str, args)]
    completed = runner(
        command,
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
    )
    return ClientResult(
        returncode=int(completed.returncode),
        stdout=str(completed.stdout or ""),
        stderr=str(completed.stderr or ""),
    )


def _declares_verb(text: str, verb: str) -> bool:
    """True only when ``verb`` appears as a standalone command token.

    A plain substring test is not good enough here: ``--output`` contains
    ``put``, so any client exposing an ``--output`` flag would be accepted as
    satisfying the put/get contract even when it cannot write at all. The
    lookarounds reject both embedded matches (``output``) and flag spellings
    (``--put``), leaving only bare subcommand tokens.
    """

    return re.search(rf"(?<![\w-]){re.escape(verb)}(?![\w-])", text) is not None


def validate_client_contract(
    client_path: Path,
    *,
    runner: Runner = subprocess.run,
) -> dict[str, Any]:
    """Confirm that the current client exposes the required no-clone contract."""

    path = Path(client_path).expanduser().resolve()
    if not path.is_file():
        raise AuthoritySyncError(f"private_db_client.py not found: {path}")
    result = _run_client(path, ["--help"], runner=runner)
    text = f"{result.stdout}\n{result.stderr}".lower()
    required = {verb: _declares_verb(text, verb) for verb in ("put", "get")}
    if result.returncode != 0 or not all(required.values()):
        raise AuthoritySyncError("private_db_client.py lacks required put/get commands")
    return {
        "state": "CLIENT_CONTRACT_VERIFIED",
        "path": str(path),
        "commands": required,
        "client_sha256": sha256(path.read_bytes()).hexdigest(),
    }


def _validate_client(client_path: Path, area: str) -> Path:
    path = Path(client_path).expanduser().resolve()
    if area not in _ALLOWED_AREAS:
        raise AuthoritySyncError(f"unsupported Private-Database area: {area}")
    if not path.is_file():
        raise AuthoritySyncError(f"private_db_client.py not found: {path}")
    return path


def sync_events(
    private_db_client: Path,
    events: Iterable[Mapping[str, Any]],
    *,
    area: str = "Private-MetaDatabase",
    prefix: str = "facts/status",
    runner: Runner = subprocess.run,
) -> dict[str, Any]:
    """Write each event idempotently and verify it through an independent get.

    Failures are isolated. Callers may mark only ``sent_event_ids`` as sent.
    """

    client = _validate_client(private_db_client, area)
    materialized = [dict(event) for event in events]
    if not materialized:
        return {
            "schema_version": 2,
            "state": "NO_NEW_FACT",
            "area": area,
            "prefix": str(_safe_prefix(prefix)),
            "sent_event_ids": [],
            "failed_event_ids": [],
            "items": [],
        }

    items: list[dict[str, Any]] = []
    sent: list[str] = []
    failed: list[str] = []

    with tempfile.TemporaryDirectory(prefix="status-authority-") as temp_dir:
        temp = Path(temp_dir)
        for position, event in enumerate(materialized):
            event_id = str(event.get("event_id") or f"missing-{position}")
            try:
                relative = _date_path(event, prefix=prefix)
                payload = _canonical_bytes(event)
                payload_hash = sha256(payload).hexdigest()
                local = temp / f"write-{position}.json"
                readback = temp / f"readback-{position}.json"
                local.write_bytes(payload)

                put = _run_client(
                    client,
                    ["put", area, relative.as_posix(), str(local)],
                    runner=runner,
                )
                if put.returncode != 0:
                    raise AuthoritySyncError(
                        f"put failed ({put.returncode}): {put.stderr.strip() or put.stdout.strip()}"
                    )

                get = _run_client(
                    client,
                    ["get", area, relative.as_posix(), str(readback)],
                    runner=runner,
                )
                if get.returncode != 0:
                    raise AuthoritySyncError(
                        f"readback failed ({get.returncode}): {get.stderr.strip() or get.stdout.strip()}"
                    )
                if not readback.is_file():
                    raise AuthoritySyncError("readback client returned success without an output file")

                readback_hash = sha256(readback.read_bytes()).hexdigest()
                if readback_hash != payload_hash:
                    raise AuthoritySyncError("readback digest mismatch")

                sent.append(event_id)
                items.append(
                    {
                        "event_id": event_id,
                        "state": "READBACK_VERIFIED",
                        "area": area,
                        "relative_path": relative.as_posix(),
                        "payload_sha256": payload_hash,
                        "readback_sha256": readback_hash,
                    }
                )
            except Exception as exc:  # event remains retryable
                failed.append(event_id)
                items.append(
                    {
                        "event_id": event_id,
                        "state": "FAILED",
                        "error_code": "AUTHORITY_READBACK_FAILED",
                        "detail": str(exc)[:500],
                    }
                )

    state = "SYNCED" if not failed else ("PARTIAL_FAILURE" if sent else "FAILED")
    return {
        "schema_version": 2,
        "state": state,
        "area": area,
        "prefix": str(_safe_prefix(prefix)),
        "sent_event_ids": sent,
        "failed_event_ids": failed,
        "items": items,
    }
