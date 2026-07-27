#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""分层自洽守卫:**每一级必须等于它自身加上它的下级**。

来源是 KMFA 线程 2026-07-27 的实测教训 —— 他们同一天踩了两次同类坑:
报表映射只写了模板 A 的行名,落到模板 B 上金额直接蒸发;上卷靠行号认层级,
而模板 B 有一批行没有行号,那些钱永远卷不上去。
**两次总额守恒时错误都照样成立**,是靠逐级自洽才逼出来的。

本站的同型风险很具体:格子只按 `stage_model` 建
(`for st in stages`),基线若声明了 stage_model 里没有的阶段,那一格连同它的
状态/证据/缺陷会被**整个丢掉**,而各级总数依然完美自洽 —— 页面上看不出任何异常。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import collect as C                                          # noqa: E402


def _proj(name, stages, baselines, verified=0):
    return {"project": name, "stages": stages, "baselines": baselines,
            "cells_n": sum(len(b["cells"]) for b in baselines), "verified": verified}


def _bl(cells, verified=0):
    return {"cells": {k: {"s": v} for k, v in cells.items()}, "verified": verified}


class RollupCheckTest(unittest.TestCase):
    def _tot(self, projects, **over):
        cells = sum(p["cells_n"] for p in projects)
        t = {k: 0 for k in C.FLOW_STATES}
        t["cells"] = cells
        t["healthy"] = cells
        t.update(over)
        return t

    def test_clean_matrix_reports_nothing(self):
        """★ 先证明它不是恒红的 —— 一个规整的矩阵必须一条都不报,否则横幅就是噪音。"""
        p = _proj("X", ["a", "b"], [_bl({"a": "healthy", "b": "healthy"}),
                                    _bl({"a": "healthy", "b": "healthy"})])
        self.assertEqual(C._rollup_check([p], self._tot([p])), [])

    def test_project_cells_must_equal_sum_of_baselines(self):
        p = _proj("X", ["a", "b"], [_bl({"a": "healthy", "b": "healthy"})])
        p["cells_n"] = 99                                   # 项目级数字被改坏
        kinds = [x["kind"] for x in C._rollup_check([p], self._tot([p]))]
        self.assertIn("cells_mismatch", kinds)

    def test_non_rectangular_matrix_is_caught(self):
        """基线之间段数不齐 = 有格子没建出来,总数却可能仍然自洽。"""
        p = _proj("X", ["a", "b"], [_bl({"a": "healthy", "b": "healthy"}),
                                    _bl({"a": "healthy"})])
        kinds = [x["kind"] for x in C._rollup_check([p], self._tot([p]))]
        self.assertIn("matrix_not_rectangular", kinds)

    def test_state_counts_must_sum_to_cells(self):
        """★ 这条正对着「静默丢格子」:按状态分类加起来少于总格数,
        说明有格子没被任何状态计到 —— 总量看着仍然正常。"""
        p = _proj("X", ["a", "b"], [_bl({"a": "healthy", "b": "healthy"})])
        tot = self._tot([p])
        tot["healthy"] = 1                                  # 少算一格
        kinds = [x["kind"] for x in C._rollup_check([p], tot)]
        self.assertIn("state_sum_mismatch", kinds)

    def test_verified_must_roll_up(self):
        p = _proj("X", ["a"], [_bl({"a": "healthy"}, verified=1)], verified=0)
        kinds = [x["kind"] for x in C._rollup_check([p], self._tot([p]))]
        self.assertIn("verified_mismatch", kinds)

    def test_total_cells_must_equal_sum_of_projects(self):
        p = _proj("X", ["a"], [_bl({"a": "healthy"})])
        tot = self._tot([p])
        tot["cells"] = 5
        tot["healthy"] = 5
        kinds = [x["kind"] for x in C._rollup_check([p], tot)]
        self.assertIn("total_cells_mismatch", kinds)


if __name__ == "__main__":
    unittest.main(verbosity=2)
