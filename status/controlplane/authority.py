"""Private-Database authority adapter with no-empty-commit semantics."""

from __future__ import annotations

from datetime import datetime, timezone
import re
from pathlib import Path
from typing import Any, Iterable, Mapping

from .commands import run_command
from .state import atomic_write_json, exclusive_lock


class AuthoritySyncError(RuntimeError):
    pass


_SAFE_PART = re.compile(r"[^A-Za-z0-9._:-]+")


def _safe_part(value: Any, field: str) -> str:
    raw = str(value).strip()
    safe = _SAFE_PART.sub("-", raw).strip(".-")[:180]
    if not safe or safe in {".", ".."}:
        raise AuthoritySyncError(f"invalid {field}")
    return safe


def _date_path(event: Mapping[str, Any]) -> Path:
    raw = str(event.get("completed_at") or datetime.now(timezone.utc).isoformat())
    date = raw[:10]
    try:
        datetime.strptime(date, "%Y-%m-%d")
    except ValueError as exc:
        raise AuthoritySyncError("invalid completed_at date") from exc
    event_type = _safe_part(event.get("fact_type", "status.fact"), "fact_type")
    if "event_id" not in event:
        raise AuthoritySyncError("event_id missing")
    event_id = _safe_part(event["event_id"], "event_id")
    return Path("facts/status") / date / event_type / f"{event_id}.json"


def stage_events(authority_worktree: Path, events: Iterable[Mapping[str, Any]]) -> list[Path]:
    root = authority_worktree.resolve()
    written = []
    for event in events:
        relative = _date_path(event)
        target = (root / relative).resolve()
        try:
            target.relative_to(root)
        except ValueError as exc:
            raise AuthoritySyncError("authority fact path escaped worktree") from exc
        atomic_write_json(target, dict(event))
        written.append(relative)
    return written


def _require_clean(root: Path) -> None:
    status = run_command(["git", "status", "--porcelain"], cwd=root)
    if not status.ok:
        raise AuthoritySyncError(f"cannot inspect authority worktree: {status.stderr}")
    if status.stdout.strip():
        raise AuthoritySyncError("authority worktree must be clean before synchronization")


def _push(root: Path) -> bool:
    result = run_command(["git", "push"], cwd=root, timeout=180)
    if not result.ok:
        raise AuthoritySyncError(f"authority push failed: {result.stderr}")
    return True


def sync_events(
    authority_worktree: Path,
    events: list[Mapping[str, Any]],
    *,
    commit_message: str,
    push: bool = False,
    lock_path: Path | None = None,
) -> dict[str, Any]:
    root = authority_worktree.resolve()
    inside = run_command(["git", "rev-parse", "--is-inside-work-tree"], cwd=root)
    if not inside.ok or inside.stdout.strip() != "true":
        raise AuthoritySyncError("authority worktree is not a Git worktree")
    git_lock = run_command(["git", "rev-parse", "--git-path", "status-authority-sync.lock"], cwd=root)
    if not git_lock.ok:
        raise AuthoritySyncError("cannot resolve authority Git lock path")
    resolved_lock = Path(git_lock.stdout.strip())
    if not resolved_lock.is_absolute():
        resolved_lock = (root / resolved_lock).resolve()
    lock = lock_path or resolved_lock
    with exclusive_lock(lock):
        _require_clean(root)
        paths = stage_events(root, events)
        if not paths:
            return {"state": "NO_NEW_FACT", "committed": False, "pushed": False, "paths": []}
        add = run_command(["git", "add", "--", *[str(path) for path in paths]], cwd=root, timeout=60)
        if not add.ok:
            raise AuthoritySyncError(f"cannot stage authority facts: {add.stderr}")
        diff = run_command(["git", "diff", "--cached", "--quiet", "--exit-code"], cwd=root)
        if diff.returncode == 0:
            pushed = _push(root) if push else False
            return {
                "state": "NO_NEW_FACT", "committed": False, "pushed": pushed,
                "paths": [str(path) for path in paths],
            }
        if diff.returncode != 1:
            raise AuthoritySyncError(f"cannot inspect staged authority changes: {diff.stderr}")
        commit = run_command(["git", "commit", "-m", commit_message, "--", *[str(path) for path in paths]], cwd=root, timeout=120)
        if not commit.ok:
            raise AuthoritySyncError(f"authority commit failed: {commit.stderr}")
        head = run_command(["git", "rev-parse", "HEAD"], cwd=root)
        if not head.ok:
            raise AuthoritySyncError("cannot resolve authority commit")
        pushed = _push(root) if push else False
        return {
            "state": "COMMITTED",
            "committed": True,
            "pushed": pushed,
            "commit": head.stdout.strip(),
            "paths": [str(path) for path in paths],
        }
