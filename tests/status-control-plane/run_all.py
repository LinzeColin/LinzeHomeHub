#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
from pathlib import Path
import subprocess
import sys


def run(command: list[str], cwd: Path, env: dict[str, str]) -> int:
    print("+", " ".join(command), flush=True)
    completed = subprocess.run(command, cwd=cwd, env=env, check=False, shell=False)
    return completed.returncode


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", default=".")
    parser.add_argument("--browser", action="store_true")
    args = parser.parse_args()
    here = Path(__file__).resolve().parent
    env = os.environ.copy()
    env["PYTHONPATH"] = str(here) + os.pathsep + env.get("PYTHONPATH", "")
    commands = [
        [sys.executable, "-m", "unittest", "discover", "-s", str(here / "unit"), "-p", "test_*.py"],
        [sys.executable, str(here / "governance_check.py"), "--repo", args.repo],
        [sys.executable, str(here / "policy_scan.py"), "--repo", args.repo],
        # 遗留平面的 shell 守卫(自愈看门狗的复探)。被测对象是真正跑在 root cron 里的
        # linze-selfheal.sh,所以必须真跑那个文件;需要 GNU stat/tac,脚本会在
        # 非 Linux 平台自行 SKIP 并返回 0 —— 本机跑不了不等于可以不跑,
        # 上线前的 VPS 那一轮必须看到 SELFHEAL_POST_PROBE_PASS。
        ["bash", str(here / "shell" / "test_selfheal_post_probe.sh")],
        ["bash", str(here / "shell" / "test_artifact_digest_stable.sh")],
    ]
    for command in commands:
        if run(command, here, env):
            return 1
    if args.browser:
        frontend = here / "frontend"
        if run(["npm", "install", "--ignore-scripts", "--no-audit", "--no-fund"], frontend, env):
            return 1
        if run(["npx", "playwright", "install", "--with-deps", "chromium"], frontend, env):
            return 1
        if run(["npx", "playwright", "test"], frontend, env):
            return 1
    print("ALL_FROZEN_TESTS_PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
