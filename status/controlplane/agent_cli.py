#!/usr/bin/env python3
"""Operator CLI for the development-time Agent governance vertical slice."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import sys
from typing import Any

from .agent_projection import write_projection
from .agent_store import AgentStore
from .authority import validate_client_contract
from .backup_transport import RcloneTransport, backup_replicate_restore
from .candidate import build_candidate
from .capture import normalize_event
from .gate import evaluate
from .intent import compile_run_intent, verify_bundle


def _json(path: str) -> dict[str, Any]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def _write(path: str, value: dict[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    temporary.replace(target)


#: Exactly what ``agent-governance-backup-verify.sh`` refuses to run without.
#: Recorded as presence booleans only — key names are Stage 0 evidence, values are not.
_BACKUP_REMOTE_KEYS = ("LINZE_R2_CRYPT_REMOTE", "LINZE_OCI_CRYPT_REMOTE")


def _backup_transport_state(commands: dict[str, bool]) -> dict[str, Any]:
    """Make the R2/OCI binding explicit instead of letting a narrow PASS imply it.

    ``state`` only covers "can this host run the control plane at all". Backup
    and restore need three more things, and a missing one has to read as
    ENVIRONMENT_BLOCKED rather than disappearing from the report.
    """

    remotes = {key: bool(os.environ.get(key)) for key in _BACKUP_REMOTE_KEYS}
    confirmed = os.environ.get("RCLONE_CRYPT_REMOTE_CONFIRMED") == "1"
    missing = [key for key, present in remotes.items() if not present]
    if not commands["rclone"]:
        missing.append("rclone")
    if not confirmed:
        missing.append("RCLONE_CRYPT_REMOTE_CONFIRMED=1")
    return {
        "remotes_configured": remotes,
        "crypt_remote_confirmed": confirmed,
        "rclone_available": commands["rclone"],
        "missing": missing,
        "state": "READY" if not missing else "ENVIRONMENT_BLOCKED",
    }


def cmd_doctor(args) -> int:
    db_parent = Path(args.db).expanduser().resolve().parent
    output_parent = Path(args.output).expanduser().resolve().parent
    db_parent.mkdir(parents=True, exist_ok=True)
    output_parent.mkdir(parents=True, exist_ok=True)
    commands = {name: bool(shutil.which(name)) for name in ("python3", "systemctl", "rclone", "gh")}
    checks: dict[str, Any] = {
        "schema_version": 2,
        "checked_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "commands": commands,
        "paths": {
            "db_parent_writable": os.access(db_parent, os.W_OK),
            "output_parent_writable": os.access(output_parent, os.W_OK),
        },
        "private_db_client": {"state": "NOT_CONFIGURED"},
        "backup_transport": _backup_transport_state(commands),
        "runtime_invariants": {"agent_dependency": False, "llm_calls": 0, "token_budget": 0, "macos_launchd": False},
    }
    if args.private_db_client:
        checks["private_db_client"] = validate_client_contract(Path(args.private_db_client))
    checks["state"] = "PASS" if checks["commands"]["python3"] and all(checks["paths"].values()) else "FAIL"
    _write(args.output, checks)
    print(json.dumps(checks, ensure_ascii=False, indent=2))
    return 0 if checks["state"] == "PASS" else 5


def cmd_compile_intent(args) -> int:
    bundle = compile_run_intent(_json(args.owner), _json(args.project), _json(args.task))
    if not verify_bundle(bundle):
        raise RuntimeError("intent self-verification failed")
    _write(args.output, bundle)
    print(json.dumps({"state": "COMPILED", "intent_id": bundle["intent_id"], "intent_sha256": bundle["intent_sha256"]}, ensure_ascii=False))
    return 0


def cmd_ingest(args) -> int:
    raw = _json(args.input)
    event = normalize_event(
        raw, provider=args.provider, project_id=args.project_id, run_id=args.run_id,
        task_id=args.task_id, intent_hash=args.intent_hash, session_id=args.session_id,
        raw_object_ref=args.raw_object_ref,
    )
    store = AgentStore(Path(args.db))
    store.migrate()
    store.upsert_run({
        "run_id": args.run_id, "project_id": args.project_id, "task_id": args.task_id,
        "provider": args.provider, "intent_hash": args.intent_hash, "status": "RUNNING",
        "started_at": args.started_at or event["occurred_at"],
    })
    store.add_event(event)
    print(json.dumps({"state": "INGESTED", "event_id": event["event_id"], "redaction_count": event["redaction_count"]}, ensure_ascii=False))
    return 0


def cmd_ingest_normalized(args) -> int:
    """Persist a JSONL stream that was already normalized by agent_hook."""
    store = AgentStore(Path(args.db))
    store.migrate()
    count = 0
    with Path(args.input).open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            event = json.loads(line)
            required = ("event_id", "provider", "project_id", "run_id", "task_id", "intent_hash", "session_id", "event_type", "occurred_at", "safe_payload")
            missing = [key for key in required if key not in event or event[key] in (None, "")]
            if missing:
                raise ValueError(f"normalized event line {line_number} missing: {','.join(missing)}")
            if event.get("adapter_state") != "NORMALIZED_REDACTED":
                raise ValueError(f"normalized event line {line_number} has invalid adapter_state")
            expected = {
                "project_id": args.project_id,
                "run_id": args.run_id,
                "task_id": args.task_id,
                "intent_hash": args.intent_hash,
                "session_id": args.session_id,
                "provider": args.provider,
            }
            mismatched = [key for key, value in expected.items() if str(event.get(key)) != str(value)]
            if mismatched:
                raise ValueError(f"normalized event line {line_number} identity mismatch: {','.join(mismatched)}")
            store.upsert_run({
                "run_id": args.run_id,
                "project_id": args.project_id,
                "task_id": args.task_id,
                "provider": args.provider,
                "intent_hash": args.intent_hash,
                "status": "RUNNING",
                "started_at": args.started_at or event["occurred_at"],
            })
            store.add_event(event)
            count += 1
    print(json.dumps({"state": "INGESTED_NORMALIZED", "count": count}, ensure_ascii=False))
    return 0

def cmd_gate(args) -> int:
    verdict = evaluate(_json(args.contract), _json(args.observations))
    output = Path(args.output)
    if output.exists() and not args.overwrite:
        raise RuntimeError("gate output already exists; immutable verdict requires a new path")
    _write(args.output, verdict)
    store = AgentStore(Path(args.db))
    store.migrate()
    store.record_gate(verdict)
    print(json.dumps(verdict, ensure_ascii=False, indent=2))
    return 0 if verdict["verdict"] == "PASS" else 6


def cmd_candidate(args) -> int:
    evidence = _json(args.evidence)
    candidate = build_candidate(
        run_id=args.run_id, title=args.title, signals=_json(args.signals),
        evidence_refs=list(evidence.get("evidence_refs") or []),
    )
    store = AgentStore(Path(args.db))
    store.migrate()
    store.add_candidate(candidate)
    _write(args.output, candidate)
    print(json.dumps({"state": "PROPOSED", "candidate_id": candidate["candidate_id"]}, ensure_ascii=False))
    return 0


def cmd_project(args) -> int:
    value = write_projection(Path(args.db), Path(args.output), ttl_minutes=args.ttl_minutes)
    print(json.dumps({"state": "PROJECTED", "release_state": value["release_decision"]["state"], "output": args.output}, ensure_ascii=False))
    return 0


def cmd_backup(args) -> int:
    result = backup_replicate_restore(
        Path(args.source), r2_prefix=args.r2_prefix, oci_prefix=args.oci_prefix,
        transport=RcloneTransport(executable=args.rclone, timeout=args.timeout),
        evidence_path=Path(args.output), encryption_profile="rclone-crypt",
        semantic_contract=_json(args.semantic_contract),
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if result["state"] == "BACKUP_RESTORE_VERIFIED" and args.delete_source:
        source = Path(args.source).resolve()
        if source.is_file():
            source.unlink()
        elif source.is_dir():
            shutil.rmtree(source)
    return 0 if result["state"] == "BACKUP_RESTORE_VERIFIED" else 7


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="status-agent-governance")
    sub = root.add_subparsers(dest="command", required=True)

    doctor = sub.add_parser("doctor")
    doctor.add_argument("--db", default="status/runtime/status.db")
    doctor.add_argument("--private-db-client", default=os.environ.get("PRIVATE_DB_CLIENT_PATH"))
    doctor.add_argument("--output", default="status/runtime/agent-doctor.json")
    doctor.set_defaults(func=cmd_doctor)

    intent = sub.add_parser("compile-intent")
    intent.add_argument("--owner", required=True)
    intent.add_argument("--project", required=True)
    intent.add_argument("--task", required=True)
    intent.add_argument("--output", required=True)
    intent.set_defaults(func=cmd_compile_intent)

    ingest = sub.add_parser("ingest-hook")
    ingest.add_argument("--db", default="status/runtime/status.db")
    ingest.add_argument("--input", required=True)
    ingest.add_argument("--provider", choices=("codex", "claude"), required=True)
    ingest.add_argument("--project-id", required=True)
    ingest.add_argument("--run-id", required=True)
    ingest.add_argument("--task-id", required=True)
    ingest.add_argument("--intent-hash", required=True)
    ingest.add_argument("--session-id", required=True)
    ingest.add_argument("--raw-object-ref")
    ingest.add_argument("--started-at")
    ingest.set_defaults(func=cmd_ingest)

    normalized = sub.add_parser("ingest-normalized")
    normalized.add_argument("--db", default="status/runtime/status.db")
    normalized.add_argument("--input", required=True)
    normalized.add_argument("--provider", choices=("codex", "claude"), required=True)
    normalized.add_argument("--project-id", required=True)
    normalized.add_argument("--run-id", required=True)
    normalized.add_argument("--task-id", required=True)
    normalized.add_argument("--intent-hash", required=True)
    normalized.add_argument("--session-id", required=True)
    normalized.add_argument("--started-at")
    normalized.set_defaults(func=cmd_ingest_normalized)

    gate = sub.add_parser("gate")
    gate.add_argument("--db", default="status/runtime/status.db")
    gate.add_argument("--contract", required=True)
    gate.add_argument("--observations", required=True)
    gate.add_argument("--output", required=True)
    gate.add_argument("--overwrite", action="store_true")
    gate.set_defaults(func=cmd_gate)

    candidate = sub.add_parser("candidate")
    candidate.add_argument("--db", default="status/runtime/status.db")
    candidate.add_argument("--run-id", required=True)
    candidate.add_argument("--title", required=True)
    candidate.add_argument("--signals", required=True)
    candidate.add_argument("--evidence", required=True)
    candidate.add_argument("--output", required=True)
    candidate.set_defaults(func=cmd_candidate)

    project = sub.add_parser("project")
    project.add_argument("--db", default="status/runtime/status.db")
    # STATUS_V3_LEGACY_PROJECTION_DEFAULT
    project.add_argument("--output", default="status/data/agent-governance-v1-legacy.json")
    project.add_argument("--ttl-minutes", type=int, default=30)
    project.set_defaults(func=cmd_project)

    backup = sub.add_parser("backup-verify")
    backup.add_argument("--source", required=True)
    backup.add_argument("--r2-prefix", required=True)
    backup.add_argument("--oci-prefix", required=True)
    backup.add_argument("--rclone", default="rclone")
    backup.add_argument("--timeout", type=int, default=900)
    backup.add_argument("--output", required=True)
    backup.add_argument("--semantic-contract", required=True)
    backup.add_argument("--delete-source", action="store_true")
    backup.set_defaults(func=cmd_backup)
    return root


def main(argv=None) -> int:
    try:
        args = parser().parse_args(argv)
        return int(args.func(args))
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
        print(json.dumps({"state": "FAILED", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 9


if __name__ == "__main__":
    raise SystemExit(main())
