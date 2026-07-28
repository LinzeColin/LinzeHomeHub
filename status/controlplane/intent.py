"""Deterministic three-layer intent compiler for development-time agents."""

from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
from typing import Any, Mapping


class IntentError(ValueError):
    pass


_SENSITIVE_KEYS = {
    "api_key", "authorization", "cookie", "credential", "password",
    "private_key", "secret", "token",
}


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def content_hash(value: Any) -> str:
    return sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _mapping(value: Mapping[str, Any], name: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise IntentError(f"{name} must be an object")
    return dict(value)


def _scan_sensitive_keys(value: Any, path: str = "$") -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            normalized = str(key).strip().lower()
            if normalized in _SENSITIVE_KEYS or normalized.endswith("_token") or normalized.endswith("_secret"):
                raise IntentError(f"secret-bearing key is forbidden in intent: {path}.{key}")
            _scan_sensitive_keys(child, f"{path}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            _scan_sensitive_keys(child, f"{path}[{index}]")


def _list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return list(value)
    raise IntentError("intent list field must be an array")


def compile_run_intent(
    owner_intent: Mapping[str, Any],
    project_intent: Mapping[str, Any],
    task_contract: Mapping[str, Any],
    *,
    created_at: str | None = None,
) -> dict[str, Any]:
    """Compile a minimal immutable bundle instead of concatenating a super prompt."""

    owner = _mapping(owner_intent, "owner_intent")
    project = _mapping(project_intent, "project_intent")
    task = _mapping(task_contract, "task_contract")
    _scan_sensitive_keys(owner)
    _scan_sensitive_keys(project)
    _scan_sensitive_keys(task)

    project_id = str(project.get("project_id") or "").strip()
    task_id = str(task.get("task_id") or "").strip()
    goal = str(task.get("goal") or "").strip()
    if not project_id or not task_id or not goal:
        raise IntentError("project_id, task_id and goal are required")

    source_hashes = {
        "owner_intent_sha256": content_hash(owner),
        "project_intent_sha256": content_hash(project),
        "task_contract_sha256": content_hash(task),
    }
    body = {
        "schema_version": 1,
        "project_id": project_id,
        "task_id": task_id,
        "goal": goal,
        "owner_contract": {
            "principles": _list(owner.get("principles")),
            "hard_constraints": _list(owner.get("hard_constraints")),
            "agent_responsibilities": dict(owner.get("agent_responsibilities") or {}),
            "risk_policy": dict(owner.get("risk_policy") or {}),
        },
        "project_contract": {
            "target_repository": str(project.get("target_repository") or ""),
            "target_area": str(project.get("target_area") or ""),
            "product_version": str(project.get("product_version") or ""),
            "scope": _list(project.get("scope")),
            "non_goals": _list(project.get("non_goals")),
            "runtime_constraints": _list(project.get("runtime_constraints")),
            "authority_model": dict(project.get("authority_model") or {}),
        },
        "task_contract": {
            "scope": _list(task.get("scope")),
            "non_goals": _list(task.get("non_goals")),
            "allowed_paths": _list(task.get("allowed_paths")),
            "forbidden_paths": _list(task.get("forbidden_paths")),
            "acceptance_ids": _list(task.get("acceptance_ids")),
            "stop_conditions": _list(task.get("stop_conditions")),
            "rollback": str(task.get("rollback") or ""),
            "skill_allowlist": _list(task.get("skill_allowlist")),
            "resource_ceiling": dict(task.get("resource_ceiling") or {}),
        },
        "source_hashes": source_hashes,
    }
    body_hash = content_hash(body)
    bundle = dict(body)
    bundle["intent_id"] = f"intent:{project_id}:{task_id}:{body_hash[:12]}"
    bundle["intent_sha256"] = content_hash(bundle)
    bundle["created_at"] = created_at or datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    return bundle


def verify_bundle(bundle: Mapping[str, Any]) -> bool:
    value = dict(bundle)
    claimed = str(value.pop("intent_sha256", ""))
    value.pop("created_at", None)
    return bool(claimed) and claimed == content_hash(value)
