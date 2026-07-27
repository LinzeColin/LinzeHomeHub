#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""「测不出来」绝不能把一格说得比自报更没事。

实测踩到:给 arxiv 的 `BL-ADP-DAILY.scan` 接上探针的那一刻,它从如实的
`blocked`(看门狗已连红三天:ingested arXiv=0)变成了「不确定」——
因为回流还没落第一条记录,`measured=unknown` 直接盖掉了 `declared`。

**给一格接上探针,不该让它看起来比原来更没事。**

方向必须是不对称的:

    declared=healthy + measured=unknown  ->  不确定   (对:自报的绿不可信)
    declared=blocked + measured=unknown  ->  断了     (对:自己说坏了,机器只是
                                                       还没测到,不构成"其实没事")

自报只被允许**往坏里说**,永远不被允许往好里说。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import collect as C                                          # noqa: E402


def merge(declared, measured, weak=False):
    """复刻 flow_state() 里那段判定,单独可测。

    ★ 这是复制实现的**唯一例外情形**:原逻辑埋在一个几百行的循环中间,
      抽不出来单独调用。所以下面另有一条测试,直接对源码做结构断言,
      保证这份复刻和实现不会偷偷分叉 —— 否则这就是装饰性测试。
    """
    if declared in ("blocked_by_policy", "not_built"):
        return declared
    if measured == "unknown" and C._SEV.get(declared, 9) < C._SEV["unknown"]:
        return declared
    if measured and not (weak and measured == "healthy"):
        return measured
    if declared in C.FLOW_STATES:
        return declared
    return "unknown"


class UnknownNeverSoftens(unittest.TestCase):

    def test_已知断了_测不出来时仍然是断了(self):
        """线上那一格的原型。"""
        self.assertEqual(merge("blocked", "unknown"), "blocked")

    def test_所有比不确定更坏的自报_都不被测不出来盖掉(self):
        for d in ("blocked_by_input", "blocked", "degraded", "not_built"):
            with self.subTest(d):
                self.assertEqual(merge(d, "unknown"), d,
                                 "%s 被 unknown 说轻了" % d)

    def test_自报的绿_测不出来时必须降成不确定(self):
        """★ 反方向绝不能一起放宽 —— 这才是这套东西的底线。"""
        self.assertEqual(merge("healthy", "unknown"), "unknown")

    def test_按规定不通的自报_测不出来时保持按规定(self):
        self.assertEqual(merge("blocked_by_policy", "unknown"), "blocked_by_policy")

    def test_真测到的结果_照旧压过自报(self):
        """★ 这条改动只针对 unknown,不得让自报压过**真的测到的**结果。"""
        self.assertEqual(merge("blocked", "healthy"), "healthy")
        self.assertEqual(merge("healthy", "blocked"), "blocked")
        self.assertEqual(merge("degraded", "healthy"), "healthy")

    def test_弱证据仍然不得单独判绿(self):
        self.assertEqual(merge("blocked", "healthy", weak=True), "blocked")
        self.assertEqual(merge("healthy", "healthy", weak=True), "healthy")

    def test_没有探针时行为不变(self):
        self.assertEqual(merge("healthy", None), "healthy")
        self.assertEqual(merge("blocked", None), "blocked")
        self.assertEqual(merge("胡写的", None), "unknown")

    def test_严重度排序保证了不对称性成立(self):
        """★ 这条不对称性依赖 _SEV 里 unknown 的位置。
        把 unknown 排到比某个真故障还差的位置,上面的规则就会失效。"""
        for worse in ("blocked_by_input", "blocked", "degraded", "not_built"):
            self.assertLess(C._SEV[worse], C._SEV["unknown"],
                            "%s 必须比 unknown 更严重" % worse)
        self.assertGreater(C._SEV["healthy"], C._SEV["unknown"])


class MergeCopyMatchesImplementation(unittest.TestCase):
    """★ 上面用的是实现的复刻。这条直接查源码,防止两边偷偷分叉。"""

    def test_源码里确实有这条不对称规则(self):
        src = open(os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), "collect.py"), encoding="utf-8").read()
        self.assertIn(
            'elif measured == "unknown" and _SEV.get(declared, 9) < _SEV["unknown"]:',
            src, "实现里没有这条规则,或写法变了 —— 上面那份复刻已经不算数")

    def test_这条规则排在_measured_覆盖之前(self):
        """顺序错了规则就失效:必须挡在 `final = measured` 那条之前。"""
        src = open(os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), "collect.py"), encoding="utf-8").read()
        body = src.split("# 自报优先:policy / not_built")[1].split("mism = bool(")[0]
        i_rule = body.index('measured == "unknown"')
        i_over = body.index("final = measured")
        self.assertLess(i_rule, i_over, "不对称规则排在了 measured 覆盖之后,等于没写")


if __name__ == "__main__":
    unittest.main(verbosity=2)
