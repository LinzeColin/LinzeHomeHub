#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
migrate_legacy_to_facts.py —— 把旧人类可读文件转成机器平面事实

旧结构（功能清单.md）是同构的：摘要字段块 + 功能表 + 证据表。
本工具把其中的**事实**抽取成 machine/facts/status.json 与 features.json，
使新七文件渲染出真实内容，而不是空 UNKNOWN。

原则：只搬事实，不搬叙述。抽取后旧文件即可删除（内容已进机器平面，
历史仍在 git 中可追溯）。抽不到的字段留空并标 UNKNOWN，绝不编造。

用法:
  python3 migrate_legacy_to_facts.py --project <项目目录>
    [--legacy <旧功能清单路径，默认自动探测>]
退出码: 0=成功  1=找不到旧文件
"""
import argparse
import json
import re
import sys
from pathlib import Path
from typing import Optional

STATUS_FIELDS = {
    "project_id": "project_id",
    "product_version": "version",
    "current_stage": "stage",
    "current_phase": "phase",
    "current_task": "task",
    "evidence_status": "evidence_status",
}


def find_legacy(project: Path):
    for cand in [
        project / "功能清单.md",
        project / "machine" / "legacy" / "功能清单.md",
    ]:
        if cand.is_file():
            return cand
    return None


def parse_summary(text: str) -> dict:
    """抽取 '- key: `value`' 摘要块。"""
    out = {}
    for m in re.finditer(r"^-\s+([a-z_]+):\s*`([^`]*)`", text, re.MULTILINE):
        out[m.group(1)] = m.group(2)
    return out


def parse_features(text: str) -> list:
    """抽取功能表 '| FEAT-... | 名称 | 状态 | ... | 证据等级 |'。"""
    feats = []
    for m in re.finditer(r"^\|\s*(FEAT-[A-Z0-9\-]+)\s*\|([^|]*)\|([^|]*)\|(.*)\|([^|]*)\|\s*$",
                         text, re.MULTILINE):
        fid, name, status = m.group(1).strip(), m.group(2).strip(), m.group(3).strip()
        evidence = m.group(5).strip().lower()
        feats.append({
            "id": fid,
            "name": clean_feature_name(name),
            "status": status,
            "evidence": "extracted" if "extract" in evidence else "declared",
        })
    return feats


def clean_feature_name(name: str) -> str:
    """从功能名剥离日志词（纯净门：功能名只放功能，不放 review/gate 等）。"""
    out = name
    for w in ["review", "gate", "replay", "audit", "closure", "remediation",
              "recheck", "复审", "阶段门"]:
        out = re.sub(rf"(?i)\b{w}\d*s?\b", "", out)
    # 收拾多余空格与标点
    out = re.sub(r"\s{2,}", " ", out).strip(" ·、,，-")
    return out or name


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", required=True)
    ap.add_argument("--legacy")
    ap.add_argument("--from-features", action="store_true")
    args = ap.parse_args()

    project = Path(args.project).resolve()
    facts_dir = project / "machine" / "facts"
    # --from-features：机器平面已有 features.json，直接据此补 glossary/product
    if args.from_features:
        import json as _json
        ff = facts_dir / "features.json"
        features = _json.loads(ff.read_text(encoding="utf-8")) if ff.is_file() else []
        terms = seed_glossary_terms(features)
        (facts_dir / "glossary.json").write_text(_json.dumps(
            {"numbers": [], "data_shapes": [], "invariants": [],
             "terms": [{"英文": t, "中文": "待补中文", "说明": "来自功能名，待复审确认释义"}
                       for t in sorted(terms)]}, ensure_ascii=False, indent=2), encoding="utf-8")
        pf = facts_dir / "product.json"
        if not pf.is_file():
            pf.write_text(_json.dumps({"goal": "", "users": [], "non_goals": []},
                                      ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"✅ {project.name}: 从 {len(features)} 个已有功能补 {len(terms)} 个术语 -> glossary")
        return 0
    legacy = Path(args.legacy) if args.legacy else find_legacy(project)
    if not legacy or not legacy.is_file():
        print(f"FAIL: 找不到旧功能清单（{project}）")
        return 1

    text = legacy.read_text(encoding="utf-8")
    summary = parse_summary(text)
    features = parse_features(text)

    facts = project / "machine" / "facts"
    facts.mkdir(parents=True, exist_ok=True)

    status = {
        "version": summary.get("product_version", "UNKNOWN"),
        "stage": summary.get("current_stage", "UNKNOWN"),
        "phase": summary.get("current_phase", "UNKNOWN"),
        "task": summary.get("current_task", "无进行中任务"),
        "real_progress": (summary.get("progress", "") or "待补")
                         + "（这是走完了多少道结构关卡；离真正做完还差多少，要对着真实数据核一遍才知道）",
        "report_grade": "",
        "business_verdict": "",
        "evidence_status": summary.get("evidence_status", "UNKNOWN"),
        "rendered_at": "2026-07-15",
        "migrated_from_legacy": legacy.name,
    }
    (facts / "status.json").write_text(
        json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")
    (facts / "features.json").write_text(
        json.dumps(features, ensure_ascii=False, indent=2), encoding="utf-8")

    # glossary.json：功能名里的英文术语进机器平面术语表（渲染 03 时并入）
    terms = seed_glossary_terms(features)
    glossary = {
        "numbers": [], "data_shapes": [], "invariants": [],
        "terms": [{"英文": t, "中文": "待补中文", "说明": "来自功能名，待复审确认释义"}
                  for t in sorted(terms)],
    }
    (facts / "glossary.json").write_text(
        json.dumps(glossary, ensure_ascii=False, indent=2), encoding="utf-8")

    # product.json：产品需求的机器平面事实。旧功能清单摘要里能抽的先抽，
    # 抽不到的留空 -> 渲染出"待补"，由后续机器平面补全，不手写。
    product = {
        "goal": summary.get("product_goal", ""),
        "users": [],
        "non_goals": [],
    }
    pf = facts / "product.json"
    if not pf.is_file():  # 不覆盖已有的更完整 product 事实
        pf.write_text(json.dumps(product, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"✅ {project.name}: 抽取 {len(features)} 个功能 + {len(summary)} 个摘要字段"
          f" + {len(terms)} 个术语 -> machine/facts")
    return 0


# 日志词：不该出现在功能名里（纯净门）。抽取时从功能名剥离。
LOG_WORDS = ["phase", "gate", "review", "remediation", "replay",
             "recheck", "audit", "closure", "阶段门", "复审"]
# 已在 check_doc_budget ALLOW 里的通用词，不必登记
COMMON_ALLOW = {
    "id", "ui", "ux", "api", "sdk", "cli", "app", "db", "sql", "csv", "json",
    "yaml", "html", "pdf", "md", "http", "https", "url", "ok", "and", "or",
    "the", "a", "of", "to", "in", "on", "top", "public", "and", "or", "with",
    "for", "by", "raw", "live", "board", "in_progress", "blocked", "active",
    "done", "pending", "planned",
}
ENG = re.compile(r"[A-Za-z][A-Za-z_\-]{1,}")


def seed_glossary_terms(features):
    """从功能名收集需登记的英文术语（去掉日志词和通用词）。"""
    terms = {}
    for f in features:
        for w in ENG.findall(f.get("name", "")):
            wl = w.lower()
            if wl in COMMON_ALLOW or wl in LOG_WORDS:
                continue
            terms.setdefault(w, "")
    return terms




if __name__ == "__main__":
    sys.exit(main())
