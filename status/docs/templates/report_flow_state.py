#!/usr/bin/env python3
"""把「这一步刚跑完」写进 docs/governance/flow_state.json —— 各项目直接拷走用。

用法（在你自己的 CI / cron 里，那一步真的跑完之后调用）：

    python3 report_flow_state.py --repo-root . --project-dir arxiv-daily-push \\
        --key BL-ARXIV-DAILY.deliver --state healthy --n 128 \\
        --note "本轮投递 128 篇"

它只做一件事：把一条记录合并进那个 JSON，带上**当前时间戳和时区偏移**。
不联网、不装依赖、不调任何模型 —— 纯标准库，符合全域「零 Agent 零 Token」规则。

★ 三条不会让步的规矩（status 侧同样会强制）：
  1. 过期一律降级成「不确定」。status 默认 26 小时，超了这条就不算数。
     所以**必须在那一步真的跑完之后调用**，不能开工时先写好。
  2. 报忧和报喜一样重要。跑失败就写 blocked / blocked_by_input，
     status 会照实显示并进 owner 的待办表。**这个通道不是用来刷绿的。**
  3. 绝不能拿相邻信号冒充这一步的产出。「进程还活着」不等于「数据导出了」——
     `--n` 应该是**这一步真实产出的条数**，没有产出就别写。

契约全文：LinzeHomeHub/status/docs/FLOW_STATE_CONTRACT.md
"""
import argparse
import json
import os
import re
from datetime import datetime, timedelta, timezone

STATES = ("healthy", "degraded", "blocked", "blocked_by_input",
          "blocked_by_policy", "not_built", "unknown")
KEY_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,80}$")
CN = timezone(timedelta(hours=8))


def report(repo_root, project_dir, key, state, n=None, note="", now=None):
    if state not in STATES:
        raise SystemExit("state 必须是这七个之一：%s" % ", ".join(STATES))
    if not KEY_RE.match(key):
        raise SystemExit("key 只允许 A-Za-z0-9_.:- ，最长 80 字符")
    rel = os.path.join(project_dir, "docs", "governance") if project_dir not in (".", "") \
        else os.path.join("docs", "governance")
    path = os.path.join(repo_root, rel, "flow_state.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)

    doc = {"schema": "linze.flow_state.v1", "steps": {}}
    if os.path.exists(path):
        try:
            old = json.load(open(path, encoding="utf-8"))
            if isinstance(old, dict) and isinstance(old.get("steps"), dict):
                doc = old
                doc.setdefault("schema", "linze.flow_state.v1")
        except Exception:
            # 读不动就重建，但**不静默丢**：把原文件挪开留证
            os.replace(path, path + ".unreadable")

    rec = {"state": state, "at": (now or datetime.now(CN)).isoformat()}
    if n is not None:
        rec["n"] = int(n)
    if note:
        rec["note"] = str(note)[:120]
    doc["steps"][key] = rec

    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.write("\n")
    os.replace(tmp, path)                      # 原子替换，避免 CI 中断留半个文件
    return path


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo-root", default=".")
    ap.add_argument("--project-dir", default=".",
                    help="单仓多项目时填项目目录名；整仓一个项目填 .")
    ap.add_argument("--key", required=True, help="<baseline id>.<stage>，和 flow.yaml 一致")
    ap.add_argument("--state", required=True, choices=STATES)
    ap.add_argument("--n", type=int, default=None, help="这一步**真实产出**的条数")
    ap.add_argument("--note", default="")
    a = ap.parse_args()
    print("written:", report(a.repo_root, a.project_dir, a.key, a.state, a.n, a.note))


if __name__ == "__main__":
    main()
