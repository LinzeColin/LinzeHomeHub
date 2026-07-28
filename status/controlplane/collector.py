"""Additive control-plane collector built on existing status snapshots."""

from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any, Mapping

from .inventory import InventorySnapshot, reconcile_inventories
from .models import Condition, InventoryRecord, TruthValue, stable_id
from .projection import build_private_projection, build_public_projection
from .state import atomic_write_json, load_json

STATUS_TTL_SECONDS = 5 * 60
GITHUB_TTL_SECONDS = 2 * 60 * 60


def _hash_file(path: Path) -> str:
    digest = sha256()
    try:
        with path.open("rb") as handle:
            while chunk := handle.read(1024 * 1024):
                digest.update(chunk)
        return digest.hexdigest()
    except OSError:
        return "UNAVAILABLE"


def _parse_time(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value), tz=timezone.utc)
        except (ValueError, OSError, OverflowError):
            return None
    text = str(value).strip()
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except ValueError:
        return None


def _observed_time(value: Mapping[str, Any], path: Path) -> datetime | None:
    for key in ("updated_epoch", "collected_epoch", "generated_epoch", "generated_at", "collected_at", "updated_at", "at"):
        parsed = _parse_time(value.get(key))
        if parsed:
            return parsed
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    except OSError:
        return None


def _freshness(value: Mapping[str, Any], path: Path, now: datetime, ttl_seconds: int) -> tuple[str, str | None]:
    if not path.is_file() or not value:
        return "UNKNOWN", None
    observed = _observed_time(value, path)
    if not observed:
        return "UNKNOWN", None
    age = max(0.0, (now - observed).total_seconds())
    return ("FRESH" if age <= ttl_seconds else "STALE"), observed.replace(microsecond=0).isoformat()


def _observed_revision(*paths: Path) -> str:
    value = "|".join(_hash_file(path) for path in paths)
    return sha256(value.encode("utf-8")).hexdigest()


def _project_rows(status_data: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    rows: list[Mapping[str, Any]] = []
    for key in ("projects", "services", "project_status", "service_status"):
        value = status_data.get(key)
        if isinstance(value, list):
            rows.extend(item for item in value if isinstance(item, Mapping))
    return rows


def _source_rows(github_data: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    for key in ("public_repos", "repos"):
        rows = github_data.get(key)
        if isinstance(rows, list):
            return [item for item in rows if isinstance(item, Mapping)]
    return []


def _project_key(raw: Mapping[str, Any]) -> str:
    """项目的唯一键。

    ★ 原实现是 `repo or project or id or name`,**把 repo 排在最前面**,于是
      同一个仓里的多个项目会塌成同一个键。实测生产数据:Nab / PFI / Serenity /
      EEI / Alpha / ADP / CyberBoss 七个项目都住在 MetaDatabase 这一个仓里,
      Home 与 Status 都住在 LinzeHomeHub —— 12 个项目被压成 5 个键,
      直接让 records_by_id 抛 `duplicate entity_id`,控制面采集整个跑不起来。

      「一个仓装多个项目」是本工作间**有意为之**的架构(新功能默认做子项目,
      不单独建仓),所以这不是数据脏,是键选错了。

    ★ 修法不是去重。去重会把 7 个真项目合成 1 个,那是 DA-002 明令禁止的
      silent shrink —— 清单悄悄变小,覆盖率分母跟着缩水,看板反而更好看。
      正确做法是让键真正唯一:用 `repo/name` 复合,既区分同仓内的不同项目,
      也保留项目与仓的从属关系;没有 repo 时退回项目自身标识。
    """
    name = str(raw.get("name") or raw.get("project") or raw.get("id") or "").strip()
    repo = str(raw.get("repo") or "").strip()
    if repo and name:
        return f"{repo}/{name}"
    return name or repo


def _project_name(raw: Mapping[str, Any]) -> str:
    return str(raw.get("name") or raw.get("project") or raw.get("repo") or raw.get("id") or "").strip()


def _declared_records(status_data: Mapping[str, Any]) -> tuple[InventoryRecord, ...]:
    records = []
    for raw in _project_rows(status_data):
        key, name = _project_key(raw), _project_name(raw)
        if not key or not name:
            continue
        records.append(InventoryRecord(
            entity_id=stable_id("project", key), kind="project", name=name, source="declared",
            lifecycle=str(raw.get("lifecycle") or raw.get("stage") or "active"), runtime_state="UNKNOWN",
            metadata={"evidence_ref": str(raw.get("evidence_ref") or "")},
        ))
    return tuple(records)


def _source_records(github_data: Mapping[str, Any]) -> tuple[InventoryRecord, ...]:
    records = []
    for raw in _source_rows(github_data):
        key = str(raw.get("name") or raw.get("repo") or "").strip()
        if not key:
            continue
        records.append(InventoryRecord(
            entity_id=stable_id("project", key), kind="repository", name=key, source="source",
            lifecycle="retired" if raw.get("archived") else "active", runtime_state="UNKNOWN",
            metadata={
                "public_url": (raw.get("url") or raw.get("html_url")) if not raw.get("private") else None,
                "evidence_ref": str(raw.get("pushed_at") or raw.get("updated_at") or ""),
            },
        ))
    return tuple(records)


def _runtime_records(status_data: Mapping[str, Any]) -> tuple[InventoryRecord, ...]:
    records = []
    for raw in _project_rows(status_data):
        key, name = _project_key(raw), _project_name(raw)
        if not key or not name:
            continue
        raw_state = raw.get("state") or raw.get("status") or raw.get("health")
        if raw.get("ok") is True:
            raw_state = "HEALTHY"
        elif raw.get("ok") is False:
            raw_state = "FAILED"
        state = str(raw_state or "UNKNOWN").upper()
        if state in {"RUN", "ACCESS"}:
            state = "HEALTHY"
        elif state in {"DOWN", "BAD"}:
            state = "FAILED"
        records.append(InventoryRecord(
            entity_id=stable_id("project", key), kind="runtime", name=name, source="runtime",
            runtime_state=state, metadata={"evidence_ref": str(raw.get("checked_at") or raw.get("updated_at") or "")},
        ))
    software = status_data.get("software") if isinstance(status_data.get("software"), Mapping) else {}
    units = software.get("units") if isinstance(software, Mapping) else None
    if not isinstance(units, list):
        units = status_data.get("units") or status_data.get("runtime_units")
    if isinstance(units, list):
        for raw in units:
            if not isinstance(raw, Mapping):
                continue
            name = str(raw.get("id") or raw.get("name") or "").strip()
            if not name:
                continue
            raw_state = str(raw.get("state") or raw.get("status") or "UNKNOWN").upper()
            state = "HEALTHY" if raw_state in {"RUNNING", "ACTIVE", "SCHEDULED", "HEALTHY"} else "FAILED" if raw_state in {"FAILED", "EXITED", "INACTIVE", "DEAD"} else "UNKNOWN"
            records.append(InventoryRecord(
                entity_id=stable_id("runtime", name), kind="runtime_unit", name=name, source="runtime",
                runtime_state=state, metadata={"evidence_ref": str(software.get("at") or status_data.get("updated_at") or "")},
            ))
    return tuple(records)


def _availability(value: Mapping[str, Any], path: Path, freshness: str) -> bool:
    return path.is_file() and bool(value) and value.get("available") is not False and freshness == "FRESH"


def _business_lines(status_data: Mapping[str, Any], freshness: str, observed_at: str | None) -> list[dict[str, Any]]:
    software = status_data.get("software") if isinstance(status_data.get("software"), Mapping) else {}
    lines = software.get("lines") if isinstance(software, Mapping) else None
    if not isinstance(lines, list):
        return []
    result = []
    for raw in lines:
        if not isinstance(raw, Mapping):
            continue
        name = str(raw.get("name") or "").strip()
        if not name:
            continue
        repo = str(raw.get("repo") or name).strip()
        raw_state = str(raw.get("state") or "unknown").lower()
        runtime_state = "HEALTHY" if raw_state == "ok" else "DEGRADED" if raw_state == "warn" else "FAILED" if raw_state == "bad" else "UNKNOWN"
        result.append({
            "business_line_id": stable_id("business-line", name), "name": name, "lifecycle": "active",
            "coverage_state": "OBSERVED", "runtime_state": runtime_state, "evidence_state": "UNVERIFIED",
            "data_freshness": freshness, "recovery_state": "UNKNOWN",
            "project_ids": [stable_id("project", repo)], "dependencies": [],
            "stage_score": int(raw.get("score") or 0), "last_observed_at": observed_at,
            "reason": f"九段基线已判定 {raw.get('judged', 0)}/{raw.get('stages_total', 0)} 段",
        })
    return result


def _capabilities(status_data: Mapping[str, Any], freshness: str, observed_at: str | None) -> list[dict[str, Any]]:
    software = status_data.get("software") if isinstance(status_data.get("software"), Mapping) else {}
    lines = software.get("lines") if isinstance(software, Mapping) else None
    if not isinstance(lines, list):
        return []
    stage_names = {str(x.get("k")): str(x.get("n") or x.get("k")) for x in software.get("stages", []) if isinstance(x, Mapping)}
    result = []
    for line in lines:
        if not isinstance(line, Mapping):
            continue
        line_name = str(line.get("name") or "").strip()
        repo = str(line.get("repo") or line_name).strip()
        cells = line.get("cells")
        if not line_name or not isinstance(cells, Mapping):
            continue
        for stage, cell in cells.items():
            if not isinstance(cell, Mapping):
                continue
            raw_state = str(cell.get("s") or "unknown").lower()
            operational = "HEALTHY" if raw_state == "ok" else "DEGRADED" if raw_state == "warn" else "FAILED" if raw_state == "bad" else "UNKNOWN"
            aggregate = "FAILED" if operational == "FAILED" else "IMPLEMENTED_UNVERIFIED"
            result.append({
                "capability_id": stable_id("capability", line_name, str(stage)),
                "project_id": stable_id("project", repo),
                "name": f"{line_name} · {stage_names.get(str(stage), str(stage))}",
                "aggregate_state": aggregate, "declared": True, "implemented": raw_state != "not_built",
                "verified": "UNVERIFIED", "packaged": False, "deployed": stage in {"deploy", "run", "entry"} and operational != "UNKNOWN",
                "operational": operational, "recoverable": "UNKNOWN",
                "data_freshness": freshness, "last_observed_at": observed_at,
                "reason": str(cell.get("v") or "")[:240], "evidence_refs": [],
            })
    return result


def _architecture(status_data: Mapping[str, Any]) -> dict[str, Any]:
    graph = status_data.get("graph") if isinstance(status_data.get("graph"), Mapping) else {}
    nodes = []
    for raw in graph.get("nodes", []) if isinstance(graph.get("nodes"), list) else []:
        if not isinstance(raw, Mapping) or not raw.get("id"):
            continue
        nodes.append({
            "id": str(raw["id"]), "kind": str(raw.get("kind") or "unknown"),
            "label": str(raw.get("label") or raw["id"]),
            "state": str(raw.get("state") or raw.get("status") or "UNKNOWN").upper(),
        })
    edges = []
    for raw in graph.get("edges", []) if isinstance(graph.get("edges"), list) else []:
        if not isinstance(raw, Mapping):
            continue
        source, target = raw.get("s") or raw.get("source"), raw.get("t") or raw.get("target")
        if not source or not target:
            continue
        edges.append({
            "source": str(source), "target": str(target),
            "relation": str(raw.get("rel") or raw.get("relation") or "related_to"),
            "evidence_level": "DERIVED",
        })
    return {"nodes": nodes, "edges": edges, "provenance_mode": "DERIVED"}


def collect_control_plane(
    *, status_path: Path, github_path: Path, output_private: Path, output_public: Path, now: datetime | None = None,
) -> dict[str, Any]:
    instant = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    status_data = load_json(status_path, {}) or {}
    github_data = load_json(github_path, {}) or {}
    revision = _observed_revision(status_path, github_path)
    status_freshness, status_observed_at = _freshness(status_data, status_path, instant, STATUS_TTL_SECONDS)
    github_freshness, github_observed_at = _freshness(github_data, github_path, instant, GITHUB_TTL_SECONDS)

    declared = InventorySnapshot(_declared_records(status_data), _availability(status_data, status_path, status_freshness), revision, f"status:{status_freshness}")
    source = InventorySnapshot(_source_records(github_data), _availability(github_data, github_path, github_freshness), revision, f"github:{github_freshness}")
    runtime = InventorySnapshot(_runtime_records(status_data), _availability(status_data, status_path, status_freshness), revision, f"runtime:{status_freshness}")
    reconciled = reconcile_inventories(declared, source, runtime)

    conditions = [
        Condition(type="CoverageReady", status=TruthValue.TRUE if reconciled.coverage_health == "HEALTHY" else TruthValue.UNKNOWN if reconciled.coverage_health == "UNKNOWN" else TruthValue.FALSE, desired_revision=revision, observed_revision=revision, reason="inventory_reconciliation", message=f"覆盖健康：{reconciled.coverage_health}", source="controlplane.inventory", evidence_refs=tuple(reconciled.reasons)).to_dict(),
        Condition(type="RuntimeHealthy", status=TruthValue.TRUE if reconciled.runtime_health == "HEALTHY" else TruthValue.UNKNOWN if reconciled.runtime_health == "UNKNOWN" else TruthValue.FALSE, desired_revision=revision, observed_revision=revision, reason="runtime_rollup", message=f"运行健康：{reconciled.runtime_health}", source="controlplane.inventory").to_dict(),
        Condition(type="SourceFresh", status=TruthValue.TRUE if github_freshness == "FRESH" else TruthValue.FALSE if github_freshness == "STALE" else TruthValue.UNKNOWN, desired_revision=revision, observed_revision=revision, reason="github_snapshot_freshness", message=f"GitHub 数据：{github_freshness}", source="controlplane.freshness", expires_at=None).to_dict(),
    ]

    projects = []
    for item in reconciled.items:
        projects.append({
            "entity_id": item.entity_id, "name": item.name, "lifecycle": "active",
            "coverage_state": item.state, "runtime_state": item.runtime_state, "evidence_state": "UNVERIFIED",
            "data_freshness": status_freshness if item.runtime_observed else github_freshness if item.source_observed else "UNKNOWN",
            "recovery_state": "UNKNOWN", "dependencies": [], "last_observed_at": status_observed_at or github_observed_at,
            "reason": "; ".join(item.reasons),
        })

    capabilities = _capabilities(status_data, status_freshness, status_observed_at)
    canonical = {
        "schema_version": 1, "generated_at": instant.replace(microsecond=0).isoformat(), "observed_revision": revision,
        "portfolio": {
            "coverage_health": reconciled.coverage_health, "runtime_health": reconciled.runtime_health,
            "project_count": len(projects), "business_line_count": len(_business_lines(status_data, status_freshness, status_observed_at)),
            "unknown_is_healthy": False, "status_snapshot_freshness": status_freshness,
            "github_snapshot_freshness": github_freshness,
        },
        "business_lines": _business_lines(status_data, status_freshness, status_observed_at),
        "projects": projects, "capabilities": capabilities, "architecture": _architecture(status_data),
        "conditions": conditions,
        "evidence_summary": {"verified_fresh": 0, "stale": 0, "unverified": len(capabilities) or len(projects)},
        "provenance_summary": {"native": 0, "reconstructed": len(capabilities), "unknown": max(0, len(projects) - len(capabilities))},
    }
    private_projection = build_private_projection(canonical)
    public_projection = build_public_projection(canonical)
    atomic_write_json(output_private, private_projection, mode=0o640)
    atomic_write_json(output_public, public_projection, mode=0o644)
    return canonical
