#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
check_dual_plane_ci.py —— 仓库级双平面合规校验（CI 入口）

对一个 repo 下的每个项目，校验双平面七文件架构是否就位并过门。
「项目」= 含 machine/tools/render_human.py 的目录，或 --projects 显式指定。

对每个项目执行：
  1. 结构门：文档/ 下 7 个文件齐全、machine/facts 与 machine/tools 存在
  4. 语义门：facts/changelog.json 最新条目的 version 必须可见于 文档/06_运维手册.md；
     machine/runs/ 非空时，05 必须要么显示最新一条、要么说明省略了多少条
     （防「渲染器缺陷两侧一致、一致门恒绿」型潜伏——2026-07-18 切片缺陷教训）
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


SELF_TOOLS = Path(__file__).resolve().parent


def tool_path(proj: Path, name: str) -> str:
    """项目里有就用项目里那份；没有就回退到本校验器身边那份。

    为什么要回退：kit 的 7 个工具靠复制分发，全工作间 124 份 / 25192 行，
    去重后只需 1456 行 —— 94% 是复制品，而且会各自漂移
    （2026-08-18 实测 render_human.py 曾漂成 7 个版本，其中一个带着
    「变更记录取反」的 bug 藏了一个月）。

    有了回退，一个仓只需留一份工具，其余项目目录可以清空。
    保留项目内副本仍然有效 —— 这是**向后兼容**的增强，不是行为变更。
    MooMooAU 早就证明零 vendoring 可行：它有 governance.no_framework_copy
    守卫，复制共享工具进它的 machine/tools/ 直接 FAIL。

    返回相对 proj 的路径或绝对路径，供 subprocess 以 cwd=proj 调用。
    """
    local = proj / "machine" / "tools" / name
    if local.is_file():
        return f"machine/tools/{name}"
    shared = SELF_TOOLS / name
    if shared.is_file():
        return str(shared)
    return f"machine/tools/{name}"      # 让调用方以「缺文件」的方式失败，信息更直白


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
        [sys.executable, tool_path(proj, "render_human.py"), "--root", "."],
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

    # 4. 语义门：最新 changelog 条目必须真实渲染进运维手册（一致门测不出渲染器自身缺陷）
    chlog_path = proj / "machine" / "facts" / "changelog.json"
    manual_path = docs / "06_运维手册.md"
    if chlog_path.is_file() and manual_path.is_file():
        try:
            import json
            chlog = json.loads(chlog_path.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            chlog = []
        if isinstance(chlog, list) and chlog:
            latest = str(chlog[0].get("version", "")).strip()
            if latest and latest not in manual_path.read_text(encoding="utf-8"):
                failures.append(
                    f"[{name}] 语义门: changelog 最新条目 {latest} 未出现在 文档/06_运维手册.md"
                    f"（渲染器截断/切片缺陷，或条目顺序约定被破坏）")

    # 4b. 语义门·运行记录：machine/runs/ 非空却在 05 里查无痕迹 -> FAIL
    #
    # 起因（2026-08-18 实测）：KM_IDSystem 的 acceptance 涨到 78 条，它那份渲染器
    # 算出 run_limit = max(0, 77 - 78) = 0，36 条运行记录一条不渲染，
    # 标题写「最近 0 条」、空表下写「还没有运行记录」。三道门全绿：
    # 一致门比的是同一个渲染器的两次输出，体积门只数行数（100/100 正好卡满）。
    #
    # 判据是「说得出真相」而不是「必须展示」：05 有 100 行硬预算，装不下是常态；
    # 但装不下就得说装不下。所以下面两者满足其一即通过 ——
    #   ① 最新一条的 run_id 出现在 05 里；或
    #   ② 05 里出现真实总条数（例：「另有 36 条因 05 行数预算未展示」）。
    # 两者都没有 = 静默省略，等于文档在骗人。
    runs_dir = proj / "machine" / "runs"
    exec_path = docs / "05_执行与验收.md"
    if runs_dir.is_dir() and exec_path.is_file():
        import json as _json
        import re as _re
        flat = []
        for rf in sorted(runs_dir.glob("*.json")):
            try:
                rdata = _json.loads(rf.read_text(encoding="utf-8"))
            except (ValueError, OSError):
                continue
            flat.extend(rdata if isinstance(rdata, list) else [rdata])
        if flat:
            exec_body = exec_path.read_text(encoding="utf-8")
            last = flat[-1] if isinstance(flat[-1], dict) else {}
            latest_run = str(last.get("run_id") or last.get("id") or "").strip()
            shows_latest = bool(latest_run) and latest_run in exec_body
            states_total = _re.search(r"\b%d\b\s*条" % len(flat), exec_body) is not None
            if not shows_latest and not states_total:
                failures.append(
                    f"[{name}] 语义门·运行记录: machine/runs/ 有 {len(flat)} 条，"
                    f"但 文档/05_执行与验收.md 既没有最新一条"
                    f"{'（' + latest_run + '）' if latest_run else ''}"
                    f"，也没有说明省略了多少条 —— 静默省略等于文档在骗人。"
                    f"装不下可以，说出来必须。")

    # 3. 三道门
    for tool, arg in [("check_doc_budget.py", ["--docs", "文档"]),
                      ("check_blocker_stop.py", ["--machine", "machine"])]:
        rr = subprocess.run(
            [sys.executable, tool_path(proj, tool)] + arg,
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
