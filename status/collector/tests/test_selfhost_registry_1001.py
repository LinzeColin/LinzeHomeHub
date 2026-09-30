#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""2026-10 登记表跟上现状:EEI / ADP 已搬回 VPS-3,Signal-Lattice 重建版。

守四件事:
  1) 登记表不再把 EEI / ADP 描述成依赖 Cloudflare(否则页面会标红一个并不存在的依赖);
  2) 容器、systemd 单元、数据目录能按 owns 正确归属到各自的业务线;
  3) ADP 探活是业务判据(fresh),不是首页 200;状态「服务在线但数据陈旧」必须是 stale 而不是 run;
  4) 不新增任何 Cloudflare API 调用(Cloudflare 账面说明只改文字)。
"""
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import collect as C                                          # noqa: E402


def proj(name):
    return next(p for p in C.PROJECTS if p["name"] == name)


class RegistryFactsTest(unittest.TestCase):
    def test_eei_no_longer_depends_on_cloudflare(self):
        p = proj("EEI")
        for k in ("host", "db", "store", "deploy"):
            self.assertNotIn("Cloudflare", p[k].replace("不再使用 Cloudflare", ""), k)
        self.assertFalse((p["owns"]).get("cloudflare"))
        self.assertIn("eei-", p["owns"]["container"])
        self.assertIn("eei-", p["owns"]["systemd"])
        self.assertIn("eei-api-pull.timer", p["deploy"])
        self.assertIn("eei-universe-pull.timer", p["deploy"])

    def test_adp_is_selfhosted_on_vps3(self):
        p = proj("ADP")
        self.assertEqual(p["host"], "OVH VPS-3")
        self.assertFalse(p["host"].startswith("Cloudflare"))
        self.assertFalse((p["owns"]).get("cloudflare"))
        self.assertIn("/var/lib/adp/adp.sqlite", p["db"])
        self.assertIn("adp-web-pull.timer", p["deploy"])
        self.assertIn("adp-daily", p["deploy"])
        self.assertIn("adp-backfill", p["deploy"])
        self.assertIn("adp-", p["owns"]["container"])
        self.assertIn("adp-", p["owns"]["systemd"])

    def test_signal_lattice_is_registered_and_skips_v19(self):
        p = proj("Signal-Lattice")
        self.assertTrue(C.SYSTEMD_SERVICE_PATTERN.match("signal-lattice-v2-api.service"))
        self.assertTrue(C.SYSTEMD_SERVICE_PATTERN.match("signal-lattice-v2-loop.service"))
        self.assertTrue(C.SYSTEMD_SERVICE_PATTERN.match("signal-lattice-tunnel.service"))
        # v19 只留作回滚、平时是 inactive:被发现就会造出假红
        self.assertIsNone(C.SYSTEMD_SERVICE_PATTERN.match("signal-lattice-v19-api.service"))
        self.assertEqual(p["health"]["url"], "https://signal-lattice.linzezhang.com/health/ready")

    def test_units_are_claimed_by_their_line(self):
        reg = C.PROJECTS + C.PLATFORM
        cases = [
            ({"kind": "container", "id": "eei-db"}, "EEI"),
            ({"kind": "container", "id": "eei-api-0123456789ab-101500"}, "EEI"),
            ({"kind": "container", "id": "eei-universe-0123456789ab-101500"}, "EEI"),
            ({"kind": "container", "id": "eei-refresh"}, "EEI"),
            ({"kind": "container", "id": "eei-watch"}, "EEI"),
            ({"kind": "systemd", "id": "eei-universe-pull.service"}, "EEI"),
            ({"kind": "systemd", "id": "eei-api-pull.service"}, "EEI"),
            ({"kind": "container", "id": "adp-0123456789ab-101500"}, "ADP"),
            ({"kind": "container", "id": "adp-job-daily"}, "ADP"),
            ({"kind": "systemd", "id": "adp-web-pull.service"}, "ADP"),
            ({"kind": "systemd", "id": "adp-daily.service"}, "ADP"),
            ({"kind": "systemd", "id": "adp-backfill.service"}, "ADP"),
            ({"kind": "systemd", "id": "adp-failure@adp-daily.service.service"}, "ADP"),
            ({"kind": "systemd", "id": "signal-lattice-v2-api.service"}, "Signal-Lattice"),
            ({"kind": "systemd", "id": "signal-lattice-v2-loop.service"}, "Signal-Lattice"),
            ({"kind": "systemd", "id": "signal-lattice-v2-research.service"}, "Signal-Lattice"),
            ({"kind": "systemd", "id": "signal-lattice-tunnel.service"}, "Signal-Lattice"),
        ]
        for unit, want in cases:
            self.assertEqual(C._owner_of(dict(unit, domain=None, detail=""), reg), want, unit["id"])

    def test_no_new_cloudflare_api_calls(self):
        src = open(C.__file__, encoding="utf-8").read()
        # 既有的三处(R2 / D1 用量、Access 席位)都要读令牌才会发请求;总数不得增加
        self.assertEqual(len(re.findall(r"api\.cloudflare\.com", src)), 3)

    def test_cloudflare_usage_notes_reflect_retirement(self):
        notes = {m["key"]: m["note"] for m in C.MANUAL_USAGE}
        self.assertIn("待退役", notes["r2"])
        self.assertIn("待退役", notes["d1"])
        self.assertNotIn("eei-publication + adp-mirror", notes["d1"])


class ProjectHealthTest(unittest.TestCase):
    H = {"kind": "json_true", "url": "https://adp.linzezhang.com/api/selfhost/status", "path": "fresh",
         "reason_path": "fresh_reason", "age_path": "data_age_hours"}

    def test_fresh_is_run(self):
        st, note = C.project_health(self.H, fetch=lambda u: ({"fresh": True, "fresh_reason": "ok", "data_age_hours": 3.2}, None))
        self.assertEqual(st, "run")
        self.assertIn("fresh=True", note)

    def test_stale_data_is_not_run_even_though_service_answers(self):
        st, note = C.project_health(self.H, fetch=lambda u: (
            {"fresh": False, "fresh_reason": "stale:40.0h>30h", "data_age_hours": 40.0}, None))
        self.assertEqual(st, "stale")
        self.assertIn("stale:40.0h>30h", note)

    def test_arxiv_zero_is_stale(self):
        st, _ = C.project_health(self.H, fetch=lambda u: ({"fresh": False, "fresh_reason": "arxiv_zero"}, None))
        self.assertEqual(st, "stale")

    def test_truthy_but_not_true_is_not_run(self):
        for v in ("true", 1, "yes", None):
            st, _ = C.project_health(self.H, fetch=lambda u, v=v: ({"fresh": v}, None))
            self.assertNotEqual(st, "run", repr(v))

    def test_unreachable_or_404_is_down(self):
        st, note = C.project_health(self.H, fetch=lambda u: (None, "端点返回 HTTP 404"))
        self.assertEqual((st, note), ("down", "端点返回 HTTP 404"))

    def test_missing_field_is_down_not_healthy(self):
        st, _ = C.project_health(self.H, fetch=lambda u: ({"service": "adp"}, None))
        self.assertEqual(st, "down")

    def test_http_ok_kind(self):
        h = {"kind": "http_ok", "url": "https://signal-lattice.linzezhang.com/health/ready"}
        self.assertEqual(C.project_health(h, code_fn=lambda u: "200")[0], "run")
        self.assertEqual(C.project_health(h, code_fn=lambda u: "503")[0], "stale")
        self.assertEqual(C.project_health(h, code_fn=lambda u: "404")[0], "down")
        self.assertEqual(C.project_health(h, code_fn=lambda u: "")[0], "down")

    def test_adp_probe_target_is_business_level_and_inside_estate(self):
        h = proj("ADP")["health"]
        self.assertEqual(h["kind"], "json_true")
        self.assertEqual(h["path"], "fresh")
        self.assertTrue(C._HOST_OK.match(h["url"]))
        self.assertTrue(h["url"].endswith("/api/selfhost/status"))

    def test_projects_live_uses_health_not_homepage(self):
        orig_code, orig_fetch = C.http_code, C._FETCH
        try:
            C.http_code = lambda u: "200"                       # 首页一律 200
            C._FETCH = lambda u: ({"fresh": False, "fresh_reason": "stale:99h>30h"}, None)
            out, online = C.projects_live()
        finally:
            C.http_code, C._FETCH = orig_code, orig_fetch
        adp = next(p for p in out if p["name"] == "ADP")
        self.assertEqual(adp["status"], "stale")
        self.assertIn("stale:99h>30h", adp["health_note"])


if __name__ == "__main__":
    unittest.main()
