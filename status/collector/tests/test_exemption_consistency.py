#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""豁免口径一致性:两个扫描器必须用同一份名单。

实测踩到的口径分裂 —— 两份豁免名单都只在两个扫描器中的一个里生效:

    ARCHIVE_REPOS  只在 discover_ungoverned 生效
      ⇒ 同一个 Archive 仓,「没纳入治理」那条赦免它,
        「有治理文件就必须登记业务流」那条照样追它。Archive/EVA_OS 长期挂红。
    NOT_PROJECT    同理
      ⇒ MetaDatabase/LinzeDatabase 早写明「数据目录,不是软件项目」,
        却仍被要求发布 flow.yaml。

**豁免写了不生效,比没写更糟**:看板上是红的,名单上是赦免的,两边都无法据以行动。

★ 还有一条更隐蔽的:扫描时跳过了,却被 `found | FLOW_PROJECTS_FALLBACK`
  原样塞回来 —— 守卫生效了,但下游有条路把它撤销了。下面单独钉死。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import collect_github as G                                   # noqa: E402


class FakeTree(object):
    """假 GitHub 树:只回 project.yaml 路径,不发任何网络请求。"""

    def __init__(self, by_repo):
        self.by_repo = by_repo
        self.asked = []

    def __call__(self, url, token, *a, **kw):
        repo = url.split("/repos/LinzeColin/")[1].split("/")[0]
        self.asked.append(repo)
        paths = self.by_repo.get(repo)
        if paths is None:
            return None, None
        return {"tree": [{"path": p} for p in paths]}, None


class ExemptionsHonoredByBothScanners(unittest.TestCase):

    def setUp(self):
        self._real_get = G._get

    def tearDown(self):
        G._get = self._real_get

    def _discover(self, by_repo, repos):
        fake = FakeTree(by_repo)
        G._get = fake
        found, meta = G.discover_projects("tok", repos)
        return found, meta, fake

    def test_归档仓不进项目清单(self):
        """Archive/EVA_OS 那条红的直接成因。"""
        found, meta, fake = self._discover(
            {"Archive": ["EVA_OS/docs/governance/project.yaml"],
             "KMOS": ["KMFA/docs/governance/project.yaml"]},
            [{"name": "Archive", "default_branch": "main"},
             {"name": "KMOS", "default_branch": "main"}])
        self.assertNotIn(("Archive", "EVA_OS"), found)
        self.assertIn("Archive", meta["archived_skipped"])
        # ★ 跳过要跳得彻底:连树都不该去拉
        self.assertNotIn("Archive", fake.asked)

    def test_NOT_PROJECT_里的目录不进项目清单(self):
        """LinzeDatabase 早就写明不是软件项目,却仍被要求发 flow.yaml。"""
        found, meta, _ = self._discover(
            {"MetaDatabase": ["LinzeDatabase/docs/governance/project.yaml",
                              "FIFA/docs/governance/project.yaml"]},
            [{"name": "MetaDatabase", "default_branch": "main"}])
        self.assertNotIn(("MetaDatabase", "LinzeDatabase"), found)
        self.assertIn("MetaDatabase/LinzeDatabase", meta["exempt_skipped"])
        # ★ 同一个仓里没被豁免的照旧要纳入 —— 别把整仓一起赦免了
        self.assertIn(("MetaDatabase", "FIFA"), found)

    def test_兜底清单不得把豁免项塞回来(self):
        """★ 守卫生效了,但下游 `found | FALLBACK` 有条路能把它撤销。"""
        real = G.FLOW_PROJECTS_FALLBACK
        try:
            # 故意把一个已豁免项放进兜底清单 —— 这正是撤销守卫的那条路
            G.FLOW_PROJECTS_FALLBACK = list(real) + [("MetaDatabase", "LinzeDatabase")]
            found, _, _ = self._discover(
                {"MetaDatabase": ["FIFA/docs/governance/project.yaml"]},
                [{"name": "MetaDatabase", "default_branch": "main"}])
            self.assertNotIn(("MetaDatabase", "LinzeDatabase"), found,
                             "豁免项被兜底清单塞回来了 —— 豁免等于没写")
        finally:
            G.FLOW_PROJECTS_FALLBACK = real

    def test_GitHub自带archived标记也认(self):
        found, meta, _ = self._discover(
            {"OldRepo": ["X/docs/governance/project.yaml"]},
            [{"name": "OldRepo", "default_branch": "main", "archived": True}])
        self.assertNotIn(("OldRepo", "X"), found)
        self.assertIn("OldRepo", meta["archived_skipped"])

    def test_一个都没扫到时仍回落兜底而不是返回空(self):
        """★ 豁免不能把「发现机制坏了」伪装成「真的没有项目」。"""
        found, meta, _ = self._discover(
            {}, [{"name": "Archive", "default_branch": "main"}])
        self.assertEqual(meta["mode"], "fallback")
        self.assertTrue(found, "返回空会让未登记瞬间归零、看板一片绿")

    def test_两个扫描器共用同一份名单对象(self):
        """★ 口径一致靠**共用同一个常量**,不靠两处各抄一份。"""
        src = open(os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), "collect_github.py"), encoding="utf-8").read()
        body = src.split("def discover_projects(")[1].split("\ndef ")[0]
        self.assertIn("ARCHIVE_REPOS", body)
        self.assertIn("NOT_PROJECT", body)


if __name__ == "__main__":
    unittest.main(verbosity=2)
