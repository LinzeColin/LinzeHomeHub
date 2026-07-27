#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""live_traffic() 的分类与幂等断言。

用**合成 access log** 打进真实函数,而不是去线上制造故障——线上站点是 SPA,
任何未知路径都 200 兜底,根本逼不出 4xx,只能靠注入才能真正走到错误分支。
"""
import json
import os
import sys
import tempfile
import time
import unittest
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

UA_BROWSER = "Mozilla/5.0 (Macintosh) AppleWebKit/537.36 Chrome/148.0 Safari/537.36"
UA_GATUS = "Gatus/1.0"
UA_CURL = "curl/8.5.0"


def line(site, when, path, code, ua, meth="GET"):
    stamp = when.strftime("%d/%b/%Y:%H:%M:%S +0000")
    return ('%s\t10.0.1.6 - - [%s] "%s %s HTTP/1.1" %s 1234 "-" "%s" "172.70.1.1"'
            % (site, stamp, meth, path, code, ua))


class LiveTrafficTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        os.environ["STATUS_APP_DIR"] = self.dir
        for m in list(sys.modules):
            if m == "collect":
                del sys.modules[m]
        import collect
        self.collect = collect
        collect.DATA_DIR = os.path.join(self.dir, "data")
        os.makedirs(collect.DATA_DIR, exist_ok=True)
        # 落在当前这一分钟,才会进入 60m/24h 的统计窗口
        self.t = datetime.now(timezone.utc).replace(second=0, microsecond=0)

    def _run(self, rows):
        return self.collect.live_traffic(raw="\n".join(rows))

    def test_classifies_four_kinds(self):
        rows = [
            line("a.example.com", self.t, "/", 200, UA_BROWSER),        # h
            line("a.example.com", self.t, "/about", 200, UA_BROWSER),   # h
            line("a.example.com", self.t, "/", 200, UA_GATUS),          # p
            line("a.example.com", self.t, "/healthz", 200, UA_CURL),    # p(UA 优先于路径)
            line("a.example.com", self.t, "/data/x.json", 200, UA_BROWSER),  # s
            line("a.example.com", self.t, "/boom", 500, UA_BROWSER),    # h + e
            line("a.example.com", self.t, "/gone", 404, UA_BROWSER),    # h + e
            line("a.example.com", self.t, "/admin", 403, UA_BROWSER),   # h,不算 e(Access 拦截)
        ]
        r = self._run(rows)["r60"]
        self.assertEqual(r["h"], 5, "真实浏览应为 5(含 500/404/403 三条)")
        self.assertEqual(r["p"], 2, "探测应为 2")
        self.assertEqual(r["s"], 1, "自轮询应为 1")
        self.assertEqual(r["e"], 2, "错误应只数 500 和 404,403 不算")

    def test_same_minute_is_overwritten_not_doubled(self):
        """同一分钟被读到两次(cron 每分钟跑 + --since 3m 有重叠)不能算两遍。"""
        rows = [line("a.example.com", self.t, "/", 200, UA_BROWSER)] * 3
        self.assertEqual(self._run(rows)["r60"]["h"], 3)
        self.assertEqual(self._run(rows)["r60"]["h"], 3, "重复采集把同一分钟算重了")

    def test_hour_bucket_recomputed_not_accumulated(self):
        rows = [line("a.example.com", self.t, "/", 200, UA_BROWSER)] * 4
        self._run(rows)
        out = self._run(rows)
        cur = self.t.replace(minute=0)
        hit = [x for x in out["hours"] if x["t"] == int(cur.timestamp())]
        self.assertTrue(hit, "应有当前小时的桶")
        self.assertEqual(hit[0]["h"], 4, "小时桶必须由分钟桶重算,不能累加")

    def test_malformed_lines_are_skipped_not_fatal(self):
        rows = ["garbage", "a.example.com\tno-bracket-here", "",
                line("a.example.com", self.t, "/", 200, UA_BROWSER)]
        self.assertEqual(self._run(rows)["r60"]["h"], 1)

    def test_no_visitor_ip_is_ever_stored(self):
        """日志里最后一段是 Cloudflare 边缘 IP,绝不能进产物(拿它算 UV 就是编造)。"""
        rows = [line("a.example.com", self.t, "/", 200, UA_BROWSER)]
        out = json.dumps(self._run(rows), ensure_ascii=False)
        self.assertNotIn("172.70.1.1", out)
        self.assertNotIn(UA_BROWSER, out)
        archive = open(os.path.join(self.collect.DATA_DIR, "live_traffic.json")).read()
        self.assertNotIn("172.70.1.1", archive)


if __name__ == "__main__":
    unittest.main(verbosity=2)
