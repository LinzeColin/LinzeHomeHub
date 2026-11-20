#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""给首页、体检页和旧版页的静态资产 URL 打上内容哈希戳。

为什么必须这么做(2026-08-11 实测):
  源站 nginx 给 /assets/*.js 设的是 `Cache-Control: no-cache`,
  但**公网实际收到的是 `max-age=14400`** —— Cloudflare 的 Browser Cache TTL
  (默认 4 小时)会覆盖源站的头。也就是说:**源站怎么设都没用**,
  改了主题文件,用户最长 4 小时拿不到新代码,而 /health 之类的探针一切正常。
  这类"部署成功 ≠ 用户拿到新代码"是静默的,只能靠 URL 变化强制回源。

为什么不手写版本号:
  隔壁 social-archive 就踩过 —— 缓存戳 `?v=007-r2` 从建站起写死没动过,
  于是戳等于没有。所以这里的戳**必须来自文件内容本身**,人改不动、也忘不了。

用法:
    python3 status/deploy/stamp-assets.py            # 只改有变化的页面
    python3 status/deploy/stamp-assets.py --check    # 只检查,不改;戳过时则退出码 1
"""
import hashlib
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
WEB = os.path.normpath(os.path.join(HERE, "..", "web"))
PAGES = ("legacy.html", "index.html", "check.html")

# 只戳本站自己的静态资产。data/ 是每分钟变的运行态(nginx 已 expires -1),不戳;
# 外链 CDN 不归我们管,也戳不了。
# query 放宽到任意非引号内容:否则遇到 `?v=007-r2` 这类非法戳会整条匹配不上,
# 既检查不出来也覆盖不掉 —— 等于给死戳开了个后门。
PATTERN = re.compile(r'((?:src|href)=")(/?(?:assets|vendor)/[^"?]+\.(?:js|css))(\?v=[^"]*)?(")')


def sha8(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()[:8]


def resolve(url):
    return os.path.join(WEB, url.lstrip("/"))


def main():
    check = "--check" in sys.argv
    stale, missing, stamped = [], [], 0

    def repl(m):
        nonlocal stamped
        pre, url, old, post = m.group(1), m.group(2), m.group(3), m.group(4)
        p = resolve(url)
        if not os.path.isfile(p):
            missing.append(url)
            return m.group(0)
        want = "?v=" + sha8(p)
        if old != want:
            stale.append("%s  %s -> %s" % (url, old or "(无戳)", want))
        stamped += 1
        return pre + url + want + post

    updated = []
    for name in PAGES:
        page = os.path.join(WEB, name)
        if not os.path.isfile(page):
            missing.append(name)
            continue
        with open(page, encoding="utf-8") as f:
            src = f.read()
        out = PATTERN.sub(repl, src)
        if out != src:
            updated.append((page, out))

    for u in missing:
        print("✗ 资产不存在,无法打戳:%s" % u)
    if check:
        for s in stale:
            print("✗ 戳过时:%s" % s)
        if stale or missing:
            print("FAIL —— 跑 `python3 status/deploy/stamp-assets.py` 重新打戳后再提交")
            return 1
        print("OK —— %d 个资产的戳都与内容一致" % stamped)
        return 0

    for page, out in updated:
        with open(page, "w", encoding="utf-8") as f:
            f.write(out)
    for s in stale:
        print("已更新:%s" % s)
    print("OK —— %d 个资产已打戳" % stamped)
    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(main())
