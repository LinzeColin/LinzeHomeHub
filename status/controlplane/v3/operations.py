# STATUS_AGENT_V3_MANAGED: v0.0.0.3
from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
from typing import Any, Mapping

from .canonical import digest, utc_now


class OperationsError(RuntimeError):
    pass


def doctor(config: Mapping[str, Any]) -> dict[str, Any]:
    checks = []
    for binary in ("python3", "git", "rclone"):
        checks.append({"name": binary, "state": "PASS" if shutil.which(binary) else "UNAVAILABLE"})
    for provider in ("codex", "claude"):
        checks.append({"name": provider, "state": "PASS" if shutil.which(provider) else "UNAVAILABLE"})
    private_client = Path(str(config.get("private_db_client") or "")).expanduser()
    checks.append({"name": "private_db_client.py", "state": "PASS" if private_client.is_file() else "UNAVAILABLE"})
    blockers = [item["name"] for item in checks if item["state"] != "PASS" and item["name"] in {"python3", "git"}]
    return {"schema_version": 3, "state": "PASS" if not blockers else "BLOCKED", "checks": checks, "blockers": blockers, "daemon_required": False, "launchd_required": False}


def _systemctl(action: str, units: list[str], apply: bool) -> dict[str, Any]:
    if action not in {"start", "stop", "restart", "status"}: raise OperationsError("invalid action")
    if not units: return {"state": "NO_UNITS", "action": action, "commands": []}
    commands = [["systemctl", action, unit] for unit in units]
    if not apply: return {"state": "DRY_RUN", "action": action, "commands": commands}
    results = []
    for command in commands:
        completed = subprocess.run(command, text=True, capture_output=True, check=False)
        results.append({"argv": command, "returncode": completed.returncode})
    return {"state": "PASS" if all(item["returncode"] == 0 for item in results) else "FAIL", "action": action, "results": results}


def start(config: Mapping[str, Any], apply: bool = False) -> dict[str, Any]: return _systemctl("start", list(config.get("existing_systemd_units") or []), apply)
def stop(config: Mapping[str, Any], apply: bool = False) -> dict[str, Any]: return _systemctl("stop", list(config.get("existing_systemd_units") or []), apply)
def status(config: Mapping[str, Any], apply: bool = False) -> dict[str, Any]: return _systemctl("status", list(config.get("existing_systemd_units") or []), apply)


def rollback_from_receipt(receipt: Mapping[str, Any], apply: bool = False) -> dict[str, Any]:
    commands = receipt.get("rollback_commands") or []
    if not isinstance(commands, list) or any(not isinstance(command, list) for command in commands):
        raise OperationsError("rollback_commands must be argv arrays")
    if not apply: return {"state": "DRY_RUN", "commands": commands}
    results=[]
    for command in commands:
        completed=subprocess.run([str(x) for x in command],text=True,capture_output=True,check=False)
        results.append({"argv":command,"returncode":completed.returncode})
    return {"state":"PASS" if all(x["returncode"]==0 for x in results) else "FAIL","results":results,"executed_at":utc_now()}
