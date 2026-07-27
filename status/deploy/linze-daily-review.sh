#!/bin/bash
# 每日只读复审 —— 装到 /usr/local/bin/linze-daily-review.sh,由 cron 每天 08:00(悉尼)以 ubuntu 运行。
#
# 设计铁律(和自愈引擎同源):**纯服务器脚本自运行,零 agent、零 token、零人工授权**。
# 只读:全部动作是 curl GET / 读本地快照 / 调 GitHub 只读 API。绝不写任何被监控对象。
# 唯一写动作是产出问题清单 private/daily_review.md 与去重状态文件。
#
# 为什么存在:status 面板只统计"进了门的"数据 —— 被 401 挡在 Coolify 门外的部署失败不进分母,
# ci_fail 只看仓的最近一次 run(一条绿的能盖住长期连红)。这个脚本从 GitHub 侧独立取证补这个洞。
set -uo pipefail

APP=/srv/linze/apps/status
OUT="$APP/private/daily_review.md"          # CF Access 门禁后可见,含金额,不进公开目录
STATE="$APP/private/.review_state"           # 指纹|首次出现日期
LOG="$APP/daily-review.log"
SECRETS="$APP/.secrets"
SNAP="https://status.linzezhang.com/data/snapshot.json"
TODAY=$(TZ=Australia/Sydney date +%F)
NOWS=$(TZ=Australia/Sydney date +'%Y-%m-%d %H:%M')
TMP=$(mktemp -d); trap 'rm -rf "$TMP"' EXIT

log(){ echo "$NOWS $*" >> "$LOG"; }
get(){ curl -s --max-time 20 "$1" 2>/dev/null; }
code(){ curl -s -o /dev/null -w '%{http_code}' --max-time 12 "$1" 2>/dev/null || echo 000; }

# findings: 紧急度\t指纹\t标题\t证据
F="$TMP/findings.tsv"; : > "$F"
add(){ printf '%s\t%s\t%s\t%s\n' "$1" "$2" "$3" "$4" >> "$F"; }
GAPS="$TMP/gaps.txt"; : > "$GAPS"
gap(){ echo "$1" >> "$GAPS"; }

# ---------- 1. 平台快照 ----------
get "$SNAP" > "$TMP/snap.json"
if ! jq -e . "$TMP/snap.json" >/dev/null 2>&1; then
  gap "快照取不到或不是合法 JSON —— 本轮主机/证书/备份/自愈全部未核"
else
  q(){ jq -r "$1 // empty" "$TMP/snap.json" 2>/dev/null; }
  snap_at=$(q .updated_at); disk=$(q .host.disk_pct); mem=$(q .host.mem_pct)
  cert_days=$(q .ops.cert.days); cert_dom=$(q .ops.cert.domain)
  bak_at=$(q .ops.backup.at); bak_ok=$(q .ops.backup.ok)
  armed=$(q .selfheal.armed); heal_total=$(q .selfheal.total); heal_at=$(q .selfheal.last_run)
  online=$(q .summary.services_online)

  [ -n "$disk" ] && [ "$disk" -ge 80 ] 2>/dev/null && \
    add now "disk-high" "主机磁盘 ${disk}%" "快照 $snap_at · 自愈 70% 清 build cache / 85% 全量,已过 80% 说明清理跟不上增速"
  [ -n "$mem" ] && [ "$mem" -ge 90 ] 2>/dev/null && \
    add now "mem-high" "主机内存 ${mem}%" "快照 $snap_at"
  [ -n "$cert_days" ] && [ "$cert_days" -lt 30 ] 2>/dev/null && \
    add now "cert-expiring" "证书 $cert_dom 仅剩 ${cert_days} 天" "Traefik 自动续期若失效,到期即全站不可访问"
  [ "$bak_ok" = "false" ] && add now "backup-stale" "备份异常" "最近备份 $bak_at · ops.backup.ok=false"
  if [ -n "$armed" ] && [ -n "$heal_total" ] && [ "$armed" -lt "$heal_total" ] 2>/dev/null; then
    add now "selfheal-unarmed" "自愈规则未全部武装 ${armed}/${heal_total}" "自愈上次运行 $heal_at"
  fi
  # 快照自身停更(采集器挂了)
  if [ -n "$snap_at" ]; then
    age=$(( ( $(date +%s) - $(date -d "$snap_at" +%s 2>/dev/null || echo $(date +%s)) ) / 60 ))
    [ "$age" -gt 30 ] && add now "snapshot-stale" "快照停更 ${age} 分钟" "最后更新 $snap_at · 采集器或其看门狗失效"
  fi
  # 容量趋势(磁盘之外的额度)
  jq -r '.usage[]? | select(.eta_date != null) | "\(.key)\t\(.label)\t\(.eta_date)\t\(.eta_note)"' \
     "$TMP/snap.json" 2>/dev/null | while IFS=$'\t' read -r k lb ed en; do
    days=$(( ( $(date -d "$ed" +%s 2>/dev/null || echo 99999999999) - $(date +%s) ) / 86400 ))
    [ "$days" -lt 30 ] && printf 'later\tcap-%s\t容量 %s 预计 %s 触顶\t%s\n' "$k" "$lb" "$ed" "$en" >> "$F"
  done
fi

# ---------- 2. 12 个域名探活(两次,躲开滚动部署空窗) ----------
HOSTS="home nab pfi serenity kmfa account eei status alpha adp uptime server"
for h in $HOSTS; do
  c=$(code "https://$h.linzezhang.com/")
  case "$c" in
    200|301|302|307|308|401|403) ;;
    *) sleep 45
       c2=$(code "https://$h.linzezhang.com/")
       case "$c2" in
         200|301|302|307|308|401|403) ;;
         *) add now "down-$h" "$h.linzezhang.com 不可用" "连续两次探测:$c → $c2(间隔 45 秒,已排除滚动部署空窗)" ;;
       esac ;;
  esac
done

# ---------- 3. Alpha 资金口径 ----------
get "https://alpha.linzezhang.com/api/overview" > "$TMP/alpha.json"
if jq -e .hero "$TMP/alpha.json" >/dev/null 2>&1; then
  a(){ jq -r "$1 // empty" "$TMP/alpha.json"; }
  expo=$(a .hero.exposure_pct); inv=$(a .hero.invested_usd); auth=$(a .hero.authorized_usd)
  mode=$(a .mode_cn); bk=$(a .banner.kind); bt=$(a .banner.text)
  awk -v e="${expo:-0}" 'BEGIN{exit !(e>100)}' && \
    add now "alpha-exposure" "Alpha 敞口 ${expo}% 超过授权上限" "持仓 ${inv} USD / 授权 ${auth} USD · 模式 ${mode}"
  awk -v i="${inv:-0}" -v u="${auth:-0}" 'BEGIN{exit !(u>0 && i>u)}' && \
    add now "alpha-overinvested" "Alpha 持仓超授权" "持仓 ${inv} USD > 授权 ${auth} USD"
  [ -n "$bk" ] && [ "$bk" != "ok" ] && \
    add now "alpha-banner" "Alpha 自报异常状态[$bk]" "$(echo "$bt" | cut -c1-120)"
  jq -r '.health.components[]? | select(.status != "RUNNING") | "\(.name)\t\(.status)\t\(.age_s)"' \
     "$TMP/alpha.json" 2>/dev/null | while IFS=$'\t' read -r n s ag; do
    printf 'now\talpha-hb-%s\tAlpha 心跳异常:%s=%s\t静默 %s 秒(真实资金系统,主循环停摆即无人接管)\n' "$n" "$n" "$s" "$ag" >> "$F"
  done
else
  gap "Alpha /api/overview 取不到 —— 本轮资金口径未核"
fi

# ---------- 4. ADP ----------
get "https://adp.linzezhang.com/api/runhealth" > "$TMP/adp_run.json"
if jq -e . "$TMP/adp_run.json" >/dev/null 2>&1; then
  res=$(jq -r '.result // empty' "$TMP/adp_run.json")
  [ -n "$res" ] && [ "$res" != "正常" ] && \
    add now "adp-run" "ADP 最近一跑结果:$res" "$(jq -c '{result,degraded}' "$TMP/adp_run.json" | cut -c1-200)"
  # meta 为 null 只有在 degraded 里确实带 meta: 标记时才是病(已验证的良性判读)
  if [ "$(jq -r '.meta // "null"' "$TMP/adp_run.json")" = "null" ] && \
     jq -e '[.degraded[]? | select(startswith("meta:"))] | length > 0' "$TMP/adp_run.json" >/dev/null 2>&1; then
    add now "adp-meta" "ADP 富集真实失败" "meta 为空且 degraded 带 meta: 标记 —— 这是 P08 病,不是当天无 DOI"
  fi
else
  gap "ADP /api/runhealth 取不到"
fi
get "https://adp.linzezhang.com/api/backfill" > "$TMP/adp_bf.json"
if jq -e . "$TMP/adp_bf.json" >/dev/null 2>&1; then
  cur=$(jq -r '.cursor // "null"' "$TMP/adp_bf.json")
  prev=$(grep '^adp_cursor=' "$STATE.kv" 2>/dev/null | cut -d= -f2-)
  if [ -n "$prev" ] && [ "$cur" = "$prev" ] && [ "$cur" != "null" ]; then
    add now "adp-cursor" "ADP 回填 cursor 24 小时未推进" "cursor 仍停在 $cur · 回填 cron 应每天推进约两个窗口"
  fi
  sed -i '/^adp_cursor=/d' "$STATE.kv" 2>/dev/null
  echo "adp_cursor=$cur" >> "$STATE.kv"
fi

# ---------- 5. EEI 探针 ----------
ct=$(curl -s -o /dev/null -w '%{content_type}' --max-time 12 https://eei.linzezhang.com/healthz 2>/dev/null)
case "$ct" in
  *json*) ;;
  *) add later "eei-healthz" "EEI /healthz 返回 $ct 而非 JSON" "被前端兜底路由吞掉,任何基于它的存活判断永远为真(连 500 都探不出)" ;;
esac

# ---------- 6. GitHub 工程面(独立于面板取证) ----------
PAT=$(cat "$SECRETS/github_pat" 2>/dev/null)
if [ -z "$PAT" ]; then
  gap "读不到 github_pat —— 本轮 CI 与 secret 同步未核(面板的 ci_fail 会漏报长期连红)"
else
  gh_api(){ curl -s --max-time 25 -H "Authorization: Bearer $PAT" \
            -H "Accept: application/vnd.github+json" "https://api.github.com/$1" 2>/dev/null; }
  for r in CodexProject MetaDatabase KMOS AgentDatabase Governance LinzeHomeHub Archive; do
    gh_api "repos/LinzeColin/$r/actions/runs?branch=main&per_page=30" > "$TMP/runs.json"
    if ! jq -e .workflow_runs "$TMP/runs.json" >/dev/null 2>&1; then
      msg=$(jq -r '.message // "响应不是合法 JSON"' "$TMP/runs.json" 2>/dev/null)
      gap "$r 的 CI 未核 —— GitHub API 说「$msg」。若是权限问题,需给 .secrets/github_pat 补该仓的 Actions:read"
      continue
    fi
    # 每条 workflow 只看它自己最新一次的结论 —— 面板那种"仓的最近一次 run"口径会被一条绿的盖住
    jq -r '[.workflow_runs[] | {n:.name, c:.conclusion, at:.created_at, u:.html_url}]
           | group_by(.n)[] | .[0] | select(.c == "failure")
           | "\(.n)\t\(.at)\t\(.u)"' "$TMP/runs.json" 2>/dev/null | \
    while IFS=$'\t' read -r wf at url; do
      case "$wf" in
        *deploy*|*Deploy*|*golden*|*Golden*)
          printf 'now\tci-%s-%s\t%s 的 main 分支 %s 失败\t%s · %s —— 部署链路断了,面板看不见(被挡在 Coolify 门外的失败不进分母)\n' \
                 "$r" "$(echo "$wf"|tr -c 'a-zA-Z0-9' '-')" "$r" "$wf" "$at" "$url" >> "$F" ;;
        *)
          printf 'now\tci-%s-%s\t%s 的 main 分支 %s 失败\t%s · %s\n' \
                 "$r" "$(echo "$wf"|tr -c 'a-zA-Z0-9' '-')" "$r" "$wf" "$at" "$url" >> "$F" ;;
      esac
    done
  done
  # Coolify token 轮换是否全量同步(2026-07-24 漏同步导致 LinzeHomeHub 连挂 13 次、线上停两天)
  newest=0; sec_err=""
  for r in LinzeHomeHub MetaDatabase KMOS Archive; do
    gh_api "repos/LinzeColin/$r/actions/secrets/COOLIFY_API_TOKEN" > "$TMP/sec.json"
    u=$(jq -r '.updated_at // empty' "$TMP/sec.json")
    if [ -z "$u" ]; then
      [ -z "$sec_err" ] && sec_err=$(jq -r '.message // "无返回"' "$TMP/sec.json" 2>/dev/null)
      continue
    fi
    ts=$(date -d "$u" +%s 2>/dev/null || echo 0)
    echo "$r $ts $u" >> "$TMP/secrets.txt"
    [ "$ts" -gt "$newest" ] && newest=$ts
  done
  if [ ! -s "$TMP/secrets.txt" ]; then
    gap "Coolify token 同步检查未跑 —— GitHub API 说「${sec_err:-未知}」。这条正是 2026-07-24 那次「部署连挂 13 次、线上停两天」的根因检查:需要 .secrets/github_pat 具备四个仓的 Secrets:read 权限"
  fi
  if [ -s "$TMP/secrets.txt" ] && [ "$newest" -gt 0 ]; then
    while read -r r ts u; do
      d=$(( (newest - ts) / 86400 ))
      [ "$d" -ge 3 ] && add now "secret-lag-$r" "$r 的 COOLIFY_API_TOKEN 比最新的旧 ${d} 天" \
        "该仓 $u —— 轮换时可能漏同步,下次推 main 会 401,而面板不会显示(待爆的雷)"
    done < "$TMP/secrets.txt"
  fi
fi

# ---------- 7. 指纹去重 → 新增 / 仍在 / 已消失 ----------
touch "$STATE"
sort -u "$F" -o "$F"
cut -f2 "$F" | sort -u > "$TMP/today_fp"
: > "$TMP/state_new"
NEW=0; CONT=0; GONE=0; GONE_LIST="$TMP/gone.txt"; : > "$GONE_LIST"
while IFS='|' read -r fp first; do
  [ -z "$fp" ] && continue
  if grep -qxF "$fp" "$TMP/today_fp"; then
    echo "$fp|$first" >> "$TMP/state_new"
  else
    echo "$fp|$first" >> "$GONE_LIST"; GONE=$((GONE+1))
  fi
done < "$STATE"
while read -r fp; do
  [ -z "$fp" ] && continue
  grep -q "^$fp|" "$STATE" || { echo "$fp|$TODAY" >> "$TMP/state_new"; }
done < "$TMP/today_fp"
sort -u "$TMP/state_new" -o "$TMP/state_new"

# ---------- 8. 写清单 ----------
{
  echo ""
  echo "## $TODAY"
  echo ""
  tn=$(awk -F'\t' '$1=="now"' "$F" | wc -l); tl=$(awk -F'\t' '$1=="later"' "$F" | wc -l)
  if [ "$tn" -eq 0 ] && [ "$tl" -eq 0 ] && [ ! -s "$GAPS" ]; then
    echo "全绿 · 在线 ${online:-?} · 磁盘 ${disk:-?}% · 快照 ${snap_at:-?}"
  else
    echo "在线 ${online:-?} · 磁盘 ${disk:-?}% · 现在 ${tn} 条 / 以后 ${tl} 条"
  fi

  for lvl in now later; do
    cnt=$(awk -F'\t' -v L="$lvl" '$1==L' "$F" | wc -l)
    [ "$cnt" -eq 0 ] && continue
    [ "$lvl" = now ] && echo "" && echo "### 现在" || { echo ""; echo "### 以后"; }
    awk -F'\t' -v L="$lvl" '$1==L' "$F" | while IFS=$'\t' read -r _ fp title ev; do
      first=$(grep "^$fp|" "$TMP/state_new" | head -1 | cut -d'|' -f2)
      if [ "$first" = "$TODAY" ]; then
        mark="🆕 新增"
      else
        days=$(( ( $(date -d "$TODAY" +%s) - $(date -d "$first" +%s) ) / 86400 + 1 ))
        [ "$lvl" = now ] && [ "$days" -ge 3 ] && mark="⚠️ 已连续 ${days} 天" || mark="已连续 ${days} 天"
      fi
      echo ""
      echo "- **$title** · $mark"
      echo "  - 证据:$ev"
    done
  done

  if [ -s "$GONE_LIST" ]; then
    echo ""; echo "### 已消失"
    while IFS='|' read -r fp first; do echo "- $fp(首次出现 $first)"; done < "$GONE_LIST"
  fi
  if [ -s "$GAPS" ]; then
    echo ""; echo "### 取证缺口(取不到 ≠ 没问题)"
    sed 's/^/- /' "$GAPS"
  fi
} >> "$OUT"

mv "$TMP/state_new" "$STATE"
chmod 640 "$OUT" "$STATE" 2>/dev/null
NEW=$(awk -F'\t' '{print $2}' "$F" | sort -u | while read -r fp; do grep -q "^$fp|$TODAY$" "$STATE" && echo x; done | wc -l)
log "REVIEW today=$TODAY findings=$(wc -l < "$F") new=$NEW gone=$GONE gaps=$(wc -l < "$GAPS")"

# 清单过长时把当月归档切走,避免无限膨胀
if [ "$(wc -l < "$OUT")" -gt 1200 ]; then
  ARCH="$APP/private/daily_review-$(TZ=Australia/Sydney date +%Y-%m).md"
  mv "$OUT" "$ARCH"; chmod 640 "$ARCH"
  { echo "# 每日只读复审 · 问题清单"; echo ""; echo "上一段已归档到 $(basename "$ARCH")。"; } > "$OUT"
  chmod 640 "$OUT"; log "ARCHIVE -> $ARCH"
fi
exit 0
