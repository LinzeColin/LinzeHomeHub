#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
LinzeStatus 采集器 —— 在 OVH VPS-1 主机上由 cron 每 15 分钟运行一次。
把 Coolify 数据库、主机指标、证书、备份、汇率、价格库汇总成 data/snapshot.json,
供 status.linzezhang.com 的静态页读取渲染。只读采集,唯一写动作是价格库(人工编辑)之外的快照文件。
所有面向用户的时间统一「北京时间 UTC+8」。
"""
import json
import os
import re
import ssl
import socket
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timezone, timedelta

CN = timezone(timedelta(hours=8))          # 北京时间
APP_DIR = os.environ.get("STATUS_APP_DIR", "/srv/linze/apps/status")
DATA_DIR = os.path.join(APP_DIR, "data")
BACKUP_DIR = os.environ.get("STATUS_BACKUP_DIR", "/srv/linze/backups")
HISTORY_MAX = 96                            # 24h @ 15min

# 项目静态配置(存不存在库、通知渠道等靠运维已知;运行状态靠实时探测)
# 每个项目的运行逻辑:跑在哪(host)/ 数据库(db)/ 文件存储(store)/ 部署方式(deploy)/
# 备份(backup)/ agent 依赖度(agent:无/低/中)。运行状态靠实时探测。
PROJECTS = [
    {"name": "Home",     "url": "https://home.linzezhang.com",     "parts": ["前台"],
     "host": "OVH VPS-1", "db": "无(纯静态前台)", "store": "无(构建产物在镜像内)", "deploy": "Golden Path 自动",
     "backup": "随主机镜像 + 源码在 GitHub", "agent": "低", "notify": "无"},
    {"name": "Nab",      "url": "https://nab.linzezhang.com",      "parts": ["前台"],
     "host": "OVH VPS-1", "db": "无(纯静态前台)", "store": "无(构建产物在镜像内)", "deploy": "Golden Path 自动",
     "backup": "随主机镜像 + 源码在 GitHub", "agent": "低", "notify": "无"},
    {"name": "PFI",      "url": "https://pfi.linzezhang.com",      "parts": ["前台"],
     "host": "OVH VPS-1", "db": "无(纯静态前台)", "store": "无(构建产物在镜像内)", "deploy": "Golden Path 自动",
     "backup": "随主机镜像 + 源码在 GitHub", "agent": "低", "notify": "无"},
    {"name": "Serenity", "url": "https://serenity.linzezhang.com", "parts": ["前台"],
     "host": "OVH VPS-1", "db": "无(纯静态前台)", "store": "无(构建产物在镜像内)", "deploy": "Golden Path 自动",
     "backup": "随主机镜像 + 源码在 GitHub", "agent": "低", "notify": "无"},
    {"name": "KMFA",     "url": "https://kmfa.linzezhang.com",     "parts": ["前台", "后台"],
     "host": "OVH VPS-1", "db": "无独立库·报告写文件", "store": "OVH 文件", "deploy": "Coolify + cron worker",
     "backup": "私有备份仓 + 随主机", "agent": "中", "notify": "钉钉"},
    {"name": "Account",  "url": "https://account.linzezhang.com",  "parts": ["后台"],
     "host": "OVH VPS-1", "db": "OVH Postgres · identity-postgres", "store": "Postgres", "deploy": "Coolify compose",
     "backup": "身份库 cron 03:37 + 随主机", "agent": "低", "notify": "邮件"},
    {"name": "EEI",      "url": "",                                "parts": ["后台"],
     "host": "OVH VPS-1", "db": "OVH Postgres · eei-db  +  CF D1 · eei-publication", "store": "Postgres + CF D1",
     "deploy": "Coolify compose", "backup": "随主机 + CF", "agent": "中", "notify": "无(内部服务)"},
    {"name": "Status",   "url": "https://status.linzezhang.com",   "parts": ["前台"],
     "host": "OVH VPS-1", "db": "OVH 文件 · prices.json", "store": "OVH 文件", "deploy": "host-direct rsync",
     "backup": "每日加密 → GitHub", "agent": "无(纯 cron)", "notify": "无"},
]


def now_cn():
    return datetime.now(CN)


def fmt(dt):
    return dt.strftime("%Y-%m-%d %H:%M")


def run(cmd, timeout=20):
    try:
        out = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
        return out.stdout.strip()
    except Exception:
        return ""


def psql(sql):
    """在 coolify-db 容器里跑只读查询,竖线分隔。"""
    esc = sql.replace('"', '\\"')
    return run(f'docker exec coolify-db psql -U coolify -t -A -F"|" -c "{esc}" 2>/dev/null')


def http_code(url):
    return run(f'curl -s -o /dev/null -w "%{{http_code}}" --max-time 10 "{url}"', timeout=15)


# ---------- 主机指标 ----------
def host_metrics():
    mem = run("free -m | awk '/Mem:/{printf \"%.0f\", $3/$2*100}'")
    disk = run("df / | awk 'NR==2{gsub(\"%\",\"\",$5); print $5}'")
    up_days = run("awk '{printf \"%d\", $1/86400}' /proc/uptime")
    load = run("awk '{print $1}' /proc/loadavg")
    dbytes = run("df -B1 / | awk 'NR==2{print $3\" \"$2}'").split()
    used_b = int(dbytes[0]) if len(dbytes) == 2 and dbytes[0].isdigit() else None
    total_b = int(dbytes[1]) if len(dbytes) == 2 and dbytes[1].isdigit() else None
    return {
        "mem_pct": int(mem) if mem.isdigit() else None,
        "disk_pct": int(disk) if disk.isdigit() else None,
        "disk_used_b": used_b,
        "disk_total_b": total_b,
        "uptime_days": int(up_days) if up_days.isdigit() else None,
        "load1": load,
    }


def container_health():
    """一次遍历同时拿:最大重启次数 + 崩溃自愈(restart 策略)覆盖率。"""
    names = run("docker ps --format '{{.Names}}'").splitlines()
    mx = covered = total = ephemeral = 0
    for n in names:
        if not n:
            continue
        total += 1
        info = run(f"docker inspect -f '{{{{.RestartCount}}}}|{{{{.HostConfig.RestartPolicy.Name}}}}' {n}")
        rc, _, pol = info.partition("|")
        if rc.isdigit():
            mx = max(mx, int(rc))
        if pol in ("always", "unless-stopped"):
            covered += 1
        elif pol in ("no", ""):
            ephemeral += 1                       # Coolify 构建/一次性任务容器,天然无需自愈策略
    return {"restarts": mx, "policy_covered": covered,
            "policy_total": total, "policy_ephemeral": ephemeral}


def fmt_bytes(b):
    if b is None:
        return "—"
    u = ["B", "KB", "MB", "GB", "TB"]
    i, v = 0, float(b)
    while v >= 1024 and i < len(u) - 1:
        v /= 1024
        i += 1
    return ("%.1f %s" % (v, u[i])) if (i > 0 and v < 100) else ("%.0f %s" % (v, u[i]))


# ---------- 部署统计(Coolify DB)----------
def deploy_stats():
    rows = psql("select status,count(*) from application_deployment_queues "
                "where created_at > now()-interval '30 days' group by status;")
    succ = total = 0
    for line in rows.splitlines():
        if "|" not in line:
            continue
        st, cnt = line.split("|", 1)
        cnt = int(cnt) if cnt.strip().isdigit() else 0
        total += cnt
        if st.strip() == "finished":
            succ += cnt
    rate = round(succ / total * 100, 1) if total else 0.0

    # 近7天(北京时间)每日计数
    labels, data = [], []
    for i in range(6, -1, -1):
        d = (now_cn() - timedelta(days=i))
        labels.append(d.strftime("%m-%d"))
        data.append(0)
    counts = psql("select to_char((created_at + interval '8 hours')::date,'MM-DD'), count(*) "
                  "from application_deployment_queues where created_at > now()-interval '7 days' "
                  "group by 1;")
    idx = {l: k for k, l in enumerate(labels)}
    for line in counts.splitlines():
        if "|" in line:
            lb, c = line.split("|", 1)
            if lb in idx and c.strip().isdigit():
                data[idx[lb]] = int(c)

    # 最近部署记录
    log = []
    recs = psql("select to_char(created_at + interval '8 hours','YYYY-MM-DD HH24:MI'), "
                "application_name, status from application_deployment_queues "
                "order by created_at desc limit 6;")
    for line in recs.splitlines():
        p = line.split("|")
        if len(p) == 3:
            log.append({"at": p[0], "app": p[1], "ok": p[2].strip() == "finished"})
    return {"success": succ, "total": total, "rate": rate,
            "d7_labels": labels, "d7_data": data, "log": log}


# ---------- 备份 ----------
def backup_status():
    latest = run(f"ls -t {BACKUP_DIR}/*.enc 2>/dev/null | head -1")
    if not latest:
        return {"at": None, "ok": False}
    ts = run(f"date -d @$(stat -c %Y '{latest}') +'%Y-%m-%d %H:%M'")
    # 24h 内算健康
    age = run(f"echo $(( ( $(date +%s) - $(stat -c %Y '{latest}') ) / 3600 ))")
    ok = age.isdigit() and int(age) < 26
    return {"at": ts, "ok": ok}


# ---------- 证书(直连本机 Traefik,绕过 CF;用 openssl CLI 取到期日)----------
def cert_earliest():
    domains = ["home.linzezhang.com", "kmfa.linzezhang.com", "account.linzezhang.com",
               "status.linzezhang.com", "serenity.linzezhang.com"]
    earliest = None
    for d in domains:
        out = run(f"echo | openssl s_client -connect 127.0.0.1:443 -servername {d} 2>/dev/null "
                  f"| openssl x509 -noout -enddate 2>/dev/null")
        if "=" not in out:
            continue
        try:
            exp = datetime.strptime(out.split("=", 1)[1].strip(),
                                    "%b %d %H:%M:%S %Y %Z").replace(tzinfo=timezone.utc)
        except Exception:
            continue
        if earliest is None or exp < earliest[1]:
            earliest = (d, exp)
    if not earliest:
        return {"date": None, "days": None}
    days = (earliest[1] - datetime.now(timezone.utc)).days
    return {"date": earliest[1].astimezone(CN).strftime("%Y-%m-%d"), "days": days, "domain": earliest[0]}


# ---------- 汇率 ----------
FX_CURRENCIES = ["AUD", "USD", "CNY", "EUR", "SGD", "GBP", "HKD", "JPY"]


def fx_rates(prev):
    try:
        req = urllib.request.Request("https://open.er-api.com/v6/latest/AUD",
                                     headers={"User-Agent": "Mozilla/5.0"})
        d = json.loads(urllib.request.urlopen(req, timeout=15).read())
        r = d["rates"]
        rates = {c: round(r[c], 6) for c in FX_CURRENCIES if c in r}
        rates["AUD"] = 1.0
        return {"aud_cny": round(r["CNY"], 4), "aud_usd": round(r["USD"], 4),
                "rates": rates, "at": fmt(now_cn())}
    except Exception:
        if prev and prev.get("fx"):
            return prev["fx"]           # 拉不到就沿用上次,不编造
        return {"aud_cny": None, "aud_usd": None, "rates": {"AUD": 1.0}, "at": None}


# ---------- 续费倒计时 ----------
def renew_days(purchase, cadence):
    """purchase 'YYYY-MM-DD';cadence 'monthly'|'yearly'  (下次日期, 剩余天)。"""
    p = datetime.strptime(purchase, "%Y-%m-%d").replace(tzinfo=CN)
    today = now_cn()
    nxt = p
    if cadence == "monthly":
        while nxt <= today:
            m = nxt.month + 1
            y = nxt.year + (1 if m > 12 else 0)
            m = 1 if m > 12 else m
            day = min(p.day, 28)
            nxt = nxt.replace(year=y, month=m, day=day)
    else:
        while nxt <= today:
            nxt = nxt.replace(year=nxt.year + 1)
    return nxt.strftime("%Y-%m-%d"), (nxt.date() - today.date()).days


# ---------- 开支(读价格库 + 汇率折算)----------
def cost(prices, fx):
    cny_rate = fx.get("aud_cny")
    rates = fx.get("rates") or {"AUD": 1.0}          # 各币种「每 1 AUD 折多少」

    def to_aud(amt, cur):
        per = rates.get(cur)
        return amt / per if per else amt

    today = now_cn()
    items, monthly_aud, month_cash_aud = [], 0.0, 0.0
    for it in prices.get("items", []):
        try:
            amt = float(it.get("amount", 0))
        except Exception:
            amt = 0.0
        cur = str(it.get("currency", "AUD")).upper()
        cadence = it.get("cadence", "monthly")
        purchase = it.get("purchase", "")
        base_aud = to_aud(amt, cur)                    # 原周期一次扣费的 AUD
        m_aud = base_aud / 12 if cadence == "yearly" else base_aud   # 月摊
        monthly_aud += m_aud

        pday = None
        if purchase:
            try:
                pday = datetime.strptime(purchase, "%Y-%m-%d").replace(tzinfo=CN)
            except Exception:
                pday = None

        this_renew, cash_aud = None, 0.0
        if amt > 0:
            if cadence == "monthly":
                day = min(pday.day, 28) if pday else min(today.day, 28)
                this_renew = today.replace(day=day).strftime("%Y-%m-%d")
                cash_aud = base_aud                    # 月付:本月照扣
            elif cadence == "yearly" and pday and pday.month == today.month:
                this_renew = today.replace(day=min(pday.day, 28)).strftime("%Y-%m-%d")
                cash_aud = base_aud                    # 年付:本月正好是周年月  本月扣年费
        month_cash_aud += cash_aud

        row = {
            "name": it.get("name", ""), "note": it.get("note", ""),
            "cadence": cadence, "currency": cur, "amount": round(amt, 2),
            "purchase": purchase, "auto_renew": bool(it.get("auto_renew", False)),
            "aud": round(m_aud, 2),                    # 月摊 AUD
            "cny": round(m_aud * cny_rate, 2) if cny_rate else None,
            "this_month_renew": this_renew,            # 本月续费日(无则 None)
            "month_cost_aud": round(cash_aud, 2),      # 当月实付 AUD
            "month_cost_cny": round(cash_aud * cny_rate, 2) if cny_rate else None,
        }
        if purchase and it.get("track_renew"):
            row["renew_date"], row["renew_days"] = renew_days(purchase, cadence)
        items.append(row)
    return {
        "items": items,
        "monthly_aud": round(monthly_aud, 2),
        "monthly_cny": round(monthly_aud * cny_rate, 2) if cny_rate else None,
        "yearly_aud": round(monthly_aud * 12, 2),
        "yearly_cny": round(monthly_aud * 12 * cny_rate, 2) if cny_rate else None,
        "month_cash_aud": round(month_cash_aud, 2),
        "month_cash_cny": round(month_cash_aud * cny_rate, 2) if cny_rate else None,
    }


# ---------- 外部服务(公共状态 API,真实)----------
def externals():
    def status_api(url):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            d = json.loads(urllib.request.urlopen(req, timeout=10).read())
            ind = d.get("status", {}).get("indicator", "none")
            return ind in ("none", "minor")
        except Exception:
            return None
    cf = status_api("https://www.cloudflarestatus.com/api/v2/status.json")
    gh = status_api("https://www.githubstatus.com/api/v2/status.json")
    return [
        {"name": "Cloudflare", "ok": cf, "note": "DNS+代理" if cf else "查不到状态"},
        {"name": "GitHub", "ok": gh, "note": "运行正常" if gh else "查不到状态"},
        {"name": "NitroSend", "ok": True, "note": "已接入·免费"},
        {"name": "OVH VPS-1", "ok": True, "note": "主机在线"},
    ]


# ---------- 项目实时状态 ----------
def projects_live():
    out = []
    online = 0
    for p in PROJECTS:
        st = "run"
        if p["url"]:
            code = http_code(p["url"])
            if code in ("200", "301", "308"):
                st, online = "run", online + 1
            elif code in ("302", "401", "403"):
                st, online = "access", online + 1     # 被 Access 拦=服务其实活着
            else:
                st = "down"
        else:
            # 无对外址,看容器在不在(EEI)
            running = run(f"docker ps --format '{{{{.Names}}}}' | grep -i '{p['name'].lower()}' | head -1")
            st = "run" if running else "down"
            online += 1 if running else 0
        out.append({**p, "status": st})
    return out, online


def load_json(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return default


# ---------- 用量 vs 免费额度(潜在收费监控)----------
CF_ACCOUNT = "a8e86fa4be62ee3f9b5873b2aa934256"
OFFSITE_LOG = "/srv/linze/logs/offsite-backup.log"
SECRETS_DIR = os.path.join(APP_DIR, ".secrets")
GB = 1024 ** 3

# R2/D1 需一次性授权(读令牌)才能自动;在此之前展示人工核对值并标注日期,不冒充实时
MANUAL_USAGE = [
    {"key": "r2", "label": "Cloudflare R2 存储", "used": 5054136, "limit": 10 * GB,
     "unit": "bytes", "source": "manual", "checked": "2026-07-24",
     "note": "adp-raw-artifacts 桶 · 手动核对,变动很慢"},
    {"key": "d1", "label": "Cloudflare D1 存储", "used": 53784576, "limit": 5 * GB,
     "unit": "bytes", "source": "manual", "checked": "2026-07-24",
     "note": "eei-publication + adp-mirror · 手动核对,变动很慢"},
]


def _read_secret(name):
    try:
        with open(os.path.join(SECRETS_DIR, name)) as f:
            return f.read().strip()
    except Exception:
        return None


def oci_usage():
    """OCI PAR 只写不可删  累计上传量  远端占用。顺带从日志日期还原历史,用于测增速。"""
    total, series = 0, []
    try:
        with open(OFFSITE_LOG) as f:
            for line in f:
                m = re.search(r"^(\d{4}-\d{2}-\d{2})T.*offsite=200 size=(\d+)B", line)
                if m:
                    total += int(m.group(2))
                    series.append({"d": m.group(1), "u": total})
    except Exception:
        return None
    return {"key": "oci_backup", "label": "OCI 离机备份(备份的备份)", "used": total,
            "limit": 20 * GB, "unit": "bytes", "source": "auto", "series": series,
            "note": "已改为每周日一次;远端不可删,只增不减"}


def github_backup_usage():
    """GitHub Release 备份资产:滚动保留,天然有上限。"""
    tok = _read_secret("github_pat")
    if not tok:
        return None
    try:
        req = urllib.request.Request(
            "https://api.github.com/repos/LinzeColin/Private-Database/releases/tags/infra-backups",
            headers={"Authorization": "Bearer " + tok, "User-Agent": "linze-status"})
        rel = json.loads(urllib.request.urlopen(req, timeout=15).read())
        assets = [a for a in rel.get("assets", []) if a.get("name", "").startswith("linze-backup-")]
        size = sum(a.get("size", 0) for a in assets)
        return {"key": "github_backup", "label": "GitHub 备份(滚动保留)", "used": len(assets),
                "limit": 30, "unit": "count", "source": "auto", "bounded": True,
                "note": "合计 %.1f MB · 满 30 份自动删最旧" % (size / 1048576)}
    except Exception:
        return None


def r2_usage(token):
    """R2 存储字节 via GraphQL analytics(需 R2 读令牌)。"""
    try:
        now_u = datetime.now(timezone.utc)
        start = (now_u - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        end = now_u.strftime("%Y-%m-%dT%H:%M:%SZ")
        q = ('query{viewer{accounts(filter:{accountTag:"%s"}){'
             'r2StorageAdaptiveGroups(limit:50,filter:{datetime_geq:"%s",datetime_leq:"%s"})'
             '{max{payloadSize metadataSize}dimensions{bucketName}}}}}') % (CF_ACCOUNT, start, end)
        req = urllib.request.Request("https://api.cloudflare.com/client/v4/graphql",
            data=json.dumps({"query": q}).encode(),
            headers={"Authorization": "Bearer " + token, "User-Agent": "linze-status",
                     "Content-Type": "application/json"})
        d = json.loads(urllib.request.urlopen(req, timeout=15).read())
        groups = d["data"]["viewer"]["accounts"][0]["r2StorageAdaptiveGroups"]
        total = sum(g["max"].get("payloadSize", 0) + g["max"].get("metadataSize", 0) for g in groups)
        names = ", ".join(g["dimensions"]["bucketName"] for g in groups) or "无桶"
        return {"key": "r2", "label": "Cloudflare R2 存储", "used": total, "limit": 10 * GB,
                "unit": "bytes", "source": "auto", "note": names + " 桶"}
    except Exception:
        return None


def d1_usage(token):
    """D1 各库 file_size 求和(需 D1 读令牌)。"""
    try:
        req = urllib.request.Request(
            "https://api.cloudflare.com/client/v4/accounts/%s/d1/database?per_page=100" % CF_ACCOUNT,
            headers={"Authorization": "Bearer " + token, "User-Agent": "linze-status"})
        d = json.loads(urllib.request.urlopen(req, timeout=15).read())
        dbs = d.get("result", []) or []
        total = sum(x.get("file_size", 0) for x in dbs)
        names = ", ".join(x["name"] for x in dbs) or "无库"
        return {"key": "d1", "label": "Cloudflare D1 存储", "used": total, "limit": 5 * GB,
                "unit": "bytes", "source": "auto", "note": names}
    except Exception:
        return None


def access_seats():
    tok = _read_secret("cf_access_token")
    if not tok:
        return None
    try:
        req = urllib.request.Request(
            f"https://api.cloudflare.com/client/v4/accounts/{CF_ACCOUNT}/access/users?per_page=1",
            headers={"Authorization": "Bearer " + tok, "User-Agent": "Mozilla/5.0"})
        d = json.loads(urllib.request.urlopen(req, timeout=12).read())
        n = (d.get("result_info") or {}).get("total_count")
        if n is None:
            return None
        return {"key": "cf_access", "label": "Cloudflare Access 席位", "used": n, "limit": 50,
                "unit": "count", "source": "auto", "note": "满 45 席自动熔断"}
    except Exception:
        return None


def record_usage_history(items):
    """每天给每个指标留一个采样点,用来测增速。"""
    hist = load_json(os.path.join(DATA_DIR, "usage_history.json"), {})
    today = now_cn().strftime("%Y-%m-%d")
    for it in items:
        k = it["key"]
        arr = hist.get(k, [])
        if it.get("series") and len(it["series"]) > len(arr):
            arr = it["series"]                      # 用日志还原的历史直接补齐
        elif arr and arr[-1]["d"] == today:
            arr[-1]["u"] = it["used"]
        else:
            arr.append({"d": today, "u": it["used"]})
        hist[k] = arr[-90:]
    try:
        with open(os.path.join(DATA_DIR, "usage_history.json"), "w") as f:
            json.dump(hist, f)
    except Exception:
        pass
    return hist


def eta_for(item, hist):
    """按历史增速推算触顶日期  (日期, 说明)。"""
    if item.get("bounded"):
        return None, "自动轮转,不会触顶"
    if item.get("source") == "manual":
        return None, "手动核对值 · 变动缓慢"
    arr = hist.get(item["key"], [])
    if len(arr) < 2:
        return None, "增速累积中(需2天采样)"
    try:
        d0 = datetime.strptime(arr[0]["d"], "%Y-%m-%d")
        d1 = datetime.strptime(arr[-1]["d"], "%Y-%m-%d")
    except Exception:
        return None, "增速累积中"
    days = (d1 - d0).days
    if days <= 0:
        return None, "增速累积中(需2天采样)"
    growth = (arr[-1]["u"] - arr[0]["u"]) / days
    if growth <= 0:
        return None, "近%d天无增长" % days
    remain = item["limit"] - item["used"]
    if remain <= 0:
        return None, "已超额度"
    eta_days = int(remain / growth)
    return (now_cn() + timedelta(days=eta_days)).strftime("%Y-%m-%d"), \
           "按近%d天增速 · 约%d天" % (days, eta_days)


def usage_block(prev, host):
    """本地项每次算;走网络的(Access/GitHub)30 分钟节流。返回 (列表, 取数时间)。"""
    out = []
    pu = {u.get("key"): u for u in (prev.get("usage") or [])}
    net_at = prev.get("usage_seats_at")
    stale = not (net_at and age_min(net_at) < 30)

    o = oci_usage()
    if o:
        out.append(o)

    tok = _read_secret("cf_r2d1_token")
    seats = pu.get("cf_access")
    gh = pu.get("github_backup")
    r2 = pu.get("r2")
    d1 = pu.get("d1")
    need_r2d1 = tok and (r2 is None or r2.get("source") != "auto" or d1 is None or d1.get("source") != "auto")
    # 过期要刷;某项从没自动取到过也必须刷(否则节流会一直挡住首次取数)
    if stale or seats is None or gh is None or need_r2d1:
        fresh_seats, fresh_gh = access_seats(), github_backup_usage()
        fresh_r2 = r2_usage(tok) if tok else None
        fresh_d1 = d1_usage(tok) if tok else None
        if any([fresh_seats, fresh_gh, fresh_r2, fresh_d1]):
            seats = fresh_seats or seats
            gh = fresh_gh or gh
            r2 = fresh_r2 or r2
            d1 = fresh_d1 or d1
            net_at = fmt(now_cn())
    if gh:
        out.append(gh)
    if seats:
        out.append(seats)

    if host.get("disk_used_b") and host.get("disk_total_b"):
        out.append({"key": "disk", "label": "主机磁盘", "used": host["disk_used_b"],
                    "limit": host["disk_total_b"], "unit": "bytes", "source": "auto",
                    "note": "OVH VPS-1 系统盘"})

    # R2/D1:优先自动值,拿不到才用人工兜底
    out.append(r2 or MANUAL_USAGE[0])
    out.append(d1 or MANUAL_USAGE[1])

    hist = record_usage_history(out)
    for it in out:
        it["eta_date"], it["eta_note"] = eta_for(it, hist)
        it.pop("series", None)
    return out, net_at


# ---------- 慢变量节流(1分钟采集下不浪费外部接口)----------
def age_min(ts):
    try:
        return (now_cn() - datetime.strptime(ts, "%Y-%m-%d %H:%M").replace(tzinfo=CN)).total_seconds() / 60
    except Exception:
        return 1e9


def fx_cached(prev):
    """汇率日更,缓存 6 小时。"""
    pf = prev.get("fx", {})
    if pf.get("aud_cny") and pf.get("at") and age_min(pf["at"]) < 360:
        return pf
    return fx_rates(prev)


def cert_cached(prev):
    """证书 90 天才换,缓存 60 分钟;剩余天数每次本地重算(不走网络)。"""
    pc = prev.get("ops", {}).get("cert", {})
    if pc.get("date") and pc.get("checked_at") and age_min(pc["checked_at"]) < 60:
        try:
            d = datetime.strptime(pc["date"], "%Y-%m-%d").replace(tzinfo=CN)
            pc = dict(pc)
            pc["days"] = (d.date() - now_cn().date()).days
            return pc
        except Exception:
            pass
    c = cert_earliest()
    c["checked_at"] = fmt(now_cn())
    return c


def externals_cached(prev):
    """外部状态页缓存 5 分钟。"""
    pe, pat = prev.get("externals"), prev.get("externals_at")
    if pe and pat and age_min(pat) < 5:
        return pe, pat
    return externals(), fmt(now_cn())


# ---------- 资产总览(按供应商:状态/成本/风险/健康)----------
def inventory(host, fx, costblk, usage, ext, backup, cert, ovh, ch):
    cny = fx.get("aud_cny")
    umap = {u.get("key"): u for u in (usage or [])}
    extmap = {e.get("name"): e for e in (ext or [])}

    def pctof(u):
        return (u["used"] / u["limit"] * 100) if (u and u.get("limit")) else None

    def vmonthly(keys):
        return round(sum(it["aud"] for it in costblk["items"]
                         if any(k.lower() in (it["name"] + it.get("note", "")).lower() for k in keys)), 2)

    def R(level, text):
        return {"level": level, "text": text}

    cards = []
    # —— OVH VPS-1 ——
    dp, mp = host.get("disk_pct"), host.get("mem_pct")
    r = []
    if dp is not None and dp >= 85:
        r.append(R("danger", "磁盘 %d%% 偏高 · 自愈会自动清理" % dp))
    elif dp is not None and dp >= 75:
        r.append(R("warn", "磁盘 %d%% 需留意" % dp))
    if mp is not None and mp >= 90:
        r.append(R("warn", "内存 %d%%" % mp))
    if ovh.get("days") is not None and ovh["days"] <= 7:
        r.append(R("warn", "续费仅剩 %d 天" % ovh["days"]))
    if not r:
        r = [R("ok", "无")]
    cost_ovh = "A$7/月" + (" 约 ¥%d" % round(7 * cny) if cny else "")
    if ovh.get("date"):
        cost_ovh += " · 下次 %s(%s天)" % (ovh["date"], ovh.get("days", "—"))
    cards.append({
        "key": "ovh", "name": "OVH VPS-1", "role": "云服务器 · 所有程序 + 自建数据库都在这台跑",
        "status": {"ok": True, "note": "在线 %s 天 · 负载 %s" % (host.get("uptime_days", "—"), host.get("load1", "—"))},
        "cost": cost_ovh, "risks": r,
        "health": [
            {"label": "内存", "value": ("%d%%" % mp) if mp is not None else "—"},
            {"label": "磁盘", "value": ("%d%%" % dp) if dp is not None else "—"},
            {"label": "容器重启", "value": str(ch.get("restarts", 0))},
            {"label": "负载", "value": host.get("load1", "—")},
        ]})
    # —— Cloudflare ——
    r2, d1, seats = umap.get("r2"), umap.get("d1"), umap.get("cf_access")
    cf_ok = extmap.get("Cloudflare", {}).get("ok")
    r = []
    for u, lab in ((r2, "R2"), (d1, "D1")):
        p = pctof(u)
        if p is None:
            continue
        if p >= 80:
            r.append(R("danger", "%s 用量 %.0f%%" % (lab, p)))
        elif p >= 40:
            r.append(R("warn", "%s 用量 %.0f%% 在涨" % (lab, p)))
    sp = pctof(seats)
    if sp is not None and sp >= 80:
        r.append(R("warn", "Access 席位 %.0f%%" % sp))
    if not r:
        r = [R("ok", "均在免费额度内")]
    h = []
    if r2:
        h.append({"label": "R2", "value": fmt_bytes(r2["used"]) + " / " + fmt_bytes(r2["limit"])})
    if d1:
        h.append({"label": "D1", "value": fmt_bytes(d1["used"]) + " / " + fmt_bytes(d1["limit"])})
    if seats:
        h.append({"label": "Access 席位", "value": "%s / %s" % (seats["used"], seats["limit"])})
    h.append({"label": "DNS/边缘", "value": "正常" if cf_ok else "查不到"})
    cf_m = vmonthly(["域名", "cloudflare", "cf ", "r2", "d1"])
    cards.append({
        "key": "cf", "name": "Cloudflare", "role": "门口 · 域名解析/加速/防护/门禁 + R2 文件仓 + D1 小库",
        "status": {"ok": cf_ok, "note": "官方状态正常" if cf_ok else "官方状态查不到"},
        "cost": "域名 US$15/年 · 其余免费" + ((" · 月摊 A$%.2f" % cf_m) if cf_m > 0 else ""),
        "risks": r, "health": h})
    # —— GitHub ——
    gh, gh_ok = umap.get("github_backup"), extmap.get("GitHub", {}).get("ok")
    r = []
    p = pctof(gh)
    if p is not None and p >= 80:
        r.append(R("warn", "备份份数 %.0f%%(满自动删最旧)" % p))
    r.append(R("ok", "Actions 分钟未监控 · 目前免费额度充裕"))
    h = []
    if gh:
        h.append({"label": "备份份数", "value": "%s / %s" % (gh["used"], gh["limit"])})
    h.append({"label": "最新备份", "value": backup.get("at") or "—"})
    h.append({"label": "官方状态", "value": "正常" if gh_ok else "查不到"})
    cards.append({
        "key": "github", "name": "GitHub", "role": "代码仓库 + 每日加密备份的落地点",
        "status": {"ok": gh_ok, "note": "官方状态正常" if gh_ok else "官方状态查不到"},
        "cost": "免费额度内 · A$0", "risks": r, "health": h})
    # —— OCI ——
    oci = umap.get("oci_backup")
    r = []
    p = pctof(oci)
    if p is not None and p >= 70:
        r.append(R("warn", "只写不可删 · 累计 %.0f%%" % p))
    if not r:
        r = [R("ok", "仅每周日写入 · 余量充足")]
    h = []
    if oci:
        h.append({"label": "累计上传", "value": fmt_bytes(oci["used"]) + " / " + fmt_bytes(oci["limit"])})
    h.append({"label": "角色", "value": "备份的备份"})
    cards.append({
        "key": "oci", "name": "OCI(甲骨文云)", "role": "备份的备份 · 每周日再抄一份异地副本",
        "status": {"ok": True, "note": "离机副本 · 只写保险柜"},
        "cost": "免费额度内 · A$0", "risks": r, "health": h})
    return cards


# ---------- 运维自动修复(两套自愈:主自愈保业务;元自愈=自愈的自愈,保采集/自愈本身)----------
def selfheal_state(ch, cert, backup, seats):
    sh = load_json(os.path.join(DATA_DIR, "selfheal.json"), {})
    main_rules, meta_rules = [], []
    # —— 主自愈:内置 4 条 ——
    cov = ch.get("policy_covered", 0)
    eph = ch.get("policy_ephemeral", 0)
    persistent = ch.get("policy_total", 0) - eph          # 常驻容器数(排除临时构建容器)
    armed = persistent > 0 and cov >= persistent
    detail = "%d/%d 常驻容器已配置崩溃自愈" % (cov, persistent)
    if eph:
        detail += " · %d 个临时容器无需" % eph
    main_rules.append({"key": "restart", "name": "容器崩溃自动拉起", "engine": "builtin", "set": "main",
                       "armed": armed, "threshold": "Docker restart 策略 always/unless-stopped",
                       "state": "ok" if armed else "warn", "detail": detail,
                       "actions_total": ch.get("restarts", 0), "last_action": None, "last_action_at": None})
    cd = cert.get("days")
    main_rules.append({"key": "cert", "name": "TLS 证书自动续期", "engine": "builtin", "set": "main", "armed": True,
                       "threshold": "Traefik 到期前自动续(Let's Encrypt)",
                       "state": "ok" if (cd is None or cd > 7) else "warn",
                       "detail": ("最早证书 %s 到期 · 剩 %s 天" % (cert.get("date", "—"), cd)) if cd is not None else "自动续期",
                       "actions_total": 0, "last_action": None, "last_action_at": None})
    su = seats.get("used") if seats else None
    main_rules.append({"key": "seatfuse", "name": "Access 席位自动熔断", "engine": "builtin", "set": "main", "armed": True,
                       "threshold": "席位满 45 自动降级(cron 每 30 分钟)", "state": "ok",
                       "detail": ("当前 %s/%s 席位" % (su, seats.get("limit"))) if seats else "巡检中",
                       "actions_total": 0, "last_action": None, "last_action_at": None})
    main_rules.append({"key": "backup", "name": "每日异地备份", "engine": "builtin", "set": "main", "armed": bool(backup.get("ok")),
                       "threshold": "每日打包加密 → GitHub(自动轮转 30 份)",
                       "state": "ok" if backup.get("ok") else "warn",
                       "detail": ("上次备份 %s" % backup.get("at")) if backup.get("at") else "尚无备份",
                       "actions_total": 0, "last_action": None, "last_action_at": None})
    # —— 自愈脚本产出的规则(disk/watchdog=主;collector_watch=元)——
    script_rules = sh.get("rules")
    if script_rules:
        for r in script_rules:
            (meta_rules if r.get("set") == "meta" else main_rules).append(r)
        last_run, engine_note, recent = sh.get("last_run"), sh.get("engine"), sh.get("recent", [])
    else:
        for k, n in (("disk", "磁盘守护"), ("watchdog", "服务看门狗")):
            main_rules.append({"key": k, "name": n, "engine": "selfheal", "set": "main", "armed": False,
                               "threshold": "—", "state": "pending", "detail": "自愈脚本待部署/未运行",
                               "actions_total": 0, "last_action": None, "last_action_at": None})
        last_run, engine_note, recent = None, "服务器 cron · 不依赖 agent/token", []

    # —— 元自愈(自愈的自愈):监测自愈引擎与采集器自身是否还活着 ——
    sh_ep = sh.get("last_run_epoch")
    sh_age = int((time.time() - sh_ep) / 60) if sh_ep else None
    sh_alive = sh_age is not None and sh_age <= 15               # cron 每5分钟,>15分钟算掉线
    meta_rules.append({"key": "selfheal_alive", "name": "自愈引擎存活监测", "engine": "builtin", "set": "meta",
                       "armed": sh_alive, "threshold": "自愈引擎 >15 分钟没心跳即判定失效(它挂了谁来救)",
                       "state": "ok" if sh_alive else "warn",
                       "detail": ("自愈引擎 %s 分钟前刚跑过 · 心跳正常" % sh_age) if sh_alive
                                 else ("自愈引擎已 %s 分钟无心跳,请查 /etc/cron.d/linze-selfheal" % (sh_age if sh_age is not None else "?")),
                       "actions_total": 0, "last_action": None, "last_action_at": None})
    gp = load_json(os.path.join(DATA_DIR, "github_public.json"), None)
    g_ep = gp.get("collected_epoch") if isinstance(gp, dict) else None
    g_age = int((time.time() - g_ep) / 60) if g_ep else None
    g_alive = g_age is not None and g_age <= 10                  # cron 每1分钟,>10分钟算掉线
    meta_rules.append({"key": "github_alive", "name": "GitHub 采集存活监测", "engine": "builtin", "set": "meta",
                       "armed": bool(g_alive), "threshold": "GitHub 采集 >10 分钟没更新即判定失效",
                       "state": "ok" if g_alive else ("warn" if g_ep else "pending"),
                       "detail": ("GitHub 采集 %s 分钟前更新 · 正常" % g_age) if g_alive
                                 else ("GitHub 采集已 %s 分钟无更新" % g_age if g_ep else "GitHub 采集尚未产出首份"),
                       "actions_total": 0, "last_action": None, "last_action_at": None})

    rules = main_rules + meta_rules
    return {"last_run": last_run, "engine": engine_note, "recent": recent,
            "armed": sum(1 for x in rules if x.get("armed")), "total": len(rules),
            "main_armed": sum(1 for x in main_rules if x.get("armed")), "main_total": len(main_rules),
            "meta_armed": sum(1 for x in meta_rules if x.get("armed")), "meta_total": len(meta_rules),
            "rules": rules}


# ---------- 服务器贡献网格(每日部署次数,365 天)----------
def deploy_calendar():
    """从 Coolify 库取每日部署数,**累积存档**到 deploy_calendar.json。
    Coolify 队列表会被清理,存档只增不减,所以网格能长期保留。"""
    path = os.path.join(DATA_DIR, "deploy_calendar.json")
    store = load_json(path, {}) or {}
    rows = psql("select to_char((created_at + interval '8 hours')::date,'YYYY-MM-DD'), count(*) "
                "from application_deployment_queues "
                "where created_at > now()-interval '365 days' group by 1;")
    for line in rows.splitlines():
        if "|" not in line:
            continue
        d, c = line.split("|", 1)
        if c.strip().isdigit():
            store[d.strip()] = max(int(c), store.get(d.strip(), 0))
    cutoff = (now_cn() - timedelta(days=400)).strftime("%Y-%m-%d")
    store = {k: v for k, v in store.items() if k >= cutoff}
    try:
        with open(path, "w") as f:
            json.dump(store, f)
    except Exception:
        pass
    # 输出成与 GitHub 网格一致的结构(最近 365 天,缺失日补 0)
    today = now_cn().date()
    days = []
    for i in range(364, -1, -1):
        d = (today - timedelta(days=i)).strftime("%Y-%m-%d")
        days.append({"d": d, "c": store.get(d, 0)})
    vals = [x["c"] for x in days]
    first = min(store) if store else None
    return {"total": sum(vals), "days": days, "max": max(vals) if vals else 0,
            "since": first, "label": "每日部署次数"}


# ---------- GitHub Engineering Plane(读 github 采集器产出的公开安全聚合)----------
def github_public_block():
    gp = load_json(os.path.join(DATA_DIR, "github_public.json"), None)
    if not isinstance(gp, dict):
        return {"available": False, "note": "GitHub 采集尚未产出(每 1 分钟一次)"}
    gp["available"] = True
    ep = gp.get("collected_epoch")
    if ep:
        gp["stale_min"] = int((time.time() - ep) / 60)
    return gp


def main():
    os.makedirs(DATA_DIR, exist_ok=True)
    prev = load_json(os.path.join(DATA_DIR, "snapshot.json"), {})
    prices = load_json(os.path.join(DATA_DIR, "prices.json"),
                       {"items": []})

    host = host_metrics()
    fx = fx_cached(prev)
    cert = cert_cached(prev)
    ext, ext_at = externals_cached(prev)

    # 内存/磁盘历史:分层留存(1分钟粒度留 24h;小时粒度留 31 天)供多时段趋势
    hist = load_json(os.path.join(DATA_DIR, "history.json"), {})
    for tier in ("min", "hour", "day"):
        hist.setdefault(tier, {"t": [], "mem": [], "disk": []})
    ep = int(time.time())
    mem_v, disk_v = host.get("mem_pct"), host.get("disk_pct")
    m = hist["min"]
    m["t"].append(ep); m["mem"].append(mem_v); m["disk"].append(disk_v)
    for k in ("t", "mem", "disk"):
        m[k] = m[k][-1440:]                      # 24h @ 1min
    h = hist["hour"]
    cur_hour = ep - (ep % 3600)
    if not h["t"] or h["t"][-1] != cur_hour:
        h["t"].append(cur_hour); h["mem"].append(mem_v); h["disk"].append(disk_v)
    else:
        h["mem"][-1] = mem_v; h["disk"][-1] = disk_v
    for k in ("t", "mem", "disk"):
        h[k] = h[k][-744:]                       # 31 天 @ 1hour
    d = hist["day"]
    cur_day = ep - (ep % 86400)
    if not d["t"] or d["t"][-1] != cur_day:
        d["t"].append(cur_day); d["mem"].append(mem_v); d["disk"].append(disk_v)
    else:
        d["mem"][-1] = mem_v; d["disk"][-1] = disk_v
    # 天级别**不设上限**:一天一个点,永久保留(无期限)
    with open(os.path.join(DATA_DIR, "history.json"), "w") as f:
        json.dump(hist, f)

    projects, online = projects_live()
    dep = deploy_stats()
    usage, usage_seats_at = usage_block(prev, host)
    ovh_date, ovh_days = renew_days("2026-07-17", "monthly")
    ovh_renew = {"date": ovh_date, "days": ovh_days}
    ch = container_health()
    backup = backup_status()
    costblk = cost(prices, fx)
    seats = next((u for u in usage if u.get("key") == "cf_access"), None)

    snap = {
        "updated_at": fmt(now_cn()),
        "updated_epoch": int(time.time()),
        "tz": "北京时间",
        "summary": {
            "services_online": f"{online}/{len(PROJECTS)}",
            "deploy_rate": dep["rate"],
            "deploy_success": dep["success"],
            "deploy_total": dep["total"],
            "uptime_days": host["uptime_days"],
        },
        "ops": {
            "backup": backup,
            "cert": cert,
            "ovh_renew": ovh_renew,
            "restarts": ch["restarts"],
        },
        "host": host,
        "fx": fx,
        "cost": costblk,
        "projects": projects,
        "deploy": dep,
        "history": hist,
        "externals": ext,
        "externals_at": ext_at,
        "usage": usage,
        "usage_seats_at": usage_seats_at,
        "inventory": inventory(host, fx, costblk, usage, ext, backup, cert, ovh_renew, ch),
        "selfheal": selfheal_state(ch, cert, backup, seats),
        "github": github_public_block(),
        "deploy_calendar": deploy_calendar(),
    }

    tmp = os.path.join(DATA_DIR, "snapshot.json.tmp")
    with open(tmp, "w") as f:
        json.dump(snap, f, ensure_ascii=False, indent=1)
    os.replace(tmp, os.path.join(DATA_DIR, "snapshot.json"))
    print("snapshot written:", snap["updated_at"], "online", online, "rate", dep["rate"])


if __name__ == "__main__":
    main()
