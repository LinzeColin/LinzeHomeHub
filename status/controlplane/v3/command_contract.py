# STATUS_AGENT_V3_MANAGED: v0.0.0.3
from __future__ import annotations

from hashlib import sha256
import json
import os
from pathlib import Path
import re
import subprocess
from typing import Any, Mapping, Sequence

from .canonical import atomic_write, canonical_bytes, digest, utc_now

PHASES = {"release", "post_deploy", "rollback_drill", "settle"}
FORBIDDEN_EXECUTABLES = {"sh", "bash", "zsh", "fish", "powershell", "pwsh"}
FORBIDDEN_ARGUMENT_PATTERNS = (
    re.compile(r"^--force(?:-with-lease)?$"),
    re.compile(r"^--delete$"),
    re.compile(r"^--hard$"),
    re.compile(r"^rm$"),
)
TOKEN_PATTERN = re.compile(r"^\{\{([A-Z0-9_]+)\}\}$")


class CommandContractError(RuntimeError):
    pass


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise CommandContractError("contract root must be object")
    return value


def _resolve(value: str, variables: Mapping[str, str]) -> str:
    match = TOKEN_PATTERN.fullmatch(str(value))
    if not match:
        if "{{" in str(value) or "}}" in str(value):
            raise CommandContractError("tokens must occupy the complete argv item")
        return str(value)
    name = match.group(1)
    resolved = str(variables.get(name) or "")
    if not resolved:
        raise CommandContractError(f"missing variable: {name}")
    if "\x00" in resolved or "\n" in resolved:
        raise CommandContractError(f"invalid variable: {name}")
    return resolved


def _safe_argv(command: Mapping[str, Any], variables: Mapping[str, str]) -> list[str]:
    raw = command.get("argv")
    if not isinstance(raw, list) or not raw or any(not isinstance(item, str) for item in raw):
        raise CommandContractError("argv must be a non-empty string array")
    argv = [_resolve(item, variables) for item in raw]
    executable = Path(argv[0]).name
    allowed = {str(item) for item in command.get("allowed_executables") or []}
    if not allowed or (executable not in allowed and argv[0] not in allowed):
        raise CommandContractError(f"executable not allowlisted: {argv[0]}")
    if executable in FORBIDDEN_EXECUTABLES:
        raise CommandContractError("shell interpreters are not permitted in release command contracts")
    for item in argv[1:]:
        if any(pattern.fullmatch(item) for pattern in FORBIDDEN_ARGUMENT_PATTERNS):
            raise CommandContractError(f"forbidden argument: {item}")
    if executable == "git" and "push" in argv and any("force" in item.lower() for item in argv):
        raise CommandContractError("force push is forbidden")
    return argv


def validate_contract(contract: Mapping[str, Any]) -> dict[str, Any]:
    errors: list[str] = []
    if contract.get("schema_version") != "status.release_environment_contract.v3":
        errors.append("INVALID_SCHEMA_VERSION")
    for field in ("subject_id", "subject_sha256", "candidate_commit", "variables", "phases"):
        if contract.get(field) in (None, "", {}):
            errors.append(f"MISSING:{field}")
    variables = {str(key): str(value) for key, value in dict(contract.get("variables") or {}).items()}
    phases = contract.get("phases") or {}
    if not isinstance(phases, Mapping):
        errors.append("PHASES_NOT_OBJECT")
        phases = {}
    command_ids: set[str] = set()
    for phase, commands in phases.items():
        if phase not in PHASES:
            errors.append(f"INVALID_PHASE:{phase}")
            continue
        if not isinstance(commands, list):
            errors.append(f"PHASE_NOT_ARRAY:{phase}")
            continue
        for index, command in enumerate(commands):
            if not isinstance(command, Mapping):
                errors.append(f"COMMAND_NOT_OBJECT:{phase}:{index}")
                continue
            command_id = str(command.get("command_id") or "")
            if not command_id or command_id in command_ids:
                errors.append(f"INVALID_COMMAND_ID:{phase}:{index}")
            command_ids.add(command_id)
            try:
                _safe_argv(command, variables)
            except Exception as exc:
                errors.append(f"COMMAND_INVALID:{command_id}:{exc}")
            expected = command.get("expected_exit_codes", [0])
            if not isinstance(expected, list) or not expected or any(not isinstance(code, int) for code in expected):
                errors.append(f"EXPECTED_EXIT_INVALID:{command_id}")
    required_phases = {"release", "post_deploy", "rollback_drill", "settle"}
    for phase in required_phases:
        if not phases.get(phase):
            errors.append(f"MISSING_PHASE_COMMANDS:{phase}")
    return {"state": "PASS" if not errors else "BLOCKED", "errors": errors, "command_count": len(command_ids)}


def execute_phase(*, contract_path: Path, phase: str, evidence_dir: Path) -> dict[str, Any]:
    if phase not in PHASES:
        raise CommandContractError("invalid phase")
    contract = _load(contract_path)
    validation = validate_contract(contract)
    if validation["state"] != "PASS":
        raise CommandContractError("release contract invalid: " + ";".join(validation["errors"]))
    variables = {str(key): str(value) for key, value in contract["variables"].items()}
    evidence_dir = Path(evidence_dir).expanduser().resolve()
    evidence_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    results: list[dict[str, Any]] = []
    for command in contract["phases"][phase]:
        command_id = str(command["command_id"])
        argv = _safe_argv(command, variables)
        cwd_value = _resolve(str(command.get("cwd") or "{{TARGET_REPO}}"), variables)
        cwd = Path(cwd_value).expanduser().resolve()
        if not cwd.is_dir():
            results.append({"command_id": command_id, "state": "BLOCKED", "reason": "CWD_MISSING", "argv": argv})
            break
        timeout = max(1, min(int(command.get("timeout_seconds") or 300), 1800))
        max_output = max(4096, min(int(command.get("max_output_bytes") or 524288), 4 * 1024 * 1024))
        env = {"PATH": os.environ.get("PATH", ""), "HOME": os.environ.get("HOME", ""), "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8"}
        for key in command.get("environment_allowlist") or []:
            if key in os.environ:
                env[str(key)] = os.environ[str(key)]
        try:
            completed = subprocess.run(argv, cwd=cwd, env=env, text=False, capture_output=True, timeout=timeout, check=False)
            stdout = bytes(completed.stdout or b"")[:max_output]
            stderr = bytes(completed.stderr or b"")[:max_output]
            raw = {
                "command_id": command_id,
                "argv": argv,
                "cwd": str(cwd),
                "returncode": completed.returncode,
                "stdout_sha256": sha256(stdout).hexdigest(),
                "stderr_sha256": sha256(stderr).hexdigest(),
                "stdout_truncated": len(completed.stdout or b"") > max_output,
                "stderr_truncated": len(completed.stderr or b"") > max_output,
            }
            raw_path = evidence_dir / f"{command_id}.json"
            atomic_write(raw_path, canonical_bytes(raw))
            expected = {int(code) for code in command.get("expected_exit_codes", [0])}
            state = "PASS" if completed.returncode in expected else "FAIL"
            results.append({**raw, "state": state, "evidence_path": str(raw_path), "evidence_sha256": sha256(raw_path.read_bytes()).hexdigest()})
            if state != "PASS" and command.get("required", True):
                break
        except subprocess.TimeoutExpired:
            results.append({"command_id": command_id, "state": "BLOCKED", "reason": "TIMEOUT", "argv": argv})
            break
        except OSError as exc:
            results.append({"command_id": command_id, "state": "BLOCKED", "reason": f"EXECUTION_ERROR:{exc.__class__.__name__}", "argv": argv})
            break
    required_ids = [str(command["command_id"]) for command in contract["phases"][phase] if command.get("required", True)]
    passed_ids = {str(result.get("command_id")) for result in results if result.get("state") == "PASS"}
    if any(result.get("state") == "FAIL" for result in results):
        state = "FAIL"
    elif set(required_ids).issubset(passed_ids):
        state = "PASS"
    else:
        state = "BLOCKED"
    receipt = {
        "schema_version": "status.command_phase_receipt.v3",
        "state": state,
        "phase": phase,
        "subject_id": contract["subject_id"],
        "subject_sha256": contract["subject_sha256"],
        "candidate_commit": contract["candidate_commit"],
        "contract_sha256": sha256(Path(contract_path).read_bytes()).hexdigest(),
        "results": results,
        "required_command_ids": required_ids,
        "completed_at": utc_now(),
        "force_push": False,
    }
    receipt["receipt_id"] = f"command-phase:{phase}:" + digest(receipt)
    return receipt
