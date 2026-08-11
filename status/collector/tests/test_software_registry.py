#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""软件登记治理的机器守卫。

治理规则:**凡是部署到 OVH 或 Cloudflare 的软件,都必须在登记表里有归属,
并接入实时监控与动态自愈。** 这条规则要能被执行,必须同时满足两件事:

  1) 登记表本身是完整的(每条业务线九段切片都说得清,没有空字段);
  2) 判定逻辑不制造假红 —— 假红比没有告警更糟:一旦习惯了红色,真出事那次也不会有人看。

第 2 点是实测踩出来的,所以这里把每个踩过的坑都钉成断言。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import collect as C                                          # noqa: E402


class RegistryCompletenessTest(unittest.TestCase):
    REQUIRED = ("name", "url", "parts", "host", "db", "store", "deploy", "backup", "agent", "notify")

    def test_every_business_line_is_fully_declared(self):
        for p in C.PROJECTS:
            for k in self.REQUIRED:
                self.assertIn(k, p, "%s 缺字段 %s" % (p.get("name"), k))
                self.assertNotEqual(p[k], "", "%s 的 %s 是空的" % (p["name"], k))

    def test_every_platform_component_declares_owner_and_heal(self):
        for p in C.PLATFORM:
            for k in ("name", "role", "owns", "heal"):
                self.assertIn(k, p, "平台组件 %s 缺 %s" % (p.get("name"), k))
            self.assertTrue(p["owns"], "平台组件 %s 没有声明它拥有哪些单元" % p["name"])

    def test_stage_set_covers_code_to_selfheal(self):
        keys = [k for k, _ in C.STAGES]
        for must in ("code", "ci", "deploy", "run", "entry", "data", "backup", "monitor", "heal"):
            self.assertIn(must, keys, "纵向切片少了 %s 段,端到端就断了" % must)

    def test_cloudflare_hosted_lines_declare_their_cf_units(self):
        for p in C.PROJECTS:
            if (p.get("host") or "").startswith("Cloudflare"):
                self.assertTrue((p.get("owns") or {}).get("cloudflare"),
                                "%s 跑在 Cloudflare,必须登记 CF 单元名" % p["name"])

    def test_cyberboss_is_declared_with_its_linux_units(self):
        cyberboss = next(p for p in C.PROJECTS if p["name"] == "CyberBoss")
        self.assertEqual(cyberboss["url"], "https://cyberboss.linzezhang.com")
        self.assertEqual(cyberboss["owns"].get("systemd"), ["cyberboss-"])
        self.assertIsNotNone(C.SYSTEMD_SERVICE_PATTERN.match("cyberboss-cloud.service"))
        self.assertIsNotNone(C.SYSTEMD_SERVICE_PATTERN.match("cyberboss-cf-tunnel.service"))

    def test_jobhuntbot_online_owns_its_compose_runtime(self):
        project = next(p for p in C.PROJECTS if p["name"] == "JobHuntBot Online")
        self.assertEqual(project["url"], "https://jobhunt.linzezhang.com")
        self.assertEqual(project["repo"], "MetaDatabase")
        self.assertEqual(project["host"], "OVH VPS-3")
        self.assertEqual(project["notify"], "标准 SMTP 注册已开放；真实验收邮件受安全冷却与收件人限速保护")
        self.assertEqual(project["owns"].get("container"), ["jobhuntbot-online-"])


class NoFalseAlarmTest(unittest.TestCase):
    """每一条都对应一次实测出来的误报,不是假想。"""

    def test_timer_driven_oneshot_is_scheduled_not_down(self):
        # Alpha 的盘前自检/账本备份:Type=oneshot + TriggeredBy=timer,平时就该 inactive
        self.assertEqual(C._systemd_state({
            "Id": "alpha-backup.service", "Type": "oneshot", "ActiveState": "inactive",
            "Result": "success", "TriggeredBy": "alpha-backup.timer"}), "scheduled")

    def test_onfailure_template_instance_is_scheduled(self):
        self.assertEqual(C._systemd_state({
            "Id": "alpha-alert@alpha-backup.service.service", "Type": "oneshot",
            "ActiveState": "inactive", "Result": "success", "TriggeredBy": ""}), "scheduled")

    def test_long_running_unit_active_is_active(self):
        self.assertEqual(C._systemd_state({
            "Id": "alpha-supervisor.service", "Type": "simple", "ActiveState": "active",
            "Result": "success", "TriggeredBy": ""}), "active")

    def test_real_failure_is_still_failed(self):
        """别为了消灭误报把真故障也一起消灭了。"""
        self.assertEqual(C._systemd_state({
            "Id": "alpha-supervisor.service", "Type": "simple", "ActiveState": "inactive",
            "Result": "exit-code", "TriggeredBy": ""}), "failed")

    def test_longest_prefix_wins_so_proxy_is_not_swallowed(self):
        """`coolify` 和 `coolify-proxy` 同时登记时,按登记顺序匹配会让 Traefik 变成空线。"""
        reg = [{"name": "Coolify 平台", "owns": {"container": ["coolify"]}},
               {"name": "Traefik 入口", "owns": {"container": ["coolify-proxy"]}}]
        unit = {"kind": "container", "id": "coolify-proxy", "domain": None, "detail": "traefik:v3"}
        self.assertEqual(C._owner_of(unit, reg), "Traefik 入口")
        self.assertEqual(C._owner_of(
            {"kind": "container", "id": "coolify-db", "domain": None, "detail": "pg"}, reg),
            "Coolify 平台")

    def test_domain_match_beats_prefix(self):
        reg = [{"name": "别人", "owns": {"container": ["linze"]}},
               {"name": "Status", "url": "https://status.linzezhang.com", "owns": {}}]
        self.assertEqual(C._owner_of(
            {"kind": "container", "id": "linze-status", "domain": "status.linzezhang.com",
             "detail": "nginx"}, reg), "Status")

    def test_random_named_helper_is_claimed_by_image(self):
        """Coolify 构建辅助容器名是随机串,只能按镜像认领,否则永远是"未登记"。"""
        reg = [{"name": "Coolify 平台", "owns": {"image": ["coolify-helper"]}}]
        self.assertEqual(C._owner_of(
            {"kind": "container", "id": "hpaq26ras3gwigv5k6gpn4vx", "domain": None,
             "detail": "docker.io/coollabsio/coolify-helper:1.0"}, reg), "Coolify 平台")

    def test_unclaimed_unit_stays_unclaimed(self):
        """守卫的意义在于「找得出没人管的东西」,不能因为放宽匹配就把违规也认领了。"""
        reg = [{"name": "Coolify 平台", "owns": {"container": ["coolify"]}}]
        self.assertIsNone(C._owner_of(
            {"kind": "container", "id": "someone-elses-app", "domain": None, "detail": "x"}, reg))

    def test_cyberboss_systemd_units_are_owned_by_its_business_line(self):
        reg = list(C.PROJECTS) + [dict(p, url="") for p in C.PLATFORM]
        for unit_id in ("cyberboss-cloud.service", "cyberboss-cf-tunnel.service"):
            self.assertEqual(C._owner_of(
                {"kind": "systemd", "id": unit_id, "domain": None, "detail": "CyberBoss"}, reg),
                "CyberBoss")


if __name__ == "__main__":
    unittest.main(verbosity=2)
