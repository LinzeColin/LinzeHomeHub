"""遗留平面(collect.py / index.html)的「不许假绿」守卫。

为什么单独开一个文件:冻结测试原本只覆盖 status/controlplane/ 这个**新增**的控制面,
而每分钟真正在生产跑、真正渲染给公众看的是 collect.py 与 web/index.html 这套**遗留平面**。
S6-T1 收敛时对照探测器逐条实测,发现三处「测试全绿、线上却是写死的绿」:

  1. externals() 里 NitroSend / OVH VPS-1 直接写 "ok": True —— 从来没探过;
  2. 供应商卡 OCI 写 "ok": True —— 那是条只写、读不回来的 PAR 通道,永远无法验证;
  3. index.html 渲染自愈规则时 `includes(state)?state:'ok'` —— 认不出的状态一律当绿。

这三条都命中冻结验收 INV-005「UNKNOWN/UNVERIFIED 等永不聚合成 PASS 或绿」。
下面每条守卫都配了破坏测试(test_negative_control_*):把修好的代码改回原样,
断言守卫**确实会红**。守卫本身抓不到自己被绕过,就等于没有守卫。
"""
from __future__ import annotations

import ast
import importlib.util
from pathlib import Path
import re
import unittest

from test_support import locate

REPO, _MODULE_ROOT, _CONTRACTS = locate()
COLLECT = REPO / "status" / "collector" / "collect.py"
INDEX = REPO / "status" / "web" / "index.html"


def load_collect():
    spec = importlib.util.spec_from_file_location("legacy_collect", COLLECT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def literal_ok_values(source: str, func_name: str) -> list[object]:
    """取出某个函数体内所有 `"ok": <字面量>` 的字面量值。

    用 AST 而不是正则:正则会把注释里、字符串里提到的 "ok": True 也算进去,
    那样守卫会因为我在注释里解释这件事而误报自己。
    """
    tree = ast.parse(source)
    found: list[object] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef) or node.name != func_name:
            continue
        for sub in ast.walk(node):
            if not isinstance(sub, ast.Dict):
                continue
            for key, value in zip(sub.keys, sub.values):
                if isinstance(key, ast.Constant) and key.value == "ok":
                    if isinstance(value, ast.Constant):
                        found.append(value.value)
    return found


class OvhSelfStateTests(unittest.TestCase):
    """主机状态必须从真读到的指标派生,读不到就是 None(未知),不是绿。"""

    def setUp(self):
        self.state = load_collect().ovh_self_state

    def test_metrics_missing_is_unknown_not_green(self):
        for host in (None, {}, {"uptime_days": None, "disk_pct": 10, "mem_pct": 10}):
            ok, note = self.state(host)
            self.assertIsNone(ok, f"读不到指标却给出了 {ok!r}:{host!r}")
            self.assertIn("未知", note, "未知状态必须在文字里说出来,不能只靠点的颜色")

    def test_healthy_host_is_green(self):
        ok, note = self.state({"uptime_days": 42, "load1": "0.30", "disk_pct": 61, "mem_pct": 55})
        self.assertIs(ok, True)
        self.assertIn("42", note)

    def test_resource_pressure_is_not_green(self):
        for disk, mem in ((96, 55), (61, 97), (99, 98)):
            ok, note = self.state({"uptime_days": 42, "load1": "0.3", "disk_pct": disk, "mem_pct": mem})
            self.assertIs(ok, False, f"磁盘 {disk}% 内存 {mem}% 仍判绿")
            self.assertIn("吃紧", note)

    def test_negative_control_hardcoded_true_would_be_caught(self):
        """破坏测试:把 ovh_self_state 改回「永远 True」,断言上面的断言会失败。"""
        def sabotaged(_host):
            return True, "主机在线"
        with self.assertRaises(AssertionError):
            ok, _note = sabotaged(None)
            self.assertIsNone(ok)


class ExternalsHaveNoHardcodedGreenTests(unittest.TestCase):
    """externals() 里不许再出现写死的 "ok": True。"""

    def setUp(self):
        self.source = COLLECT.read_text(encoding="utf-8")

    def test_no_literal_true_in_externals(self):
        values = literal_ok_values(self.source, "externals")
        self.assertNotIn(
            True, values,
            'externals() 里出现了写死的 "ok": True —— 未探测的服务必须是 None',
        )

    def test_unprobed_entry_is_none_and_says_so(self):
        entries = load_collect().externals()
        by_name = {e["name"]: e for e in entries}
        self.assertIn("NitroSend", by_name)
        self.assertIsNone(by_name["NitroSend"]["ok"])
        self.assertIn("未探测", by_name["NitroSend"]["note"])

    def test_negative_control_reintroduced_hardcoded_green_is_caught(self):
        """破坏测试:把写死的绿塞回 externals(),守卫必须抓到。"""
        sabotaged = self.source.replace(
            '{"name": "NitroSend", "ok": None,',
            '{"name": "NitroSend", "ok": True,',
            1,
        )
        self.assertNotEqual(sabotaged, self.source, "破坏没生效,这条破坏测试本身是坏的")
        values = literal_ok_values(sabotaged, "externals")
        self.assertIn(True, values, "把 True 塞回去之后守卫却没看见 —— 守卫是装饰品")


class OciCardIsNeverGreenTests(unittest.TestCase):
    """OCI 是单向 PAR:结构上读不回来,所以它的状态**永远**不能是绿。"""

    def setUp(self):
        self.source = COLLECT.read_text(encoding="utf-8")

    def _oci_ok_literal(self, source: str):
        """定位 OCI 卡片那个 dict 的 status.ok 字面量。"""
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Dict):
                continue
            keys = {k.value for k in node.keys if isinstance(k, ast.Constant)}
            if "key" not in keys:
                continue
            pairs = dict(zip(
                [k.value if isinstance(k, ast.Constant) else None for k in node.keys],
                node.values,
            ))
            key_node = pairs.get("key")
            if not (isinstance(key_node, ast.Constant) and key_node.value == "oci"):
                continue
            status = pairs.get("status")
            if isinstance(status, ast.Dict):
                for k, v in zip(status.keys, status.values):
                    if isinstance(k, ast.Constant) and k.value == "ok" and isinstance(v, ast.Constant):
                        return v.value, True
            return None, True
        return None, False

    def test_oci_status_is_not_true(self):
        value, found = self._oci_ok_literal(self.source)
        self.assertTrue(found, "没找到 OCI 供应商卡 —— 守卫失去了目标,当作失败处理")
        self.assertIsNot(value, True, "OCI 单向通道被写成绿:投递成功不等于可恢复(OP-003)")

    def test_negative_control_green_oci_is_caught(self):
        sabotaged = self.source.replace(
            '"status": {"ok": None, "note": "单向投递 · 读不回来,无法验证可恢复"}',
            '"status": {"ok": True, "note": "离机副本 · 只写保险柜"}',
            1,
        )
        self.assertNotEqual(sabotaged, self.source, "破坏没生效,这条破坏测试本身是坏的")
        value, found = self._oci_ok_literal(sabotaged)
        self.assertTrue(found)
        self.assertIs(value, True, "OCI 被改回绿,守卫却没抓到")


class FrontendUnknownStateFallsBackSafeTests(unittest.TestCase):
    """前端认不出的状态必须落到「不确定」,绝不能落到 'ok'。"""

    FALLBACK = re.compile(r"KNOWN\.includes\(r\.state\)\s*\?\s*r\.state\s*:\s*'([a-z]+)'")

    def setUp(self):
        self.source = INDEX.read_text(encoding="utf-8")

    def test_fallback_is_not_green(self):
        match = self.FALLBACK.search(self.source)
        self.assertIsNotNone(match, "找不到自愈规则的状态兜底分支 —— 结构变了,守卫需要跟着改")
        self.assertNotEqual(match.group(1), "ok", "未知状态兜底成绿,这正是 INV-005 禁止的")
        self.assertEqual(match.group(1), "unknown")

    def test_failed_state_has_visible_style_and_words(self):
        self.assertIn(".rule .ic.failed{", self.source, "failed 没有样式会渲染成透明的点 —— 看不见")
        self.assertIn(".rule .ic.unknown{", self.source)
        self.assertIn("未恢复", self.source, "状态必须有文字,不能只靠颜色")

    def test_negative_control_fallback_to_ok_is_caught(self):
        sabotaged = self.source.replace(
            "KNOWN.includes(r.state)?r.state:'unknown'",
            "KNOWN.includes(r.state)?r.state:'ok'",
            1,
        )
        self.assertNotEqual(sabotaged, self.source, "破坏没生效,这条破坏测试本身是坏的")
        match = self.FALLBACK.search(sabotaged)
        self.assertIsNotNone(match)
        self.assertEqual(match.group(1), "ok", "兜底被改回绿,守卫却没抓到")


if __name__ == "__main__":
    unittest.main()
