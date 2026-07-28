"""一个仓装多个项目时,清单不许塌台(DA-002:不得 silent shrink)。

本工作间的架构是**新功能默认做子项目,不单独建仓**,所以「多个项目共用一个 repo」
是常态而非异常。生产实测:Nab / PFI / Serenity / EEI / Alpha / ADP / CyberBoss
七个项目都在 MetaDatabase 仓里,Home 与 Status 都在 LinzeHomeHub。

改这一版之前 `_project_key` 把 `repo` 排在最前面,于是 12 个项目塌成 5 个键,
`records_by_id` 直接抛 `duplicate entity_id`,控制面采集整个跑不起来
(线上部署就是卡在这一步)。

★ 这里特别要防的是「把修复做成去重」:去重能让异常消失、让采集跑通,
  但会把 7 个真项目合成 1 个 —— 清单悄悄变小,覆盖率分母跟着缩水,
  看板反而更好看。那是比崩溃更坏的结果,因为它不报错。
  所以下面第一条断言是**数量守恒**:进去几个项目,就得出来几条记录。
"""
from __future__ import annotations

import unittest

from test_support import locate

locate()

from controlplane.collector import _declared_records, _project_key
from controlplane.models import records_by_id


# 取自生产 snapshot.json 的真实形状(12 个项目,repo 大量重复)
PRODUCTION_SHAPE = [
    {"name": "Home", "repo": "LinzeHomeHub"},
    {"name": "Nab", "repo": "MetaDatabase"},
    {"name": "PFI", "repo": "MetaDatabase"},
    {"name": "Serenity", "repo": "MetaDatabase"},
    {"name": "KMFA", "repo": "KMOS"},
    {"name": "Account", "repo": None},
    {"name": "EEI", "repo": "MetaDatabase"},
    {"name": "Alpha", "repo": "MetaDatabase"},
    {"name": "ADP", "repo": "MetaDatabase"},
    {"name": "CyberBoss", "repo": "MetaDatabase"},
    {"name": "Uptime", "repo": None},
    {"name": "Status", "repo": "LinzeHomeHub"},
]


class MultiProjectRepoTests(unittest.TestCase):
    def test_no_silent_shrink(self):
        """12 个项目必须产出 12 条记录 —— 一条都不能少。"""
        records = _declared_records({"projects": PRODUCTION_SHAPE})
        self.assertEqual(
            len(records), len(PRODUCTION_SHAPE),
            f"清单塌台:{len(PRODUCTION_SHAPE)} 个项目只剩 {len(records)} 条记录",
        )

    def test_entity_ids_are_unique(self):
        """records_by_id 不得抛 duplicate —— 这是线上采集跑不起来的直接原因。"""
        records = _declared_records({"projects": PRODUCTION_SHAPE})
        indexed = records_by_id(records)          # 撞 ID 会在这里抛 ValueError
        self.assertEqual(len(indexed), len(PRODUCTION_SHAPE))

    def test_same_repo_projects_stay_distinct(self):
        """同一个仓里的 7 个项目必须是 7 个不同的键,不是 1 个。"""
        same_repo = [r for r in PRODUCTION_SHAPE if r["repo"] == "MetaDatabase"]
        self.assertEqual(len(same_repo), 7, "夹具本身写错了")
        keys = {_project_key(r) for r in same_repo}
        self.assertEqual(len(keys), 7, f"同仓项目被压成 {len(keys)} 个键:{sorted(keys)}")

    def test_key_keeps_repo_relationship(self):
        """键要区分项目,但不能丢掉「它属于哪个仓」这层关系。"""
        self.assertEqual(_project_key({"name": "Nab", "repo": "MetaDatabase"}), "MetaDatabase/Nab")
        self.assertEqual(_project_key({"name": "Account", "repo": None}), "Account")
        self.assertEqual(_project_key({"repo": "OnlyRepo"}), "OnlyRepo")

    def test_negative_control_repo_first_key_would_collapse(self):
        """破坏测试:换回「repo 优先」的旧键,断言上面的守恒断言确实会失败。

        守卫必须能抓到自己被改坏 —— 抓不到就说明这几条断言是摆设。
        """
        def old_key(raw):
            return str(raw.get("repo") or raw.get("project")
                       or raw.get("id") or raw.get("name") or "").strip()

        collapsed = {old_key(r) for r in PRODUCTION_SHAPE}
        self.assertLess(
            len(collapsed), len(PRODUCTION_SHAPE),
            "旧键居然没塌台 —— 那这条破坏测试本身是坏的",
        )
        self.assertEqual(len(collapsed), 5, f"旧键把 12 个压成 {len(collapsed)} 个")


if __name__ == "__main__":
    unittest.main()
