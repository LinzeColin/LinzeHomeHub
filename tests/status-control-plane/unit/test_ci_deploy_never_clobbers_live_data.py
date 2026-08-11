"""CI 部署不许覆盖线上活数据,也不许用状态码冒充可达性验收。

status 的部署是 rsync 整个 status/ 到主机。仓内的 `data/prices.json` 只是**初始快照**,
线上那份是通过 /admin 编辑的活数据 —— 少一个 `--exclude 'data/'`,一次部署就把 owner
改过的价格库覆盖回旧值,而且不会有任何报错。同理 `private/`、`.secrets/`、`runtime/`。

`--delete` 同样危险:实测 dry-run 显示它会删掉主机上的 collect.log / github.log /
selfheal.log。

第三条是这个站特有的:nginx 的 `try_files … /index.html` 让**任何路径都返回 200**,
实测 `/this-does-not-exist-xyz` 的 digest 与 index.html 完全相同。所以部署验收
**只能比对内容/digest**,谁把它改回看状态码,这条就红。
"""

from __future__ import annotations

from pathlib import Path
import re
import unittest

from test_support import locate
REPO, _, _ = locate()

WORKFLOW = REPO / ".github" / "workflows" / "status-deploy.yml"
GATE = REPO / "status" / "deploy" / "control-plane" / "ci-deploy-gate.sh"

#: 少任意一个,一次部署就可能抹掉线上活数据
REQUIRED_EXCLUDES = ("data/", "private/", ".secrets/", "runtime/", "spool/")


class CiDeploySafetyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workflow = WORKFLOW.read_text(encoding="utf-8")
        cls.gate = GATE.read_text(encoding="utf-8")

    def test_files_exist(self):
        self.assertTrue(WORKFLOW.is_file(), f"缺少 {WORKFLOW}")
        self.assertTrue(GATE.is_file(), f"缺少 {GATE}")
        self.assertTrue(GATE.stat().st_mode & 0o111, "闸门脚本没有可执行位")

    def test_rsync_excludes_every_live_data_dir(self):
        for item in REQUIRED_EXCLUDES:
            with self.subTest(exclude=item):
                self.assertIn(f"--exclude '{item}'", self.workflow,
                              f"部署缺少 --exclude '{item}' —— 会用仓内快照覆盖线上活数据")

    def test_rsync_never_uses_delete(self):
        live = [ln for ln in self.workflow.splitlines()
                if "--delete" in ln and not ln.lstrip().startswith("#")]
        self.assertEqual(live, [], f"部署用了 --delete,会删掉主机上的日志等文件:{live}")

    def test_acceptance_compares_digests_not_status_codes(self):
        self.assertIn("sha256sum", self.workflow, "公网验收没有比对 digest")
        self.assertIn("__deploy_probe_should_fallback__", self.workflow,
                      "缺少「乱写路径必须回落 index」这条反假绿探针")
        # 只用 %{http_code} 判定成败是这个站上的典型假绿
        self.assertNotIn("-w '%{http_code}'", self.workflow,
                         "公网验收在看状态码 —— try_files 让任何路径都 200,状态码没有鉴别力")

    def test_gate_only_allows_rsync_and_finalize(self):
        self.assertIn("rrsync -wo", self.gate, "闸门没有用 rrsync 把 rsync 钉在部署目录内")
        self.assertIn("deploy-finalize", self.gate)
        self.assertRegex(self.gate, r"exit 77", "闸门没有对未知命令返回拒绝码")

    def test_gate_finalize_fails_loudly_when_homepage_contract_is_not_deployed(self):
        """自检必须能红：当前首页的用户可见契约缺失时不得放行。"""
        self.assertIn('grep -q "云平台总览"', self.gate, "闸门没有验首页标题")
        self.assertIn('grep -q ">内存</th>"', self.gate, "闸门没有验项目资源列")
        self.assertIn('grep -q "rtResFoot"', self.gate, "闸门没有验资源页脚")
        self.assertIn('[[ "$index_body" == "$gov_body" ]]', self.gate,
                      "闸门没有验证已删除的治理路径回落到首页")
        self.assertRegex(self.gate, r'exit 7[56]', "闸门自检失败时没有返回非零")
        self.assertIn("container_ip", self.gate,
                      "闸门应直连容器验;打主机前置代理只会拿到 302,等于没验")

    def test_workflow_is_scoped_and_serialised(self):
        self.assertIn("concurrency:", self.workflow, "没有 concurrency,两次部署可能互相覆盖")
        self.assertRegex(self.workflow, r"paths:\s*\n\s*-\s*'status/\*\*'",
                         "没有按 status/** 限定触发路径")


if __name__ == "__main__":
    unittest.main()
