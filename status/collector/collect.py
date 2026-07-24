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
PROJECTS = [
    {"name": "Home",     "url": "https://home.linzezhang.com",     "parts": ["前台"],       "db": "",                    "notify": "无"},
    {"name": "Nab",      "url": "https://nab.linzezhang.com",      "parts": ["前台"],       "db": "",                    "notify": "无"},
    {"name": "PFI",      "url": "https://pfi.linzezhang.com",      "parts": ["前台"],       "db": "",                    "notify": "无"},
    {"name": "Serenity", "url": "https://serenity.linzezhang.com", "parts": ["前台"],       "db": "",                    "notify": "无"},
    {"name": "KMFA",     "url": "https://kmfa.linzezhang.com",     "parts": ["前台", "后台"], "db": "OVH 自建·SQLite",     "notify": "钉钉"},
    {"name": "Account",  "url": "https://account.linzezhang.com",  "parts": ["后台"],       "db": "OVH 自建·Postgres",   "notify": "邮件"},
    {"name": "EEI",      "url": "",                                "parts": ["后台"],       "db": "OVH 自建·Postgres",   "notify": "无"},
    {"name": "Status",   "url": "https://status.linzezhang.com",   "parts": ["前台"],       "db": "OVH 自建·SQLite",     "notify": "无"},
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


def container_restarts():
    names = run("docker ps --format '{{.Names}}'").splitlines()
    mx = 0
    for n in names:
        r = run(f"docker inspect -f '{{{{.RestartCount}}}}' {n}")
        if r.isdigit():
            mx = max(mx, int(r))
    return mx


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
    """purchase 'YYYY-MM-DD';cadence 'monthly'|'yearly' → (下次日期, 剩余天)。"""
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
                cash_aud = base_aud                    # 年付:本月正好是周年月 → 本月扣年费
        month_cash_aud += cash_aud

        row = {
            "name": it.get("name", ""), "note": it.get("note", ""),
            "cadence": cadence, "currency": cur, "amount": round(amt, 2),
            "purchase": purchase,
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
     "note": "adp-raw-artifacts · 需 R2 读令牌才能自动刷新"},
    {"key": "d1", "label": "Cloudflare D1 存储", "used": 53784576, "limit": 5 * GB,
     "unit": "bytes", "source": "manual", "checked": "2026-07-24",
     "note": "eei-publication + adp-mirror · 需 D1 读令牌才能自动刷新"},
]


def _read_secret(name):
    try:
        with open(os.path.join(SECRETS_DIR, name)) as f:
            return f.read().strip()
    except Exception:
        return None


def oci_usage():
    """离机备份 PAR 只写不可删 → 累计上传量 ≈ 远端占用(按日志,属下限估算)。"""
    total = last = 0
    try:
        with open(OFFSITE_LOG) as f:
            for line in f:
                m = re.search(r"offsite=200 size=(\d+)B", line)
                if m:
                    last = int(m.group(1))
                    total += last
    except Exception:
        return None
    limit = 20 * GB
    return {"key": "oci_backup", "label": "OCI 离机备份", "used": total, "limit": limit,
            "unit": "bytes", "source": "auto",
            "eta_days": int((limit - total) / last) if last > 0 else None,
            "note": "每日上传且远端不可删,只增不减"}


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
                "unit": "count", "source": "auto", "note": "≥45 席位自动熔断保护"}
    except Exception:
        return None


def usage_block(prev, host):
    """本地项每次算;Access 走网络 → 30 分钟节流。返回 (列表, 席位取数时间)。"""
    out = []
    o = oci_usage()
    if o:
        out.append(o)

    pu = {u.get("key"): u for u in (prev.get("usage") or [])}
    seats, seats_at = pu.get("cf_access"), prev.get("usage_seats_at")
    if not (seats and seats_at and age_min(seats_at) < 30):
        fresh = access_seats()
        if fresh:
            seats, seats_at = fresh, fmt(now_cn())
    if seats:
        out.append(seats)

    if host.get("disk_used_b") and host.get("disk_total_b"):
        out.append({"key": "disk", "label": "主机磁盘", "used": host["disk_used_b"],
                    "limit": host["disk_total_b"], "unit": "bytes", "source": "auto",
                    "note": "OVH VPS-1 系统盘"})

    out.extend(MANUAL_USAGE)
    return out, seats_at


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


def main():
    os.makedirs(DATA_DIR, exist_ok=True)
    prev = load_json(os.path.join(DATA_DIR, "snapshot.json"), {})
    prices = load_json(os.path.join(DATA_DIR, "prices.json"),
                       {"items": []})

    host = host_metrics()
    fx = fx_cached(prev)
    cert = cert_cached(prev)
    ext, ext_at = externals_cached(prev)

    # 内存/磁盘历史(滚动)
    hist = load_json(os.path.join(DATA_DIR, "history.json"), {"mem": [], "disk": [], "at": []})
    hist["at"].append(now_cn().strftime("%H:%M"))
    hist["mem"].append(host["mem_pct"])
    hist["disk"].append(host["disk_pct"])
    for k in ("at", "mem", "disk"):
        hist[k] = hist[k][-HISTORY_MAX:]
    with open(os.path.join(DATA_DIR, "history.json"), "w") as f:
        json.dump(hist, f)

    projects, online = projects_live()
    dep = deploy_stats()
    usage, usage_seats_at = usage_block(prev, host)
    ovh_date, ovh_days = renew_days("2026-07-17", "monthly")

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
            "backup": backup_status(),
            "cert": cert,
            "ovh_renew": {"date": ovh_date, "days": ovh_days},
            "restarts": container_restarts(),
        },
        "host": host,
        "fx": fx,
        "cost": cost(prices, fx),
        "projects": projects,
        "deploy": dep,
        "history": hist,
        "externals": ext,
        "externals_at": ext_at,
        "usage": usage,
        "usage_seats_at": usage_seats_at,
    }

    tmp = os.path.join(DATA_DIR, "snapshot.json.tmp")
    with open(tmp, "w") as f:
        json.dump(snap, f, ensure_ascii=False, indent=1)
    os.replace(tmp, os.path.join(DATA_DIR, "snapshot.json"))
    print("snapshot written:", snap["updated_at"], "online", online, "rate", dep["rate"])


if __name__ == "__main__":
    main()
