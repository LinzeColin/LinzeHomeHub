#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""业务流探针的**安全**与**诚实**守卫。

`flow.yaml` 来自代码仓,对这台主机而言是**不可信输入**:任何能改仓库文件的人,
如果能让采集器执行 YAML 里的字符串,就等于拿到了这台机器的 shell。
所以探针一律「按类型由采集器自行构造命令」,这里把每条边界钉成断言。

另一半是诚实:没有探针的格子必须标 unknown 而不是当成通过;
`blocked_by_policy` / `not_implemented` 是人的决定,机器测不出来,必须尊重自报值。
"""
import contextlib
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import collect as C                                          # noqa: E402


@contextlib.contextmanager
def _fetch_returns(result):
    """替掉真实网络调用。注意替的是**模块级**的 _FETCH ——
    探针的 args 来自 flow.yaml,绝不能在 args 里留注入点。"""
    old = C._FETCH
    C._FETCH = lambda url, **kw: result
    try:
        yield
    finally:
        C._FETCH = old


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


class SelfReportIsNotVerifiedTest(unittest.TestCase):
    """★ 自报的绿 ≠ 实测的绿。

    KMFA 线程实测反馈:他们现在**没有任何可探的产出物** ——
    Coolify exec 不支持、logs 返回空、健康接口在 Access 后面、私有归档一次都没成功过。
    「连我都拿不到证据,你更拿不到」。这种状态下把自报的 healthy 当成验证过的健康,
    就是替被测方编了一个绿。必须能在产物里分辨。
    """

    def _run(self, cells):
        import tempfile
        d = tempfile.mkdtemp()
        os.makedirs(os.path.join(d, "private"), exist_ok=True)
        os.makedirs(os.path.join(d, "data"), exist_ok=True)
        json.dump({"projects": [{"project": "T", "repo": "R", "stages": list(cells),
                                 "baselines": [{"id": "B1", "name": "线", "priority": "P0",
                                                "cells": cells}], "defects": []}],
                   "unregistered": [], "at": 0},
                  open(os.path.join(d, "private", "flow_docs.json"), "w"), ensure_ascii=False)
        app, data = C.APP_DIR, C.DATA_DIR
        C.APP_DIR, C.DATA_DIR = d, os.path.join(d, "data")
        try:
            return C.flow_state()
        finally:
            C.APP_DIR, C.DATA_DIR = app, data

    def test_cells_without_probe_count_as_unverified(self):
        out = self._run({"a": {"state": "healthy", "evidence": "我说通"},
                         "b": {"state": "healthy", "evidence": "我也说通"}})
        p = out["projects"][0]
        self.assertEqual(p["verified"], 0, "没有探针就不该记成已验证")
        self.assertTrue(p["self_report_only"], "全无探针的项目必须被标为「仅自报」")
        self.assertEqual(out["verified_total"], 0)
        # 状态本身仍尊重自报,不篡改
        self.assertEqual(p["baselines"][0]["cells"]["a"]["s"], "healthy")
        self.assertIsNone(p["baselines"][0]["cells"]["a"]["measured"])

    def test_policy_block_still_blocks_downstream(self):
        """KMFA 的耦合规则把 blocked_by_policy 也算阻断上游 ——
        按规定不通的东西,下游同样拿不到数;但它不需要任何人去修。两种语义必须分开。"""
        self.assertIn("blocked_by_policy", C.FLOW_BLOCKS_DOWNSTREAM,
                      "按规定不通也会让下游拿不到数,必须算阻断上游")
        self.assertNotIn("blocked_by_policy", C.FLOW_BAD,
                         "但它不需要处置,不能计进待办")



class HttpFactSourceTest(unittest.TestCase):
    """被测方公开的只读健康摘要(KMFA PR #218 的 /public-api/技能健康)。

    这条路是它自己提出来的:它那边四条取证路全断,静态文件又会过期,
    而**过期的绿比没有绿更糟**。所以本站去读它的端点 —— 但端点地址与取值路径
    都写在仓库文件里,对这台主机仍然是不可信输入。
    """

    def test_only_own_estate_hosts(self):
        """★ 主机名不设限,等于任何能改仓库文件的人都能把采集器变成对外发请求的信标。"""
        for bad in ("http://kmfa.linzezhang.com/x",          # 非 https
                    "https://evil.com/x",
                    "https://linzezhang.com.evil.com/x",     # 后缀伪装
                    "https://kmfa.linzezhang.com/../../etc", # 穿越
                    "https://kmfa.linzezhang.com/x?u=http://evil"):
            doc, err = C._fetch_json(bad)
            self.assertIsNone(doc, "%s 不该被放行" % bad)
            self.assertIn("拒绝", err or "", "%s 应被主机名/协议白名单拒绝" % bad)

    def test_json_path_is_not_an_expression_engine(self):
        """只认两种形状。任何带函数、算术、通配、嵌套过滤的写法都必须被拒,
        且必须是**明确拒绝**,不能悄悄降级成模糊匹配。"""
        doc = {"技能": [{"技能": "upstream-archive", "运行次数": 3}]}
        for bad in ("技能[?技能=='a' && x=='b'].y",
                    "技能[*].运行次数",
                    "技能[?运行次数>`0`].技能",
                    "sort_by(技能,&运行次数)[0].技能",
                    "技能[0].运行次数",
                    "__class__.__init__"):
            val, err = C._json_pick(doc, bad)
            self.assertIsNone(val, "%s 不该取到值" % bad)
            self.assertTrue(err, "%s 必须给出明确的拒绝理由" % bad)

    def test_json_path_supported_shape_works(self):
        doc = {"技能": [{"技能": "a", "运行次数": 0}, {"技能": "upstream-archive", "运行次数": 7}]}
        self.assertEqual(C._json_pick(doc, "技能[?技能=='upstream-archive'].运行次数"), (7, None))
        self.assertEqual(C._json_pick(doc, "台账可读")[0], None)

    def test_redirect_may_not_leave_the_estate(self):
        """★ 光校验首个 URL 不够:端点回一个 302 就能把采集器牵到任意地址。
        (这条守卫是补上来的 —— 一开始没有它,把跳转校验改成恒真,44 条测试照样全绿。)"""
        host = "kmfa.linzezhang.com"
        self.assertTrue(C._redirect_ok("https://kmfa.linzezhang.com/b", host))
        for bad in ("https://evil.com/b", "http://kmfa.linzezhang.com/b",
                    "https://kmfa.linzezhang.com.evil.com/b", "//evil.com/b", ""):
            self.assertFalse(C._redirect_ok(bad, host), "%s 不该被跟随" % bad)

    def test_json_path_plain_shape_is_not_a_catch_all(self):
        """`a.b.c` 那条形状不能变成「什么都收」——它必须只走显式的逐段取键。"""
        doc = {"a": {"b": 1}}
        self.assertEqual(C._json_pick(doc, "a.b"), (1, None))
        val, err = C._json_pick(doc, "a[?x=='y'].b")     # 形状合法但 a 不是数组
        self.assertIsNone(val)
        self.assertIn("不是数组", err)

    def test_zero_runs_is_blocked_not_degraded(self):
        """★ 「运行次数 0」= 从未跑完过一次。日志再新鲜、退出码再正常都不算数。
        这正是 KMFA 实测过的那种假绿,必须判阻断。"""
        with _fetch_returns(({"技能": [{"技能": "s", "运行次数": 0}]}, None)):
            st, ev = C._pr_artifact_rows({"http": "https://kmfa.linzezhang.com/public-api/技能健康",
                                          "json_path": "技能[?技能=='s'].运行次数", "min": 1})
        self.assertEqual(st, "blocked")
        self.assertIn("从未跑完过一次", ev)

    def test_unreachable_endpoint_is_blocked_not_unknown(self):
        """「问不出来」是坏消息,不是「暂无数据」——不拿没有坏消息当好消息。"""
        with _fetch_returns((None, "端点返回 HTTP 404")):
            st, _ = C._pr_artifact_rows({"http": "u", "json_path": "y"})
            self.assertEqual(st, "blocked")
            st, _ = C._pr_business_ts({"http": "u", "json_path": "y"})
            self.assertEqual(st, "blocked")


class TimestampOffsetTest(unittest.TestCase):
    """★ 实测错过一次:`raw[:19]` 截断 + 「带 T 就按 UTC」,
    会把 `2026-07-27T08:00:00+08:00` 当成 UTC —— **正好差 8 小时,方向还让它显得更旧**,
    于是刚跑完的技能被报成超期。带偏移的必须按偏移算。"""

    def test_explicit_offset_is_honoured(self):
        a = C._parse_ts("2026-07-27T08:00:00+08:00")
        b = C._parse_ts("2026-07-27T08:00:00Z")
        self.assertIsNotNone(a)
        self.assertIsNotNone(b)
        # 同一串数字,一个带 +08:00 一个带 Z,必须差整 8 小时 —— 相等就说明偏移压根没被读
        self.assertEqual(int(b.timestamp() - a.timestamp()), 8 * 3600,
                         "显式时区偏移没有被解析")

    def test_naive_falls_back_to_beijing(self):
        naive = C._parse_ts("2026-07-27 08:00")
        cn = C._parse_ts("2026-07-27T08:00:00+08:00")
        self.assertEqual(int(naive.timestamp()), int(cn.timestamp()),
                         "无偏移的时间戳应按北京时间解释")

    def test_no_seconds_still_parses(self):
        """本站快照的 updated_at 是「2026-07-27 12:54」,没有秒。"""
        self.assertIsNotNone(C._parse_ts("2026-07-27 12:54"))



if __name__ == "__main__":
    unittest.main(verbosity=2)
