# STATUS_AGENT_V3_MANAGED: v0.0.0.3
from __future__ import annotations

import argparse
from hashlib import sha256
import json
import os
from pathlib import Path
import sys
from typing import Any, Mapping
import uuid

from . import __version__
from .authority import sync_events
from .canonical import atomic_write, canonical_bytes, utc_now
from .capture import run_provider, validate_session_receipt, validated_raw_object_candidate
from .dossier import build_dossier, validate_dossier
from .object_store import upload_and_readback
from .operations import doctor as operations_doctor, rollback_from_receipt, start, status as operations_status, stop
from .projection import derive_projection, public_allowlist
from .provider import default_provider_command, discover
from .restore import copy_and_restore


def load(path: Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write(path: Path, value: Any) -> None:
    atomic_write(Path(path), canonical_bytes(value))


def emit(value: Mapping[str, Any], code: int = 0) -> int:
    print(json.dumps(dict(value), ensure_ascii=False, sort_keys=True))
    return code


def iter_receipts(root: Path) -> list[dict[str, Any]]:
    root = Path(root).expanduser().resolve()
    paths = [root] if root.is_file() else sorted(root.rglob("*.json")) if root.is_dir() else []
    receipts: list[dict[str, Any]] = []
    for path in paths:
        try:
            value = load(path)
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue
        if isinstance(value, dict) and value.get("receipt_id") and value.get("provider") in {"codex", "claude"}:
            receipts.append(value)
    return receipts


def _validated_real_receipts(root: Path) -> list[dict[str, Any]]:
    receipts = iter_receipts(root)
    if not receipts:
        raise ValueError("no session receipts found")
    failures: list[str] = []
    for receipt in receipts:
        validation = validate_session_receipt(receipt, require_real=True)
        if validation["state"] != "PASS":
            failures.append(f"{receipt.get('receipt_id') or 'unknown'}:{','.join(validation['errors'])}")
    if failures:
        raise ValueError("non-promotable session receipts: " + ";".join(failures))
    return receipts


def _outside_repo(path: Path, repo: Path, *, label: str) -> Path:
    resolved = Path(path).expanduser().resolve()
    try:
        resolved.relative_to(repo)
    except ValueError:
        return resolved
    raise ValueError(f"{label} must be outside --repo to keep Candidate read-only")


def candidate_fields_from_receipts(path: Path) -> dict[str, Any]:
    receipts = iter_receipts(path)
    if not receipts:
        raise ValueError("Candidate fields require at least one real session receipt")
    invalid = []
    for receipt in receipts:
        validation = validate_session_receipt(receipt, require_real=True)
        if validation["state"] != "PASS":
            invalid.append(f"{receipt.get('receipt_id') or 'unknown'}:{','.join(validation['errors'])}")
    if invalid:
        raise ValueError("Candidate fields reject non-promotable session receipts: " + ";".join(invalid))
    providers = sorted({str(receipt.get("provider")) for receipt in receipts})
    refs = sorted(str(receipt.get("receipt_id")) for receipt in receipts if receipt.get("receipt_id"))
    successes = sorted(str(receipt.get("receipt_id")) for receipt in receipts if receipt.get("state") == "PASS")
    failures = sorted(str(receipt.get("receipt_id")) for receipt in receipts if receipt.get("state") != "PASS")
    return {
        "candidate_type": "SKILL",
        "title": "受控 Agent 会话捕获与证据闭环",
        "trigger_problem": "跨 Agent 会话、任务、Commit、Gate 与证据缺乏统一可追溯闭环。",
        "applicable_when": ["Codex 或 Claude Code 开发任务需要形成可复用且可审计的方法"],
        "not_applicable_when": ["生产运行热路径", "未经 Owner 批准的自动 Skill 安装"],
        "inputs": ["Intent Packet", "Provider Session Receipt", "Exact Subject", "Signed Gate Verdict"],
        "outputs": ["Candidate Dossier", "Evidence references", "Replay benchmark contract"],
        "required_permissions": ["开发期 Provider CLI", "只读仓库", "受保护 Gate 验证"],
        "method": ["捕获", "脱敏", "对象存储", "权威引用", "独立 Gate", "候选分类"],
        "success_samples": successes,
        "correction_samples": ["缺少 transcript、对象 readback 或 Subject 绑定时保持 BLOCKED"],
        "failure_samples": failures or ["known-bad self-attestation fixture"],
        "safety_risks": ["秘密泄漏", "Agent 自证完成", "旧证据冒充当前证据", "自动安装错误 Skill"],
        "rollback": ["删除本 run spool 与精确 fixture prefix", "保留失败 receipt 和权威事实"],
        "source_session_refs": refs,
        "portability": {"providers": providers, "cross_project": True},
        "duplicate_or_alternative": ["进入 CodexSkills 前按 capability/identity/provenance 查重"],
        "replay_benchmark": {"baseline": "人工拼接", "candidate": "自动 receipt", "metrics": ["任务轮次", "返工轮次", "证据整理时间", "Token"]},
        "confidence": "medium",
    }


def _object_items_from_receipts(receipts_root: Path, rclone: str, remote: str, prefix: str) -> dict[str, Any]:
    receipts = _validated_real_receipts(receipts_root)
    items = []
    for receipt in receipts:
        if receipt.get("state") != "PASS":
            items.append({"session_receipt_id": receipt.get("receipt_id"), "state": "BLOCKED", "reason": "SESSION_RECEIPT_NOT_PASS"})
            continue
        source = validated_raw_object_candidate(receipt)
        provider = str(receipt.get("provider"))
        session_id = str(receipt.get("session_id")).replace(":", "-")
        object_key = f"{prefix.strip('/')}/{provider}/{session_id}/{source.name}"
        result = upload_and_readback(source=source, rclone_binary=rclone, crypt_remote=remote, object_key=object_key)
        items.append({"session_receipt_id": receipt.get("receipt_id"), **result})
    state = "PASS" if items and all(item.get("state") == "READBACK_VERIFIED" for item in items) else "BLOCKED"
    return {"schema_version": "status.object_batch_receipt.v3", "state": state, "items": items, "completed_at": utc_now()}


def _authority_events(receipts_root: Path, object_receipt: Path | None) -> list[dict[str, Any]]:
    object_by_session: dict[str, Any] = {}
    if object_receipt and object_receipt.is_file():
        for item in load(object_receipt).get("items", []):
            object_by_session[str(item.get("session_receipt_id"))] = {
                "object_key": item.get("object_key"),
                "remote_path": item.get("remote_path"),
                "plaintext_sha256": item.get("plaintext_sha256"),
                "object_receipt_id": item.get("object_receipt_id"),
            }
    events = []
    for receipt in _validated_real_receipts(receipts_root):
        receipt_id = str(receipt.get("receipt_id"))
        events.append({
            "event_id": receipt_id,
            "fact_type": "agent.session.receipt",
            "completed_at": receipt.get("completed_at"),
            "summary": {
                "provider": receipt.get("provider"),
                "state": receipt.get("state"),
                "events_sha256": receipt.get("events_sha256"),
                "intent_sha256": receipt.get("intent_sha256"),
                "capture_source": receipt.get("capture_source"),
                "provider_transcript_discovered": receipt.get("provider_transcript_discovered"),
                "object": object_by_session.get(receipt_id),
            },
        })
    return events


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="status-agent-v3")
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("doctor", "diagnose"):
        command = sub.add_parser(name)
        command.add_argument("--config", type=Path)
    p_run = sub.add_parser("run")
    p_run.add_argument("provider", choices=["codex", "claude"])
    p_run.add_argument("--intent", type=Path, required=True)
    p_run.add_argument("--repo", type=Path, required=True)
    p_run.add_argument("--spool-root", type=Path, default=Path(os.environ.get("STATUS_AGENT_SPOOL", "/tmp/status-agent-v3")))
    p_run.add_argument("--receipt-output", type=Path, required=True)
    p_run.add_argument("--development-session", action="store_true")
    p_run.add_argument("--command-json", type=Path)
    p_run.add_argument("--session-root", type=Path, action="append")
    p_run.add_argument("--test-only-process-output-fallback", action="store_true")
    p_status = sub.add_parser("status")
    p_status.add_argument("--config", type=Path)
    p_sync = sub.add_parser("authority-sync")
    p_sync.add_argument("--client", type=Path, default=Path(os.environ.get("PRIVATE_DB_CLIENT", "")))
    p_sync.add_argument("--receipts", type=Path, required=True)
    p_sync.add_argument("--object-receipt", type=Path)
    p_sync.add_argument("--output", type=Path, required=True)
    p_object = sub.add_parser("object-upload")
    p_object.add_argument("--receipts", type=Path, required=True)
    p_object.add_argument("--rclone", default="rclone")
    p_object.add_argument("--remote", default=os.environ.get("STATUS_R2_CRYPT_REMOTE", ""))
    p_object.add_argument("--prefix", default="primary-objects/status-agent-v3")
    p_object.add_argument("--output", type=Path, required=True)
    p_backup = sub.add_parser("backup")
    p_backup.add_argument("--source", type=Path, required=True)
    p_backup.add_argument("--rclone", default="rclone")
    p_backup.add_argument("--remote", default=os.environ.get("STATUS_R2_CRYPT_REMOTE", ""))
    p_backup.add_argument("--object-key", required=True)
    p_backup.add_argument("--output", type=Path, required=True)
    p_restore = sub.add_parser("backup-restore")
    p_restore.add_argument("--r2-receipt", type=Path, required=True)
    p_restore.add_argument("--oci-remote", default=os.environ.get("STATUS_OCI_REMOTE", ""))
    p_restore.add_argument("--rclone", default="rclone")
    p_restore.add_argument("--output", type=Path, required=True)
    p_single_restore = sub.add_parser("restore")
    p_single_restore.add_argument("--r2-path", required=True)
    p_single_restore.add_argument("--oci-path", required=True)
    p_single_restore.add_argument("--expected-sha256", required=True)
    p_single_restore.add_argument("--rclone", default="rclone")
    p_single_restore.add_argument("--output", type=Path, required=True)
    p_candidate = sub.add_parser("candidate")
    p_candidate.add_argument("action", choices=["prepare", "build", "validate"])
    p_candidate.add_argument("--input", type=Path, required=True)
    p_candidate.add_argument("--output", type=Path, required=True)
    p_candidate.add_argument("--run-id")
    p_candidate.add_argument("--subject", type=Path)
    p_candidate.add_argument("--gate", type=Path)
    p_candidate.add_argument("--trust-root", type=Path)
    p_project = sub.add_parser("project")
    p_project.add_argument("--input", type=Path, required=True)
    p_project.add_argument("--output", type=Path, required=True)
    configured_trust_root = os.environ.get("STATUS_GATE_TRUST_ROOT")
    p_project.add_argument("--trust-root", type=Path, default=Path(configured_trust_root) if configured_trust_root else None)
    for name in ("start", "stop"):
        command = sub.add_parser(name)
        command.add_argument("--config", type=Path, required=True)
        command.add_argument("--apply", action="store_true")
    p_rollback = sub.add_parser("rollback")
    p_rollback.add_argument("--receipt", type=Path, required=True)
    p_rollback.add_argument("--apply", action="store_true")
    p_finalize = sub.add_parser("finalize-release")
    p_finalize.add_argument("--input", type=Path, required=True)
    p_finalize.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command in {"doctor", "diagnose"}:
            config = load(args.config) if args.config else {"private_db_client": os.environ.get("PRIVATE_DB_CLIENT", "")}
            result = operations_doctor(config)
            result["providers"] = [discover("codex"), discover("claude")]
            result["diagnostic_mode"] = args.command == "diagnose"
            return emit(result, 0 if result["state"] == "PASS" else 2)
        if args.command == "run":
            repo = Path(args.repo).expanduser().resolve()
            if not repo.is_dir():
                raise ValueError("--repo must be an existing directory")
            if not args.development_session:
                raise ValueError("provider sessions are development-only; pass --development-session explicitly")
            _outside_repo(args.spool_root, repo, label="--spool-root")
            _outside_repo(args.receipt_output, repo, label="--receipt-output")
            if (args.command_json or args.session_root) and not args.test_only_process_output_fallback:
                raise ValueError("--command-json and --session-root are test-only and require --test-only-process-output-fallback")
            if args.test_only_process_output_fallback and not args.command_json:
                raise ValueError("test-only process output fallback requires --command-json")
            run_id = "run:" + uuid.uuid4().hex
            transcript_binding = "status-agent-v3-binding:" + uuid.uuid4().hex
            command = load(args.command_json) if args.command_json else default_provider_command(args.provider, intent_path=args.intent, repo_root=repo, session_binding=transcript_binding)
            if not isinstance(command, list) or not command or any(not isinstance(item, str) for item in command):
                raise ValueError("provider command must be a JSON argv array")
            result = run_provider(
                args.provider,
                command,
                intent_path=args.intent,
                spool_root=args.spool_root,
                run_id=run_id,
                cwd=repo,
                session_roots=args.session_root,
                transcript_binding=transcript_binding,
                require_transcript=True,
                test_only_process_output_fallback=args.test_only_process_output_fallback,
                verified_provider_command=not args.test_only_process_output_fallback,
            )
            write(args.receipt_output, result)
            return emit(result, 0 if result["state"] == "PASS" else 2)
        if args.command == "status":
            config = load(args.config) if args.config else {"existing_systemd_units": []}
            runtime = operations_status(config, apply=False)
            return emit({
                "schema_version": 3,
                "state": "READY_FOR_DEVELOPMENT_RUN",
                "runtime": runtime,
                "daemon_running": False,
                "production_agent_dependency": 0,
                "production_llm_calls": 0,
                "production_token_consumption": 0,
                "launchd_required": False,
            })
        if args.command == "authority-sync":
            events = _authority_events(args.receipts, args.object_receipt)
            result = sync_events(args.client, events, area="Private-AgentDatabase", prefix="facts/status/agent-sessions")
            write(args.output, result)
            return emit(result, 0 if result["state"] in {"SYNCED", "NO_NEW_FACT"} else 2)
        if args.command == "object-upload":
            result = _object_items_from_receipts(args.receipts, args.rclone, args.remote, args.prefix)
            write(args.output, result)
            return emit(result, 0 if result["state"] == "PASS" else 2)
        if args.command == "backup":
            result = upload_and_readback(source=args.source, rclone_binary=args.rclone, crypt_remote=args.remote, object_key=args.object_key)
            write(args.output, result)
            return emit(result)
        if args.command == "backup-restore":
            r2 = load(args.r2_receipt)
            items = []
            for item in r2.get("items", []):
                if item.get("state") != "READBACK_VERIFIED":
                    items.append({"state": "BLOCKED", "reason": "R2_ITEM_NOT_VERIFIED", "session_receipt_id": item.get("session_receipt_id")})
                    continue
                object_name = Path(str(item.get("object_key") or "")).name
                plaintext_sha256 = str(item.get("plaintext_sha256") or "").lower()
                if (
                    object_name in {"", ".", ".."}
                    or len(plaintext_sha256) != 64
                    or any(character not in "0123456789abcdef" for character in plaintext_sha256)
                ):
                    items.append({"state": "BLOCKED", "reason": "R2_OBJECT_IDENTITY_INVALID", "session_receipt_id": item.get("session_receipt_id")})
                    continue
                # Basenames are not unique across provider transcripts. Keep a
                # readable suffix, but make the OCI replica content-addressed
                # so two same-named, different session objects cannot collide.
                oci_key = f"backups/status-agent-v3/{plaintext_sha256[:2]}/{plaintext_sha256}_{object_name}"
                oci_path = f"{args.oci_remote.rstrip(':')}:{oci_key}"
                restored = copy_and_restore(rclone_binary=args.rclone, r2_remote_path=str(item["remote_path"]), oci_remote_path=oci_path, expected_sha256=plaintext_sha256)
                items.append({"session_receipt_id": item.get("session_receipt_id"), **restored})
            result = {"schema_version": "status.oci_restore_batch.v3", "state": "PASS" if items and all(item.get("state") == "RESTORE_VERIFIED" for item in items) else "BLOCKED", "items": items, "completed_at": utc_now()}
            write(args.output, result)
            return emit(result, 0 if result["state"] == "PASS" else 2)
        if args.command == "restore":
            result = copy_and_restore(rclone_binary=args.rclone, r2_remote_path=args.r2_path, oci_remote_path=args.oci_path, expected_sha256=args.expected_sha256)
            write(args.output, result)
            return emit(result)
        if args.command == "candidate":
            if args.action == "prepare":
                result = candidate_fields_from_receipts(args.input)
            elif args.action == "validate":
                result = validate_dossier(load(args.input))
            else:
                if not all((args.run_id, args.subject, args.gate, args.trust_root)):
                    raise ValueError("candidate build requires --run-id --subject --gate --trust-root")
                result = build_dossier(run_id=args.run_id, subject=load(args.subject), gate_verdict=load(args.gate), trust_root=args.trust_root, fields=load(args.input))
            write(args.output, result)
            return emit(result, 0 if result.get("state") in {"PASS", "PROPOSED"} or args.action == "prepare" else 2)
        if args.command == "project":
            bundle = load(args.input)
            if not args.trust_root:
                raise ValueError("project requires --trust-root or STATUS_GATE_TRUST_ROOT")
            result = derive_projection(accepted_subject=bundle.get("accepted_subject"), current_subject=bundle.get("current_subject"), verdict=bundle.get("verdict"), trust_root=args.trust_root)
            result = public_allowlist(result)
            write(args.output, result)
            return emit(result, 0 if result.get("state") in {"READY", "UNKNOWN", "STALE", "BLOCKED"} else 2)
        if args.command in {"start", "stop"}:
            config = load(args.config)
            result = start(config, args.apply) if args.command == "start" else stop(config, args.apply)
            return emit(result, 0 if result["state"] in {"PASS", "DRY_RUN", "NO_UNITS"} else 2)
        if args.command == "rollback":
            result = rollback_from_receipt(load(args.receipt), args.apply)
            return emit(result, 0 if result["state"] in {"PASS", "DRY_RUN"} else 2)
        if args.command == "finalize-release":
            paths = [args.input] if args.input.is_file() else sorted(args.input.rglob("*.json")) if args.input.is_dir() else []
            receipts = []
            for path in paths:
                try:
                    value = load(path)
                except Exception:
                    continue
                if isinstance(value, dict) and value.get("state"):
                    receipts.append({"path": str(path), "state": value.get("state"), "receipt_id": value.get("receipt_id")})
            non_pass = [item for item in receipts if item["state"] not in {"PASS", "VERIFIED", "POST_DEPLOY_VERIFIED", "READBACK_VERIFIED", "RESTORE_VERIFIED", "SYNCED", "NO_NEW_FACT"}]
            if not receipts or non_pass:
                result = {"schema_version": 3, "state": "BLOCKED", "reason": "RELEASE_RECEIPTS_INCOMPLETE", "non_pass": non_pass}
            else:
                result = {"schema_version": 3, "state": "FINALIZED", "receipts": receipts, "no_empty_commit": True, "completed_at": utc_now()}
            write(args.output, result)
            return emit(result, 0 if result["state"] == "FINALIZED" else 2)
    except Exception as exc:
        return emit({"state": "BLOCKED", "error_code": exc.__class__.__name__, "detail": str(exc)[:1000]}, 2)
    return emit({"state": "BLOCKED", "error_code": "UNKNOWN_COMMAND"}, 2)


if __name__ == "__main__":
    raise SystemExit(main())
