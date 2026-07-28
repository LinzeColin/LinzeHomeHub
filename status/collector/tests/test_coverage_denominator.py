#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""覆盖率的分母:「测不了」和「还没测」必须分开。

线上实测(2026-07-28):

    全部格子 315,已实测 41 = 13.0%
    其中 49 格**根本没有东西可测**:
      还没建 (not_built)          27 格 —— 代码都不存在,探针探什么
      按规定不通 (blocked_by_policy) 22 格 —— 本来就不该跑,测了也没意义

    可测分母 266,已实测 41 = 15.4%,缺口 185 格

★ 这两态在 flow_state() 的 final 判定里第一条分支就写明是
  「人的决定,机器测不出来,必须尊重」。既然那里承认测不出来,
  覆盖率的分母就不能再拿它们去要求实测 —— 否则是拿一个自己都
  承认测不了的东西去算达成率。

★ 但**两个数都要出**:只留可测口径,「大片没建」会从看板上消失。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import collect as C                                          # noqa: E402

UNMEASURABLE = ("not_built", "blocked_by_policy")


def measurable_of(states):
    """复刻 flow_state() 里那一行。下面另有源码结构断言防止分叉。"""
    return sum(1 for s in states if s not in UNMEASURABLE)


class MeasurableDenominator(unittest.TestCase):

    def test_没建的不算进可测分母(self):
        self.assertEqual(measurable_of(["healthy", "not_built", "not_built"]), 1)

    def test_按规定不通的不算进可测分母(self):
        self.assertEqual(measurable_of(["healthy", "blocked_by_policy"]), 1)

    def test_真故障要算进可测分母(self):
        """★ 断了/等材料/有缺陷都是**测得出来**的,绝不能一起排除掉 ——
        那会把真缺口也从分母里抹掉,覆盖率立刻虚高。"""
        for s in ("blocked", "blocked_by_input", "degraded"):
            with self.subTest(s):
                self.assertEqual(measurable_of([s]), 1, "%s 被错误排除" % s)

    def test_不确定要算进可测分母(self):
        """★「测不出来」不等于「没有东西可测」——它恰恰是待办。"""
        self.assertEqual(measurable_of(["unknown"]), 1)

    def test_线上那组真实分布(self):
        states = (["healthy"] * 208 + ["not_built"] * 27 + ["blocked_by_input"] * 15
                  + ["blocked_by_policy"] * 22 + ["degraded"] * 40 + ["blocked"] * 3)
        self.assertEqual(len(states), 315)
        self.assertEqual(measurable_of(states), 266)

    def test_两态与final判定的第一条分支保持一致(self):
        """★ 分母的口径必须和「机器测不出来,必须尊重」那条分支同源。
        那边改了这边没改,就会出现「承认测不出来、却仍按它算达成率」。"""
        src = open(os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), "collect.py"), encoding="utf-8").read()
        self.assertIn('if declared in ("blocked_by_policy", "not_built"):', src)
        self.assertIn('if c["s"] not in ("not_built", "blocked_by_policy")', src)


class BothNumbersMustBePublished(unittest.TestCase):
    """★ 只留可测口径会让「大片没建」从看板上消失。两个数都得出。"""

    def test_四个计数字段都在快照里(self):
        src = open(os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), "collect.py"), encoding="utf-8").read()
        for key in ('"verified_total"', '"cells_total"',
                    '"measurable_total"', '"unmeasurable_total"'):
            self.assertIn(key, src, "%s 没进快照" % key)

    def test_不可测数是相减出来的_不另算一遍(self):
        """两处各算一遍必然漂移;不可测数只能是 cells_n - measurable。"""
        src = open(os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), "collect.py"), encoding="utf-8").read()
        self.assertIn('p["cells_n"] - p.get("measurable", 0)', src)


if __name__ == "__main__":
    unittest.main(verbosity=2)
