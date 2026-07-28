#!/bin/bash
# LinzeStatus 自愈引擎 —— 装到 /usr/local/bin/linze-selfheal.sh,由 root cron 每 5 分钟运行。
# 设计铁律:纯服务器 cron 自运行,**不依赖 agent、不依赖任何 token/外部接口**。
# 只做「安全、可回收、可自愈」的动作,绝不做破坏性操作:
#   R1 磁盘守护:两级。>=70% 只清 docker build cache(纯可再生,提前清);
#      >=85% 再叠加 system prune + 收敛系统日志 + 删 7 天前的本地备份副本(异地副本不动)。
#   R2 服务看门狗:host-direct 服务 HTTP 连续 2 次失败且过冷却期,自动 docker restart 对应容器。
# 状态写 data/selfheal.json,供 status 云平台总览页只读展示。动作留痕到 selfheal.log 与 recent。
set -uo pipefail
# 生产用默认值;这两个 env 只是**测试接缝** —— 测试要跑真脚本才算数,
# 若靠 sed 改一份副本再测,测的就不是线上那份代码了(那种测试是装饰品)。
# cron 不继承任意环境变量,能设这两个变量的人本来就已经是 root。
APP="${LINZE_STATUS_APP:-/srv/linze/apps/status}"
DATA="$APP/data"
SD="$DATA/.selfheal"                 # 计数/冷却/最近动作的持久态
STATE="$DATA/selfheal.json"
LOG="$APP/selfheal.log"
NOW=$(date +%s)
TS=$(TZ=Asia/Shanghai date +'%Y-%m-%d %H:%M')
DISK_TRIP=85                         # 全面清理触发线 %
CACHE_TRIP=70                        # 仅清 docker build cache 的触发线 %(纯可再生,提前清)
CACHE_KEEP=2GB                       # build cache 保留量(留一点,下次构建不至于全冷)
FAIL_TRIP=2                          # 连续失败几次才动手
COOLDOWN=1200                        # 同一容器两次重启最小间隔(秒)=20分钟
mkdir -p "$SD"

log(){ echo "$TS $*" >> "$LOG"; }
push_recent(){ # rule msg
  echo "{\"at\":\"$TS\",\"rule\":\"$1\",\"msg\":\"$2\"}" >> "$SD/recent.jsonl"
  tail -n 40 "$SD/recent.jsonl" > "$SD/recent.jsonl.tmp" && mv "$SD/recent.jsonl.tmp" "$SD/recent.jsonl"
}

# 200/301/302/307/308/401/403 都算「服务活着」(被 Access 拦下来也是活着)。
probe_code(){ curl -s -o /dev/null -w '%{http_code}' --max-time 10 "$1" 2>/dev/null || echo 000; }
probe_alive(){ echo "$1" | grep -qE '^(200|301|302|307|308|401|403)$'; }

# ★ 复探(post-probe):动完手之后必须再测一次,测通了才算「已恢复」。
#   改这段之前,看门狗是这样的:
#       docker restart "$cname" && ok=1 || ok=0
#       wd_last="…已自动重启容器 $cname"        # ok 根本没进这句话
#       echo 0 > "$SD/fail_$grep_name"          # 还没验证就把失败计数清零了
#   三个问题叠在一起,后果是同一个:**自愈会报告自己修好了,哪怕根本没修好**。
#     1) 重启完从不回头再探一次服务;
#     2) docker restart 返回 0 只说明「重启指令被接受」,不等于服务起来了;
#     3) 失败计数提前清零 —— 没修好的话,下一轮还得重新攒够 FAIL_TRIP 次才会再动手,
#        等于每失败一次就白等一个冷却周期。
#   冻结验收 OP-002 的判据就是这条:zero false RECOVERED state。
#   所以这里改成:重启 → 有界重试复探 → 通了才写「已恢复」、才清计数;
#   没通就如实写「重启后仍未恢复」,并且**保留失败计数**。
POST_PROBE_TRIES="${LINZE_SELFHEAL_PROBE_TRIES:-4}"   # 复探次数上限
POST_PROBE_GAP="${LINZE_SELFHEAL_PROBE_GAP:-5}"      # 间隔(秒);最坏 4×5=20s,远小于 cron 的 5 分钟
post_probe(){ # url -> 0=恢复 1=仍然不通;回显最后一次的 code
  local url="$1" i=1 c=000
  while [ "$i" -le "$POST_PROBE_TRIES" ]; do
    sleep "$POST_PROBE_GAP"
    c=$(probe_code "$url")
    if probe_alive "$c"; then echo "$c"; return 0; fi
    i=$(( i + 1 ))
  done
  echo "$c"; return 1
}

# ---------- R1 磁盘守护 ----------
disk_pct=$(df / | awk 'NR==2{gsub("%","",$5);print $5}')
disk_state="ok"; disk_detail="当前 ${disk_pct}% · 低于 ${CACHE_TRIP}% 触发线,无需清理"
disk_last=$(cat "$SD/disk_last" 2>/dev/null || echo "")
disk_last_at=$(cat "$SD/disk_last_at" 2>/dev/null || echo "")
disk_count=$(cat "$SD/disk_count" 2>/dev/null || echo 0)
if [ "${disk_pct:-0}" -ge "$CACHE_TRIP" ]; then
  before=$disk_pct
  # 主要涨点是 docker build cache(频繁部署产生)。它绝大部分不到 168h,
  # 所以 `system prune --filter until=168h` 够不到 —— 2026-07-26 实测磁盘 3 小时涨 16 个点、
  # 可回收 build cache 10.9GB,而按老规则触发时一点都清不掉。必须单独 builder prune。
  docker builder prune -f --keep-storage="$CACHE_KEEP" >/dev/null 2>&1 || true
  scope="build cache"
  if [ "${disk_pct:-0}" -ge "$DISK_TRIP" ]; then
    docker system prune -f --filter "until=168h" >/dev/null 2>&1 || true
    sudo -n journalctl --vacuum-size=200M >/dev/null 2>&1 || true
    find /srv/linze/backups -name 'linze-backup-*.enc' -mtime +7 -delete 2>/dev/null || true
    scope="全量可回收空间"
  fi
  after=$(df / | awk 'NR==2{gsub("%","",$5);print $5}')
  disk_last="清理${scope}:磁盘 ${before}% → ${after}%"
  disk_last_at="$TS"; disk_pct=$after
  disk_count=$(( disk_count + 1 )); disk_state="acted"
  disk_detail="已清理${scope} · 磁盘 ${before}% → ${after}%"
  echo "$disk_last" > "$SD/disk_last"; echo "$disk_last_at" > "$SD/disk_last_at"; echo "$disk_count" > "$SD/disk_count"
  log "DISK $disk_last"; push_recent disk "$disk_last"
fi

# ---------- R2 服务看门狗(仅可靠命名的 host-direct 服务)----------
# 每条:URL|容器名grep|展示名。Coolify 托管的 app 由 Coolify 健康检查 + docker 重启策略兜底(见页面「容器崩溃自动拉起」)。
MON="${LINZE_SELFHEAL_MON:-https://status.linzezhang.com|linze-status|Status
https://account.linzezhang.com|keycloak|Account
https://kmfa.linzezhang.com|skills|KMFA}"
wd_online=0; wd_total=0; wd_last=$(cat "$SD/wd_last" 2>/dev/null || echo ""); wd_last_at=$(cat "$SD/wd_last_at" 2>/dev/null || echo "")
wd_count=$(cat "$SD/wd_count" 2>/dev/null || echo 0)
while IFS='|' read -r url grep_name disp; do
  [ -z "$url" ] && continue
  wd_total=$(( wd_total + 1 ))
  cname=$(docker ps --format '{{.Names}}' | grep -i "$grep_name" | head -1)
  code=$(probe_code "$url")
  if probe_alive "$code"; then
    wd_online=$(( wd_online + 1 )); echo 0 > "$SD/fail_${grep_name}"
    continue
  fi
  fails=$(cat "$SD/fail_${grep_name}" 2>/dev/null || echo 0); fails=$(( fails + 1 )); echo "$fails" > "$SD/fail_${grep_name}"
  [ "$fails" -lt "$FAIL_TRIP" ] && { log "WATCH $disp code=$code fails=$fails(未达阈值)"; continue; }
  [ -z "$cname" ] && { log "WATCH $disp down code=$code 但未匹配到容器,跳过"; continue; }
  last_r=$(cat "$SD/restart_${grep_name}" 2>/dev/null || echo 0)
  if [ $(( NOW - last_r )) -lt "$COOLDOWN" ]; then log "WATCH $disp down 但在冷却期,不重启"; continue; fi
  # 冷却窗只要动了手就记,不管结果 —— 否则失败的重启会立刻重试,把服务按在地上反复起。
  echo "$NOW" > "$SD/restart_${grep_name}"
  wd_count=$(( wd_count + 1 ))
  if ! docker restart "$cname" >/dev/null 2>&1; then
    # 连重启指令都没被接受,不必复探,直接如实记。失败计数保留。
    wd_last="$disp 服务 HTTP 失败(code=$code),尝试重启容器 $cname 但**重启指令失败**,未恢复"
    wd_healed=0
  else
    after=$(post_probe "$url")
    if probe_alive "$after"; then
      echo 0 > "$SD/fail_${grep_name}"        # 只有复探通过才清计数
      wd_last="$disp 服务 HTTP 失败(code=$code),已重启容器 $cname,复探 HTTP $after **已恢复**"
      wd_healed=1
    else
      # ★ 关键:没修好就不许说修好了,失败计数也不清 —— 下一轮到点直接再动手。
      wd_last="$disp 服务 HTTP 失败(code=$code),已重启容器 $cname,但复探仍为 $after **未恢复**"
      wd_healed=0
    fi
  fi
  wd_last_at="$TS"; echo "$wd_last" > "$SD/wd_last"; echo "$wd_last_at" > "$SD/wd_last_at"; echo "$wd_count" > "$SD/wd_count"
  if [ "$wd_healed" -eq 1 ]; then wd_online=$(( wd_online + 1 )); wd_acted=1; else wd_failed=1; fi
  log "WATCH $wd_last healed=$wd_healed"; push_recent watchdog "$wd_last"
done <<< "$MON"
# 四态,按「这一轮到底发生了什么」判,而不是按「现在几个在线」判:
#   ok     全在线,什么都没做
#   warn   有服务不在线,但本轮没动手(没到阈值 / 在冷却期 / 没匹配到容器)
#   acted  本轮动了手,并且复探确认救回来了
#   failed 本轮动了手,复探没通过 —— 优先级最高,绝不能被上面几档盖住
# ★ 原来 acted 是由 `wd_online < wd_total` 推出来的。那个口径有个洞:
#   救回来之后在线数补齐了,状态就退回 ok,页面上看不出这一轮其实出过事、
#   自愈动过手。「修好了」和「压根没坏过」不是一回事,不能显示成同一个。
wd_state="ok"
[ "$wd_online" -lt "$wd_total" ] && wd_state="warn"
[ "${wd_acted:-0}" -eq 1 ] && wd_state="acted"
[ "${wd_failed:-0}" -eq 1 ] && wd_state="failed"
wd_detail="${wd_online}/${wd_total} host-direct 服务在线"
[ "$wd_state" = "acted" ] && wd_detail="$wd_detail · 本轮自愈动过手并已复探通过"
[ "$wd_state" = "warn" ] && wd_detail="$wd_detail · 有服务不在线,本轮未达动手条件"
[ "$wd_state" = "failed" ] && wd_detail="$wd_detail · 有服务重启后复探仍未通过"

# ---------- R3 元自愈:采集器看门狗(自愈的自愈——采集器卡死就自动重跑)----------
SNAP=/srv/linze/apps/status/data/snapshot.json
cw_state="ok"; cw_detail="采集器心跳正常"; cw_last=$(cat "$SD/cw_last" 2>/dev/null || echo "")
cw_last_at=$(cat "$SD/cw_last_at" 2>/dev/null || echo ""); cw_count=$(cat "$SD/cw_count" 2>/dev/null || echo 0)
if [ -f "$SNAP" ]; then
  snap_age=$(( NOW - $(stat -c %Y "$SNAP") ))
  if [ "$snap_age" -gt 300 ]; then         # 快照 >5 分钟没更新 = 采集器卡死
    # ★ 复探同理:采集器退出码 0 只说明进程没报错,不说明快照真的被写新了
    #   (卡在网络等待、写到一半、写到别的路径,都可能 exit 0)。
    #   唯一算数的证据是**快照 mtime 真的往前走了**。
    before_m=$(stat -c %Y "$SNAP")
    sudo -u ubuntu python3 /srv/linze/apps/status/collector/collect.py >/dev/null 2>&1 && ok=1 || ok=0
    after_m=$(stat -c %Y "$SNAP" 2>/dev/null || echo "$before_m")
    cw_count=$(( cw_count + 1 ))
    if [ "$after_m" -gt "$before_m" ]; then
      cw_state="acted"
      cw_last="快照已 $(( snap_age/60 )) 分钟未更新,已自动重跑采集器 · 复探:快照已刷新(**已恢复**)"
      cw_detail="已自动重跑采集器 · 快照已刷新"
    else
      # 没刷新就是没修好 —— 不许显示成 acted(那会被读成"处理过了,没事了")
      cw_state="failed"
      cw_last="快照已 $(( snap_age/60 )) 分钟未更新,重跑采集器(exit ok=$ok)后**快照仍未刷新,未恢复**"
      cw_detail="重跑采集器后快照仍未刷新 · 需人工介入"
    fi
    cw_last_at="$TS"
    echo "$cw_last" > "$SD/cw_last"; echo "$cw_last_at" > "$SD/cw_last_at"; echo "$cw_count" > "$SD/cw_count"
    log "COLLECTOR-WATCH $cw_last"; push_recent collector_watch "$cw_last"
  else
    cw_detail="快照 $(( snap_age/60 )) 分钟内更新过 · 采集器正常"
  fi
else
  cw_state="warn"; cw_detail="尚无快照文件"
fi

# ---------- 写状态(python3 保证 JSON 转义正确)----------
RECENT=$( [ -f "$SD/recent.jsonl" ] && tail -n 12 "$SD/recent.jsonl" | tac | paste -sd, - || echo "" )
export TS NOW disk_pct disk_state disk_detail disk_count disk_last disk_last_at DISK_TRIP CACHE_TRIP
export wd_state wd_detail wd_count wd_last wd_last_at wd_online wd_total FAIL_TRIP COOLDOWN RECENT
export cw_state cw_detail cw_count cw_last cw_last_at
python3 - "$STATE" <<'PY'
import json, os, sys
g=os.environ.get
def num(x,d=0):
    try: return int(x)
    except: return d
recent=[]
raw=g("RECENT","").strip()
if raw:
    try: recent=json.loads("["+raw+"]")
    except Exception: recent=[]
rules=[
 {"key":"disk","name":"磁盘守护","engine":"selfheal","set":"main","armed":True,
  "threshold":"磁盘 ≥%s%% 清 build cache · ≥%s%% 全量清理"%(g("CACHE_TRIP","70"),g("DISK_TRIP","85")),
  "state":g("disk_state","ok"),"detail":g("disk_detail",""),
  "actions_total":num(g("disk_count","0")),
  "last_action":g("disk_last","") or None,"last_action_at":g("disk_last_at","") or None},
 {"key":"watchdog","name":"服务看门狗","engine":"selfheal","set":"main","armed":True,
  "threshold":"HTTP 连续%s次失败且过%d分钟冷却 → 自动重启容器"%(g("FAIL_TRIP","2"),num(g("COOLDOWN","1200"))//60),
  "state":g("wd_state","ok"),"detail":g("wd_detail",""),
  "actions_total":num(g("wd_count","0")),
  "last_action":g("wd_last","") or None,"last_action_at":g("wd_last_at","") or None},
 {"key":"collector_watch","name":"采集器看门狗","engine":"selfheal","set":"meta","armed":True,
  "threshold":"快照 >5 分钟停更 → 自动重跑采集器(自愈的自愈)",
  "state":g("cw_state","ok"),"detail":g("cw_detail",""),
  "actions_total":num(g("cw_count","0")),
  "last_action":g("cw_last","") or None,"last_action_at":g("cw_last_at","") or None},
]
out={"last_run":g("TS",""),"last_run_epoch":num(g("NOW","0")),
     "engine":"服务器 cron · 不依赖 agent/token",
     "rules":rules,"recent":recent}
tmp=sys.argv[1]+".tmp"
open(tmp,"w").write(json.dumps(out,ensure_ascii=False,indent=1))
os.replace(tmp,sys.argv[1])
PY
log "RUN disk=${disk_pct}%/${disk_state} watchdog=${wd_online}/${wd_total}/${wd_state}"
