"""Subject-bound evidence lease evaluation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping

from .models import EvidenceBinding


@dataclass(frozen=True)
class CurrentSubject:
    subject_commit: str
    lock_hash: str
    contract_hash: str
    artifact_digest: str
    environment_hash: str


@dataclass(frozen=True)
class EvidenceEvaluation:
    state: str
    reasons: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {"state": self.state, "reasons": list(self.reasons)}


def _parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def evaluate_evidence(
    binding: EvidenceBinding,
    current: CurrentSubject,
    *,
    now: datetime | None = None,
) -> EvidenceEvaluation:
    reasons: list[str] = []
    if binding.verdict != "PASS":
        return EvidenceEvaluation("FAILED" if binding.verdict == "FAIL" else "UNVERIFIED", (f"verdict:{binding.verdict}",))

    comparisons = {
        "subject_commit_changed": (binding.subject_commit, current.subject_commit),
        "lock_hash_changed": (binding.lock_hash, current.lock_hash),
        "contract_hash_changed": (binding.contract_hash, current.contract_hash),
        "artifact_digest_changed": (binding.artifact_digest, current.artifact_digest),
        "environment_hash_changed": (binding.environment_hash, current.environment_hash),
    }
    for reason, (old, new) in comparisons.items():
        if old != new:
            reasons.append(reason)

    instant = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if binding.expires_at and instant >= _parse_time(binding.expires_at):
        reasons.append("lease_expired")

    return EvidenceEvaluation("STALE", tuple(reasons)) if reasons else EvidenceEvaluation("VERIFIED_FRESH", ())


def binding_from_json(value: Mapping[str, Any]) -> EvidenceBinding:
    return EvidenceBinding(
        evidence_id=str(value["evidence_id"]),
        subject_commit=str(value["subject_commit"]),
        lock_hash=str(value["lock_hash"]),
        contract_hash=str(value["contract_hash"]),
        artifact_digest=str(value["artifact_digest"]),
        environment_hash=str(value["environment_hash"]),
        verdict=str(value["verdict"]),
        verified_at=str(value["verified_at"]),
        expires_at=str(value["expires_at"]) if value.get("expires_at") else None,
        oracle=str(value["oracle"]),
        evidence_refs=tuple(str(x) for x in value.get("evidence_refs", [])),
    )
