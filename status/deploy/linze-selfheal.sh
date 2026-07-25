#!/bin/bash
# LinzeStatus 自愈引擎 —— 装到 /usr/local/bin/linze-selfheal.sh,由 root cron 每 5 分钟运行。
# 设计铁律:纯服务器 cron 自运行,**不依赖 agent、不依赖任何 token/外部接口**。
# 只做「安全、可回收、可自愈」的动作,绝不做破坏性操作:
#   R1 磁盘守护:磁盘 >=85% 时清理 docker 可回收缓存 + 收敛系统日志 + 删 7 天前的本地备份副本(异地副本不动)。
#   R2 服务看门狗:host-direct 服务 HTTP 连续 2 次失败且过冷却期,自动 docker restart 对应容器。
# 状态写 data/selfheal.json,供 status 云平台总览页只读展示。动作留痕到 selfheal.log 与 recent。
set -uo pipefail
APP=/srv/linze/apps/status
DATA="$APP/data"
SD="$DATA/.selfheal"                 # 计数/冷却/最近动作的持久态
STATE="$DATA/selfheal.json"
LOG="$APP/selfheal.log"
NOW=$(date +%s)
TS=$(TZ=Asia/Shanghai date +'%Y-%m-%d %H:%M')
DISK_TRIP=85                         # 磁盘触发线 %
FAIL_TRIP=2                          # 连续失败几次才动手
COOLDOWN=1200                        # 同一容器两次重启最小间隔(秒)=20分钟
mkdir -p "$SD"

log(){ echo "$TS $*" >> "$LOG"; }
push_recent(){ # rule msg
  echo "{\"at\":\"$TS\",\"rule\":\"$1\",\"msg\":\"$2\"}" >> "$SD/recent.jsonl"
  tail -n 40 "$SD/recent.jsonl" > "$SD/recent.jsonl.tmp" && mv "$SD/recent.jsonl.tmp" "$SD/recent.jsonl"
}

# ---------- R1 磁盘守护 ----------
disk_pct=$(df / | awk 'NR==2{gsub("%","",$5);print $5}')
disk_state="ok"; disk_detail="当前 ${disk_pct}% · 低于 ${DISK_TRIP}% 触发线,无需清理"
disk_last=$(cat "$SD/disk_last" 2>/dev/null || echo "")
disk_last_at=$(cat "$SD/disk_last_at" 2>/dev/null || echo "")
disk_count=$(cat "$SD/disk_count" 2>/dev/null || echo 0)
if [ "${disk_pct:-0}" -ge "$DISK_TRIP" ]; then
  before=$disk_pct
  docker system prune -f --filter "until=168h" >/dev/null 2>&1 || true
  sudo -n journalctl --vacuum-size=200M >/dev/null 2>&1 || true
  find /srv/linze/backups -name 'linze-backup-*.enc' -mtime +7 -delete 2>/dev/null || true
  after=$(df / | awk 'NR==2{gsub("%","",$5);print $5}')
  disk_last="清理可回收空间:磁盘 ${before}% → ${after}%"
  disk_last_at="$TS"; disk_pct=$after
  disk_count=$(( disk_count + 1 )); disk_state="acted"
  disk_detail="已清理 · 磁盘 ${before}% → ${after}%"
  echo "$disk_last" > "$SD/disk_last"; echo "$disk_last_at" > "$SD/disk_last_at"; echo "$disk_count" > "$SD/disk_count"
  log "DISK $disk_last"; push_recent disk "$disk_last"
fi

# ---------- R2 服务看门狗(仅可靠命名的 host-direct 服务)----------
# 每条:URL|容器名grep|展示名。Coolify 托管的 app 由 Coolify 健康检查 + docker 重启策略兜底(见页面「容器崩溃自动拉起」)。
MON="https://status.linzezhang.com|linze-status|Status
https://account.linzezhang.com|keycloak|Account
https://kmfa.linzezhang.com|skills|KMFA"
wd_online=0; wd_total=0; wd_last=$(cat "$SD/wd_last" 2>/dev/null || echo ""); wd_last_at=$(cat "$SD/wd_last_at" 2>/dev/null || echo "")
wd_count=$(cat "$SD/wd_count" 2>/dev/null || echo 0)
while IFS='|' read -r url grep_name disp; do
  [ -z "$url" ] && continue
  wd_total=$(( wd_total + 1 ))
  cname=$(docker ps --format '{{.Names}}' | grep -i "$grep_name" | head -1)
  code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 "$url" 2>/dev/null || echo 000)
  # 200/301/302/401/403 都算「服务活着」(被 Access 拦也是活着)
  if echo "$code" | grep -qE '^(200|301|302|307|308|401|403)$'; then
    wd_online=$(( wd_online + 1 )); echo 0 > "$SD/fail_${grep_name}"
    continue
  fi
  fails=$(cat "$SD/fail_${grep_name}" 2>/dev/null || echo 0); fails=$(( fails + 1 )); echo "$fails" > "$SD/fail_${grep_name}"
  [ "$fails" -lt "$FAIL_TRIP" ] && { log "WATCH $disp code=$code fails=$fails(未达阈值)"; continue; }
  [ -z "$cname" ] && { log "WATCH $disp down code=$code 但未匹配到容器,跳过"; continue; }
  last_r=$(cat "$SD/restart_${grep_name}" 2>/dev/null || echo 0)
  if [ $(( NOW - last_r )) -lt "$COOLDOWN" ]; then log "WATCH $disp down 但在冷却期,不重启"; continue; fi
  docker restart "$cname" >/dev/null 2>&1 && ok=1 || ok=0
  echo "$NOW" > "$SD/restart_${grep_name}"; echo 0 > "$SD/fail_${grep_name}"
  wd_count=$(( wd_count + 1 ))
  wd_last="$disp 服务 HTTP 连续失败(code=$code),已自动重启容器 $cname"
  wd_last_at="$TS"; echo "$wd_last" > "$SD/wd_last"; echo "$wd_last_at" > "$SD/wd_last_at"; echo "$wd_count" > "$SD/wd_count"
  log "WATCH $wd_last ok=$ok"; push_recent watchdog "$wd_last"
done <<< "$MON"
wd_state="ok"; [ "$wd_online" -lt "$wd_total" ] && wd_state="acted"
wd_detail="${wd_online}/${wd_total} host-direct 服务在线"

# ---------- R3 元自愈:采集器看门狗(自愈的自愈——采集器卡死就自动重跑)----------
SNAP=/srv/linze/apps/status/data/snapshot.json
cw_state="ok"; cw_detail="采集器心跳正常"; cw_last=$(cat "$SD/cw_last" 2>/dev/null || echo "")
cw_last_at=$(cat "$SD/cw_last_at" 2>/dev/null || echo ""); cw_count=$(cat "$SD/cw_count" 2>/dev/null || echo 0)
if [ -f "$SNAP" ]; then
  snap_age=$(( NOW - $(stat -c %Y "$SNAP") ))
  if [ "$snap_age" -gt 300 ]; then         # 快照 >5 分钟没更新 = 采集器卡死
    sudo -u ubuntu python3 /srv/linze/apps/status/collector/collect.py >/dev/null 2>&1 && ok=1 || ok=0
    cw_count=$(( cw_count + 1 )); cw_state="acted"
    cw_last="快照已 $(( snap_age/60 )) 分钟未更新,已自动重跑采集器(ok=$ok)"
    cw_last_at="$TS"; cw_detail="已自动重跑采集器"
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
export TS NOW disk_pct disk_state disk_detail disk_count disk_last disk_last_at DISK_TRIP
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
  "threshold":"磁盘 ≥%s%% 自动清理可回收空间"%g("DISK_TRIP","85"),
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
