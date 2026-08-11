"""UI/UX 版本整合的硬约束:**现有版本的任何信息都不许丢**。

v0.0.0.2 把已批准的冷蓝设计方向套到全站,顺手新增了治理/证据链/基础设施三块。
换皮的风险不是"不好看",而是**换的过程中悄悄少了一块**:少一个区块、少一个入口、
少一条路由,页面照样能开、测试照样全绿,只有真去点的人才发现。

所以这里把整合前(main@c02f3a6,快照在
_protected/uiux-snapshots/linzehomehub-status-web-20260729-c02f3a6)那一版的
**全部信息点**写死成清单,逐条断言仍然在。要删任何一条,必须先改这个清单 ——
让"我要删掉这块信息"变成一个显式动作,而不是一次手滑。已获 owner 明确授权
下线的治理页是唯一例外，测试必须锁住其删除状态，防止旧资产反复回流。
"""

from __future__ import annotations

from pathlib import Path
import re
import unittest

from test_support import locate
REPO, _, _ = locate()

INDEX = REPO / "status" / "web" / "index.html"

# 整合前六个视图的全部区块标题(逐字取自 c02f3a6 的 index.html)
LEGACY_SECTIONS = [
    # 总览
    "需要你处理的事", "容量与升级", "供应商", "活跃度", "关键趋势", "治理登记合规",
    "业务基线纵向切片", "端到端链路", "端到端健康历史", "自动探测到的运行单元",
    # 业务流
    "业务基线矩阵", "已知缺陷", "源级爆炸半径", "健康趋势",
    # 运行
    "项目清单", "本站实时访问", "主机趋势", "耦合关系", "自愈引擎", "最近动作",
    # 成本
    "账单明细", "免费额度用量", "外部服务", "AI 供应商账务",
    # 时间轴
    "续费时间轴", "订阅历史", "盯不住的订阅",
    # GitHub
    "仓库宇宙", "Actions 收费风险", "贡献网格", "工程可视化", "访问流量", "Monorepo 子项目",
]

# 整合前的全部导航入口:六条 hash 路由 + 三个整页出口 + 中枢体检
LEGACY_ROUTES = ["#/", "#/flow", "#/runtime", "#/cost", "#/timeline", "#/github"]
LEGACY_EXITS = {
    "运维健康": "https://uptime.linzezhang.com",
    "价格设置": "/admin",
    "GitHub 私有": "/admin/github",
    "中枢体检": "/hub.html",
}

class NoInformationLossTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.index = INDEX.read_text(encoding="utf-8")

    def test_every_legacy_section_survives(self):
        missing = [name for name in LEGACY_SECTIONS if name not in self.index]
        self.assertEqual(missing, [], f"整合把这些既有区块弄丢了:{missing}")

    def test_legacy_section_list_is_not_silently_shrunk(self):
        """清单本身也要有下限,防止有人靠删清单让测试变绿。"""
        self.assertGreaterEqual(len(LEGACY_SECTIONS), 33)

    def test_every_legacy_route_survives(self):
        for route in LEGACY_ROUTES:
            with self.subTest(route=route):
                page_id = route.removeprefix("#/")
                self.assertRegex(self.index, rf"id:\s*'{re.escape(page_id)}'",
                                 f"PAGES 里少了路由 {route}")

    def test_every_legacy_exit_survives(self):
        for label, href in LEGACY_EXITS.items():
            with self.subTest(exit=label):
                self.assertIn(href, self.index, f"整合把出口「{label}」({href}) 弄丢了")
                self.assertIn(label, self.index, f"整合把出口「{label}」的文案弄丢了")

    def test_github_view_is_still_a_first_class_entry(self):
        """GitHub 是任务包侧完全没有、只有现有版本才有的一块 —— 最容易在"对齐设计稿"时被吃掉。"""
        self.assertRegex(self.index, r"id:\s*'github'")
        self.assertIn("vGithub", self.index)
        for block in ("仓库宇宙", "Actions 收费风险", "贡献网格", "Monorepo 子项目"):
            self.assertIn(block, self.index)

    def test_contribution_heat_ramp_still_has_five_steps(self):
        """贡献网格的 5 档热力是信息(0/低/中/高/最高),换配色不等于可以并档。"""
        for token in ("--h0", "--h1", "--h2", "--h3", "--h4"):
            self.assertIn(token, self.index, f"贡献热力少了 {token}")

    def test_more_popup_entries_keep_menuitem_roles(self):
        """「更多」弹层的每个可点击出口必须保留菜单语义。"""
        popup = re.search(r'<div class="pop" id="morePop".*?</div>', self.index, re.S)
        self.assertIsNotNone(popup, "「更多」弹层不见了")
        for item in re.findall(r"<a [^>]*>", popup.group(0)):
            self.assertIn('role="menuitem"', item, f"弹层里有条目丢了 role=menuitem:{item}")

    def test_owner_removed_governance_surface_stays_removed(self):
        self.assertNotRegex(self.index, r'<a\b[^>]*href=["\']/agent-governance\.html',
                            "已下线路径仍被渲染为可点击链接")
        self.assertNotIn("id:'governance'", self.index)
        for relative in ("agent-governance.html", "agent-governance.css", "agent-governance.js"):
            with self.subTest(file=relative):
                self.assertFalse((INDEX.parent / relative).exists(), f"已下线资产意外回流:{relative}")

    def test_timeline_scrolls_inside_itself_not_the_whole_page(self):
        """订阅历史轨道写死 560px、行标签又挂在轨道外,窄屏会把整页顶出横向滚动。

        实测:375px 视口下整页溢出 208px,基线 c02f3a6 同样如此(既有缺陷,本次修掉)。
        修法是让这一段自己横向滚动并给标签留位;修完 6 个视图整页溢出都是 0,
        且标签没有被裁掉 —— 宽度受限不能靠砍掉信息来解决。
        """
        self.assertIn(".tlScroll{overflow-x:auto", self.index, "时间轴缺少自身的横向滚动容器")
        self.assertRegex(self.index, r'<div class="tlScroll"><div class="tlTrackHist"',
                         "tlTrackHist 没有被包进滚动容器")
        self.assertIn("padding-left:158px", self.index, "轨道没有给左侧行标签留位,标签会被裁掉")

    def test_charts_still_render_without_raf(self):
        """本项目栽过四次的坑:带动画时该环境一帧 rAF 都不触发,图表全白。"""
        self.assertIn("C.defaults.animation=false", self.index)


if __name__ == "__main__":
    unittest.main()
