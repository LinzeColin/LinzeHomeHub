# STATUS_AGENT_V3_MANAGED: v0.0.0.9
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import subprocess
from typing import Any, Iterable, Mapping, Sequence
import uuid

from .canonical import atomic_write, canonical_bytes, digest, utc_now
from .provider import (
    changed_transcript,
    changed_transcript_candidates,
    copy_transcript,
    snapshot_session_files,
    transcript_binding_candidates,
    transcript_binding_sha256,
    validate_transcript_binding,
)

CRITICAL_EVENTS = ("session_start", "process_start", "process_end", "session_end")
FORBIDDEN_FIELD_NAMES = {"secret", "password", "cookie", "access_token", "api_key", "private_key", "raw_prompt", "raw_transcript"}
SECRET_PATTERNS = [
    re.compile(r"(?i)(api[_-]?key|access[_-]?token|password|secret)\s*[:=]\s*[^\s,;]+"),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
]
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
REAL_CAPTURE_QUALIFICATION = "REAL_PROVIDER_TRANSCRIPT"
TEST_CAPTURE_QUALIFICATION = "TEST_ONLY_NON_PROMOTABLE"
PROCESS_OUTPUT_DIAGNOSTIC_SCHEMA_VERSION = 1
PROCESS_OUTPUT_DIAGNOSTIC_MAX_BYTES = 1024 * 1024
PROCESS_OUTPUT_DIAGNOSTIC_FIELDS = {
    "schema_version",
    "state",
    "classification",
    "input_bytes",
    "automatic_recovery_allowed",
    "raw_excerpt_emitted",
    "raw_retained_by_classifier",
}
PROCESS_OUTPUT_DIAGNOSTIC_SIGNATURES = (
    (
        "SIGNATURE_AUTHENTICATION",
        (
            b"not logged in",
            b"invalid api key",
            b"oauth token revoked",
            b"oauth token has expired",
            b"authentication_error",
        ),
    ),
    (
        "SIGNATURE_CAPACITY_OR_RATE_LIMIT",
        (
            b"request rejected (429)",
            b"hit your session limit",
            b"hit your weekly limit",
            b"credit balance is too low",
            b"temporarily limiting requests",
        ),
    ),
    (
        "SIGNATURE_NETWORK_OR_TLS",
        (
            b"tls connect error",
            b"ssl/tls secure channel",
            b"could not resolve host",
            b"connection timeout",
            b"network error",
            b"econnreset",
            b"enotfound",
        ),
    ),
    (
        "SIGNATURE_CLI_ARGUMENT_OR_VALIDATION",
        (
            b"unknown option",
            b"unknown argument",
            b"unrecognized option",
            b"invalid option",
            b"missing required argument",
        ),
    ),
    (
        "SIGNATURE_LOCAL_RUNTIME",
        (
            b"command not found",
            b"module not found",
            b"cannot find module",
            b"dyld: cannot load",
            b"exec format error",
            b"illegal instruction",
        ),
    ),
    (
        "SIGNATURE_POLICY_OR_PERMISSION",
        (
            b"403 forbidden",
            b"organization has been disabled",
            b"permission denied",
            b"request blocked by policy",
        ),
    ),
    (
        "SIGNATURE_TRANSIENT_SERVER",
        (
            b"api error: 500",
            b"529 overloaded",
            b"internal server error",
            b"temporarily unavailable",
            b"request timed out",
        ),
    ),
)
PROCESS_OUTPUT_DIAGNOSTIC_ALLOWED_CLASSIFICATIONS = {
    "EXIT_ZERO",
    "EMPTY_OUTPUT",
    "NO_ALLOWED_SIGNATURE",
    "AMBIGUOUS_ALLOWED_SIGNATURES",
    "SENSITIVE_OUTPUT_DETECTED",
    "BINARY_OUTPUT_UNSUPPORTED",
    "OUTPUT_EXCEEDS_LIMIT",
    "OUTPUT_UNAVAILABLE",
    *(category for category, _ in PROCESS_OUTPUT_DIAGNOSTIC_SIGNATURES),
}
PROCESS_OUTPUT_SENSITIVE_PATTERNS = (
    re.compile(rb"(?i)(?:api[_-]?key|access[_-]?token|auth(?:entication)?[_-]?token|password|secret)\s*[:=]\s*[^\s,;]+"),
    re.compile(rb"(?i)\bbearer\s+[a-z0-9._~+/=-]{8,}"),
    re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
)


def _scan(value: Any, path: str = "$") -> list[str]:
    findings: list[str] = []
    if isinstance(value, Mapping):
        for key, child in value.items():
            normalized = str(key).lower()
            if normalized in FORBIDDEN_FIELD_NAMES or normalized.endswith("_token") or normalized.endswith("_secret"):
                findings.append(f"{path}.{key}")
            findings.extend(_scan(child, f"{path}.{key}"))
    elif isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            findings.extend(_scan(child, f"{path}[{index}]"))
    elif isinstance(value, str):
        for pattern in SECRET_PATTERNS:
            if pattern.search(value):
                findings.append(path)
                break
    return findings


def _valid_sha256(value: Any) -> bool:
    return bool(SHA256_RE.fullmatch(str(value or "").lower()))


def _process_output_diagnostic(state: str, classification: str, input_bytes: int | None) -> dict[str, Any]:
    """Return a fixed-schema diagnostic that cannot contain a raw output excerpt."""
    return {
        "schema_version": PROCESS_OUTPUT_DIAGNOSTIC_SCHEMA_VERSION,
        "state": state,
        "classification": classification,
        "input_bytes": input_bytes,
        "automatic_recovery_allowed": False,
        "raw_excerpt_emitted": False,
        "raw_retained_by_classifier": False,
    }


def classify_provider_process_output(path: Path, *, exit_code: int) -> dict[str, Any]:
    """Classify a bounded private process output without emitting or retaining its bytes.

    A classification is an observed allowlisted signature, never a causal conclusion
    and never authorization for a retry or configuration mutation.  Sensitive or
    oversized output intentionally produces a blocked diagnostic rather than a label.
    """
    try:
        source = Path(path)
        if source.is_symlink() or not source.is_file():
            return _process_output_diagnostic("BLOCKED", "OUTPUT_UNAVAILABLE", None)
        input_bytes = source.stat().st_size
    except OSError:
        return _process_output_diagnostic("BLOCKED", "OUTPUT_UNAVAILABLE", None)
    if input_bytes < 0 or input_bytes > PROCESS_OUTPUT_DIAGNOSTIC_MAX_BYTES:
        return _process_output_diagnostic("BLOCKED", "OUTPUT_EXCEEDS_LIMIT", input_bytes)
    if int(exit_code) == 0:
        return _process_output_diagnostic("NOT_APPLICABLE", "EXIT_ZERO", input_bytes)
    try:
        output = source.read_bytes()
    except OSError:
        return _process_output_diagnostic("BLOCKED", "OUTPUT_UNAVAILABLE", input_bytes)
    if len(output) != input_bytes:
        return _process_output_diagnostic("BLOCKED", "OUTPUT_UNAVAILABLE", input_bytes)
    if not output:
        return _process_output_diagnostic("UNCLASSIFIED", "EMPTY_OUTPUT", input_bytes)
    if any(pattern.search(output) for pattern in PROCESS_OUTPUT_SENSITIVE_PATTERNS):
        return _process_output_diagnostic("BLOCKED", "SENSITIVE_OUTPUT_DETECTED", input_bytes)
    if b"\x00" in output:
        return _process_output_diagnostic("BLOCKED", "BINARY_OUTPUT_UNSUPPORTED", input_bytes)
    normalized = b" ".join(output.lower().split())
    matches = [
        category
        for category, signatures in PROCESS_OUTPUT_DIAGNOSTIC_SIGNATURES
        if any(signature in normalized for signature in signatures)
    ]
    if len(matches) == 1:
        return _process_output_diagnostic("CLASSIFIED", matches[0], input_bytes)
    if len(matches) > 1:
        return _process_output_diagnostic("UNCLASSIFIED", "AMBIGUOUS_ALLOWED_SIGNATURES", input_bytes)
    return _process_output_diagnostic("UNCLASSIFIED", "NO_ALLOWED_SIGNATURE", input_bytes)


def _validate_process_output_diagnostic(value: Any) -> list[str]:
    errors: list[str] = []
    if not isinstance(value, Mapping):
        return ["PROCESS_OUTPUT_DIAGNOSTIC_NOT_OBJECT"]
    diagnostic = dict(value)
    if set(diagnostic) != PROCESS_OUTPUT_DIAGNOSTIC_FIELDS:
        errors.append("PROCESS_OUTPUT_DIAGNOSTIC_FIELDS_INVALID")
    if diagnostic.get("schema_version") != PROCESS_OUTPUT_DIAGNOSTIC_SCHEMA_VERSION:
        errors.append("PROCESS_OUTPUT_DIAGNOSTIC_SCHEMA_INVALID")
    state = diagnostic.get("state")
    classification = diagnostic.get("classification")
    if state not in {"NOT_APPLICABLE", "CLASSIFIED", "UNCLASSIFIED", "BLOCKED"}:
        errors.append("PROCESS_OUTPUT_DIAGNOSTIC_STATE_INVALID")
    if classification not in PROCESS_OUTPUT_DIAGNOSTIC_ALLOWED_CLASSIFICATIONS:
        errors.append("PROCESS_OUTPUT_DIAGNOSTIC_CLASSIFICATION_INVALID")
    if type(diagnostic.get("input_bytes")) is not int or int(diagnostic["input_bytes"]) < 0:
        errors.append("PROCESS_OUTPUT_DIAGNOSTIC_BYTES_INVALID")
    for key in ("automatic_recovery_allowed", "raw_excerpt_emitted", "raw_retained_by_classifier"):
        if diagnostic.get(key) is not False:
            errors.append(f"PROCESS_OUTPUT_DIAGNOSTIC_{key.upper()}_INVALID")
    if state == "NOT_APPLICABLE" and classification != "EXIT_ZERO":
        errors.append("PROCESS_OUTPUT_DIAGNOSTIC_EXIT_ZERO_INVALID")
    if state == "CLASSIFIED" and classification not in {category for category, _ in PROCESS_OUTPUT_DIAGNOSTIC_SIGNATURES}:
        errors.append("PROCESS_OUTPUT_DIAGNOSTIC_CLASSIFIED_VALUE_INVALID")
    if state == "UNCLASSIFIED" and classification not in {"EMPTY_OUTPUT", "NO_ALLOWED_SIGNATURE", "AMBIGUOUS_ALLOWED_SIGNATURES"}:
        errors.append("PROCESS_OUTPUT_DIAGNOSTIC_UNCLASSIFIED_VALUE_INVALID")
    if state == "BLOCKED" and classification not in {"SENSITIVE_OUTPUT_DETECTED", "BINARY_OUTPUT_UNSUPPORTED", "OUTPUT_EXCEEDS_LIMIT", "OUTPUT_UNAVAILABLE"}:
        errors.append("PROCESS_OUTPUT_DIAGNOSTIC_BLOCKED_VALUE_INVALID")
    return sorted(set(errors))


def sanitize_event(event: Mapping[str, Any]) -> dict[str, Any]:
    body = dict(event)
    findings = _scan(body)
    if findings:
        raise ValueError("secret/raw fields detected: " + ",".join(sorted(set(findings))))
    body.setdefault("schema_version", 3)
    body.setdefault("event_id", "event:" + uuid.uuid4().hex)
    body.setdefault("occurred_at", utc_now())
    return body


def validate_session_receipt(value: Any, *, require_real: bool = False) -> dict[str, Any]:
    """Validate receipt integrity and promotion eligibility without trusting caller state.

    A receipt digest catches accidental tampering.  It is not a signature and therefore
    cannot replace the independent Gate.  `require_real` is deliberately strict enough
    to keep test-only stdout fixtures out of Candidate, authority, and object paths.
    """
    errors: list[str] = []
    if not isinstance(value, Mapping):
        return {"state": "BLOCKED", "errors": ["SESSION_RECEIPT_NOT_OBJECT"]}
    receipt = dict(value)
    receipt_id = str(receipt.get("receipt_id") or "")
    body = dict(receipt)
    body.pop("receipt_id", None)
    if receipt.get("schema_version") != 3:
        errors.append("SESSION_RECEIPT_SCHEMA_INVALID")
    if receipt_id != "session-receipt:" + digest(body):
        errors.append("SESSION_RECEIPT_DIGEST_INVALID")
    if receipt.get("provider") not in {"codex", "claude"}:
        errors.append("SESSION_RECEIPT_PROVIDER_INVALID")
    for key in ("run_id", "session_id", "completed_at", "spool_dir"):
        if not isinstance(receipt.get(key), str) or not str(receipt.get(key)).strip():
            errors.append(f"SESSION_RECEIPT_{key.upper()}_MISSING")
    for key in ("intent_sha256", "events_sha256"):
        if not _valid_sha256(receipt.get(key)):
            errors.append(f"SESSION_RECEIPT_{key.upper()}_INVALID")
    if not isinstance(receipt.get("event_counts"), Mapping) or not isinstance(receipt.get("event_count"), int):
        errors.append("SESSION_RECEIPT_EVENTS_INVALID")
    if not isinstance(receipt.get("blockers"), list):
        errors.append("SESSION_RECEIPT_BLOCKERS_INVALID")
    if receipt.get("raw_in_git_allowed") is not False:
        errors.append("SESSION_RECEIPT_RAW_GIT_POLICY_INVALID")
    if require_real:
        if receipt.get("state") not in {"PASS", "BLOCKED"}:
            errors.append("SESSION_RECEIPT_NON_REAL_STATE")
        if receipt.get("test_only_process_output_fallback") is not False:
            errors.append("TEST_ONLY_PROCESS_OUTPUT_FALLBACK")
        if receipt.get("capture_qualification") != REAL_CAPTURE_QUALIFICATION:
            errors.append("SESSION_RECEIPT_CAPTURE_NOT_REAL")
        if receipt.get("provider_command_contract_verified") is not True:
            errors.append("SESSION_RECEIPT_PROVIDER_COMMAND_CONTRACT_UNVERIFIED")
        if receipt.get("capture_source") != "provider_session_file":
            errors.append("SESSION_RECEIPT_CAPTURE_SOURCE_INVALID")
        if receipt.get("provider_transcript_discovered") is not True:
            errors.append("SESSION_RECEIPT_TRANSCRIPT_NOT_DISCOVERED")
        if receipt.get("provider_transcript_binding_matched") is not True:
            errors.append("SESSION_RECEIPT_TRANSCRIPT_BINDING_UNMATCHED")
        if not _valid_sha256(receipt.get("transcript_binding_sha256")):
            errors.append("SESSION_RECEIPT_TRANSCRIPT_BINDING_INVALID")
        if not isinstance(receipt.get("provider_transcript_candidate_count"), int) or receipt.get("provider_transcript_candidate_count", 0) < 1:
            errors.append("SESSION_RECEIPT_TRANSCRIPT_CANDIDATES_INVALID")
        if receipt.get("provider_transcript_binding_match_count") != 1:
            errors.append("SESSION_RECEIPT_TRANSCRIPT_BINDING_MATCH_COUNT_INVALID")
        for key in ("provider_transcript_sha256", "raw_object_candidate_sha256"):
            if not _valid_sha256(receipt.get(key)):
                errors.append(f"SESSION_RECEIPT_{key.upper()}_INVALID")
        if receipt.get("provider_transcript_sha256") != receipt.get("raw_object_candidate_sha256"):
            errors.append("SESSION_RECEIPT_RAW_TRANSCRIPT_DIGEST_MISMATCH")
        if not isinstance(receipt.get("raw_object_candidate_path"), str) or not str(receipt.get("raw_object_candidate_path")).strip():
            errors.append("SESSION_RECEIPT_RAW_PATH_MISSING")
        errors.extend(_validate_process_output_diagnostic(receipt.get("process_output_diagnostic")))
    return {"state": "PASS" if not errors else "BLOCKED", "errors": sorted(set(errors))}


def validated_raw_object_candidate(receipt: Mapping[str, Any]) -> Path:
    """Return a real captured transcript only after containment and digest checks."""
    validation = validate_session_receipt(receipt, require_real=True)
    if validation["state"] != "PASS":
        raise ValueError("invalid real session receipt: " + ",".join(validation["errors"]))
    spool_text = str(receipt["spool_dir"])
    raw_text = str(receipt["raw_object_candidate_path"])
    spool_input = Path(spool_text).expanduser()
    raw_input = Path(raw_text).expanduser()
    if spool_input.is_symlink() or raw_input.is_symlink():
        raise ValueError("session receipt paths may not be symlinks")
    spool = spool_input.resolve()
    source = raw_input.resolve()
    if not spool.is_dir() or not source.is_file():
        raise ValueError("session receipt raw transcript is unavailable")
    try:
        source.relative_to(spool)
    except ValueError as exc:
        raise ValueError("session receipt raw transcript escapes spool") from exc
    if sha256(source.read_bytes()).hexdigest() != receipt.get("raw_object_candidate_sha256"):
        raise ValueError("session receipt raw transcript digest mismatch")
    return source


@dataclass
class SessionRecorder:
    provider: str
    run_id: str
    session_id: str
    spool_dir: Path
    intent_sha256: str

    @classmethod
    def create(cls, provider: str, run_id: str, spool_root: Path, intent_bytes: bytes) -> "SessionRecorder":
        provider = provider.strip().lower()
        if provider not in {"codex", "claude"}:
            raise ValueError("unsupported provider")
        session_id = f"session:{provider}:{uuid.uuid4().hex}"
        spool_dir = Path(spool_root).expanduser().resolve() / session_id.replace(":", "-")
        spool_dir.mkdir(parents=True, exist_ok=False, mode=0o700)
        recorder = cls(provider, run_id, session_id, spool_dir, sha256(intent_bytes).hexdigest())
        recorder.emit("session_start", {"intent_sha256": recorder.intent_sha256})
        return recorder

    @property
    def events_path(self) -> Path:
        return self.spool_dir / "events.jsonl"

    def emit(self, event_type: str, payload: Mapping[str, Any] | None = None) -> dict[str, Any]:
        event = sanitize_event({
            "provider": self.provider,
            "run_id": self.run_id,
            "session_id": self.session_id,
            "event_type": str(event_type),
            "payload": dict(payload or {}),
        })
        line = canonical_bytes(event)
        with self.events_path.open("ab") as handle:
            handle.write(line)
            handle.flush()
            os.fsync(handle.fileno())
        self.events_path.chmod(0o600)
        return event

    def finalize(
        self,
        *,
        exit_code: int,
        raw_object_candidate: Path | None,
        capture_source: str,
        transcript_discovered: bool,
        provider_transcript: Mapping[str, Any] | None = None,
        transcript_binding: str | None = None,
        transcript_binding_matched: bool = False,
        transcript_candidate_count: int = 0,
        transcript_binding_match_count: int = 0,
        capture_failure: str | None = None,
        provider_command_contract_verified: bool = False,
        require_transcript: bool = True,
        test_only_process_output_fallback: bool = False,
        process_output_diagnostic: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        self.emit("session_end", {"exit_code": int(exit_code)})
        events = [json.loads(line) for line in self.events_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        counts = Counter(str(event.get("event_type") or "") for event in events)
        missing = [event_type for event_type in CRITICAL_EVENTS if counts[event_type] == 0]
        duplicate_ids = sorted(key for key, count in Counter(str(event.get("event_id") or "") for event in events).items() if key and count > 1)
        findings = _scan(events)
        raw_path = Path(raw_object_candidate).resolve() if raw_object_candidate else None
        raw_sha = sha256(raw_path.read_bytes()).hexdigest() if raw_path and raw_path.is_file() else None
        binding_sha = transcript_binding_sha256(transcript_binding) if transcript_binding else None
        blockers: list[str] = []
        if missing:
            blockers.append("CRITICAL_EVENT_MISSING")
        if duplicate_ids:
            blockers.append("DUPLICATE_EVENT_ID")
        if findings:
            blockers.append("STRUCTURED_PRIVACY_FINDING")
        if exit_code != 0:
            blockers.append("PROVIDER_EXIT_NONZERO")
        if process_output_diagnostic is not None:
            diagnostic_errors = _validate_process_output_diagnostic(process_output_diagnostic)
            if diagnostic_errors:
                blockers.append("PROCESS_OUTPUT_DIAGNOSTIC_INVALID")
            elif process_output_diagnostic.get("state") == "BLOCKED":
                blockers.append("PROCESS_OUTPUT_DIAGNOSTIC_BLOCKED")
        if test_only_process_output_fallback:
            blockers.append("TEST_ONLY_PROCESS_OUTPUT_FALLBACK")
        elif require_transcript and not transcript_discovered:
            blockers.append(capture_failure or ("PROVIDER_TRANSCRIPT_BINDING_NOT_FOUND" if transcript_candidate_count else "PROVIDER_TRANSCRIPT_NOT_DISCOVERED"))
        elif require_transcript and not transcript_binding_matched:
            blockers.append("PROVIDER_TRANSCRIPT_BINDING_UNMATCHED")
        non_test_blockers = [blocker for blocker in blockers if blocker != "TEST_ONLY_PROCESS_OUTPUT_FALLBACK"]
        state = "TEST_ONLY" if test_only_process_output_fallback and not non_test_blockers else "PASS" if not blockers else "BLOCKED"
        receipt = {
            "schema_version": 3,
            "provider": self.provider,
            "run_id": self.run_id,
            "session_id": self.session_id,
            "intent_sha256": self.intent_sha256,
            "event_count": len(events),
            "event_counts": dict(counts),
            "events_sha256": sha256(self.events_path.read_bytes()).hexdigest(),
            "critical_missing": missing,
            "duplicate_event_ids": duplicate_ids,
            "privacy_findings": findings,
            "exit_code": int(exit_code),
            "capture_source": capture_source,
            "capture_qualification": TEST_CAPTURE_QUALIFICATION if test_only_process_output_fallback else REAL_CAPTURE_QUALIFICATION if transcript_binding_matched else "UNQUALIFIED",
            "provider_command_contract_verified": bool(provider_command_contract_verified),
            "provider_transcript_discovered": bool(transcript_discovered),
            "provider_transcript_binding_matched": bool(transcript_binding_matched),
            "provider_transcript_candidate_count": int(transcript_candidate_count),
            "provider_transcript_binding_match_count": int(transcript_binding_match_count),
            "transcript_binding_sha256": binding_sha,
            "provider_transcript_sha256": (provider_transcript or {}).get("sha256"),
            "provider_transcript_source_path_sha256": (provider_transcript or {}).get("source_path_sha256"),
            "provider_transcript_bytes": (provider_transcript or {}).get("bytes"),
            "raw_object_candidate_sha256": raw_sha,
            "raw_object_candidate_path": str(raw_path) if raw_path else None,
            "spool_dir": str(self.spool_dir.resolve()),
            "raw_in_git_allowed": False,
            "test_only_process_output_fallback": bool(test_only_process_output_fallback),
            "process_output_diagnostic": dict(process_output_diagnostic) if process_output_diagnostic is not None else None,
            "capture_failure": capture_failure,
            "blockers": blockers,
            "state": state,
            "completed_at": utc_now(),
        }
        receipt["receipt_id"] = "session-receipt:" + digest(receipt)
        atomic_write(self.spool_dir / "session-receipt.json", canonical_bytes(receipt))
        return receipt


def run_provider(
    provider: str,
    command: Sequence[str],
    *,
    intent_path: Path,
    spool_root: Path,
    run_id: str,
    cwd: Path | None = None,
    session_roots: Sequence[Path] | None = None,
    transcript_binding: str | None = None,
    require_transcript: bool = True,
    test_only_process_output_fallback: bool = False,
    verified_provider_command: bool = False,
) -> dict[str, Any]:
    if not command:
        raise ValueError("provider command is required")
    if not test_only_process_output_fallback and not verified_provider_command:
        raise ValueError("real provider capture requires a verified default provider command")
    if not test_only_process_output_fallback:
        binding = validate_transcript_binding(str(transcript_binding or ""))
    else:
        binding = validate_transcript_binding(transcript_binding) if transcript_binding else None
    intent = Path(intent_path).read_bytes()
    recorder = SessionRecorder.create(provider, run_id, spool_root, intent)
    before = snapshot_session_files(provider, session_roots)
    recorder.emit("process_start", {"argv_digest": digest(list(command)), "executable": Path(command[0]).name})
    process_output = recorder.spool_dir / "process-output.bin"
    with process_output.open("wb") as raw:
        try:
            completed = subprocess.run(list(command), cwd=Path(cwd).resolve() if cwd else None, stdin=None, stdout=raw, stderr=subprocess.STDOUT, check=False)
            code = int(completed.returncode)
        except OSError as exc:
            raw.write(f"provider execution error: {exc.__class__.__name__}\\n".encode("utf-8"))
            code = 127
        raw.flush()
        os.fsync(raw.fileno())
    process_output.chmod(0o600)
    process_output_diagnostic = classify_provider_process_output(process_output, exit_code=code)
    after = snapshot_session_files(provider, session_roots)
    candidates = changed_transcript_candidates(before, after)
    matching = transcript_binding_candidates(before, after, transcript_binding=binding) if binding else []
    selected = changed_transcript(before, after, transcript_binding=binding) if binding else None
    transcript_info: dict[str, Any] | None = None
    capture_source = "process_output_fallback"
    raw_candidate = process_output
    if selected is not None:
        transcript_info = copy_transcript(selected.path, recorder.spool_dir, transcript_binding=binding)
        raw_candidate = Path(transcript_info["path"])
        capture_source = "provider_session_file"
        recorder.emit("provider_transcript_captured", {
            "provider_transcript_sha256": transcript_info["sha256"],
            "source_path_sha256": transcript_info["source_path_sha256"],
            "bytes": transcript_info["bytes"],
            "transcript_binding_sha256": transcript_info["transcript_binding_sha256"],
        })
    capture_failure = None
    if require_transcript and selected is None:
        if not candidates:
            capture_failure = "PROVIDER_TRANSCRIPT_NOT_DISCOVERED"
        elif not matching:
            capture_failure = "PROVIDER_TRANSCRIPT_BINDING_NOT_FOUND"
        else:
            capture_failure = "PROVIDER_TRANSCRIPT_BINDING_AMBIGUOUS"
    recorder.emit("process_end", {
        "exit_code": code,
        "process_output_sha256": sha256(process_output.read_bytes()).hexdigest(),
        "process_output_diagnostic": process_output_diagnostic,
        "transcript_discovered": selected is not None,
        "transcript_candidate_count": len(candidates),
        "transcript_binding_match_count": len(matching),
        "transcript_binding_matched": selected is not None,
    })
    return recorder.finalize(
        exit_code=code,
        raw_object_candidate=raw_candidate,
        capture_source=capture_source,
        transcript_discovered=selected is not None,
        provider_transcript=transcript_info,
        transcript_binding=binding,
        transcript_binding_matched=selected is not None,
        transcript_candidate_count=len(candidates),
        transcript_binding_match_count=len(matching),
        capture_failure=capture_failure,
        provider_command_contract_verified=verified_provider_command,
        require_transcript=require_transcript,
        test_only_process_output_fallback=test_only_process_output_fallback,
        process_output_diagnostic=process_output_diagnostic,
    )


def evaluate_capture(expected_event_types: Iterable[str], events: Iterable[Mapping[str, Any]], minimum_rate: float = 0.95) -> dict[str, Any]:
    expected = [str(value) for value in expected_event_types]
    materialized = list(events)
    counts = Counter(str(event.get("event_type") or "") for event in materialized)
    captured = sum(1 for event_type in expected if counts[event_type] > 0)
    rate = 1.0 if not expected else captured / len(expected)
    missing = [event_type for event_type in expected if counts[event_type] == 0]
    critical_missing = [event_type for event_type in CRITICAL_EVENTS if counts[event_type] == 0]
    state = "PASS" if rate >= minimum_rate and not critical_missing else "BLOCKED"
    return {
        "state": state,
        "capture_rate": round(rate, 6),
        "minimum_rate": minimum_rate,
        "missing": missing,
        "critical_missing": critical_missing,
    }
