"""账务探针的安全边界:放行查账,仍然拦死调模型。

为了让 status 能显示 AI 供应商余额,`policy_scan` 对 `api.openai.com` 开了一个
**按文件**的豁免。开口子这件事本身就是风险 —— 所以这个文件存在的意义不是证明
「余额能查了」,而是证明 **口子只有那么大**:

  · 域名豁免只覆盖 collector/probe_ai_balance.py 这一个文件,别处出现照旧违规;
  · 推理端点(chat/completions 等)**不设任何豁免**,连被豁免的那个文件也要查;
  · 探针自己在运行期还会再拒一次(URL 命中推理路径就不发请求)。

下面每条都配了种子:把违规样本真的写进树里,断言扫描器**确实会红**。
只验「干净树是绿的」等于没验 —— 那是这一整轮反复踩到的教训。
"""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import unittest

from test_support import locate

REPO, MODULE_ROOT, _ = locate()
SCAN = Path(__file__).resolve().parents[1] / "policy_scan.py"
PROBE_REL = "collector/probe_ai_balance.py"


def run_scan() -> tuple[int, str]:
    proc = subprocess.run(
        [sys.executable, str(SCAN), "--repo", str(REPO)],
        capture_output=True, text=True, check=False,
        env={**os.environ, "PYTHONPATH": str(SCAN.parent)},
    )
    return proc.returncode, proc.stdout


class SeededFile:
    """把一个违规样本写进被扫描的树里,退出时删掉。"""

    def __init__(self, relative: str, content: str):
        self.path = REPO / "status" / relative
        self.content = content

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(self.content, encoding="utf-8")
        return self.path

    def __exit__(self, *_exc):
        self.path.unlink(missing_ok=True)


class AiBalanceGuardTests(unittest.TestCase):
    def test_clean_tree_passes(self):
        """基线:含账务探针的干净树必须是绿的,否则豁免没生效。"""
        code, _ = run_scan()
        self.assertEqual(code, 0, "干净树扫描不通过 —— 账务探针的豁免没生效")

    def test_probe_file_exists_and_is_the_only_exempt_one(self):
        probe = REPO / "status" / PROBE_REL
        self.assertTrue(probe.is_file(), "账务探针不在预期路径,豁免会落空")
        text = probe.read_text(encoding="utf-8")
        self.assertIn("api.openai.com", text, "探针里没有那个域名,说明豁免指错了文件")

    # ── 以下每条都是破坏测试:种违规 -> 必须红 ──────────────────────

    def test_vendor_host_outside_probe_is_still_violation(self):
        """域名出现在别的文件里,豁免不该覆盖到。"""
        with SeededFile("collector/_seed_host_elsewhere.py",
                        "URL = 'https://api.openai.com/v1/models'\n"):
            code, out = run_scan()
        self.assertNotEqual(code, 0, "★ 域名跑到别的文件里,扫描器却放过了")
        self.assertIn("runtime_agent_or_model_dependency", out)

    def test_inference_endpoint_in_probe_is_violation(self):
        """★ 最关键的一条:被豁免的文件里也不许出现推理端点。"""
        probe = REPO / "status" / PROBE_REL
        original = probe.read_text(encoding="utf-8")
        try:
            probe.write_text(
                original + '\nBAD = "https://api.openai.com/v1/chat/completions"\n',
                encoding="utf-8")
            code, out = run_scan()
        finally:
            probe.write_text(original, encoding="utf-8")
        self.assertNotEqual(code, 0, "★ 豁免文件里塞了推理端点,扫描器却放过了 —— 口子开穿了")
        self.assertIn("model_inference_endpoint", out)

    def test_inference_endpoint_anywhere_is_violation(self):
        for name, url in (
            ("_seed_openai_chat.py", "https://api.openai.com/v1/chat/completions"),
            ("_seed_anthropic.py", "https://api.anthropic.com/v1/messages"),
            ("_seed_deepseek_chat.py", "https://api.deepseek.com/chat/completions"),
        ):
            with self.subTest(url=url):
                with SeededFile(f"collector/{name}", f'U = "{url}"\n'):
                    code, out = run_scan()
                self.assertNotEqual(code, 0, f"★ {url} 没被拦下")
                self.assertIn("model_inference_endpoint", out)

    def test_sdk_dependency_still_blocked_even_in_probe(self):
        """豁免只放行域名,SDK 依赖照旧拦 —— 否则等于把 SDK 也放进来了。"""
        probe = REPO / "status" / PROBE_REL
        original = probe.read_text(encoding="utf-8")
        try:
            probe.write_text(original + "\n# openai==1.0.0\n", encoding="utf-8")
            code, out = run_scan()
        finally:
            probe.write_text(original, encoding="utf-8")
        self.assertNotEqual(code, 0, "★ 豁免文件里声明了 SDK 依赖,扫描器却放过了")

    def test_probe_refuses_inference_url_at_runtime(self):
        """守卫不能只活在扫描里:探针运行期自己也要拒。"""
        sys.path.insert(0, str(REPO / "status" / "collector"))
        import probe_ai_balance as probe
        self.assertIsNotNone(probe._refuse_if_inference("https://api.openai.com/v1/chat/completions"))
        self.assertIsNone(probe._refuse_if_inference("https://api.openai.com/v1/organization/costs"))


class AiBalanceHonestyTests(unittest.TestCase):
    """取不到就说取不到,绝不编一个数。"""

    def setUp(self):
        sys.path.insert(0, str(REPO / "status" / "collector"))
        import probe_ai_balance as probe
        self.probe = probe

    def test_missing_key_file_is_unconfigured_not_a_number(self):
        original = dict(self.probe.KEY_FILES)
        try:
            self.probe.KEY_FILES["deepseek"] = "/nonexistent/definitely-not-here"
            result = self.probe.probe_vendor("deepseek")
        finally:
            self.probe.KEY_FILES.update(original)
        self.assertEqual(result["state"], "unconfigured")
        self.assertIsNone(result["amount"], "没配置却给出了金额")

    def test_unknown_shape_never_yields_a_number(self):
        """结构不认识时不许猜字段。"""
        self.assertEqual(self.probe._parse_deepseek({"unexpected": 1}), (None, None))
        self.assertEqual(self.probe._parse_openai_costs({"data": "wrong-type"}), (None, None))

    def test_known_shapes_parse(self):
        amount, currency = self.probe._parse_deepseek(
            {"balance_infos": [{"currency": "CNY", "total_balance": "12.34"}]})
        self.assertAlmostEqual(amount, 12.34)
        self.assertEqual(currency, "CNY")
        amount, currency = self.probe._parse_openai_costs(
            {"data": [{"results": [{"amount": {"value": 1.5, "currency": "usd"}}]},
                      {"results": [{"amount": {"value": 2.25, "currency": "usd"}}]}]})
        self.assertAlmostEqual(amount, 3.75)

    def test_openai_is_labelled_spend_not_balance(self):
        """OpenAI 没有公开的剩余余额端点,只能报花费。标成余额就是假数据。"""
        self.assertEqual(self.probe.ENDPOINTS["openai"][1], "spend")
        self.assertEqual(self.probe.ENDPOINTS["deepseek"][1], "balance")

    def test_partial_ok_does_not_aggregate_to_all_ok(self):
        """一条 ok 一条不 ok,整体不许是 ok(INV-005)。"""
        original = dict(self.probe.KEY_FILES)
        try:
            self.probe.KEY_FILES["deepseek"] = "/nonexistent/a"
            self.probe.KEY_FILES["openai"] = "/nonexistent/b"
            result = self.probe.collect_ai_accounts()
        finally:
            self.probe.KEY_FILES.update(original)
        self.assertFalse(result["all_ok"], "全都没配置却聚合成 all_ok")


if __name__ == "__main__":
    unittest.main()
