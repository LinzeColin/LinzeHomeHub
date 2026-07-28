"""验收中心的公开面隐私守卫。

补丁包在 `status/data/acceptance/` 下新增了一个**会被公网直接访问**的 JSON
(https://status.linzezhang.com/data/acceptance/chatgpt_latest.json)。
它自己带了一个 `"public_safe": true` 字段 —— 但那只是**声明**,
仓里原有的隐私守卫覆盖的是快照投影路径,**够不到这个新路径**。

「有个字段说自己安全」不等于「有人验过它安全」。这正是本仓反复踩到的形态:
口径只覆盖子集,却按全局宣称。所以这里补一条真的去读那个文件的守卫。

★ 尺度说明(免得后人以为这里在拦一切):
  `Private-Database` / `LinzeHomeHub` 这类仓名在本仓公开文档与线上快照里
  **本来就已经公开**,不在拦截范围。真正要拦的是三类**只存在于私有面**的东西:
  生产主机地址、服务器绝对路径、任何形状的凭据。
"""
from __future__ import annotations

import json
from pathlib import Path
import re
import unittest

from test_support import locate

REPO, _, _ = locate()
PUBLIC_ACCEPTANCE_DIR = REPO / "status" / "data" / "acceptance"

# 只存在于私有面的东西 —— 出现在公开目录里即违规
FORBIDDEN = [
    (re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"), "疑似生产主机 IP"),
    (re.compile(r"/srv/linze(?:/|\b)"), "服务器绝对路径"),
    (re.compile(r"/etc/(?:cron|systemd)"), "服务器系统路径"),
    (re.compile(r"gh[pousr]_[A-Za-z0-9]{16,}"), "GitHub 令牌"),
    (re.compile(r"github_pat_[A-Za-z0-9_]{20,}"), "GitHub 细粒度令牌"),
    (re.compile(r"\bsk-(?:admin-)?[A-Za-z0-9_-]{16,}"), "OpenAI 形状的密钥"),
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"), "私钥"),
    (re.compile(r"\bBearer\s+[A-Za-z0-9._-]{16,}"), "Bearer 令牌"),
    (re.compile(r"cloudflareaccess\.com"), "CF Access 团队域"),
]


def public_files() -> list[Path]:
    if not PUBLIC_ACCEPTANCE_DIR.is_dir():
        return []
    return sorted(p for p in PUBLIC_ACCEPTANCE_DIR.rglob("*") if p.is_file())


class AcceptancePublicSafetyTests(unittest.TestCase):
    def test_directory_is_covered_at_all(self):
        """守卫必须真的有东西可查 —— 目录空了就等于这条测试在空转。"""
        self.assertTrue(public_files(), "公开验收目录为空,这条守卫会变成摆设")

    def test_no_private_surface_leaks(self):
        for path in public_files():
            text = path.read_text(encoding="utf-8", errors="replace")
            for pattern, label in FORBIDDEN:
                hit = pattern.search(text)
                self.assertIsNone(
                    hit,
                    f"{path.relative_to(REPO)} 泄露{label}:{hit.group(0)[:40] if hit else ''}",
                )

    def test_public_safe_flag_is_not_taken_on_trust(self):
        """★ 文件自称 public_safe 时,守卫必须**真的查过**,不能凭这个字段放行。

        这条断言的意义不是校验字段值,而是钉死一个事实:
        上面那条扫描是无条件执行的,不会因为 public_safe=true 就跳过。
        """
        for path in public_files():
            if path.suffix != ".json":
                continue
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and data.get("public_safe") is True:
                text = path.read_text(encoding="utf-8")
                for pattern, _label in FORBIDDEN:
                    self.assertIsNone(pattern.search(text),
                                      f"{path.name} 自称 public_safe,但实扫命中了违禁模式")

    def test_negative_control_guard_catches_seeded_leak(self):
        """破坏测试:把一条私有路径种进公开目录,守卫必须抓到。

        抓不到就说明上面两条是装饰性的。
        """
        seeded = PUBLIC_ACCEPTANCE_DIR / "_seed_leak.json"
        PUBLIC_ACCEPTANCE_DIR.mkdir(parents=True, exist_ok=True)
        seeded.write_text(
            json.dumps({"public_safe": True, "note": "备份在 /srv/linze/secrets 里"},
                       ensure_ascii=False),
            encoding="utf-8")
        try:
            caught = False
            for path in public_files():
                text = path.read_text(encoding="utf-8", errors="replace")
                if any(p.search(text) for p, _ in FORBIDDEN):
                    caught = True
            self.assertTrue(caught, "★ 种了私有路径进去,守卫却没抓到 —— 它是瞎的")
        finally:
            seeded.unlink(missing_ok=True)

    def test_repo_names_are_deliberately_not_blocked(self):
        """尺度锚点:仓名不在拦截范围,别把这条守卫误当成万能过滤。"""
        sample = "本次没有 authenticated 读取 Private-Database"
        self.assertFalse(any(p.search(sample) for p, _ in FORBIDDEN),
                         "仓名被误拦了 —— 这些名字在公开文档与线上快照里本来就有")


if __name__ == "__main__":
    unittest.main()
