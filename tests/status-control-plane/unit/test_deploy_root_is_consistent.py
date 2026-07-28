"""仓内所有部署件必须指向**同一个**部署根,而且 ExecStart 指的脚本要真的存在。

实测踩到的坑:v0.0.0.2 任务包带来的 systemd 单元与三个 shell 脚本,全部写死
`/srv/linze-home-hub` —— 那是任务包设想的部署根。本机生产实际是 `/srv/linze/apps`,
而且实测确认:该目录不存在、`linze-status` 用户不存在、`/etc/linze/` 也不存在。
照原样装上去,timer 每次触发都会 `cd` 失败或 ExecStart 找不到文件 ——
而且**失败在 timer 里是安静的**,看板上只会表现为「投影一直不更新」。

单元测试全绿、CLI 手跑也正常,坏的又是「谁去调它」那一层(和
[[test_systemd_execstart_matches_cli]] 抓到的 --push 是同一类)。所以这里不断言某一个
已知路径,而是断言**一致性**:所有部署件只能有一个根,谁再引入第二个根就红。
"""

from __future__ import annotations

from pathlib import Path
import re
import unittest

from test_support import locate
REPO, _, _ = locate()

SYSTEMD_DIR = REPO / "status" / "deploy" / "systemd"
CONTROL_PLANE_DIR = REPO / "status" / "deploy" / "control-plane"

#: 部署根形如 /srv/<something>/<something> 或 /srv/<something>;从绝对路径里提取
DEPLOY_ROOT = re.compile(r"(/srv/[A-Za-z0-9._-]+(?:/[A-Za-z0-9._-]+)?)/status/")
#: 已知被移除的、任务包设想但本机不存在的根
RETIRED_ROOTS = ("/srv/linze-home-hub",)


def _unit_directives(unit: Path, name: str) -> list[str]:
    return [line.split("=", 1)[1].strip()
            for line in unit.read_text(encoding="utf-8").splitlines()
            if line.strip().startswith(f"{name}=")]


def _roots_in(text: str) -> set[str]:
    return set(DEPLOY_ROOT.findall(text))


class DeployRootConsistencyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.units = sorted(SYSTEMD_DIR.glob("*.service")) + sorted(SYSTEMD_DIR.glob("*.timer"))
        cls.scripts = sorted(CONTROL_PLANE_DIR.glob("*.sh"))

    def test_there_are_units_and_scripts_to_check(self):
        self.assertTrue(self.units, "一个 systemd 单元都没有,这条测试就是空转")
        self.assertTrue(self.scripts, "一个部署脚本都没有,这条测试就是空转")

    def test_all_units_share_one_deploy_root(self):
        roots: dict[str, set[str]] = {}
        for unit in self.units:
            found = _roots_in(unit.read_text(encoding="utf-8"))
            if found:
                roots[unit.name] = found
        all_roots = set().union(*roots.values()) if roots else set()
        self.assertEqual(
            len(all_roots), 1,
            f"systemd 单元出现了 {len(all_roots)} 个部署根 {sorted(all_roots)};逐单元:{roots}")

    def test_no_retired_root_anywhere(self):
        for path in self.units + self.scripts:
            with self.subTest(file=path.name):
                text = path.read_text(encoding="utf-8")
                for retired in RETIRED_ROOTS:
                    # 注释里可以提这个名字(用来解释为什么废弃),但不能出现在真正的指令/赋值里
                    live = [ln for ln in text.splitlines()
                            if retired in ln and not ln.lstrip().startswith("#")]
                    self.assertEqual(live, [], f"{path.name} 仍在使用已废弃的部署根 {retired}:{live}")

    def test_every_execstart_script_exists_in_repo(self):
        checked = 0
        for unit in self.units:
            for exec_start in _unit_directives(unit, "ExecStart"):
                target = exec_start.split()[0]
                if "/status/deploy/" not in target:
                    continue  # 走 python -m 的由 test_systemd_execstart_matches_cli 管
                checked += 1
                with self.subTest(unit=unit.name, script=target):
                    relative = target.split("/status/", 1)[1]
                    on_disk = REPO / "status" / relative
                    self.assertTrue(on_disk.is_file(), f"{unit.name} 的 ExecStart 指向仓内不存在的 {relative}")
                    self.assertTrue(on_disk.stat().st_mode & 0o111, f"{relative} 没有可执行位")
        self.assertGreater(checked, 0, "没有任何 ExecStart 指向仓内脚本 —— 断言失去意义")

    def test_readwritepaths_stay_under_the_deploy_root(self):
        for unit in SYSTEMD_DIR.glob("*.service"):
            roots = _roots_in(unit.read_text(encoding="utf-8"))
            if not roots:
                continue
            root = next(iter(roots))
            for line in _unit_directives(unit, "ReadWritePaths"):
                for path in line.split():
                    with self.subTest(unit=unit.name, path=path):
                        self.assertTrue(path.startswith(root + "/"),
                                        f"{unit.name} 的 ReadWritePaths {path} 不在部署根 {root} 下")

    def test_scripts_do_not_hardcode_a_deploy_root_default(self):
        """REPO_ROOT 的默认值应当从脚本自身位置推导,而不是写死某个绝对路径。

        写死的那一刻,脚本就只在一种部署形态下正确 —— 而本仓已经因此坏过一次。
        """
        pattern = re.compile(r'^\s*REPO_ROOT="\$\{REPO_ROOT:-(/[^}"]+)\}"', re.M)
        for script in self.scripts:
            with self.subTest(script=script.name):
                hardcoded = pattern.findall(script.read_text(encoding="utf-8"))
                self.assertEqual(hardcoded, [],
                                 f"{script.name} 把 REPO_ROOT 默认值写死成了 {hardcoded}")


if __name__ == "__main__":
    unittest.main()
