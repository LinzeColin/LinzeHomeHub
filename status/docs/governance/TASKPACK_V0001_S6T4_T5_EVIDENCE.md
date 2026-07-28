# S6-T4 / S6-T5 上线后验证与发布证据(TaskPack v0.0.0.1)

**部署主体**:commit `5840ccd244518d5dd403ebb45014f41f6f620113` · tree `ca890623561226404d27a79fb76a80d9d0d89794`
**制品摘要**:`sha256:b68af28ec99838467ca8fa7dc5d454382d6c92d80ba4ea8d1a598165294e3be6`
**候选与部署主体**:commit 与 tree **完全一致**(STOP_CONDITIONS 第 6 条通过)
**零 Agent 不变量**:`runtime_agent_dependency=false` · `llm_calls=0` · `token_budget=0`(由部署脚本自检写入)

---

## 1. Smoke(OP-004 / 核心路径)

| 项 | 结果 |
|---|---|
| `https://status.linzezhang.com/` | HTTP 200(全过程多次探测未中断) |
| `/admin` | HTTP 302(Cloudflare Access 登录跳转 = 服务存活) |
| `linze-status` 容器 | Up,镜像摘要固定 `nginx@sha256:65645c7b…` |
| `linze-status-admin` 容器 | Up,`RestartCount=0`,镜像 `linze-status-admin:864bfb3a94aa`(内容派生标签) |
| `control-plane.json` | 已产出 156 KB |

### 幂等性(OP-004)—— 实测,不是声称

同一候选连续跑三次 `deploy.sh`,artifact 摘要**三次完全相同**:
```
DEPLOY_PASS candidate=c5220b83… artifact=sha256:45bc88de…
DEPLOY_PASS candidate=c5220b83… artifact=sha256:45bc88de…
DEPLOY_PASS candidate=c5220b83… artifact=sha256:45bc88de…
```
回滚之后再滚回来,摘要仍是 `sha256:b68af28e…`,与回滚前一致 ——
幂等性跨「回滚 + 重部署」依然成立。

## 2. Restore(OP-003:备份存在与恢复验证是两个独立条件)

按手册要求,object 与 hash 从 `backup-status.json` **机械读出**,不是手抄:

```
object = private-database-20260728T093002Z-e67cd654….bundle.enc
sha256 = 494d546e02aefcd92d9bb5517f44c177f74069a959ca6cefcfa72b4d8704182c
```

**主通道**(下载 → 摘要 → 解密 → bundle 校验 → fsck → refs):
```
RESTORE_VERIFIED source=primary refs=16
restore_verified: true
reason: 主通道当轮实跑:下载+摘要+解密+bundle 校验+fsck+refs 全过
```

**异地腿**(必须响一声,不许静默跳过):
```
RESTORE_NOT_POSSIBLE source=offsite reason=one_way_par_channel
```

★ 备份对象是**真实的 Private-Database**,1.2 GB、458 个提交,不是小样本。

## 3. Rollback

```
ROLLBACK_PASS source=/srv/linze/releases/status/previous/
回滚全程站点保持 HTTP 200
```

---

## 4. 上线过程中**实测踩出来**的六个缺陷(全部已修并各带守卫)

这一节是本次交付里最有价值的部分:下面每一条都是真的在生产上炸了才发现的,
没有一条是靠读代码推断出来的。

| # | 缺陷 | 后果 | 守卫 |
|---|---|---|---|
| 1 | `deploy.sh` 只 `chmod` 不 `chown` runtime | admin 容器以 uid 1000 跑,进不去 root:root 目录 → 无限重启,`/admin` 挂掉 | 已补 `chown` |
| 2 | `_project_key` 把 `repo` 排最前 | 同仓 7 个项目塌成 1 个键 → `duplicate entity_id`,控制面采集跑不起来 | `test_multi_project_repo.py` 数量守恒断言 |
| 3 | 运行单元只用名字做键 | `linze-status` 既是 container 又是 cron,撞成一个 | 键加入 `kind` 维度 + 破坏测试 |
| 4 | 仓与项目共用 ID 命名空间 | 修完 #2 后,托管项目的仓被误判 `REPOSITORY_UNREGISTERED`(**我自己引入的假红**) | 分命名空间 + 「消红不消信号」测试 |
| 5 | artifact 摘要含运行副产物 | 同一候选两次部署摘要不同 → 部署主体作为证据失效 | `test_artifact_digest_stable.sh`(双向:副产物变→不动;源码变→要动) |
| 6 | `git bundle verify` 缺仓上下文(backup + restore 两处) | 备份校验与恢复链每次都停在这一步 | 两处都改为 `-C` |
| 7 | 上传用 `--data-binary @file` | 1.2 GB 真实体积下 `curl: out of memory` | 改 `-T` 流式;失败时打印 HTTP 码而非 JSON 崩溃 |

### 特别记一笔:#7 暴露的是「小样本通过」的假象

早先 S4-T2 那次 `BACKUP_READBACK_PASS` 是拿**小对象**测的,所以上传路径的
OOM 从未暴露。备份这件事恰恰只在真实体积下才有意义 ——
**「小样本过了」不等于「真实大小能跑」**,而在此之前这条路径从未在真实大小上验证过。

### 以及 #4:修假红时差点把真信号一起修掉

用生产真实数据实测:修前 `REPOSITORY_UNREGISTERED=5`(其中 3 个是托管着已登记项目的仓,
全是假红);修后 `=2`,恰好是 `AgentDatabase` / `CodexProject` ——
真正没有任何项目引用的那两个。**假红消了,真信号一条没丢**,这是实测出来的,不是设计意图。

---

## 5. 仍然如实写明的边界

- **OCI 异地腿永远不构成恢复证据**。它是只写 PAR,结构上读不回来。
  本轮投递成功(HTTP 200)只说明「投出去了」,不说明「能恢复」。
  账号级失效时它是唯一残余副本,而它读不回来 —— 这一点不许被任何绿盖过去。
- **未安装 systemd timer**。生产四件事(采集 / 备份 / 自愈 / 权威同步)仍在 cron 上跑。
  `install-systemd.sh` 会与现有 cron 重复执行(两个采集器抢写同一份快照、两套备份),
  故本轮不装。要迁移必须同时摘掉对应 cron,属 owner 取舍。
- **`nginx.conf` 仍有 `'unsafe-inline'`**。FE-005 的阈值(zero script execution)改动前后都满足,
  但纵深防御被削弱。移除需要给 145 KB 单文件页面里所有内联脚本/样式配 hash 或 nonce,
  是独立工作量,本轮未做,也不假装已解决。
- **任务包自带 `reconcile_tasks.py` 的 `CONTRACT_CONFLICT` 分支不可达**(见 S6-T2 判决)。
  发布闸门(`converge_candidate.py`)有效,故不阻断,但报告说不清是哪一片。
