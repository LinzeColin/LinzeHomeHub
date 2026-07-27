#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""业务流 v2:工作流步骤链 + 步骤级跨流关系的传导守卫。

owner 的原例就是这套测试的骨架:
    吃饭 = 买菜 → 洗菜 → 切菜 → 备菜 → 炒菜 → 吃饭
    洗菜 --provides--> 洗水果.开始      我这步通了,别的流才能开工
    切菜 --depends_on--> 菜刀.磨刀      我这步等别的流的结果
    吃饭 <--bound_with--> 喝水.喝水     强绑定,任一边不成立业务就不算达成

★ v1 的错在于把这三种抹平成一个 baseline 级的 upstream。三者的传导方向与处置动作都不同:
    depends_on  上游断 → 我不能通。要催的是**上游那条流**。
    provides    我断  → 下游整条流开不了工。要催的是**我自己**,影响面比看起来大。
    bound_with  任一边断 → 两边都算业务未达成。**对称**,谁也不是谁的上游。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import flow_v2 as F                                          # noqa: E402


def kitchen():
    return [
        {"id": "EAT", "name": "吃饭", "steps": [
            {"id": "buy", "name": "买菜", "own": "healthy"},
            {"id": "wash", "name": "洗菜", "own": "healthy",
             "provides": [{"flow": "FRUIT", "step": "start", "why": "洗菜池腾出来才能洗水果"}]},
            {"id": "cut", "name": "切菜", "own": "healthy",
             "depends_on": [{"flow": "KNIFE", "step": "sharpen", "why": "刀没磨好切不动"}]},
            {"id": "prep", "name": "备菜", "own": "healthy"},
            {"id": "cook", "name": "炒菜", "own": "healthy"},
            {"id": "eat", "name": "吃饭", "own": "healthy",
             "bound_with": [{"flow": "DRINK", "step": "drink", "why": "没水喝这顿饭不算吃好"}]}]},
        {"id": "FRUIT", "name": "洗水果", "steps": [
            {"id": "start", "name": "开始", "own": "healthy", "after": []},
            {"id": "rinse", "name": "冲洗", "own": "healthy"},
            {"id": "plate", "name": "装盘", "own": "healthy"}]},
        {"id": "KNIFE", "name": "菜刀", "steps": [
            {"id": "find", "name": "找刀", "own": "healthy", "after": []},
            {"id": "sharpen", "name": "磨刀", "own": "healthy"}]},
        {"id": "DRINK", "name": "喝水", "steps": [
            {"id": "boil", "name": "烧水", "own": "healthy", "after": []},
            {"id": "pour", "name": "倒水", "own": "healthy"},
            {"id": "drink", "name": "喝水", "own": "healthy"}]},
    ]


def run(breaks):
    flows = kitchen()
    for fid, sid, st in breaks:
        for f in flows:
            if f["id"] != fid:
                continue
            for s in f["steps"]:
                if s["id"] == sid:
                    s["own"] = st
    node, order, edges = F.build_graph(flows)
    F.propagate(node, order)
    return node, edges


def eff(node, fid, sid):
    return node[(fid, sid)]["eff"]


class AllHealthyTest(unittest.TestCase):
    def test_nothing_broken_means_nothing_propagates(self):
        """★ 先证明它不是恒红的 —— 传导引擎最容易的失败方式是把所有东西都染红。"""
        node, _ = run([])
        for key, n in node.items():
            self.assertEqual(n["eff"], "healthy", "%s 不该被染红" % (key,))
            self.assertEqual(n["causes"], [])
        self.assertEqual(F.blast_radius(node), [])


class SequentialPropagationTest(unittest.TestCase):
    """流内顺序:上一步拿不到东西,后面全都跑不了。这是「纵向切片」的核心。"""

    def test_break_propagates_to_every_later_step(self):
        node, _ = run([("EAT", "wash", "blocked")])
        for sid in ("cut", "prep", "cook", "eat"):
            self.assertNotEqual(eff(node, "EAT", sid), "healthy",
                                "洗菜断了,%s 不可能还是通的" % sid)
        self.assertEqual(eff(node, "EAT", "buy"), "healthy", "洗菜之前的步骤不该被牵连")

    def test_own_state_is_never_overwritten(self):
        """★ 传导出来的是 eff(实际可达性),自报/实测的 own 必须原样保留 ——
        「它自己声称通、但上游断了」这件事本身就是要看的信息。"""
        node, _ = run([("EAT", "wash", "blocked")])
        n = node[("EAT", "cook")]
        self.assertEqual(n["own"], "healthy")
        self.assertNotEqual(n["eff"], "healthy")

    def test_degraded_does_not_block_downstream(self):
        """有缺陷但没断 ≠ 断。degraded 传导下去会造出一整片假红。"""
        node, _ = run([("EAT", "wash", "degraded")])
        self.assertEqual(eff(node, "EAT", "eat"), "healthy")


class DependsOnTest(unittest.TestCase):
    """我等别人:上游那条流的结果没出来,我这步就不能通。"""

    def test_upstream_flow_blocks_me(self):
        node, _ = run([("KNIFE", "sharpen", "blocked")])
        self.assertNotEqual(eff(node, "EAT", "cut"), "healthy")
        c = node[("EAT", "cut")]["causes"][0]
        self.assertEqual((c["flow"], c["step"], c["kind"]), ("KNIFE", "sharpen", "depends_on"))
        self.assertIn("刀没磨好", c["why"])

    def test_dependency_is_directional_not_symmetric(self):
        """★ 方向必须是单向的:我依赖菜刀,菜刀不依赖我。
        做成对称的话,任何一处坏都会把全图染红。"""
        node, _ = run([("EAT", "cut", "blocked")])
        self.assertEqual(eff(node, "KNIFE", "sharpen"), "healthy")


class ProvidesTest(unittest.TestCase):
    """我供给别人:我这步断了,下游那条流**开不了工** —— 影响面比 depends_on 大得多。"""

    def test_my_break_blocks_the_whole_downstream_flow(self):
        node, _ = run([("EAT", "wash", "blocked")])
        for sid in ("start", "rinse", "plate"):
            self.assertNotEqual(eff(node, "FRUIT", sid), "healthy",
                                "洗菜供给洗水果的开始,%s 应被整条带断" % sid)
        c = node[("FRUIT", "start")]["causes"][0]
        self.assertEqual(c["kind"], "provides")

    def test_downstream_break_does_not_climb_back(self):
        node, _ = run([("FRUIT", "rinse", "blocked")])
        self.assertEqual(eff(node, "EAT", "wash"), "healthy")


class BoundWithTest(unittest.TestCase):
    """强绑定:对称。任一边不成立,两边都算业务未达成 —— 谁也不是谁的上游。"""

    def test_binding_propagates_both_ways(self):
        a, _ = run([("DRINK", "boil", "blocked_by_input")])
        self.assertNotEqual(eff(a, "EAT", "eat"), "healthy", "没水喝,这顿饭不算吃好")
        b, _ = run([("EAT", "cook", "blocked")])
        self.assertNotEqual(eff(b, "DRINK", "drink"), "healthy", "饭没做成,绑定的喝水也不算达成")

    def test_binding_does_not_touch_unrelated_steps(self):
        """绑定只作用在被绑的那一步上,不能顺着把对方整条流染红。"""
        node, _ = run([("DRINK", "boil", "blocked_by_input")])
        for sid in ("buy", "wash", "cut", "prep", "cook"):
            self.assertEqual(eff(node, "EAT", sid), "healthy",
                             "%s 与喝水无关,不该被绑定牵连" % sid)


class BlastRadiusTest(unittest.TestCase):
    def test_root_cause_counts_steps_and_flows(self):
        node, _ = run([("EAT", "wash", "blocked")])
        br = F.blast_radius(node)
        self.assertEqual(len(br), 1, "只有真正自己坏的那一步才算根因")
        self.assertEqual((br[0]["flow"], br[0]["step"]), ("EAT", "wash"))
        self.assertGreaterEqual(br[0]["flows"], 3, "吃饭 + 洗水果 + 喝水")

    def test_derived_breaks_are_not_counted_as_root_causes(self):
        """★ 被传导坏的不算根因,否则同一个根因会被重复计好几遍,
        待办列表里全是同一件事的回声。"""
        node, _ = run([("KNIFE", "sharpen", "blocked")])
        br = F.blast_radius(node)
        self.assertEqual([(x["flow"], x["step"]) for x in br], [("KNIFE", "sharpen")])

    def test_policy_block_is_radius_but_not_action(self):
        """按规定不通照样挡住下游(下游确实拿不到东西),但**不需要任何人去修**。
        两种语义必须分开,否则要么漏判阻塞、要么把「不用管」排进待办。"""
        node, _ = run([("KNIFE", "sharpen", "blocked_by_policy")])
        br = F.blast_radius(node)
        self.assertGreater(br[0]["steps"], 0, "按规定不通,下游同样拿不到东西")
        self.assertFalse(br[0]["needs_action"], "但它不需要任何人做事")


class CycleSafetyTest(unittest.TestCase):
    def test_mutual_dependency_terminates(self):
        """互相依赖(登记写错时很容易出现)不能把采集器挂死。"""
        flows = [
            {"id": "A", "name": "A", "steps": [
                {"id": "x", "name": "x", "own": "blocked", "after": [],
                 "depends_on": [{"flow": "B", "step": "y"}]}]},
            {"id": "B", "name": "B", "steps": [
                {"id": "y", "name": "y", "own": "healthy", "after": [],
                 "depends_on": [{"flow": "A", "step": "x"}]}]},
        ]
        node, order, _ = F.build_graph(flows)
        F.propagate(node, order)
        self.assertNotEqual(node[("B", "y")]["eff"], "healthy")

    def test_dangling_reference_is_ignored_not_crashed(self):
        """指向不存在的流/步(登记写错)只能被忽略,不能炸掉整轮采集。"""
        flows = [{"id": "A", "name": "A", "steps": [
            {"id": "x", "name": "x", "own": "healthy", "after": [],
             "depends_on": [{"flow": "NOPE", "step": "nope"}]}]}]
        node, order, _ = F.build_graph(flows)
        F.propagate(node, order)
        self.assertEqual(node[("A", "x")]["eff"], "healthy")



class MultipleConstraintsTest(unittest.TestCase):
    """★ 一个步骤可能同时受多条约束,不能只留一条。

    坑的来源(KMFA 2026-07-27 在自己那份登记里查出来的):
    `BL-PAYROLL-STD / deliver` 的理由只写了「测试期禁群」(**临时**,owner 授权即解除),
    而「工资为敏感数据只进私有库」(**永久**)被塞进了 known_defects。
    后果是:owner 一授权发群,临时理由消失、这一格转绿 —— **而永久约束还在**。
    临时理由掩盖了永久理由,而页面上看着完全正常。
    """

    def _step(self, cons, own="healthy"):
        return {"id": "x", "name": "x", "own": own, "after": [], "constraints": cons}

    def test_own_is_worst_of_all_constraints(self):
        own, cons, _ = F.resolve_constraints(self._step([
            {"kind": "blocked_by_policy", "permanent": True, "why": "只进私有库"},
            {"kind": "blocked_by_input", "permanent": False, "why": "测试期禁群"}]))
        self.assertEqual(own, "blocked_by_input", "要取最差的那个,不是最后一个")
        self.assertEqual(len(cons), 2, "两条约束都要保留")

    def test_lifting_the_temporary_one_does_not_turn_it_green(self):
        """★ 核心:解除临时约束后,永久约束必须仍然把它按住。"""
        after = F.resolve_constraints(self._step([
            {"kind": "blocked_by_policy", "permanent": True, "why": "只进私有库"}]))[0]
        self.assertNotEqual(after, "healthy", "永久约束还在,不能转绿")

    def test_masked_permanent_is_flagged_in_advance(self):
        """同时存在临时与永久约束时提前标出来 —— 这是唯一会「假转绿」的组合。"""
        self.assertTrue(F.resolve_constraints(self._step([
            {"kind": "blocked_by_policy", "permanent": True},
            {"kind": "blocked_by_input", "permanent": False}]))[2])
        self.assertFalse(F.resolve_constraints(self._step([
            {"kind": "blocked_by_policy", "permanent": True}]))[2],
            "只有永久约束时不存在被掩盖的问题")
        self.assertFalse(F.resolve_constraints(self._step([]))[2])

    def test_constraints_propagate_like_any_other_block(self):
        flows = [{"id": "A", "name": "A", "steps": [
            {"id": "x", "name": "x", "own": "healthy", "after": [],
             "constraints": [{"kind": "blocked_by_policy", "permanent": True,
                              "why": "只进私有库"}]},
            {"id": "y", "name": "y", "own": "healthy"}]}]
        node, order, _ = F.build_graph(flows)
        F.propagate(node, order)
        self.assertNotEqual(node[("A", "y")]["eff"], "healthy",
                            "约束造成的阻断同样要往下游传")

    def test_no_constraints_means_state_is_untouched(self):
        """没有约束时不能凭空改状态 —— 否则整表被这个机制染一遍。"""
        self.assertEqual(F.resolve_constraints(self._step([], own="healthy"))[0], "healthy")




class EvidenceSplitTest(unittest.TestCase):
    """★ 证据必须分开「测得的」与「推出的」。

    来源(KMFA 2026-07-27 实测):DEF-KMFA-001 的 desc 把两类写进同一句 ——
      测得的:劳务费约占生产成本八成,其中约七成记在「不分项目」占位下
      推出的:所以项目成本算不出来
    后来输入门禁给出 INPUT_SUFFICIENT,**推论被推翻而测量仍然成立**。
    因为混在一段里,消费方只能整段取,也就只能整段错。

    与「一个步骤只能挂一条约束」是同一个病:
    把性质不同的东西塞进同一个字段,就只能整体取、整体错。
    """

    def test_measured_survives_when_inference_is_retracted(self):
        before = F.split_evidence({"evidence": {
            "measured": "劳务费约占生产成本八成,其中约七成记在「不分项目」占位下",
            "inferred": "所以项目成本算不出来"}})
        after = F.split_evidence({"evidence": {"measured": before["measured"]}})
        self.assertEqual(after["measured"], before["measured"], "推论被推翻,测量必须原样留下")
        self.assertEqual(after["inferred"], [], "推论已撤")

    def test_legacy_blob_is_not_guessed_apart(self):
        """★ 旧格式是一整段文字时,**不许猜哪句是测量哪句是推论** ——
        猜错就是替被测方编事实。如实标为未拆分。"""
        out = F.split_evidence({"evidence": "劳务费八成,所以算不出来"})
        self.assertFalse(out["split"])
        self.assertEqual(out["measured"], [])
        self.assertEqual(out["inferred"], [])
        self.assertIn("劳务费八成", out["raw"])

    def test_both_kinds_are_kept(self):
        out = F.split_evidence({"evidence": {"measured": ["a", "b"], "inferred": ["c"]}})
        self.assertEqual((out["measured"], out["inferred"]), (["a", "b"], ["c"]))
        self.assertTrue(out["split"])

    def test_missing_evidence_is_empty_not_crash(self):
        out = F.split_evidence({})
        self.assertFalse(out["split"])
        self.assertEqual(out["raw"], "")



class RepairGainTest(unittest.TestCase):
    """★ 「挡住多少」会**高估**修复收益 —— 被挡住的步里有一部分同时被别的原因挡着,
    把这一处修好也不会松。排待办必须用「修好能松开多少」。
    """

    def test_gain_excludes_steps_blocked_by_something_else_too(self):
        """切菜同时等菜刀,也在洗菜之后。只修菜刀,切菜之后那截仍被洗菜挡着。"""
        node, _ = run([("KNIFE", "sharpen", "blocked"), ("EAT", "wash", "blocked")])
        rad = {(x["flow"], x["step"]): x["steps"] for x in F.blast_radius(node)}
        flows = kitchen()
        for f in flows:
            for st in f["steps"]:
                if (f["id"], st["id"]) in (("KNIFE", "sharpen"), ("EAT", "wash")):
                    st["own"] = "blocked"
        gain = {(x["flow"], x["step"]): x["frees"] for x in F.repair_gain(flows, node)}
        self.assertLess(gain[("KNIFE", "sharpen")], rad[("KNIFE", "sharpen")],
                        "只修菜刀不会把洗菜挡住的那截也松开,收益必须小于阻塞面")

    def test_gain_equals_radius_when_it_is_the_only_cause(self):
        node, _ = run([("KNIFE", "sharpen", "blocked")])
        flows = kitchen()
        for f in flows:
            if f["id"] == "KNIFE":
                for st in f["steps"]:
                    if st["id"] == "sharpen":
                        st["own"] = "blocked"
        g = [x for x in F.repair_gain(flows, node) if x["step"] == "sharpen"][0]
        r = [x for x in F.blast_radius(node) if x["step"] == "sharpen"][0]
        self.assertEqual(g["frees"], r["steps"] + 1, "唯一根因时,松开的是它自己加上被它挡住的")

    def test_no_roots_means_no_rows(self):
        node, _ = run([])
        self.assertEqual(F.repair_gain(kitchen(), node), [])



class SingleAuthorityTest(unittest.TestCase):
    """★ 同一套规则不得有第二份实现。

    实测教训:此前页面里有一份 JS 重实现,而压缩数据给它时字段被改名
    (depends_on/provides/bound_with → dep/prov/bind)。同一份真实数据,
    引擎算出 26 条边 / 100 步不通,页面那份算出 0 条边 / 57 步不通 ——
    两边都能自证,**没有唯一真值可对**,比假绿更难查。
    六位独立评审全体漏掉,是隔离反证角色抓出来的。
    """

    def test_payload_carries_computed_results_not_raw_relations(self):
        """页面拿到的必须是**算完的结果**;它不该有机会自己推导。"""
        out = F.render_payload(kitchen())
        self.assertIn("nodes", out)
        n = out["nodes"]["EAT||cut"]
        for key in ("own", "eff", "causes", "blocks", "self_broken", "needs_action"):
            self.assertIn(key, n, "%s 必须由引擎算好送过去" % key)

    def test_rendered_flows_do_not_leak_relation_details(self):
        """flows 里只留渲染要用的计数,不再带 depends_on/provides/bound_with 原文 ——
        带了就等于给页面留了自己重算的原料。"""
        out = F.render_payload(kitchen())
        for f in out["flows"]:
            for s in f["steps"]:
                for forbidden in ("depends_on", "provides", "bound_with", "after", "constraints"):
                    self.assertNotIn(forbidden, s)

    def test_scenarios_are_precomputed_not_recomputed(self):
        """连交互沙盘也预先算好:按钮只切快照,不触发任何推导。"""
        out = F.render_payload(kitchen(), scenarios={
            "knife": [("KNIFE", "sharpen", "blocked")]})
        self.assertIn("knife", out["scenarios"])
        s = out["scenarios"]["knife"]
        self.assertNotEqual(s["EAT||cut"]["eff"], "healthy")
        self.assertNotEqual(s["EAT||eat"]["eff"], "healthy", "流内多跳必须已经算进去")
        self.assertNotEqual(s["DRINK||drink"]["eff"], "healthy", "强绑定必须已经算进去")
        self.assertEqual(out["nodes"]["EAT||cut"]["eff"], "healthy", "基准快照不受影响")

    def test_bilateral_declaration_is_deduped(self):
        """★ 同一条现实关系两边都写时,edges 与 blocks 各记一遍,
        页面会报「我挡住了 2 步」而实际只有 1 步。"""
        flows = [
            {"id": "A", "name": "A", "steps": [
                {"id": "x", "name": "x", "own": "blocked", "after": [],
                 "provides": [{"flow": "B", "step": "y", "why": "同一条关系"}]}]},
            {"id": "B", "name": "B", "steps": [
                {"id": "y", "name": "y", "own": "healthy", "after": [],
                 "depends_on": [{"flow": "A", "step": "x", "why": "同一条关系"}]}]},
        ]
        out = F.render_payload(flows)
        self.assertEqual(len(out["nodes"]["A||x"]["blocks"]), 1,
                         "同一个下游只能算一次")
        # ★ blocks 与 edges 是两处独立的去重,必须各测各的 ——
        #   先前只断言了 blocks,把 edges 的去重删掉时这条测试照样全绿(装饰性断言)。
        self.assertEqual(len(out["edges"]), 1,
                         "同一条现实关系在 edges 里也只能有一条,否则页面会画两条线")

    def test_totals_are_self_consistent(self):
        out = F.render_payload(kitchen())
        self.assertEqual(out["totals"]["steps"], len(out["nodes"]))
        self.assertEqual(out["totals"]["own_bad"],
                         sum(1 for v in out["nodes"].values() if v["self_broken"]))



class PayloadFidelityTest(unittest.TestCase):
    """★ 页面凭什么这么判,必须能被复核。

    上一版把 payload 砍成了「渲染最小集」,结果整块丢掉:
    自报 vs 实测(「自报的绿 ≠ 实测的绿」这条主线)、证据原文、只有弱证据、
    自报与实测不符、挂的缺陷、违反耦合。砍完之后页面只剩颜色,
    读者没有任何办法追问「凭什么」。
    """

    def _flows(self):
        return [{"id": "F", "name": "流", "project": "P", "priority": "P0",
                 "note": "备注", "since": "2026-07-01", "verified": 1, "cells_n": 2,
                 "steps": [
                     {"id": "a", "name": "甲", "own": "healthy", "after": [],
                      "declared": "healthy", "measured": "healthy",
                      "evidence": "探针实测 200", "meaning": "这一段做什么",
                      "weak": False, "mismatch": False,
                      "from_external": [{"id": "X", "name": "外部甲", "party": "外部"}]},
                     {"id": "b", "name": "乙", "own": "degraded",
                      "declared": "healthy", "measured": "degraded",
                      "evidence": "日志三天没写", "weak": True, "mismatch": True,
                      "coupling_violation": ["上游X"],
                      "defect": {"id": "DEF-1", "desc": "已知缺陷", "since": "2026-07-02"}}]}]

    def test_every_judgement_input_survives_into_payload(self):
        n = F.render_payload(self._flows())["nodes"]["F||b"]
        for k, want in [("declared", "healthy"), ("measured", "degraded"),
                        ("evidence", "日志三天没写"), ("weak", True), ("mismatch", True)]:
            self.assertEqual(n[k], want, "%s 必须保留 —— 它是这一格的判断依据" % k)
        self.assertEqual(n["defect"]["id"], "DEF-1")
        self.assertEqual(n["coupling_violation"], ["上游X"])

    def test_self_report_vs_measured_is_distinguishable(self):
        """★ 这条主线不能再丢:自报说通、实测说不通,页面必须能分开显示。"""
        n = F.render_payload(self._flows())["nodes"]["F||b"]
        self.assertNotEqual(n["declared"], n["measured"])
        self.assertTrue(n["mismatch"])

    def test_flow_level_facts_survive(self):
        f = F.render_payload(self._flows())["flows"][0]
        for k in ("note", "since", "verified", "cells_n", "priority", "repo"):
            self.assertIn(k, f, "流级信息 %s 不能丢" % k)

    def test_totals_cover_the_honesty_metrics(self):
        t = F.render_payload(self._flows())["totals"]
        for k in ("measured", "mismatch", "weak", "coupling_violation",
                  "with_defect", "externals"):
            self.assertIn(k, t, "全域口径 %s 不能丢" % k)
        self.assertEqual(t["measured"], 2)
        self.assertEqual(t["mismatch"], 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
