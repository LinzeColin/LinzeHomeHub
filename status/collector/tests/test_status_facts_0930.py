#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""2026-09-30 状态页与事实不符的几处纠正 —— 守卫。

每条对应一次亲眼看到的假红/矛盾:
  1. GitHub 备份滚动保留 30/30 被报成「接近额度」(满额是设计内常态)
  2. 已退役的订阅(VPS-1 / OCI)被算成「盯不住」—— cost() 构造 row 时把 retired 丢了
  3. VPS-3 同时说「10-09 即将扣费」和「2027-02-09 到期停机」
"""
import json
import os
import shutil
import subprocess
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import collect as C  # noqa: E402

WEB = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                   "web", "index.html")
FX = {"aud_cny": 4.7, "rates": {"AUD": 1.0, "USD": 0.66}}


def _cost(items):
    return C.cost({"items": items}, FX)


class RetiredTest(unittest.TestCase):
    def test_retired_survives_cost_and_leaves_ledger(self):
        blk = _cost([
            {"name": "OVH VPS-1", "amount": 0, "currency": "AUD", "cadence": "monthly",
             "purchase": "2026-07-17", "track_renew": False, "retired": "2026-08-10"},
            {"name": "OCI 离机备份", "amount": 0, "currency": "AUD", "cadence": "monthly",
             "retired": "2026-09-30"},
            {"name": "NitroSend", "amount": 0, "currency": "AUD", "cadence": "monthly"}])
        led = C.subscription_ledger(blk)
        self.assertEqual([x["name"] for x in led["retired"]], ["OVH VPS-1", "OCI 离机备份"])
        self.assertEqual([x["name"] for x in led["blind"]], ["NitroSend"],
                         "退役的不该再被报成盯不住;没退役又没日期的仍要报")
        self.assertEqual(led["total"], 1, "退役的不进分母")


class ServiceEndTest(unittest.TestCase):
    def test_service_end_replaces_guessed_monthly_renewal(self):
        blk = _cost([{"name": "OVH VPS-3", "amount": 20.1, "currency": "AUD", "cadence": "monthly",
                      "purchase": "2026-08-09", "track_renew": True, "service_end": "2027-02-09"}])
        row = blk["items"][0]
        self.assertEqual(row["renew_date"], "2027-02-09")
        self.assertTrue(row["end_only"])
        led = C.subscription_ledger(blk)
        self.assertEqual(led["items"][0]["date"], "2027-02-09")
        self.assertTrue(led["items"][0]["end_only"])

    def test_without_service_end_the_cycle_logic_is_unchanged(self):
        blk = _cost([{"name": "X", "amount": 1, "currency": "AUD", "cadence": "monthly",
                      "purchase": "2026-08-09", "track_renew": True}])
        self.assertNotIn("end_only", blk["items"][0])
        self.assertIsNotNone(blk["items"][0]["renew_date"])


@unittest.skipUnless(shutil.which("node"), "没有 node")
class PageInsightsTest(unittest.TestCase):
    """把页面里真实的 insights() 抠出来在 node 里跑。"""

    def run_insights(self, snap):
        src = open(WEB, encoding="utf-8").read()
        i = src.index("function insights(s){")
        j = src.index("function healthScore(s){")
        js = (src[i:j] + "\nconst s=" + json.dumps(snap, ensure_ascii=False)
              + ";console.log(JSON.stringify(insights(s)));")
        r = subprocess.run(["node", "-e", js], capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr[:300])
        return json.loads(r.stdout)

    def gh(self, age_h=3, code="201", used=30):
        now = 1_800_000_000
        return {"updated_epoch": now, "usage": [{
            "key": "github_backup", "label": "GitHub 备份(滚动保留)", "used": used, "limit": 30,
            "bounded": True, "latest_epoch": now - int(age_h * 3600), "max_age_h": 26,
            "last_upload_code": code}]}

    def test_full_rolling_retention_is_not_a_risk(self):
        out = self.run_insights(self.gh())
        self.assertFalse([i for i in out if "备份" in i["t"]], out)

    def test_stale_latest_backup_is_reported(self):
        out = self.run_insights(self.gh(age_h=40))
        self.assertTrue([i for i in out if i["lv"] == "bad" and "没更新" in i["t"]], out)

    def test_failed_upload_is_reported(self):
        out = self.run_insights(self.gh(code="403"))
        self.assertTrue([i for i in out if i["lv"] == "bad" and "上传失败" in i["t"]], out)

    def test_non_rolling_usage_still_alerts_at_80_percent(self):
        out = self.run_insights({"usage": [{"key": "r2", "label": "R2", "used": 9, "limit": 10}]})
        self.assertTrue([i for i in out if i["lv"] == "bad" and "接近额度" in i["t"]], out)

    def test_end_only_subscription_says_shutdown_not_charge(self):
        out = self.run_insights({"ops": {"subs": {"items": [
            {"name": "OVH VPS-3", "date": "2027-02-09", "days": 5, "auto_renew": False,
             "level": "bad", "end_only": True}], "blind": []}}})
        self.assertTrue([i for i in out if "即将到期停机" in i["t"]], out)
        self.assertFalse([i for i in out if "即将扣费" in i["t"]], out)


class OciGoneTest(unittest.TestCase):
    def test_no_oci_usage_or_card(self):
        self.assertFalse(hasattr(C, "oci_usage"))
        src = open(C.__file__, encoding="utf-8").read()
        self.assertNotIn('"key": "oci"', src)
        self.assertNotIn("v:oci", src)


if __name__ == "__main__":
    unittest.main()
