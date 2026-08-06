"""index.html 与 agent-governance.css 必须是**同一套**设计令牌。

index.html 按设计是单文件(内联样式、零外部资源、CSP default-src 'self'),
所以令牌没法 @import 共享,只能在两处各写一份。复制必然漂移 ——
漂移的表现是两个页面看着"差不多"但不是一套东西,而这正是整合要消灭的东西。

这条测试把复制变成受检不变量:逐个令牌比对三个作用域(:root、[data-theme=light]、
[data-theme=dark])的值,对不上就红。
"""

from __future__ import annotations

from pathlib import Path
import re
import unittest

from test_support import locate
REPO, _, _ = locate()

WEB = REPO / "status" / "web"
INDEX = WEB / "index.html"
GOV_CSS = WEB / "agent-governance.css"
HUB_CSS = WEB / "assets" / "hub" / "hub.css"

# 只比对设计系统令牌;--r/--ease 这类几何与缓动也算,因为它们同样决定"看起来是不是一套"。
TOKEN = re.compile(r"(--[a-z0-9-]+)\s*:\s*([^;}]+)")


def _scope_body(css: str, selector: str) -> str:
    """取出某个选择器紧随其后的第一个 {...} 块。"""
    index = css.find(selector)
    if index < 0:
        return ""
    start = css.find("{", index)
    if start < 0:
        return ""
    depth, position = 0, start
    while position < len(css):
        if css[position] == "{":
            depth += 1
        elif css[position] == "}":
            depth -= 1
            if depth == 0:
                return css[start + 1:position]
        position += 1
    return ""


def _tokens(css: str, selector: str) -> dict[str, str]:
    body = _scope_body(css, selector)
    return {name: value.strip() for name, value in TOKEN.findall(body)}


class DesignTokenParityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sources = {
            "index.html": INDEX.read_text(encoding="utf-8"),
            "agent-governance.css": GOV_CSS.read_text(encoding="utf-8"),
            "hub.css": HUB_CSS.read_text(encoding="utf-8"),
        }

    def test_all_files_exist(self):
        for path in (INDEX, GOV_CSS, HUB_CSS):
            self.assertTrue(path.is_file(), f"缺少 {path}")

    def _assert_scope_agrees(self, selector: str):
        reference_name = "index.html"
        reference = _tokens(self.sources[reference_name], selector)
        self.assertTrue(reference, f"{reference_name} 里找不到 {selector} 的令牌")
        for name, css in self.sources.items():
            if name == reference_name:
                continue
            with self.subTest(file=name, selector=selector):
                other = _tokens(css, selector)
                self.assertTrue(other, f"{name} 里找不到 {selector} 的令牌")
                shared = sorted(set(reference) & set(other))
                self.assertGreaterEqual(len(shared), 20, "共有令牌太少,比对没有意义")
                for token in shared:
                    self.assertEqual(
                        reference[token], other[token],
                        f"{selector} 的 {token} 与 index.html 不一致:"
                        f"index={reference[token]} {name}={other[token]}")

    def test_light_and_dark_overrides_agree(self):
        for selector in (':root[data-theme="light"]', ':root[data-theme="dark"]'):
            self._assert_scope_agrees(selector)

    def test_base_root_scope_agrees(self):
        self._assert_scope_agrees(":root")

    def test_hub_no_longer_ships_its_own_palette(self):
        """hub.css 原本自带一套深色优先的独立配色 —— 站内第二套设计系统,整合要消灭的正是它。"""
        hub = self.sources["hub.css"]
        for legacy in ("#0d1014", "#151a21", "#232a34", "#3fb950", "#d29922", "#f85149"):
            self.assertNotIn(legacy, hub, f"hub.css 里还留着旧配色 {legacy}")

    def test_no_external_resources_anywhere(self):
        """零 CDN 是硬约束:CSP 是 default-src 'self',取外部资源等于线上直接白屏。"""
        # control-plane.css 于 2026-08-04 随「业务线与证据治理」板块下线而删除，故不再检查。
        for path in (INDEX, GOV_CSS, HUB_CSS, WEB / "hub.html",
                     WEB / "agent-governance.js", WEB / "agent-governance.html"):
            with self.subTest(file=path.name):
                text = path.read_text(encoding="utf-8")
                for host in ("cdn.jsdelivr.net", "unpkg.com", "fonts.googleapis.com",
                             "cdnjs.cloudflare.com", "fonts.gstatic.com"):
                    self.assertNotIn(host, text)


if __name__ == "__main__":
    unittest.main()
