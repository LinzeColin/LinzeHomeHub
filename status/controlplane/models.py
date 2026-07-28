"""Canonical data types shared by collectors, projections and tests."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from hashlib import sha256
import json
import re
from typing import Any, Iterable, Mapping


_ID_PART = re.compile(r"[^a-z0-9._-]+")


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def content_hash(value: Any) -> str:
    return sha256(canonical_json(value).encode("utf-8")).hexdigest()


def stable_id(kind: str, *parts: str) -> str:
    """Create a readable deterministic ID with a collision-resistant suffix."""
    normalized = []
    for raw in parts:
        item = _ID_PART.sub("-", str(raw).strip().lower()).strip("-._")
        if item:
            normalized.append(item)
    basis = ":".join([kind, *normalized])
    readable = "-".join(normalized)[:72] or "unnamed"
    suffix = sha256(basis.encode("utf-8")).hexdigest()[:10]
    return f"{kind}:{readable}:{suffix}"


class TruthValue(str, Enum):
    TRUE = "TRUE"
    FALSE = "FALSE"
    UNKNOWN = "UNKNOWN"


class AggregateState(str, Enum):
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    FAILED = "FAILED"
    UNKNOWN = "UNKNOWN"
    STALE = "STALE"
    UNVERIFIED = "UNVERIFIED"
    BLOCKED = "BLOCKED"
    RETIRED = "RETIRED"


class ReconcileState(str, Enum):
    ALREADY_SATISFIED = "ALREADY_SATISFIED"
    APPLY_CLEAN = "APPLY_CLEAN"
    ADAPT_REQUIRED = "ADAPT_REQUIRED"
    UPSTREAM_EQUIVALENT = "UPSTREAM_EQUIVALENT"
    CONTRACT_CONFLICT = "CONTRACT_CONFLICT"
    ENVIRONMENT_BLOCKED = "ENVIRONMENT_BLOCKED"
    OBSOLETE = "OBSOLETE"


@dataclass(frozen=True)
class Condition:
    type: str
    status: TruthValue
    desired_revision: str
    observed_revision: str
    reason: str
    message: str
    source: str
    evidence_refs: tuple[str, ...] = ()
    last_observed_at: str = field(default_factory=utc_now)
    last_transition_at: str = field(default_factory=utc_now)
    expires_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["status"] = self.status.value
        value["evidence_refs"] = list(self.evidence_refs)
        return value


@dataclass(frozen=True)
class InventoryRecord:
    entity_id: str
    kind: str
    name: str
    source: str
    lifecycle: str = "active"
    runtime_state: str = "UNKNOWN"
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "entity_id": self.entity_id,
            "kind": self.kind,
            "name": self.name,
            "source": self.source,
            "lifecycle": self.lifecycle,
            "runtime_state": self.runtime_state,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class ReconciledInventory:
    entity_id: str
    name: str
    state: str
    declared: bool
    source_observed: bool
    runtime_observed: bool
    runtime_state: str
    evidence_refs: tuple[str, ...] = ()
    reasons: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["evidence_refs"] = list(self.evidence_refs)
        value["reasons"] = list(self.reasons)
        return value


@dataclass(frozen=True)
class EvidenceBinding:
    evidence_id: str
    subject_commit: str
    lock_hash: str
    contract_hash: str
    artifact_digest: str
    environment_hash: str
    verdict: str
    verified_at: str
    expires_at: str | None
    oracle: str
    evidence_refs: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["evidence_refs"] = list(self.evidence_refs)
        return value


@dataclass(frozen=True)
class CapabilityState:
    capability_id: str
    name: str
    declared: bool
    implemented: bool
    verified: str
    packaged: bool
    deployed: bool
    operational: str
    recoverable: str
    evidence_refs: tuple[str, ...] = ()

    def aggregate(self) -> str:
        if not self.declared:
            return "UNKNOWN"
        if not self.implemented:
            return "DECLARED"
        if self.verified == "FAILED" or self.operational == "FAILED" or self.recoverable == "FAILED":
            return "FAILED"
        if self.verified in {"STALE", "UNVERIFIED", "UNKNOWN"}:
            return "VERIFIED_STALE" if self.verified == "STALE" else "IMPLEMENTED_UNVERIFIED"
        if self.packaged and self.deployed and self.operational == "HEALTHY" and self.recoverable == "VERIFIED":
            return "VERIFIED_FRESH"
        if self.deployed and self.operational == "DEGRADED":
            return "DEGRADED"
        return "IMPLEMENTED_UNVERIFIED"

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["aggregate_state"] = self.aggregate()
        value["evidence_refs"] = list(self.evidence_refs)
        return value


def records_by_id(records: Iterable[InventoryRecord]) -> dict[str, InventoryRecord]:
    result: dict[str, InventoryRecord] = {}
    for record in records:
        if record.entity_id in result:
            raise ValueError(f"duplicate entity_id: {record.entity_id}")
        result[record.entity_id] = record
    return result
