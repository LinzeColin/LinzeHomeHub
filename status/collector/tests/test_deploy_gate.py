"""Run deployment content checks locally with fixture pages and fake container commands."""
import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path


STATUS = Path(__file__).resolve().parents[2]
GATE = STATUS / "deploy" / "control-plane" / "ci-deploy-gate.sh"
WEB = STATUS / "web"
BUILD = r'(<meta name="km-build" content=")[^"]+(">)'


class DeployGateTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.fixture = Path(temporary.name)
        self.index = (WEB / "index.html").read_text(encoding="utf-8")
        self.add_command("docker", """
case "$1" in
    restart) exit 0 ;;
    inspect) printf '%s\n' '127.0.0.1' ;;
    *) exit 1 ;;
esac
""")
        self.add_command("curl", """
if [ "$1" = "-sf" ]; then exit 0; fi
for url do :; done
case "$url" in
    http://127.0.0.1/|http://127.0.0.1/agent-governance.html) cat "$STATUS_INDEX" ;;
    http://127.0.0.1/legacy.html) cat "$STATUS_LEGACY" ;;
    *) exit 22 ;;
esac
""")
        for name in ("sleep", "logger"):
            self.add_command(name, "exit 0\n")

    def add_command(self, name, script):
        executable = self.fixture / name
        executable.write_text("#!/bin/sh\n" + script, encoding="utf-8")
        executable.chmod(0o755)

    def finalize(self, index):
        path = self.fixture / "index.html"
        path.write_text(index, encoding="utf-8")
        env = {
            **os.environ,
            "PATH": str(self.fixture) + os.pathsep + os.environ["PATH"],
            "SSH_ORIGINAL_COMMAND": "deploy-finalize",
            "STATUS_INDEX": str(path),
            "STATUS_LEGACY": str(WEB / "legacy.html"),
        }
        return subprocess.run(
            ["bash", str(GATE)], env=env, capture_output=True, text=True, timeout=8,
        )

    def test_gate_accepts_current_and_future_build_ids(self):
        future = re.sub(BUILD, r'\g<1>ZL00-271231-99\2', self.index)
        self.assertNotEqual(self.index, future)
        for index in (self.index, future):
            result = self.finalize(index)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("finalize_ok content_verified", result.stdout)

    def test_gate_rejects_missing_homepage_contract(self):
        cases = (
            (re.sub(BUILD, "", self.index), "首页编号不对"),
            (re.sub(BUILD, r'\g<1>OTHER-271231-99\2', self.index), "首页编号不对"),
            (self.index.replace('id="arch"', 'id="architecture"'), "首页缺少架构区块"),
            (self.index.replace("/data/arch.json", "/data/missing.json"), "首页缺少 arch 数据入口"),
            (self.index.replace("/data/history.json", "/data/missing.json"), "首页缺少 history 数据入口"),
        )
        for index, error in cases:
            with self.subTest(error=error):
                result = self.finalize(index)
                self.assertEqual(result.returncode, 76, result.stderr)
                self.assertIn(error, result.stderr)
