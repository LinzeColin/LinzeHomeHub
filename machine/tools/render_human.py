#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
render_human.py —— 从机器平面渲染人类平面七文件

双平面原则：
- 机器平面 machine/facts/*.json 是唯一事实源。
- 人类平面 文档/ 七个文件全部是渲染产物，无一手写。
  agent 负责生产机器平面事实，渲染器把它们渲染成七文件；负责人只复审，不手写。
- 因此没有任何"手写区"。每次渲染都会覆盖全部七个文件。

为什么人类平面必须渲染而不能手写：
  「功能清单里没有功能」的根因是它允许被手写。任何允许手写的人类可读文件，
  最终必然退化成 append-only 日志。这是结构决定的。产品需求与口径字典同样如此，
  所以它们也从机器平面渲染，不留手写口子。

事实源约定（每个渲染文件的头注释也声明了同一份映射）：
  00_我在哪.md      <- facts/status.json, facts/blockers.json, facts/roadmap.json
  01_产品需求.md    <- facts/product.json
  02_系统架构.md    <- facts/features.json, facts/data_contract.yaml, facts/config.yaml
  03_口径字典.md    <- facts/glossary.json
  04_操作流程.md    <- facts/flows.json
  05_执行与验收.md  <- facts/plan.json, facts/acceptance.json, runs/*.json
  06_运维手册.md    <- facts/config.yaml, facts/ops.json, facts/changelog.json

缺失的事实源不会让渲染崩溃：对应章节如实显示"待补"，
随后三道门据此把关。「绿的门是假门」——事实没接通时就该留白并让门保持红。

用法:  python3 machine/tools/render_human.py [--root .]
退出码: 0=渲染完成
"""
import argparse
import json
import re
import sys
from pathlib import Path

GENERATED = (
    "<!-- 本文件由 machine/tools/render_human.py 从机器平面生成。请勿手写——下次渲染会覆盖。 -->"
)
# 单个字段还没值时的占位——短、明确、不装懂
UNKNOWN = "待补"


def blank_note(what, whence):
    """某个章节暂时没内容时，用一句人话说明，而不是摆一张空表。

    what: 这一节本该写什么（读者视角）
    whence: 内容将来从哪来（谁补、补什么）；其中的文件路径会自动包成代码样式，
            以豁免中文门。
    """
    # 把 machine/facts/xxx.yaml 之类路径包进反引号（代码标识符豁免中文门）
    whence = re.sub(r"(machine/[\w./-]+)", r"`\1`", whence)
    return f"> 暂时还没有{what}。等{whence}之后，这里会自动出现内容。现在空着是如实反映——没接上就是没接上。"

# 跨项目通用治理术语，渲染 03_口径字典 时并入术语表，使中文门对它们豁免。
COMMON_GLOSSARY = [
    ("Owner", "负责人", "项目/产品的决策与复审责任人"),
    ("Stage", "阶段", "路线图一级单元"),
    ("Phase", "步骤", "阶段下的二级单元"),
    ("Task", "任务", "步骤下的三级单元"),
    ("Roadmap", "路线图", "阶段→步骤→任务的完整计划"),
]


def load_json(path: Path, default):
    """读一个 JSON 事实源；缺失或损坏时返回 default（渲染成 UNKNOWN）。"""
    if not path.is_file():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return default


def load_yaml_or_json(path: Path, default):
    """config/data_contract 允许 yaml；无 yaml 库时退化为只读文本存在性。"""
    if not path.is_file():
        return default
    text = path.read_text(encoding="utf-8")
    try:
        import yaml  # noqa
        return yaml.safe_load(text) or default
    except Exception:
        # 没有 yaml 库也不阻塞渲染：返回原始文本，调用方只判存在性
        return {"_raw": text}


def table(rows, header, empty=None):
    """把 [(a,b,...)] 渲染成 markdown 表。

    空 rows 时不摆空表，改用一句人话（empty）。没给 empty 就退回极简提示。
    """
    if not rows:
        return empty or "> 暂时没有内容。"
    out = ["| " + " | ".join(header) + " |",
           "|" + "|".join(["---"] * len(header)) + "|"]
    for r in rows:
        out.append("| " + " | ".join(str(c) for c in r) + " |")
    return "\n".join(out)


# ---------- 各文件渲染器 ----------

def render_00(facts: Path):
    status = load_json(facts / "status.json", {})
    blockers = load_json(facts / "blockers.json", [])
    roadmap = load_json(facts / "roadmap.json", {})
    src_commit = status.get("facts_commit", "未知（待本机渲染写入）")

    def val(key, default="待补"):
        v = status.get(key)
        return f"`{v}`" if v else default

    state_rows = [
        ("版本", val("version")),
        ("进行到哪", f"{val('stage')} · {val('phase')} · {val('task')}"),
        ("进度", status.get("real_progress") or "待补"),
        ("报告可信度", status.get("report_grade") or "待口径字典裁定"),
        ("业务结论", status.get("business_verdict") or "待补"),
        ("卡住的事", f"{len(blockers)} 件" if blockers else "无"),
    ]
    blk_rows = [
        (b.get("id", "?"), b.get("内容", b.get("desc", "")),
         "**只有你能解**" if b.get("owner_only") else (b.get("owner") or "待定"),
         b.get("首次登记", b.get("since", "?")))
        for b in blockers
    ]
    rm_rows = [
        (s.get("id", "?"), s.get("name", ""), s.get("gate", ""),
         s.get("status", ""))
        for s in roadmap.get("stages", [])
    ]

    body = f"""{GENERATED}
<!-- 事实源：machine/facts/status.json、blockers.json、roadmap.json -->
<!-- 上限 120 行 -->

# 我在哪

一眼看清这个项目现在是什么状态、卡在哪、下一步该做什么。

**更新于** {status.get('rendered_at', '待渲染')}

## 一、当前状态

{table(state_rows, ["", ""])}

## 二、卡住的事

{table(blk_rows, ["编号", "什么事", "谁能解", "卡了多久"],
       empty="> 目前没有卡住的事。一旦出现只有你能拍板的阻塞，会自动列在这里并提醒你。")}

## 三、路线图

{table(rm_rows, ["阶段", "名称", "过关标准", "状态"],
       empty=blank_note("路线图", "把阶段计划写进机器平面（machine/facts/roadmap.json）"))}

## 四、这套文档怎么读

想了解什么，就翻对应的一份。七份全部由机器平面自动生成，你只需复审：

| 想知道 | 翻哪份 |
|---|---|
| 这东西为谁做、要解决什么、不碰什么 | `01_产品需求.md` |
| 有哪些功能、数据怎么流、参数为什么这么设 | `02_系统架构.md` |
| 每个数字到底怎么算、外部数据得长什么样 | `03_口径字典.md` |
| 业务上一步步怎么走 | `04_操作流程.md` |
| 这一轮在做什么、怎么算做完、做到哪了 | `05_执行与验收.md` |
| 怎么跑起来、参数改哪、报错了怎么办 | `06_运维手册.md` |
"""
    return body


def render_01(facts: Path):
    """产品需求。事实源：machine/facts/product.json。"""
    prod = load_json(facts / "product.json", {})
    users = [(u.get("who", ""), u.get("want", "")) for u in prod.get("users", [])]
    nots = prod.get("non_goals", [])
    goal = prod.get("goal") or ""
    body = f"""{GENERATED}
<!-- 事实源：machine/facts/product.json -->
<!-- 上限 200 行 -->

# 产品需求

## 一、产品目标

{goal if goal else blank_note("产品目标", "在机器平面写清目标（machine/facts/product.json 的 goal）")}

## 二、给谁用

{table(users, ["用户", "他要什么"], empty=blank_note("目标用户", "在机器平面登记用户（machine/facts/product.json 的 users）"))}

## 三、不做什么

{chr(10).join(f"- {n}" for n in nots) if nots else blank_note("不做清单", "在机器平面登记非目标（machine/facts/product.json 的 non_goals）")}
"""
    return body


def render_03(facts: Path):
    """口径字典。事实源：machine/facts/glossary.json。"""
    g = load_json(facts / "glossary.json", {})
    numbers = [(n.get("项", n.get("item", "")), n.get("裁定", n.get("rule", "")),
                n.get("状态", n.get("status", ""))) for n in g.get("numbers", [])]
    shapes = [(s.get("来源", s.get("source", "")), s.get("要求", s.get("shape", "")),
               s.get("状态", s.get("status", ""))) for s in g.get("data_shapes", [])]
    rules = [(r.get("规则", r.get("rule", "")), r.get("说明", r.get("note", "")))
             for r in g.get("invariants", [])]
    # 术语表 = 通用治理术语 + 项目专属术语（机器平面提供）
    terms = list(COMMON_GLOSSARY) + [
        (t.get("英文", t.get("en", "")), t.get("中文", t.get("zh", "待补中文")),
         t.get("说明", t.get("note", "")))
        for t in g.get("terms", [])
    ]
    body = f"""{GENERATED}
<!-- 事实源：machine/facts/glossary.json -->
<!-- 全项目唯一裁定"一个数字是什么"的地方。有争议以本文件为准。 -->
<!-- 中文门：正文出现的英文术语必须在第五节术语表登记，否则渲染 FAIL。 -->

# 口径字典

## 一、关键数字口径

{table(numbers, ["项", "裁定", "状态"], empty=blank_note("数字口径", "在机器平面裁定（machine/facts/glossary.json 的 numbers）"))}

## 二、外部数据形态

{table(shapes, ["来源", "必须长什么样", "状态"], empty=blank_note("外部数据形态", "在机器平面登记（machine/facts/glossary.json 的 data_shapes）"))}

## 三、恒定为真的规则

{table(rules, ["规则", "说明"], empty=blank_note("恒真规则", "在机器平面登记（machine/facts/glossary.json 的 invariants）"))}

## 四、证据等级定义

| 等级 | 含义 |
|---|---|
| `已提取` | 从代码或配置提取并核对 |
| `已声明` | 只有文档说法，未核对 |

## 五、术语对照

正文出现的英文术语在此登记。

{table(terms, ["英文", "中文", "说明"])}
"""
    return body



def _status_zh(mapping, status):
    """状态值中文化。长的机器串（含下划线）归到其首词的中文，避免污染人类平面。"""
    if not status:
        return ""
    if status in mapping:
        return mapping[status]
    head = status.split("_")[0].split("-")[0].lower()
    if head in mapping:
        return mapping[head] + "（详见机器平面）"
    return status if status.isascii() is False or "_" not in status else "详见机器平面"

def render_02(facts: Path):
    features = load_json(facts / "features.json", [])
    config = load_yaml_or_json(facts / "config.yaml", {})
    contract = load_yaml_or_json(facts / "data_contract.yaml", {})

    STATUS_ZH = {"active": "进行中", "in_progress": "进行中", "completed": "已完成",
                 "done": "已完成", "planned": "计划中", "blocked": "阻塞",
                 "deprecated": "已弃用", "draft": "草案", "pending": "待办",
                 "proposed": "提议中", "reconstructed": "已重建", "verified": "已核实"}
    feat_rows = [
        (f"`{f.get('id', '?')}`", f.get("name", ""),
         _status_zh(STATUS_ZH, f.get("status", "")),
         "已提取" if f.get("evidence") == "extracted" else "已声明")
        for f in features
    ]
    cfg = config if isinstance(config, dict) and "_raw" not in config else {}
    param_rows = [(k, v.get("intent", "") if isinstance(v, dict) else "")
                  for k, v in cfg.get("parameters", {}).items()] if cfg else []
    ent_rows = [(e.get("entity", "?"), ", ".join(e.get("keys", [])), e.get("pk", ""))
                for e in (contract.get("entities", []) if isinstance(contract, dict) else [])]

    body = f"""{GENERATED}
<!-- 事实源：machine/facts/features.json、data_contract.yaml、config.yaml -->
<!-- 上限 200 行 -->
<!-- 纯净门：本文件「一、功能清单」章节出现 phase/gate/review/replay/audit 等日志词 -> 渲染 FAIL -->

# 系统架构

一句话：这个系统有哪些功能、数据怎么从头走到尾、关键参数为什么这么定。

## 一、功能清单

只列**功能本身**。做了什么、过了哪些关，去看 `05_执行与验收.md`。

{table(feat_rows, ["编号", "功能", "状态", "证据"],
       empty=blank_note("功能清单", "从代码里把功能抽进机器平面（machine/facts/features.json）"))}

{"**证据一栏怎么看：** 「已提取」= 从代码或配置里核对过；「已声明」= 目前只有文档说法，还没对过代码。" if feat_rows else ""}

## 二、数据从哪到哪

{contract.get('data_flow') if isinstance(contract, dict) and contract.get('data_flow') else blank_note("数据流说明", "在机器平面写清一份数据流（machine/facts/data_contract.yaml）")}

## 三、关键参数为什么这么定

当前值和改哪里在 `06_运维手册.md`，这里只讲**为什么是这个值**。

{table(param_rows, ["参数", "为什么定这个值"],
       empty=blank_note("参数设计说明", "在机器平面登记参数及其意图（machine/facts/config.yaml）"))}

## 四、数据长什么样

{table(ent_rows, ["数据", "关键字段", "主键"],
       empty=blank_note("数据结构", "在机器平面写清数据契约（machine/facts/data_contract.yaml）"))}
"""
    return body


def render_04(facts: Path):
    flows = load_json(facts / "flows.json", {})
    main_rows = [
        (s.get("step", "?"), s.get("who", ""), s.get("do", ""), s.get("out", ""))
        for s in flows.get("main", [])
    ]
    body = f"""{GENERATED}
<!-- 事实源：machine/facts/flows.json -->
<!-- 上限 150 行 -->

# 操作流程

业务上一步步怎么走。每一步是谁、做什么、产出什么。规则本身（数字怎么算）在 `03_口径字典.md`。

## 一、主流程

{table(main_rows, ["第几步", "谁", "做什么", "产出"],
       empty=blank_note("操作流程", "把业务流程写进机器平面（machine/facts/flows.json）"))}
"""
    return body


def render_05(facts: Path, runs_dir: Path):
    plan = load_json(facts / "plan.json", {})
    acceptance = load_json(facts / "acceptance.json", {})
    runs = []
    if runs_dir.is_dir():
        for f in sorted(runs_dir.glob("*.json")):
            data = load_json(f, [])
            runs.extend(data if isinstance(data, list) else [data])

    now = " · ".join(x for x in [plan.get("stage"), plan.get("phase"),
                                 plan.get("task")] if x) or None
    owner = plan.get("owner")
    acc_rows = [(a.get("id", "?"), a.get("criteria", ""), a.get("status", ""))
                for a in acceptance.get("items", [])]
    run_rows = [(r.get("run_id", "?"), r.get("action", ""), r.get("result", ""))
                for r in runs[-20:]]

    this_round = (f"**在做：** {now}\n\n**负责：** {owner or '待定'}"
                  if now else blank_note("当前任务", "把这一轮的计划写进机器平面（machine/facts/plan.json）"))

    body = f"""{GENERATED}
<!-- 事实源：machine/facts/plan.json、acceptance.json、runs/*.json -->
<!-- 上限 100 行 -->

# 执行与验收

这一轮在做什么、怎么算做完、实际做到哪了。

## 一、这一轮在做什么

{this_round}

## 二、怎么算做完

{table(acc_rows, ["编号", "达成标准", "状态"],
       empty=blank_note("验收标准", "把这一轮的验收标准写进机器平面（machine/facts/acceptance.json）"))}

## 三、已经做了什么（最近 20 条）

{table(run_rows, ["记录", "做了什么", "结果"],
       empty="> 还没有运行记录。每完成一步会自动追加一条，这里就有了。")}
"""
    return body


def render_06(facts: Path, project_name: str):
    config = load_yaml_or_json(facts / "config.yaml", {})
    ops = load_json(facts / "ops.json", {})
    changelog = load_json(facts / "changelog.json", [])

    cfg = config if isinstance(config, dict) and "_raw" not in config else {}
    param_rows = [(k, v.get("value", "") if isinstance(v, dict) else v,
                   v.get("where", "") if isinstance(v, dict) else "")
                  for k, v in cfg.get("parameters", {}).items()] if cfg else []
    err_rows = [(e.get("symptom", "?"), e.get("cause", ""), e.get("fix", ""))
                for e in ops.get("troubleshooting", [])]
    cl_rows = [(c.get("version", "?"), c.get("date", ""), c.get("summary", ""))
               for c in changelog[-10:]]

    body = f"""{GENERATED}
<!-- 事实源：machine/config.yaml、machine/facts/ops.json、changelog.json -->
<!-- 上限 200 行 -->

# 运维手册

怎么把它跑起来、参数在哪调、报错了怎么办、都改过些什么。

## 一、怎么跑

改完机器平面，按顺序跑这三条，让人类平面刷新并过关：

```bash
python3 machine/tools/render_human.py            # 先渲染人类平面
python3 machine/tools/check_doc_budget.py        # 体积、中文、纯净三道门
python3 machine/tools/check_blocker_stop.py      # 阻塞重审门
```

## 二、参数在哪调

改一个参数要动哪个文件，都在这。为什么定这个值在 `02_系统架构.md`。

{table(param_rows, ["参数", "当前值", "改哪里"],
       empty=blank_note("参数清单", "在机器平面登记参数（machine/facts/config.yaml）"))}

## 三、报错了怎么办

{table(err_rows, ["遇到什么", "为什么", "怎么解决"],
       empty="> 还没有登记常见故障。踩过坑、解决了，就往 machine/facts/ops.json 里记一条，这里会自动列出，下次少走弯路。")}

## 四、都改过什么（最近 10 条）

{table(cl_rows, ["版本", "时间", "改了什么"],
       empty="> 还没有变更记录。每次发版往 machine/facts/changelog.json 记一条，这里就有了。")}
"""
    return body


# ---------- 主流程 ----------

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=".")
    args = ap.parse_args()

    root = Path(args.root)
    docs = root / "文档"
    facts = root / "machine" / "facts"
    runs = root / "machine" / "runs"
    project_name = root.resolve().name

    docs.mkdir(parents=True, exist_ok=True)
    rendered = {
        "00_我在哪.md": render_00(facts),
        "01_产品需求.md": render_01(facts),
        "02_系统架构.md": render_02(facts),
        "03_口径字典.md": render_03(facts),
        "04_操作流程.md": render_04(facts),
        "05_执行与验收.md": render_05(facts, runs),
        "06_运维手册.md": render_06(facts, project_name),
    }
    for name, body in rendered.items():
        (docs / name).write_text(body.rstrip() + "\n", encoding="utf-8")

    print(f"渲染完成：{len(rendered)} 个文件全部由机器平面生成")
    return 0


if __name__ == "__main__":
    sys.exit(main())
