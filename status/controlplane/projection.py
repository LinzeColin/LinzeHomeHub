"""Fail-closed public/private projection builders."""

from __future__ import annotations

from copy import deepcopy
import re
from typing import Any, Mapping
from urllib.parse import urlparse

PUBLIC_TOP_LEVEL = frozenset({"schema_version", "generated_at", "observed_revision", "portfolio", "business_lines", "projects", "capabilities", "architecture", "conditions", "evidence_summary", "provenance_summary"})
PUBLIC_PORTFOLIO_FIELDS = frozenset({"coverage_health", "runtime_health", "project_count", "business_line_count", "unknown_is_healthy", "status_snapshot_freshness", "github_snapshot_freshness"})
PUBLIC_PROJECT_FIELDS = frozenset({"entity_id", "name", "lifecycle", "coverage_state", "runtime_state", "evidence_state", "data_freshness", "recovery_state", "dependencies", "public_url", "last_observed_at", "reason"})
PUBLIC_BUSINESS_LINE_FIELDS = frozenset({"business_line_id", "name", "lifecycle", "coverage_state", "runtime_state", "evidence_state", "data_freshness", "recovery_state", "project_ids", "dependencies", "stage_score", "last_observed_at", "reason"})
PUBLIC_CAPABILITY_FIELDS = frozenset({"capability_id", "project_id", "name", "aggregate_state", "declared", "implemented", "verified", "packaged", "deployed", "operational", "recoverable", "data_freshness", "last_observed_at", "reason", "evidence_refs"})
PUBLIC_CONDITION_FIELDS = frozenset({"type", "status", "desired_revision", "observed_revision", "reason", "message", "source", "evidence_refs", "last_observed_at", "last_transition_at", "expires_at"})
PUBLIC_ARCHITECTURE_FIELDS = frozenset({"nodes", "edges", "provenance_mode"})
PUBLIC_NODE_FIELDS = frozenset({"id", "kind", "label", "state"})
PUBLIC_EDGE_FIELDS = frozenset({"source", "target", "relation", "evidence_level"})
PUBLIC_EVIDENCE_SUMMARY_FIELDS = frozenset({"verified_fresh", "stale", "unverified"})
PUBLIC_PROVENANCE_SUMMARY_FIELDS = frozenset({"native", "reconstructed", "unknown"})

SENSITIVE_KEY = re.compile(r"(?i)(secret|token|password|passwd|cookie|session|prompt|private_key|api_key|authorization|email|host_path)")
SECRET_VALUE = re.compile(r"(?i)(github_pat_|ghp_|sk-[A-Za-z0-9_-]{12,}|bearer\s+[A-Za-z0-9._~+/=-]{12,})")
ALLOWED_URL_SCHEMES = frozenset({"https"})
ALLOWED_PUBLIC_HOSTS = frozenset({"status.linzezhang.com", "linzezhang.com", "github.com"})

class ProjectionError(RuntimeError):
    pass

def safe_public_url(value: str) -> str | None:
    try:
        parsed = urlparse(value)
    except ValueError:
        return None
    if parsed.scheme not in ALLOWED_URL_SCHEMES or parsed.hostname not in ALLOWED_PUBLIC_HOSTS or parsed.username or parsed.password:
        return None
    return value

def _assert_exact_keys(value: Mapping[str, Any], allowed: frozenset[str], path: str) -> None:
    unknown = set(value) - allowed
    if unknown:
        raise ProjectionError(f"undeclared fields at {path}: {sorted(unknown)}")

def _filtered(value: Mapping[str, Any], allowed: frozenset[str], path: str) -> dict[str, Any]:
    _assert_exact_keys(value, allowed, path)
    return {key: deepcopy(value[key]) for key in allowed if key in value}

def _assert_no_sensitive(value: Any, path: str = "$") -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if SENSITIVE_KEY.search(str(key)):
                raise ProjectionError(f"sensitive key at {path}.{key}")
            _assert_no_sensitive(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _assert_no_sensitive(child, f"{path}[{index}]")
    elif isinstance(value, str) and SECRET_VALUE.search(value):
        raise ProjectionError(f"secret-like value at {path}")

def build_public_projection(canonical: Mapping[str, Any]) -> dict[str, Any]:
    _assert_exact_keys(canonical, PUBLIC_TOP_LEVEL, "$")
    public: dict[str, Any] = {
        key: deepcopy(canonical[key]) for key in ("schema_version", "generated_at", "observed_revision") if key in canonical
    }
    public["portfolio"] = _filtered(canonical.get("portfolio", {}), PUBLIC_PORTFOLIO_FIELDS, "$.portfolio")
    public["projects"] = []
    for index, raw in enumerate(canonical.get("projects", [])):
        project = _filtered(raw, PUBLIC_PROJECT_FIELDS, f"$.projects[{index}]")
        if "public_url" in project:
            project["public_url"] = safe_public_url(str(project["public_url"]))
        public["projects"].append(project)
    public["business_lines"] = [_filtered(raw, PUBLIC_BUSINESS_LINE_FIELDS, f"$.business_lines[{i}]") for i, raw in enumerate(canonical.get("business_lines", []))]
    public["capabilities"] = [_filtered(raw, PUBLIC_CAPABILITY_FIELDS, f"$.capabilities[{i}]") for i, raw in enumerate(canonical.get("capabilities", []))]
    architecture = _filtered(canonical.get("architecture", {}), PUBLIC_ARCHITECTURE_FIELDS, "$.architecture")
    architecture["nodes"] = [_filtered(raw, PUBLIC_NODE_FIELDS, f"$.architecture.nodes[{i}]") for i, raw in enumerate(architecture.get("nodes", []))]
    architecture["edges"] = [_filtered(raw, PUBLIC_EDGE_FIELDS, f"$.architecture.edges[{i}]") for i, raw in enumerate(architecture.get("edges", []))]
    public["architecture"] = architecture
    public["conditions"] = [_filtered(raw, PUBLIC_CONDITION_FIELDS, f"$.conditions[{i}]") for i, raw in enumerate(canonical.get("conditions", []))]
    public["evidence_summary"] = _filtered(canonical.get("evidence_summary", {}), PUBLIC_EVIDENCE_SUMMARY_FIELDS, "$.evidence_summary")
    public["provenance_summary"] = _filtered(canonical.get("provenance_summary", {}), PUBLIC_PROVENANCE_SUMMARY_FIELDS, "$.provenance_summary")
    _assert_no_sensitive(public)
    return public

def build_private_projection(canonical: Mapping[str, Any]) -> dict[str, Any]:
    return deepcopy(dict(canonical))
