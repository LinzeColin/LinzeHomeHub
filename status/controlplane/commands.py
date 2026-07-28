"""Safe subprocess execution with argv arrays and explicit executable allowlists."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import os
from pathlib import Path
import shutil
import subprocess
from typing import Iterable, Mapping, Sequence


DEFAULT_ALLOWED = frozenset({
    "git", "docker", "systemctl", "journalctl", "curl", "python3",
    "rclone", "wrangler", "oci", "sha256sum", "openssl",
})


@dataclass(frozen=True)
class CommandResult:
    argv: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool = False

    @property
    def ok(self) -> bool:
        return self.returncode == 0 and not self.timed_out

    def to_dict(self) -> dict:
        value = asdict(self)
        value["argv"] = list(self.argv)
        value["ok"] = self.ok
        return value


class CommandPolicyError(RuntimeError):
    pass


def run_command(
    argv: Sequence[str],
    *,
    cwd: Path | None = None,
    timeout: int = 30,
    allowed_executables: Iterable[str] = DEFAULT_ALLOWED,
    extra_env: Mapping[str, str] | None = None,
    max_output: int = 200_000,
) -> CommandResult:
    if not argv or not isinstance(argv[0], str):
        raise CommandPolicyError("argv must be a non-empty string sequence")
    executable = Path(argv[0]).name
    allowed = frozenset(allowed_executables)
    if executable not in allowed:
        raise CommandPolicyError(f"executable is not allowlisted: {executable}")
    resolved = shutil.which(argv[0])
    if not resolved:
        return CommandResult(tuple(argv), 127, "", f"executable unavailable: {executable}")
    env = os.environ.copy()
    if extra_env:
        env.update({str(k): str(v) for k, v in extra_env.items()})
    try:
        completed = subprocess.run(
            list(argv),
            cwd=str(cwd) if cwd else None,
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            shell=False,
        )
        return CommandResult(
            tuple(argv),
            completed.returncode,
            completed.stdout[:max_output],
            completed.stderr[:max_output],
        )
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout if isinstance(exc.stdout, str) else ""
        stderr = exc.stderr if isinstance(exc.stderr, str) else ""
        return CommandResult(tuple(argv), 124, stdout[:max_output], stderr[:max_output], True)
