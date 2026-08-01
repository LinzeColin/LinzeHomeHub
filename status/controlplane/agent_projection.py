"""Generate a public-safe Chinese projection for status.linzezhang.com."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from typing import Any

from .agent_store import AgentStore


def _parse(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def _atomic_write(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def build_projection(snapshot: dict[str, Any], *, now: datetime | None = None, ttl_minutes: int = 30) -> dict[str, Any]:
    current = now or datetime.now(timezone.utc)
    runs = list(snapshot.get("runs") or [])
    gates = list(snapshot.get("gates") or [])
    candidates = list(snapshot.get("candidates") or [])
    event_times = [value for value in (_parse(item.get("occurred_at")) for item in snapshot.get("events") or []) if value]
    newest_event = max(event_times, default=None)
    evidence_fresh = bool(newest_event and current - newest_event <= timedelta(minutes=max(1, ttl_minutes)))

    latest_gate_by_run: dict[str, dict[str, Any]] = {}
    for gate in gates:
        latest_gate_by_run.setdefault(str(gate.get("run_id")), gate)

    blockers: list[dict[str, str]] = []
    if not runs:
        blockers.append({"code": "NO_RUN_EVIDENCE", "title": "尚无 Agent 运行证据", "state": "UNKNOWN"})
    if runs and not gates:
        blockers.append({"code": "NO_GATE_EVIDENCE", "title": "尚无独立验收结论", "state": "UNKNOWN"})
    for run in runs:
        gate = latest_gate_by_run.get(str(run.get("run_id")))
        if not gate:
            blockers.append({"code": "RUN_WITHOUT_GATE", "title": f"运行 {run.get('run_id')} 未完成独立验收", "state": "UNKNOWN"})
        elif gate.get("verdict") != "PASS":
            blockers.append({"code": "GATE_NOT_PASS", "title": f"运行 {run.get('run_id')} 验收状态为 {gate.get('verdict')}", "state": "BLOCKED"})
    if runs and not evidence_fresh:
        blockers.append({"code": "EVIDENCE_STALE", "title": "运行证据缺失或已过期", "state": "STALE"})

    if blockers:
        release_state = "BLOCKED" if any(item["state"] == "BLOCKED" for item in blockers) else "UNKNOWN"
    elif runs and gates and evidence_fresh:
        release_state = "READY"
    else:
        release_state = "UNKNOWN"

    safe_runs = []
    for run in runs[:50]:
        safe_runs.append({
            "run_id": run.get("run_id"),
            "project_id": run.get("project_id"),
            "task_id": run.get("task_id"),
            "provider": run.get("provider"),
            "status": run.get("status"),
            "gate_verdict": run.get("gate_verdict", "UNKNOWN"),
            "candidate_commit": run.get("candidate_commit"),
            "updated_at": run.get("updated_at"),
        })

    safe_gates = []
    for gate in gates[:50]:
        safe_gates.append({
            "verdict_id": gate.get("verdict_id"),
            "run_id": gate.get("run_id"),
            "subject_commit": gate.get("subject_commit"),
            "artifact_digest": gate.get("artifact_digest"),
            "acceptance_hash": gate.get("acceptance_hash"),
            "verifier_version": gate.get("verifier_version"),
            "verdict": gate.get("verdict"),
            "verified_at": gate.get("verified_at"),
        })

    safe_candidates = []
    for candidate in candidates[:50]:
        safe_candidates.append({
            "candidate_id": candidate.get("candidate_id"),
            "run_id": candidate.get("run_id"),
            "candidate_type": candidate.get("candidate_type"),
            "title": candidate.get("title"),
            "state": candidate.get("state"),
            "requires_owner_approval": bool(candidate.get("requires_owner_approval", True)),
            "created_at": candidate.get("created_at"),
        })

    return {
        "schema_version": 1,
        "generated_at": current.replace(microsecond=0).isoformat(),
        "source": {
            "authority": "Private-Database",
            "runtime_journal": "OVH SQLite（可重建）",
            "projection": "status.linzezhang.com",
            "evidence_fresh": evidence_fresh,
            "newest_event_at": newest_event.isoformat() if newest_event else None,
            "ttl_minutes": ttl_minutes,
        },
        "release_decision": {
            "state": release_state,
            "label": {"READY": "可进入发布验收", "BLOCKED": "暂不可发布", "UNKNOWN": "证据不足"}[release_state],
            "blockers": blockers,
        },
        "metrics": {
            "run_count": len(runs),
            "gate_count": len(gates),
            "pass_count": sum(1 for gate in gates if gate.get("verdict") == "PASS"),
            "candidate_count": len(candidates),
        },
        "pipeline": [
            "意图包", "Agent 执行器", "过程记录器", "脱敏归一", "独立验收门", "证据清单", "经验候选路由",
        ],
        "runtime_invariants": {
            "agent_dependency": False,
            "llm_calls": 0,
            "token_budget": 0,
            "macos_launchd": False,
        },
        "runs": safe_runs,
        "gates": safe_gates,
        "candidates": safe_candidates,
    }


def write_projection(db_path: Path, output_path: Path, *, ttl_minutes: int = 30) -> dict[str, Any]:
    store = AgentStore(db_path)
    snapshot = store.snapshot()
    value = build_projection(snapshot, ttl_minutes=ttl_minutes)
    _atomic_write(output_path, value)
    return value

# STATUS_V3_LEGACY_PROJECTION_ISOLATED
# v1 facts remain observable, but this module cannot publish a release-authoritative
# green state or write the signed v3 public projection path.
_status_v3_legacy_build_projection = build_projection


def build_projection(*args, **kwargs):
    value = _status_v3_legacy_build_projection(*args, **kwargs)
    decision = dict(value.get("release_decision") or {})
    blockers = list(decision.get("blockers") or [])
    if not any(item.get("code") == "LEGACY_V1_HAS_NO_RELEASE_AUTHORITY" for item in blockers if isinstance(item, dict)):
        blockers.append({"code": "LEGACY_V1_HAS_NO_RELEASE_AUTHORITY", "title": "历史 v1 投影不具备发布授权", "state": "UNKNOWN"})
    if decision.get("state") == "READY":
        decision["state"] = "UNKNOWN"
        decision["label"] = "历史投影不具备发布授权"
    decision["blockers"] = blockers
    value["release_decision"] = decision
    value["legacy_projection"] = {"state": "UNTRUSTED", "green_allowed": False, "writer": "status-agent-governance-v1"}
    return value


_status_v3_legacy_write_projection = write_projection


def write_projection(db_path, output_path, *, ttl_minutes=30):
    if Path(output_path).name == "agent-governance.json":
        raise RuntimeError("legacy v1 projection may not write protected v3 public path")
    return _status_v3_legacy_write_projection(db_path, output_path, ttl_minutes=ttl_minutes)
