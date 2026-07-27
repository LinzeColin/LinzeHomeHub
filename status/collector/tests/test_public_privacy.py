#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""公开面隐私守卫。

起因是一个**真实泄漏**:`traffic_history.json` 按仓名分桶归档,却被写进了
`data/`——而 `data/` 就是 nginx 的公开根目录,于是私有仓名和它们的流量数字
在公网上可以直接下载(实测 HTTP 200)。公开派生逻辑本身是对的,漏的是原始档的落盘位置。

所以这里守两件事:
  1) 任何**按仓名分桶**的原始归档,路径都不许落在 DATA_DIR 里;
  2) 公开产物 github_public.json 里不许出现任何私有仓名——包括耦合边这种新增字段。
"""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import collect_github as G                                   # noqa: E402

SECRET = sorted(G.PRIVATE_NAMES_GUARD)


class PublicPrivacyTest(unittest.TestCase):
    def test_repo_keyed_archives_are_not_in_public_dir(self):
        pub = os.path.abspath(G.DATA_DIR)
        for label, path in (("traffic_history", G.TRAFFIC_HISTORY),
                            ("commit_history", G.COMMIT_HISTORY)):
            parent = os.path.abspath(os.path.dirname(path))
            self.assertNotEqual(parent, pub,
                                "%s 按仓名分桶,不能落在公开目录 %s" % (label, pub))

    def test_public_derivation_strips_private_names(self):
        priv = {
            "repos": [{"name": "PublicOne", "private": False, "top_lang": "Python",
                       "size_kb": 10, "commits_7d": 1, "commits_30d": 2, "branches": 1,
                       "open_pr": 0, "open_issue": 0, "url": "u", "ci_state": "SUCCESS"}]
                    + [{"name": n, "private": True, "top_lang": "Python", "size_kb": 9,
                        "commits_7d": 1, "commits_30d": 1, "branches": 1, "open_pr": 0,
                        "open_issue": 0, "url": "u", "ci_state": "SUCCESS"} for n in SECRET],
            "subprojects": [{"repo": SECRET[0], "project": "SecretSub", "path": "x/"}],
            "traffic": {"per_repo": [{"name": n, "private": True, "views_14d": 5}
                                     for n in SECRET],
                        "freshness": {"upstream_through": "2026-07-25", "lag_days": 2}},
            "billing": {"available": True, "by_sku": [{"sku": SECRET[0], "qty": 1.0,
                                                       "gross": 1.0, "net": 0.0}],
                        "billed_repos": SECRET + ["PublicOne"]},
            "actions": {"by_repo": [{"name": n, "private": True, "runs_30d": 3}
                                    for n in SECRET]},
            "coupling": {
                "edges": [{"s": SECRET[0], "t": "PublicOne", "rel": "co_change", "w": .9},
                          {"s": SECRET[0], "t": SECRET[1], "rel": "co_change", "w": .8},
                          {"s": "sp:%s/SecretSub" % SECRET[0], "t": SECRET[0],
                           "rel": "contains", "w": 1.0},
                          {"s": "PublicOne", "t": "PublicOne", "rel": "stack", "w": .2}],
                "degree": {n: 1.5 for n in SECRET + ["PublicOne"]},
            },
        }
        G.recompute_totals(priv)
        blob = json.dumps(G.build_public(priv), ensure_ascii=False)
        for name in SECRET:
            self.assertNotIn(name, blob, "私有仓名 %s 漏进了公开产物" % name)
        self.assertIn("PublicOne", blob, "公开仓不该被误杀")

    def test_written_public_file_has_no_private_names(self):
        """走真正的写盘路径(write_all),而不只是内存里的派生结果。"""
        d = tempfile.mkdtemp()
        pub_out, priv_out = G.PUBLIC_OUT, G.PRIVATE_OUT
        try:
            G.PUBLIC_OUT = os.path.join(d, "data", "github_public.json")
            G.PRIVATE_OUT = os.path.join(d, "private", "github.json")
            G.write_all({"repos": [{"name": n, "private": True} for n in SECRET]
                                  + [{"name": "PublicOne", "private": False}],
                         "coupling": {"edges": [{"s": SECRET[0], "t": "PublicOne",
                                                 "rel": "co_change", "w": .9}],
                                      "degree": {}}})
            blob = open(G.PUBLIC_OUT).read()
        finally:
            G.PUBLIC_OUT, G.PRIVATE_OUT = pub_out, priv_out
        for name in SECRET:
            self.assertNotIn(name, blob, "落盘的公开文件里出现了私有仓名 %s" % name)



class UngovernedPrivacyTest(unittest.TestCase):
    """★ 未纳入治理的检出**只能出计数,绝不能出名字**。

    条目里带的是仓名/目录名，而 MetaDatabase / KMOS / AgentDatabase 都是私有仓。
    整份搬到公开面 = 把私有仓名泄到公网 —— 本站已经修过一次同类漏
    （traffic_history.json 曾经公网暴露私有仓名）。
    """

    def test_public_gets_count_only_never_names(self):
        priv = {"repos": [{"name": "PubRepo", "private": False},
                          {"name": "SecretRepo", "private": True}],
                "totals": {}, "ungoverned": {
                    "count": 2, "scanned": 9,
                    "items": [{"repo": "SecretRepo", "dir": "HushHush", "why": "x"},
                              {"repo": "PubRepo", "dir": "Thing", "why": "y"}],
                    "exempt": [{"repo": "SecretRepo", "dir": "Skip", "why": "z"}]}}
        pub = G.build_public(priv)
        blob = json.dumps(pub, ensure_ascii=False)
        self.assertNotIn("SecretRepo", blob, "私有仓名出现在公开面 —— 又泄了一次")
        self.assertNotIn("HushHush", blob, "私有仓里的目录名出现在公开面")
        self.assertNotIn("ungoverned", [k for k in pub if k == "ungoverned"],
                         "整份 ungoverned 被搬进公开面")
        self.assertEqual(pub.get("ungoverned_count"), 2, "计数必须出，否则公开面看不到有问题")
        self.assertEqual(pub.get("ungoverned_scanned"), 9)
        self.assertEqual(pub.get("ungoverned_exempt"), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
