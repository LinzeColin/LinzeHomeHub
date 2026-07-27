#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""耦合判定:末段「按规定不公开」不算阻断下游。

线上实测抓到的误报(2026-07-28,8 处耦合违规里有 3 处是假的):

    whkmSalary「季度绩效工资总额与税后保留」只有**末段 present** 是
    blocked_by_policy ——「工资为敏感数据,结果只进私有库、不出公开面」。
    数据照样流到了下游的结算基线,可整条上游基线的 state 取的是**最差的
    那一格**,被这一格拉成 blocked_by_policy,于是下游 input/score/weight
    三格全被判成耦合违规。

    → 下游消费的是上游算出来的**数据**,不是上游的**最后一公里展示**。
      末段按规定不公开,挡的是公开面,不是数据流。

同一批里另外 5 处是**真的**:KMFA 上游归档的 intake 卡在「私有库缺群清单
配置」(blocked_by_input),下游却报「通」。那必须继续报出来 —— 修这个误报
时最容易顺手把真违规也一起漏掉,所以下面每条都配了反向断言。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import collect as C                                          # noqa: E402

# whkm 的真实阶段序列(末段是 present)
WHKM_STAGES = ["input", "score", "weight", "present"]
# KMFA 的真实阶段序列(末段是 deliver)
KMFA_STAGES = ["intake", "parse", "compute", "verify", "deliver"]


def up(**cells):
    """造一条上游基线:传 stage=state。"""
    return {"cells": {k: {"s": v} for k, v in cells.items()}}


class TerminalPolicyDoesNotBlock(unittest.TestCase):

    def test_末段按规定不公开_不阻断下游(self):
        """线上那 3 处误报的原型。"""
        u = up(input="healthy", score="healthy",
               weight="healthy", present="blocked_by_policy")
        self.assertFalse(C._blocks_downstream(u, WHKM_STAGES))

    def test_末段真故障_照旧阻断下游(self):
        """★ 只放过「按规定」,不能连末段的真故障一起放过。"""
        for bad in ("blocked", "blocked_by_input", "not_built"):
            with self.subTest(bad):
                u = up(input="healthy", score="healthy",
                       weight="healthy", present=bad)
                self.assertTrue(C._blocks_downstream(u, WHKM_STAGES),
                                "末段 %s 是真故障,必须阻断下游" % bad)

    def test_非末段按规定_照旧阻断下游(self):
        """★ 中间段被规定挡住,数据是真的到不了下游。"""
        u = up(input="healthy", score="blocked_by_policy",
               weight="healthy", present="healthy")
        self.assertTrue(C._blocks_downstream(u, WHKM_STAGES))

    def test_KMFA_上游归档_intake_卡住_必须阻断(self):
        """线上那 5 处真违规的原型 —— 修误报时最容易把它一起漏掉。"""
        u = up(intake="blocked_by_input", parse="healthy", compute="healthy",
               verify="healthy", deliver="healthy")
        self.assertTrue(C._blocks_downstream(u, KMFA_STAGES))

    def test_末段按规定_但别处也坏_仍然阻断(self):
        """★ 豁免只针对末段那一格,不是「只要末段是 policy 就整条放行」。"""
        u = up(intake="blocked_by_input", parse="healthy", compute="healthy",
               verify="healthy", deliver="blocked_by_policy")
        self.assertTrue(C._blocks_downstream(u, KMFA_STAGES))

    def test_全通_不阻断(self):
        u = up(**{s: "healthy" for s in KMFA_STAGES})
        self.assertFalse(C._blocks_downstream(u, KMFA_STAGES))

    def test_degraded_不阻断下游(self):
        """能用但打折 —— 数据还在流,不该判下游违规。"""
        u = up(intake="degraded", parse="healthy", compute="healthy",
               verify="healthy", deliver="healthy")
        self.assertFalse(C._blocks_downstream(u, KMFA_STAGES))

    def test_阶段表为空时_不把按规定当豁免(self):
        """★ 取不到阶段序列就没有「末段」可言 —— 此时必须按老规矩从严,
        否则一个空的 stages 会静默地把所有 policy 阻断全部豁免掉。"""
        u = up(present="blocked_by_policy")
        self.assertTrue(C._blocks_downstream(u, []))

    def test_末段名字不叫present或deliver也成立(self):
        """★ 判定必须靠**阶段序列的最后一项**,不能靠猜名字。"""
        stages = ["a", "b", "收尾随便叫什么"]
        u = up(a="healthy", b="healthy", **{"收尾随便叫什么": "blocked_by_policy"})
        self.assertFalse(C._blocks_downstream(u, stages))


class CollapsedStateMasksBlockage(unittest.TestCase):
    """★ 修上面那个误报时,顺带挖出来的**假绿** —— 比误报严重得多。

    基线对外只报一个 state,取的是 _SEV 最小的那一格。但 _SEV 是**给人看的
    展示优先级**(「等你给材料」最该先看所以排 0),不是**阻断严重度**:

        degraded          = 2      ← 于是它被选成 state
        blocked_by_policy = 5
        healthy           = 6

    线上真实案例 BL-ALPHA-RISK(交易前风控):
        approve = degraded,  execute = blocked_by_policy
    整条基线对外报 degraded,而 degraded 不在 FLOW_BLOCKS_DOWNSTREAM 里,
    于是「执行段按规定禁掉了」被彻底盖住,下游 Alpha 实盘 5 个格子一路
    报「通」,**从来没被标记过**。

    → 判定阻断必须**逐格看**,绝不能先把基线塌缩成一个值。塌缩会丢信息,
      而丢哪一条取决于一个为别的目的排的序。
    """

    def test_degraded不得盖住同基线里的policy阻断(self):
        stages = ["feed", "preflight", "decide", "approve", "execute", "settle"]
        u = up(feed="healthy", preflight="healthy", decide="healthy",
               approve="degraded", execute="blocked_by_policy", settle="healthy")
        # 旧口径塌缩后得到 degraded,判「不阻断」—— 这就是那个假绿
        collapsed = min(u["cells"].values(), key=lambda c: C._SEV.get(c["s"], 9))["s"]
        self.assertEqual(collapsed, "degraded")
        self.assertNotIn(collapsed, C.FLOW_BLOCKS_DOWNSTREAM)
        # 逐格看才拦得住
        self.assertTrue(C._blocks_downstream(u, stages),
                        "execute 段按规定禁掉了,下游不该自称健康")

    def test_degraded不得盖住同基线里的真故障(self):
        """同一个塌缩问题,换成真故障一样成立。"""
        stages = ["a", "b", "c"]
        u = up(a="degraded", b="not_built", c="healthy")
        collapsed = min(u["cells"].values(), key=lambda c: C._SEV.get(c["s"], 9))["s"]
        self.assertEqual(collapsed, "degraded")
        self.assertTrue(C._blocks_downstream(u, stages))


class RealSnapshotShape(unittest.TestCase):
    """端到端:用三条真实形状的基线跑一遍,确认假报被撤、真报被留、假绿被抓。"""

    def test_三种情形同时成立(self):
        whkm_up = up(input="healthy", score="healthy",
                     weight="healthy", present="blocked_by_policy")
        kmfa_up = up(intake="blocked_by_input", parse="healthy",
                     compute="healthy", verify="healthy", deliver="healthy")
        alpha_up = up(feed="healthy", preflight="healthy", decide="healthy",
                      approve="degraded", execute="blocked_by_policy",
                      settle="healthy")
        alpha_stages = ["feed", "preflight", "decide", "approve", "execute", "settle"]
        self.assertFalse(C._blocks_downstream(whkm_up, WHKM_STAGES))   # 误报,撤
        self.assertTrue(C._blocks_downstream(kmfa_up, KMFA_STAGES))    # 真报,留
        self.assertTrue(C._blocks_downstream(alpha_up, alpha_stages))  # 假绿,抓


if __name__ == "__main__":
    unittest.main(verbosity=2)
