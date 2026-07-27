# -*- coding: utf-8 -*-
"""业务流 v2 引擎:工作流 = 步骤链;关系 = 步骤级、跨流、分三类。

★ v1 错在哪(owner 指出):它是「基线 × 统一阶段」的**方格表**,
  跨流关系只挂在整条基线上(upstream/downstream),把三种性质完全不同的关系抹平成一种。

★ v2 的模型(用 owner 的例子说明):
    吃饭 = 买菜 → 洗菜 → 切菜 → 备菜 → 炒菜 → 吃饭     ← 每条流有自己的步骤链,长度不一
    洗菜 --provides--> 洗水果.开始                      ← 我这步通了,别的流才能开工(我影响别人)
    切菜 --depends_on--> 菜刀.磨好                      ← 我这步等别的流的结果(别人影响我)
    吃饭 <--bound_with--> 喝水                          ← 强绑定,任一边不成立业务就不算达成

  三者的**传导方向与处置动作都不同**,合成一种就既排不出优先级、也画不出真实阻塞面:
    depends_on  上游断 → 我不能标健康。要催的是**上游那条流**的人。
    provides    我断  → 下游整条流开不了工。要催的是**我自己**,而且影响面比看起来大。
    bound_with  任一边断 → 两边都算业务未达成。**对称**,谁也不是谁的上游。
"""
import json
import re

FLOW_STATES = ("healthy", "degraded", "blocked", "blocked_by_policy",
               "blocked_by_input", "not_built", "unknown")
# 会让后续步骤拿不到东西的状态(degraded 不在内:有缺陷但没断)
BLOCKING = ("blocked", "blocked_by_policy", "blocked_by_input", "not_built")
# 需要有人做事的(policy 不在内:按规定不通,不需要任何人去修)
NEEDS_ACTION = ("blocked", "blocked_by_input", "not_built")
SEV = {"blocked_by_input": 0, "blocked": 1, "degraded": 2, "not_built": 3,
       "unknown": 4, "blocked_by_policy": 5, "healthy": 6}
REL_KINDS = ("depends_on", "provides", "bound_with")
SAFE_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")


def _worse(a, b):
    return a if SEV.get(a, 9) <= SEV.get(b, 9) else b


def split_evidence(step):
    """证据必须分开「测得的」与「推出的」。

    ★ 这条同样是 KMFA 线程实测出来的(2026-07-27):
      他们 DEF-KMFA-001 的 desc 把两类东西写进了同一句话 ——
        测得的:劳务费约占生产成本八成,其中约七成记在「不分项目」占位下
        推出的:所以项目成本算不出来
      后来技能自己的输入门禁给出 `INPUT_SUFFICIENT`,**推论被推翻,而测量仍然成立**
      (推论只看了金蝶一个维度,那条产线的输入其实有八个槽位)。
      因为两类混在一段里,消费方只能整段取 —— 也就只能整段错。

    与「一个步骤只能挂一条约束」是同一个病:
    **把性质不同的东西塞进同一个字段,就只能整体取、整体错。**

    所以:测量与推论分开存;推论被推翻时,测量不受影响;页面上分开显示,
    并明确标出哪些是可被推翻的。
    """
    ev = step.get("evidence")
    if isinstance(ev, dict):
        m = [x for x in (ev.get("measured") or []) if x] if isinstance(
            ev.get("measured"), list) else ([ev["measured"]] if ev.get("measured") else [])
        i = [x for x in (ev.get("inferred") or []) if x] if isinstance(
            ev.get("inferred"), list) else ([ev["inferred"]] if ev.get("inferred") else [])
        return {"measured": m, "inferred": i, "split": True}
    # 旧格式:一整段文字。**不猜哪句是测量哪句是推论** —— 如实标为未拆分。
    return {"measured": [], "inferred": [], "raw": str(ev or ""), "split": False}


def resolve_constraints(step):
    """一个步骤可能同时受**多条**约束,必须全部保留。

    ★ 这个坑是 KMFA 线程 2026-07-27 在自己那份登记里发现的:
      他们 `BL-PAYROLL-STD / deliver` 的理由只写了「测试期禁群」(**临时**,owner 授权即解除),
      而「工资为敏感数据只进私有库」(**永久**)被塞进了 known_defects。
      后果:将来 owner 一授权发群,临时理由消失、这一格转绿 —— **而永久约束还在**。
      临时理由掩盖了永久理由,页面上看着完全正常。

    所以:
      own = 自报状态与全部约束里**最差**的那个;
      约束全量保留,不因为其中一条解除就整格转绿;
      带 permanent 的单独标出 —— 它永远不会因为「等等就好了」而消失。
    """
    cons = [c for c in (step.get("constraints") or []) if isinstance(c, dict)]
    own = step.get("own") or step.get("state") or "unknown"
    for c in cons:
        k = c.get("kind")
        if k in FLOW_STATES:
            own = _worse(own, k)
    perm = [c for c in cons if c.get("permanent")]
    temp = [c for c in cons if not c.get("permanent")]
    # 「解除临时约束后会不会假转绿」—— 这是唯一需要提前警告的组合
    masked = bool(perm and temp)
    return own, cons, masked


def build_graph(flows):
    """把所有流的步骤摊成一张图。节点 = (flow_id, step_id)。

    三种边全部规范化成「上游 → 下游」的有向边 + 一类无向边:
      seq         流内顺序:上一步不通,下一步就拿不到东西
      depends_on  A.step 依赖 B.step  →  边 B.step → A.step
      provides    A.step 供给 B.step  →  边 A.step → B.step
      bound_with  无向,单独存
    """
    node = {}
    order = []
    for f in flows:
        prev = None
        for i, s in enumerate(f["steps"]):
            key = (f["id"], s["id"])
            own, cons, masked = resolve_constraints(s)
            node[key] = {"flow": f["id"], "flow_name": f["name"], "step": s["id"],
                         "name": s.get("name") or s["id"], "idx": i,
                         "own": own, "eff": own, "causes": [], "blocks": [],
                         "bound": [], "constraints": cons,
                         "evidence": split_evidence(s),
                         # 临时约束一解除就会假转绿的格子,提前标出来
                         "masked_permanent": masked}
            order.append(key)
            # 流内顺序:默认串行;显式 after: [] 表示这一步不等前一步
            after = s.get("after")
            if after is None:
                if prev is not None:
                    node[key]["_up"] = [(prev, "seq", None)]
            else:
                node[key]["_up"] = [((f["id"], a), "seq", None) for a in after
                                    if SAFE_ID.match(str(a))]
            # ★ 这里必须存 (flow, step) 元组 —— 只存 step id 的话 node.get(src) 恒为 None,
            #   流内顺序传导会整个失效:上一步断了,后面照样全绿,而且**不报任何错**。
            prev = key
    edges = []
    for f in flows:
        for s in f["steps"]:
            key = (f["id"], s["id"])
            for d in (s.get("depends_on") or []):
                src = (d.get("flow"), d.get("step"))
                if src in node:
                    node[key].setdefault("_up", []).append((src, "depends_on", d.get("why")))
                    edges.append({"s": src, "t": key, "kind": "depends_on", "why": d.get("why") or ""})
            for p in (s.get("provides") or []):
                dst = (p.get("flow"), p.get("step"))
                if dst in node:
                    node[dst].setdefault("_up", []).append((key, "provides", p.get("why")))
                    edges.append({"s": key, "t": dst, "kind": "provides", "why": p.get("why") or ""})
            for b in (s.get("bound_with") or []):
                other = (b.get("flow"), b.get("step"))
                if other in node:
                    node[key]["bound"].append({"flow": other[0], "step": other[1],
                                               "why": b.get("why") or ""})
                    node[other]["bound"].append({"flow": key[0], "step": key[1],
                                                 "why": b.get("why") or ""})
                    edges.append({"s": key, "t": other, "kind": "bound_with",
                                  "why": b.get("why") or ""})
    return node, order, edges


def propagate(node, order):
    """多跳传导,带环保护。

    ★ 传导出来的是 `eff`(实际可达性),**不覆盖 `own`(自报/实测)** ——
      两者都保留,页面上分开显示。自报说通、但上游断了 ⇒ 这一格的真相是不通,
      而「它自己声称通」这件事本身也是要看的信息(登记表和现实脱节的信号)。
    """
    # 先按流内顺序 + 跨流边做拓扑式松弛;有环时最多迭代节点数轮,不会死循环
    for _ in range(len(order) + 1):
        changed = False
        for key in order:
            n = node[key]
            eff = n["own"]
            causes = []
            for (src, kind, why) in n.get("_up", []):
                up = node.get(src)
                if not up:
                    continue
                if up["eff"] in BLOCKING:
                    eff = _worse(eff, "blocked")
                    causes.append({"flow": src[0], "step": src[1], "kind": kind,
                                   "state": up["eff"], "why": why or "",
                                   "name": up["name"], "flow_name": up["flow_name"]})
            sig = (eff, tuple(sorted((c["flow"], c["step"], c["kind"]) for c in causes)))
            if sig != (n["eff"], tuple(sorted((c["flow"], c["step"], c["kind"])
                                              for c in n["causes"]))):
                n["eff"], n["causes"] = eff, causes
                changed = True
        if not changed:
            break
    # 强绑定是**对称**的,单独一轮:任一边不成立,两边都算业务未达成
    for _ in range(len(order) + 1):
        changed = False
        for key in order:
            n = node[key]
            for b in n["bound"]:
                other = node.get((b["flow"], b["step"]))
                if other and other["eff"] in BLOCKING and n["eff"] not in BLOCKING:
                    n["eff"] = _worse(n["eff"], "blocked")
                    n["causes"].append({"flow": b["flow"], "step": b["step"],
                                        "kind": "bound_with", "state": other["eff"],
                                        "why": b["why"], "name": other["name"],
                                        "flow_name": other["flow_name"]})
                    changed = True
        if not changed:
            break
    # 反向索引:我挡住了谁 —— 这是「我影响别人」那一面,v1 完全没有
    for key in order:
        for c in node[key]["causes"]:
            up = node.get((c["flow"], c["step"]))
            if up is not None:
                up["blocks"].append({"flow": key[0], "step": key[1],
                                     "kind": c["kind"], "name": node[key]["name"],
                                     "flow_name": node[key]["flow_name"]})
    return node


def blast_radius(node):
    """每个「自己就坏了」的步骤,实际挡住了多少步、多少条流。
    ★ 只算 own 本身就坏的,不算被传导坏的 —— 否则同一个根因会被重复计好几遍。"""
    out = []
    for key, n in node.items():
        if n["own"] not in BLOCKING:
            continue
        seen, stack = set(), [key]
        while stack:
            k = stack.pop()
            for b in node[k]["blocks"]:
                nk = (b["flow"], b["step"])
                if nk not in seen:
                    seen.add(nk)
                    stack.append(nk)
        out.append({"flow": key[0], "step": key[1], "name": n["name"],
                    "flow_name": n["flow_name"], "state": n["own"],
                    "steps": len(seen), "flows": len({f for f, _ in seen}),
                    "needs_action": n["own"] in NEEDS_ACTION})
    out.sort(key=lambda x: (-x["steps"], x["flow"]))
    return out


def repair_gain(flows, node):
    """修好某一步,**实际能松开多少** —— 这才是排优先级该用的数。

    ★ 「挡住 N 步」会高估修复收益:被它挡住的那些步里,有一部分同时被别的原因挡着,
      把它修好也不会松。实测差距不小(某处 挡住 36 步 → 实际只松 23 步)。
      排待办要用「修好能松开多少」,不能用「挡住多少」。
    """
    import copy
    base = {k: v["eff"] for k, v in node.items()}
    blocked0 = {k for k, v in base.items() if v in BLOCKING}
    out = []
    roots = [k for k, v in node.items() if v["own"] in BLOCKING]
    for key in roots:
        fs = copy.deepcopy(flows)
        for f in fs:
            if f["id"] != key[0]:
                continue
            for st in f["steps"]:
                if st["id"] == key[1]:
                    st["own"], st["state"] = "healthy", "healthy"
                    st["constraints"] = []
        n2, o2, _ = build_graph(fs)
        propagate(n2, o2)
        blocked1 = {k for k, v in n2.items() if v["eff"] in BLOCKING}
        freed = blocked0 - blocked1
        out.append({"flow": key[0], "step": key[1], "name": node[key]["name"],
                    "flow_name": node[key]["flow_name"], "state": node[key]["own"],
                    "frees": len(freed),
                    "frees_flows": len({f for f, _ in freed}),
                    "needs_action": node[key]["own"] in NEEDS_ACTION})
    out.sort(key=lambda x: (-x["frees"], x["flow"]))
    return out


# =========================================================================
# 唯一权威:页面**不许自己算**,只渲染这里算好的东西。
#
# ★ 这条规则是实测教训:此前页面里有一份 JS 重实现,而我把数据压缩给它时
#   顺手把 depends_on/provides/bound_with 改名成了 dep/prov/bind。
#   同一份真实数据,引擎算出 26 条边 / 100 步不通,页面那份算出 0 条边 / 57 步不通。
#   两边都能自证,谁也发现不了 —— 比假绿更难查,因为**没有唯一真值可对**。
#   六位独立评审全体漏掉了这一条,是隔离反证角色抓出来的。
#
#   所以:同一套规则不得有第二份实现。页面收到的是**算完的结果**,
#   连交互式沙盘也改成预先算好的若干快照,按钮只切换快照。
# =========================================================================

def render_payload(flows, scenarios=None):
    """产出页面唯一的数据源。页面对它只做渲染,不做任何推导。"""
    node, order, edges = build_graph(flows)
    propagate(node, order)
    rad = {(x["flow"], x["step"]): x for x in blast_radius(node)}
    gain = repair_gain(flows, node)

    raw = {(f["id"], s["id"]): s for f in flows for s in f["steps"]}

    def pack(node, order, edges):
        # 去重:同一条现实关系两边都写过时,edges/blocks 会各记一遍
        seen_e, ed = set(), []
        for e in edges:
            # ★ depends_on 与 provides 是**同一条边的两种写法**(下游声明 / 上游声明),
            #   去重键里不能带 kind —— 带上就变成两条不同的边,页面画两条线、
            #   「我挡住了 N 步」也翻倍。bound_with 是无向的,两个方向同样只算一条。
            kind = "dir" if e["kind"] in ("depends_on", "provides") else e["kind"]
            k = (e["s"], e["t"], kind)
            rk = (e["t"], e["s"], kind)          # 无向 / 反向声明
            if k in seen_e or rk in seen_e:
                continue
            seen_e.add(k)
            ed.append({"s": list(e["s"]), "t": list(e["t"]),
                       "kind": e["kind"], "why": e.get("why") or ""})
        out = {}
        for k in order:
            n = node[k]
            bl, seen_b = [], set()
            for b in n["blocks"]:
                bk = (b["flow"], b["step"])
                if bk in seen_b:
                    continue
                seen_b.add(bk)
                bl.append(b)
            src = raw.get(k, {})
            out["%s||%s" % k] = {
                "flow": n["flow"], "flow_name": n["flow_name"], "step": n["step"],
                "name": n["name"], "own": n["own"], "eff": n["eff"],
                "causes": n["causes"], "blocks": bl,
                "self_broken": n["own"] in BLOCKING,
                "needs_action": n["own"] in NEEDS_ACTION,
                # ★ 下面这些是「这一格凭什么这么判」的全部依据。
                #   上一版把它们砍掉了 —— 砍掉之后页面只剩颜色,读者没有任何办法复核,
                #   「自报的绿 ≠ 实测的绿」这条主线也随之消失。
                "declared": src.get("declared"), "measured": src.get("measured"),
                "evidence": src.get("evidence") or "",
                "meaning": src.get("meaning") or "",
                "weak": bool(src.get("weak")), "mismatch": bool(src.get("mismatch")),
                "coupling_violation": src.get("coupling_violation") or None,
                "defect": src.get("defect") or None,
                "from_external": src.get("from_external") or [],
            }
        return out, ed

    nodes, ed = pack(node, order, edges)
    ext = externals_index(flows, node)
    ledger = relation_ledger(flows, node, ed)
    scen = {}
    for name, breaks in (scenarios or {}).items():
        fs = json.loads(json.dumps(flows))
        for fid, sid, st in breaks:
            for f in fs:
                if f["id"] != fid:
                    continue
                for s2 in f["steps"]:
                    if s2["id"] == sid:
                        s2["own"] = s2["state"] = st
        n2, o2, e2 = build_graph(fs)
        propagate(n2, o2)
        sn, _ = pack(n2, o2, e2)
        scen[name] = sn
    return {
        "schema": "linze.flow.v2.rendered",
        "flows": [{"id": f["id"], "name": f["name"], "project": f.get("project", ""),
                   "priority": f.get("priority", ""), "repo": f.get("repo", ""),
                   "note": f.get("note", ""), "since": f.get("since", ""),
                   "verified": f.get("verified", 0), "cells_n": f.get("cells_n", 0),
                   "steps": [{"id": s["id"], "name": s.get("name") or s["id"]}
                             for s in f["steps"]]} for f in flows],
        "nodes": nodes, "edges": ed, "scenarios": scen,
        "externals": ext, "ledger": ledger,
        "radius": {("%s||%s" % (k)): v for k, v in rad.items()},
        "gain": gain,
        "totals": {
            "steps": len(nodes), "flows": len(flows),
            "own_bad": sum(1 for v in nodes.values() if v["own"] in BLOCKING),
            "eff_bad": sum(1 for v in nodes.values() if v["eff"] in BLOCKING),
            "edges": len(ed),
            "measured": sum(1 for v in nodes.values() if v["measured"]),
            "mismatch": sum(1 for v in nodes.values() if v["mismatch"]),
            "weak": sum(1 for v in nodes.values() if v["weak"]),
            "coupling_violation": sum(1 for v in nodes.values() if v["coupling_violation"]),
            "with_defect": sum(1 for v in nodes.values() if v["defect"]),
            "externals": len(ext),
        },
    }


def externals_index(flows, node):
    """外部来源:数据从哪儿进来,以及它断了会连累谁。

    ★ 外部方(钉钉/金蝶/银行/券商行情/arXiv/owner 授权……)**不是业务流** ——
      我们探不到它们内部,也不该假装能探。所以它们不带状态、不参与传导,
      只回答一个问题:**这个东西没了,谁受影响。**
    ★ 必须挂在**步骤**上。此前 9 份登记里 `sources:` 写在顶层、和任何步骤都没关系 ——
      写了等于没写,页面上一条都出不来。挂到步骤才有去向账。
    """
    ex = {}
    for f in flows:
        for st in f["steps"]:
            for e in (st.get("from_external") or []):
                if not isinstance(e, dict) or not e.get("id"):
                    continue
                k = e["id"]
                r = ex.setdefault(k, {"id": k, "name": e.get("name") or k,
                                      "party": e.get("party") or "外部",
                                      "steps": [], "_flows": set(), "_projects": set()})
                n = node.get((f["id"], st["id"]))
                r["steps"].append({"flow": f["id"], "flow_name": f["name"],
                                   "step": st["id"], "name": st.get("name") or st["id"],
                                   "eff": n["eff"] if n else "unknown",
                                   "why": e.get("why") or ""})
                r["_flows"].add(f["id"])
                r["_projects"].add(f.get("project") or "")
    out = []
    for r in ex.values():
        out.append({"id": r["id"], "name": r["name"], "party": r["party"],
                    "steps": r["steps"], "flows": len(r["_flows"]),
                    "projects": sorted(x for x in r["_projects"] if x),
                    "n_steps": len(r["steps"]),
                    "blocked_now": sum(1 for x in r["steps"] if x["eff"] in BLOCKING)})
    out.sort(key=lambda x: (-x["flows"], -x["n_steps"], x["id"]))
    return out


def relation_ledger(flows, node, edges):
    """关系总账:**同一条关系必须在两端各留一条记录**。

    ★ owner 原话:「上下游关系、耦合关系不清楚,需要双向留记录」。
      引擎里 causes(我在等谁)与 blocks(谁在等我)本来就是同一条边的两面,
      但只在点开某一步时才看得到 —— 等于没有账。这里摊成一张表,每条关系一行、
      两端都写清楚,并且**由同一条声明派生**,不可能两边说法不一致。
    """
    proj = {}
    for f in flows:
        for st in f["steps"]:
            proj[(f["id"], st["id"])] = f.get("project") or ""
    rows = []
    for e in edges:
        src, dst = tuple(e["s"]), tuple(e["t"])
        a, b = node.get(src), node.get(dst)
        if not a or not b:
            continue
        pa, pb = proj.get(src, ""), proj.get(dst, "")
        rows.append({
            "kind": e["kind"],
            "from": {"flow": a["flow_name"], "step": a["name"], "eff": a["eff"], "project": pa},
            "to": {"flow": b["flow_name"], "step": b["name"], "eff": b["eff"], "project": pb},
            "why": e.get("why") or "",
            "active": a["eff"] in BLOCKING and b["eff"] in BLOCKING,
            "cross_project": pa != pb,
        })
    rows.sort(key=lambda r: (not r["active"], r["kind"], r["from"]["flow"]))
    return rows
