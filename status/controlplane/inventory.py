"""Three-inventory reconciliation with explicit coverage semantics."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from .models import InventoryRecord, ReconciledInventory, records_by_id


@dataclass(frozen=True)
class InventorySnapshot:
    records: tuple[InventoryRecord, ...]
    available: bool
    observed_revision: str
    reason: str = ""


@dataclass(frozen=True)
class InventoryResult:
    items: tuple[ReconciledInventory, ...]
    coverage_health: str
    runtime_health: str
    reasons: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "coverage_health": self.coverage_health,
            "runtime_health": self.runtime_health,
            "reasons": list(self.reasons),
            "items": [item.to_dict() for item in self.items],
        }


def _runtime_rollup(states: Iterable[str]) -> str:
    values = {str(value).upper() for value in states}
    if not values:
        return "UNKNOWN"
    if "FAILED" in values or "DOWN" in values:
        return "FAILED"
    if values & {"DEGRADED", "STALE", "UNKNOWN", "UNAVAILABLE"}:
        return "DEGRADED"
    if values <= {"HEALTHY", "UP", "OK"}:
        return "HEALTHY"
    return "UNKNOWN"


def reconcile_inventories(
    declared: InventorySnapshot,
    source: InventorySnapshot,
    runtime: InventorySnapshot,
) -> InventoryResult:
    maps = {
        "declared": records_by_id(declared.records),
        "source": records_by_id(source.records),
        "runtime": records_by_id(runtime.records),
    }
    all_ids = sorted(set().union(*(mapping.keys() for mapping in maps.values())))
    items: list[ReconciledInventory] = []
    coverage_reasons: list[str] = []

    unavailable = [
        label for label, snapshot in (
            ("declared", declared), ("source", source), ("runtime", runtime)
        ) if not snapshot.available
    ]
    if unavailable:
        coverage_reasons.append("inventory_unavailable:" + ",".join(unavailable))

    for entity_id in all_ids:
        d = maps["declared"].get(entity_id)
        s = maps["source"].get(entity_id)
        r = maps["runtime"].get(entity_id)
        name = (d or s or r).name
        reasons: list[str] = []

        if not all((declared.available, source.available, runtime.available)):
            state = "INVENTORY_UNAVAILABLE"
        elif r and not d and not s:
            state = "DEPLOYED_UNREGISTERED"
            reasons.append("runtime exists without declaration or source registration")
        elif s and not d:
            state = "REPOSITORY_UNREGISTERED" if not r else "DEPLOYED_UNREGISTERED"
            reasons.append("source exists without declaration")
        elif d and d.lifecycle.lower() == "retired" and (s or r):
            state = "RETIRED_BUT_OBSERVED"
            reasons.append("retired entity remains observable")
        elif d and not s and not r:
            state = "DECLARED_NOT_DEPLOYED"
            reasons.append("declared entity has no source or runtime observation")
        elif d and s and not r:
            state = "DECLARED_UNMONITORED"
            reasons.append("declared source has no runtime observation")
        elif r and d:
            runtime_state = r.runtime_state.upper()
            state = "DECLARED_OBSERVED_HEALTHY" if runtime_state in {"HEALTHY", "UP", "OK"} else "DECLARED_OBSERVED_DEGRADED"
        else:
            state = "COVERAGE_UNKNOWN"
            reasons.append("inventory relationship is not classifiable")

        runtime_state = r.runtime_state.upper() if r else "UNKNOWN"
        evidence = []
        for record in (d, s, r):
            if record:
                ref = record.metadata.get("evidence_ref")
                if ref:
                    evidence.append(str(ref))
        items.append(ReconciledInventory(
            entity_id=entity_id,
            name=name,
            state=state,
            declared=d is not None,
            source_observed=s is not None,
            runtime_observed=r is not None,
            runtime_state=runtime_state,
            evidence_refs=tuple(sorted(set(evidence))),
            reasons=tuple(reasons),
        ))

    difference_states = {
        item.state for item in items
        if item.state not in {"DECLARED_OBSERVED_HEALTHY"}
    }
    if unavailable or not all_ids:
        coverage = "UNKNOWN"
    elif difference_states:
        coverage = "DEGRADED"
    else:
        coverage = "HEALTHY"

    runtime_health = _runtime_rollup(
        item.runtime_state for item in items if item.runtime_observed
    )
    if not any(item.runtime_observed for item in items):
        runtime_health = "UNKNOWN"

    return InventoryResult(
        items=tuple(items),
        coverage_health=coverage,
        runtime_health=runtime_health,
        reasons=tuple(coverage_reasons),
    )


def snapshot_from_json(value: Mapping[str, Any], source_name: str) -> InventorySnapshot:
    records = []
    for raw in value.get("records", []):
        records.append(InventoryRecord(
            entity_id=str(raw["entity_id"]),
            kind=str(raw.get("kind", "project")),
            name=str(raw.get("name", raw["entity_id"])),
            source=source_name,
            lifecycle=str(raw.get("lifecycle", "active")),
            runtime_state=str(raw.get("runtime_state", "UNKNOWN")),
            metadata=dict(raw.get("metadata", {})),
        ))
    return InventorySnapshot(
        records=tuple(records),
        available=bool(value.get("available", False)),
        observed_revision=str(value.get("observed_revision", "UNKNOWN")),
        reason=str(value.get("reason", "")),
    )
