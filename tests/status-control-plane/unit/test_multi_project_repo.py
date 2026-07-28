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

from controlplane.collector import _declared_records, _project_key, _runtime_records
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
        """12 个项目必须产出 12 条 project 记录 —— 一条都不能少。

        注意只数 kind == "project":declared 里还会额外带上「被项目引用到的仓」
        那几条 repository 记录(用于和 GitHub 侧对账)。断言要盯住的是
        **项目有没有变少**,把仓也算进总数只会让这条断言失去意义。
        """
        records = _declared_records({"projects": PRODUCTION_SHAPE})
        projects = [r for r in records if r.kind == "project"]
        self.assertEqual(
            len(projects), len(PRODUCTION_SHAPE),
            f"清单塌台:{len(PRODUCTION_SHAPE)} 个项目只剩 {len(projects)} 条记录",
        )

    def test_entity_ids_are_unique(self):
        """records_by_id 不得抛 duplicate —— 这是线上采集跑不起来的直接原因。"""
        records = _declared_records({"projects": PRODUCTION_SHAPE})
        indexed = records_by_id(records)          # 撞 ID 会在这里抛 ValueError
        self.assertEqual(len(indexed), len(records), "declared 内部自己就撞了 ID")
        projects = [r for r in records if r.kind == "project"]
        self.assertEqual(len(projects), len(PRODUCTION_SHAPE))

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


class RuntimeUnitKindTests(unittest.TestCase):
    """同名不同类型的运行单元必须各算各的。"""

    # 实测:linze-status 既是 container 又是 cron 单元,两个都真实存在
    UNITS = {"software": {"units": [
        {"id": "linze-status", "kind": "container", "state": "running"},
        {"id": "linze-status", "kind": "cron", "state": "scheduled"},
        {"id": "linze-github", "kind": "cron", "state": "scheduled"},
    ]}}

    def test_same_name_different_kind_are_distinct(self):
        records = _runtime_records(self.UNITS)
        units = [r for r in records if r.kind == "runtime_unit"]
        self.assertEqual(len(units), 3, "同名不同类型的单元被合并了")
        records_by_id(units)                       # 撞 ID 会抛

    def test_negative_control_name_only_key_collides(self):
        """破坏测试:只用名字做键,必然撞车。"""
        from controlplane.models import stable_id
        ids = {stable_id("runtime", u["id"]) for u in self.UNITS["software"]["units"]}
        self.assertEqual(len(ids), 2, "只用名字做键居然没撞 —— 破坏测试本身坏了")


class RepositoryNamespaceTests(unittest.TestCase):
    """仓与项目分命名空间:有项目的仓不算未登记,没项目的仓仍要报出来。"""

    def _reconcile(self, repos):
        from controlplane.collector import _source_records
        from controlplane.inventory import reconcile_inventories, InventorySnapshot
        status = {"projects": PRODUCTION_SHAPE}
        gh = {"public_repos": [{"name": n} for n in repos]}
        return reconcile_inventories(
            InventorySnapshot(_declared_records(status), True, "r", "s"),
            InventorySnapshot(_source_records(gh), True, "r", "s"),
            InventorySnapshot((), True, "r", "s"),
        )

    def test_repo_hosting_declared_projects_is_not_unregistered(self):
        result = self._reconcile(["MetaDatabase", "LinzeHomeHub", "KMOS"])
        unregistered = [i.name for i in result.items if i.state == "REPOSITORY_UNREGISTERED"]
        self.assertEqual(unregistered, [],
                         f"托管着已登记项目的仓被误报为未登记:{unregistered}")

    def test_repo_without_any_declared_project_is_still_flagged(self):
        """★ 消假红不能把真信号一起消掉。"""
        result = self._reconcile(["MetaDatabase", "NobodyDeclaredThis"])
        unregistered = [i.name for i in result.items if i.state == "REPOSITORY_UNREGISTERED"]
        self.assertEqual(unregistered, ["NobodyDeclaredThis"],
                         "没有任何项目引用的仓应当仍被报出来")

    def test_projects_and_repos_do_not_share_ids(self):
        """仓 MetaDatabase 与项目 MetaDatabase/Nab 必须是两个不同实体。"""
        from controlplane.models import stable_id
        self.assertNotEqual(stable_id("repository", "MetaDatabase"),
                            stable_id("project", "MetaDatabase"))


if __name__ == "__main__":
    unittest.main()
