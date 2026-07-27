#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""业务流探针的**安全**与**诚实**守卫。

`flow.yaml` 来自代码仓,对这台主机而言是**不可信输入**:任何能改仓库文件的人,
如果能让采集器执行 YAML 里的字符串,就等于拿到了这台机器的 shell。
所以探针一律「按类型由采集器自行构造命令」,这里把每条边界钉成断言。

另一半是诚实:没有探针的格子必须标 unknown 而不是当成通过;
`blocked_by_policy` / `not_implemented` 是人的决定,机器测不出来,必须尊重自报值。
"""
import json
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

    def test_five_state_vocabulary(self):
        """★ blocked 必须拆成 by_policy / by_input —— 处置动作相反(KMFA 线程实测反馈):
        by_policy 不需要任何人做事,by_input 必须催人。合成一态就排不出优先级。"""
        for st in ("healthy", "degraded", "blocked_by_policy", "blocked_by_input",
                   "not_built", "unknown"):
            self.assertIn(st, C.FLOW_STATES, "状态 %s 缺失" % st)
        self.assertNotEqual(C._SEV["blocked_by_input"], C._SEV["blocked_by_policy"],
                            "两种阻断的严重度必须不同,否则总览排不出优先级")
        self.assertLess(C._SEV["blocked_by_input"], C._SEV["blocked_by_policy"],
                        "缺输入要催人,必须排在按规定不通之前")

    def test_log_freshness_alone_cannot_declare_healthy(self):
        """★ KMFA 实测:cron 只跑校验器从没调真归档程序 —— 日志新鲜、退出码 0、
        但一个文件都没取回来。拿日志新鲜度判健康,页面就会系统性说谎。"""
        self.assertIn("log_recent", C.WEAK_PROBES)
        import tempfile, os as _os, time as _t
        d = tempfile.mkdtemp()
        f = _os.path.join(d, "x.log")
        open(f, "w").write("ok\n")
        orig = C.FLOW_ROOTS
        C.FLOW_ROOTS = (d,)
        try:
            state, ev = C._pr_log_recent({"path": f, "max_age_h": 24})
        finally:
            C.FLOW_ROOTS = orig
        self.assertNotEqual(state, "healthy",
                            "日志新鲜度绝不能单独判 healthy —— 这正是假绿的来源")

    def test_artifact_probe_reports_zero_output_as_blocked(self):
        """有产出才算通:进程在跑但没产出,必须是阻断而不是健康。"""
        import tempfile
        d = tempfile.mkdtemp()
        orig = C.FLOW_ROOTS
        C.FLOW_ROOTS = (d,)
        try:
            state, ev = C._pr_artifact_rows({"dir": d, "suffix": ".json", "min": 1})
        finally:
            C.FLOW_ROOTS = orig
        self.assertEqual(state, "blocked")
        self.assertIn("没产出", ev)


class PublicMaskTest(unittest.TestCase):
    """公开面永不出现私有仓名 —— 即使来源仓是 public。

    实测抓到过:KMFA 的 business_baselines.json(在 public 的 KMOS 里)evidence 文案含
    `Private-Database`,原样搬进本站公开快照就破了这条不变量。
    这条不变量是本站自己的,**不随上游可见性放松**。
    """

    def test_flow_state_actually_applies_the_mask(self):
        """★ 必须走 **flow_state 的真实产出**,不能只测 _mask_private 本身。

        第一版就是只测了工具函数:把 flow_state 里的调用删掉,守卫依然全绿 ——
        断言没钉在「产物」上,就挡不住「忘了调用」。这是同一天内第二次踩装饰性断言。
        """
        import tempfile
        d = tempfile.mkdtemp()
        os.makedirs(os.path.join(d, "private"), exist_ok=True)
        os.makedirs(os.path.join(d, "data"), exist_ok=True)
        json.dump({"projects": [{"project": "T", "repo": "R", "stages": ["intake"],
                                 "baselines": [{"id": "B1", "name": "线", "priority": "P0",
                                                "cells": {"intake": {
                                                    "state": "healthy",
                                                    "evidence": "写入 Private-Database 分区"}}}],
                                 "defects": []}],
                   "unregistered": [{"project": "X", "repo": "KMFA-App-State-Backup",
                                     "expect": "p", "why": "w"}], "at": 0},
                  open(os.path.join(d, "private", "flow_docs.json"), "w"), ensure_ascii=False)
        app, data = C.APP_DIR, C.DATA_DIR
        C.APP_DIR, C.DATA_DIR = d, os.path.join(d, "data")
        try:
            out = json.dumps(C.flow_state(), ensure_ascii=False)
        finally:
            C.APP_DIR, C.DATA_DIR = app, data
        for name in ("Private-Database", "KMFA-App-State-Backup"):
            self.assertNotIn(name, out, "%s 出现在 flow_state 的产出里" % name)
        self.assertIn("私有库", out, "脱敏后语义不该丢失")


if __name__ == "__main__":
    unittest.main(verbosity=2)
