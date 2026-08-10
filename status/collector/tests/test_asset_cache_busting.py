#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""静态资产的缓存戳必须跟着内容走。

**为什么这条测试比脚本本身重要:**

隔壁 social-archive 的缓存戳是 `?v=007-r2`,从建站起写死没动过 —— 戳存在,但等于没有。
实测后果:`/health` 报新版本,而公网拿到的 `app.js` 还是旧文件。
**部署成功 ≠ 用户拿到新代码**,而且这类失效完全静默:探针全绿、页面是旧的。

本站 2026-08-11 实测到同一形态的更深一层:
  源站 nginx 给 `/assets/*.js` 设的是 `Cache-Control: no-cache`,
  **公网实际收到的却是 `max-age=14400`** —— Cloudflare 的 Browser Cache TTL(默认 4 小时)
  会覆盖源站的头。也就是说**源站怎么设都没用**,只能靠 URL 变化强制回源。

所以:
  1) 戳必须来自文件内容(sha256 前 8 位),人改不动;
  2) **有一条测试盯着它** —— 改了资产忘了重新打戳,CI 就红,而不是等 4 小时后
     用户报"页面没变"。没有 (2),(1) 迟早退化成又一个 `?v=007-r2`。
"""
import hashlib
import os
import re
import subprocess
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
COLLECTOR = os.path.dirname(HERE)
STATUS = os.path.dirname(COLLECTOR)
WEB = os.path.join(STATUS, "web")
INDEX = os.path.join(WEB, "index.html")
STAMPER = os.path.join(STATUS, "deploy", "stamp-assets.py")

# query 部分必须放宽到「任意非引号内容」。第一版写成 (\?v=([0-9a-f]+))? —— 只认十六进制,
# 结果 `?v=007-r2` 这种**非法戳整条匹配不上**,那个资产直接从检查里消失,反而躲过了判定。
# 负控当场抓到:注入隔壁那个死戳形态,测试却是绿的。**判据只覆盖合法输入 = 假绿。**
PATTERN = re.compile(r'(?:src|href)="(/?(?:assets|vendor)/[^"?]+\.(?:js|css))(?:\?v=([^"]*))?"')


def index_src():
    with open(INDEX, encoding="utf-8") as f:
        return f.read()


def sha8(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()[:8]


class AssetStampTest(unittest.TestCase):

    def test_every_local_asset_is_stamped(self):
        missing = [m.group(1) for m in PATTERN.finditer(index_src()) if not m.group(2)]
        self.assertEqual(missing, [],
                         "这些本站资产没有缓存戳,改了它们用户最长 4 小时拿不到:%s" % missing)

    def test_stamp_matches_current_content(self):
        # 核心断言:戳 == 当前文件内容的哈希。
        # 改了资产没重新打戳 → 这里红,而不是等用户发现页面没变。
        bad = []
        for m in PATTERN.finditer(index_src()):
            url, stamp = m.group(1), m.group(2)
            p = os.path.join(WEB, url.lstrip("/"))
            if not os.path.isfile(p):
                bad.append("%s 文件不存在" % url)
                continue
            want = sha8(p)
            if stamp != want:
                bad.append("%s 戳=%s 实际内容=%s" % (url, stamp, want))
        self.assertEqual(bad, [],
                         "缓存戳与内容对不上,跑 `python3 status/deploy/stamp-assets.py` 重新打戳:%s" % bad)

    def test_stamper_check_mode_agrees(self):
        # 用脚本自己的 --check 再验一次,防止测试与脚本对"什么算过时"的理解分叉
        r = subprocess.run([sys.executable, STAMPER, "--check"],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, "stamp-assets.py --check 失败:\n%s%s" % (r.stdout, r.stderr))

    def test_stamp_is_content_derived_not_hand_written(self):
        # 反 `?v=007-r2`:戳必须是 8 位十六进制(哈希形态),不能是人手写的版本号。
        # 手写的戳没人会记得更新,那正是隔壁踩过的坑。
        for m in PATTERN.finditer(index_src()):
            stamp = m.group(2)
            if stamp is None:
                continue
            self.assertRegex(stamp, r"^[0-9a-f]{8}$",
                             "戳 %r 不是内容哈希形态 —— 手写版本号会退化成永不更新的死戳" % stamp)

    def test_data_dir_is_not_stamped(self):
        # data/ 是每分钟变的运行态,nginx 已 expires -1;给它打戳既没用又会让 index 天天变
        self.assertNotIn('data/snapshot.json?v=', index_src())


if __name__ == "__main__":
    unittest.main(verbosity=2)
