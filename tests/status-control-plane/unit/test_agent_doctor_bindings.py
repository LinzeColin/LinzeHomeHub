"""doctor 的 `state: PASS` 只代表「这台机器能跑控制面」,不代表备份链路可用。

实测过的误读风险:`state=PASS` 与 `commands.rclone=false` 同时出现,而报告里
根本没有 R2/OCI remote 这一项 —— T00-05 要求「binaries、paths 和 **remotes** 都显式」,
少了 remotes 就等于把「没配」读成了「没问题」。

这里锁住:三缺一都必须落到 ENVIRONMENT_BLOCKED,且报告里只出现键名与布尔,
绝不出现 remote 的值(Stage 0 只采集键名、路径和可验证结果)。
"""

from __future__ import annotations

import os
import unittest
from unittest import mock

from test_support import locate
locate()

from controlplane.agent_cli import _backup_transport_state

READY_ENV = {
    "LINZE_R2_CRYPT_REMOTE": "r2crypt:",
    "LINZE_OCI_CRYPT_REMOTE": "ocicrypt:",
    "RCLONE_CRYPT_REMOTE_CONFIRMED": "1",
}
ALL_COMMANDS = {"python3": True, "systemctl": True, "rclone": True, "gh": True}


class BackupTransportBindingTests(unittest.TestCase):
    def test_fully_bound_environment_is_ready(self):
        with mock.patch.dict(os.environ, READY_ENV, clear=True):
            state = _backup_transport_state(ALL_COMMANDS)
        self.assertEqual(state["state"], "READY")
        self.assertEqual(state["missing"], [])

    def test_each_missing_binding_blocks_on_its_own(self):
        cases = {
            "LINZE_R2_CRYPT_REMOTE": (dict(READY_ENV), ALL_COMMANDS, "LINZE_R2_CRYPT_REMOTE"),
            "LINZE_OCI_CRYPT_REMOTE": (dict(READY_ENV), ALL_COMMANDS, "LINZE_OCI_CRYPT_REMOTE"),
            "RCLONE_CRYPT_REMOTE_CONFIRMED=1": (dict(READY_ENV), ALL_COMMANDS, "RCLONE_CRYPT_REMOTE_CONFIRMED"),
            "rclone": (dict(READY_ENV), {**ALL_COMMANDS, "rclone": False}, None),
        }
        for expected_missing, (env, commands, drop_key) in cases.items():
            with self.subTest(missing=expected_missing):
                if drop_key:
                    env.pop(drop_key)
                with mock.patch.dict(os.environ, env, clear=True):
                    state = _backup_transport_state(commands)
                self.assertEqual(state["state"], "ENVIRONMENT_BLOCKED")
                self.assertIn(expected_missing, state["missing"])

    def test_unconfirmed_crypt_remote_is_not_treated_as_confirmed(self):
        """RCLONE_CRYPT_REMOTE_CONFIRMED 只有字面量 "1" 算数 —— 人工确认过才算加密。"""
        for value in ("0", "true", "yes", ""):
            with self.subTest(value=value):
                env = {**READY_ENV, "RCLONE_CRYPT_REMOTE_CONFIRMED": value}
                with mock.patch.dict(os.environ, env, clear=True):
                    state = _backup_transport_state(ALL_COMMANDS)
                self.assertFalse(state["crypt_remote_confirmed"])
                self.assertEqual(state["state"], "ENVIRONMENT_BLOCKED")

    def test_report_never_leaks_remote_values(self):
        with mock.patch.dict(os.environ, READY_ENV, clear=True):
            state = _backup_transport_state(ALL_COMMANDS)
        blob = repr(state)
        for secretish in ("r2crypt:", "ocicrypt:"):
            self.assertNotIn(secretish, blob, "remote 的值不该出现在 Stage 0 证据里")


if __name__ == "__main__":
    unittest.main()
