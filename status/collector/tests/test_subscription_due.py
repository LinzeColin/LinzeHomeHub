"""订阅到期提醒 —— 守卫。

**实测出来的问题**：告警只检查 OVH 一条。7 条订阅覆盖 1 条。
`ChatGPT Pro20x` 续费日 2026-08-03、**剩 7 天**，页面上一点提示都没有 ——
因为它不是 OVH，而 OVH 那时还剩 21 天。

这是「口径只覆盖子集却当成全局」在这套系统里的第 N 次复发。
它比明显的 bug 危险，因为产物看着完全正常：告警区干干净净，
而干干净净的原因是**根本没在看**。

★ 还有一个更隐蔽的洞：3 条订阅没登记购买日期 → 算不出到期日 → 永远不会告警。
  「算不出」被当成了「没事」。必须单独列成「盯不住」。
"""
import os
import sys
import unittest
from datetime import timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import collect as C  # noqa: E402


def _item(name, days_from_now, auto=False):
    """造一条「距今 N 天到期」的订阅（按月）。"""
    if days_from_now is None:
        return {"name": name, "cadence": "monthly", "auto_renew": auto,
                "renew_date": None, "renew_days": None}
    d = (C.now_cn() + timedelta(days=days_from_now)).strftime("%Y-%m-%d")
    return {"name": name, "cadence": "monthly", "auto_renew": auto,
            "renew_date": d, "renew_days": days_from_now}


def build(items):
    """★ 直接调**真实实现**。

    第一版这里自己复制了一份口径 —— 结果负控（把盯不住的那批从分母里剔掉）
    改的是 collect.py，测试动都不动，全绿。**测自己写的副本等于没测。**
    """
    return C.subscription_ledger({"items": items})


class ThresholdTest(unittest.TestCase):
    def test_seven_days_is_red(self):
        """★ owner 的口径：提前 7 天变红。7 天当天就必须是红。"""
        s = build([_item("X", 7)])
        self.assertEqual(s["items"][0]["level"], "bad",
                         "剩 7 天没有变红 —— 这正是 ChatGPT Pro20x 那条漏掉的原因")

    def test_inside_seven_days_is_red(self):
        for d in (0, 1, 3, 6):
            self.assertEqual(build([_item("X", d)])["items"][0]["level"], "bad")

    def test_already_expired_is_red(self):
        self.assertEqual(build([_item("X", -2)])["items"][0]["level"], "bad")

    def test_eight_days_is_not_red_yet(self):
        """边界另一侧也要钉住，否则「全都红」和「按时红」看不出区别。"""
        self.assertEqual(build([_item("X", 8)])["items"][0]["level"], "warn")

    def test_far_away_is_ok(self):
        self.assertEqual(build([_item("X", 90)])["items"][0]["level"], "ok")


class CoverageTest(unittest.TestCase):
    """★ 这一组是这个文件存在的真正理由。"""

    def test_every_subscription_is_checked_not_just_ovh(self):
        s = build([_item("OVH VPS-1", 21), _item("ChatGPT Pro20x", 7),
                   _item("Claude Max20x", 18), _item("域名 linzezhang.com", 316)])
        names = [x["name"] for x in s["due_soon"]]
        self.assertIn("ChatGPT Pro20x", names,
                      "非 OVH 的订阅到期没有被发现 —— 覆盖面又缩回子集了")
        self.assertEqual(s["tracked"], 4, "分母必须是全部订阅，不是其中一条")

    def test_untrackable_items_get_their_own_ledger(self):
        """算不出到期日 ≠ 没事。必须单列，否则永远不会有人发现它盯不住。"""
        s = build([_item("OVH", 21), _item("NitroSend", None),
                   _item("Cloudflare / GitHub", None), _item("OCI 离机备份", None)])
        self.assertEqual(len(s["blind"]), 3)
        self.assertEqual(s["total"], 4, "盯不住的也要进总数，不能从分母里消失")
        self.assertEqual(s["tracked"], 1)
        self.assertNotEqual(s["tracked"], s["total"],
                            "盯得住的数量等于总数 = 盯不住的那批被吞了")

    def test_sorted_by_urgency(self):
        s = build([_item("A", 30), _item("B", 3), _item("C", 12)])
        self.assertEqual([x["name"] for x in s["items"]], ["B", "C", "A"])


class WiringTest(unittest.TestCase):
    def test_collector_publishes_subs_block(self):
        import inspect
        src = inspect.getsource(C)
        self.assertIn("subs = subscription_ledger(costblk)", src, "口径函数没有被真正调用")
        self.assertIn('"subs": subs', src, "全量订阅账没有进快照，页面拿不到")

    def test_page_really_raises_a_red_alert_for_a_non_ovh_item(self):
        """★ 行为测试，不是文本匹配。

        上一版这里只断言「源码里没有 'OVH 即将续费' 这个字符串」——
        负控把过滤条件改成 `x.name==='OVH VPS-1'` 之后，字符串确实还是不在，
        测试照样全绿。**测字符串等于没测。**
        现在把页面那段告警代码抠出来，在 node 里喂一份假快照真跑一遍。
        """
        import json
        import re
        import shutil
        import subprocess
        if not shutil.which("node"):
            self.skipTest("没有 node，跳过页面行为测试")
        web = os.path.join(os.path.dirname(os.path.dirname(
            os.path.dirname(os.path.abspath(__file__)))), "web", "index.html")
        src = open(web, encoding="utf-8").read()
        m = re.search(r"(const sb=o\.subs\|\|\{\};.*?没登记购买日期，到期前不会有任何提醒'\);)",
                      src, re.S)
        self.assertIsNotNone(m, "页面里找不到订阅告警那一段")
        snap = {"subs": {"items": [
            {"name": "OVH VPS-1", "date": "2026-08-17", "days": 21,
             "auto_renew": False, "level": "ok"},
            {"name": "ChatGPT Pro20x", "date": "2026-08-03", "days": 7,
             "auto_renew": True, "level": "bad"}],
            "blind": [{"name": "NitroSend"}]}}
        js = ("const out=[];const push=(l,t,d)=>out.push({l,t,d});"
              "const o=" + json.dumps(snap, ensure_ascii=False) + ";"
              + m.group(1) + "console.log(JSON.stringify(out));")
        r = subprocess.run(["node", "-e", js], capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr[:300])
        alerts = json.loads(r.stdout)
        red = [a for a in alerts if a["l"] == "bad"]
        self.assertTrue(any("ChatGPT" in a["t"] for a in red),
                        "非 OVH 的订阅剩 7 天，页面没有报红 —— 就是 owner 说的「我怎么还是没看到」")
        self.assertTrue(any("盯不住" in a["t"] for a in alerts),
                        "算不出到期日的那批没有被单独提醒")
        self.assertFalse(any("OVH" in a["t"] for a in alerts),
                         "OVH 还剩 21 天，不该出现在告警里（否则是每条都报=等于没报）")


if __name__ == "__main__":
    unittest.main()
