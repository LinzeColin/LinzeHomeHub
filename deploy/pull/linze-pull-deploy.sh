#!/usr/bin/env bash
# linze-pull-deploy.sh —— 拉取式部署（GitOps pull）：免令牌、免 Coolify API。
#
# 每次被 systemd timer 唤醒：
#   1. git ls-remote 看公开仓 BRANCH 的最新提交（一次极小的 HTTPS 请求）；
#   2. 与线上容器标签 linze.pull.commit 比，一样就退出；
#   3. 不一样：浅取该提交 -> docker build -> 起新容器（先放在隔离网络里，Traefik 看不见它）-> 健康检查通过
#      + 直连探活（状态码与内容都对）-> 才接入 Traefik 所在网络 -> 停旧容器 -> 经 Traefik 回测
#      （同域名、走真 TLS、内容与新容器逐字节一致）；
#   4. 任何一步失败：删掉新容器、把已停的旧容器拉起来，线上版本不变；同一提交按退避重试
#      （5 分钟起翻倍，封顶 6 小时），有新提交立刻重来。
#
# 不做：不碰任何令牌（仓库 URL 里带凭据会被拒绝）；不自我更新 —— 脚本和 unit 只由 install.sh
# 安装，避免「合入 main = 拿到宿主机 root」。仓库里的版本与已安装版本不一致时只在状态里标 script_drift。
#
# 用法：
#   linze-pull-deploy.sh run <app> [--branch <分支>]   # timer 调用；--branch 仅用于故障演练
#   linze-pull-deploy.sh status <app>                  # 看它活没活着
# 配置：/etc/linze-pull-deploy/<app>.env（模板见 deploy/pull/home-hub.env，参数逐项有注释）。
set -uo pipefail
export GIT_TERMINAL_PROMPT=0 LC_ALL=C

CONF_DIR=${LINZE_PULL_CONF_DIR:-/etc/linze-pull-deploy}
STATE_ROOT=${LINZE_PULL_STATE_ROOT:-/var/lib/linze-pull-deploy}
LOCK_DIR=${LINZE_PULL_LOCK_DIR:-/run/lock}
SELF=$(readlink -f "$0")

usage() { echo "用法: $0 run <app> [--branch <分支>] | status <app>" >&2; exit 64; }
CMD=${1:-}; APP=${2:-}
[ -n "$CMD" ] && [ -n "$APP" ] || usage
shift 2
BRANCH_OVERRIDE=""
while [ $# -gt 0 ]; do
  case "$1" in
    --branch) BRANCH_OVERRIDE=${2:-}; [ -n "$BRANCH_OVERRIDE" ] || usage; shift 2 ;;
    *) usage ;;
  esac
done
[[ $APP =~ ^[a-z0-9][a-z0-9-]*$ ]] || { echo "app 名只允许小写字母、数字和 -" >&2; exit 64; }

CONF="$CONF_DIR/$APP.env"
[ -r "$CONF" ] || { echo "缺配置文件 $CONF" >&2; exit 78; }
set -a; . "$CONF"; set +a
: "${REPO_URL:?配置缺 REPO_URL}" "${DOMAIN:?配置缺 DOMAIN}" "${IMAGE:?配置缺 IMAGE}"
BRANCH=${BRANCH_OVERRIDE:-${BRANCH:-main}}
SUBDIR=${SUBDIR:-.}
DOCKERFILE=${DOCKERFILE:-Dockerfile}
CONTAINER_PORT=${CONTAINER_PORT:-80}
HEALTH_PATH=${HEALTH_PATH:-/}
HEALTH_CODE=${HEALTH_CODE:-200}
HEALTH_BODY_REGEX=${HEALTH_BODY_REGEX:-}
HEALTH_TIMEOUT=${HEALTH_TIMEOUT:-90}
VERSION_PATH=${VERSION_PATH:-}
DOCKER_NETWORK=${DOCKER_NETWORK:-coolify}
STAGING_NETWORK=${STAGING_NETWORK:-bridge}
CERT_RESOLVER=${CERT_RESOLVER:-letsencrypt}
MEMORY=${MEMORY:-256m}
BUILD_TIMEOUT=${BUILD_TIMEOUT:-900}
RETIRE_FILTER=${RETIRE_FILTER:-}
POSTCHECK_INSECURE=${POSTCHECK_INSECURE:-0}
KEEP_PREV=${KEEP_PREV:-1}
SELF_DIR_IN_REPO=${SELF_DIR_IN_REPO:-deploy/pull}

[[ $REPO_URL =~ ^https://[^@[:space:]]+$ ]] || { echo "REPO_URL 必须是不带凭据的 https 公开地址" >&2; exit 78; }
[[ $DOMAIN =~ ^[a-z0-9.-]+$ ]] || { echo "DOMAIN 格式不对" >&2; exit 78; }
[[ $IMAGE =~ ^[a-z0-9][a-z0-9._/-]*$ ]] || { echo "IMAGE 格式不对" >&2; exit 78; }
[[ $SUBDIR =~ ^[A-Za-z0-9._/-]+$ && $SUBDIR != /* && $SUBDIR != *..* ]] || { echo "SUBDIR 格式不对" >&2; exit 78; }

STATE_DIR="$STATE_ROOT/$APP"
STATE_FILE="$STATE_DIR/state.env"
JSON_FILE="$STATE_DIR/status.json"
WORK="$STATE_DIR/repo"
STATE_KEYS=(S_state S_branch S_target_sha S_deployed_sha S_container S_last_check_at S_last_deploy_at \
            S_last_error S_fail_sha S_fail_count S_next_retry_epoch S_script_drift)
for _k in "${STATE_KEYS[@]}"; do printf -v "$_k" '%s' ""; done

log() { printf '%s [%s] %s\n' "$(date -u +%FT%TZ)" "$APP" "$*"; }
jesc() { local s=${1//\\/\\\\}; s=${s//\"/\\\"}; s=${s//$'\n'/ }; s=${s//$'\t'/ }; printf '%s' "$s"; }

load_state() { [ -f "$STATE_FILE" ] && . "$STATE_FILE"; return 0; }
save_state() {
  local k first=1
  : > "$STATE_FILE.tmp"
  for k in "${STATE_KEYS[@]}"; do printf '%s=%q\n' "$k" "${!k-}" >> "$STATE_FILE.tmp"; done
  mv -f "$STATE_FILE.tmp" "$STATE_FILE"
  {
    printf '{'
    for k in "${STATE_KEYS[@]}"; do
      [ "$first" = 1 ] || printf ','
      first=0
      printf '"%s":"%s"' "${k#S_}" "$(jesc "${!k-}")"
    done
    printf ',"app":"%s","repo":"%s","domain":"%s"}\n' "$APP" "$(jesc "$REPO_URL")" "$DOMAIN"
  } > "$JSON_FILE.tmp"
  mv -f "$JSON_FILE.tmp" "$JSON_FILE"
}

# ---------- 小工具 ----------
live_container() { docker ps -q --filter "label=linze.pull.app=$APP" --filter status=running | head -n1; }
label_of() { docker inspect -f "{{index .Config.Labels \"$2\"}}" "$1" 2>/dev/null; }
name_of() { docker inspect -f '{{.Name}}' "$1" 2>/dev/null | sed 's|^/||'; }
health_of() { docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$1" 2>/dev/null; }
sha256_of() { sha256sum < "$1" | cut -d' ' -f1; }
http_probe() { # url outfile [curl 额外参数...] -> 回显 HTTP 状态码（连不上回显 000）
  local url=$1 out=$2 c; shift 2
  c=$(curl -s --max-time 10 -o "$out" -w '%{http_code}' "$@" "$url" 2>/dev/null)
  echo "${c:-000}"
}
probe_ok() { # code bodyfile
  [ "$1" = "$HEALTH_CODE" ] || return 1
  [ -z "$HEALTH_BODY_REGEX" ] || grep -Eq -- "$HEALTH_BODY_REGEX" "$2"
}

# ---------- status ----------
do_status() {
  echo "== 状态文件 $JSON_FILE"
  if [ -f "$JSON_FILE" ]; then cat "$JSON_FILE"; else echo "(尚无状态：还没跑过)"; fi
  echo "== 容器（label linze.pull.app=$APP）"
  docker ps -a --filter "label=linze.pull.app=$APP" --format '{{.Names}}  {{.Status}}  commit={{.Label "linze.pull.commit"}}'
  echo "== 定时器"
  systemctl list-timers "linze-pull-deploy@$APP.timer" --no-pager 2>/dev/null | sed -n '1,3p'
  if [ -n "$VERSION_PATH" ]; then
    echo "== 公网版本 https://$DOMAIN$VERSION_PATH"
    curl -fsS --max-time 10 "https://$DOMAIN$VERSION_PATH?t=$(date +%s)" 2>&1 || echo "(取不到)"
  fi
}

# ---------- run ----------
CAND=""; RETIRED=(); LIVE_SHA=""; TARGET=""

fail() { # 回滚：删新容器、拉起已停的旧容器、记退避，然后 exit 1
  local msg=$1 c n wait
  log "失败：$msg"
  [ -n "$CAND" ] && docker logs --tail 20 "$CAND" 2>&1 | sed 's/^/  candidate| /'
  # 先把旧容器拉起来，再删新容器，缩短空窗
  for c in ${RETIRED[@]+"${RETIRED[@]}"}; do
    if docker start "$c" >/dev/null 2>&1; then log "已拉起旧容器 $(name_of "$c")"; else log "警告：旧容器 $c 拉不起来"; fi
  done
  [ -n "$CAND" ] && docker rm -f "$CAND" >/dev/null 2>&1
  n=1; [ "$S_fail_sha" = "$TARGET" ] && n=$(( ${S_fail_count:-0} + 1 ))
  wait=$(( 300 * (1 << (n > 7 ? 6 : n - 1)) )); [ "$wait" -gt 21600 ] && wait=21600
  S_fail_sha=$TARGET; S_fail_count=$n; S_next_retry_epoch=$(( $(date +%s) + wait ))
  S_last_error=$msg; S_state=failing
  save_state
  log "线上保持 ${LIVE_SHA:0:12}；同一提交最早 ${wait}s 后重试（第 $n 次失败）"
  exit 1
}

fetch_source() {
  local sha=$1
  if [ ! -d "$WORK/.git" ]; then
    rm -rf "$WORK"; git init -q "$WORK" || return 1
  fi
  git -C "$WORK" remote remove origin >/dev/null 2>&1
  git -C "$WORK" remote add origin "$REPO_URL" || return 1
  timeout 180 git -C "$WORK" -c credential.helper= fetch -q --depth 1 origin "$sha" || return 1
  if [ "$SUBDIR" != "." ]; then git -C "$WORK" sparse-checkout set --cone "$SUBDIR" || return 1; fi
  git -C "$WORK" checkout -q -f --detach FETCH_HEAD || return 1
  git -C "$WORK" clean -ffdxq
  [ "$(git -C "$WORK" rev-parse HEAD)" = "$sha" ] || return 1
  [ -f "$WORK/$SUBDIR/$DOCKERFILE" ] || { log "仓库里找不到 $SUBDIR/$DOCKERFILE"; return 1; }
}

build_image() {
  local sha=$1 ctx="$WORK/$SUBDIR" logf="$STATE_DIR/last-build.log"
  if docker image inspect "$IMAGE:$sha" >/dev/null 2>&1; then log "镜像 $IMAGE:${sha:0:12} 已存在，跳过构建"; return 0; fi
  log "构建 $IMAGE:${sha:0:12}"
  timeout "$BUILD_TIMEOUT" docker build --progress=plain \
    --label "linze.pull.commit=$sha" --label "linze.pull.app=$APP" \
    --build-arg "SOURCE_COMMIT=$sha" \
    -t "$IMAGE:$sha" -f "$ctx/$DOCKERFILE" "$ctx" >"$logf" 2>&1
  local rc=$?
  if [ "$rc" -ne 0 ]; then tail -n 40 "$logf" | sed 's/^/  build| /'; return 1; fi
  tail -n 2 "$logf" | sed 's/^/  build| /'
}

start_candidate() {
  local sha=$1 name="$APP-${sha:0:12}" l
  local hp="http://127.0.0.1:${CONTAINER_PORT}${HEALTH_PATH}"
  local -a labels=(
    "linze.pull.app=$APP" "linze.pull.commit=$sha"
    "traefik.enable=true" "traefik.docker.network=$DOCKER_NETWORK"
    "traefik.http.services.$APP.loadbalancer.server.port=$CONTAINER_PORT"
    "traefik.http.middlewares.$APP-gzip.compress=true"
    "traefik.http.middlewares.$APP-retry.retry.attempts=3"
    "traefik.http.middlewares.$APP-https-redirect.redirectscheme.scheme=https"
    "traefik.http.routers.$APP-http.entrypoints=http"
    "traefik.http.routers.$APP-http.rule=Host(\`$DOMAIN\`)"
    "traefik.http.routers.$APP-http.middlewares=$APP-https-redirect"
    "traefik.http.routers.$APP-http.service=$APP"
    "traefik.http.routers.$APP-https.entrypoints=https"
    "traefik.http.routers.$APP-https.rule=Host(\`$DOMAIN\`)"
    "traefik.http.routers.$APP-https.tls=true"
    "traefik.http.routers.$APP-https.middlewares=$APP-retry,$APP-gzip"
    "traefik.http.routers.$APP-https.service=$APP"
  )
  [ -n "$CERT_RESOLVER" ] && labels+=("traefik.http.routers.$APP-https.tls.certresolver=$CERT_RESOLVER")
  local -a largs=(); for l in "${labels[@]}"; do largs+=(--label "$l"); done
  docker rm -f "$name" >/dev/null 2>&1
  # 先记名字：即使 run 半途失败，fail() 也会把它清掉
  CAND=$name
  # 先放进隔离网络（Traefik 不在这个网络上，看不见它）：直连探活通过后才 network connect 接入 $DOCKER_NETWORK，
  # 这样内容不对的新版本不会有一秒钟接到线上流量。切换成功前不设自动重启：崩溃的候选容器直接暴露为 exited。
  docker run -d --name "$name" --network "$STAGING_NETWORK" --restart no \
    --memory "$MEMORY" --pids-limit 256 --security-opt no-new-privileges \
    --log-opt max-size=10m --log-opt max-file=3 \
    --health-cmd "wget -q -O /dev/null '$hp' || curl -fsS -o /dev/null '$hp'" \
    --health-interval 5s --health-timeout 3s --health-retries 3 --health-start-period 3s \
    "${largs[@]}" "$IMAGE:$sha" >/dev/null
}

WH_STATE=""
wait_healthy() { # 0=健康；1=没起来/已退出/超时（最后状态在 WH_STATE）
  local c=$1 t=0
  while [ "$t" -lt "$HEALTH_TIMEOUT" ]; do
    WH_STATE=$(docker inspect -f '{{.State.Status}} {{if .State.Health}}{{.State.Health.Status}}{{end}}' "$c" 2>/dev/null)
    case "$WH_STATE" in
      "running healthy") return 0 ;;
      exited*|dead*|restarting*|"") return 1 ;;
    esac
    sleep 2; t=$(( t + 2 ))
  done
  return 1
}

postcheck() { # 切流量之后：同域名、走真 TLS 回测；内容必须与新容器直连结果逐字节一致；配了 VERSION_PATH 时还要报出新提交号
  local want=$1 want_sha=$2 i=0 code vcode ok=0 tmp="$STATE_DIR/post.body" vtmp="$STATE_DIR/post.version"
  local -a ins=(); [ "$POSTCHECK_INSECURE" = 1 ] && ins=(-k)
  while [ "$i" -lt 20 ]; do
    code=$(http_probe "https://$DOMAIN$HEALTH_PATH" "$tmp" ${ins[@]+"${ins[@]}"} --resolve "$DOMAIN:443:127.0.0.1")
    if [ "$code" = "$HEALTH_CODE" ] && [ "$(sha256_of "$tmp")" = "$want" ]; then
      ok=1
      if [ -n "$VERSION_PATH" ]; then
        ok=0
        vcode=$(http_probe "https://$DOMAIN$VERSION_PATH" "$vtmp" ${ins[@]+"${ins[@]}"} --resolve "$DOMAIN:443:127.0.0.1")
        [ "$vcode" = 200 ] && [ "$(tr -d '[:space:]' < "$vtmp")" = "$want_sha" ] && ok=1
      fi
    fi
    [ "$ok" = 1 ] && break
    sleep 3; i=$(( i + 1 ))
  done
  [ "$ok" = 1 ] || return 1
  code=$(http_probe "http://$DOMAIN$HEALTH_PATH" /dev/null --resolve "$DOMAIN:80:127.0.0.1")
  case "$code" in 301|302|307|308) return 0 ;; *) log "80 端口未重定向到 https（得到 $code）"; return 1 ;; esac
}

check_drift() { # 只报告，不自更新
  local d="" f
  [ -d "$WORK/$SELF_DIR_IN_REPO" ] || { S_script_drift="仓库里无 $SELF_DIR_IN_REPO，未比对"; return 0; }
  cmp -s "$SELF" "$WORK/$SELF_DIR_IN_REPO/linze-pull-deploy.sh" || d="$d linze-pull-deploy.sh"
  for f in linze-pull-deploy@.service linze-pull-deploy@.timer; do
    cmp -s "/etc/systemd/system/$f" "$WORK/$SELF_DIR_IN_REPO/$f" || d="$d $f"
  done
  cmp -s "$CONF" "$WORK/$SELF_DIR_IN_REPO/$APP.env" || d="$d $APP.env"
  if [ -z "$d" ]; then S_script_drift="ok"; else S_script_drift="与仓库不一致:$d（在服务器跑 install.sh 同步）"; log "$S_script_drift"; fi
}

prune_old() { # 尽力清理：只保留 KEEP_PREV 个已停的旧版本容器；不碰不带本脚本标签的容器（如旧 Coolify 容器）
  local sha=$1 c n=0 t
  for c in $(docker ps -aq --filter "label=linze.pull.app=$APP" --filter status=exited --filter status=created --filter status=dead); do
    n=$(( n + 1 )); [ "$n" -le "$KEEP_PREV" ] && continue
    docker rm "$c" >/dev/null 2>&1 && log "已删除更旧的容器 $c"
  done
  for t in $(docker images --format '{{.Tag}}' "$IMAGE"); do
    [ "$t" = "$sha" ] || docker image rm "$IMAGE:$t" >/dev/null 2>&1
  done
  return 0
}

do_run() {
  mkdir -p "$STATE_DIR" || exit 73
  exec 9>"$LOCK_DIR/linze-pull-deploy-$APP.lock"
  flock -n 9 || { log "上一轮还在跑，本轮跳过"; exit 0; }
  load_state
  local now live sha direct_ip direct_tmp="$STATE_DIR/direct.body" code c
  now=$(date +%s)
  S_last_check_at=$(date -u +%FT%TZ); S_branch=$BRANCH

  sha=$(timeout 30 git -c credential.helper= ls-remote "$REPO_URL" "refs/heads/$BRANCH" 2>/dev/null | awk 'NR==1{print $1}')
  if ! [[ $sha =~ ^[0-9a-f]{40}$ ]]; then
    S_last_error="ls-remote 失败（网络或仓不可达），线上不动"; log "$S_last_error"; save_state; exit 0
  fi
  TARGET=$sha; S_target_sha=$sha

  live=$(live_container)
  [ -n "$live" ] && LIVE_SHA=$(label_of "$live" linze.pull.commit)
  if [ -n "$live" ] && [ "$LIVE_SHA" = "$sha" ] && [ "$(health_of "$live")" = healthy ]; then
    S_state=up-to-date; S_deployed_sha=$sha; S_container=$(name_of "$live"); S_last_error=""
    log "已是最新 ${sha:0:12}"; save_state; exit 0
  fi
  if [ "$S_fail_sha" = "$sha" ] && [ "${S_next_retry_epoch:-0}" -gt "$now" ]; then
    S_state=failing; log "提交 ${sha:0:12} 上次部署失败（$S_last_error），退避到 $(date -u -d "@$S_next_retry_epoch" +%T)Z 前不重试；线上仍是 ${LIVE_SHA:0:12}"
    save_state; exit 1
  fi
  [ -n "$live" ] && [ "$LIVE_SHA" = "$sha" ] && log "线上容器不健康（$(health_of "$live")），重新部署同一提交"

  S_state=deploying; save_state
  log "开始部署 ${sha:0:12}（线上 ${LIVE_SHA:-无}）分支 $BRANCH"
  fetch_source "$sha" || fail "取源码失败"
  check_drift
  build_image "$sha" || fail "docker build 失败（日志尾部见上）"
  start_candidate "$sha" || fail "新容器启动失败"
  wait_healthy "$CAND" || fail "新容器没变健康（最后状态：${WH_STATE:-未知}，最多等 ${HEALTH_TIMEOUT}s）"
  direct_ip=$(docker inspect -f "{{with index .NetworkSettings.Networks \"$STAGING_NETWORK\"}}{{.IPAddress}}{{end}}" "$CAND")
  [ -n "$direct_ip" ] || fail "拿不到新容器在隔离网络 $STAGING_NETWORK 上的 IP"
  code=$(http_probe "http://$direct_ip:$CONTAINER_PORT$HEALTH_PATH" "$direct_tmp")
  probe_ok "$code" "$direct_tmp" || fail "新容器直连探活不合格（状态 $code，期望 $HEALTH_CODE${HEALTH_BODY_REGEX:+，内容需匹配 $HEALTH_BODY_REGEX}）"
  log "新容器 $CAND 健康、直连探活通过，接入 $DOCKER_NETWORK 并切换流量"
  docker network connect "$DOCKER_NETWORK" "$CAND" || fail "新容器接入网络 $DOCKER_NETWORK 失败"
  # update 会产生一个容器事件，Traefik 的 docker provider 据此重读配置、把新容器纳入；同时设上自动重启
  docker update --restart unless-stopped "$CAND" >/dev/null 2>&1 || log "警告：没能给 $CAND 设 restart=unless-stopped"
  # 修复（首次部署）：实测 Traefik 的 docker provider 只在容器 start / die / health_status 事件时重读配置，network connect 与 update 都不触发。
  # 上面靠「下面停旧容器（die 事件）」顺带触发；首次部署没有旧容器可停时，Traefik 会一直保留「新容器只挂在隔离网络上」的旧视图，
  # 路由指向不可达地址（症状：TLS 通、请求挂起）。这里起停一个一次性容器（start + die）主动触发一次重读；对已有旧容器的部署无害。
  # 要求镜像里有 true（nginx/alpine/debian 系都有）；没有只会打一条警告，行为与修复前一致。
  docker run --rm --network none --entrypoint true "$IMAGE:$sha" >/dev/null 2>&1 || log "警告：没能触发 Traefik 重读配置"
  sleep 3

  # 停旧：本脚本标签的旧容器 + RETIRE_FILTER 指定的遗留容器（如旧 Coolify 容器，只停不删）
  for c in $(docker ps -q --filter "label=linze.pull.app=$APP" --filter status=running; \
             [ -n "$RETIRE_FILTER" ] && docker ps -q --filter "$RETIRE_FILTER" --filter status=running); do
    [ "$(name_of "$c")" = "$CAND" ] && continue
    RETIRED+=("$c")
  done
  for c in ${RETIRED[@]+"${RETIRED[@]}"}; do
    log "停旧容器 $(name_of "$c")"
    docker stop -t 15 "$c" >/dev/null 2>&1
  done
  postcheck "$(sha256_of "$direct_tmp")" "$sha" || fail "切换后经 Traefik 回测不合格（域名 $DOMAIN）"

  S_state=up-to-date; S_deployed_sha=$sha; S_container=$CAND; S_last_deploy_at=$(date -u +%FT%TZ)
  S_last_error=""; S_fail_sha=""; S_fail_count=0; S_next_retry_epoch=""
  save_state
  log "部署完成 ${sha:0:12}（线上此前 ${LIVE_SHA:0:12}）"
  prune_old "$sha"
  exit 0
}

case "$CMD" in
  run) do_run ;;
  status) do_status ;;
  *) usage ;;
esac
