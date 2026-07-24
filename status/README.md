# status —— LinzeHomeHub 子项目

> **本目录是 `LinzeColin/LinzeHomeHub` 仓的子项目,不单独建仓。** 改这里的代码走 LinzeHomeHub 的分支/PR 流程。

`status.linzezhang.com` 的**只读云平台总览**:各项目部署状态、部署成功率与记录、主机内存/磁盘趋势、
备份/证书/续费/容器重启运维指标、月度开支(默认 AUD,按实时汇率折 CNY)、外部服务状态。
配套 `uptime.linzezhang.com`(Gatus 运维健康)与 `/admin` 价格编辑器(Cloudflare Access 仅 owner)。

## 架构
- **采集器** `collector/collect.py`:在 OVH VPS-1 主机上由 cron 每 15 分钟运行,读取 Coolify 数据库、
  主机指标、证书、备份、实时汇率、价格库,产出 `data/snapshot.json`(原子写),并滚动累积 `data/history.json`(内存/磁盘趋势)。只读采集。
- **静态页** `web/index.html`:纯前端,自带 Chart.js(`web/vendor/`,不依赖外网 CDN,便于中国大陆访问),
  拉 `data/snapshot.json` 渲染。深浅色自适应。
- **编排** `deploy/docker-compose.yml` + `deploy/nginx.conf`:nginx:alpine 只读挂载 `web/` 与 `data/`,
  进 `coolify` 网络,Traefik 标签占 `status.linzezhang.com`。

## 部署位置(OVH VPS-1)
```
/srv/linze/apps/status/
├── web/            # 页面 + vendor/chart.umd.min.js  (repo 同步)
├── deploy/         # docker-compose.yml + nginx.conf  (repo 同步)
├── collector/      # collect.py                        (repo 同步)
└── data/           # prices.json(种子来自 repo)、snapshot.json / history.json(运行时产出,不入库)
```
Gatus 存活探测已挪到 `uptime.linzezhang.com`,页脚有链接。

## 月度开支
金额存 `data/prices.json`(原币种原周期),采集器按实时汇率折算。改价格 = 改此文件(带写入的
Access 门禁编辑器为后续项)。汇率取自 open.er-api.com,拉不到则沿用上次值,绝不编造。

## cron
```
*/15 * * * * ubuntu  cd /srv/linze/apps/status && /usr/bin/python3 collector/collect.py >> /var/log/linze-status.log 2>&1
```

Owner:linzezhang35@gmail.com · 面向用户时间统一北京时间(UTC+8)。
