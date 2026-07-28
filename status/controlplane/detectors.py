"""Read-only current-state detectors for a continuously evolving repository."""

from __future__ import annotations

import ast
from dataclasses import dataclass, asdict
from hashlib import sha256
import json
from pathlib import Path
import re
import subprocess
from typing import Any, Iterable


@dataclass(frozen=True)
class Detection:
    key: str
    state: str
    evidence: tuple[str, ...]
    detail: str

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["evidence"] = list(self.evidence)
        return value


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (FileNotFoundError, UnicodeDecodeError, OSError):
        return ""


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True,
        timeout=30, check=False, shell=False,
    )
    return result.stdout.strip() if result.returncode == 0 else "UNKNOWN"


def repository_identity(repo: Path) -> dict[str, Any]:
    return {
        "head": _git(repo, "rev-parse", "HEAD"),
        "tree": _git(repo, "rev-parse", "HEAD^{tree}"),
        "branch": _git(repo, "branch", "--show-current"),
        "status_porcelain": _git(repo, "status", "--porcelain"),
        "origin": _git(repo, "remote", "get-url", "origin"),
    }


def _extract_literal_assignment(source: str, name: str) -> Any:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None
    for node in tree.body:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        if any(isinstance(target, ast.Name) and target.id == name for target in targets):
            try:
                return ast.literal_eval(node.value)
            except Exception:
                return None
    return None


def detect(repo: Path) -> dict[str, Any]:
    repo = repo.resolve()
    status = repo / "status"
    collector = status / "collector" / "collect.py"
    github_collector = status / "collector" / "collect_github.py"
    admin = status / "admin" / "app.py"
    compose = status / "deploy" / "docker-compose.yml"
    selfheal = status / "deploy" / "linze-selfheal.sh"
    backup = status / "deploy" / "linze-offsite-backup.sh"
    nginx = status / "deploy" / "nginx.conf"
    deploy_workflow = repo / ".github" / "workflows" / "deploy.yml"

    collector_text = _read(collector)
    admin_text = _read(admin)
    compose_text = _read(compose)
    selfheal_text = _read(selfheal)
    backup_text = _read(backup)
    nginx_text = _read(nginx)
    workflow_text = _read(deploy_workflow)

    project_literal = _extract_literal_assignment(collector_text, "PROJECTS")
    detections = [
        Detection(
            "status_directory", "SATISFIED" if status.is_dir() else "CONTRACT_CONFLICT",
            (str(status.relative_to(repo)),), "existing status source must be preserved",
        ),
        Detection(
            "static_project_denominator", "ADAPT_REQUIRED" if project_literal else "ALREADY_SATISFIED",
            (str(collector.relative_to(repo)),),
            "static declarations may remain as migration input but cannot be the coverage denominator",
        ),
        Detection(
            "shell_true", "ADAPT_REQUIRED" if "shell=True" in collector_text else "ALREADY_SATISFIED",
            (str(collector.relative_to(repo)),), "dynamic operational commands require argv adapters",
        ),
        Detection(
            "hard_coded_external_green", "ADAPT_REQUIRED" if re.search(r"(?:nitrosend|ovh(?:\s+vps-?1)?).{0,240}['\"]?ok['\"]?\s*:\s*True", collector_text, re.I | re.S) else "ALREADY_SATISFIED",
            (str(collector.relative_to(repo)),), "unprobed external status must be UNKNOWN",
        ),
        Detection(
            "admin_issuer_validation", "ALREADY_SATISFIED" if "issuer=" in admin_text or "CF_ACCESS_ISSUER" in admin_text else "ADAPT_REQUIRED",
            (str(admin.relative_to(repo)),), "JWT must bind issuer, audience, expiry and owner",
        ),
        Detection(
            "admin_safe_rendering", "ADAPT_REQUIRED" if ".innerHTML" in admin_text else "ALREADY_SATISFIED",
            (str(admin.relative_to(repo)),), "dynamic private data must use a safe DOM renderer",
        ),
        Detection(
            "admin_transaction_outbox", "ALREADY_SATISFIED" if "idempotency" in admin_text.lower() and "outbox" in admin_text.lower() else "ADAPT_REQUIRED",
            (str(admin.relative_to(repo)),), "accepted mutation, journal and outbox must commit atomically",
        ),
        Detection(
            "immutable_admin_image", "ADAPT_REQUIRED" if "linze-status-admin:latest" in compose_text else "ALREADY_SATISFIED",
            (str(compose.relative_to(repo)),), "deployment subject requires a version or digest",
        ),
        Detection(
            "selfheal_truthful_post_probe", "ALREADY_SATISFIED" if "post_probe" in selfheal_text.lower() else "ADAPT_REQUIRED",
            (str(selfheal.relative_to(repo)),), "RECOVERED requires successful action and post-probe",
        ),
        Detection(
            "r2_backup", "ALREADY_SATISFIED" if "r2" in backup_text.lower() else "ADAPT_REQUIRED",
            (str(backup.relative_to(repo)),), "R2 is the primary cold backup/object layer",
        ),
        Detection(
            "restore_proof", "ALREADY_SATISFIED" if "restore" in backup_text.lower() and "sha256" in backup_text.lower() else "ADAPT_REQUIRED",
            (str(backup.relative_to(repo)),), "backup age cannot substitute for restore proof",
        ),
        Detection(
            "csp_inline", "ADAPT_REQUIRED" if "'unsafe-inline'" in nginx_text else "ALREADY_SATISFIED",
            (str(nginx.relative_to(repo)),), "inline execution should be externalized or hash/nonce constrained",
        ),
        Detection(
            "cross_repo_mutable_workflow", "ADAPT_REQUIRED" if re.search(r"LinzeColin/CodexProject/.+@main", workflow_text) else "ALREADY_SATISFIED",
            (str(deploy_workflow.relative_to(repo)),), "status governance must not depend on a mutable cross-repo main",
        ),
        Detection(
            "unit_churn_ledger", "UPSTREAM_EQUIVALENT" if "def _unit_ledger(" in collector_text else "APPLY_CLEAN",
            (str(collector.relative_to(repo)),), "preserve upstream removed-unit ledger and its negative tests",
        ),
        Detection(
            "controlplane_overlay", "ALREADY_SATISFIED" if (status / "controlplane").is_dir() else "APPLY_CLEAN",
            ("status/controlplane",), "additive deterministic modules",
        ),
    ]
    return {
        "schema_version": 1,
        "repository": repository_identity(repo),
        "status_file_hashes": {
            str(path.relative_to(repo)): sha256(path.read_bytes()).hexdigest()
            for path in (collector, github_collector, admin, compose, selfheal, backup, nginx, deploy_workflow)
            if path.is_file()
        },
        "detections": [item.to_dict() for item in detections],
    }
