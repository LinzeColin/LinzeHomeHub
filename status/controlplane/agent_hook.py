#!/usr/bin/env python3
"""Session-scoped command hook: stdin JSON -> redacted append-only JSONL.

This process exits after one event. It is not a daemon and never stores raw
prompt or tool content.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

from .capture import normalize_event


def _required(name: str, value: str | None) -> str:
    result = str(value or "").strip()
    if not result:
        raise ValueError(f"missing {name}")
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="status-agent-hook")
    parser.add_argument("--provider", choices=("codex", "claude"), required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--project-id", default=os.environ.get("STATUS_AGENT_PROJECT_ID", "status.linzezhang.com"))
    parser.add_argument("--run-id", default=os.environ.get("STATUS_AGENT_RUN_ID"))
    parser.add_argument("--task-id", default=os.environ.get("STATUS_AGENT_TASK_ID"))
    parser.add_argument("--intent-hash", default=os.environ.get("STATUS_AGENT_INTENT_HASH"))
    parser.add_argument("--session-id", default=os.environ.get("STATUS_AGENT_SESSION_ID"))
    args = parser.parse_args(argv)
    raw = json.load(sys.stdin)
    event = normalize_event(
        raw,
        provider=args.provider,
        project_id=_required("project_id", args.project_id),
        run_id=_required("run_id", args.run_id),
        task_id=_required("task_id", args.task_id),
        intent_hash=_required("intent_hash", args.intent_hash),
        session_id=_required("session_id", args.session_id),
    )
    output = Path(args.output).expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
