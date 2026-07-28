#!/usr/bin/env bash
# CI 部署密钥的强制命令(authorized_keys 里的 command="...")。
#
# 背景:OVH 上 ubuntu 有 NOPASSWD ALL。把一把普通密钥交给 GitHub Actions,
# 等于把 root 交出去 —— 一旦 Actions 被投毒或 secret 泄露,整台机器就没了。
# 所以这把 CI 密钥只能通过本闸门,且只允许两件事:
#
#   1. rsync 写入,且只能写 /srv/linze/apps/status(由 rrsync -wo 强制,连读出去都不行)
#   2. deploy-finalize —— 重启 linze-status 并**真的验一次**,验不过返回非零让 CI 红
#
# 其余一律拒绝:拿不到 shell、不能 sudo、碰不到该目录以外的任何东西。
# 配合 authorized_keys 里的 restrict(禁端口转发/agent 转发/pty/X11)一起生效。
# 越权已实测:whoami / sudo -n id / cat .secrets / docker rm -f / rsync 到 /tmp 全部被拒。
set -Eeuo pipefail
DEPLOY_ROOT=/srv/linze/apps/status
CMD="${SSH_ORIGINAL_COMMAND:-}"

log() { logger -t status-ci-deploy -- "$*" 2>/dev/null || true; }

case "$CMD" in
  rsync\ --server\ *)
    # rrsync 把 rsync 钉死在 DEPLOY_ROOT 之内;-wo = 只允许写入(不允许从主机读出去)
    log "rsync accepted"
    exec /usr/bin/rrsync -wo "$DEPLOY_ROOT"
    ;;
  deploy-finalize)
    log "finalize accepted"
    # ubuntu 在 docker 组里,重启容器不需要 sudo
    docker restart linze-status >/dev/null
    # 等容器真正能应答,而不是 sleep 一个拍脑袋的秒数
    ip=""
    for _ in $(seq 1 30); do
      sleep 1
      candidate="$(docker inspect linze-status --format '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' 2>/dev/null || true)"
      if [[ -n "$candidate" ]] && curl -sf -o /dev/null --max-time 3 "http://${candidate}/"; then
        ip="$candidate"; break
      fi
    done
    [[ -n "$ip" ]] || { echo "★ 重启后容器 30 秒内没有应答" >&2; exit 75; }
    echo "container_ip=${ip}"

    # ★ 直连容器验,不打主机前置代理 —— 打代理只会拿到 302,等于没验。
    #   而且必须验**内容**:nginx 的 try_files 让任何路径都返回 200,
    #   只看状态码的话 /agent-governance.html 永远是绿的。
    fail=0
    index_body="$(curl -s --max-time 5 "http://${ip}/")"
    gov_body="$(curl -s --max-time 5 "http://${ip}/agent-governance.html")"
    grep -q "云平台总览" <<<"$index_body"      || { echo "★ 首页内容不对" >&2; fail=1; }
    grep -q "id:'governance'" <<<"$index_body" || { echo "★ 首页缺治理入口" >&2; fail=1; }
    grep -q "Agent 开发治理" <<<"$gov_body"    || { echo "★ /agent-governance.html 返回的是 index 兜底,不是真页面" >&2; fail=1; }
    [[ "$index_body" != "$gov_body" ]]         || { echo "★ 治理页与首页完全相同 —— 说明没部署上" >&2; fail=1; }
    (( fail == 0 )) || { echo "部署自检未通过" >&2; exit 76; }
    echo "finalize_ok content_verified"
    ;;
  *)
    log "REFUSED: ${CMD:0:120}"
    echo "此密钥只能用于 status/ 的 rsync 部署与 deploy-finalize,拒绝执行:${CMD:0:80}" >&2
    exit 77
    ;;
esac
