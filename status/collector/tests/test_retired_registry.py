#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""退役项目不该被要求登记业务流 —— 权威是各仓自己的 governance/projects.yaml。

实测踩到的**假红**:

    CodexProject/WDA 早在 2026-07-13 就被 owner 判定退役
      retirement_reason: OWNER_DECISION_PROJECT_ABANDONED_NO_ACTIVE_OR_USEFUL_THREAD
      reactivation_requires_owner_authorization: true
    但 status 只看 `docs/governance/project.yaml` 存不存在,于是照样把它
    列进「有治理文件但没登记业务流」的红名单。

    我按那条红去给它补 flow.yaml,被 CodexProject 的治理 CI 当场拦下:
      RETIRED_PROJECT_CHANGE: Retired project paths changed without
      Owner-authorized reactivation

★ **假红比假绿更隐蔽**:假绿让人不动,假红让人动错 —— 它会推着人去改一个
  owner 已经判定放弃、且需要授权才能复活的目录。

★ 权威在各仓自己手里,status 不另维护一份退役名单:两份必然漂移。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import collect_github as G                                   # noqa: E402

WDA_REGISTRY = """
projects: []
retired_projects:
  - project_id: "WDA"
    path: "WDA"
    status: "retired"
    retired_at: "2026-07-13"
    retirement_reason: "OWNER_DECISION_PROJECT_ABANDONED_NO_ACTIVE_OR_USEFUL_THREAD"
    reactivation_requires_owner_authorization: true
migrated_projects:
  - project_id: "whkmSalary"
    path: "whkmSalary"
    status: "migrated"
    migrated_to: "KMOS"
"""


class Fake(object):
    """假 GitHub:树按仓给,blob 按 (repo, path) 给。不发任何网络请求。"""

    def __init__(self, trees, blobs=None):
        self.trees, self.blobs = trees, (blobs or {})
        self.tree_calls = []

    def get(self, url, token, *a, **kw):
        repo = url.split("/repos/LinzeColin/")[1].split("/")[0]
        self.tree_calls.append(repo)
        paths = self.trees.get(repo)
        if paths is None:
            return None, None
        return {"tree": [{"path": p} for p in paths]}, None

    def blobs(self, token, keys, kind, chunk=8):
        return {k: self.blobs_data.get(k) for k in keys}


class RetiredProjectsAreNotRequiredToRegister(unittest.TestCase):

    def setUp(self):
        self._get, self._blobs = G._get, G._blobs

    def tearDown(self):
        G._get, G._blobs = self._get, self._blobs

    def _run(self, trees, blobs, repos):
        fake = Fake(trees)
        G._get = fake.get
        G._blobs = lambda token, keys, kind, chunk=8: {k: blobs.get(k) for k in keys}
        return G.discover_projects("tok", repos)

    def test_退役项目不进项目清单(self):
        found, meta = self._run(
            {"CodexProject": ["governance/projects.yaml",
                              "WDA/docs/governance/project.yaml",
                              "GOLDEN_PATH/docs/governance/project.yaml"],
             "KMOS": ["KMFA/docs/governance/project.yaml"]},
            {("CodexProject", "governance/projects.yaml"): WDA_REGISTRY},
            [{"name": "CodexProject", "default_branch": "main"},
             {"name": "KMOS", "default_branch": "main"}])
        self.assertNotIn(("CodexProject", "WDA"), found)
        # ★ 留痕:必须查得到「是谁说它退役的、为什么」
        self.assertTrue(any(r.get("project") == "WDA" and r.get("state") == "已退役"
                            for r in meta["retired_registry"]))
        # 同仓里没退役的照常纳入
        self.assertIn(("KMOS", "KMFA"), found)

    def test_已迁走的项目同样不进(self):
        found, _ = self._run(
            {"CodexProject": ["governance/projects.yaml",
                              "whkmSalary/docs/governance/project.yaml"]},
            {("CodexProject", "governance/projects.yaml"): WDA_REGISTRY},
            [{"name": "CodexProject", "default_branch": "main"}])
        self.assertNotIn(("CodexProject", "whkmSalary"), found)

    def test_兜底清单不得把退役项目塞回来(self):
        """★ 兜底清单是人写死的,它不知道谁退役了。"""
        real = G.FLOW_PROJECTS_FALLBACK
        try:
            G.FLOW_PROJECTS_FALLBACK = list(real) + [("CodexProject", "WDA")]
            found, _ = self._run(
                {"CodexProject": ["governance/projects.yaml",
                                  "WDA/docs/governance/project.yaml"],
                 "KMOS": ["KMFA/docs/governance/project.yaml"]},
                {("CodexProject", "governance/projects.yaml"): WDA_REGISTRY},
                [{"name": "CodexProject", "default_branch": "main"},
                 {"name": "KMOS", "default_branch": "main"}])
            self.assertNotIn(("CodexProject", "WDA"), found,
                             "退役项目被兜底清单塞回来了")
        finally:
            G.FLOW_PROJECTS_FALLBACK = real

    def test_读不到登记时_按未退役处理而不是静默抹掉(self):
        """★ 看不见必须表现为看不见。读不到登记就当没有退役登记,
        项目照常纳入 —— 宁可多列一个待办,也不能因为读不到就把项目抹掉。"""
        found, meta = self._run(
            {"CodexProject": ["governance/projects.yaml",
                              "WDA/docs/governance/project.yaml"],
             "KMOS": ["KMFA/docs/governance/project.yaml"]},
            {},                                   # blob 取不到
            [{"name": "CodexProject", "default_branch": "main"},
             {"name": "KMOS", "default_branch": "main"}])
        self.assertIn(("CodexProject", "WDA"), found)
        self.assertTrue(any("读不到" in r.get("how", "")
                            for r in meta["retired_registry"]))

    def test_登记解析失败时_按未退役处理并留痕(self):
        found, meta = self._run(
            {"CodexProject": ["governance/projects.yaml",
                              "WDA/docs/governance/project.yaml"],
             "KMOS": ["KMFA/docs/governance/project.yaml"]},
            {("CodexProject", "governance/projects.yaml"): "\t: [不是合法 YAML"},
            [{"name": "CodexProject", "default_branch": "main"},
             {"name": "KMOS", "default_branch": "main"}])
        self.assertIn(("CodexProject", "WDA"), found)
        self.assertTrue(any("解析失败" in r.get("how", "")
                            for r in meta["retired_registry"]))

    def test_退役登记只作用于本仓(self):
        """★ 一个仓说 X 退役了,不能把别的仓的同名项目一起判掉。"""
        found, _ = self._run(
            {"CodexProject": ["governance/projects.yaml",
                              "WDA/docs/governance/project.yaml"],
             "KMOS": ["WDA/docs/governance/project.yaml",
                      "KMFA/docs/governance/project.yaml"]},
            {("CodexProject", "governance/projects.yaml"): WDA_REGISTRY},
            [{"name": "CodexProject", "default_branch": "main"},
             {"name": "KMOS", "default_branch": "main"}])
        self.assertNotIn(("CodexProject", "WDA"), found)
        self.assertIn(("KMOS", "WDA"), found)


if __name__ == "__main__":
    unittest.main(verbosity=2)
