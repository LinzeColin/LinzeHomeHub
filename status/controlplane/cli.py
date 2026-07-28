"""Operator CLI for deterministic status control-plane functions."""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Any

from .authority import AuthoritySyncError, sync_events
from .backup import build_manifest, verify_restore
from .collector import collect_control_plane
from .commands import run_command
from .db import RuntimeStore
from .detectors import detect
from .selfheal import heal_target, policy_from_json
from .state import atomic_write_json, load_json


def _repo(value: str) -> Path:
    path = Path(value).expanduser().resolve()
    if not (path / "status").is_dir():
        raise SystemExit(f"目标仓缺少 status 目录：{path}")
    return path


def _status(repo: Path) -> Path:
    return repo / "status"


def cmd_doctor(args) -> int:
    repo = _repo(args.repo)
    status = _status(repo)
    checks: dict[str, Any] = {
        "repository": detect(repo),
        "commands": {name: bool(shutil.which(name)) for name in ("git", "python3", "docker", "systemctl", "rclone")},
        "paths": {
            "status": status.is_dir(),
            "data": (status / "data").is_dir(),
            "private": (status / "private").is_dir(),
            "runtime": (status / "runtime").is_dir(),
        },
        "environment_keys_only": sorted(
            key for key in os.environ
            if key in {"PRIVATE_DB_CLIENT_PATH", "PRIVATE_DB_AREA", "LINZE_R2_REMOTE", "LINZE_OCI_REMOTE", "CF_TEAM_DOMAIN", "CF_ACCESS_AUD", "OWNER_EMAIL"}
        ),
        "runtime_invariants": {
            "agent_dependency": False,
            "llm_calls": 0,
            "token_budget": 0,
            "macos_launchd": False,
        },
    }
    output = Path(args.output) if args.output else status / "runtime" / "doctor.json"
    atomic_write_json(output, checks)
    print(json.dumps(checks, ensure_ascii=False, indent=2))
    return 0


def cmd_collect(args) -> int:
    repo = _repo(args.repo)
    status = _status(repo)
    collect_control_plane(
        status_path=status / "data" / "snapshot.json",
        github_path=status / "data" / "github_public.json",
        output_private=status / "private" / "control-plane.json",
        output_public=status / "data" / "control-plane.json",
    )
    return 0


def cmd_migrate(args) -> int:
    repo = _repo(args.repo)
    status = _status(repo)
    store = RuntimeStore(status / "runtime" / "status.db")
    store.migrate()
    print(json.dumps({"ok": True, "revision": store.current_revision()}, ensure_ascii=False))
    return 0


def cmd_import_legacy_prices(args) -> int:
    repo = _repo(args.repo)
    status = _status(repo)
    store = RuntimeStore(status / "runtime" / "status.db")
    store.migrate()
    if store.latest_fact("status.prices"):
        print(json.dumps({"state": "ALREADY_SATISFIED"}, ensure_ascii=False))
        return 0
    source = load_json(status / "data" / "prices.json", {"items": []}) or {"items": []}
    outcome = store.apply_command(
        idempotency_key="legacy-prices-import-v0.0.0.1",
        command_type="legacy_import",
        expected_revision=store.current_revision(),
        actor_hash="system-migration",
        payload=source,
        fact_type="status.prices",
    )
    print(json.dumps(outcome.to_dict(), ensure_ascii=False))
    return 0


def cmd_sync_authority(args) -> int:
    repo = _repo(args.repo)
    status = _status(repo)
    client_value = args.private_db_client or os.environ.get("PRIVATE_DB_CLIENT_PATH")
    area = args.area or os.environ.get("PRIVATE_DB_AREA", "Private-MetaDatabase")
    if not client_value:
        print(json.dumps({"state": "ENVIRONMENT_BLOCKED", "reason": "PRIVATE_DB_CLIENT_PATH unavailable"}, ensure_ascii=False))
        return 4
    store = RuntimeStore(status / "runtime" / "status.db")
    store.migrate()
    pending = store.pending_outbox(limit=args.limit)
    if not pending:
        print(json.dumps({"state": "NO_NEW_FACT", "count": 0}, ensure_ascii=False))
        return 0
    events = [item["payload"] for item in pending]
    try:
        result = sync_events(
            Path(client_value),
            events,
            area=area,
            prefix=args.prefix,
        )
    except AuthoritySyncError as exc:
        failed_at = datetime.now(timezone.utc)
        for item in pending:
            delay = min(3600, 60 * (2 ** min(int(item.get("attempts", 0)), 5)))
            next_attempt = (failed_at + timedelta(seconds=delay)).replace(microsecond=0).isoformat()
            store.mark_failed(item["event_id"], "AUTHORITY_SYNC_FAILED", next_attempt)
        print(json.dumps({"state": "FAILED", "error_code": "AUTHORITY_SYNC_FAILED", "detail": str(exc)}, ensure_ascii=False))
        return 7
    sent = set(result.get("sent_event_ids") or [])
    for item in pending:
        if item["event_id"] in sent:
            store.mark_sent(item["event_id"])
        else:
            delay = min(3600, 60 * (2 ** min(int(item.get("attempts", 0)), 5)))
            next_attempt = (datetime.now(timezone.utc) + timedelta(seconds=delay)).replace(microsecond=0).isoformat()
            store.mark_failed(item["event_id"], "AUTHORITY_READBACK_FAILED", next_attempt)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("state") in {"SYNCED", "NO_NEW_FACT"} and not result.get("failed_event_ids") else 7


def cmd_selfheal(args) -> int:
    policy = policy_from_json(json.loads(Path(args.policy).read_text(encoding="utf-8")))
    outcome = heal_target(
        policy,
        state_path=Path(args.state),
        lock_path=Path(args.lock),
    )
    print(json.dumps(outcome.to_dict(), ensure_ascii=False, indent=2))
    return 0 if outcome.state in {"HEALTHY", "RECOVERED", "COOLDOWN"} else 5


def cmd_manifest(args) -> int:
    root = Path(args.root).resolve()
    files = [path for path in root.rglob("*") if path.is_file()]
    manifest = build_manifest(root, files, encryption_profile=args.encryption_profile)
    atomic_write_json(Path(args.output), manifest)
    return 0


def cmd_verify_restore(args) -> int:
    manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    result = verify_restore(manifest, Path(args.restored_root))
    if args.output:
        atomic_write_json(Path(args.output), result)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["state"] == "RESTORE_VERIFIED" else 6


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(prog="status-control-plane")
    sub = value.add_subparsers(dest="command", required=True)

    doctor = sub.add_parser("doctor")
    doctor.add_argument("--repo", default=".")
    doctor.add_argument("--output")
    doctor.set_defaults(func=cmd_doctor)

    collect = sub.add_parser("collect")
    collect.add_argument("--repo", default=".")
    collect.set_defaults(func=cmd_collect)

    migrate = sub.add_parser("migrate")
    migrate.add_argument("--repo", default=".")
    migrate.set_defaults(func=cmd_migrate)

    legacy = sub.add_parser("import-legacy-prices")
    legacy.add_argument("--repo", default=".")
    legacy.set_defaults(func=cmd_import_legacy_prices)

    sync = sub.add_parser("sync-authority")
    sync.add_argument("--repo", default=".")
    sync.add_argument("--private-db-client")
    sync.add_argument("--area", default="Private-MetaDatabase")
    sync.add_argument("--prefix", default="facts/status")
    sync.add_argument("--limit", type=int, default=100)
    sync.set_defaults(func=cmd_sync_authority)

    heal = sub.add_parser("selfheal")
    heal.add_argument("--policy", required=True)
    heal.add_argument("--state", required=True)
    heal.add_argument("--lock", required=True)
    heal.set_defaults(func=cmd_selfheal)

    manifest = sub.add_parser("build-backup-manifest")
    manifest.add_argument("--root", required=True)
    manifest.add_argument("--output", required=True)
    manifest.add_argument("--encryption-profile", default="rclone-crypt")
    manifest.set_defaults(func=cmd_manifest)

    restore = sub.add_parser("verify-backup")
    restore.add_argument("--manifest", required=True)
    restore.add_argument("--restored-root", required=True)
    restore.add_argument("--output")
    restore.set_defaults(func=cmd_verify_restore)
    return value


def main(argv=None) -> int:
    args = parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
