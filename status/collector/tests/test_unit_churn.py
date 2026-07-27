#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""单元增减流水守卫:**消失必须留痕**。

★ 这是「被丢掉的东西不参与任何总量校验」的第四种形状,而且是**不对称**的那一种:
  新增的单元会被登记核对标成治理违规,有人看;
  被删掉的单元只是列表里少一行 —— 没有任何指标会因此变红,没人会注意。
  「少一行」恰恰是最容易被当成正常的形态(KMFA 线程 2026-07-27 的提醒)。

守卫要钉住三件事,每一件都对应一种会让这块功能悄悄失效的改法:
  1. 消失必须被记 —— 只记新增等于没做
  2. 首轮不得刷出全量「新增」—— 否则第一屏就是几十条噪音,之后没人再看
  3. 临时容器折叠**不能连常驻单元一起折** —— 折过头就又回到「没有去向账」
"""
import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import collect as C                                          # noqa: E402


def U(uid, kind="container", owner="X", policy="always"):
    return {"id": uid, "kind": kind, "owner": owner, "policy": policy}


class UnitChurnTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self._data = C.DATA_DIR
        C.DATA_DIR = self.dir

    def tearDown(self):
        C.DATA_DIR = self._data
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_first_round_seeds_silently(self):
        """★ 首轮把全部单元刷成「新增」会让第一屏全是噪音,之后就没人看了。"""
        out = C._unit_ledger([U("a"), U("b"), U("c")])
        self.assertTrue(out["seeded"])
        self.assertEqual(out["events"], [])

    def test_removal_is_recorded(self):
        """★ 核心:少一行必须留痕。只记新增 = 这块功能等于没做。"""
        C._unit_ledger([U("a"), U("b")])
        out = C._unit_ledger([U("a")])
        ops = [(e["op"], e["id"]) for e in out["events"]]
        self.assertIn(("removed", "b"), ops)
        self.assertEqual(out["removed_n"], 1)
        self.assertEqual(out["recent_removed"][0]["id"], "b")

    def test_addition_is_recorded_too(self):
        C._unit_ledger([U("a")])
        out = C._unit_ledger([U("a"), U("new")])
        self.assertIn(("added", "new"), [(e["op"], e["id"]) for e in out["events"]])

    def test_removed_unit_keeps_its_former_owner(self):
        """消失的单元原来归谁 —— 这是判断严重程度的关键,不能丢。"""
        C._unit_ledger([U("gone", owner="Alpha")])
        out = C._unit_ledger([])
        self.assertEqual(out["recent_removed"][0]["owner"], "Alpha")

    def test_ephemeral_folded_but_still_on_the_books(self):
        """★ 临时构建容器(restart=no)来去频繁,要**折叠**不是丢弃 ——
        丢弃就又变成「没有去向账」,正是这块功能要防的东西。"""
        C._unit_ledger([U("build-1", policy="no"), U("svc", policy="always")])
        out = C._unit_ledger([U("svc", policy="always")])
        self.assertEqual(out["removed_n"], 0, "临时容器不该计进「常驻单元消失」")
        self.assertEqual(out["ephemeral_n"], 1, "但它必须仍在账上,不能被丢掉")
        self.assertIn("build-1", [e["id"] for e in out["events"]])

    def test_persistent_removal_not_folded_with_ephemeral(self):
        """折叠不能折过头:常驻单元消失必须照样进 recent_removed。"""
        C._unit_ledger([U("build-1", policy="no"), U("svc", policy="always")])
        out = C._unit_ledger([])
        self.assertEqual([e["id"] for e in out["recent_removed"]], ["svc"])

    def test_unchanged_round_produces_no_events(self):
        """没变化就不该有事件 —— 否则流水会被自己刷满。"""
        C._unit_ledger([U("a"), U("b")])
        C._unit_ledger([U("a"), U("b")])
        out = C._unit_ledger([U("a"), U("b")])
        self.assertEqual(out["events"], [])

    def test_ledger_survives_corrupt_store(self):
        """存档损坏时不能炸掉整轮采集,退化成重新建基线即可。"""
        with open(os.path.join(self.dir, "unit_ledger.json"), "w") as f:
            f.write("{not json")
        out = C._unit_ledger([U("a")])
        self.assertTrue(out["seeded"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
