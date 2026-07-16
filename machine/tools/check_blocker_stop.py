#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
check_blocker_stop.py —— 阻塞重审门

规则：一个阻塞如果只有 Owner 能解（owner_only=true），
      那么第 1 次遇到 -> 登记 + 上浮 + 本次结束；
      第 2 次及以后对它做 audit/review/recheck -> FAIL。

这条门如果 20 天前存在，KMFA 的 Residual Difference 会停在第 1 个，
而不是第 40 个。

输入:
  machine/facts/blockers.json   [{"id","内容","owner_only","首次登记"}]
  machine/runs/*.json           [{"run_id","blocker_id","action","ts"}]
                                action ∈ {audit, resolve, escalate}

用法:  python3 machine/tools/check_blocker_stop.py [--machine machine]
退出码: 0=PASS  1=FAIL
"""
import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

AUDIT_ACTIONS = {"audit", "review", "recheck", "reaudit", "replay"}


def load_blockers(machine: Path) -> dict:
    f = machine / "facts" / "blockers.json"
    if not f.exists():
        return {}
    return {b["id"]: b for b in json.loads(f.read_text(encoding="utf-8"))}


def load_runs(machine: Path) -> list:
    runs = []
    d = machine / "runs"
    if not d.is_dir():
        return runs
    for f in sorted(d.glob("*.json")):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            print(f"  ! 跳过无法解析的运行记录 {f.name}: {e}")
            continue
        runs.extend(data if isinstance(data, list) else [data])
    return runs


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--machine", default="machine")
    args = ap.parse_args()
    machine = Path(args.machine)

    blockers = load_blockers(machine)
    runs = load_runs(machine)

    if not blockers:
        print("PASS —— 无登记阻塞")
        return 0

    # 统计每个阻塞被 audit 了几次
    audits = defaultdict(list)
    for r in runs:
        bid = r.get("blocker_id")
        if bid and str(r.get("action", "")).lower() in AUDIT_ACTIONS:
            audits[bid].append(r.get("run_id", "?"))

    failures = []
    for bid, blk in blockers.items():
        n = len(audits.get(bid, []))
        if blk.get("owner_only") and n > 1:
            failures.append(
                f"[阻塞重审门] {bid} 只有 Owner 能解，却被重审了 {n} 次: "
                f"{audits[bid][:5]}{' ...' if n > 5 else ''}\n"
                f"      内容: {blk.get('内容', '')}\n"
                f"      正确做法: 第 1 次登记后上浮到 00_我在哪.md 第二节，本次结束。"
                f"重审不会产生新信息。"
            )

    # 未上浮的 Owner 阻塞也算 FAIL
    surfaced = Path("文档/00_我在哪.md")
    if surfaced.exists():
        body = surfaced.read_text(encoding="utf-8")
        for bid, blk in blockers.items():
            if blk.get("owner_only") and bid not in body:
                failures.append(
                    f"[阻塞重审门] {bid} 是 Owner 专属阻塞，但没有上浮到 00_我在哪.md。"
                    f"Owner 看不见的阻塞永远不会被解。"
                )

    if failures:
        print(f"FAIL —— {len(failures)} 项\n")
        for x in failures:
            print("  ✗ " + x)
        return 1
    print(f"PASS —— {len(blockers)} 个阻塞，无重复重审")
    return 0


if __name__ == "__main__":
    sys.exit(main())
