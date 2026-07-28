#!/usr/bin/env python3
"""Session-scoped command hook: stdin JSON -> redacted append-only JSONL.

This process exits after one event. It is not a daemon and never stores raw
prompt or tool content.

★ 它被装成 provider 的全局 command hook 后,会在**这台机器的每一次工具调用**上被拉起 ——
  包括所有和 status 无关的项目。所以有两条硬要求:

  1. 不在受治理的 run 里(session.env 没 source 过、四个 STATUS_AGENT_* 缺任意一个)时,
     必须**安静地什么都不做并以 0 退出**。原实现是直接抛 missing run_id,装上去等于让
     本机每一次工具调用都报一次错。
  2. 这条判断必须在导入 capture/redaction 之前完成 —— 每次工具调用都多花一次模块导入,
     在高频会话里是实打实的开销。
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

#: session.env 提供的四个绑定。缺任意一个都说明「当前不在受治理的 run 里」。
_SESSION_KEYS = ("STATUS_AGENT_RUN_ID", "STATUS_AGENT_TASK_ID",
                 "STATUS_AGENT_INTENT_HASH", "STATUS_AGENT_SESSION_ID")


def session_is_active(environ: "os._Environ[str] | dict[str, str] | None" = None) -> bool:
    source = os.environ if environ is None else environ
    return all(str(source.get(key) or "").strip() for key in _SESSION_KEYS)


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
    parser.add_argument(
        "--require-session", dest="require_session", action="store_true", default=True,
        help="不在受治理的 run 里就安静退出(默认;全局安装时必须保持开启)")
    parser.add_argument(
        "--no-require-session", dest="require_session", action="store_false",
        help="即使缺少 session 绑定也强行归一 —— 只给 fixture 测试用")
    args = parser.parse_args(argv)

    # 与 session_is_active 用同一把尺子:空白字符串算「没给」,否则 "   " 会被当成显式提供,
    # 绕过惰性判断再在下面 _required 里炸掉 —— 那正是这条守卫要防的东西。
    explicit = all(str(value or "").strip() for value in
                   (args.run_id, args.task_id, args.intent_hash, args.session_id))
    if args.require_session and not explicit and not session_is_active():
        # 全局 hook 的常态路径:不是受治理的 run,不读 stdin、不写盘、不报错。
        return 0

    # 走到这里才需要归一与脱敏,capture/redaction 到此刻再导入。
    from .capture import normalize_event

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
