#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
check_kit_drift.py —— 双平面 kit 漂移门

问题：kit 的 6 个工具靠 install_dual_plane.py 复制进每个子项目，全工作间共 20 份副本。
复制之后没人管，副本各自被改 —— 2026-08-18 实测 render_human.py 已经漂成 7 个不同版本。
后果是同一道门在不同子项目行为不同：你在 A 项目修好的 bug，B 项目还留着；
更糟的是「过了门」在不同项目根本不是同一件事，绿灯不可比。

这道门只做一件事：拿 kit_manifest.json 里登记的 sha256，逐个核对本仓的副本。
manifest 是唯一真源（规格），副本是它的镜像；对不上就是漂移，必须先解释再合并。

**一个仓只允许有一份清单。** 如果每个子项目各带一份，漂移的副本配一份同样漂移的
清单就永远绿 —— 那不是门，是装饰。所以发现多份清单直接 FAIL。
清单本身被人改写来消音，会明晃晃留在 PR diff 里，那是人的责任边界，门不负责。

用法:
  python3 machine/tools/check_kit_drift.py                  # 校验本仓全部副本
  python3 machine/tools/check_kit_drift.py --root .         # 指定扫描根
  python3 machine/tools/check_kit_drift.py --manifest <路径>  # 指定清单
  python3 machine/tools/check_kit_drift.py --update         # 按 kit 重算清单（只在 Governance 仓跑）

退出码: 0=PASS  1=FAIL(有漂移或缺失)  2=用法错误
"""
import argparse
import hashlib
import json
import sys
from pathlib import Path

# --update 的落点。校验时不用它 —— 校验是全仓 rglob 找 kit_manifest.json，
# 免得「换个位置放就绕过去了」。
KIT_MANIFEST = Path("dual_plane/machine/facts/kit_manifest.json")

# kit 本体位置（--update 时从这里重算）
KIT_TOOLS = Path("dual_plane/machine/tools")

# 扫描时跳过的目录：临时产物、依赖、git 内部、以及 kit 自己
SKIP_DIRS = {".git", "node_modules", "_scratch", ".venv", "venv",
             "__pycache__", "dist", "build", ".pytest_cache"}


def sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def find_manifests(explicit, root):
    """返回本仓所有清单。多于一份就是设计被破坏了，交给调用方 FAIL。"""
    if explicit:
        p = Path(explicit)
        return [p] if p.is_file() else []
    found = []
    for p in root.rglob("kit_manifest.json"):
        rel_parts = p.relative_to(root).parts
        if any(part in SKIP_DIRS for part in rel_parts):
            continue
        found.append(p)
    return sorted(found)


def iter_copies(root: Path, names: set) -> list:
    """找出本仓所有 <任意路径>/machine/tools/<kit工具名> 的副本。"""
    found = []
    for tools_dir in root.rglob("machine/tools"):
        # 只看 root 以内的路径段。用绝对路径判断会把 root 自身的祖先目录
        # 一起算进去 —— 工作树落在 _scratch/ 下时会整仓被跳过，还报 PASS。
        rel_parts = tools_dir.relative_to(root).parts
        if any(part in SKIP_DIRS for part in rel_parts):
            continue
        if not tools_dir.is_dir():
            continue
        for name in sorted(names):
            f = tools_dir / name
            if f.is_file():
                found.append(f)
    return sorted(found)


def cmd_update(root: Path, manifest_path: Path) -> int:
    kit = root / KIT_TOOLS
    if not kit.is_dir():
        print(f"FAIL: --update 只能在 Governance 仓跑，这里没有 {KIT_TOOLS}")
        return 2
    tools = {f.name: sha256_of(f) for f in sorted(kit.glob("*.py"))}
    if not tools:
        print(f"FAIL: {KIT_TOOLS} 里没有 .py，无法生成清单")
        return 2
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps({"source": str(KIT_TOOLS), "tools": tools},
                   ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8")
    print(f"已写入 {manifest_path} —— 登记 {len(tools)} 个工具")
    for n, h in tools.items():
        print(f"  {h[:12]}  {n}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=".", help="扫描根，默认当前目录")
    ap.add_argument("--manifest", default=None, help="清单路径，默认自动找")
    ap.add_argument("--update", action="store_true", help="按 kit 重算清单")
    args = ap.parse_args()

    root = Path(args.root).resolve()
    if not root.is_dir():
        print(f"FAIL: 找不到扫描根 {root}")
        return 2

    if args.update:
        target = Path(args.manifest) if args.manifest else root / KIT_MANIFEST
        return cmd_update(root, target)

    manifests = find_manifests(args.manifest, root)
    if not manifests:
        # fail-closed：没有清单不等于没有漂移，等于没人在看
        print("FAIL: 找不到 kit_manifest.json。")
        print("  它是 kit 的唯一真源，缺了这道门就是空转。")
        print("  从 Governance 仓 dual_plane/machine/facts/kit_manifest.json 复制一份进来，")
        print("  放在本仓 machine/facts/kit_manifest.json（一个仓只放一份）。")
        return 1
    if len(manifests) > 1:
        print(f"FAIL: 本仓有 {len(manifests)} 份 kit_manifest.json，只允许一份。")
        for p in manifests:
            print(f"  - {p.relative_to(root)}")
        print("\n  每个子项目各带一份清单 = 漂移的副本配漂移的清单，这道门就永远绿。")
        print("  只保留仓根那一份，其余删掉。")
        return 1
    manifest_path = manifests[0]

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    expected = manifest.get("tools") or {}
    if not expected:
        print(f"FAIL: {manifest_path} 里没有 tools 条目")
        return 1

    copies = iter_copies(root, set(expected))
    if not copies:
        print(f"PASS —— 本仓没有 kit 副本，无需核对（清单: {manifest_path}）")
        return 0

    drifted, ok = [], 0
    for f in copies:
        got = sha256_of(f)
        want = expected[f.name]
        if got == want:
            ok += 1
        else:
            drifted.append((f.relative_to(root), got, want))

    if drifted:
        print(f"FAIL —— {len(drifted)} 份副本已漂离 kit（另有 {ok} 份一致）\n")
        for rel, got, want in drifted:
            print(f"  ✗ {rel}")
            print(f"      本地 {got[:16]} ≠ kit {want[:16]}")
        print("\n  两条路，二选一，别放着不管：")
        print("  ① 副本是错的 → 从 Governance 仓 dual_plane/machine/tools/ 覆盖回来。")
        print("  ② 副本是对的（你在这里修了 bug）→ 先把修改提回 Governance kit，")
        print("     跑 check_kit_drift.py --update 更新清单，再同步其余副本。")
        return 1

    print(f"PASS —— {ok} 份副本与 kit 一致（清单: {manifest_path.name}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
