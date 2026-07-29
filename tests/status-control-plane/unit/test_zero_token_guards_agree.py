"""两个零 Token 守卫必须对同一份输入给出**同一个结论**。

本仓有两把守卫在管同一条规则(运行期不得调模型):

  tests/status-control-plane/policy_scan.py   —— Python,精确策略,账务探针按文件豁免域名
  scripts/validate-homehub.mjs                —— JS,AGENTS.md 里 `npm run validate` 的一部分

在此之前它们对 `status/collector/probe_ai_balance.py` 给出**相反**结论:policy_scan 认定合规
(查账单不是调模型),validate-homehub 整域名封杀判它违规。于是 `npm run validate` 长期红着,
红的还是一个被另一把守卫认定为合规的文件。

两把守卫互相矛盾,后果不是「多了一层保护」而是**人开始忽略其中一把** —— 那比少一把更糟,
因为忽略一把的同时也就忽略了它本来能抓到的真问题。

这条测试不断言某个正则长什么样,而是把同样的违规样本喂给两把守卫,断言它们**同意**。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from test_support import locate
REPO, _, _ = locate()

POLICY_SCAN = REPO / "tests" / "status-control-plane" / "policy_scan.py"
VALIDATE_JS = REPO / "scripts" / "validate-homehub.mjs"
PROBE = "status/collector/probe_ai_balance.py"

#: (说明, 相对路径, 内容, 两把守卫都应判违规?)
CASES = [
    ("干净树", None, None, False),
    ("厂商域名出现在别的文件里", "status/collector/_seed_vendor.py",
     'URL = "https://api.openai.com/v1/models"\n', True),
    ("推理端点出现在别的文件里", "status/collector/_seed_inference.py",
     'U = "https://api.openai.com/v1/chat/completions"\n', True),
    ("推理端点出现在**被豁免的**账务探针里", PROBE,
     '\nBAD = "https://api.openai.com/v1/chat/completions"\n', True),
]


def _node() -> str | None:
    return shutil.which("node")


class ZeroTokenGuardParityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if _node() is None:
            raise unittest.SkipTest("本环境没有 node,跑不了 validate-homehub.mjs")
        cls.probe_original = (REPO / PROBE).read_text(encoding="utf-8")

    def tearDown(self):
        (REPO / PROBE).write_text(self.probe_original, encoding="utf-8")

    def _python_guard_flags_violation(self) -> bool:
        proc = subprocess.run(
            [sys.executable, str(POLICY_SCAN), "--repo", str(REPO)],
            capture_output=True, text=True, check=False,
            env={**os.environ, "PYTHONPATH": str(POLICY_SCAN.parent)})
        return proc.returncode != 0

    def _js_guard_flags_zero_token_violation(self) -> bool:
        proc = subprocess.run([_node(), str(VALIDATE_JS)],
                              cwd=REPO, capture_output=True, text=True, check=False)
        return "零Token守卫" in (proc.stdout + proc.stderr)

    def test_both_guards_exist(self):
        self.assertTrue(POLICY_SCAN.is_file())
        self.assertTrue(VALIDATE_JS.is_file())

    def test_the_two_guards_agree_on_every_case(self):
        for label, relative, content, expected in CASES:
            with self.subTest(case=label):
                seeded = None
                try:
                    if relative == PROBE:
                        (REPO / PROBE).write_text(self.probe_original + content, encoding="utf-8")
                    elif relative:
                        seeded = REPO / relative
                        seeded.write_text(content, encoding="utf-8")
                    py = self._python_guard_flags_violation()
                    js = self._js_guard_flags_zero_token_violation()
                    self.assertEqual(
                        py, js,
                        f"两把守卫对「{label}」结论不一致:policy_scan={py} validate-homehub={js}")
                    self.assertEqual(py, expected, f"「{label}」的判定与预期不符")
                finally:
                    if seeded and seeded.exists():
                        seeded.unlink()
                    (REPO / PROBE).write_text(self.probe_original, encoding="utf-8")

    def test_the_exemption_is_file_scoped_and_documented_in_both(self):
        """放宽守卫时必须写清「放宽到哪为止」,而且两边写的是同一个文件。"""
        py = POLICY_SCAN.read_text(encoding="utf-8")
        js = VALIDATE_JS.read_text(encoding="utf-8")
        self.assertIn("probe_ai_balance.py", py)
        self.assertIn("probe_ai_balance.py", js)
        for text, who in ((py, "policy_scan.py"), (js, "validate-homehub.mjs")):
            self.assertIn("推理端点", text, f"{who} 没有写明推理端点不设豁免")


if __name__ == "__main__":
    unittest.main()
