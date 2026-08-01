# STATUS_AGENT_V3_MANAGED: v0.0.0.3
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

from .gate import GATE_TRUST_MODE, verify_verdict
from .subject import compare_current, subject_fingerprint

CHINESE_LABELS = {
    "READY": "就绪", "BLOCKED": "阻断", "UNKNOWN": "未知", "STALE": "证据过期",
    "FAILED": "失败", "DEGRADED": "降级", "UNVERIFIED": "未验证",
}


DEFAULT_EVIDENCE_TTL_MINUTES = 30
MAX_EVIDENCE_TTL_MINUTES = 24 * 60


def _parse_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def _utc_seconds(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat()


def _ttl_minutes(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= MAX_EVIDENCE_TTL_MINUTES:
        raise ValueError(f"ttl_minutes must be an integer between 1 and {MAX_EVIDENCE_TTL_MINUTES}")
    return value


def _evidence_freshness(verdict: Mapping[str, Any] | None, *, now: datetime, ttl_minutes: int) -> dict[str, Any]:
    if not verdict:
        return {"state": "STALE", "reason": "VERDICT_MISSING", "verified_at": None, "expires_at": None, "ttl_minutes": ttl_minutes}
    verified_at = _parse_timestamp(verdict.get("verified_at"))
    if verified_at is None:
        return {"state": "STALE", "reason": "VERDICT_TIME_MISSING_OR_INVALID", "verified_at": None, "expires_at": None, "ttl_minutes": ttl_minutes}
    expires_at = verified_at + timedelta(minutes=ttl_minutes)
    if verified_at > now:
        return {"state": "STALE", "reason": "VERDICT_TIME_IN_FUTURE", "verified_at": _utc_seconds(verified_at), "expires_at": _utc_seconds(expires_at), "ttl_minutes": ttl_minutes}
    if now >= expires_at:
        return {"state": "STALE", "reason": "VERDICT_EXPIRED", "verified_at": _utc_seconds(verified_at), "expires_at": _utc_seconds(expires_at), "ttl_minutes": ttl_minutes}
    return {"state": "CURRENT", "reason": "CURRENT", "verified_at": _utc_seconds(verified_at), "expires_at": _utc_seconds(expires_at), "ttl_minutes": ttl_minutes}


def derive_projection(*, accepted_subject: Mapping[str, Any] | None, current_subject: Mapping[str, Any] | None, verdict: Mapping[str, Any] | None, trust_root, observed_at: str | None = None, ttl_minutes: int = DEFAULT_EVIDENCE_TTL_MINUTES, now: datetime | None = None) -> dict[str, Any]:
    ttl_minutes = _ttl_minutes(ttl_minutes)
    current_time = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if observed_at is not None and _parse_timestamp(observed_at) is None:
        raise ValueError("observed_at must be an RFC3339 timestamp with timezone")
    reasons: list[str] = []
    state = "UNKNOWN"
    signature = {"state": "FAIL"}
    changed_fields: list[str] = []
    subject_freshness = {"state": "STALE", "changed_fields": ["missing_subject"]}
    if not accepted_subject or not current_subject:
        reasons.append("SUBJECT_MISSING")
        changed_fields.append("missing_subject")
    else:
        subject_freshness = compare_current(accepted_subject, current_subject)
        changed_fields.extend(subject_freshness["changed_fields"])
        if subject_freshness["state"] != "CURRENT":
            reasons.append("SUBJECT_STALE")
    if not verdict:
        reasons.append("VERDICT_MISSING")
    else:
        signature = verify_verdict(verdict, trust_root)
        if signature["state"] != "PASS":
            reasons.append("VERDICT_UNSIGNED_OR_TAMPERED")
        if verdict.get("verdict") != "PASS":
            reasons.append("VERDICT_NOT_PASS")
        if accepted_subject and verdict.get("subject_id") != accepted_subject.get("subject_id"):
            reasons.append("VERDICT_SUBJECT_MISMATCH")
        if accepted_subject and verdict.get("subject_sha256") != subject_fingerprint(accepted_subject):
            reasons.append("VERDICT_SUBJECT_DIGEST_MISMATCH")
        if verdict.get("trust_mode") != GATE_TRUST_MODE:
            reasons.append("UNTRUSTED_GATE_MODE")
    evidence = _evidence_freshness(verdict, now=current_time, ttl_minutes=ttl_minutes)
    if evidence["state"] != "CURRENT":
        changed_fields.append("verdict_verified_at")
        if evidence["reason"] != "VERDICT_MISSING":
            reasons.append(str(evidence["reason"]))
    freshness = {
        "state": "CURRENT" if subject_freshness["state"] == "CURRENT" and evidence["state"] == "CURRENT" else "STALE",
        "changed_fields": sorted(set(changed_fields)),
        "evidence": evidence,
    }
    if not reasons:
        state = "READY"
    elif any(reason in reasons for reason in ("VERDICT_NOT_PASS", "VERDICT_UNSIGNED_OR_TAMPERED", "VERDICT_SUBJECT_MISMATCH", "VERDICT_SUBJECT_DIGEST_MISMATCH", "UNTRUSTED_GATE_MODE")):
        state = "BLOCKED"
    elif any(reason in reasons for reason in ("SUBJECT_STALE", "VERDICT_TIME_MISSING_OR_INVALID", "VERDICT_TIME_IN_FUTURE", "VERDICT_EXPIRED")):
        state = "STALE"
    body = {
        "schema_version": 3, "state": state, "state_zh": CHINESE_LABELS[state],
        "green_allowed": state == "READY", "reasons": reasons,
        "subject_id": accepted_subject.get("subject_id") if accepted_subject else None,
        "verdict_id": verdict.get("verdict_id") if verdict else None,
        "signature_state": signature.get("state"), "freshness": freshness,
        "bootstrap": False, "observed_at": observed_at or _utc_seconds(current_time),
        "truth_source": "Private-Database/R2/OCI receipts projected by LinzeHomeHub/status",
    }
    return body


def public_allowlist(value: Mapping[str, Any]) -> dict[str, Any]:
    allowed = {"schema_version", "state", "state_zh", "green_allowed", "reasons", "subject_id", "verdict_id", "signature_state", "freshness", "bootstrap", "observed_at", "truth_source"}
    projected = {key: value[key] for key in allowed if key in value and key != "freshness"}
    freshness = value.get("freshness")
    if isinstance(freshness, Mapping):
        safe_freshness = {key: freshness[key] for key in ("state", "changed_fields") if key in freshness}
        evidence = freshness.get("evidence")
        if isinstance(evidence, Mapping):
            safe_freshness["evidence"] = {key: evidence[key] for key in ("state", "reason", "verified_at", "expires_at", "ttl_minutes") if key in evidence}
        projected["freshness"] = safe_freshness
    return projected
