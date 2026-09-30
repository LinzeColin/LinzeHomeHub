# deploy/pull —— 拉取式部署（免令牌）

服务器自己每 5 分钟去公开仓看一眼分支，有新提交就构建、起新容器、健康检查通过再切流量并停旧的；失败保留旧的。
不需要 GitHub 往服务器推、不需要 Coolify API 令牌、不需要任何密钥（仓库是公开的，`git ls-remote` / `git fetch` 匿名即可）。

## 一次装好（服务器上，root）

```bash
git clone --depth 1 https://github.com/LinzeColin/LinzeHomeHub.git /tmp/lhh && sudo bash /tmp/lhh/deploy/pull/install.sh home-hub
```

装的东西：`/usr/local/bin/linze-pull-deploy.sh`、`/etc/systemd/system/linze-pull-deploy@.{service,timer}`、`/etc/linze-pull-deploy/home-hub.env`，并启用 `linze-pull-deploy@home-hub.timer`。
`install.sh home-hub --check` 比对服务器与仓库是否一致（不一致退出码 1）。脚本**不会自我更新**（避免「合入 main = 宿主机 root」）；改了本目录后，在服务器上重跑 `install.sh` 即可，状态里的 `script_drift` 字段会提醒。

## 怎么看它活着

| 想知道 | 命令 |
|---|---|
| 线上是哪一版 | `curl https://<域名>/version.txt`（Dockerfile 用 `SOURCE_COMMIT` 写入） |
| 部署器状态 | `sudo linze-pull-deploy.sh status <app>`，或读 `/var/lib/linze-pull-deploy/<app>/status.json`（`state` = up-to-date / deploying / failing） |
| 日志 | `journalctl -u linze-pull-deploy@<app>.service` |
| 下次什么时候跑 | `systemctl list-timers 'linze-pull-deploy@*'` |

## 失败时的行为

新容器先放进隔离网络（Traefik 看不见），健康检查和直连探活（状态码 + 页面内容）都过了才接入线上网络、再停旧的 —— 内容不对的版本不会接到一个访客。
构建失败、新容器起不来/不健康、探活不合格、切换后经 Traefik 回测不合格（内容、提交号、http 跳转 https）—— 都会删掉新容器、把已停的旧容器拉起来，线上版本不变；unit 记为 failed。同一提交按 5 分钟、10 分钟…封顶 6 小时退避重试，有新提交立刻重来。
上一个版本的容器保留（已停）作回滚；再往前的自动清理。

## 套用到别的项目

复制 `home-hub.env` 为 `<app>.env`，改 `REPO_URL` / `SUBDIR` / `IMAGE` / `DOMAIN` / `HEALTH_PATH` / `HEALTH_BODY_REGEX`（每项都有注释），去掉 `RETIRE_FILTER`（那是本次从 Coolify 迁移用的），再 `install.sh <app>`。要求：项目有 Dockerfile，容器里有 `wget` 或 `curl` 供健康检查，域名由 coolify-proxy（Traefik）代理。
