"""Deterministic secret, PII and local-path redaction before persistence."""

from __future__ import annotations

from dataclasses import dataclass
import json
import re
from typing import Any, Mapping


@dataclass(frozen=True)
class Finding:
    kind: str
    path: str
    severity: str

    def to_dict(self) -> dict[str, str]:
        return {"kind": self.kind, "path": self.path, "severity": self.severity}


_SENSITIVE_KEYS = re.compile(
    r"^(authorization|cookie|credential|password|private[_-]?key|secret|token|api[_-]?key)$",
    re.I,
)
_VALUE_PATTERNS: tuple[tuple[str, re.Pattern[str], str], ...] = (
    ("private_key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"), "critical"),
    ("github_token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"), "critical"),
    ("anthropic_key", re.compile(r"\bsk-ant-[A-Za-z0-9_-]{16,}\b"), "critical"),
    ("openai_key", re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"), "critical"),
    ("aws_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "critical"),
    ("bearer_token", re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/-]{16,}=*"), "critical"),
    ("inline_secret", re.compile(r"(?i)\b(password|passwd|secret|token|api[_-]?key)\s*[:=]\s*[^\s,;]{6,}"), "critical"),
    ("email", re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"), "medium"),
    ("mac_home", re.compile(r"/Users/[^/\s]+"), "medium"),
    ("linux_home", re.compile(r"/home/[^/\s]+"), "medium"),
    ("windows_home", re.compile(r"(?i)[A-Z]:\\Users\\[^\\\s]+"), "medium"),
)


def _redact_string(value: str, path: str, findings: list[Finding]) -> str:
    output = value
    for kind, pattern, severity in _VALUE_PATTERNS:
        if pattern.search(output):
            findings.append(Finding(kind, path, severity))
            replacement = "$HOME" if kind.endswith("_home") else f"[REDACTED:{kind}]"
            output = pattern.sub(replacement, output)
    return output


def redact(value: Any, *, path: str = "$") -> tuple[Any, list[Finding]]:
    findings: list[Finding] = []

    def visit(node: Any, current: str) -> Any:
        if isinstance(node, Mapping):
            result: dict[str, Any] = {}
            for key, child in node.items():
                key_text = str(key)
                child_path = f"{current}.{key_text}"
                if _SENSITIVE_KEYS.search(key_text):
                    findings.append(Finding("sensitive_key", child_path, "critical"))
                    result[key_text] = "[REDACTED:sensitive_key]"
                else:
                    result[key_text] = visit(child, child_path)
            return result
        if isinstance(node, (list, tuple)):
            return [visit(child, f"{current}[{index}]") for index, child in enumerate(node)]
        if isinstance(node, str):
            return _redact_string(node, current, findings)
        return node

    return visit(value, path), findings


def scan(value: Any) -> list[Finding]:
    _, findings = redact(value)
    return findings


def assert_no_critical_secret(value: Any) -> None:
    serialized = json.dumps(value, ensure_ascii=False, sort_keys=True)
    findings = scan(serialized)
    critical = [finding for finding in findings if finding.severity == "critical"]
    if critical:
        kinds = ",".join(sorted({finding.kind for finding in critical}))
        raise ValueError(f"critical secret pattern remains after redaction: {kinds}")
