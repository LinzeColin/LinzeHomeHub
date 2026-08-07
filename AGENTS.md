# AGENTS.md — LinzeHomeHub

## Language

默认中文回复；代码、API、库名、错误信息可保留英文。

## Product Rules

- 首屏标题必须是 `Linze Home Hub`。
- 禁止显示 `A living atlas of systems, memory, research, and tools.`。
- `Archive / Nebula / Voyage / Garden` 必须是四套不同系统，不只是换色。
- 项目入口是整张星球卡点击。
- 不显示显式 `Open / Docs / GitHub` 按钮。
- 不显示最后更新时间字段。
- 滚动方向和速度必须影响粒子、物理和读数。
- 支持 `prefers-reduced-motion` 和质量分档。


## 零 Agent 依赖 / 零 Token 消耗(硬性方向,新功能一律照此设计)

**目标:整个系统在没有任何 agent、没有任何模型调用的情况下,自己长期跑下去。**
Agent 只应出现在「开发期」,不得成为「运行期」的必要零件。

### 三条硬规则

1. **运行期禁止调用任何大模型 / AI 接口。**
   线上代码(前端、采集器、cron、自愈脚本、Worker)不得请求 OpenAI / Anthropic / Gemini
   等推理接口,也不得依赖 agent 定时来「跑一下」。违反即为架构缺陷,不是功能。
2. **数据靠派生,不靠生成。**
   需要新指标时,优先从**已经采到的数据**里纯计算推导(见 `status/collector/` 的
   `inventory()` / `selfheal_state()` / `project_graph()`);其次才是新增一个便宜的
   只读 API 轮询;**永远不要**用模型去「总结」「猜测」或「补全」运行期数据。
3. **自运行 + 自愈。**
   新增的后台工作必须是 cron/systemd 定时器,并且要能被自愈引擎看住
   (见 `status/deploy/linze-selfheal.sh` 的主自愈与元自愈两套)。
   不允许出现「要人手动跑一下」或「等 agent 来跑」的环节。

### 配套要求

- **额度要看得见**:任何新接的外部服务,都要把用量/额度接进 status 的「用量」页,
  并如实标注取不到时的状态(`UNAVAILABLE`),**绝不估算、绝不编造费用**。
- **成本要能归零验证**:优先选公开仓/免费额度内的方案;若确有付费,必须能在
  status「成本」页看到真实账单口径(见 GitHub `billing/usage` 的 `netAmount` 做法)。
- **凭据最小化**:能不新增 token 就不新增(例:status 的备份与关系图都是搭已有机制的车)。
- **守卫**:`npm run validate` 会扫描运行期源码里的 AI 接口域名,发现即失败,
  防止这条规则随时间被悄悄破坏。

> 已按此建成的例子:status 采集(cron 1 分钟)、自愈引擎(cron 5 分钟,两套)、
> 每日加密备份上 GitHub、**home 的全仓关系图**(纯派生 `graph.json`,浏览器直接读)。
> 这些全部 0 agent / 0 token。

## Validation

优先运行：

```bash
npm run validate
npm run build
npm run preview
npx wrangler deploy --dry-run
```

## Safety

不要提交 secrets、token、私有数据、原始导出、浏览器状态、cookie、session、本机凭据或不必要的本机绝对路径。

## 数据落地（长期有效 · 自运行分仓治理）

开发中新产生的任何长期/业务/运行时数据一律写私有仓 `LinzeColin/Private-Database`（其余项目数据 → `Private-MetaDatabase/`），用 `private_db_client.py` 免 clone 读写；**禁止把数据提交进本代码仓**，派生/临时物走 `.gitignore`。`status/` 公开监控数据按设计随备份入仓属例外。目的：分仓治理长期自运行，不需人工反复迁移。

---

## 云成本红线：对象存储必须零付费（Owner 硬指令 · 长期有效）

**云端账单必须恒为 $0.00。不允许任何 agent 触发收费行为。**

1. **禁止 `InfrequentAccess` 存储类** —— 建桶、写对象、生命周期转换，一律不许。
   R2 的免费额度（10GB 存储 / 100 万 Class A / 1000 万 Class B）**只覆盖 Standard**；
   IA 从第 1 次操作起计费，且**按整计费单位向上取整**。
   2026-08-07 实账单：**51 次 IA 操作 = $9.00**，同期 **301 万次 Standard 操作 = $0.00**。
   根因是建桶时默认存储类选了 IA，写入端不指定存储类就全部继承 —— 一次手滑，之后静默自动计费。
2. **禁止"整包下载来判断存在 / 做校验"的高频轮询。** 判断对象存在用 `HeadObject`
   （写入时把 sha256 放进对象 `Metadata`，Head 就读得到）；真要逐字节复核，
   **按天或按周跑，不许按分钟跑**。
   反例：memory-atlas reconcile 每 15 分钟把 2466 个对象整包拉一遍核 sha256，
   折合 71 万次 Class B/天、21.3M/月，直接打穿 10M/月免费额度。
3. **新增或改动任何周期性任务，先算月操作量**：
   `每轮操作数 × 每天轮数 × 31 < 免费额度 × 50%`。**算不出来就不上线。**
4. **存储优先级**：**GitHub Release 资产 > R2 > OVH 本地**。
   Release 资产不计仓库体积、没有操作计费，永远优先。

完整事故记录、账单逐行归因、免费额度速查表 → **`Private-Database` 仓 `OPS/AGENT_ONBOARDING.md` §9.7**。
机器守卫 → OVH `/usr/local/bin/linze-r2-free-tier-guard.py`（每 6 小时，非 Standard 桶自动熔断改回；
判定 `/srv/linze/apps/status/data/r2_free_tier_guard.json`）。
