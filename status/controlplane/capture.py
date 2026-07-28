"""Normalize Codex/Claude hook events without persisting raw prompt content."""

from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
from typing import Any, Mapping

from .redaction import redact


_CONTENT_KEYS = {
    "content", "input", "output", "prompt", "transcript", "messages",
    "tool_input", "tool_output", "arguments", "result",
}


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _summarize_content(value: Any) -> dict[str, Any]:
    redacted, findings = redact(value)
    raw = _canonical_bytes(redacted)
    if isinstance(redacted, str):
        excerpt = redacted[:240]
    else:
        excerpt = json.dumps(redacted, ensure_ascii=False, sort_keys=True)[:240]
    return {
        "content_sha256": sha256(raw).hexdigest(),
        "size_bytes": len(raw),
        "redacted_excerpt": excerpt,
        "redaction_count": len(findings),
    }


def _safe_tree(value: Any, *, key: str = "") -> tuple[Any, int]:
    normalized_key = key.strip().lower()
    if normalized_key in _CONTENT_KEYS or normalized_key.endswith("_content"):
        summary = _summarize_content(value)
        return summary, int(summary["redaction_count"])
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        total = 0
        for child_key, child in value.items():
            safe, count = _safe_tree(child, key=str(child_key))
            result[str(child_key)] = safe
            total += count
        redacted, findings = redact(result)
        return redacted, total + len(findings)
    if isinstance(value, (list, tuple)):
        result = []
        total = 0
        for child in value:
            safe, count = _safe_tree(child)
            result.append(safe)
            total += count
        return result, total
    redacted, findings = redact(value)
    return redacted, len(findings)


def normalize_event(
    raw_event: Mapping[str, Any],
    *,
    provider: str,
    project_id: str,
    run_id: str,
    task_id: str,
    intent_hash: str,
    session_id: str,
    raw_object_ref: str | None = None,
) -> dict[str, Any]:
    provider = provider.strip().lower()
    if provider not in {"codex", "claude"}:
        raise ValueError("provider must be codex or claude")
    required = {"project_id": project_id, "run_id": run_id, "task_id": task_id, "intent_hash": intent_hash, "session_id": session_id}
    missing = [name for name, value in required.items() if not str(value).strip()]
    if missing:
        raise ValueError(f"missing event identity: {','.join(missing)}")

    safe_payload, count = _safe_tree(dict(raw_event))
    occurred_at = str(raw_event.get("occurred_at") or raw_event.get("timestamp") or _now())
    event_type = str(raw_event.get("event_type") or raw_event.get("hook_event_name") or raw_event.get("type") or "UNKNOWN")[:120]
    identity = {
        "provider": provider,
        "project_id": project_id,
        "run_id": run_id,
        "task_id": task_id,
        "intent_hash": intent_hash,
        "session_id": session_id,
        "event_type": event_type,
        "occurred_at": occurred_at,
        "safe_payload": safe_payload,
    }
    event_id = "agent-event:" + sha256(_canonical_bytes(identity)).hexdigest()[:32]
    return {
        "schema_version": 1,
        "event_id": event_id,
        "provider": provider,
        "project_id": project_id,
        "run_id": run_id,
        "task_id": task_id,
        "intent_hash": intent_hash,
        "session_id": session_id,
        "event_type": event_type,
        "occurred_at": occurred_at,
        "safe_payload": safe_payload,
        "raw_object_ref": raw_object_ref,
        "redaction_count": count,
        "adapter_state": "NORMALIZED_REDACTED",
        "created_at": _now(),
    }
