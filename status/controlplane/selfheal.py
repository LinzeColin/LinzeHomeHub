"""Bounded, exact-target self-heal with truthful post-probe semantics."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from typing import Any, Callable, Mapping

from .commands import CommandResult, run_command
from .state import StateError, atomic_write_json, exclusive_lock, load_json


@dataclass(frozen=True)
class TargetPolicy:
    target_id: str
    probe_argv: tuple[str, ...]
    action_argv: tuple[str, ...]
    timeout_seconds: int = 30
    cooldown_seconds: int = 300
    maximum_actions_per_window: int = 3
    window_seconds: int = 3600


@dataclass(frozen=True)
class HealOutcome:
    target_id: str
    state: str
    reason: str
    action_result: Mapping[str, Any] | None
    pre_probe: Mapping[str, Any]
    post_probe: Mapping[str, Any] | None
    observed_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "target_id": self.target_id,
            "state": self.state,
            "reason": self.reason,
            "action_result": dict(self.action_result) if self.action_result else None,
            "pre_probe": dict(self.pre_probe),
            "post_probe": dict(self.post_probe) if self.post_probe else None,
            "observed_at": self.observed_at,
        }


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.replace(microsecond=0).isoformat()


def _result(value: CommandResult) -> dict[str, Any]:
    return {
        "returncode": value.returncode,
        "ok": value.ok,
        "timed_out": value.timed_out,
        "stdout": value.stdout[-4000:],
        "stderr": value.stderr[-4000:],
    }


def heal_target(
    policy: TargetPolicy,
    *,
    state_path: Path,
    lock_path: Path,
    now: datetime | None = None,
    runner: Callable[..., CommandResult] = run_command,
) -> HealOutcome:
    instant = (now or _now()).astimezone(timezone.utc)
    with exclusive_lock(lock_path, blocking=False):
        state = load_json(state_path, {"actions": []}) or {"actions": []}
        actions = []
        for raw in state.get("actions", []):
            try:
                when = datetime.fromisoformat(str(raw["at"]).replace("Z", "+00:00"))
            except Exception:
                continue
            if instant - when <= timedelta(seconds=policy.window_seconds):
                actions.append({"at": _iso(when), "result": raw.get("result", "UNKNOWN")})

        pre = runner(policy.probe_argv, timeout=policy.timeout_seconds)
        if pre.ok:
            outcome = HealOutcome(
                policy.target_id, "HEALTHY", "pre_probe_healthy", None,
                _result(pre), None, _iso(instant),
            )
            atomic_write_json(state_path, {"target_id": policy.target_id, "actions": actions, "last_outcome": outcome.to_dict()})
            return outcome

        if actions:
            last = datetime.fromisoformat(actions[-1]["at"].replace("Z", "+00:00"))
            if instant - last < timedelta(seconds=policy.cooldown_seconds):
                outcome = HealOutcome(
                    policy.target_id, "COOLDOWN", "cooldown_active", None,
                    _result(pre), None, _iso(instant),
                )
                atomic_write_json(state_path, {"target_id": policy.target_id, "actions": actions, "last_outcome": outcome.to_dict()})
                return outcome

        if len(actions) >= policy.maximum_actions_per_window:
            outcome = HealOutcome(
                policy.target_id, "BUDGET_EXHAUSTED", "action_budget_exhausted", None,
                _result(pre), None, _iso(instant),
            )
            atomic_write_json(state_path, {"target_id": policy.target_id, "actions": actions, "last_outcome": outcome.to_dict()})
            return outcome

        action = runner(policy.action_argv, timeout=policy.timeout_seconds)
        actions.append({"at": _iso(instant), "result": "COMMAND_OK" if action.ok else "COMMAND_FAILED"})
        if not action.ok:
            outcome = HealOutcome(
                policy.target_id, "ACTION_FAILED", "action_command_failed",
                _result(action), _result(pre), None, _iso(instant),
            )
            atomic_write_json(state_path, {"target_id": policy.target_id, "actions": actions, "last_outcome": outcome.to_dict()})
            return outcome

        post = runner(policy.probe_argv, timeout=policy.timeout_seconds)
        if post.ok:
            outcome = HealOutcome(
                policy.target_id, "RECOVERED", "action_and_post_probe_succeeded",
                _result(action), _result(pre), _result(post), _iso(instant),
            )
        else:
            outcome = HealOutcome(
                policy.target_id, "FAILED", "post_probe_unhealthy",
                _result(action), _result(pre), _result(post), _iso(instant),
            )
        atomic_write_json(state_path, {"target_id": policy.target_id, "actions": actions, "last_outcome": outcome.to_dict()})
        return outcome


def policy_from_json(value: Mapping[str, Any]) -> TargetPolicy:
    return TargetPolicy(
        target_id=str(value["target_id"]),
        probe_argv=tuple(str(item) for item in value["probe_argv"]),
        action_argv=tuple(str(item) for item in value["action_argv"]),
        timeout_seconds=int(value.get("timeout_seconds", 30)),
        cooldown_seconds=int(value.get("cooldown_seconds", 300)),
        maximum_actions_per_window=int(value.get("maximum_actions_per_window", 3)),
        window_seconds=int(value.get("window_seconds", 3600)),
    )
