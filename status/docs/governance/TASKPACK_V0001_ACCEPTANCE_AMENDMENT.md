# TaskPack v0.0.0.1 — Owner 授权的验收修订(DA-004 / AR-003)

**状态**:生效
**授权人**:owner(Linze)
**授权时间**:2026-07-28
**适用任务包**:`status_linzezhang_taskpack_v0.0.0.1`
**触发条款**:任务包 `execution/STOP_CONDITIONS.md` 第 3 条
—— 「现有 OVH/Cloudflare/OCI 资源不足且会产生新付费或架构变化」

> 任务包禁止执行方自行修改 Acceptance。本文件不是执行方的自我放宽,
> 而是 **owner 在收到下列证据后作出的书面决定**,并按任务包要求的升级格式记录。

---

## 1. 为什么触发

任务包 S4-T2 硬性要求 `LINZE_R2_REMOTE` 与 `LINZE_OCI_REMOTE` 都是**经核验的
rclone crypt remote**(前者底层 R2,后者独立 OCI Object Storage),
且每次上传后必须 readback + digest 比对,并能 `restore`。

### 实采证据(全部为实跑,非推断)

| 事实 | 取证方式 |
|---|---|
| `rclone: command not found`;`rclone listremotes` 无输出 | OVH 主机实跑 |
| `oci: false`、`wrangler: false` | 在 OVH 上跑任务包自带 `context_capture_readonly.py` |
| `/srv/linze/secrets/` 无任何 `r2_*` 凭据(有 `oci_par_url`、`github_backup_pat`) | OVH 实列(仅列文件名,未读取值) |
| 现有异地备份 = GitHub Release 资产(主,每日)+ OCI PAR 只写(备,仅周日) | `status/deploy/linze-offsite-backup.sh` |
| `# R2/D1 需一次性授权(读令牌)才能自动` | 本仓 `status/collector/collect.py` |
| `"backup": "…R2/OCI 专属验证待 CB-530"` | 本仓 `status/collector/collect.py` |
| 任务包自身 reconcile 判定 `r2_backup` / `restore_proof` 均为 `ADAPT_REQUIRED` | `reconcile_tasks.py` 输出 |

**结构性结论**:OCI 侧当前是 **PAR 只写**通道,**读不回来** ——
不是权限没配好,是这个通道形态本身就不支持 readback 与 restore。

### 补齐到原验收需要什么

新建 R2 桶 + 两个独立 crypt remote + OCI 可读凭据 + 在 OVH 安装 rclone。
即**新增云资源、新增凭据、改变备份架构**,正落在停止条件第 3 条。

---

## 2. Owner 的决定

**选择 B:修改验收以适配现有通道,不新建云资源、不新增凭据。**

---

## 3. 修订范围 —— 只动两条,另两条一字不改

复核四条相关 Acceptance 原文后确认:

| Acceptance | 是否点名 R2/OCI | 处理 |
|---|---|---|
| `OP-003` 备份存在与恢复验证是两个独立条件 | 否,与通道无关 | **不修改** |
| `OP-004` 运维命令幂等且可即时验证 | 否,与通道无关 | **不修改** |
| `DA-004` R2 冷备 + OCI 可独立恢复 | **是** | 修订(见 §4) |
| `AR-003` 故障隔离(列举 R2/OCI 平面) | 仅作为平面列举 | 仅改平面命名(见 §5) |

★ `OP-003` 与 `OP-004` 是本次降级中**强度完全不变**的两条。
它们才是「备份 ≠ 可恢复」这条底线的承载者,不随通道改变而放宽。

★ `DA-004` 原文 baseline 即写着
「Public code reflects a **legacy GitHub Release and OCI-oriented** backup path」——
任务包本就知道现状,只是意图迁走。本修订是**不迁**,不是发明新现状。

---

## 4. DA-004 修订后全文

- **requirement**:主对象层为**某个私有仓的 GitHub Release 资产**
  (见 §4a 的目的地约束),存放加密冷备与大体积私有对象;
  OCI PAR 作为**单向异地投递**副本。
- **target**:主通道必须记录 **上传 → readback → digest → 解密 → 归档重建证据**;
  OCI 通道只记录**投递回执**。
- **input**:小体量确定性加密对象集(沿用原文)。
- **oracle**:
  - 主通道:GitHub Release readback / decrypt / 归档重建比对;
  - OCI 通道:**仅** HTTP 投递状态码与对象名回执。
- **threshold**:
  1. 主通道全部还原对象摘要与预期事实一致 → 可判 `RESTORE_VERIFIED`;
  2. **OCI 通道永远不得单独构成 `RESTORE_VERIFIED`**;
  3. **恢复能力总体判定只取主通道**;OCI 缺失或失败只降级为「异地副本未确认」,
     不得使整体判定变绿,也不得因其存在而使整体判定变绿。
- **evidence**:备份清单 + 主通道恢复清单 + OCI 单向投递回执(三份分开)。

### ★ 反假绿约束(本修订的核心,不可删)

1. OCI 腿的能力从「可独立恢复的异地副本」**降级为「单向投递回执」**,
   且**降级本身必须在看板与报告中显式可见** ——
   标注为 `一次性投递,未回读验证`,禁止以任何形式并入「已验证恢复」计数。
2. 禁止用「备份文件存在 / 文件较新 / HTTP 200」替代恢复证据(此即 `OP-003` 原意)。
3. 主通道的恢复证据必须是**当轮实际执行**的 readback+decrypt+重建,
   不得复用历史结论。

---

## 4a. 目的地约束 —— 修订我自己第一版写错的地方

第一版本文件把主通道写成「**Private-Database** 的 Release 资产」。**这是错的。**

`backup.sh` 备份的对象正是 **Private-Database 仓的 git bundle**。把它存进
Private-Database 自己的 Release,等于**把备份放在被备份对象内部** ——
该仓一旦被删除或失去访问,备份与源同归于尽。这不是可接受的冷备。

因此加入两条**硬性目的地约束**,由脚本在运行期强制,不是文档口号:

1. **目的地仓不得等于被备份的仓**(防循环依赖);
2. **目的地仓必须是私有仓**(防私有数据进公开面)。

默认目的地取 `LinzeColin/Governance`(既有私有仓,不新建云资源)。
可由 `LINZE_BACKUP_GH_REPO` 覆盖,但上述两条约束不可绕过。

★ 同时如实写明本方案的**保护边界**:
GitHub Release 资产与 git 对象是分开存储的,所以它能防住
**误强推 / 历史重写 / 分支删除**;但它**防不住整个 GitHub 账号级失效**。
OCI PAR 那条单向腿是账号级失效时的唯一残余副本,而它读不回来 ——
**这一点必须在看板上写着,不许被「已验证恢复」的绿盖过去。**

## 5. AR-003 修订

仅将故障隔离平面的命名由
`Status / GitHub / Private-Database / R2 / OCI`
改为
`Status / GitHub / Private-Database / GitHub Release 对象层 / OCI 单向异地`。

隔离要求、输入、判据、阈值**均不变**。

---

## 6. 本次已取得的可行性证据

在 OVH 上对**真实最新备份**实跑主通道恢复链(仅验结构,未读业务内容):

```
资产      linze-backup-20260727-034001.tar.gz.enc   61,433,200 B
readback  下载 61,433,200 B == 期望大小              ✓
digest    fb5c2dd30865d6929a3401196563ef0062fe16dbe0c32b9d688b5d61fc1460ed
解密      openssl aes-256-cbc 成功,明文 61,433,181 B ✓
归档      tar 列出 15 条目,顶层 config / db / status  ✓
```

### 这条证据**没有**证明什么(必须写明,避免被当成整机恢复已验证)

- 只证明「归档可下载、摘要可算、可解密、结构完整」;
- **未**证明整机恢复:未做落盘重建、未做数据库导入、未做服务起停验证;
- 未校验归档内单个文件的摘要(下一步 S4-T2 实现时补:对 `db/*.sql.gz`
  只做 `gzip -t` 完整性校验,**不读取其内容**,因其含财务数据)。

---

## 7. 不受本修订影响的部分

- 生产 0 Agent / 0 LLM / 0 Token / 无 launchd:不变;
- 不冻结 `main`:不变;
- 凭据值不得进入 Git、日志或任务包:不变;
- `candidate` 与 `deployment artifact` digest 必须一致方可上线:不变;
- 其余 21 个任务的 Acceptance:不变。
