#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""换机后「页面上真的看得见」的机器守卫。

起因很值得记:2026-08-10 我(agent)两次向 owner 报告"项目内存/存储的绝对值和百分比
已经加好了",两次都只验了 `snapshot.json` 里有没有那几个字段 —— **没验页面渲不渲染**。
实际情况是 `index.html` 里的 `mem_pct` 全都是**整机**内存,项目级的
`mem_mb / storage_mb / mem_pct / storage_pct` 从头到尾没有任何一行渲染代码,
owner 打开页面当然看不到。是 owner 自己发现的("你的百分比内存存储我没看到")。

同一轮里还有第二个同类问题:供应商卡片写死 "OVH VPS-1",而那台机器当天已经退役关机,
页面顶上于是长期挂着一条"OVH VPS-1 即将扣费,剩 6 天"——指向一台停了的机器。

两件事是同一个毛病:**验了数据管线,没验用户那一端**。所以这里的断言全部打在
`web/index.html` 的渲染代码上,不打在采集器的输出结构上 —— 后者已经有别的测试覆盖了。
"""
import os
import re
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
COLLECTOR = os.path.dirname(HERE)
WEB = os.path.join(os.path.dirname(COLLECTOR), "web", "index.html")
sys.path.insert(0, COLLECTOR)


def web_src():
    with open(WEB, encoding="utf-8") as f:
        return f.read()


def collect_src():
    with open(os.path.join(COLLECTOR, "collect.py"), encoding="utf-8") as f:
        return f.read()


class ProjectResourceIsRenderedTest(unittest.TestCase):
    """项目级资源必须出现在页面渲染代码里,不能只躺在 JSON 里。"""

    def test_table_has_memory_and_storage_headers(self):
        src = web_src()
        # 表头:项目清单那张表必须有这两列
        self.assertIn(">内存</th>", src, "项目表缺「内存」列表头")
        self.assertIn(">存储</th>", src, "项目表缺「存储」列表头")

    def test_row_renderer_reads_project_level_fields(self):
        src = web_src()
        for field in ("p.mem_mb", "p.mem_pct", "p.storage_mb", "p.storage_pct"):
            self.assertIn(field, src, "行渲染没有读取 %s" % field)

    def test_absolute_and_percent_are_both_shown(self):
        # owner 明确要求"绝对数值和百分比都要"——光有一个都不算做到。
        # resCell 同时输出 fmtMB(绝对值) 和 pct(百分比)。
        src = web_src()
        m = re.search(r"const resCell\s*=\s*\(mb,\s*pct[^)]*\)\s*=>\s*\{(.*?)\n  \};", src, re.S)
        self.assertIsNotNone(m, "找不到 resCell 渲染函数")
        body = m.group(1)
        self.assertIn("fmtMB(mb)", body, "没有输出绝对值")
        self.assertIn("pct", body, "没有输出百分比")

    def test_denominator_is_disclosed(self):
        # 百分比不给分母就没法核对配额,页脚必须把整机总量摆出来
        src = web_src()
        self.assertIn("rtResFoot", src)
        self.assertIn("res_base", src, "页脚没有引用 host.res_base 作为分母")

    def test_unclaimed_is_disclosed(self):
        # 各项目之和 ≠ 整机用量时,差额必须看得见,否则会被当成算错
        self.assertIn("未归属", web_src())

    def test_zero_is_not_rendered_as_dash(self):
        # 0 和"没数据"是两回事:0 = 真的没占,— = 没认出来。混在一起会掩盖归属失效,
        # 而"归属失效"正是 2026-08-10 之前四个项目长期显示 0 的原因。
        src = web_src()
        m = re.search(r"const resCell\s*=\s*\(mb,\s*pct[^)]*\)\s*=>\s*\{(.*?)\n  \};", src, re.S)
        self.assertIsNotNone(m)
        self.assertIn("mb==null&&pct==null", m.group(1),
                      "resCell 应当只在两者都为 null 时显示 —,不能把 0 也当没数据")


class ProductionHostIsVps3Test(unittest.TestCase):
    """2026-08-10 起生产是 VPS-3;页面上不能再把 VPS-1 当生产。"""

    def test_no_vps1_as_production_label(self):
        # 只查**当数据用**的地方(字符串字面量),不查注释 —— 注释里为了说清楚这次修的是什么,
        # 必须能引用旧文案"OVH VPS-1 即将扣费"。第一版断言粗暴地查全文,把自己的注释判成违规,
        # 那种测试会逼着后人删掉解释性注释来"过测",是负作用。
        code = "\n".join(
            l for l in collect_src().splitlines()
            if not l.lstrip().startswith("#")
        )
        self.assertNotIn("OVH VPS-1", code,
                         "collect.py 的代码里还把 OVH VPS-1 当生产主机 —— 它已于 2026-08-10 退役")

    def test_vendor_card_named_vps3(self):
        self.assertIn('"name": "OVH VPS-3"', collect_src())

    def test_vendor_cost_not_hardcoded_vps1_price(self):
        # A$7/月 是 VPS-1 的价。把它挂到 VPS-3 名下会造出一个看起来精确的错数字,
        # 比"待登记"难发现得多。
        src = collect_src()
        self.assertNotIn('cost_ovh = "A$7/月"', src, "还在用 VPS-1 的 A$7/月 当 VPS-3 成本")
        self.assertIn("金额待登记", src, "拿不到真实金额时应显式标注待登记")

    def test_renewal_uses_vps3_service_end(self):
        src = collect_src()
        self.assertIn("service_end", src)
        self.assertIn("到期直接停机", src)


class WiringTest(unittest.TestCase):
    """接线必须测 —— 这文件里已经因为"单测测了函数、没测调用点"挂过一次采集器。"""

    def test_inventory_receives_prices(self):
        src = collect_src()
        self.assertIn("def inventory(host, fx, costblk, usage, ext, backup, cert, ovh, ch, prices=None):", src)
        self.assertIn("inventory(host, fx, costblk, usage, ext, backup, cert, ovh_renew, ch, prices)", src,
                      "调用点没有把 prices 传进去,cost_ovh 会拿不到价格")

    def test_collect_module_imports(self):
        # 语法/顶层错误在这里就炸,而不是等到主机上采集器整轮挂掉
        import collect  # noqa: F401


if __name__ == "__main__":
    unittest.main(verbosity=2)
