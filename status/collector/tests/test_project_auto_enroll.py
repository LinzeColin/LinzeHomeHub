"""新建项目自动纳入体系 —— 守卫。

**问题**：在这之前，项目清单是 `collect_github.py` 里写死的 9 条。
新建一个项目，在有人想起来改那一行之前它是**隐形的**：
不在分母里，所以覆盖率不会掉、未登记数不会涨、看板一切正常。

这正是本域反复出现的假绿形态 ——
**被丢掉的东西不参与任何总量校验，所以总量永远对。**

**修法**：扫全部仓的 git tree，凡是有 `<项目>/docs/governance/project.yaml`
的就自动纳入；没发布 `flow.yaml` 的落进 unregistered（红），而不是隐形。

每条把关都配负控：放松任何一条，这里必须变红。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import collect_github as G  # noqa: E402


def _fake_get(trees):
    def get(url, token, timeout=20):
        name = url.split("/repos/LinzeColin/")[1].split("/")[0]
        if name not in trees:
            return None, 404                    # 取不到 tree
        return {"tree": [{"path": p} for p in trees[name]]}, 200
    return get


class Harness(unittest.TestCase):
    def setUp(self):
        self._real = G._get

    def tearDown(self):
        G._get = self._real

    def run_discovery(self, trees, repos=None):
        G._get = _fake_get(trees)
        names = repos if repos is not None else list(trees)
        return G.discover_projects("t", [{"name": n, "default_branch": "main"} for n in names])


class DiscoveryTest(Harness):
    def test_brand_new_project_is_picked_up_without_code_change(self):
        """★ 这条就是 owner 问的那个问题的答案。"""
        lst, meta = self.run_discovery({"NewRepo": ["Shiny/docs/governance/project.yaml"]})
        self.assertIn(("NewRepo", "Shiny"), lst,
                      "新建项目没有被自动发现 —— 它会一直隐形")
        self.assertIn("NewRepo/Shiny", meta["newly_discovered"])

    def test_whole_repo_project_form(self):
        lst, _ = self.run_discovery({"Solo": ["docs/governance/project.yaml"]})
        self.assertIn(("Solo", "."), lst)

    def test_skeleton_dirs_are_not_projects(self):
        """仓自己的骨架目录不是业务项目，扫到也不能算。

        这里让噪声和一个**真项目**同时存在 —— 否则「一个都没认」会走进
        fail-safe 回落分支，测不出「认得对不对」，只测出「什么都没认」。
        """
        lst, meta = self.run_discovery({"R": ["docs/docs/governance/project.yaml",
                                              "tests/docs/governance/project.yaml",
                                              "scripts/docs/governance/project.yaml",
                                              "RealOne/docs/governance/project.yaml"]})
        for bad in ("docs", "tests", "scripts"):
            self.assertNotIn(("R", bad), lst)
        self.assertEqual(meta["newly_discovered"], ["R/RealOne"])

    def test_nested_deeper_than_one_level_is_ignored(self):
        """`a/b/docs/governance/project.yaml` 不是本域的形态，宁可不认也不猜。"""
        lst, _ = self.run_discovery({"R": ["a/b/docs/governance/project.yaml"]})
        self.assertNotIn(("R", "a"), lst)
        self.assertNotIn(("R", "a/b"), lst)


class FailSafeTest(Harness):
    """发现机制坏掉时的方向必须是「看不见」，不能是「没问题」。"""

    def test_total_failure_falls_back_never_returns_empty(self):
        """★ 命门：返回空会让未登记数瞬间归零、看板一片绿。

        真相是「这一轮没看见」，不是「真的没有项目」。
        """
        lst, meta = self.run_discovery({}, repos=["A", "B"])
        self.assertEqual(meta["mode"], "fallback")
        self.assertEqual(lst, list(G.FLOW_PROJECTS_FALLBACK),
                         "发现全部失败时没有回落到兜底清单 —— 项目会集体消失")
        self.assertTrue(lst, "任何情况下都不许返回空清单")
        self.assertEqual(sorted(meta["failed"]), ["A", "B"])

    def test_partial_failure_is_reported_not_swallowed(self):
        lst, meta = self.run_discovery(
            {"Good": ["P/docs/governance/project.yaml"]}, repos=["Good", "Broken"])
        self.assertIn(("Good", "P"), lst)
        self.assertIn("Broken", meta["failed"], "取不到 tree 的仓必须被点名，不能静默跳过")

    def test_fallback_entries_missing_this_round_get_their_own_ledger(self):
        """★ 少一条最容易被当成正常。

        兜底清单里有、这一轮没扫到的，必须单独留去向账 ——
        否则「项目消失了」和「本来就没有」在产物里长得一模一样。
        """
        lst, meta = self.run_discovery({"NewRepo": ["Shiny/docs/governance/project.yaml"]})
        self.assertTrue(meta["in_fallback_but_not_found"],
                        "兜底里的项目这轮一个都没扫到，却没有任何去向记录")
        # 只增不减：兜底清单里的项目仍然留在结果里，不会因为没扫到就蒸发
        for pair in G.FLOW_PROJECTS_FALLBACK:
            self.assertIn(pair, lst)


class WiringTest(unittest.TestCase):
    def test_gather_flows_accepts_a_discovered_list(self):
        """写死的清单只能是兜底，不能是发现机制本身。"""
        import inspect
        sig = inspect.signature(G.gather_flows)
        self.assertIn("projects_list", sig.parameters,
                      "gather_flows 不接受发现结果 = 它仍然只认写死清单")
        self.assertIn("discovery", sig.parameters)

    def test_discovery_meta_is_published(self):
        """发现的口径必须出现在产物里，否则没人能复核这一轮扫了多少、漏了谁。"""
        import inspect
        src = inspect.getsource(G.gather_flows)
        self.assertIn('"discovery": discovery', src)


if __name__ == "__main__":
    unittest.main()
