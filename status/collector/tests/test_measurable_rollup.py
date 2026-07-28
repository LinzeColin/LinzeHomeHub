#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""可测格子的计数必须**逐层传上去**,直到快照。

我漏了这一层,把线上覆盖率打成了 4100%:

    measurable 只加在**基线级**的 row 上,
    而快照汇总读的是**项目级**的 p.get("measurable", 0) —— 每个项目取到默认值 0
    ⇒ measurable_total=0、unmeasurable_total=315
    ⇒ 41/0 渲染成「4100%」

★「在一层算、在另一层读」是这个文件反复出现的形状。新增一个计数字段必须
  **四层都过一遍**:格 → 基线 → 项目 → 快照。漏任何一层都是静默失真 ——
  不会抛异常,只会给出一个荒谬或者更糟(看起来合理)的数。

★ 上一批测试没抓到它,是因为全在测辅助函数和源码结构,**没有一条跑真实的
  汇总链路**。这份测试直接调 flow_state(),检查快照里的最终数字。
"""
import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import collect as C                                          # noqa: E402


def docs(cells_by_state):
    """造一个最小 docs:一个项目、一条基线,格子状态由调用方给。"""
    stages = ["s%d" % i for i in range(len(cells_by_state))]
    cells = {st: {"state": s} for st, s in zip(stages, cells_by_state)}
    return {"projects": [{
        "project": "T", "repo": "R", "stages": stages,
        "baselines": [{"id": "BL-T", "name": "测试基线", "cells": cells}],
    }], "at": 0}


def snap(cells_by_state):
    """跑**真实的** flow_state(),拿回快照。

    ★ 它不收参数、从 APP_DIR/private/flow_docs.json 读,所以这里把 APP_DIR
      指到临时目录 —— 既能驱动真链路,又不碰线上任何文件(它还会写历史)。
    """
    tmp = tempfile.mkdtemp()
    try:
        os.makedirs(os.path.join(tmp, "private"))
        os.makedirs(os.path.join(tmp, "data"), exist_ok=True)
        with open(os.path.join(tmp, "private", "flow_docs.json"),
                  "w", encoding="utf-8") as fh:
            json.dump(docs(cells_by_state), fh, ensure_ascii=False)
        old = C.APP_DIR
        C.APP_DIR = tmp
        try:
            return C.flow_state()
        finally:
            C.APP_DIR = old
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


class MeasurableReachesTheSnapshot(unittest.TestCase):

    def test_四个计数字段都出现在快照里(self):
        s = snap(["healthy", "not_built"])
        for k in ("verified_total", "cells_total",
                  "measurable_total", "unmeasurable_total"):
            self.assertIn(k, s, "%s 没进快照" % k)

    def test_可测数不得为零_这正是4100那个bug(self):
        """★ 直接钉死那次事故:有可测格子时 measurable_total 必须 > 0。"""
        s = snap(["healthy", "degraded", "not_built"])
        self.assertGreater(s["measurable_total"], 0,
                           "measurable_total=0 —— 汇总链路又断了,覆盖率会变成天文数字")

    def test_可测数与不可测数相加等于全部(self):
        s = snap(["healthy", "degraded", "not_built", "blocked_by_policy", "blocked"])
        self.assertEqual(s["measurable_total"] + s["unmeasurable_total"],
                         s["cells_total"])

    def test_可测数精确等于扣掉两态之后的数(self):
        s = snap(["healthy", "degraded", "blocked", "blocked_by_input",
                  "not_built", "not_built", "blocked_by_policy"])
        self.assertEqual(s["cells_total"], 7)
        self.assertEqual(s["unmeasurable_total"], 3)   # 2 个没做 + 1 个按规定
        self.assertEqual(s["measurable_total"], 4)

    def test_全部不可测时_分母是零而不是负数(self):
        """★ 边界:全是没做/按规定时分母为 0,前端必须能安全地不除。"""
        s = snap(["not_built", "blocked_by_policy"])
        self.assertEqual(s["measurable_total"], 0)
        self.assertEqual(s["unmeasurable_total"], 2)

    def test_项目级也要带上这个数(self):
        """★ 事故就发生在这一层:基线级有、项目级没有。"""
        s = snap(["healthy", "not_built"])
        for p in s["projects"]:
            self.assertIn("measurable", p, "项目级少了 measurable —— 汇总会取默认 0")

    def test_基线级也要带上这个数(self):
        s = snap(["healthy", "not_built"])
        for p in s["projects"]:
            for b in p["baselines"]:
                self.assertIn("measurable", b, "基线级少了 measurable")

    def test_项目级等于其下基线之和(self):
        s = snap(["healthy", "degraded", "not_built"])
        for p in s["projects"]:
            self.assertEqual(p["measurable"],
                             sum(b["measurable"] for b in p["baselines"]))

    def test_快照级等于各项目之和(self):
        s = snap(["healthy", "degraded", "not_built"])
        self.assertEqual(s["measurable_total"],
                         sum(p["measurable"] for p in s["projects"]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
