"""未纳入治理的项目检出 —— 守卫。

**为什么还要这一层**：`discover_projects()` 找的是**已经有** `project.yaml` 的目录。
一个项目在写出治理文件之前，对本站是**彻底隐形**的 ——
不在分母里，所以覆盖率不掉、未登记数不涨、看板一切正常。

实测（2026-07-27）全仓 10 个这样的目录，其中 `MetaDatabase/CyberBoss`
有 **634 个文件**、owner 明确说是在跑的活跃项目，而本站一个字都看不到它。

★ 判定原则：**不自动下结论，强制表态。**
扫到的目录要么补治理文件、要么在 `NOT_PROJECT` 里写明为什么不是项目。
**沉默不是选项** —— 沉默正是它过去能隐形的原因。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import collect_github as G  # noqa: E402


class Harness(unittest.TestCase):
    def setUp(self):
        self._real = G._get

    def tearDown(self):
        G._get = self._real

    def scan(self, trees, dirs, governed=()):
        def fake(url, token, timeout=20):
            n = url.split("/repos/LinzeColin/")[1].split("/")[0]
            if n not in trees:
                return None, 404
            return {"tree": [{"path": p,
                              "type": "tree" if p in dirs.get(n, ()) else "blob"}
                             for p in trees[n]]}, 200
        G._get = fake
        return G.discover_ungoverned("t", [{"name": n} for n in trees], list(governed))


class DetectionTest(Harness):
    def test_active_project_without_governance_is_surfaced(self):
        """★ 这条就是 CyberBoss 那个洞。"""
        r = self.scan({"M": ["CyberBoss", "CyberBoss/README.md", "CyberBoss/AGENTS.md"]},
                      {"M": {"CyberBoss"}})
        self.assertEqual([(x["repo"], x["dir"]) for x in r["items"]], [("M", "CyberBoss")],
                         "没有治理文件的活跃项目没被检出 —— 它会一直隐形")

    def test_already_governed_is_not_reported_twice(self):
        r = self.scan({"M": ["EEI", "EEI/README.md", "EEI/docs/governance/project.yaml"]},
                      {"M": {"EEI"}})
        self.assertEqual(r["items"], [])

    def test_flow_yaml_alone_also_counts_as_governed(self):
        """status 自己只有 flow.yaml 没有 project.yaml，不该被误报。"""
        r = self.scan({"H": ["status", "status/README.md",
                             "status/docs/governance/flow.yaml"]}, {"H": {"status"}})
        self.assertEqual(r["items"], [])

    def test_dir_without_any_marker_is_not_a_project(self):
        """连 README/AGENTS/VERSION 都没有的目录不像项目，不报 —— 避免满屏噪音。"""
        r = self.scan({"M": ["build", "build/x.o"]}, {"M": {"build"}})
        self.assertEqual(r["items"], [])

    def test_skeleton_dirs_excluded(self):
        r = self.scan({"M": ["scripts", "scripts/README.md", "tests", "tests/README.md"]},
                      {"M": {"scripts", "tests"}})
        self.assertEqual(r["items"], [])


class ExemptionTest(Harness):
    """豁免必须是**显式且带理由**的，不能是代码里一句隐式跳过。"""

    def test_explicit_exemption_suppresses_report(self):
        r = self.scan({"AgentDatabase": ["CodexSkills", "CodexSkills/README.md"]},
                      {"AgentDatabase": {"CodexSkills"}})
        self.assertEqual(r["items"], [])

    def test_every_exemption_carries_a_reason(self):
        """★ 没有理由的豁免过两个月就没人知道当初为什么豁免 = 永久黑洞。"""
        for (repo, d), why in G.NOT_PROJECT.items():
            self.assertTrue(str(why).strip(),
                            "%s/%s 被豁免却没写理由" % (repo, d))
            self.assertGreater(len(str(why).strip()), 6,
                               "%s/%s 的豁免理由太敷衍" % (repo, d))

    def test_exemptions_are_published_for_review(self):
        """豁免名单必须出现在产物里 —— 否则没人能复核「谁被放过了」。"""
        r = self.scan({"M": ["X", "X/README.md"]}, {"M": {"X"}})
        self.assertEqual(len(r["exempt"]), len(G.NOT_PROJECT))
        for e in r["exempt"]:
            self.assertTrue(e["why"])


class WiringTest(unittest.TestCase):
    def test_published_in_snapshot(self):
        import inspect
        src = inspect.getsource(G)
        self.assertIn('"ungoverned": ungov', src, "检出结果没有进快照，页面拿不到")
        self.assertIn("discover_ungoverned(token, repo_rows, disc_list)", src,
                      "检出没有被真正调用")


if __name__ == "__main__":
    unittest.main()
