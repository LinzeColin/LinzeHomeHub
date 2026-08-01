# STATUS_AGENT_V3_MANAGED: v0.0.0.3
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
from typing import Any, Mapping

from .canonical import canonical_json, digest, is_relative_to, require_sha256, require_text, utc_now

ALLOWED_VERDICTS = {"PASS", "FAIL", "BLOCKED"}
NON_PASS_STATES = {"UNKNOWN", "NOT_RUN", "WAIVED", "ABSTAIN", "STALE", "UNVERIFIED"}
MAX_ORACLE_TIMEOUT_SECONDS = 1800
MAX_ORACLE_OUTPUT_BYTES = 1048576
ORACLE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
GIT_COMMIT_RE = re.compile(r"^[0-9a-fA-F]{40,64}$")
FROZEN_CONTRACT_SCHEMA = "status.frozen_gate_contract.v3"
TRUST_POLICY_SCHEMA = "status.gate_trust_policy.v1"
GATE_TRUST_MODE = "ASYMMETRIC_SSHSIG_GATE_WITH_EXACT_CANDIDATE_AND_TASKPACK_IDENTITY"
GATE_SIGNATURE_FORMAT = "sshsig"
GATE_SIGNATURE_NAMESPACE = "status-agent-v3-gate"
MAX_SIGNATURE_BYTES = 65536
MAX_SIGNATURE_TIMEOUT_SECONDS = 30
SIGNER_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:@-]{0,127}$")
TRUST_ROOT_ENTRIES = frozenset({"policy.json", "allowed_signers", "contracts", "evidence"})


class GateError(RuntimeError):
    pass


@dataclass(frozen=True)
class GateTrust:
    root: Path
    policy_path: Path
    allowed_signers_path: Path
    signer_identity: str
    namespace: str
    gate_principal_uid: int
    allowed_signers_sha256: str


@dataclass(frozen=True)
class OracleResult:
    oracle_id: str
    state: str
    argv: tuple[str, ...]
    returncode: int | None
    stdout_sha256: str | None
    stderr_sha256: str | None
    evidence_path: str | None
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "oracle_id": self.oracle_id,
            "state": self.state,
            "argv": list(self.argv),
            "returncode": self.returncode,
            "stdout_sha256": self.stdout_sha256,
            "stderr_sha256": self.stderr_sha256,
            "evidence_path": self.evidence_path,
            "reason": self.reason,
        }


def _path_owner_uid(path: Path) -> int:
    return Path(path).stat().st_uid


def _effective_uid() -> int | None:
    getter = getattr(os, "geteuid", None)
    return int(getter()) if callable(getter) else None


def _require_not_writable_by_group_or_world(path: Path, label: str) -> None:
    if os.name != "nt" and Path(path).stat().st_mode & 0o022:
        raise GateError(f"{label} must not be writable by group or world")


def _regular_file(path: Path, label: str) -> Path:
    raw = Path(path).expanduser()
    if raw.is_symlink():
        raise GateError(f"{label} must not be symlink")
    try:
        resolved = raw.resolve(strict=True)
    except OSError as exc:
        raise GateError(f"{label} missing") from exc
    if not resolved.is_file() or resolved.is_symlink():
        raise GateError(f"{label} must be regular file")
    return resolved


def _require_descendant(path: Path, root: Path, label: str) -> None:
    if not is_relative_to(path, root):
        raise GateError(f"{label} must be inside protected trust root")


def _read_trust_policy(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise GateError("gate trust policy unreadable") from exc
    if not isinstance(value, dict):
        raise GateError("gate trust policy root must be object")
    return value


def _allowed_signer_identities(path: Path) -> set[str]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        raise GateError("allowed signers unreadable") from exc
    identities = {
        line.split(maxsplit=1)[0]
        for line in lines
        if line.strip() and not line.lstrip().startswith("#") and len(line.split(maxsplit=1)) == 2
    }
    if not identities:
        raise GateError("allowed signers has no principals")
    return identities


def load_gate_trust(
    trust_root: Path,
    *,
    candidate_root: Path | None = None,
    package_root: Path | None = None,
    require_gate_principal: bool = False,
) -> GateTrust:
    raw_root = Path(trust_root).expanduser()
    if raw_root.is_symlink():
        raise GateError("gate trust root must not be symlink")
    try:
        root = raw_root.resolve(strict=True)
    except OSError as exc:
        raise GateError("gate trust root missing") from exc
    if not root.is_dir():
        raise GateError("gate trust root must be directory")
    if candidate_root is not None and is_relative_to(root, Path(candidate_root).resolve()):
        raise GateError("gate trust root must be outside candidate workspace")
    if package_root is not None and is_relative_to(root, Path(package_root).resolve()):
        raise GateError("gate trust root must be outside taskpack workspace")
    _require_not_writable_by_group_or_world(root, "gate trust root")
    names = {item.name for item in root.iterdir()}
    unexpected = names - TRUST_ROOT_ENTRIES
    missing = TRUST_ROOT_ENTRIES - names
    if unexpected:
        raise GateError("gate trust root has unexpected entries: " + ",".join(sorted(unexpected)))
    if missing:
        raise GateError("gate trust root missing entries: " + ",".join(sorted(missing)))

    policy_path = _regular_file(root / "policy.json", "gate trust policy")
    allowed_signers_path = _regular_file(root / "allowed_signers", "allowed signers")
    _require_not_writable_by_group_or_world(policy_path, "gate trust policy")
    _require_not_writable_by_group_or_world(allowed_signers_path, "allowed signers")
    for name in ("contracts", "evidence"):
        child = root / name
        if child.is_symlink() or not child.is_dir():
            raise GateError(f"gate trust {name} must be directory")
        _require_not_writable_by_group_or_world(child, f"gate trust {name}")

    policy = _read_trust_policy(policy_path)
    if policy.get("schema_version") != TRUST_POLICY_SCHEMA:
        raise GateError("unsupported gate trust policy")
    if policy.get("signature_format") != GATE_SIGNATURE_FORMAT:
        raise GateError("unsupported gate signature format")
    namespace = str(policy.get("namespace") or "")
    if namespace != GATE_SIGNATURE_NAMESPACE:
        raise GateError("unexpected gate signature namespace")
    signer_identity = str(policy.get("signer_identity") or "")
    if not SIGNER_ID_RE.fullmatch(signer_identity):
        raise GateError("invalid gate signer identity")
    gate_principal_uid = policy.get("gate_principal_uid")
    if isinstance(gate_principal_uid, bool) or not isinstance(gate_principal_uid, int) or gate_principal_uid < 0:
        raise GateError("invalid gate principal uid")
    allowed_digest = str(policy.get("allowed_signers_sha256") or "")
    try:
        require_sha256(allowed_digest, "allowed_signers_sha256")
    except ValueError as exc:
        raise GateError("invalid allowed signers digest") from exc
    if sha256(allowed_signers_path.read_bytes()).hexdigest() != allowed_digest:
        raise GateError("allowed signers digest mismatch")
    if signer_identity not in _allowed_signer_identities(allowed_signers_path):
        raise GateError("configured signer identity absent from allowed signers")

    if require_gate_principal:
        effective_uid = _effective_uid()
        if effective_uid is None:
            raise GateError("gate principal identity unavailable")
        if effective_uid != gate_principal_uid:
            raise GateError("gate must run as configured gate principal")
        if _path_owner_uid(root) != effective_uid:
            raise GateError("gate trust root must be owned by gate principal")
        if candidate_root is None:
            raise GateError("candidate root required for gate principal check")
        if _path_owner_uid(Path(candidate_root).resolve()) == effective_uid:
            raise GateError("candidate and gate principal must be different OS users")

    return GateTrust(
        root=root,
        policy_path=policy_path,
        allowed_signers_path=allowed_signers_path,
        signer_identity=signer_identity,
        namespace=namespace,
        gate_principal_uid=gate_principal_uid,
        allowed_signers_sha256=allowed_digest,
    )


def gate_trust_preflight(*, candidate_root: Path, package_root: Path, trust_root: Path, require_gate_principal: bool = False) -> dict[str, Any]:
    try:
        trust = load_gate_trust(
            trust_root,
            candidate_root=Path(candidate_root),
            package_root=Path(package_root),
            require_gate_principal=require_gate_principal,
        )
        ssh_keygen = shutil.which("ssh-keygen")
        if not ssh_keygen:
            raise GateError("ssh-keygen unavailable")
        return {
            "state": "PASS",
            "trust_root": str(trust.root),
            "signer_identity": trust.signer_identity,
            "signature_format": GATE_SIGNATURE_FORMAT,
            "namespace": trust.namespace,
            "gate_principal_uid": trust.gate_principal_uid,
            "candidate_principal_uid": _path_owner_uid(Path(candidate_root).resolve()),
            "role_separation_confirmed": trust.gate_principal_uid != _path_owner_uid(Path(candidate_root).resolve()),
            "ssh_keygen": ssh_keygen,
            "private_key_checked": False,
        }
    except GateError as exc:
        return {
            "state": "BLOCKED",
            "reason": str(exc),
            "role_separation_confirmed": False,
            "private_key_checked": False,
        }


def _validate_signing_key(*, signing_key_path: Path, candidate_root: Path, package_root: Path, trust: GateTrust) -> Path:
    raw = Path(signing_key_path).expanduser()
    if raw.is_symlink():
        raise GateError("gate signing key must not be symlink")
    key = _ensure_protected(candidate_root, package_root, raw, "gate signing key")
    if is_relative_to(key, trust.root):
        raise GateError("gate signing key must not live in public trust root")
    effective_uid = _effective_uid()
    if effective_uid is None or _path_owner_uid(key) != effective_uid:
        raise GateError("gate signing key must be owned by executing gate principal")
    if os.name != "nt" and key.stat().st_mode & 0o077:
        raise GateError("gate signing key permissions must be 0600")
    return key


def _ssh_keygen_binary() -> str:
    binary = shutil.which("ssh-keygen")
    if not binary:
        raise GateError("ssh-keygen unavailable")
    return binary


def _sign_payload(*, payload: bytes, signing_key_path: Path, trust: GateTrust) -> str:
    with tempfile.TemporaryDirectory(prefix="status-gate-sign-") as directory:
        work = Path(directory)
        if os.name != "nt":
            work.chmod(0o700)
        payload_path = work / "verdict.json"
        payload_path.write_bytes(payload)
        try:
            completed = subprocess.run(
                [_ssh_keygen_binary(), "-Y", "sign", "-f", str(signing_key_path), "-n", trust.namespace, str(payload_path)],
                capture_output=True,
                text=False,
                timeout=MAX_SIGNATURE_TIMEOUT_SECONDS,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise GateError("sshsig signing unavailable") from exc
        if completed.returncode != 0:
            raise GateError("sshsig signing failed")
        signature_path = Path(str(payload_path) + ".sig")
        try:
            signature = signature_path.read_bytes()
        except OSError as exc:
            raise GateError("sshsig signing produced no signature") from exc
        if not signature or len(signature) > MAX_SIGNATURE_BYTES or not signature.startswith(b"-----BEGIN SSH SIGNATURE-----"):
            raise GateError("invalid sshsig output")
        try:
            return signature.decode("ascii")
        except UnicodeDecodeError as exc:
            raise GateError("sshsig output is not ascii") from exc


def _verify_payload(*, payload: bytes, armored_signature: str, trust: GateTrust) -> tuple[bool, str]:
    try:
        signature = armored_signature.encode("ascii")
    except UnicodeEncodeError:
        return False, "SIGNATURE_NOT_ASCII"
    if not signature or len(signature) > MAX_SIGNATURE_BYTES or not signature.startswith(b"-----BEGIN SSH SIGNATURE-----"):
        return False, "INVALID_SIGNATURE_ENCODING"
    with tempfile.TemporaryDirectory(prefix="status-gate-verify-") as directory:
        work = Path(directory)
        if os.name != "nt":
            work.chmod(0o700)
        signature_path = work / "verdict.sig"
        signature_path.write_bytes(signature)
        try:
            completed = subprocess.run(
                [
                    _ssh_keygen_binary(), "-Y", "verify",
                    "-f", str(trust.allowed_signers_path),
                    "-I", trust.signer_identity,
                    "-n", trust.namespace,
                    "-s", str(signature_path),
                ],
                input=payload,
                capture_output=True,
                text=False,
                timeout=MAX_SIGNATURE_TIMEOUT_SECONDS,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return False, "SIGNATURE_VERIFY_TIMEOUT"
        except OSError:
            return False, "SIGNATURE_VERIFY_UNAVAILABLE"
    return (completed.returncode == 0, "VALID" if completed.returncode == 0 else "SIGNATURE_REJECTED")




def legacy_observation_gate(*_args: Any, test_only: bool = False, **_kwargs: Any) -> dict[str, Any]:
    """Legacy caller-supplied observations never authorize production PASS."""
    state = "PASS_TEST_ONLY" if test_only else "BLOCKED"
    return {
        "schema_version": 3,
        "verdict": state,
        "reason": "LEGACY_CALLER_OBSERVATIONS_HAVE_NO_RELEASE_AUTHORITY",
        "trusted_gate_required": True,
        "verified_at": utc_now(),
    }


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise GateError("JSON root must be object")
    return value


def _ensure_protected(candidate_root: Path, package_root: Path, path: Path, label: str) -> Path:
    resolved = Path(path).expanduser().resolve()
    if is_relative_to(resolved, candidate_root):
        raise GateError(f"{label} must be outside candidate workspace")
    if is_relative_to(resolved, package_root):
        raise GateError(f"{label} must be outside taskpack workspace")
    if not resolved.is_file():
        raise GateError(f"{label} missing: {resolved}")
    return resolved


def _expand_argv(argv: list[Any], candidate_root: Path, package_root: Path, evidence_dir: Path) -> list[str]:
    mapping = {
        "{PYTHON}": sys.executable,
        "{CANDIDATE}": str(candidate_root),
        "{PACKAGE}": str(package_root),
        "{EVIDENCE}": str(evidence_dir),
    }
    result: list[str] = []
    for raw in argv:
        token = str(raw)
        for key, value in mapping.items():
            token = token.replace(key, value)
        if "\x00" in token or "\n" in token:
            raise GateError("invalid argv token")
        result.append(token)
    if not result:
        raise GateError("oracle argv required")
    return result


def _validate_oracle_id(value: str) -> str:
    if not ORACLE_ID_RE.fullmatch(value) or ".." in value or "/" in value or "\\" in value:
        raise GateError(f"invalid oracle_id: {value!r}")
    return value


def _evidence_filename(oracle_id: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", oracle_id)
    suffix = sha256(oracle_id.encode("utf-8")).hexdigest()[:12]
    return f"{safe}-{suffix}.json"


def _write_evidence(evidence_dir: Path, oracle_id: str, raw: Mapping[str, Any]) -> Path:
    path = evidence_dir / _evidence_filename(oracle_id)
    path.write_text(json.dumps(dict(raw), ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def _run_git(repo: Path, *args: str, binary: bool = False) -> bytes | str:
    completed = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=not binary,
        timeout=120,
        check=False,
    )
    if completed.returncode != 0:
        stderr = completed.stderr.decode("utf-8", errors="replace") if binary else str(completed.stderr or "")
        stdout = completed.stdout.decode("utf-8", errors="replace") if binary else str(completed.stdout or "")
        raise GateError((stderr or stdout or "git command failed").strip())
    return completed.stdout


def _tracked_tree_sha256(repo: Path) -> str:
    raw = _run_git(repo, "ls-files", "-z", binary=True)
    assert isinstance(raw, bytes)
    digestor = sha256()
    for item in raw.split(b"\x00"):
        if not item:
            continue
        relative = item.decode("utf-8", errors="strict")
        path = (repo / relative).resolve()
        if not is_relative_to(path, repo) or not path.is_file() or path.is_symlink():
            raise GateError(f"tracked path is not a regular file: {relative}")
        digestor.update(relative.encode("utf-8") + b"\x00" + path.read_bytes() + b"\x00")
    return digestor.hexdigest()


def _package_tree_sha256(root: Path) -> str:
    digestor = sha256()
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise GateError(f"taskpack contains symlink: {path.relative_to(root).as_posix()}")
        if not path.is_file():
            continue
        relative = path.relative_to(root).as_posix()
        if "__pycache__" in path.parts or path.suffix in {".pyc", ".pyo"}:
            raise GateError(f"taskpack contains generated cache: {relative}")
        digestor.update(relative.encode("utf-8") + b"\x00" + path.read_bytes() + b"\x00")
    return digestor.hexdigest()


def _taskpack_identity_result(*, package_root: Path, contract: Mapping[str, Any], evidence_dir: Path, phase: str) -> OracleResult:
    oid = f"OR-TASKPACK-IDENTITY-{phase}"
    expected = str(contract.get("taskpack_tree_sha256") or "")
    require_sha256(expected, "taskpack_tree_sha256")
    try:
        actual = _package_tree_sha256(package_root)
    except (GateError, OSError, UnicodeError) as exc:
        raw = {"oracle_id": oid, "phase": phase, "expected_tree_sha256": expected, "error": exc.__class__.__name__}
        evidence_path = _write_evidence(evidence_dir, oid, raw)
        return OracleResult(oid, "BLOCKED", ("internal:taskpack-tree-identity", phase), None, None, None, str(evidence_path), "TASKPACK_IDENTITY_UNAVAILABLE")
    failures = [] if actual == expected else ["TASKPACK_TREE_MISMATCH"]
    raw = {"oracle_id": oid, "phase": phase, "expected_tree_sha256": expected, "actual_tree_sha256": actual, "failures": failures}
    evidence_path = _write_evidence(evidence_dir, oid, raw)
    state = "PASS" if not failures else "FAIL"
    return OracleResult(
        oid, state, ("internal:taskpack-tree-identity", phase), 0 if state == "PASS" else 2,
        actual, sha256(b"").hexdigest(), str(evidence_path),
        "EXACT_TASKPACK_IDENTITY_CONFIRMED" if state == "PASS" else "TASKPACK_IDENTITY_DRIFT:TASKPACK_TREE_MISMATCH",
    )


def _candidate_identity_result(
    *,
    candidate_root: Path,
    contract: Mapping[str, Any],
    evidence_dir: Path,
    phase: str,
) -> OracleResult:
    oid = f"OR-CANDIDATE-IDENTITY-{phase}"
    expected_commit = str(contract.get("candidate_commit") or "")
    expected_tree = str(contract.get("candidate_tree_sha256") or "")
    if not GIT_COMMIT_RE.fullmatch(expected_commit):
        raise GateError("invalid or missing candidate_commit in frozen contract")
    require_sha256(expected_tree, "candidate_tree_sha256")
    try:
        actual_commit_raw = _run_git(candidate_root, "rev-parse", "HEAD")
        actual_commit = str(actual_commit_raw).strip()
        actual_tree = _tracked_tree_sha256(candidate_root)
        status_args = ("status", "--porcelain=v1", "--untracked-files=all" if phase == "PRE" else "--untracked-files=no")
        porcelain_raw = _run_git(candidate_root, *status_args)
        porcelain = str(porcelain_raw).strip()
    except (GateError, OSError, UnicodeError) as exc:
        raw = {
            "oracle_id": oid,
            "phase": phase,
            "expected_commit": expected_commit,
            "expected_tree_sha256": expected_tree,
            "error": exc.__class__.__name__,
        }
        evidence_path = _write_evidence(evidence_dir, oid, raw)
        return OracleResult(
            oid, "BLOCKED", ("internal:git-candidate-identity", phase), None,
            None, None, str(evidence_path), "CANDIDATE_IDENTITY_UNAVAILABLE",
        )

    failures: list[str] = []
    if actual_commit != expected_commit:
        failures.append("CANDIDATE_COMMIT_MISMATCH")
    if actual_tree != expected_tree:
        failures.append("CANDIDATE_TREE_MISMATCH")
    if porcelain:
        failures.append("WORKTREE_NOT_CLEAN" if phase == "PRE" else "TRACKED_WORKTREE_CHANGED_DURING_GATE")
    raw = {
        "oracle_id": oid,
        "phase": phase,
        "expected_commit": expected_commit,
        "actual_commit": actual_commit,
        "expected_tree_sha256": expected_tree,
        "actual_tree_sha256": actual_tree,
        "worktree_clean_for_phase": not bool(porcelain),
        "status_sha256": sha256(porcelain.encode("utf-8")).hexdigest(),
        "failures": failures,
    }
    evidence_path = _write_evidence(evidence_dir, oid, raw)
    state = "PASS" if not failures else "FAIL"
    reason = "EXACT_CANDIDATE_IDENTITY_CONFIRMED" if state == "PASS" else "CANDIDATE_IDENTITY_DRIFT:" + ",".join(failures)
    return OracleResult(
        oid, state, ("internal:git-candidate-identity", phase), 0 if state == "PASS" else 2,
        raw["status_sha256"], sha256(b"").hexdigest(), str(evidence_path), reason,
    )


def run_frozen_gate(
    *,
    contract_path: Path,
    candidate_root: Path,
    package_root: Path,
    evidence_dir: Path,
    trust_root: Path,
    signing_key_path: Path,
) -> dict[str, Any]:
    candidate_root = Path(candidate_root).resolve()
    package_root = Path(package_root).resolve()
    if not candidate_root.is_dir():
        raise GateError("candidate workspace missing")
    if not package_root.is_dir():
        raise GateError("taskpack root missing")
    trust = load_gate_trust(
        trust_root,
        candidate_root=candidate_root,
        package_root=package_root,
        require_gate_principal=True,
    )
    contract_path = _ensure_protected(candidate_root, package_root, Path(contract_path), "gate contract")
    _require_descendant(contract_path, trust.root / "contracts", "gate contract")
    signing_key_path = _validate_signing_key(
        signing_key_path=Path(signing_key_path),
        candidate_root=candidate_root,
        package_root=package_root,
        trust=trust,
    )

    contract_bytes = contract_path.read_bytes()
    parsed = json.loads(contract_bytes.decode("utf-8"))
    if not isinstance(parsed, dict):
        raise GateError("JSON root must be object")
    contract = parsed
    for field in ("run_id", "subject_id", "subject_sha256", "acceptance_sha256", "oracle_registry_sha256", "verifier_version", "oracles"):
        if contract.get(field) in (None, "", []):
            raise GateError(f"missing contract field: {field}")
    for field in ("subject_sha256", "acceptance_sha256", "oracle_registry_sha256"):
        require_sha256(contract[field], field)
    if not isinstance(contract.get("oracles"), list) or any(not isinstance(item, dict) for item in contract["oracles"]):
        raise GateError("oracles must be an array of objects")

    frozen_contract = contract.get("schema_version") == FROZEN_CONTRACT_SCHEMA
    if not frozen_contract:
        raise GateError("production gate requires frozen contract")
    if contract.get("worker_may_write_contract") is not False or contract.get("worker_may_write_verdict") is not False:
        raise GateError("frozen contract must deny worker writes to contract and verdict")
    expected_commit = str(contract.get("candidate_commit") or "")
    expected_tree = str(contract.get("candidate_tree_sha256") or "")
    if not GIT_COMMIT_RE.fullmatch(expected_commit):
        raise GateError("invalid or missing candidate_commit in frozen contract")
    require_sha256(expected_tree, "candidate_tree_sha256")
    require_sha256(str(contract.get("taskpack_tree_sha256") or ""), "taskpack_tree_sha256")

    evidence_dir = Path(evidence_dir).expanduser().resolve()
    if is_relative_to(evidence_dir, candidate_root) or is_relative_to(evidence_dir, package_root):
        raise GateError("evidence directory must be outside candidate and taskpack workspaces")
    _require_descendant(evidence_dir, trust.root / "evidence", "gate evidence directory")
    evidence_dir.mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        evidence_dir.chmod(0o700)
    if _path_owner_uid(evidence_dir) != _effective_uid():
        raise GateError("gate evidence directory must be owned by gate principal")
    _require_not_writable_by_group_or_world(evidence_dir, "gate evidence directory")

    results: list[OracleResult] = []
    identity_pre_pass = True
    if frozen_contract:
        taskpack_pre = _taskpack_identity_result(package_root=package_root, contract=contract, evidence_dir=evidence_dir, phase="PRE")
        results.append(taskpack_pre)
        identity_pre = _candidate_identity_result(
            candidate_root=candidate_root,
            contract=contract,
            evidence_dir=evidence_dir,
            phase="PRE",
        )
        results.append(identity_pre)
        identity_pre_pass = taskpack_pre.state == "PASS" and identity_pre.state == "PASS"

    seen_oracle_ids: set[str] = set()
    if identity_pre_pass:
        for oracle in contract["oracles"]:
            oid = _validate_oracle_id(require_text(oracle, "oracle_id"))
            if oid in seen_oracle_ids:
                raise GateError(f"duplicate oracle_id: {oid}")
            seen_oracle_ids.add(oid)
            argv = _expand_argv(list(oracle.get("argv") or []), candidate_root, package_root, evidence_dir)
            allowed = [str(item) for item in oracle.get("allowed_executables") or []]
            if not allowed:
                raise GateError(f"allowed_executables required for oracle: {oid}")
            executable = Path(argv[0]).name
            if executable not in allowed and argv[0] not in allowed:
                results.append(OracleResult(oid, "BLOCKED", tuple(argv), None, None, None, None, "EXECUTABLE_NOT_ALLOWLISTED"))
                continue
            cwd_rel = str(oracle.get("cwd_rel") or ".")
            cwd = (candidate_root / cwd_rel).resolve()
            if not is_relative_to(cwd, candidate_root) or not cwd.is_dir():
                results.append(OracleResult(oid, "BLOCKED", tuple(argv), None, None, None, None, "INVALID_CWD"))
                continue
            try:
                requested_timeout = int(oracle.get("timeout_seconds") or 120)
                requested_output = int(oracle.get("max_output_bytes") or 262144)
            except (TypeError, ValueError):
                results.append(OracleResult(oid, "BLOCKED", tuple(argv), None, None, None, None, "INVALID_RESOURCE_LIMIT"))
                continue
            if requested_timeout < 1 or requested_timeout > MAX_ORACLE_TIMEOUT_SECONDS or requested_output < 1024 or requested_output > MAX_ORACLE_OUTPUT_BYTES:
                results.append(OracleResult(oid, "BLOCKED", tuple(argv), None, None, None, None, "RESOURCE_LIMIT_OUTSIDE_GATE_POLICY"))
                continue
            timeout = requested_timeout
            max_output = requested_output
            env = {
                "PATH": os.environ.get("PATH", ""),
                "LANG": "C.UTF-8",
                "LC_ALL": "C.UTF-8",
                "PYTHONDONTWRITEBYTECODE": "1",
            }
            for env_key in oracle.get("environment_allowlist") or []:
                if env_key in os.environ:
                    env[str(env_key)] = os.environ[str(env_key)]
            try:
                completed = subprocess.run(
                    argv,
                    cwd=cwd,
                    env=env,
                    text=False,
                    capture_output=True,
                    timeout=timeout,
                    check=False,
                )
                stdout_full = bytes(completed.stdout or b"")
                stderr_full = bytes(completed.stderr or b"")
                stdout = stdout_full[:max_output]
                stderr = stderr_full[:max_output]
                raw = {
                    "oracle_id": oid,
                    "argv": argv,
                    "cwd": str(cwd),
                    "returncode": completed.returncode,
                    "requested_timeout_seconds": requested_timeout,
                    "effective_timeout_seconds": timeout,
                    "stdout_sha256": sha256(stdout_full).hexdigest(),
                    "stderr_sha256": sha256(stderr_full).hexdigest(),
                    "stdout_captured_prefix_sha256": sha256(stdout).hexdigest(),
                    "stderr_captured_prefix_sha256": sha256(stderr).hexdigest(),
                    "stdout_bytes": len(stdout_full),
                    "stderr_bytes": len(stderr_full),
                    "stdout_truncated": len(stdout_full) > max_output,
                    "stderr_truncated": len(stderr_full) > max_output,
                }
                evidence_path = _write_evidence(evidence_dir, oid, raw)
                expected = int(oracle.get("expected_exit_code", 0))
                state = "PASS" if completed.returncode == expected else "FAIL"
                reason = "EXPECTED_EXIT" if state == "PASS" else "UNEXPECTED_EXIT"
                results.append(OracleResult(
                    oid, state, tuple(argv), completed.returncode,
                    raw["stdout_sha256"], raw["stderr_sha256"], str(evidence_path), reason,
                ))
            except subprocess.TimeoutExpired:
                results.append(OracleResult(oid, "BLOCKED", tuple(argv), None, None, None, None, "TIMEOUT"))
            except OSError as exc:
                results.append(OracleResult(oid, "BLOCKED", tuple(argv), None, None, None, None, f"EXECUTION_ERROR:{exc.__class__.__name__}"))

    if frozen_contract and identity_pre_pass:
        results.append(_candidate_identity_result(
            candidate_root=candidate_root,
            contract=contract,
            evidence_dir=evidence_dir,
            phase="POST",
        ))
        results.append(_taskpack_identity_result(package_root=package_root, contract=contract, evidence_dir=evidence_dir, phase="POST"))

    states = [item.state for item in results]
    if any(state == "FAIL" for state in states):
        verdict, reason = "FAIL", "ORACLE_OR_SUBJECT_FAILURE"
    elif not results or any(state != "PASS" for state in states):
        verdict, reason = "BLOCKED", "ORACLE_OR_SUBJECT_INCOMPLETE"
    else:
        verdict, reason = "PASS", "EXACT_SUBJECT_AND_ALL_FROZEN_ORACLES_PASSED"
    body = {
        "schema_version": 3,
        "run_id": str(contract["run_id"]),
        "subject_id": str(contract["subject_id"]),
        "subject_sha256": str(contract["subject_sha256"]),
        "acceptance_sha256": str(contract["acceptance_sha256"]),
        "oracle_registry_sha256": str(contract["oracle_registry_sha256"]),
        "verifier_version": str(contract["verifier_version"]),
        "candidate_commit": contract.get("candidate_commit"),
        "candidate_tree_sha256": contract.get("candidate_tree_sha256"),
        "taskpack_tree_sha256": contract.get("taskpack_tree_sha256"),
        "gate_contract_sha256": sha256(contract_bytes).hexdigest(),
        "verdict": verdict,
        "reason": reason,
        "oracle_results": [item.to_dict() for item in results],
        "verified_at": utc_now(),
        "trust_mode": GATE_TRUST_MODE,
        "taskpack_identity_enforced": True,
        "signature_format": GATE_SIGNATURE_FORMAT,
        "signature_namespace": trust.namespace,
        "signer_identity": trust.signer_identity,
        "allowed_signers_sha256": trust.allowed_signers_sha256,
    }
    body["verdict_id"] = "gate:" + digest(body)
    signature = _sign_payload(
        payload=canonical_json(body).encode("utf-8"),
        signing_key_path=signing_key_path,
        trust=trust,
    )
    body["signature"] = {
        "format": GATE_SIGNATURE_FORMAT,
        "namespace": trust.namespace,
        "signer_identity": trust.signer_identity,
        "armored": signature,
    }
    verification = verify_verdict(body, trust.root)
    if verification.get("state") != "PASS":
        raise GateError("signed gate verdict did not verify against public trust policy")
    return body


def verify_verdict(verdict: Mapping[str, Any], trust_root: Path) -> dict[str, Any]:
    try:
        trust = load_gate_trust(Path(trust_root))
    except GateError as exc:
        return {"state": "FAIL", "reason": f"TRUST_POLICY_INVALID:{exc}"}
    if str(verdict.get("verdict")) not in ALLOWED_VERDICTS:
        return {"state": "FAIL", "reason": "INVALID_VERDICT"}
    if verdict.get("trust_mode") != GATE_TRUST_MODE or verdict.get("taskpack_identity_enforced") is not True:
        return {"state": "FAIL", "reason": "UNTRUSTED_GATE_MODE"}
    body = dict(verdict)
    signature = body.pop("signature", None)
    if not isinstance(signature, dict):
        return {"state": "FAIL", "reason": "SIGNATURE_MISSING_OR_INVALID"}
    if signature.get("format") != GATE_SIGNATURE_FORMAT:
        return {"state": "FAIL", "reason": "SIGNATURE_FORMAT_MISMATCH"}
    if signature.get("namespace") != trust.namespace or signature.get("signer_identity") != trust.signer_identity:
        return {"state": "FAIL", "reason": "SIGNATURE_POLICY_MISMATCH"}
    armored = signature.get("armored")
    if not isinstance(armored, str):
        return {"state": "FAIL", "reason": "SIGNATURE_MISSING_OR_INVALID"}
    if body.get("signature_format") != GATE_SIGNATURE_FORMAT or body.get("signature_namespace") != trust.namespace:
        return {"state": "FAIL", "reason": "VERDICT_SIGNATURE_METADATA_MISMATCH"}
    if body.get("signer_identity") != trust.signer_identity or body.get("allowed_signers_sha256") != trust.allowed_signers_sha256:
        return {"state": "FAIL", "reason": "VERDICT_TRUST_METADATA_MISMATCH"}
    expected_id = "gate:" + digest({key: value for key, value in body.items() if key != "verdict_id"})
    id_valid = body.get("verdict_id") == expected_id
    try:
        signature_valid, signature_reason = _verify_payload(
            payload=canonical_json(body).encode("utf-8"),
            armored_signature=armored,
            trust=trust,
        )
    except GateError as exc:
        return {"state": "FAIL", "reason": f"SIGNATURE_VERIFICATION_UNAVAILABLE:{exc}", "verdict_id_valid": id_valid}
    return {
        "state": "PASS" if signature_valid and id_valid else "FAIL",
        "signature_valid": signature_valid,
        "verdict_id_valid": id_valid,
        "reason": signature_reason if signature_valid else signature_reason,
    }
