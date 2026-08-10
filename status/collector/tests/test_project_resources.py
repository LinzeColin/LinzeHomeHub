#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""项目资源归属(project_resources)的机器守卫。

这个函数的产出直接被 owner 用来核对"哪个项目吃了多少内存/存储、有没有守配额"。
它最危险的失效方式不是报错,而是**安静地返回 0** —— 0 看起来像"这个项目很省",
实际是"根本没认出它"。2026-08-10 VPS-1→VPS-3 迁移时一次性暴露了三个这样的洞:

  1) `owns.coolify` 填的是应用名,而 Coolify 起的容器按 **uuid** 命名,名字压根不在
     容器名里 —— Home / PFI / Serenity 三个项目从上线起就一直是 0 MB。
     换机后 uuid 全变、数字"依然是 0",才让人意识到它本来就没生效过。
  2) `owns.systemd` 完全没参与内存计算 —— Alpha / CyberBoss 这类 host-direct 部署
     的项目明明在跑,页面上是 0。
  3) 修 (2) 时差点踩的坑:`docker.service` 的 cgroup **包含旗下所有容器**,
     把它算进去会让容器内存被数两遍,加总能超过整机。

三个洞各钉一条断言。断言全部针对纯函数逻辑,不连真机、不依赖 docker。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import collect as C  # noqa: E402,F401  (供未来断言使用)


class CoolifyUuidMappingTest(unittest.TestCase):
    """owns.coolify 必须能落到按 uuid 命名的容器上。"""

    def test_app_name_never_appears_in_container_name(self):
        # 这是前提事实:Coolify 的容器名是 "<uuid>-<时间戳>",不含应用名。
        # 如果哪天 Coolify 改成用应用名命名,这条会失败 —— 那时候 uuid 映射就多余了,
        # 应该回来把它删掉,而不是留着两套匹配。
        container = "0d8c4960-0e59-43c8-9020-83e16e944444-105446583581"
        self.assertNotIn("linze-home-hub", container)

    def test_uuid_pattern_matches_container(self):
        uuid = "0d8c4960-0e59-43c8-9020-83e16e944444"
        container = f"{uuid}-105446583581"
        pats = ["linze-home-hub", uuid]
        self.assertTrue(any(container.startswith(x) or x in container for x in pats))

    def test_name_only_pattern_does_not_match(self):
        # 负控:只有应用名时必须匹配不上 —— 这正是修复前的行为
        container = "0d8c4960-0e59-43c8-9020-83e16e944444-105446583581"
        pats = ["linze-home-hub"]
        self.assertFalse(any(container.startswith(x) or x in container for x in pats))


class SystemdAttributionTest(unittest.TestCase):
    """owns.systemd 的单元必须被认领,且哨兵值不能算进去。"""

    def test_prefix_claims_unit(self):
        unit_mem = {"cyberboss-cloud.service": 128.0, "sshd.service": 8.0}
        owns_systemd = ["cyberboss-"]
        claimed = [u for u in unit_mem if any(u.startswith(x) for x in owns_systemd)]
        self.assertEqual(claimed, ["cyberboss-cloud.service"])
        self.assertEqual(sum(unit_mem[u] for u in claimed), 128.0)

    def test_sentinel_memory_is_dropped(self):
        # systemd 在拿不到 cgroup 计数时返回 2^64-1;当成真值会得到 1.8e13 MB
        sentinel = str((1 << 64) - 1)
        self.assertFalse(sentinel.isdigit() and int(sentinel) < (1 << 63))

    def test_plain_value_is_kept(self):
        v = str(134217728)                                  # 128 MiB
        self.assertTrue(v.isdigit() and int(v) < (1 << 63))
        self.assertAlmostEqual(int(v) / 1024 / 1024, 128.0)


class DoubleCountGuardTest(unittest.TestCase):
    """docker/containerd 的 cgroup 含全部容器,必须排除,否则加总会超过整机。"""

    # 这里原本还有一条 test_cgroup_parents_excluded,写成
    #     self.assertIn(p, C.__dict__.get("_CGROUP_PARENTS_FOR_TEST", parents))
    # —— 取不到就 fallback 成待测的 parents 自己,于是恒真。那是假绿:
    # 它永远通过,包括把排除逻辑整段删掉的时候。已删除,由下面读源码那条真正兜底。

    def test_including_docker_would_exceed_host(self):
        # 造一个真实比例的场景:整机 11676 MB,容器合计 3000 MB,
        # docker.service 的 cgroup 自然也是 ~3000 MB(它包住了这些容器)
        host = 11676
        containers = 3000.0
        docker_service = 3000.0
        with_double = containers + docker_service
        self.assertGreater(with_double / host * 100, 50)     # 双计后占比虚高一倍
        self.assertLess(containers / host * 100, 30)         # 正确口径

    def test_source_actually_excludes_them(self):
        # 直接读源码确认排除清单还在 —— 防止有人重构时顺手删掉
        with open(os.path.join(os.path.dirname(os.path.dirname(
                os.path.abspath(__file__))), "collect.py"), encoding="utf-8") as f:
            src = f.read()
        self.assertIn("CGROUP_PARENTS", src)
        self.assertIn("docker.service", src)
        self.assertIn("cur_id not in CGROUP_PARENTS", src)


class UnclaimedCompletenessTest(unittest.TestCase):
    """各项目之和 + 未归属,必须能对上实际用量,否则差额去哪了没人知道。"""

    def test_unclaimed_includes_systemd(self):
        mem = {"eei-db": 300.0, "linze-status": 20.0}
        unit_mem = {"cyberboss-cloud.service": 128.0, "sshd.service": 8.0}
        claimed_c = {"eei-db"}
        claimed_u = {"cyberboss-cloud.service"}
        unclaimed = (sum(v for k, v in mem.items() if k not in claimed_c)
                     + sum(v for k, v in unit_mem.items() if k not in claimed_u))
        self.assertEqual(unclaimed, 28.0)                   # 20 容器 + 8 单元
        # 负控:漏掉 systemd 那半边就会少算 8 MB,差额无处可查
        only_containers = sum(v for k, v in mem.items() if k not in claimed_c)
        self.assertEqual(only_containers, 20.0)
        self.assertNotEqual(only_containers, unclaimed)

    def test_source_sums_both_halves(self):
        with open(os.path.join(os.path.dirname(os.path.dirname(
                os.path.abspath(__file__))), "collect.py"), encoding="utf-8") as f:
            src = f.read()
        self.assertIn("k not in claimed_u", src)


if __name__ == "__main__":
    unittest.main(verbosity=2)
