"""Independent deterministic Gate bound to an immutable Candidate subject."""

from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
from typing import Any, Mapping


_ALLOWED_OBSERVATION_STATES = {"PASS", "FAIL", "UNKNOWN", "NOT_RUN", "WAIVED"}


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def evaluate(contract: Mapping[str, Any], observations: Mapping[str, Any], *, verified_at: str | None = None) -> dict[str, Any]:
    required_contract = ("run_id", "subject_commit", "artifact_digest", "acceptance_hash", "verifier_version", "required_oracles")
    missing = [key for key in required_contract if key not in contract or contract[key] in (None, "", [])]
    if missing:
        raise ValueError(f"gate contract missing fields: {','.join(missing)}")

    exact_fields = ("run_id", "subject_commit", "artifact_digest", "acceptance_hash")
    binding_errors = [key for key in exact_fields if str(observations.get(key) or "") != str(contract[key])]
    required_oracles = [str(value) for value in contract["required_oracles"]]
    observed_oracles = dict(observations.get("oracles") or {})
    normalized: dict[str, str] = {}
    for oracle_id in required_oracles:
        state = str(observed_oracles.get(oracle_id) or "NOT_RUN").upper()
        normalized[oracle_id] = state if state in _ALLOWED_OBSERVATION_STATES else "UNKNOWN"

    if binding_errors:
        verdict = "BLOCKED"
        reason = "SUBJECT_BINDING_MISMATCH"
    elif any(state == "FAIL" for state in normalized.values()):
        verdict = "FAIL"
        reason = "ORACLE_FAILURE"
    elif any(state != "PASS" for state in normalized.values()):
        verdict = "BLOCKED"
        reason = "ORACLE_INCOMPLETE"
    else:
        verdict = "PASS"
        reason = "ALL_FROZEN_ORACLES_PASSED"

    timestamp = verified_at or _now()
    body = {
        "schema_version": 1,
        "run_id": str(contract["run_id"]),
        "subject_commit": str(contract["subject_commit"]),
        "artifact_digest": str(contract["artifact_digest"]),
        "acceptance_hash": str(contract["acceptance_hash"]),
        "verifier_version": str(contract["verifier_version"]),
        "verdict": verdict,
        "reason": reason,
        "observations": {
            "oracles": normalized,
            "binding_errors": binding_errors,
            "evidence_refs": list(observations.get("evidence_refs") or []),
        },
        "verified_at": timestamp,
    }
    body["verdict_id"] = "gate:" + sha256(_canonical(body).encode("utf-8")).hexdigest()[:32]
    return body
