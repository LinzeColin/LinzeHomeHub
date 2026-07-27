#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""业务流探针的**安全**与**诚实**守卫。

`flow.yaml` 来自代码仓,对这台主机而言是**不可信输入**:任何能改仓库文件的人,
如果能让采集器执行 YAML 里的字符串,就等于拿到了这台机器的 shell。
所以探针一律「按类型由采集器自行构造命令」,这里把每条边界钉成断言。

另一半是诚实:没有探针的格子必须标 unknown 而不是当成通过;
`blocked_by_policy` / `not_implemented` 是人的决定,机器测不出来,必须尊重自报值。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import collect as C                                          # noqa: E402


class PathSandboxTest(unittest.TestCase):
    def test_only_allowed_roots(self):
        self.assertIsNotNone(C._safe_path("/srv/linze/apps/status/data/snapshot.json"))
        for bad in ("/etc/passwd", "/root/.ssh/id_rsa", "relative/path",
                    "/srv/linze/../../etc/shadow", "/home/ubuntu/.secrets"):
            self.assertIsNone(C._safe_path(bad), "%s 不该被允许" % bad)

    def test_traversal_rejected_even_inside_root(self):
        self.assertIsNone(C._safe_path("/srv/linze/apps/../../../etc/passwd"))


class NoFreeFormExecutionTest(unittest.TestCase):
    """核心:YAML 里放不进任何可执行语句。"""

    def test_db_probe_rejects_injection_in_every_field(self):
        for args in (
            {"container": "coolify-db; rm -rf /", "db": "coolify", "table": "t"},
            {"container": "coolify-db", "db": "coolify", "table": "t; drop table t"},
            {"container": "coolify-db", "db": "co olify", "table": "t"},
            {"container": "coolify-db", "db": "coolify", "table": "t",
             "time_column": "x); drop table y --"},
        ):
            state, ev = C._pr_db_rows(args)
            self.assertEqual(state, "unknown", "注入参数必须被拒绝:%s" % args)
            self.assertIn("非法", ev)

    def test_db_probe_has_no_free_sql_field(self):
        """schema 层面就不该存在自由 SQL 字段 —— 有了就迟早会被用。"""
        src = open(os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), "collect.py")).read()
        body = src[src.index("def _pr_db_rows"):src.index("def _pr_log_recent")]
        for banned in ('a.get("sql")', 'a.get("query")', 'a["sql"]'):
            self.assertNotIn(banned, body, "探针不得接受自由 SQL")

    def test_http_probe_rejects_non_https_and_weird_urls(self):
        for u in ("http://x.com", "https://x.com/$(whoami)", "file:///etc/passwd",
                  "https://x.com/a;b", "javascript:alert(1)"):
            state, ev = C._pr_http({"url": u})
            self.assertEqual(state, "unknown", "%s 不该被探测" % u)

    def test_unit_and_container_names_must_be_safe(self):
        self.assertEqual(C._pr_systemd({"unit": "a b; id"})[0], "unknown")
        self.assertEqual(C._pr_container({"name": "x`whoami`"})[0], "unknown")

    def test_log_pattern_rejects_shell_metacharacters(self):
        state, _ = C._pr_log_recent({"path": "/srv/linze/x.log", "contains": "$(id)"})
        self.assertEqual(state, "unknown")


class HonestyTest(unittest.TestCase):
    def test_cell_without_probe_is_not_treated_as_pass(self):
        state, _ = C._run_probe({"evidence": "只是写了句说明"})
        self.assertIsNone(state, "没有探针就不该产生任何实测结论")

    def test_unknown_probe_type_is_unknown_not_ok(self):
        state, ev = C._run_probe({"probe": "curl_anything"})
        self.assertEqual(state, "unknown")

    def test_probe_exception_does_not_kill_the_table(self):
        """一格出错不能拖垮整张矩阵。"""
        C.PROBES["__boom"] = lambda a: 1 / 0
        try:
            state, ev = C._run_probe({"probe": "__boom"})
        finally:
            C.PROBES.pop("__boom", None)
        self.assertEqual(state, "unknown")
        self.assertIn("异常", ev)

    def test_four_state_vocabulary_includes_policy_block(self):
        """少了 blocked_by_policy 这一态,KMFA 的「禁群」会立刻变成 4 个假红。"""
        for s in ("ok", "warn", "bad", "blocked_by_policy", "not_implemented", "unknown"):
            self.assertIn(s, C.FLOW_STATES)


if __name__ == "__main__":
    unittest.main(verbosity=2)
