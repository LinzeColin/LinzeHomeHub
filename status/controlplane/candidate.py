"""Route session-derived knowledge into review-only candidate dossiers."""

from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
from typing import Any, Mapping, Sequence


_TYPES = {"SKILL", "ADR", "PROFILE", "CONVENTION", "FAILURE_RUNBOOK"}


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def classify(signals: Mapping[str, Any]) -> str:
    explicit = str(signals.get("candidate_type") or "").upper()
    if explicit in _TYPES:
        return explicit
    if signals.get("failure_recovery"):
        return "FAILURE_RUNBOOK"
    if signals.get("architecture_decision"):
        return "ADR"
    if signals.get("owner_preference"):
        return "PROFILE"
    if signals.get("repository_specific"):
        return "CONVENTION"
    return "SKILL"


def build_candidate(
    *,
    run_id: str,
    title: str,
    signals: Mapping[str, Any],
    evidence_refs: Sequence[str],
    created_at: str | None = None,
) -> dict[str, Any]:
    if not run_id.strip() or not title.strip():
        raise ValueError("run_id and title are required")
    references = sorted({str(value).strip() for value in evidence_refs if str(value).strip()})
    if not references:
        raise ValueError("at least one evidence reference is required")
    body = {
        "schema_version": 1,
        "run_id": run_id,
        "candidate_type": classify(signals),
        "title": title.strip()[:240],
        "evidence_refs": references,
        "state": "PROPOSED",
        "requires_owner_approval": True,
        "created_at": created_at or _now(),
        "approved_at": None,
    }
    body["candidate_id"] = "candidate:" + sha256(_canonical(body).encode("utf-8")).hexdigest()[:32]
    return body
