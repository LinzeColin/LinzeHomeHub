# STATUS_AGENT_V3_MANAGED: v0.0.0.3
from __future__ import annotations

from pathlib import Path
import re
from typing import Any, Iterable

BILLING_PROBE_RELATIVE_PATH = "status/collector/probe_ai_balance.py"
ALLOWED_OPENAI_COSTS_ENDPOINT = "https://api.openai.com/v1/organization/costs"
ALLOWED_OPENAI_HOST_CONTEXTS = ("`api.openai.com`", ALLOWED_OPENAI_COSTS_ENDPOINT)
REQUIRED_BILLING_PROBE_MARKERS = (
    ALLOWED_OPENAI_COSTS_ENDPOINT,
    'urllib.request.Request(url, method="GET")',
    "def _refuse_if_inference",
    "INFERENCE_PATHS",
)

OPENAI_HOST = re.compile(r"api\.openai\.com", re.I)
OPENAI_CREDENTIAL_OR_SDK = re.compile(
    r"OPENAI_(?:API_KEY|BASE_URL)|"
    r"(?:^|[;\n])\s*(?:from\s+openai\s+import|import\s+openai(?:\s|$))|"
    r"(?:require\(|from\s+)[\"']openai[\"']|[\"']openai[\"']\s*:\s*[\"']",
    re.I,
)
OPENAI_INFERENCE_ENDPOINT = re.compile(
    r"https?://api\.openai\.com/v1/(?:chat/completions|completions|responses|embeddings|"
    r"images/generations|audio(?:/|$)|assistants(?:/|$)|threads(?:/|$)|fine_tuning(?:/|$))",
    re.I,
)
UNSAFE_HTTP_METHOD = re.compile(r"\b(?:post|put|patch|delete)\s*\(", re.I)

FORBIDDEN_RUNTIME_PATTERNS = {
    "Anthropic API": re.compile(
        r"api\.anthropic\.com|ANTHROPIC_API_KEY|ANTHROPIC_BASE_URL|"
        r"(?:^|[;\n])\s*(?:from\s+anthropic\s+import|import\s+anthropic(?:\s|$))|"
        r"(?:require\(|from\s+)[\"']@?anthropic(?:-ai)?[\"']|[\"']@?anthropic(?:-ai)?[\"']\s*:\s*[\"']",
        re.I,
    ),
    "Gemini API": re.compile(
        r"generativelanguage\.googleapis\.com|GEMINI_API_KEY|GOOGLE_API_KEY|"
        r"google\.generativeai|@google/generative-ai",
        re.I,
    ),
    "launchd": re.compile(r"(?:^|/)LaunchAgents?(?:/|$)|\blaunchctl\b", re.I),
}
SKIP_PARTS = {"tests", "fixtures", "references", "node_modules", ".git", "preparation"}


def is_exact_openai_billing_probe(relative: str, text: str) -> bool:
    """Allow one audited, GET-only billing collector without allowing model inference."""
    if relative != BILLING_PROBE_RELATIVE_PATH:
        return False
    if not all(marker in text for marker in REQUIRED_BILLING_PROBE_MARKERS):
        return False
    if OPENAI_CREDENTIAL_OR_SDK.search(text) or OPENAI_INFERENCE_ENDPOINT.search(text):
        return False
    if UNSAFE_HTTP_METHOD.search(text):
        return False
    remaining = text
    for context in ALLOWED_OPENAI_HOST_CONTEXTS:
        remaining = remaining.replace(context, "")
    return not OPENAI_HOST.search(remaining)


def scan_runtime(
    root: Path,
    paths: Iterable[str] = ("status/collector", "status/deploy", "status/web"),
) -> dict[str, Any]:
    findings: list[dict[str, str]] = []
    billing_exceptions: list[dict[str, str]] = []
    root = Path(root).resolve()
    for relative in paths:
        target = (root / relative).resolve()
        if not target.exists():
            continue
        for path in target.rglob("*"):
            if not path.is_file() or any(part in SKIP_PARTS for part in path.relative_to(root).parts):
                continue
            if path.suffix.lower() not in {".py", ".js", ".ts", ".tsx", ".sh", ".json", ".yml", ".yaml"}:
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            relative_path = path.relative_to(root).as_posix()
            openai_present = bool(OPENAI_HOST.search(text) or OPENAI_CREDENTIAL_OR_SDK.search(text))
            if OPENAI_INFERENCE_ENDPOINT.search(text):
                findings.append({"path": relative_path, "pattern": "OpenAI inference endpoint"})
            elif openai_present:
                if is_exact_openai_billing_probe(relative_path, text):
                    billing_exceptions.append({"path": relative_path, "exception": "OPENAI_BILLING_GET_ONLY"})
                else:
                    findings.append({"path": relative_path, "pattern": "OpenAI API"})
            for name, pattern in FORBIDDEN_RUNTIME_PATTERNS.items():
                if pattern.search(text):
                    findings.append({"path": relative_path, "pattern": name})
    passed = not findings
    return {
        "state": "PASS" if passed else "FAIL",
        "findings": findings,
        "billing_exceptions": billing_exceptions,
        "production_agent_dependency": 0 if passed else "UNKNOWN",
        "production_llm_calls": 0 if passed else "UNKNOWN",
        "production_token_consumption": 0 if passed else "UNKNOWN",
    }
