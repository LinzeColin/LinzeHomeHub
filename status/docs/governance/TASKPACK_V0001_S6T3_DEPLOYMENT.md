# S6-T3 部署记录与部署主体绑定(TaskPack v0.0.0.1)

**状态**:**部分完成** —— 遗留平面已上线并验证;控制面部署**被环境前置条件阻断**,已升级给 owner。

## 1. 已合并

PR [#57](https://github.com/LinzeColin/LinzeHomeHub/pull/57) squash 合并进 `main`,`main` 未冻结。

| | |
|---|---|
| 候选 commit | `a36819d6c885b9c3a2aa7426620e21a16e23dbf6` |
| 候选 tree | `60dbdc2993157d77bf8dec95aed4a6ebd0dc02c5` |
| Acceptance Hash | `e68f17b8757c9394eee526834256a3d3bb71e2117c54ba1cbd90fba33361a35a` |
| 三个 CI job | browser / deterministic / dual-plane 全绿 |

> 合并前候选曾绑在 `236610d`,与合并后 `main` 的 tree 不一致 —— 因为其后还有一次
> CI 修复提交。按 STOP_CONDITIONS 第 6 条不允许带着不一致往下走,
> 所以把 worktree 对齐到合并后的 `main` 重新收敛,候选与 `main` 的 tree 现已完全相同。

## 2. 已上线:遗留平面(host-direct)

生产真正每分钟在跑的是 `collector/collect.py`,每 5 分钟跑 `/usr/local/bin/linze-selfheal.sh`,
nginx 从 `web/` 提供页面。这三处正是本次修掉假绿的地方,走既有 rsync 通道上线。

**部署主体 = 逐文件摘要,候选与生产逐字节一致:**

| 文件 | 候选 = 生产 |
|---|---|
| `collector/collect.py` | `d9c03f5353e8733a0ecc312820e6f007499c4737e42771c0494249cd258d6054` |
| `web/index.html` | `460c98a1bc2c02a4f0e76ee5b64979a4331994eac28f4c4ac5713cecdf25269f` |
| `/usr/local/bin/linze-selfheal.sh` | `9404efba9cb45dfd48d1957e582787fbdb32311eec392c4d58afc47e8402578d` |

`web/index.html` 的**线上实测摘要**(`curl https://status.linzezhang.com/`)
与候选完全相同,即部署主体经公网侧独立核验,不是只比对了本机文件。

**回滚点**:`/srv/linze/releases/status/20260728T060209Z-pre-taskpack-v0001`
(含 `collector/`、`web/`、`linze-selfheal.sh` 的部署前完整副本)。

### 上线后实测

```
NitroSend      ok=None   未探测 · 无公开状态页,不代表异常
OVH VPS-1      ok=True   在线 10 天 · 负载 0.82        ← 从真实指标派生,非常量
卡片 oci       ok=None   单向投递 · 读不回来,无法验证可恢复
自愈 watchdog  state=ok  3/3 host-direct 服务在线
```

⚠ 一个必须写明的时序事实:刚部署完那一轮,NitroSend 仍显示旧的写死绿。
原因是 `externals` 有 5 分钟缓存,那一轮复用的是上一份快照里的旧值。
等缓存过期后复测才转为 `未探测`。**这条是等到实测转变之后才记的,不是推断的**。

## 3. 被阻断:控制面部署(`deploy/control-plane/deploy.sh`)

三条阻断,任意一条都足以让这一步停下:

### 3.1 缺受保护环境文件,且**我不能创建它**

`deploy.sh` 要求 `/srv/linze/apps/status/.secrets/control-plane.env`(实测**不存在**,
脚本会 exit 78)。该文件按 `ENVIRONMENT_REQUIREMENTS.md` 必须含
`CF_ACCESS_AUD`、`CF_ACCESS_ISSUER` 等**凭据值**。

写入凭据不在我可执行的范围内 —— 这一条没有变通余地,只能由 owner 亲自建立。

### 3.2 该文件的必填项与 owner 已作的决定相互矛盾

它要求 `STATUS_BACKUP_ENCRYPTION_PROFILE=rclone-crypt`,并要求
`LINZE_R2_REMOTE` / `LINZE_OCI_REMOTE` 都是**经核验的 rclone crypt remote**。
而 owner 已在 DA-004 修订中选定**方案 B:不新建 R2、不装 rclone、沿用现有通道**。

也就是说:按这份要求填这个文件,等于推翻 owner 已经作出的决定。
必须先明确以哪一个为准,不能由我替 owner 选。

### 3.3 `install-systemd.sh` 会与现有 cron **重复执行**

它会 `systemctl enable --now` 四个 timer:
`control-plane-collect` / `authority-sync` / `backup` / `selfheal`。

生产实测:**当前没有任何 linze systemd timer,这四件事全部由 cron 在跑**
(`/etc/cron.d/linze-status`、`linze-github`、`linze-selfheal`、`linze-offsite-backup`)。
两套同时启用会导致两个采集器抢写同一份快照、两套备份、两个自愈引擎互相打架。

这不是风格问题,是会真的弄坏自运行的。要么迁移到 systemd 并**同时摘掉对应 cron**,
要么继续用 cron 而不装 timer —— 这是 owner 的取舍。

## 4. 因此,S6-T4 / S6-T5 未开始

按 `/goal` 的要求(关键任务未 PASS 或证据缺失时禁止进入后续依赖任务),
S6-T4(smoke / restore / rollback)与 S6-T5(同步发布事实)在 §3 得到答复前不启动。

已具备的部分能力已在别处实测过,但**不能拿来冒充 S6-T4 已完成**:
备份/恢复脚本的四条负控与一条正控此前已在 OVH 实跑通过
(`BACKUP_READBACK_PASS`),但那是脚本级验证,不等于控制面上线后的恢复演练。
