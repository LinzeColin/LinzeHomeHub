"""线上 systemd 单元的 ExecStart 必须真的能被 CLI 的 argparse 接受。

实测踩到的坑:v0.0.0.2 把 authority 换成 no-clone 合同时,`sync-authority` 去掉了
`--push`,但 `linze-status-authority-sync.service` 还在传 —— argparse 直接
`error: unrecognized arguments: --push` 退出 2,那个 timer 每次触发都静默失败。
单元测试全绿、CLI 自己也能跑,坏的只有「谁去调它」这一层。

所以这里不去断言某一个已知参数,而是把**仓内所有单元的 ExecStart 全量喂给真正的
parser**:以后再有人增删参数而忘了改调用方,这条会红。
"""

from __future__ import annotations

from pathlib import Path
import shlex
import unittest

from test_support import locate
REPO, _, _ = locate()

from controlplane import agent_cli, cli

SYSTEMD_DIR = REPO / "status" / "deploy" / "systemd"
# `python3 -m <module>` -> 提供 parser() 的模块
MODULE_PARSERS = {
    "controlplane": cli.parser,
    "controlplane.cli": cli.parser,
    "controlplane.agent_cli": agent_cli.parser,
}


def _python_module_invocations():
    """从所有 .service 里挑出 `python3 -m <module> ...` 形式的 ExecStart。"""
    for unit in sorted(SYSTEMD_DIR.glob("*.service")):
        for raw in unit.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line.startswith("ExecStart="):
                continue
            tokens = shlex.split(line[len("ExecStart="):])
            if "-m" not in tokens:
                continue  # 调的是 shell 脚本,不归这条测试管
            module_index = tokens.index("-m") + 1
            if module_index >= len(tokens):
                continue
            yield unit.name, tokens[module_index], tokens[module_index + 1:]


class SystemdExecStartTests(unittest.TestCase):
    def test_systemd_dir_exists_and_has_units(self):
        self.assertTrue(SYSTEMD_DIR.is_dir(), f"找不到 {SYSTEMD_DIR}")
        self.assertTrue(list(SYSTEMD_DIR.glob("*.service")), "一个 .service 都没有,这条测试就是空转")

    def test_every_python_execstart_parses(self):
        invocations = list(_python_module_invocations())
        self.assertTrue(invocations, "没有任何 `python3 -m` 形式的 ExecStart —— 断言失去意义")
        for unit_name, module, args in invocations:
            with self.subTest(unit=unit_name, module=module):
                self.assertIn(module, MODULE_PARSERS, f"{unit_name} 调了未知模块 {module}")
                try:
                    MODULE_PARSERS[module]().parse_args(args)
                except SystemExit as exc:
                    self.fail(f"{unit_name} 的 ExecStart 参数被 {module} 拒绝(exit={exc.code}):{' '.join(args)}")

    def test_removed_push_flag_is_really_gone_from_units(self):
        """no-clone 合同下没有可 push 的东西;任何单元再带 --push 都是回归。"""
        for unit in sorted(SYSTEMD_DIR.glob("*.service")):
            for raw in unit.read_text(encoding="utf-8").splitlines():
                line = raw.strip()
                if line.startswith("ExecStart=") and "sync-authority" in line:
                    self.assertNotIn("--push", shlex.split(line[len("ExecStart="):]),
                                     f"{unit.name} 仍在给 sync-authority 传 --push")


if __name__ == "__main__":
    unittest.main()
