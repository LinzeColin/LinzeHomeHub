# STATUS_AGENT_V3_MANAGED: v0.0.0.3
from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
from typing import Any, Iterable, Mapping

STATES = {"satisfied", "apply", "adapt", "equivalent", "conflict", "blocked", "obsolete"}


def classify_target(repo: Path, task: Mapping[str, Any]) -> dict[str, Any]:
    repo = Path(repo).resolve()
    tid = str(task.get("task_id") or "")
    target_paths = [str(x) for x in task.get("target_paths") or []]
    markers = [str(x) for x in task.get("satisfied_markers") or []]
    conflict_markers = [str(x) for x in task.get("conflict_markers") or []]
    missing = []
    marker_hits = []
    conflicts = []
    for relative in target_paths:
        path = (repo / relative).resolve()
        try: path.relative_to(repo)
        except ValueError: return {"task_id": tid, "state": "conflict", "reason": "PATH_ESCAPE", "target_paths": target_paths}
        if not path.exists(): missing.append(relative); continue
        if path.is_file():
            try: text = path.read_text(encoding="utf-8")
            except UnicodeDecodeError: text = ""
            marker_hits.extend(marker for marker in markers if marker in text)
            conflicts.extend(marker for marker in conflict_markers if marker in text)
    if conflicts: state, reason = "conflict", "CONTRACT_OR_SECURITY_CONFLICT"
    elif markers and len(set(marker_hits)) == len(set(markers)): state, reason = "satisfied", "ALL_SEMANTIC_MARKERS_PRESENT"
    elif len(missing) == len(target_paths): state, reason = "apply", "TARGET_ABSENT"
    elif missing or marker_hits: state, reason = "adapt", "PARTIAL_OR_DRIFTED_IMPLEMENTATION"
    else: state, reason = "adapt", "EXISTING_IMPLEMENTATION_REQUIRES_SEMANTIC_REVIEW"
    return {"task_id": tid, "state": state, "reason": reason, "target_paths": target_paths, "missing": missing, "marker_hits": sorted(set(marker_hits)), "conflicts": sorted(set(conflicts))}


def validate_plan(items: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    failures=[]; seen=set()
    materialized=list(items)
    for item in materialized:
        tid=str(item.get("task_id") or ""); state=str(item.get("state") or "")
        if not tid or tid in seen: failures.append({"task_id":tid,"reason":"MISSING_OR_DUPLICATE_ID"})
        seen.add(tid)
        if state not in STATES: failures.append({"task_id":tid,"reason":"INVALID_STATE"})
        if state in {"adapt","conflict","blocked"} and not str(item.get("reason") or "").strip(): failures.append({"task_id":tid,"reason":"REASON_REQUIRED"})
        if state in {"apply","adapt"} and not item.get("target_paths"): failures.append({"task_id":tid,"reason":"TARGET_PATHS_REQUIRED"})
    return {"state":"PASS" if not failures else "BLOCKED","failures":failures,"count":len(materialized)}
