#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
install_dual_plane.py —— 把双平面七文件架构装进一个项目

幂等：重复运行不覆盖已有的 machine/facts 事实源，绝不碰手写区
(文档/01_产品需求.md、文档/03_口径字典.md)。只补齐缺失的骨架。

装什么：
  machine/tools/          render_human.py + 三道门校验器（从本 kit 复制）
  machine/facts/          事实源目录（空则建，附 .gitkeep）
  machine/runs/           运行记录目录
  machine/legacy/         旧人类可读文件的归档位（README/功能清单/开发记录等移入）
  文档/                    渲染 7 文件（缺失事实源 -> UNKNOWN，诚实显示未接通）

用法:
  python3 install_dual_plane.py --project <项目目录> [--kit <kit目录>] [--archive-legacy]
  --archive-legacy  把项目根现有的旧人类可读 .md（功能清单/开发记录/模型参数文件/
                    HANDOFF 等）移入 machine/legacy/，只保留双平面七文件。
                    README.md / AGENTS.md / CHANGELOG.md 保留在根（工具/平台约定文件）。
退出码: 0=成功
"""
import argparse
import shutil
import subprocess
import sys
from pathlib import Path

TOOLS = ["render_human.py", "check_doc_budget.py", "check_blocker_stop.py", "check_dual_plane_ci.py"]

# 被双平面取代的旧人类可读文件（--archive-legacy 时移入 machine/legacy/）
LEGACY_HUMAN = ["功能清单.md", "开发记录.md", "模型参数文件.md", "模型参数.md", "HANDOFF.md"]
# 这些留在根：README 是仓库门面，AGENTS 是 agent 契约，CHANGELOG 是平台约定
KEEP_AT_ROOT = ["README.md", "AGENTS.md", "CHANGELOG.md", "LICENSE", "VERSION"]


def copy_tools(project: Path, kit: Path):
    dst = project / "machine" / "tools"
    dst.mkdir(parents=True, exist_ok=True)
    for t in TOOLS + ["install_dual_plane.py"]:
        src = kit / "machine" / "tools" / t
        if src.is_file():
            shutil.copy2(src, dst / t)


def ensure_dirs(project: Path):
    for d in ["machine/facts", "machine/runs", "machine/legacy"]:
        p = project / d
        p.mkdir(parents=True, exist_ok=True)
        keep = p / ".gitkeep"
        if not any(p.iterdir()):
            keep.write_text("", encoding="utf-8")


def archive_legacy(project: Path, moved: list):
    legacy = project / "machine" / "legacy"
    legacy.mkdir(parents=True, exist_ok=True)
    for name in LEGACY_HUMAN:
        f = project / name
        if f.is_file():
            shutil.move(str(f), str(legacy / name))
            moved.append(name)


def render(project: Path):
    r = subprocess.run(
        [sys.executable, "machine/tools/render_human.py", "--root", "."],
        cwd=project, capture_output=True, text=True,
    )
    return r.stdout.strip() + (("\n" + r.stderr.strip()) if r.stderr.strip() else "")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", required=True)
    ap.add_argument("--kit", default=str(Path(__file__).resolve().parents[2]))
    ap.add_argument("--archive-legacy", action="store_true")
    args = ap.parse_args()

    project = Path(args.project).resolve()
    kit = Path(args.kit).resolve()
    if not project.is_dir():
        print(f"FAIL: 项目目录不存在 {project}")
        return 1

    copy_tools(project, kit)
    ensure_dirs(project)

    moved = []
    if args.archive_legacy:
        archive_legacy(project, moved)

    out = render(project)

    print(f"✅ 双平面已装入 {project.name}")
    if moved:
        print(f"   旧人类可读文件已归档到 machine/legacy/: {', '.join(moved)}")
    print("   " + out.replace("\n", "\n   "))
    return 0


if __name__ == "__main__":
    sys.exit(main())
