# 云平台总览 · status.linzezhang.com

自建只读总览页：把 Coolify 部署记录、主机指标、证书、备份、自愈规则、汇率与成本汇总成一张静态页。
**这里就是它的唯一源码位置**（它曾被误建为独立仓 `LinzeStatus`，已整体迁入本目录，那个仓不再使用）。

## 目录

| 路径 | 作用 |
|---|---|
| `collector/collect.py` | 主采集器，主机 cron **每 1 分钟**跑一次，产出 `data/snapshot.json` |
| `collector/collect_github.py` | GitHub 工程面采集，**三层**：fast 每 1 分钟 / traffic 每 15 分钟 / deep 每小时；公开件写 `data/`，私有件写 `private/` |
| `collector/tests/` | 机器守卫：登记治理、公开面隐私、实时访问分类 |
| `web/` | 静态页壳（自带本地 Chart.js，不取第三方 CDN） |
| `admin/` | `/admin` 价格编辑器与私有视图，前有 Cloudflare Access，后端再自校验 JWT |
| `deploy/` | docker-compose、nginx、自愈引擎与备份脚本、各 cron 片段 |
| `data/prices.json` | 成本价格库，唯一人工维护项（其余全为采集产物） |

## 部署

**host-direct rsync**，不走 Golden Path / Coolify 自动部署 —— 改完要手动同步到主机。

⚠️ **必须排除 `data/`**：`data/prices.json` 是线上通过 `/admin` 编辑的活数据，仓内那份只是初始快照，
整目录同步会把你在后台改过的价格库覆盖回旧值。主机上的 `private/`、`.secrets/` 同理，仓内根本没有。

```bash
rsync -av --exclude 'data/' --exclude 'private/' --exclude '.secrets/' status/ ubuntu@139.99.61.6:/srv/linze/apps/status/
```

只改了采集器时，同步那一个文件最稳：

```bash
rsync -av status/collector/collect.py ubuntu@139.99.61.6:/srv/linze/apps/status/collector/collect.py
```

`collector/` 的改动在下一次 cron（≤1 分钟）生效；`web/` 与 `deploy/` 的改动需重启 `linze-status` 容器。

## 治理规则：部署即登记（强制）

> **凡是部署到 OVH 或 Cloudflare 的软件，都必须在 `collect.py` 的登记表里有归属，
> 并接入实时监控与动态自愈。** 没登记 = 治理违规。

登记表有两张，按性质二选一：

| 表 | 放什么 | 必填 |
|---|---|---|
| `PROJECTS` | 对外的业务线 | `url` `host` `db` `store` `deploy` `backup` `agent` `notify`，以及 `owns`（它拥有哪些运行单元） |
| `PLATFORM` | 不对外的平台底座（代理、网关、备份、隧道…） | `role` `owns` `heal` |

`owns` 声明这条线拥有哪些单元，支持 `container` / `systemd` / `cron` / `coolify` / `image` /
`cloudflare` 几种键；**域名能自动匹配的不用写**（Traefik 的 Host 规则会自动认领）。

### 这条规则不是靠自觉

`collect.py` 的 `discover_units()` 每分钟从 **Docker / systemd / cron / Coolify 库**
四路把主机上真正在跑的东西找出来 —— **完全不看登记表**，再和登记表比对：

- 对得上 → 归到那条业务线，进入九段纵向切片评分；
- **对不上 → 在「软件」页顶部标红为治理违规，并计入 tab 角标。**

先能不看登记表就把东西找全，这条规则才有可能被执行；否则漏登记的永远发现不了。
它已经实打实抓到过一个：`cloudflared.service`（Cloudflare Tunnel，active）此前没出现在任何一张视图里。

### Cloudflare 侧的诚实边界

owner 明确不建 CF 只读令牌，因此 CF 单元**不做账面枚举**，只按登记表做对外可达性实测。
页面上这一段如实标注为「实测而非枚举」——不假装能看到看不到的东西。

### 业务基线纵向切片

每条业务线都要走完九段：`代码源 → CI → 部署 → 运行 → 入口 → 数据 → 备份 → 监控 → 自愈`。
每格都必须带**本轮实测证据**（不是"配置成这样"，而是"刚才测出来是这样"）。
任何一段是黑箱，这条线就不算白箱受控。分数与逐小时历史归档在 `data/baseline_history.json`。

### 改这块时必须同时跑

```bash
python3 status/collector/tests/test_software_registry.py
```

它守两件事：登记表字段完整性，以及**判定逻辑不制造假红**。
假红比没有告警更糟 —— 一旦习惯了红色，真出事那次也不会有人看。
里面每一条 `NoFalseAlarmTest` 都对应一次实测踩出来的误报（timer 驱动的 oneshot、
`OnFailure` 模板实例、`coolify` 前缀吞掉 `coolify-proxy`、部署 `in_progress` 被判失败、
临时构建容器被要求配自愈策略）。

## 约定

- 面向用户的时间一律「北京时间 UTC+8」。
- 采集器只读：唯一写动作是产出快照文件，不碰任何被监控对象。
- **全套跑在云端**：采集、自愈、归档全部由主机 cron 驱动，不依赖任何本机进程、
  不占用本机内存/存储/缓存，也不依赖 agent 与 token（见 CodexProject 的零 Agent 契约）。
- 按仓名分桶的原始归档一律写 `private/`，**绝不能落在 `data/`** —— `data/` 是 nginx 的公开根目录，
  曾因此把私有仓名连同流量数字挂到公网上。`tests/test_public_privacy.py` 守住这条。
