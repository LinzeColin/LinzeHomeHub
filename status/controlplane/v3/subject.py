# STATUS_AGENT_V3_MANAGED: v0.0.0.3
from __future__ import annotations

from typing import Any, Mapping, Sequence

from .canonical import canonical_bytes, digest, require_sha256, require_text, utc_now

STAGES = ("BASE", "CANDIDATE", "ARTIFACT", "DEPLOYMENT", "RECOVERY")
BASE_FIELDS = (
    "project_id", "run_id", "task_id", "integration_base_commit",
    "taskpack_sha256", "acceptance_sha256", "oracle_registry_sha256",
    "dependency_lock_sha256", "environment_sha256", "provider_contracts_sha256",
)
STAGE_FIELDS = {
    "BASE": (),
    "CANDIDATE": ("candidate_commit", "candidate_tree_sha256"),
    "ARTIFACT": ("artifact_sha256",),
    "DEPLOYMENT": ("deployment_sha256", "private_database_fact_version", "r2_object_version"),
    "RECOVERY": ("oci_backup_receipt_sha256",),
}
SHA_FIELDS = {
    "taskpack_sha256", "acceptance_sha256", "oracle_registry_sha256",
    "dependency_lock_sha256", "environment_sha256", "provider_contracts_sha256",
    "candidate_tree_sha256", "artifact_sha256", "deployment_sha256",
    "oci_backup_receipt_sha256",
}


def _stable(value: Mapping[str, Any]) -> dict[str, Any]:
    return {key: val for key, val in value.items() if key not in {"subject_id", "created_at"}}


def subject_fingerprint(value: Mapping[str, Any]) -> str:
    """Digest the exact canonical subject bytes used by Gate and dossiers."""
    return digest(dict(value))


def build_subject(stage: str, values: Mapping[str, Any], *, parent: Mapping[str, Any] | None = None, created_at: str | None = None) -> dict[str, Any]:
    stage = str(stage).upper()
    if stage not in STAGES:
        raise ValueError("invalid subject stage")
    if stage == "BASE" and parent is not None:
        raise ValueError("BASE cannot have parent")
    if stage != "BASE" and parent is None:
        raise ValueError("non-BASE subject requires parent")
    if parent is not None:
        pstage = require_text(parent, "stage")
        if pstage not in STAGES[:-1] or STAGES[STAGES.index(pstage) + 1] != stage:
            raise ValueError(f"invalid stage transition: {pstage}->{stage}")
        if require_text(parent, "subject_id") != "subject:" + digest(_stable(parent)):
            raise ValueError("parent subject_id invalid")
    body: dict[str, Any] = {"schema_version": 3, "stage": stage}
    for key in BASE_FIELDS:
        inherited = parent.get(key) if parent else None
        supplied = values.get(key, inherited)
        if inherited not in (None, "") and supplied != inherited:
            raise ValueError(f"immutable field changed: {key}")
        body[key] = require_text({key: supplied}, key)
        if key in SHA_FIELDS:
            require_sha256(body[key], key)
    body["parent_subject_id"] = require_text(parent, "subject_id") if parent else None
    if parent:
        for previous_stage in STAGES[1:STAGES.index(stage)]:
            for key in STAGE_FIELDS[previous_stage]:
                body[key] = require_text(parent, key)
    for key in STAGE_FIELDS[stage]:
        body[key] = require_text(values, key)
        if key in SHA_FIELDS:
            require_sha256(body[key], key)
    body["created_at"] = created_at or utc_now()
    body["subject_id"] = "subject:" + digest(_stable(body))
    return body


def verify_subject(item: Mapping[str, Any]) -> dict[str, Any]:
    stage = str(item.get("stage") or "")
    if stage not in STAGES:
        return {"state": "FAIL", "reason": "INVALID_STAGE"}
    expected_id = "subject:" + digest(_stable(item))
    failures: list[str] = []
    if item.get("subject_id") != expected_id:
        failures.append("SUBJECT_ID_INVALID")
    for key in BASE_FIELDS:
        if item.get(key) in (None, ""):
            failures.append(f"MISSING:{key}")
        elif key in SHA_FIELDS:
            try:
                require_sha256(item[key], key)
            except ValueError:
                failures.append(f"INVALID_SHA:{key}")
    for position in range(1, STAGES.index(stage) + 1):
        for key in STAGE_FIELDS[STAGES[position]]:
            if item.get(key) in (None, ""):
                failures.append(f"MISSING:{key}")
            elif key in SHA_FIELDS:
                try:
                    require_sha256(item[key], key)
                except ValueError:
                    failures.append(f"INVALID_SHA:{key}")
    return {
        "state": "PASS" if not failures else "FAIL",
        "failures": failures,
        "subject_id": item.get("subject_id"),
        "subject_sha256": subject_fingerprint(item),
    }


def verify_chain(chain: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    failures: list[dict[str, Any]] = []
    previous: Mapping[str, Any] | None = None
    for index, item in enumerate(chain):
        try:
            rebuilt = build_subject(str(item.get("stage")), item, parent=previous, created_at=str(item.get("created_at")))
            if rebuilt != dict(item):
                failures.append({"index": index, "reason": "NON_CANONICAL_OR_TAMPERED"})
        except Exception as exc:
            failures.append({"index": index, "reason": str(exc)})
        previous = item
    expected_stages = list(STAGES[:len(chain)])
    actual_stages = [str(item.get("stage") or "") for item in chain]
    if actual_stages != expected_stages:
        failures.append({"reason": "STAGE_SEQUENCE", "expected": expected_stages, "actual": actual_stages})
    return {"state": "PASS" if not failures else "FAIL", "failures": failures, "count": len(chain)}


def compare_current(accepted: Mapping[str, Any], current: Mapping[str, Any]) -> dict[str, Any]:
    fields = [*BASE_FIELDS, *sum((list(values) for values in STAGE_FIELDS.values()), [])]
    changed = [field for field in fields if accepted.get(field) != current.get(field)]
    if accepted.get("subject_id") != current.get("subject_id"):
        changed.append("subject_id")
    return {"state": "CURRENT" if not changed else "STALE", "changed_fields": sorted(set(changed))}
