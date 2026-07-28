# S6-T2 验证判决(TaskPack v0.0.0.1)

**判决**:`PASS`
**候选主体**:commit `c953924f7ed9942d1414c07e52e5287ffd266198` · tree `1d5fd6a516654e063ceb3633f8b9b617273eadf2`
**Acceptance Hash**:`e68f17b8757c9394eee526834256a3d3bb71e2117c54ba1cbd90fba33361a35a`
**验收关联**:GO-001 / GO-004

## 证据

| 项 | 结果 |
|---|---|
| 冻结单元测试 | 37 通过 |
| 治理检查 | acceptance 32 · tasks 25 · fixtures 18 · 无环 |
| 策略扫描 | 0 违规 |
| Playwright(desktop + mobile) | 4/4 |
| 自愈复探 shell 守卫(OVH 实跑) | `SELFHEAL_POST_PROBE_PASS` |
| **盲测集 18 条** | **18 PASS · 0 FAIL · 0 NOT_RUN** |

盲测明细见 `TASKPACK_V0001_BLIND_SET_RESULT.json`。

## 关于「18 条」这个数字

冻结的 `governance_check.py` 对 `blind_fixtures.yaml` 只检查 id 唯一与引用有效,
**一条场景都不执行**。它输出的 `"fixtures": 18` 是「声明了 18 条」。
本轮新增 `tests/status-control-plane/blind_set.py` 给每条绑真实判据后才逐条跑,
所以这里的 18 是「验过 18 条」。两个 18 含义完全不同。

`blind_fixtures.yaml` 自身声明 `blind_set_count: 1` 与实际 18 条不符,
属冻结件元数据陈旧,已在结果 JSON 里同时记录声明值与实际值,未据此推断任何结论。

## 本轮修掉的真问题

**AR-004(BF-018)在此之前没有任何执行判据** —— `policy_scan.py` 只查模型 API 域名
与 token 形状,不查供应链可变性。补上判据后立刻红:`linze-status-admin:latest`
正在生产跑着,没有不可变部署主体。已修:

- `nginx` 固定 `@sha256`(摘要取自生产实跑镜像的 RepoDigest);
- `admin` 是本地构建、无 registry digest,改用**构建上下文内容哈希**作标签,
  由 `deploy/control-plane/admin-image-tag.py --check` 守着。

守卫做了四路破坏测试并全部变红:改回 `:latest`、挂一个随手起的假标签
(证明不是只匹配 `latest` 字符串)、`nginx` 去掉摘要、改 `admin` 源码不重算标签。

## 已知缺陷:记录但不阻断发布

任务包自己的 `reconcile_tasks.py` 中,逐任务 `CONTRACT_CONFLICT` 分支**不可达** ——
能产出该状态的探测器只有 `status_directory`,而它不在 `DETECTOR_TASKS` 映射里。
实测:在一个连 `status/` 都没有的仓上跑,0 个任务被判冲突、退出码 0(其第 91 行本应返回 4),
还有 10 个任务被判 `ALREADY_SATISFIED`。

**为什么不阻断发布**:真正的发布闸门不是这个分类器,而是 `converge_candidate.py`。
沙箱实测(造真 rebase 冲突):它退出码 4 且**不产出 `candidate-subject.json`** ——
没有候选主体就无从部署。所以冲突时发布确实会被拦住,只是报告说不清是哪一片。

这是冻结件的缺陷,按 GO-003 不自行修改任务包。建议在下一版任务包里
把 `status_directory` 纳入 `DETECTOR_TASKS`,或让分类器对缺失 `status/` 直接整体停。

## 同样必须写明:8 个任务的 `ALREADY_SATISFIED` 不构成证据

`reconcile_tasks.py` 第 72 行对**零探测器**的任务兜底成 `ALREADY_SATISFIED`。
S0-T1/T2/T3 与 S6-T1…T5 共 8 条属于此类 —— 含此刻显然尚未发生的 S6-T3(部署)
与 S6-T4(恢复验证)。这 8 条的状态含义是「没有任何东西在测它」,
不得当作已满足的证据。详见 `TASKPACK_V0001_S6T1_DRIFT_REPORT.md` §1。
