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
    check_body="$(curl -s --max-time 5 "http://${ip}/check.html")"
    gov_body="$(curl -s --max-time 5 "http://${ip}/agent-governance.html")"
    legacy_body="$(curl -s --max-time 5 "http://${ip}/legacy.html")"
    # 2026-08-11 更新这几条断言。原来它们断言「首页必须有治理入口」「/agent-governance.html
    # 必须是真页面」—— 而治理页当天已按 owner 要求整页删除。**断言在替一个不存在的功能守门**,
    # 于是每次部署都红:#83 #84 #85 三次连续 failure,而我一直在手工 scp,所以没察觉。
    # 教训:删功能时要连同「为它守门的断言」一起删,否则闸门会拿旧世界的标准判新世界。
    # KM-需-1478：新版与旧版并行。构建编号按前缀验，区块和数据入口按页面内容验。
    grep -Fq "<title>服务器运行总览</title>" <<<"$index_body" || { echo "★ 首页内容不对" >&2; fail=1; }
    build_pattern='<meta[[:space:]]+name="km-build"[[:space:]]+content="ZL00-[^"]+"'
    index_build="$(grep -Eo "$build_pattern" <<<"$index_body" || true)"
    check_build="$(grep -Eo "$build_pattern" <<<"$check_body" || true)"
    [[ -n "$index_build" ]] || { echo "★ 首页编号不对" >&2; fail=1; }
    for section in overview trend inventory; do
      grep -Fq "<section id=\"${section}\" " <<<"$index_body" || { echo "★ 首页缺少 ${section} 区块" >&2; fail=1; }
    done
    for entry in 'history#history' 'arch#arch' 'org#org' 'flows#routes'; do
      grep -Fq "href=\"/check.html?view=${entry}\"" <<<"$index_body" || { echo "★ 首页缺少 ${entry} 直达入口" >&2; fail=1; }
    done
    for data_source in arch snapshot history; do
      grep -Fq "/data/${data_source}.json" <<<"$index_body" || { echo "★ 首页缺少 ${data_source} 数据入口" >&2; fail=1; }
    done
    grep -Fq 'href="/check.html#health"' <<<"$index_body" || { echo "★ 首页缺少体检入口" >&2; fail=1; }
    grep -Fq '<title>服务器体检清单</title>' <<<"$check_body" || { echo "★ 体检页内容不对" >&2; fail=1; }
    [[ -n "$check_build" && "$check_build" == "$index_build" ]] || { echo "★ 体检页编号与首页不一致" >&2; fail=1; }
    for section in health compare; do
      grep -Fq "<section id=\"${section}\" " <<<"$check_body" || { echo "★ 体检页缺少 ${section} 区块" >&2; fail=1; }
    done
    for table in attention-table inventory-table compare-table; do
      grep -Fq "id=\"${table}\"" <<<"$check_body" || { echo "★ 体检页缺少 ${table} 表格" >&2; fail=1; }
    done
    grep -Fq '<svg id="g" ' <<<"$check_body" || { echo "★ 体检页缺少架构图模板" >&2; fail=1; }
    # 返回和首页只在子页要（Owner 11-23：在首页还挂「返回」不对）；首页反过来不许有返回。
    grep -Eq 'href="/"[^>]*>首页</a>' <<<"$check_body" || { echo "★ 页面缺少回首页入口" >&2; fail=1; }
    grep -Fq 'id="go-back"' <<<"$check_body" || { echo "★ 页面缺少后退入口" >&2; fail=1; }
    ! grep -Fq 'id="go-back"' <<<"$index_body" || { echo "★ 首页不该有返回" >&2; fail=1; }
    grep -q "云平台总览" <<<"$legacy_body"     || { echo "★ 旧版明细页内容不对" >&2; fail=1; }
    # 项目资源列 —— owner 明确要过的功能(每个项目吃多少内存/存储),用它当"新代码真上线了"的锚
    grep -q ">内存</th>" <<<"$legacy_body"     || { echo "★ 旧版明细页缺项目内存列" >&2; fail=1; }
    grep -q "rtResFoot" <<<"$legacy_body"      || { echo "★ 旧版明细页缺资源页脚(分母/未归属)" >&2; fail=1; }
    # 缓存戳必须在:没有它,改了资产用户最长 4 小时拿不到(CF 覆盖源站 no-cache)
    grep -qE '(src|href)="[^"]*\.(js|css)\?v=[0-9a-f]{8}"' <<<"$legacy_body" \
                                               || { echo "★ 资产缺内容哈希缓存戳" >&2; fail=1; }
    # 反向断言:治理页已删,它现在**必须**是 index 兜底。
    # 原来这条是"必须不同",现在是"必须相同" —— 方向反了,但守的是同一件事:
    # 线上到底是不是我们以为的那份代码。
    [[ "$index_body" == "$gov_body" ]]         || { echo "★ /agent-governance.html 不是 index 兜底 —— 治理页没删干净" >&2; fail=1; }
    (( fail == 0 )) || { echo "部署自检未通过" >&2; exit 76; }
    echo "finalize_ok content_verified"
    ;;
  *)
    log "REFUSED: ${CMD:0:120}"
    echo "此密钥只能用于 status/ 的 rsync 部署与 deploy-finalize,拒绝执行:${CMD:0:80}" >&2
    exit 77
    ;;
esac
