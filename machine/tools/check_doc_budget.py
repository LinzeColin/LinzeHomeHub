#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
check_doc_budget.py —— 人类平面三道门

1. 体积门：超上限 -> FAIL（不截断，逼你去精简机器平面）
2. 中文门：正文出现的英文术语必须在 03_口径字典.md 第六节登记
3. 纯净门：02 的功能清单里不许出现日志词（phase/gate/review/...）

用法:  python3 machine/tools/check_doc_budget.py [--docs 文档]
退出码: 0=PASS  1=FAIL
"""
import argparse
import re
import sys
from pathlib import Path

# 行数上限。要调大之前，先读 06_运维手册.md 第三节。
BUDGET = {
    "00_我在哪.md": 120,
    "01_产品需求.md": 200,
    "02_系统架构.md": 200,
    "03_口径字典.md": 10**6,   # 口径不限量：这是唯一值得写长的文件
    "04_操作流程.md": 150,
    "05_执行与验收.md": 100,
    "06_运维手册.md": 200,
}

# 功能清单里出现即 FAIL —— 这些是日志词，不是功能词
LOG_WORDS = ["phase", "gate", "review", "remediation", "replay",
             "recheck", "audit", "closure", "阶段门", "复审"]

# 中文门豁免：这些英文永远不用登记
ALLOW = {
    "kmfa", "github", "codex", "claude", "chatgpt", "python3", "python",
    "sha256", "hash", "csv", "json", "yaml", "html", "pdf", "xlsx", "xls",
    "md", "id", "ok", "ui", "ux", "api", "wps", "http", "https", "url", "py",
    "gitignore", "git", "pytest", "asia", "shanghai", "linzezhang",
    "documents", "downloads", "users", "metadata", "local", "runtime",
    "tools", "machine", "facts", "config", "docs", "true", "false", "v",
    "render", "human", "render_human", "check", "budget", "blocker", "stop",
    "dual", "plane", "install", "ci", "unknown", "features", "status",
    "roadmap", "blockers", "plan", "acceptance", "runs", "ops", "changelog",
    "data", "contract", "flows", "legacy", "gitkeep",
    "streamlit", "fastapi", "flask", "django", "react", "vue", "node",
    "npm", "pip", "docker", "sqlite", "postgres", "redis", "excel", "word",
    "ppt", "xlsx", "docx", "pptx", "app", "cli", "sdk", "sql", "db",
    "and", "or", "with", "for", "by", "raw", "live", "board", "top", "public",
    "the", "a", "of", "to", "in", "on", "in_progress", "blocked", "active",
    "done", "pending", "planned", "provider", "candidate", "comparison",
    "goal", "non_goals", "users", "numbers", "data_shapes", "invariants",
    "terms", "product", "glossary",
    "agent", "agents", "codex", "schema", "release", "token", "tokens",
    "linzecolin", "codexproject", "kmos", "metadatabase", "agentdatabase",
    "governance", "kmfa", "kmids", "whkmsalary", "kmdatabase", "linzedatabase",
    "serenity", "alipay", "alpha", "fifa", "qbvs", "eei", "pfi", "adp",
    "openaidatabase", "atlas", "memory", "claude", "chatgpt",
}

CODE_BLOCK = re.compile(r"```.*?```", re.S)
INLINE_CODE = re.compile(r"`[^`]*`")
HTML_COMMENT = re.compile(r"<!--.*?-->", re.S)
LINK_TARGET = re.compile(r"\]\([^)]*\)")
ENGLISH_WORD = re.compile(r"[A-Za-z][A-Za-z_\-]{1,}")


def strip_exempt(text: str) -> str:
    """去掉代码块、行内代码、注释、链接目标 —— 这些豁免中文门。"""
    for pat in (CODE_BLOCK, HTML_COMMENT, INLINE_CODE, LINK_TARGET):
        text = pat.sub(" ", text)
    return text


def load_glossary(docs: Path) -> set:
    """读 03_口径字典.md 第六节「术语对照」的英文词条。"""
    f = docs / "03_口径字典.md"
    if not f.exists():
        return set()
    body = f.read_text(encoding="utf-8")
    m = re.search(r"##\s*[一二三四五六七八九十]*、?\s*术语对照.*?(?=\n##\s|\Z)", body, re.S)
    if not m:
        return set()
    return {w.lower() for w in ENGLISH_WORD.findall(m.group(0))}


def check_budget(docs: Path, failures: list) -> None:
    for name, limit in BUDGET.items():
        f = docs / name
        if not f.exists():
            failures.append(f"[体积门] 缺文件: {name}")
            continue
        n = len(f.read_text(encoding="utf-8").splitlines())
        if n > limit:
            failures.append(
                f"[体积门] {name}: {n} 行 > 上限 {limit} 行。"
                f"去精简机器平面，不要调大上限。"
            )


def check_chinese(docs: Path, glossary: set, failures: list) -> None:
    for name in BUDGET:
        f = docs / name
        if not f.exists() or name == "03_口径字典.md":
            continue
        body = strip_exempt(f.read_text(encoding="utf-8"))
        unknown = sorted({
            w for w in ENGLISH_WORD.findall(body)
            if w.lower() not in ALLOW and w.lower() not in glossary
        })
        if unknown:
            failures.append(
                f"[中文门] {name}: 未登记的英文术语 {unknown[:8]}"
                f"{' ...等 %d 个' % len(unknown) if len(unknown) > 8 else ''}。"
                f"去 03_口径字典.md 第六节加中文条目。"
            )


def check_purity(docs: Path, failures: list) -> None:
    f = docs / "02_系统架构.md"
    if not f.exists():
        return
    body = f.read_text(encoding="utf-8")
    m = re.search(r"##\s*一、功能清单.*?(?=\n##\s|\Z)", body, re.S)
    if not m:
        failures.append("[纯净门] 02_系统架构.md 找不到「一、功能清单」章节")
        return
    section = strip_exempt(m.group(0)).lower()
    hits = [w for w in LOG_WORDS if w in section]
    if hits:
        failures.append(
            f"[纯净门] 功能清单里出现日志词 {hits}。"
            f"功能清单只放功能，日志挪到 05_执行与验收.md。"
        )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--docs", default="文档")
    args = ap.parse_args()
    docs = Path(args.docs)

    if not docs.is_dir():
        print(f"FAIL: 找不到人类平面目录 {docs}")
        return 1

    failures: list = []
    check_budget(docs, failures)
    check_chinese(docs, load_glossary(docs), failures)
    check_purity(docs, failures)

    if failures:
        print(f"FAIL —— {len(failures)} 项\n")
        for x in failures:
            print("  ✗ " + x)
        return 1
    print("PASS —— 人类平面三道门全过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
