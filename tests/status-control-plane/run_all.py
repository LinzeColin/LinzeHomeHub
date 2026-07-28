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
