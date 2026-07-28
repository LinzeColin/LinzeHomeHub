"""静态资源必须落在**真的会被部署**的路径上。

起因是一次真实事故:外部补丁包把验收裁决 JSON 放进 `status/data/acceptance/`,
而 `deploy.sh` 的 rsync 明确 `--exclude data/`(那是运行期生成目录)。
结果是 —— 页面能打开(HTTP 200),数据 404,验收页是**空的**。

★ 最值得记的不是这个 bug,而是**谁都没抓到它**:
    · 补丁包自带的 verify_patch.py  -> PASS
    · 补丁包自带的单元测试          -> PASS
    · 仓里 70 个冻结测试            -> 全绿
  因为它们验的都是「文件在仓里吗」,没有一条验「文件到得了生产吗」。
  「进了仓」和「上得了线」是两件事,中间隔着一层 rsync 排除规则。

这条守卫把那层排除规则**从 deploy.sh 里读出来**(不手抄,免得两边漂移),
再断言:凡是前端会去 fetch 的静态数据,都不能落在被排除的目录下。
"""
from __future__ import annotations

from pathlib import Path
import re
import unittest

from test_support import locate

REPO, _, _ = locate()
DEPLOY = REPO / "status" / "deploy" / "control-plane" / "deploy.sh"
WEB = REPO / "status" / "web"


def excluded_dirs() -> set[str]:
    """从 deploy.sh 里读出 rsync 的 --exclude 目录,不手抄。"""
    text = DEPLOY.read_text(encoding="utf-8")
    return {m.rstrip("/") for m in re.findall(r"--exclude\s+([A-Za-z0-9_.-]+/)", text)}


def fetched_paths() -> list[tuple[Path, str]]:
    """扫前端 JS 里 fetch 的相对静态路径。"""
    found = []
    for js in WEB.rglob("*.js"):
        if "vendor" in js.parts:
            continue
        text = js.read_text(encoding="utf-8", errors="replace")
        for url in re.findall(r'["\']([A-Za-z0-9_./-]+\.json)(?:\?[^"\']*)?["\']', text):
            found.append((js, url))
    return found


class StaticAssetsDeployTests(unittest.TestCase):
    def test_deploy_script_declares_exclusions(self):
        """先确认真的读到了排除规则,否则下面几条会静默空转。"""
        self.assertTrue(DEPLOY.is_file(), "找不到 deploy.sh,这条守卫无从谈起")
        self.assertIn("data", excluded_dirs(),
                      "没从 deploy.sh 读到 data/ 排除项 —— 规则变了就得同步这条测试")

    def test_fetched_static_json_is_not_under_an_excluded_dir(self):
        """★ 核心:前端要 fetch 的静态 JSON,不能放在部署时被排除的目录里。"""
        excluded = excluded_dirs()
        for js, url in fetched_paths():
            if url.startswith(("http://", "https://", "/data/", "data/")):
                # /data/ 下的是运行期生成的(snapshot 等),本来就该在那儿,不管
                continue
            target = (WEB / url).resolve()
            rel = target.relative_to(REPO) if REPO in target.parents else Path(url)
            top = rel.parts[1] if len(rel.parts) > 1 and rel.parts[0] == "status" else None
            self.assertNotIn(
                top, excluded,
                f"{js.name} 要 fetch {url},但它落在被部署排除的 {top}/ 下,上线后必然 404")

    def test_acceptance_data_exists_where_the_page_looks_for_it(self):
        """验收页 fetch 的那个文件,必须真的在对应位置 —— 否则线上就是空页。"""
        js = WEB / "assets" / "acceptance" / "acceptance.js"
        if not js.is_file():
            self.skipTest("验收页未安装")
        match = re.search(r'DATA_URL\s*=\s*["\']([^"\']+)["\']', js.read_text(encoding="utf-8"))
        self.assertIsNotNone(match, "找不到 DATA_URL")
        target = WEB / match.group(1)
        self.assertTrue(target.is_file(),
                        f"验收页要取 {match.group(1)},但 {target.relative_to(REPO)} 不存在")

    def test_negative_control_excluded_path_is_caught(self):
        """破坏测试:把数据挪回 data/ 下,守卫必须判定它上不了线。"""
        excluded = excluded_dirs()
        bad = "status/data/acceptance/chatgpt_latest.json"
        top = Path(bad).parts[1]
        self.assertIn(top, excluded,
                      "★ data/ 居然不在排除项里 —— 那这条破坏测试本身是坏的")


if __name__ == "__main__":
    unittest.main()
