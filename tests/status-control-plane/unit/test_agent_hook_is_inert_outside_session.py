"""全局 hook 在「不是受治理的 run」时必须完全惰性。

agent_hook 会被装成 provider 的 command hook,在**这台机器的每一次工具调用**上被拉起 ——
包括所有和 status 毫不相干的项目。原实现缺 STATUS_AGENT_RUN_ID 就直接抛 missing run_id,
装上去等于让本机每次工具调用都报一次错;而且它在判断之前就已经把 capture/redaction
导进来了,每次调用白花一次模块导入。

这条测试锁住两件事:
  1. 缺任意一个 session 绑定 -> 返回 0、一个字节都不写、不读 stdin
  2. 四个绑定齐了 -> 正常归一并落盘,且植入的秘密不出现在产物里
"""

from __future__ import annotations

import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

from test_support import locate
REPO, _, _ = locate()

sys.path.insert(0, str(REPO))
from status.controlplane.agent_hook import main, session_is_active, _SESSION_KEYS

FIXTURE = REPO / "tests" / "status-agent-governance" / "fixtures" / "claude_event.json"
FULL_SESSION = {
    "STATUS_AGENT_RUN_ID": "run-x",
    "STATUS_AGENT_TASK_ID": "task-x",
    "STATUS_AGENT_INTENT_HASH": "a" * 64,
    "STATUS_AGENT_SESSION_ID": "session-x",
}


class HookInertOutsideSessionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.output = Path(self.temp.name) / "events.jsonl"
        self.payload = FIXTURE.read_text(encoding="utf-8")

    def tearDown(self):
        self.temp.cleanup()

    def _run(self, env):
        with mock.patch.dict(os.environ, env, clear=True), \
             mock.patch.object(sys, "stdin", io.StringIO(self.payload)):
            return main(["--provider", "claude", "--output", str(self.output)])

    def test_no_session_at_all_is_a_silent_noop(self):
        self.assertEqual(self._run({}), 0)
        self.assertFalse(self.output.exists(), "不在受治理 run 里却写了盘")

    def test_each_single_missing_binding_still_noops(self):
        for key in _SESSION_KEYS:
            with self.subTest(missing=key):
                env = dict(FULL_SESSION)
                env.pop(key)
                self.assertEqual(self._run(env), 0)
                self.assertFalse(self.output.exists(), f"缺 {key} 却仍然写了盘")

    def test_blank_binding_counts_as_missing(self):
        env = dict(FULL_SESSION, STATUS_AGENT_RUN_ID="   ")
        self.assertFalse(session_is_active(env))
        self.assertEqual(self._run(env), 0)
        self.assertFalse(self.output.exists())

    def test_full_session_captures_and_redacts(self):
        self.assertEqual(self._run(FULL_SESSION), 0)
        self.assertTrue(self.output.exists(), "受治理 run 里却没有落盘")
        lines = self.output.read_text(encoding="utf-8").strip().splitlines()
        self.assertEqual(len(lines), 1, "一次事件必须只写一行")
        event = json.loads(lines[0])
        self.assertEqual(event["provider"], "claude")
        self.assertEqual(event["run_id"], "run-x")
        self.assertEqual(event["adapter_state"], "NORMALIZED_REDACTED")
        self.assertNotIn("secret-value-for-redaction", lines[0], "植入的秘密泄漏到了产物里")

    def test_capture_is_not_imported_on_the_noop_path(self):
        """惰性不只是"不报错",还得"不白花开销" —— 每次工具调用都多一次导入是实打实的成本。"""
        source = (REPO / "status" / "controlplane" / "agent_hook.py").read_text(encoding="utf-8")
        head, _, tail = source.partition("def main(")
        self.assertNotIn("from .capture import", head,
                         "capture 仍在模块顶层导入,惰性路径省不下这次开销")
        self.assertIn("from .capture import normalize_event", tail,
                      "capture 应当在确认处于受治理 run 之后才导入")


if __name__ == "__main__":
    unittest.main()
