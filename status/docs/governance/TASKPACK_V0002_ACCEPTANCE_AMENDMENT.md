# TaskPack v0.0.0.2 — Owner 授权的验收修订（AC-004 顺延）

**状态**：生效
**授权人**：owner（Linze）
**授权时间**：2026-07-29
**适用任务包**：`status_linzezhang_v0.0.0.2_SEALED_TASKPACK_FINAL_20260728`
**冻结 Acceptance 摘要**：`sha256:9d012aebc67af45056706fc9c20884906bea9bd7118d9be8d17a95871639048d`
**触发条款**：任务包 `BUILD_AGENT_INSTRUCTIONS.md` 版本变化门 / `RISK_REGISTER` 之
「R2/OCI 只有上传无恢复证明」（P0）
**前序修订**：`TASKPACK_V0001_ACCEPTANCE_AMENDMENT.md`（2026-07-28 生效）

> 任务包禁止执行方自行修改 Acceptance。本文件不是执行方的自我放宽，
> 而是 **owner 在收到下列当轮实跑证据后作出的书面决定**，按前序修订的同一格式记录。

---

## 1. 为什么再签一次

v0.0.0.1 的修订已经认定：本环境 OCI 侧是 **PAR 只写**通道，结构上读不回来。
但 v0.0.0.2 是**独立封印**的任务包，它的 `ACCEPTANCE_CONTRACT.json` 里
`AC-004` 原文仍是：

> **title**：R2 与 OCI 可独立恢复
> **threshold**：缺失/多余/摘要不一致均为 0；关键事实一致

冻结合同不会自动继承上一版的修订。执行方也**不得**替 owner 认定它继承。
所以这条要么由 owner 明确顺延，要么本版就停在 `BLOCKED` —— 本文件是前者。

## 2. 当轮实跑证据（2026-07-29，非推断、非复用历史结论）

在 OVH 生产节点 `vps-83b882b4`（139.99.61.6）上实际执行：

| 事实 | 取证方式 |
|---|---|
| rclone 二进制在（v1.74.4），但 `ubuntu` 与 `root` **都没有 `rclone.conf`**，`listremotes` 零输出 | OVH 实跑 |
| 生产 `backup.sh` 自述：主通道 GitHub Release 资产（可回读），异地腿 OCI PAR（只写） | OVH 实读 |
| `backup-status.json` 自报 `offsite_readback_supported=false`、`restore_verified=false` | OVH 实读 |
| 主腿实跑恢复：`RESTORE_VERIFIED source=primary refs=16` | `restore.sh r2` 实跑 |
| 异地腿实跑恢复：`RESTORE_NOT_POSSIBLE source=offsite reason=one_way_par_channel` | `restore.sh oci` 实跑 |

主腿完整链路当轮跑通：GitHub Release 下载 → `openssl-aes-256-cbc-pbkdf2` 解密 →
sha256 精确匹配 `494d546e02aefcd92d9bb5517f44c177f74069a959ca6cefcfa72b4d8704182c` →
`git bundle verify` → **从空目录恢复出 16 个 ref**。

**结构性结论未变**：补齐到 AC-004 字面要求需要新建 R2 桶 + 两个独立 crypt remote +
OCI 可读凭据 + 在 OVH 安装并配置 rclone，即**新增云资源、新增凭据、改变备份架构**。

## 3. Owner 的决定

**AC-004 的 v0.0.0.1 修订顺延至 v0.0.0.2，不新建云资源、不新增凭据。**

`AC-004` 在本版按下述口径判定，其余 14 条 Acceptance **一字不改**：

- **主通道**（GitHub Release 资产）：必须当轮实跑
  上传/下载 → readback → digest 比对 → 解密 → 归档重建，全过才可判 `RESTORE_VERIFIED`。
- **异地腿**（OCI PAR）：**只**记录投递回执（HTTP 状态码 + 对象名）。
- **恢复能力总体判定只取主通道**。

### ★ 反假绿约束（与前序修订一致，不可删）

1. **OCI 腿永远不得单独构成 `RESTORE_VERIFIED`**，也不得因其存在而让整体判定变绿；
   降级本身必须在看板与报告中显式可见，标注为「一次性投递，未回读验证」，
   禁止以任何形式并入「已验证恢复」计数。
2. 禁止用「备份文件存在 / 文件较新 / HTTP 200」替代恢复证据。
3. 主通道的恢复证据必须是**当轮实际执行**的 readback + decrypt + 重建，
   不得复用历史结论。
4. `AC-003`（authority 幂等写入与逐字节 readback）、`AC-010`（缺失或陈旧 fail-closed）
   强度完全不变 —— 它们才是「备份 ≠ 可恢复」这条底线的承载者。

## 4. 本修订**不**覆盖的东西

- 不改变 `AC-011`（前端资源真实部署可达）。该条仍要求仓内、部署清单、
  生产 HTTP、digest **四者一致**；本轮实测发现 nginx 的 `try_files … /index.html`
  会让**任何路径都返回 200**，因此该条只能以**内容 + digest** 判定，不得以状态码判定。
- 不构成对 `OR-CAPTURE`（真实 run ≥95%）的任何放宽。
- 不授权部署。部署与生产验收另行决定。

## 5. 生效后的 Oracle 影响

`OR-RESTORE` 由 `UNKNOWN` 转为 `PASS`，依据为本文件 §3 口径 + §2 当轮实跑证据。
其余非 PASS Oracle 不受影响。
