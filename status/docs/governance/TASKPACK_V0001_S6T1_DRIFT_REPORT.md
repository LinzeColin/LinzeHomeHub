# S6-T1 漂移消解报告(TaskPack v0.0.0.1)

**任务**:S6-T1 Fetch latest main, reconcile drift and create final candidate
**验收关联**:INV-006 / AR-002 / GO-002
**基线**:`origin/main` = `6259d5a`(本轮 fetch 后与分叉点一致,**main 无新增提交,零冲突**)

---

## 1. 先说这份 reconciliation 报不了什么

`implementation/scripts/reconcile_tasks.py` 第 72 行是这样兜底的:

```python
state = "APPLY_CLEAN" if task["category"] == "implementation" else "ALREADY_SATISFIED"
```

也就是说,**一个任务如果没有任何探测器指向它,就会被判成 `ALREADY_SATISFIED`**。
25 个任务里有 8 个是 `detectors=0`:S0-T1/T2/T3 与 S6-T1…T5。

结论:对这 8 个任务,`ALREADY_SATISFIED` 的含义不是「测过,满足」,而是
**「没有任何东西在测它」**。最刺眼的是 S6-T3(部署)和 S6-T4(恢复验证)——
它们此刻显然还没发生,报告却写着 ALREADY_SATISFIED。

所以本报告把 25 条重新分成两栏:**被测过的 17 条** 与 **没被测的 8 条**。
后者不算证据,只能靠各自任务自己的产出来证明。这不是给任务包挑刺,
是因为如果直接把这份 JSON 当作交付证据,就会出现「没做的事显示成已满足」。

| 分类 | 条数 | 能不能当证据 |
|---|---|---|
| 有探测器支撑 | 17 | 能 |
| 零探测器兜底成 ALREADY_SATISFIED | 8 | **不能** |

---

## 2. 探测器的 8 条 ADAPT_REQUIRED —— 逐条实测复核

探测器本身多数是**关键字/字面量扫描**,会双向失真:既可能误报,也可能漏报。
所以每条都对着冻结验收原文和真实代码复核过,不采信关键字结论。

| 探测器 | 真实情况 | 判定 |
|---|---|---|
| `hard_coded_external_green` | `collect.py` 里 NitroSend / OVH VPS-1 / OCI 卡三处 `"ok": True` **确实是写死的** | ★ 真缺口,已修 |
| `selfheal_truthful_post_probe` | `linze-selfheal.sh` 重启后**从不复探**,且提前清零失败计数 | ★ 真缺口,已修 |
| `immutable_admin_image` | `linze-status-admin:latest` **确实在生产跑着**(已在 OVH 实列容器) | ★ 真缺口,交 S6-T3 |
| `static_project_denominator` | 覆盖率分母已是 `measurable_n`(按格子实算);`PROJECTS` 只作迁移输入,而探测器的 detail 明确允许它保留 | 探测器失真,属性已满足 |
| `shell_true` | `run()` 确实 `shell=True`,但流经它的探针 URL 在 `_pr_http` 有严格 https 白名单正则,shell 元字符进不来 | 注入面已封,详见 §4 |
| `admin_transaction_outbox` | 关键字扫的是 `admin/app.py`,而 outbox 实现在 `controlplane/db.py`,已有冻结单测覆盖 | 探测器扫错文件 |
| `restore_proof` | 扫的是遗留 `linze-offsite-backup.sh`;恢复证明在新的 `deploy/control-plane/restore.sh` | 探测器扫错文件,但见 §4 |
| `r2_backup` | owner 已选方案 B,不新建 R2 | 按授权修订,**永久保持此状态** |

另有一条 `csp_inline` 触发,但它**不在 `DETECTOR_TASKS` 映射表里** ——
探测到了却没有任何任务认领。见 §4。

---

## 3. 本轮实际修掉的三处假绿

三处都命中 **INV-006 之外的阻断级不变量 INV-005**:
「UNKNOWN / UNVERIFIED / NOT_RUN 等永不聚合成 PASS 或绿」。

### 3.1 供应商状态写死绿(`collect.py`)

- `NitroSend` 从来没被探过,却常年 `"ok": True`。改为 `None` + 文案「未探测 · 无公开状态页,不代表异常」。
  生产禁止调 agent / 带 token 的接口,所以「未探测」就是这里能给的最强真话。
- `OVH VPS-1` 同样写死。新增 `ovh_self_state(host)`,**从本轮真读到的 `/proc/uptime` 等指标派生**:
  读得到才判绿,读不到就是未知,磁盘或内存 ≥95% 判 False。
  供应商卡与「外部服务」列表**共用这一个判定来源**,两处不可能再互相矛盾。
- `OCI` 卡写死绿 + 文案「离机副本 · 只写保险柜」。它是 PAR 单向通道,
  **结构上读不回来**,按 owner 授权的 DA-004 修订 §4「反假绿约束」第 1 条,
  永远不得并入「已验证恢复」。改为 `None` + 「单向投递 · 读不回来,无法验证可恢复」。

### 3.2 自愈谎报「已恢复」(`linze-selfheal.sh`)

改动前的看门狗:

```bash
docker restart "$cname" && ok=1 || ok=0
wd_last="…已自动重启容器 $cname"     # ok 根本没进这句话
echo 0 > "$SD/fail_$grep_name"       # 还没验证就把失败计数清零
```

三个问题指向同一个后果:**自愈会报告自己修好了,哪怕根本没修好**。
`docker restart` 返回 0 只说明重启指令被接受,不代表服务起来了;
失败计数提前清零,还让下一轮必须重新攒够阈值才肯再动手。

改为:重启 → 有界复探(最多 4 次 × 5 秒)→ 通了才写「已恢复」、才清计数;
没通就如实写「未恢复」并**保留**失败计数。元自愈(采集器看门狗)同理:
不看退出码,只看**快照 mtime 是否真的往前走了**。

状态口径也一并修正为四态。原来 `acted` 是由 `wd_online < wd_total` 推出来的,
救回来之后在线数补齐、状态退回 `ok` —— 「修好了」和「压根没坏过」被显示成同一个。
现在按「这一轮到底发生了什么」判:`ok` / `warn`(没到动手条件)/ `acted`(动手且复探通过)/ `failed`(动手没救回来)。

### 3.3 前端把认不出的状态兜底成绿(`web/index.html`)

```js
const st=['ok','warn','acted','pending'].includes(r.state)?r.state:'ok';   // 改动前
```

任何没见过的状态名、任何拼错、任何 `selfheal.json` 被写坏的字段,
都会**静默渲染成绿点**。这正是 INV-005 禁止的方向。
兜底改为 `'unknown'`,并给 `failed` / `unknown` 补了可见样式与**文字标签**
(颜色不单独承担状态)。

> 注:若不修这一条,§3.2 新增的 `failed` 状态上线后会被渲染成绿点 ——
> 修了自愈却让前端把它盖回去,等于没修。

---

## 4. 已知但**本任务不处理**的四项(不静默跳过,写明去向)

| 项 | 现状 | 去向 |
|---|---|---|
| `linze-status-admin:latest` | 生产实跑的可变镜像标签,没有不可变部署主体 | **S6-T3**(该任务的产出就是 `deployment_subject`) |
| `nginx.conf` 的 `'unsafe-inline'` | `script-src` / `style-src` 都开着 | 见下 |
| `run()` 的 `shell=True` | 探针 URL 白名单已封死注入面;`psql()` 仅转义双引号 | 见下 |
| 遗留 `linze-offsite-backup.sh` 无 restore 证明 | 每日 03:40 真跑的是它;恢复证明在新脚本里 | 见下 |

**关于 `unsafe-inline`**:冻结验收 FE-005 的阈值是「zero script execution」,
改动前后都满足(已实测注入载荷保持纯文本)。`unsafe-inline` 削弱的是纵深防御,
不是当前有洞。移除它需要给一个 145 KB 的单文件页面里所有内联脚本/样式配 hash 或 nonce,
是独立的一块工作量,**不在本任务范围内,也不假装已解决**。

**关于 `shell=True`**:`_pr_http` 用 `^https://[A-Za-z0-9.-]+(/[A-Za-z0-9._~/-]*)?$`
把 URL 卡死,shell 元字符(`$` 反引号 `;` `|` 空格 引号)一个都过不去,
所以 flow.yaml 这条不可信输入路径进不了 shell。`psql()` 只转义双引号,
但它的入参全是源码里的字面 SQL,没有外部输入流入。
**结论:当前无可达注入路径**;彻底改成 argv 适配器是好事,但属于重构,不在本任务。

**关于遗留备份脚本**:新的 `deploy/control-plane/backup.sh` / `restore.sh` 已实现
上传→回读→摘要→解密→重建的完整链(负控与正控均在 OVH 实跑通过),
但**它们尚未接管每日 cron**。在接管之前,每天真正执行的仍是没有恢复证明的老脚本。
这一点由 **S6-T4** 的 restore 验证与 **S6-T5** 的恢复事实同步收口。

---

## 5. 本轮新增的守卫与破坏测试

新增 `tests/status-control-plane/unit/test_legacy_plane_honesty.py`(12 条)
与 `tests/status-control-plane/shell/test_selfheal_post_probe.sh`(10 条断言)。

**每条守卫都在真文件上做过破坏测试** —— 只让测试自己内部构造的样本变红不算数:

| 破坏 | 结果 |
|---|---|
| NitroSend 改回写死 `True` | FAILED(3) |
| `ovh_self_state` 改成永远 True | FAILED(1 fail + 1 error) |
| OCI 卡改回绿 | FAILED(2) |
| 前端兜底改回 `'ok'` | FAILED(2) |
| `post_probe` 谎称永远通过 | 10 条断言里失败 4 |
| **整段还原成改动前的老逻辑** | 10 条断言里失败 6 |
| 全部恢复 | 全绿 |

最后一行是关键:**改动前的生产代码放进这套测试就是红的**,
说明这不是补了一层装饰,而是原先确实不满足 OP-002。

---

## 6. 收敛结果

```
冻结测试   单元 37(原 25 + 新增 12) · 治理 32/25/无环 · 策略 0 违规
           shell 守卫在 OVH 实跑 SELFHEAL_POST_PROBE_PASS(本机非 Linux,按设计 SKIP)
           ALL_FROZEN_TESTS_PASS
main       未冻结;本轮 fetch 后无新增提交,rebase 零冲突
```
