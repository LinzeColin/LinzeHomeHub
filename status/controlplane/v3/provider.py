# STATUS_AGENT_V3_MANAGED: v0.0.0.8
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import os
from pathlib import Path
import re
import shutil
import subprocess
from typing import Any, Mapping, Sequence

PROVIDERS = {"codex", "claude"}
SESSION_SUFFIXES = {".jsonl", ".json", ".log", ".txt", ".md"}
MAX_TRANSCRIPT_BYTES = 64 * 1024 * 1024
MAX_SESSION_FILES = 4096
SESSION_BINDING_RE = re.compile(r"^status-agent-v3-binding:[0-9a-f]{32}$")
DEVELOPMENT_ONLY_PROVIDER_EXECUTION = "DEVELOPMENT_ONLY_NO_PRODUCTION_AGENT_OR_TOKEN_DEPENDENCY"
CLAUDE_SINGLE_PROMPT_READ_ONLY = "CLAUDE_SINGLE_PROMPT_READ_ONLY"
CLAUDE_READ_ONLY_TOOL = "Read"
CLAUDE_MAX_BUDGET_USD = "1.00"
CLAUDE_CONFIG_DIR_ENV = "CLAUDE_CONFIG_DIR"
CLAUDE_SKIP_PROMPT_HISTORY_ENV = "CLAUDE_CODE_SKIP_PROMPT_HISTORY"
CLAUDE_NO_SESSION_PERSISTENCE_FLAG = "--no-session-persistence"
CLAUDE_SETTING_SOURCES = "project,local"
CLAUDE_ALLOWED_SETTING_SOURCES = ("project", "local")
CLAUDE_USER_SETTINGS_SOURCE = "user"


@dataclass(frozen=True)
class SessionFileState:
    path: Path
    mtime_ns: int
    size: int

    def identity(self) -> tuple[int, int]:
        return (self.mtime_ns, self.size)


def validate_transcript_binding(value: str) -> str:
    binding = str(value or "")
    if not SESSION_BINDING_RE.fullmatch(binding):
        raise ValueError("invalid provider transcript binding")
    return binding


def transcript_binding_sha256(value: str) -> str:
    return sha256(validate_transcript_binding(value).encode("utf-8")).hexdigest()


def _environment_value(name: str, environ: Mapping[str, str] | None = None) -> str:
    environment = os.environ if environ is None else environ
    return str(environment.get(name) or "").strip()


def _claude_config_dir(*, home: Path | None = None, environ: Mapping[str, str] | None = None) -> Path:
    configured = _environment_value(CLAUDE_CONFIG_DIR_ENV, environ)
    if configured:
        config_dir = Path(configured).expanduser()
        if not config_dir.is_absolute():
            raise ValueError("CLAUDE_CONFIG_DIR must be absolute for governed transcript capture")
        return config_dir.resolve()
    return Path(home or Path.home()).expanduser().resolve() / ".claude"


def validate_claude_setting_sources(value: str = CLAUDE_SETTING_SOURCES) -> tuple[str, ...]:
    selected = tuple(source.strip().lower() for source in str(value).split(",") if source.strip())
    if selected != CLAUDE_ALLOWED_SETTING_SOURCES or CLAUDE_USER_SETTINGS_SOURCE in selected:
        raise RuntimeError("governed Claude setting sources must be exactly project,local with user settings disabled")
    return selected


def validate_claude_transcript_persistence(*, environ: Mapping[str, str] | None = None) -> None:
    # Treat every non-empty value as unsafe or ambiguous: a governed run must
    # never spend development tokens when Claude transcript persistence may be off.
    if _environment_value(CLAUDE_SKIP_PROMPT_HISTORY_ENV, environ):
        raise RuntimeError("Claude transcript persistence is disabled by CLAUDE_CODE_SKIP_PROMPT_HISTORY")


def provider_session_roots(
    provider: str,
    *,
    home: Path | None = None,
    environ: Mapping[str, str] | None = None,
) -> list[Path]:
    provider = provider.lower().strip()
    if provider not in PROVIDERS:
        raise ValueError("unsupported provider")
    if provider == "claude":
        return [_claude_config_dir(home=home, environ=environ) / "projects"]
    root = Path(home or Path.home()).expanduser().resolve()
    return [root / ".codex" / "sessions"]


def discover(
    provider: str,
    *,
    home: Path | None = None,
    environ: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    provider = provider.lower().strip()
    if provider not in PROVIDERS:
        raise ValueError("unsupported provider")
    binary = shutil.which(provider)
    roots = provider_session_roots(provider, home=home, environ=environ)
    return {
        "provider": provider,
        "binary": binary,
        "available": bool(binary),
        "session_roots": [str(path) for path in roots],
        "session_root_exists": any(path.exists() for path in roots),
        "development_only": True,
        "production_agent_dependency": 0,
        "production_token_consumption": 0,
        "daemon_required": False,
        "launchd_required": False,
    }


def snapshot_session_files(provider: str, roots: Sequence[Path] | None = None) -> dict[str, SessionFileState]:
    selected_roots = [Path(path).expanduser().resolve() for path in (roots or provider_session_roots(provider))]
    result: dict[str, SessionFileState] = {}
    eligible_count = 0
    for root in selected_roots:
        if not root.is_dir() or root.is_symlink():
            continue
        for path in root.rglob("*"):
            try:
                if not path.is_file() or path.is_symlink() or path.suffix.lower() not in SESSION_SUFFIXES:
                    continue
                resolved = path.resolve()
                resolved.relative_to(root)
                stat = resolved.stat()
                if stat.st_size <= 0 or stat.st_size > MAX_TRANSCRIPT_BYTES:
                    continue
                eligible_count += 1
                if eligible_count > MAX_SESSION_FILES:
                    raise RuntimeError("provider session scan exceeds bounded file limit")
                result[str(resolved)] = SessionFileState(resolved, stat.st_mtime_ns, stat.st_size)
            except (OSError, ValueError):
                continue
    return result


def changed_transcript_candidates(before: Mapping[str, SessionFileState], after: Mapping[str, SessionFileState]) -> list[SessionFileState]:
    candidates = [state for key, state in after.items() if key not in before or before[key].identity() != state.identity()]
    candidates.sort(key=lambda item: (item.mtime_ns, item.size, str(item.path)), reverse=True)
    return candidates


def transcript_binding_candidates(before: Mapping[str, SessionFileState], after: Mapping[str, SessionFileState], *, transcript_binding: str) -> list[SessionFileState]:
    binding = validate_transcript_binding(transcript_binding)
    return [state for state in changed_transcript_candidates(before, after) if _contains_binding(state.path, binding)]


def _contains_binding(path: Path, binding: str) -> bool:
    needle = validate_transcript_binding(binding).encode("utf-8")
    overlap = b""
    try:
        with Path(path).open("rb") as handle:
            while True:
                chunk = handle.read(1024 * 1024)
                if not chunk:
                    return False
                if needle in overlap + chunk:
                    return True
                overlap = (overlap + chunk)[-(len(needle) - 1):]
    except OSError:
        return False


def changed_transcript(before: Mapping[str, SessionFileState], after: Mapping[str, SessionFileState], *, transcript_binding: str | None = None) -> SessionFileState | None:
    candidates = changed_transcript_candidates(before, after)
    if not candidates:
        return None
    if transcript_binding is None:
        return candidates[0]
    matching = transcript_binding_candidates(before, after, transcript_binding=transcript_binding)
    return matching[0] if len(matching) == 1 else None


def copy_transcript(source: Path, destination_dir: Path, *, transcript_binding: str | None = None) -> dict[str, Any]:
    source = Path(source).resolve()
    if not source.is_file() or source.is_symlink():
        raise ValueError("provider transcript is not a regular file")
    size = source.stat().st_size
    if size <= 0 or size > MAX_TRANSCRIPT_BYTES:
        raise ValueError("provider transcript size is outside safety bounds")
    if transcript_binding is not None and not _contains_binding(source, transcript_binding):
        raise ValueError("provider transcript does not contain this run binding")
    destination_dir = Path(destination_dir).resolve()
    destination_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    suffix = source.suffix.lower() if source.suffix.lower() in SESSION_SUFFIXES else ".bin"
    destination = destination_dir / f"provider-transcript{suffix}"
    with source.open("rb") as reader, destination.open("wb") as writer:
        while True:
            chunk = reader.read(1024 * 1024)
            if not chunk:
                break
            writer.write(chunk)
        writer.flush()
        os.fsync(writer.fileno())
    destination.chmod(0o600)
    data_sha = sha256(destination.read_bytes()).hexdigest()
    return {
        "path": destination,
        "sha256": data_sha,
        "bytes": destination.stat().st_size,
        "source_path_sha256": sha256(str(source).encode("utf-8")).hexdigest(),
        "source_name": source.name,
        "transcript_binding_sha256": transcript_binding_sha256(transcript_binding) if transcript_binding else None,
    }


def _help_text(binary: str, *args: str) -> str:
    try:
        completed = subprocess.run([binary, *args, "--help"], text=True, capture_output=True, timeout=20, check=False)
        return f"{completed.stdout}\n{completed.stderr}".lower()
    except (OSError, subprocess.TimeoutExpired):
        return ""


def _require_help_features(provider: str, help_text: str, features: Sequence[str]) -> None:
    missing = [feature for feature in features if feature.lower() not in help_text]
    if missing:
        raise RuntimeError(f"{provider} CLI missing required read-only noninteractive features: {','.join(missing)}")


def default_provider_command(provider: str, *, intent_path: Path, repo_root: Path, session_binding: str, binary: str | None = None) -> list[str]:
    provider = provider.lower().strip()
    if provider not in PROVIDERS:
        raise ValueError("unsupported provider")
    executable = binary or shutil.which(provider)
    if not executable:
        raise FileNotFoundError(f"provider binary unavailable: {provider}")
    intent_path = Path(intent_path).resolve()
    repo_root = Path(repo_root).resolve()
    binding = validate_transcript_binding(session_binding)
    allowed_read_paths = (
        intent_path,
        repo_root / "status/controlplane/v3/config/provider_contracts.json",
        repo_root / "status/web/index.html",
    )
    allowed_read_path_text = "、".join(str(path) for path in allowed_read_paths)
    prompt = (
        "这是 status.linzezhang.com Agentic Walking Skeleton 的受控开发期只读会话。"
        f"只允许使用内置 Read 工具读取以下受控文件：{allowed_read_path_text}。"
        "据此检查当前仓库 status/ 的 UI 与治理合同事实；不得读取其它路径、不得使用 shell、网络、写入或任何生产副作用。"
        "只输出一个简短 JSON，字段为 state、observations、next_action。"
        f"本次会话关联标记为 {binding}；必须原样保留在 Provider 自身 transcript 中。"
    )
    if provider == "codex":
        help_text = _help_text(executable, "exec")
        _require_help_features("Codex", help_text, ("exec", "--sandbox", "read-only", "--json"))
        return [executable, "exec", "--sandbox", "read-only", "--json", prompt]
    validate_claude_setting_sources()
    validate_claude_transcript_persistence()
    provider_session_roots("claude")
    help_text = _help_text(executable)
    _require_help_features(
        "Claude",
        help_text,
        (
            "--print",
            "--output-format",
            "stream-json",
            "--verbose",
            "--permission-mode",
            "plan",
            "--tools",
            "--max-budget-usd",
            "--safe-mode",
            "--no-chrome",
            "--setting-sources",
        ),
    )
    # The installed Claude CLI no longer exposes --max-turns.  This is a
    # single-prompt read-only contract instead: --print exits after the
    # response, the sole built-in tool is Read, safe mode disables custom
    # hooks/plugins/MCP, and the explicit budget caps development cost.
    command = [
        executable,
        "--print",
        "--output-format",
        "stream-json",
        "--verbose",
        "--permission-mode",
        "plan",
        "--tools",
        CLAUDE_READ_ONLY_TOOL,
        "--max-budget-usd",
        CLAUDE_MAX_BUDGET_USD,
        "--setting-sources",
        CLAUDE_SETTING_SOURCES,
        "--safe-mode",
        "--no-chrome",
        prompt,
    ]
    if CLAUDE_NO_SESSION_PERSISTENCE_FLAG in command:
        raise RuntimeError("governed Claude command may not disable session persistence")
    return command
