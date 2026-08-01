# STATUS_AGENT_V3_MANAGED: v0.0.0.3
from __future__ import annotations

from pathlib import Path
import re
from typing import Any, Mapping

from .canonical import digest, utc_now
from .gate import GATE_TRUST_MODE, verify_verdict
from .subject import subject_fingerprint, verify_subject

TYPES = {"SKILL", "ADR", "PROFILE", "CONVENTION", "FAILURE_RUNBOOK"}
REQUIRED_FIELDS = (
    "candidate_type", "title", "trigger_problem", "applicable_when", "not_applicable_when",
    "inputs", "outputs", "required_permissions", "method", "success_samples", "correction_samples",
    "failure_samples", "safety_risks", "rollback", "source_session_refs", "portability",
    "duplicate_or_alternative", "replay_benchmark", "confidence",
)
SESSION_RECEIPT_REF_RE = re.compile(r"^session-receipt:[0-9a-f]{64}$")


def _valid_session_receipt_refs(value: Any) -> bool:
    if not isinstance(value, list) or not value or not all(isinstance(item, str) and SESSION_RECEIPT_REF_RE.fullmatch(item) for item in value):
        return False
    return len(value) == len(set(value))


def _current_subject(value: Mapping[str, Any]) -> Mapping[str, Any]:
    current = value.get("current_subject") if isinstance(value, Mapping) else None
    if isinstance(current, Mapping):
        return current
    return value


def _signed_gate(value: Mapping[str, Any]) -> Mapping[str, Any]:
    nested = value.get("gate_verdict") if isinstance(value, Mapping) else None
    if isinstance(nested, Mapping):
        return nested
    return value


def build_dossier(*, run_id: str, subject: Mapping[str, Any], gate_verdict: Mapping[str, Any], trust_root: Path, fields: Mapping[str, Any]) -> dict[str, Any]:
    missing = [key for key in REQUIRED_FIELDS if fields.get(key) in (None, "", [])]
    if missing:
        raise ValueError("candidate dossier missing: " + ",".join(missing))
    candidate_type = str(fields["candidate_type"]).upper()
    if candidate_type not in TYPES:
        raise ValueError("invalid candidate_type")
    if not _valid_session_receipt_refs(fields.get("source_session_refs")):
        raise ValueError("candidate dossier requires unique session-receipt digest references")
    current_subject = dict(_current_subject(subject))
    subject_check = verify_subject(current_subject)
    if subject_check["state"] != "PASS":
        raise ValueError("subject is not canonical: " + ",".join(subject_check.get("failures") or []))
    if current_subject.get("stage") not in {"CANDIDATE", "ARTIFACT", "DEPLOYMENT", "RECOVERY"}:
        raise ValueError("candidate dossier requires candidate-or-later subject")
    signed_gate = _signed_gate(gate_verdict)
    gate_check = verify_verdict(signed_gate, Path(trust_root))
    if gate_check.get("state") != "PASS":
        raise ValueError("gate verdict signature invalid")
    if signed_gate.get("verdict") != "PASS":
        raise ValueError("gate verdict must be PASS")
    if signed_gate.get("trust_mode") != GATE_TRUST_MODE:
        raise ValueError("untrusted gate mode")
    if signed_gate.get("subject_id") != current_subject.get("subject_id"):
        raise ValueError("gate subject_id mismatch")
    if signed_gate.get("subject_sha256") != subject_fingerprint(current_subject):
        raise ValueError("gate subject digest mismatch")
    if str(signed_gate.get("run_id")) != str(run_id):
        raise ValueError("gate run_id mismatch")
    body = {
        "schema_version": 3,
        "run_id": str(run_id),
        "subject_id": str(current_subject["subject_id"]),
        "subject_sha256": subject_fingerprint(current_subject),
        "gate_verdict_id": str(signed_gate["verdict_id"]),
        "gate_signature_verified": True,
        "gate_trust_mode": GATE_TRUST_MODE,
        **dict(fields),
    }
    body["candidate_type"] = candidate_type
    body["state"] = "PROPOSED"
    body["owner_approval"] = None
    body["teleiosis_receipt"] = None
    body["verifier_receipt"] = None
    body["installable"] = False
    body["created_at"] = utc_now()
    body["candidate_id"] = "candidate:" + digest(body)
    return body


def attach_owner_approval(dossier: Mapping[str, Any], approval: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(dossier)
    if str(approval.get("candidate_id")) != str(result.get("candidate_id")) or approval.get("approved") is not True:
        raise ValueError("owner approval mismatch")
    result["owner_approval"] = dict(approval)
    result["state"] = "OWNER_APPROVED"
    result["installable"] = False
    return result


def attach_teleiosis(dossier: Mapping[str, Any], receipt: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(dossier)
    if not result.get("owner_approval"):
        raise ValueError("owner approval required first")
    if str(receipt.get("candidate_id")) != str(result.get("candidate_id")) or receipt.get("state") not in {"PASS", "READY_FOR_VERIFIER"}:
        raise ValueError("teleiosis receipt mismatch")
    result["teleiosis_receipt"] = dict(receipt)
    result["state"] = "TELEIOSIS_REVIEWED"
    result["installable"] = False
    return result


def attach_verifier(dossier: Mapping[str, Any], receipt: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(dossier)
    if not result.get("teleiosis_receipt"):
        raise ValueError("teleiosis receipt required first")
    if str(receipt.get("candidate_id")) != str(result.get("candidate_id")) or receipt.get("verdict") != "PASS":
        raise ValueError("verifier receipt mismatch")
    result["verifier_receipt"] = dict(receipt)
    result["state"] = "INSTALLABLE"
    result["installable"] = True
    result["promoted_at"] = utc_now()
    return result


def validate_dossier(dossier: Mapping[str, Any]) -> dict[str, Any]:
    missing = [
        key for key in (*REQUIRED_FIELDS, "run_id", "subject_id", "subject_sha256", "gate_verdict_id", "candidate_id")
        if dossier.get(key) in (None, "", [])
    ]
    errors: list[str] = []
    if missing:
        errors.append("MISSING:" + ",".join(missing))
    if str(dossier.get("candidate_type")) not in TYPES:
        errors.append("INVALID_TYPE")
    if dossier.get("gate_signature_verified") is not True or dossier.get("gate_trust_mode") != GATE_TRUST_MODE:
        errors.append("UNTRUSTED_GATE")
    if not _valid_session_receipt_refs(dossier.get("source_session_refs")):
        errors.append("INVALID_SESSION_RECEIPT_REFS")
    if dossier.get("installable") and not all(dossier.get(key) for key in ("owner_approval", "teleiosis_receipt", "verifier_receipt")):
        errors.append("UNAUTHORIZED_INSTALLABLE")
    return {"state": "PASS" if not errors else "BLOCKED", "errors": errors}
