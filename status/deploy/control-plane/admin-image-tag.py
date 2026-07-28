#!/usr/bin/env python3
"""admin 镜像的**内容派生**标签 —— 让本地构建的镜像也有不可变的部署主体。

冻结验收 AR-004 要求「零个未经批准的可变执行依赖」,阈值是每个运行/构建依赖
都有固定的 SHA/digest/tag。第三方镜像好办,固定 `@sha256:` 就行;
但 `linze-status-admin` 是 `build: ../admin` 本地构建的,推之前根本没有 registry digest,
所以原来只能挂 `:latest` —— 两次不同的构建可以顶着同一个名字,
「部署的是哪一版」这个问题没有答案。

这里的做法:标签 = admin 构建上下文全部文件内容的 sha256 前 12 位。
于是:
  * 源码没变 → 标签不变 → 重复部署是幂等的;
  * 源码变了 → 标签必然变 → 不可能出现「同名不同物」;
  * 标签写死在 docker-compose.yml 里,是**仓库里可审阅的字面量**,
    不是 ${ENV} 那种运行时才知道的东西(那样等于把可变性挪个地方藏起来)。

`--check` 用来做守卫:比对 compose 里写的标签与当前内容是否一致。
改了 admin/ 却忘了重算标签,守卫会红 —— 这正是要防的那种漂移。
"""
from __future__ import annotations

import argparse
from hashlib import sha256
from pathlib import Path
import re
import sys

REPO_ROOT = Path(__file__).resolve().parents[3]
ADMIN_DIR = REPO_ROOT / "status" / "admin"
COMPOSE = REPO_ROOT / "status" / "deploy" / "docker-compose.yml"
IMAGE_LINE = re.compile(r"^(\s*image:\s*linze-status-admin:)([A-Za-z0-9._-]+)\s*$", re.M)


def context_digest(admin_dir: Path) -> str:
    """对构建上下文做确定性摘要:路径与内容都进哈希,顺序固定。

    只哈希文件名+内容,不掺时间戳/权限 —— 否则同样的源码在两台机器上会算出
    不同的标签,那这个标签就不再是「内容的身份」了。
    """
    digest = sha256()
    for path in sorted(p for p in admin_dir.rglob("*") if p.is_file()):
        rel = path.relative_to(admin_dir).as_posix()
        digest.update(rel.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true",
                        help="校验 docker-compose.yml 里的标签是否与当前内容一致")
    parser.add_argument("--write", action="store_true",
                        help="把当前内容标签写回 docker-compose.yml")
    args = parser.parse_args()

    if not ADMIN_DIR.is_dir():
        print(f"找不到 admin 构建上下文:{ADMIN_DIR}", file=sys.stderr)
        return 2
    tag = context_digest(ADMIN_DIR)[:12]

    if not (args.check or args.write):
        print(tag)
        return 0

    text = COMPOSE.read_text(encoding="utf-8")
    match = IMAGE_LINE.search(text)
    if match is None:
        print("docker-compose.yml 里找不到 linze-status-admin 的 image 行", file=sys.stderr)
        return 2
    declared = match.group(2)

    if args.write:
        COMPOSE.write_text(IMAGE_LINE.sub(rf"\g<1>{tag}", text, count=1), encoding="utf-8")
        print(f"已写入 linze-status-admin:{tag}(原 {declared})")
        return 0

    if declared == tag:
        print(f"OK linze-status-admin:{tag} 与构建上下文内容一致")
        return 0
    print(f"标签漂移:compose 写的是 {declared},当前 admin/ 内容算出来是 {tag}。\n"
          f"admin/ 改过就必须重算标签,否则同一个名字会指向不同的东西。\n"
          f"修:python3 status/deploy/control-plane/admin-image-tag.py --write", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
