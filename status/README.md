# 云平台总览 · status.linzezhang.com

自建只读总览页：把 Coolify 部署记录、主机指标、证书、备份、自愈规则、汇率与成本汇总成一张静态页。
**这里就是它的唯一源码位置**（它曾被误建为独立仓 `LinzeStatus`，已整体迁入本目录，那个仓不再使用）。

## 目录

| 路径 | 作用 |
|---|---|
| `collector/collect.py` | 主采集器，主机 cron 每 15 分钟跑一次，产出 `data/snapshot.json` |
| `collector/collect_github.py` | GitHub 工程面采集，每 30 分钟一次；公开件写 `data/`，私有件写 `private/` |
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

`collector/` 的改动在下一次 cron（≤15 分钟）生效；`web/` 与 `deploy/` 的改动需重启 `linze-status` 容器。

## 约定

- 面向用户的时间一律「北京时间 UTC+8」。
- 采集器只读：唯一写动作是产出快照文件，不碰任何被监控对象。
- 项目名单 `collect.py` 的 `PROJECTS` 是静态配置 —— **新上线一个对外服务就要往里加一条**，
  否则它不会出现在总览里，页面仍会显示"全部在线"。
