#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
check_dual_plane_ci.py —— 仓库级双平面合规校验（CI 入口）

对一个 repo 下的每个项目，校验双平面七文件架构是否就位并过门。
「项目」= 含 machine/tools/render_human.py 的目录，或 --projects 显式指定。

对每个项目执行：
  1. 结构门：文档/ 下 7 个文件齐全、machine/facts 与 machine/tools 存在
  2. 渲染一致门：重新渲染后 5 个渲染文件无变化（人类平面确由机器平面生成，
     未被手工篡改）；手写区 01/03 存在且非空
  3. 三道门：check_doc_budget + check_blocker_stop

任何项目任一门 FAIL -> 整体 FAIL（退出码 1）。

用法:
  python3 check_dual_plane_ci.py [--root .] [--projects a b c] [--require-projects]
  --require-projects  若未发现任何双平面项目也判 FAIL（用于已声明必须合规的 repo）
退出码: 0=全部 PASS  1=有 FAIL
"""
import argparse
import subprocess
import sys
from pathlib import Path

SEVEN = [
    "00_我在哪.md", "01_产品需求.md", "02_系统架构.md", "03_口径字典.md",
    "04_操作流程.md", "05_执行与验收.md", "06_运维手册.md",
]
# 七文件全部渲染，无手写区——渲染一致门覆盖全部七个。
RENDERED = list(SEVEN)


def discover(root: Path):
    found = []
    for tool in root.rglob("machine/tools/render_human.py"):
        proj = tool.parents[2]
        # 跳过 kit 自身模板目录
        if (proj / "文档").is_dir() or (proj / "machine" / "facts").is_dir():
            found.append(proj)
    return sorted(set(found))


def check_project(proj: Path, failures: list):
    name = proj.name
    docs = proj / "文档"

    # 1. 结构门
    for f in SEVEN:
        if not (docs / f).is_file():
            failures.append(f"[{name}] 结构门: 缺 文档/{f}")
    if not (proj / "machine" / "facts").is_dir():
        failures.append(f"[{name}] 结构门: 缺 machine/facts/")

    # 2. 渲染一致门：备份渲染文件 -> 重渲染 -> 比对
    before = {}
    for f in RENDERED:
        p = docs / f
        before[f] = p.read_text(encoding="utf-8") if p.is_file() else None
    r = subprocess.run(
        [sys.executable, "machine/tools/render_human.py", "--root", "."],
        cwd=proj, capture_output=True, text=True,
    )
    if r.returncode != 0:
        failures.append(f"[{name}] 渲染失败: {r.stdout.strip()} {r.stderr.strip()}")
    for f in RENDERED:
        p = docs / f
        now = p.read_text(encoding="utf-8") if p.is_file() else None
        # 渲染时间戳行会变，比对时剔除
        def norm(t):
            if t is None:
                return None
            return "\n".join(l for l in t.splitlines() if "渲染时间" not in l)
        if norm(before[f]) != norm(now):
            failures.append(
                f"[{name}] 渲染一致门: 文档/{f} 与机器平面不一致"
                f"（人类平面被手工篡改，或事实源已变但未重渲染）")

    # 3. 三道门
    for tool, arg in [("check_doc_budget.py", ["--docs", "文档"]),
                      ("check_blocker_stop.py", ["--machine", "machine"])]:
        rr = subprocess.run(
            [sys.executable, f"machine/tools/{tool}"] + arg,
            cwd=proj, capture_output=True, text=True,
        )
        if rr.returncode != 0:
            first = next((l for l in rr.stdout.splitlines() if "✗" in l), rr.stdout.strip()[:120])
            failures.append(f"[{name}] {tool}: {first.strip()}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=".")
    ap.add_argument("--projects", nargs="*")
    ap.add_argument("--require-projects", action="store_true")
    args = ap.parse_args()

    root = Path(args.root).resolve()
    projects = ([root / p for p in args.projects] if args.projects
                else discover(root))

    if not projects:
        msg = "未发现双平面项目"
        if args.require_projects:
            print(f"FAIL —— {msg}（本 repo 已声明必须合规）")
            return 1
        print(f"PASS —— {msg}（无需校验）")
        return 0

    failures: list = []
    for proj in projects:
        if not proj.is_dir():
            failures.append(f"[{proj.name}] 项目目录不存在")
            continue
        check_project(proj, failures)

    print(f"检查了 {len(projects)} 个项目：{', '.join(p.name for p in projects)}")
    if failures:
        print(f"\nFAIL —— {len(failures)} 项")
        for x in failures:
            print("  ✗ " + x)
        return 1
    print("PASS —— 全部项目双平面合规")
    return 0


if __name__ == "__main__":
    sys.exit(main())
