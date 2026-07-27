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
import urllib.parse
import urllib.error
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
    {"name": "Home",     "url": "https://home.linzezhang.com",     "parts": ["前台"], "repo": "LinzeHomeHub",
     "host": "OVH VPS-1", "db": "无(纯静态前台)", "store": "无(构建产物在镜像内)", "deploy": "Golden Path 自动",
     "backup": "随主机镜像 + 源码在 GitHub", "agent": "低", "notify": "无", "owns": {"coolify": "linze-home-hub"}},
    {"name": "Nab",      "url": "https://nab.linzezhang.com",      "parts": ["前台"], "repo": "MetaDatabase",
     "host": "OVH VPS-1", "db": "无(纯静态前台)", "store": "无(构建产物在镜像内)", "deploy": "Golden Path 自动",
     "backup": "随主机镜像 + 源码在 GitHub", "agent": "低", "notify": "无", "owns": {"coolify": "nab"}},
    {"name": "PFI",      "url": "https://pfi.linzezhang.com",      "parts": ["前台"], "repo": "MetaDatabase",
     "host": "OVH VPS-1", "db": "无(纯静态前台)", "store": "无(构建产物在镜像内)", "deploy": "Golden Path 自动",
     "backup": "随主机镜像 + 源码在 GitHub", "agent": "低", "notify": "无", "owns": {"coolify": "pfi-public"}},
    {"name": "Serenity", "url": "https://serenity.linzezhang.com", "parts": ["前台"], "repo": "MetaDatabase",
     "host": "OVH VPS-1", "db": "无(纯静态前台)", "store": "无(构建产物在镜像内)", "deploy": "Golden Path 自动",
     "backup": "随主机镜像 + 源码在 GitHub", "agent": "低", "notify": "无", "owns": {"coolify": "serenity-public"}},
    {"name": "KMFA",     "url": "https://kmfa.linzezhang.com",     "parts": ["前台", "后台"], "repo": "KMOS",
     "host": "OVH VPS-1", "db": "无独立库·报告写文件", "store": "OVH 文件", "deploy": "Coolify + cron worker",
     "backup": "私有备份仓 + 随主机", "agent": "中", "notify": "钉钉", "owns": {"container": ["app-", "skills-"], "coolify": "kmfa-kmos-p1"}},
    {"name": "Account",  "url": "https://account.linzezhang.com",  "parts": ["后台"],
     "host": "OVH VPS-1", "db": "OVH Postgres · identity-postgres", "store": "Postgres", "deploy": "Coolify compose",
     "backup": "身份库 cron 03:37 + 随主机", "agent": "低", "notify": "邮件", "owns": {"container": ["identity-"]}},
    {"name": "EEI",      "url": "https://eei.linzezhang.com",      "parts": ["前台", "后台"], "repo": "MetaDatabase",
     "host": "OVH VPS-1", "db": "OVH Postgres · eei-db  +  CF D1 · eei-publication", "store": "Postgres + CF D1",
     "deploy": "Coolify compose", "backup": "随主机 + CF", "agent": "中", "notify": "无(内部服务)", "owns": {"container": ["eei-"]}},
    {"name": "Alpha",    "url": "https://alpha.linzezhang.com",    "parts": ["前台", "后台"], "repo": "MetaDatabase",
     "host": "OVH VPS-1", "db": "OVH 文件 · 交易账本 sqlite", "store": "OVH 文件",
     "deploy": "host-direct systemd ×5", "backup": "随主机 + 账本邮件归档", "agent": "低", "notify": "邮件", "owns": {"systemd": ["alpha-"]}},
    {"name": "ADP",      "url": "https://adp.linzezhang.com",      "parts": ["前台", "后台"], "repo": "MetaDatabase",
     "host": "Cloudflare Workers", "db": "CF D1 · adp", "store": "CF D1 + R2",
     "deploy": "wrangler", "backup": "随 CF", "agent": "低", "notify": "邮件", "owns": {"cloudflare": ["adp"]}},
    {"name": "Uptime",   "url": "https://uptime.linzezhang.com",   "parts": ["前台"],
     "host": "OVH VPS-1", "db": "无(探活服务)", "store": "SQLite 探测历史", "deploy": "Coolify compose",
     "backup": "随主机", "agent": "无", "notify": "无", "owns": {"container": ["monitoring-gatus"]}},
    {"name": "Status",   "url": "https://status.linzezhang.com",   "parts": ["前台"], "repo": "LinzeHomeHub",
     "host": "OVH VPS-1", "db": "OVH 文件 · prices.json", "store": "OVH 文件", "deploy": "host-direct rsync",
     "backup": "每日加密 → GitHub", "agent": "无(纯 cron)", "notify": "无", "owns": {"container": ["linze-status"], "cron": ["linze-status", "linze-github", "linze-selfheal"]}},
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
    load3 = run("awk '{print $1\" \"$2\" \"$3}' /proc/loadavg").split()
    dbytes = run("df -B1 / | awk 'NR==2{print $3\" \"$2}'").split()
    used_b = int(dbytes[0]) if len(dbytes) == 2 and dbytes[0].isdigit() else None
    total_b = int(dbytes[1]) if len(dbytes) == 2 and dbytes[1].isdigit() else None
    # 容量判定要用到的原始量:光看百分比会误判。
    # 例:swap 用了一半但 available 还很宽裕 —— Linux 不会主动把 swap 换回内存,
    # 那是**历史峰值的遗留**,不代表此刻缺内存。两个数必须一起看。
    f = run("free -m | awk '/Mem:/{print $2\" \"$3\" \"$7} /Swap:/{print $2\" \"$3}'").split()
    g = (lambda i: int(f[i]) if i < len(f) and f[i].isdigit() else None)
    cores = run("nproc")
    return {
        "mem_pct": int(mem) if mem.isdigit() else None,
        "disk_pct": int(disk) if disk.isdigit() else None,
        "disk_used_b": used_b,
        "disk_total_b": total_b,
        "uptime_days": int(up_days) if up_days.isdigit() else None,
        "load1": load,
        "load5": load3[1] if len(load3) > 2 else None,
        "load15": load3[2] if len(load3) > 2 else None,
        "cores": int(cores) if cores.isdigit() else None,
        "mem_total_mb": g(0), "mem_used_mb": g(1), "mem_avail_mb": g(2),
        "swap_total_mb": g(3), "swap_used_mb": g(4),
    }


def docker_space():
    """Docker 占了多少、其中多少是**能立刻回收**的。

    这个数是容量判断的关键:磁盘 67% 里如果有 13% 是构建缓存,
    那么「该不该升级」的答案在回收之前根本无法成立 —— 先回收,再谈钱。
    """
    out = {"total_b": 0, "reclaim_b": 0, "rows": []}
    raw = run("docker system df --format '{{.Type}}|{{.Size}}|{{.Reclaimable}}'", timeout=40)

    def parse(s):
        m = re.match(r"([\d.]+)\s*([KMGT]?i?B)", (s or "").strip())
        if not m:
            return 0
        mul = {"B": 1, "KB": 1e3, "MB": 1e6, "GB": 1e9, "TB": 1e12,
               "KiB": 1024, "MiB": 1024 ** 2, "GiB": 1024 ** 3, "TiB": 1024 ** 4}
        return int(float(m.group(1)) * mul.get(m.group(2), 1))

    for line in raw.splitlines():
        p = line.split("|")
        if len(p) < 3:
            continue
        size, recl = parse(p[1]), parse(p[2].split("(")[0])
        out["rows"].append({"type": p[0], "size_b": size, "reclaim_b": recl})
        out["total_b"] += size
        out["reclaim_b"] += recl
    return out


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
            log.append({"at": p[0], "app": p[1], "ok": p[2].strip() == "finished",
                        "status": p[2].strip()})
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


# ---------- 业务流:软件**内部**功能实现程度监控 ----------
# 统一的是**接口**,不是内容:阶段有几段、叫什么、什么顺序,由每个项目自己定
# (KMFA 六段「源接入→解析→计算→校验→输出→投递」,Alpha 是交易执行八段,各画各的矩阵)。
# 统一的只有:schema 字段名、探针类型库、状态语义、呈现语言。
#
# ★★ 安全前提:`flow.yaml` 来自代码仓,对这台主机而言是**不可信输入**。
#    因此探针一律「按类型由采集器自行构造命令」,**绝不执行 YAML 里的自由字符串**——
#    不接受自由 SQL、不接受自由 shell、路径必须落在允许的根下、单元名/容器名必须过白名单正则。
#    否则任何能改仓库文件的人就等于拿到了这台机器的 shell。
FLOW_ROOTS = ("/srv/linze", "/etc/cron.d", "/var/log")
SAFE_NAME = re.compile(r"^[A-Za-z0-9._@:-]+$")
SAFE_COL = re.compile(r"^[A-Za-z0-9_]+$")
# ★ 五态,沿用 KMFA 线程的词汇(healthy/degraded/not_built),只把它的 blocked 拆成两态。
#   拆分理由是实测出来的:那 6 个 blocked 里混着性质完全相反的两种东西 ——
#     blocked_by_policy = 按规定就不该通(测试期禁群),**不需要任何人做事**
#     blocked_by_input  = 缺输入才算不出来,**必须催人**
#   处置动作是反的,合成一态总览就排不出优先级。
#   `blocked` 是尚未细分的过渡值,如实显示为「阻断(未细分)」,不替被测方猜。
FLOW_STATES = ("healthy", "degraded", "blocked", "blocked_by_policy", "blocked_by_input",
               "not_built", "unknown")
FLOW_BAD = ("blocked", "blocked_by_input", "not_built")     # 计数意义上的「坏」
# ★ 阻断下游 ≠ 需要处置。KMFA 的耦合规则明确把 blocked_by_policy 也算作阻断上游:
#   按规定不通的东西,下游同样拿不到数,不该自称健康 —— 但它不需要任何人去修。
#   两种语义必须分开,否则要么漏判耦合、要么把「不用管」排进待办。
FLOW_BLOCKS_DOWNSTREAM = ("blocked", "blocked_by_policy", "blocked_by_input", "not_built")
_SEV = {"blocked_by_input": 0, "blocked": 1, "degraded": 2, "not_built": 3,
        "unknown": 4, "blocked_by_policy": 5, "healthy": 6}
# 旧词汇兼容(第一版用的是 ok/warn/bad/not_implemented)
_ALIAS = {"ok": "healthy", "warn": "degraded", "bad": "blocked",
          "not_implemented": "not_built"}


def _safe_path(p):
    """路径必须是绝对路径、无 .. 、且落在允许的根下。"""
    if not isinstance(p, str) or not p.startswith("/") or ".." in p:
        return None
    rp = os.path.normpath(p)
    return rp if any(rp == r or rp.startswith(r + "/") for r in FLOW_ROOTS) else None


def _age_h(path):
    try:
        return (time.time() - os.path.getmtime(path)) / 3600.0
    except OSError:
        return None


def _pr_file_fresh(a):
    p = _safe_path(a.get("path") or "")
    if not p:
        return "unknown", "路径不在允许范围内,拒绝探测"
    if not os.path.exists(p):
        return "blocked", "产物不存在:%s" % p
    age, mx = _age_h(p), float(a.get("max_age_h") or 26)
    return (("healthy" if age <= mx else "degraded"),
            "%s · %.1f 小时前更新(阈值 %.0fh)" % (os.path.basename(p), age, mx))


# ---------- 只读 HTTP 事实源(被测方自己暴露的健康摘要) ----------
# ★ 背景:KMFA 那边四条取证路全断(Coolify exec 不支持、logs 为空、健康接口在 Access
#   后面、私有归档从未成功过),所以改由被测方在**公开命名空间**暴露一个只读健康摘要,
#   本站去读。零新增凭据 —— 这是它选 HTTP 端点而不是落静态文件的理由:
#   静态文件会过期,而**过期的绿比没有绿更糟**。
#
# ★★ 安全:URL 与 json_path 都来自仓库文件,对这台主机是**不可信输入**。
#    ① 主机名限本 estate。否则任何能改仓库文件的人,就能把采集器变成对外发请求的信标。
#    ② json_path **绝不上表达式引擎**(JMESPath / eval / 任何求值器)。只认下面这一种
#       受限形状,自己解析、显式遍历。多一分表达能力,就多一片攻击面。
_HOST_OK = re.compile(r"^https://[a-z0-9-]+(\.[a-z0-9-]+)*\.linzezhang\.com(/[^\s?#]*)?$")
# 形状:  数组字段[?键=='字面量'].取值字段   |   a.b.c
_JP_FILTER = re.compile(r"^([^\[\].]+)\[\?([^\[\]=']+)=='([^']{1,64})'\]\.([^\[\].]+)$")
_JP_PLAIN = re.compile(r"^[^\[\]?'\"]+(\.[^\[\]?'\"]+)*$")


def _redirect_ok(newurl, host):
    """跳转不得跨主机,也不得降级到 http —— 否则主机名白名单形同虚设:
    被测方(或任何能改它那个端点的人)只要回一个 302,就能把采集器牵到任意地址去。"""
    sp = urllib.parse.urlsplit(newurl or "")
    return sp.scheme == "https" and sp.netloc == host


def _fetch_json(url, cap=262144, timeout=8):
    """只读取本 estate 的 https JSON。返回 (doc, 错误说明)。"""
    if not isinstance(url, str) or not _HOST_OK.match(url) or ".." in url:
        return None, "URL 非法或不在本 estate,拒绝探测"
    # 路径里可能有中文(KMFA 的端点就是 /public-api/技能健康),自己 percent-encode
    sp = urllib.parse.urlsplit(url)
    safe = urllib.parse.urlunsplit((sp.scheme, sp.netloc,
                                    urllib.parse.quote(sp.path, safe="/"), "", ""))
    host = sp.netloc

    class _Redir(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, hdrs, newurl):
            if not _redirect_ok(newurl, host):
                return None
            return super().redirect_request(req, fp, code, msg, hdrs, newurl)

    try:
        op = urllib.request.build_opener(_Redir)
        with op.open(urllib.request.Request(safe, headers={"Accept": "application/json"}),
                     timeout=timeout) as r:
            return json.loads(r.read(cap).decode("utf-8", "replace")), None
    except urllib.error.HTTPError as e:
        return None, "端点返回 HTTP %s" % e.code
    except (urllib.error.URLError, socket.timeout, ValueError, json.JSONDecodeError) as e:
        return None, "端点不可达或不是 JSON(%s)" % type(e).__name__


_FETCH = _fetch_json          # 测试替换点(见上方注释)


def _json_pick(doc, path):
    """受限取值。**只**支持两种形状,其余一律拒绝(不猜、不降级成模糊匹配):
         技能[?技能=='upstream-archive'].运行次数
         a.b.c
    返回 (值, 错误说明)。"""
    if not isinstance(path, str) or len(path) > 200:
        return None, "json_path 非法"
    m = _JP_FILTER.match(path)
    if m:
        arr_k, key, lit, val_k = m.groups()
        node = doc
        for part in arr_k.split("."):
            if not isinstance(node, dict):
                return None, "json_path 中 %s 之前不是对象" % part
            node = node.get(part)
        if not isinstance(node, list):
            return None, "%s 不是数组" % arr_k
        for it in node:
            if isinstance(it, dict) and str(it.get(key)) == lit:
                return it.get(val_k), None
        return None, "数组里没有 %s=='%s' 的条目" % (key, lit)
    if _JP_PLAIN.match(path):
        node = doc
        for part in path.split("."):
            if not isinstance(node, dict):
                return None, "json_path 中 %s 之前不是对象" % part
            node = node.get(part)
        return node, None
    return None, "json_path 形状不支持(只认 `数组[?键=='值'].字段` 与 `a.b.c`)"


def _http_value(a):
    """按 http+json_path 取一个值。**取不到一律算坏消息**,不算 unknown ——
    「端点活着但问不出我要的东西」本身就是一个坏消息,不能拿「没有坏消息」当好消息。"""
    # 走模块级间接引用,测试可以替换掉真实网络调用。
    # ★ 刻意**不**做成 args 里的注入点(比如 `_stub` 键):args 来自 flow.yaml,
    #   那等于给不可信输入开了一个「自己声明探测结果」的后门。
    doc, err = _FETCH(a.get("http") or "")
    if err:
        return None, err
    val, perr = _json_pick(doc, a.get("json_path") or "")
    if perr:
        # 把顶层的标量字段带出来:被测方用 `台账可读: false` + 原因说明问题时,
        # 原因就在这里如实显示,不需要本站硬编码它的字段名
        top = "; ".join("%s=%s" % (k, v) for k, v in (doc or {}).items()
                        if isinstance(v, (str, int, float, bool)))[:180]
        return None, perr + (" · 端点自述:%s" % top if top else "")
    return val, None


def _parse_ts(raw):
    """解析时间戳,**认显式时区偏移**。

    ★ KMFA 线程提醒后实测确认的错:原来是 `raw[:19]` 截断 + 「带 T 就按 UTC」。
      它的 `2026-07-27T08:00:00+08:00` 会被切成 `2026-07-27T08:00:00` 再当 UTC ——
      正好差 8 小时,而且方向是让它显得**更旧**,会把刚跑完的技能报成超期。
      带偏移的按偏移算;没有偏移的才回落到北京时间(本站自己的产出都是北京时间)。
    """
    s = str(raw or "").strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(s)                    # 直接吃 ISO 8601(含偏移)
        return dt if dt.tzinfo else dt.replace(tzinfo=CN)
    except ValueError:
        pass
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(s[:19], fmt).replace(tzinfo=CN)
        except ValueError:
            continue
    return None


def _pr_artifact_rows(a):
    """产出物条数 —— **这才是健康信号**。

    ★ KMFA 线程实测反馈:他们的上游归档 cron 长期只跑校验器、从没调用真归档程序,
      日志有输出、时间戳新鲜、退出码 0,**但一个文件都没取回来过**。
      「进程有没有动」和「有没有产出」是两回事;拿前者当健康,页面就会系统性说谎。
    """
    if a.get("http"):
        # 被测方自报的产出计数(它自己的台账)。★ 0 = **从未跑完过一次**,判阻断;
        #   取不到也判阻断 —— 不给「新接入的抖动」留任何平滑余地,第一轮探到什么就是什么。
        val, err = _http_value(a)
        if err:
            return "blocked", "取不到产出计数:%s" % err
        try:
            n = int(val)
        except (TypeError, ValueError):
            return "blocked", "产出计数不是数字(值=%r)" % (str(val)[:40],)
        lo = int(a.get("min") or 1)
        if n < lo:
            return "blocked", ("**产出计数 %d,要求 ≥%d** —— 从未跑完过一次;"
                               "日志再新鲜、退出码再正常都不算数" % (n, lo)) if n == 0 else \
                              "**产出计数 %d,要求 ≥%d**" % (n, lo)
        return "healthy", "产出计数 %d(要求 ≥%d) · 来自被测方公开健康摘要" % (n, lo)
    p = _safe_path(a.get("dir") or "")
    if not p or not os.path.isdir(p):
        return "unknown", "目录不可达,无法核验产出"
    suf = a.get("suffix") or ""
    if suf and not SAFE_NAME.match(suf.lstrip(".")):
        return "unknown", "后缀名非法"
    files = [f for f in os.listdir(p) if f.endswith(suf)]
    lo = int(a.get("min") or 1)
    if len(files) < lo:
        return "blocked", "**产出物 %d 个,要求 ≥%d** —— 进程可能在跑但没产出" % (len(files), lo)
    newest = max((_age_h(os.path.join(p, f)) or 1e9) for f in files) if files else None
    fresh = min((_age_h(os.path.join(p, f)) or 1e9) for f in files) if files else None
    mx = a.get("max_age_h")
    if mx and fresh is not None and fresh > float(mx):
        return "degraded", "%d 个产出,但最新的已 %.0f 小时未更新(阈值 %sh)" % (len(files), fresh, mx)
    return "healthy", "%d 个产出 · 最新 %.1f 小时前" % (len(files), fresh or 0)


def _pr_business_ts(a):
    """最新**业务**时间戳(不是文件 mtime)。

    文件可能天天被重写但里面的业务日期停在三个月前 —— mtime 看不出来这种停滞。
    只支持两种取法,SQL 仍由采集器构造:
      来源 db:  容器 + 库 + 表 + 时间列
      来源 file: JSON 文件里的某个顶层字段(字段名过白名单,不做任意路径求值)
    """
    mx = float(a.get("max_age_h") or 26)
    if a.get("http"):
        val, err = _http_value(a)
        if err:
            return "blocked", "取不到业务时间戳:%s" % err
        ts = _parse_ts(val)
        if ts is None:
            return "blocked", "业务时间戳解析不了(值=%r)" % (str(val)[:40],)
        age = (time.time() - ts.timestamp()) / 3600
        return (("healthy" if age <= max(0.1, mx) else "degraded"),
                "业务时间戳 %s · %.1f 小时前(阈值 %.0fh) · 来自被测方公开健康摘要"
                % (str(val)[:32], age, mx))
    if a.get("container"):
        st, ev = _pr_db_rows(dict(a, min_rows=int(a.get("min_rows") or 1)))
        return st, ev
    p = _safe_path(a.get("path") or "")
    fld = a.get("field") or ""
    if not p or not os.path.exists(p):
        return "blocked", "业务时间戳来源不存在"
    if not SAFE_COL.match(fld):
        return "unknown", "字段名非法"
    try:
        with open(p) as f:
            doc = json.load(f)
        raw = str((doc or {}).get(fld) or "")
        ts = _parse_ts(raw)          # 统一解析:认显式偏移,无偏移才当北京时间
        if ts is None:
            raise ValueError(raw)
    except (OSError, ValueError, json.JSONDecodeError):
        return "unknown", "取不到 %s 字段的时间戳(值=%r)" % (fld, str((doc or {}).get(fld))[:24] if isinstance(doc, dict) else "")
    age = (time.time() - ts.timestamp()) / 3600
    return (("healthy" if age <= max(0.1, mx) else "degraded"),
            "业务时间戳 %s · %.1f 小时前(阈值 %.0fh)" % (raw[:32], age, mx))


def _pr_glob_count(a):
    p = _safe_path(a.get("dir") or "")
    if not p or not os.path.isdir(p):
        return "unknown", "目录不可达"
    suf = a.get("suffix") or ""
    if suf and not SAFE_NAME.match(suf.lstrip(".")):
        return "unknown", "后缀名非法"
    n = len([f for f in os.listdir(p) if f.endswith(suf)])
    lo = int(a.get("min") or 1)
    return ("healthy" if n >= lo else "degraded"), "%d 个产物(要求 ≥%d)" % (n, lo)


def _pr_systemd(a):
    u = a.get("unit") or ""
    if not SAFE_NAME.match(u):
        return "unknown", "单元名非法"
    kv = dict(l.split("=", 1) for l in run(
        "systemctl show '%s' --property=ActiveState --property=Result --property=Type "
        "--property=TriggeredBy --property=ExecMainExitTimestamp 2>/dev/null" % u
    ).splitlines() if "=" in l)
    if not kv:
        return "unknown", "查不到该单元"
    st = _systemd_state({"Id": u, **kv})
    ts = kv.get("ExecMainExitTimestamp") or ""
    txt = {"active": "常驻运行中", "scheduled": "定时/事件触发 · 待命",
           "failed": "上次执行失败(%s)" % kv.get("Result"), "inactive": "未运行"}.get(st, st)
    return ({"active": "healthy", "scheduled": "healthy", "failed": "blocked"}.get(st, "degraded"),
            "%s%s" % (txt, (" · 上次结束 " + ts[:19]) if ts else ""))


def _pr_container(a):
    c = a.get("name") or ""
    if not SAFE_NAME.match(c):
        return "unknown", "容器名非法"
    out = run("docker inspect '%s' --format '{{.State.Status}}|{{if .State.Health}}"
              "{{.State.Health.Status}}{{else}}-{{end}}' 2>/dev/null" % c)
    if not out:
        return "blocked", "容器不存在"
    st, hl = (out.split("|") + ["-"])[:2]
    if st != "running":
        return "blocked", "容器状态 %s" % st
    return ("degraded" if hl == "starting" else "healthy"), "运行中%s" % ("" if hl == "-" else " · 健康 " + hl)


def _pr_http(a):
    u = a.get("url") or ""
    if not re.match(r"^https://[A-Za-z0-9.-]+(/[A-Za-z0-9._~/-]*)?$", u):
        return "unknown", "URL 不合法或非 https,拒绝探测"
    code = http_code(u)
    okset = [str(x) for x in (a.get("expect") or [200, 301, 302, 308])]
    return ("healthy" if code in okset else "blocked"), "HTTP %s(期望 %s)" % (code or "无响应", "/".join(okset))


def _pr_db_rows(a):
    """SQL **由采集器构造**,YAML 只能给容器/库/表/时间列名,且都过白名单正则。
    这样仓库文件里塞不进任何可执行语句。"""
    c, db, tb = a.get("container") or "", a.get("db") or "", a.get("table") or ""
    col = a.get("time_column") or ""
    if not (SAFE_NAME.match(c) and SAFE_COL.match(db) and SAFE_COL.match(tb)):
        return "unknown", "容器/库/表名非法,拒绝探测"
    if col and not SAFE_COL.match(col):
        return "unknown", "时间列名非法"
    sql = "select count(*) from %s" % tb
    if col:
        sql = ("select count(*), coalesce(max(%s)::text,'-') from %s" % (col, tb))
    out = run("docker exec %s psql -U %s -d %s -t -A -F'|' -c \"%s\" 2>/dev/null"
              % (c, db, db, sql))
    if not out:
        return "unknown", "库不可达或表不存在"
    parts = out.strip().split("|")
    n = int(parts[0]) if parts[0].strip().isdigit() else 0
    lo = int(a.get("min_rows") or 1)
    latest = parts[1] if len(parts) > 1 else ""
    ev = "%d 行" % n + (" · 最新 %s" % latest[:19] if latest and latest != "-" else "")
    if n < lo:
        return "degraded", ev + "(要求 ≥%d)" % lo
    if col and a.get("max_age_h") and latest and latest != "-":
        try:
            age = (time.time() - datetime.strptime(latest[:19], "%Y-%m-%d %H:%M:%S")
                   .replace(tzinfo=timezone.utc).timestamp()) / 3600
            if age > float(a["max_age_h"]):
                return "degraded", ev + " · 已 %.0f 小时没有新数据" % age
        except ValueError:
            pass
    return "healthy", ev


def _pr_log_recent(a):
    """日志尾部是否有近期的匹配行。pattern 只允许字面量,不允许正则元字符 —— 防 ReDoS 与注入。"""
    p = _safe_path(a.get("path") or "")
    pat = a.get("contains") or ""
    if not p or not os.path.exists(p):
        return "unknown", "日志不可达"
    if not re.match(r"^[\w一-龥 .:/=-]{1,60}$", pat or "x"):
        return "unknown", "匹配串含非法字符"
    age = _age_h(p)
    mx = float(a.get("max_age_h") or 26)
    if pat:
        hit = run("tail -n %d %s 2>/dev/null | grep -c -F -- %s"
                  % (int(a.get("tail") or 200), p, json.dumps(pat)))
        n = int(hit) if hit.isdigit() else 0
        if not n:
            return "degraded", "近 %s 行日志里没有「%s」" % (a.get("tail") or 200, pat)
    # ★ 日志新鲜度是**弱证据**,不得单独判 healthy ——
    #   实测过 cron 跑了、退出码 0、日志新鲜,但一个产出都没有的假绿。
    return (("degraded" if age is None or age > mx else "unknown"),
            "日志 %.1f 小时前有写入(仅证明进程在动,**不证明有产出**;"
            "健康请改用 artifact_rows / business_ts)" % (age or 0))


PROBES = {"file_fresh": _pr_file_fresh, "glob_count": _pr_glob_count, "systemd": _pr_systemd,
          "container": _pr_container, "http": _pr_http, "db_rows": _pr_db_rows,
          "log_recent": _pr_log_recent, "artifact_rows": _pr_artifact_rows,
          "business_ts": _pr_business_ts}
WEAK_PROBES = ("log_recent",)                 # 只能降级、不能单独判 healthy 的弱证据


def _run_probe(spec):
    """执行一格的探针。没有探针就是纯自报,如实标 unknown 而不是当成通过。"""
    if not isinstance(spec, dict):
        return None, "格子定义不合法"
    kind = spec.get("probe")
    if not kind:
        return None, ""
    fn = PROBES.get(kind)
    if not fn:
        return "unknown", "未知探针类型 %s" % kind
    try:
        return fn(spec.get("args") or {})
    except Exception as e:                       # 单格出错不能拖垮整张表
        return "unknown", "探测异常:%s" % str(e)[:60]


# 公开面**永不出现私有仓名**。业务流的 evidence 文案来自各项目自己的事实档 ——
# 即使源仓是 public(KMOS 就是),也不该把私有仓名原样搬到本站公开快照上:
# 这条不变量是本站自己的,不随上游可见性放松。语义不丢,只做通名替换。
PRIVATE_MASK = {"Private-Database": "私有库", "KMFA-App-State-Backup": "私有备份仓",
                "Private-KMDatabase": "私有库", "Private-MetaDatabase": "私有库",
                "Private-AgentDatabase": "私有库"}


def _mask_private(obj):
    if isinstance(obj, str):
        for k, v in PRIVATE_MASK.items():
            obj = obj.replace(k, v)
        return obj
    if isinstance(obj, dict):
        return {k: _mask_private(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_mask_private(x) for x in obj]
    return obj


def flow_state():
    """把各项目登记的业务流跑一遍探针,并与自报状态**交叉校验**。

    ★ 双向:`state` 是项目自报,`probe` 是本机只读实测。两者不符单独标出 ——
      人手维护的矩阵最终就是从这里开始和现实脱节的。
    ★ 跨基线耦合(KMFA 线程提供的规则,比按阶段顺序的位置传导准得多):
      上游基线任一 stage 为 blocked/blocked_by_input/not_built 时,
      下游依赖它的 stage 若仍自称 healthy,标为**违反耦合规则**。
    """
    docs = load_json(os.path.join(APP_DIR, "private", "flow_docs.json"), None)
    if not isinstance(docs, dict) or not docs.get("projects"):
        return {"available": False,
                "note": (docs or {}).get("note") or "业务流登记尚未采集(每小时深采一次)",
                "unregistered": (docs or {}).get("unregistered") or []}
    out = []
    tot = {"baselines": 0, "cells": 0, "healthy": 0, "degraded": 0, "blocked": 0,
           "blocked_by_policy": 0, "blocked_by_input": 0, "not_built": 0, "unknown": 0,
           "probed": 0, "mismatch": 0, "coupling_violation": 0, "weak_only": 0}
    for p in docs["projects"]:
        stages = [x for x in (p.get("stages") or []) if isinstance(x, str)][:12]
        names = p.get("stage_names") or {}
        bl_out, by_id = [], {}
        for b in (p.get("baselines") or []):
            cells, worst = {}, "healthy"
            for st in stages:
                spec = (b.get("cells") or {}).get(st)
                spec = spec if isinstance(spec, dict) else None
                declared = _ALIAS.get((spec or {}).get("state"), (spec or {}).get("state"))
                measured, ev = _run_probe(spec) if spec else (None, "")
                weak = bool(spec and spec.get("probe") in WEAK_PROBES)
                if measured:
                    tot["probed"] += 1
                    if weak:
                        tot["weak_only"] += 1
                # 自报优先:policy / not_built 是人的决定,机器测不出来,必须尊重
                if declared in ("blocked_by_policy", "not_built"):
                    final = declared
                elif measured and not (weak and measured == "healthy"):
                    final = measured          # 弱证据不得单独把一格判成 healthy
                elif declared in FLOW_STATES:
                    final = declared
                else:
                    final = "unknown"
                mism = bool(measured and declared in FLOW_STATES
                            and declared not in ("blocked_by_policy", "not_built")
                            and measured != declared)
                cells[st] = {"s": final, "n": names.get(st, st),
                             "v": ev or (spec or {}).get("evidence") or "",
                             "declared": declared, "measured": measured,
                             "weak": weak, "mismatch": mism,
                             # ★ 缺失用 None,不用 ""(见 collect_github 适配层同名注释:
                             #   "" 会和 "" 相等,把一条缺陷挂到全部格子上)
                             "defect": (spec or {}).get("defect") or None}
                tot["cells"] += 1
                tot[final if final in tot else "unknown"] += 1
                if mism:
                    tot["mismatch"] += 1
                if _SEV.get(final, 9) < _SEV.get(worst, 9):
                    worst = final
            probed_n = sum(1 for c in cells.values() if c["measured"])
            row = {"verified": probed_n, "cells_n": len(cells),
                   "id": b.get("id") or "", "name": b.get("name") or "",
                   "priority": (b.get("priority") or "P3").upper(),
                   "note": b.get("note") or "",
                   "upstream": b.get("upstream") or [], "downstream": b.get("downstream") or [],
                   "cells": cells, "state": worst,
                   "bad": sum(1 for c in cells.values() if c["s"] in FLOW_BAD),
                   "warn": sum(1 for c in cells.values() if c["s"] == "degraded")}
            bl_out.append(row)
            by_id[row["id"]] = row
            tot["baselines"] += 1

        # 跨基线耦合校验
        for b in bl_out:
            ups = [by_id[u] for u in b["upstream"] if u in by_id]
            bad_up = [u for u in ups if u["state"] in FLOW_BLOCKS_DOWNSTREAM]
            if not bad_up:
                continue
            for st, c in b["cells"].items():
                if c["s"] == "healthy":
                    c["coupling_violation"] = [u["name"] or u["id"] for u in bad_up][:3]
                    tot["coupling_violation"] += 1

        rank = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}
        bl_out.sort(key=lambda x: (rank.get(x["priority"], 9), _SEV.get(x["state"], 9)))
        # 源级视图:一个上游源断了会连累哪几条基线(业务层的爆炸半径)
        srcs = {}
        for b in bl_out:
            for u in b["upstream"]:
                if str(u).startswith("SRC-"):
                    srcs.setdefault(u, []).append(b["name"] or b["id"])
        pv = sum(b["verified"] for b in bl_out)
        pc = sum(b["cells_n"] for b in bl_out)
        out.append({"verified": pv, "cells_n": pc,
                    "verified_pct": round(pv / pc * 100) if pc else 0,
                    "self_report_only": pv == 0,
                    "project": p.get("project") or "", "repo": p.get("repo") or "",
                    "stages": stages, "stage_names": names,
                    "stage_meaning": p.get("stage_meaning") or {},
                    "baselines": bl_out, "defects": p.get("defects") or [],
                    "sources": sorted(({"id": k, "lines": v, "n": len(v)}
                                       for k, v in srcs.items()), key=lambda x: -x["n"]),
                    "coupling_rule": p.get("coupling_rule") or "",
                    "authority": p.get("authority") or "",
                    "source": p.get("source") or "", "schema": p.get("schema") or "",
                    "bad": sum(b["bad"] for b in bl_out),
                    "warn": sum(b["warn"] for b in bl_out)})
    out.sort(key=lambda x: (-x["bad"], -x["warn"], x["project"]))

    # 逐日归档:同一天取**最差**的一次 —— 一天里坏过就不该被后面的好覆盖
    hp = os.path.join(DATA_DIR, "flow_history.json")
    store = load_json(hp, {}) or {}
    day = now_cn().strftime("%Y-%m-%d")
    slot = store.setdefault(day, {})
    for p in out:
        for b in p["baselines"]:
            for st, c in b["cells"].items():
                key = "%s|%s|%s" % (p["project"], b["id"] or b["name"], st)
                prev = slot.get(key)
                if prev is None or _SEV.get(c["s"], 9) < _SEV.get(prev, 9):
                    slot[key] = c["s"]
    cutoff = (now_cn() - timedelta(days=400)).strftime("%Y-%m-%d")
    store = {k: v for k, v in store.items() if k >= cutoff}
    try:
        with open(hp, "w") as f:
            json.dump(store, f)
    except OSError:
        pass
    days = sorted(store)
    for p in out:
        for b in p["baselines"]:
            if b["state"] == "healthy":
                continue
            since = None
            for st in p["stages"]:
                if b["cells"].get(st, {}).get("s") == "healthy":
                    continue
                key = "%s|%s|%s" % (p["project"], b["id"] or b["name"], st)
                d0 = None
                for d in reversed(days):
                    if store[d].get(key) and store[d][key] != "healthy":
                        d0 = d
                    else:
                        break
                if d0 and (since is None or d0 < since):
                    since = d0
            b["since"] = since
    return _mask_private({"available": True, "projects": out, "totals": tot,
            "unregistered": docs.get("unregistered") or [],
            "fetched_at": docs.get("at"),
            "history_days": len(days), "history_since": days[0] if days else None,
            "trend": [{"d": d,
                       "healthy": sum(1 for v in store[d].values() if v == "healthy"),
                       "degraded": sum(1 for v in store[d].values() if v == "degraded"),
                       "bad": sum(1 for v in store[d].values() if v in FLOW_BAD)}
                      for d in days[-90:]],
            "verified_total": sum(p["verified"] for p in out),
            "cells_total": sum(p["cells_n"] for p in out),
            "note": "★**自报的绿 ≠ 实测的绿**:未配探针的格子只代表被测方「说它通」,"
                    "不代表本站测出来通。每个项目标了实测覆盖率,0% 的项目会整体标注为「仅自报」。"
                    "阶段由各项目自己定义,本站只统一接口与呈现。格子状态 = 自报与本机只读实测的"
                    "合并结果,不符单独标出。★日志新鲜度是**弱证据**,不得单独判健康 —— "
                    "实测过 cron 跑了、退出码 0、日志新鲜,却一个产出都没有的假绿;"
                    "健康判定请用产出物条数与业务时间戳。探针只看元信息,绝不读业务数据内容。"
                    "证据文案已做私有仓名脱敏。",
            "at": int(time.time())})


# ---------- 容量判定:该不该升级 VPS / 还能再部署多少 ----------
# 阈值全部写死在这里,页面上原样展示 —— 结论必须能被复核,不能是"我觉得"。
THRESH = {
    "mem_avail_tight": 0.20,      # 可用内存 / 总内存 低于此值 = 紧张
    "mem_avail_crit": 0.10,       #                          低于此值 = 告急
    "swap_tight": 0.50,           # swap 使用率 高于此值 = 紧张(需结合可用内存判读)
    "swap_crit": 0.85,
    "disk_tight": 0.75,           # 磁盘使用率
    "disk_crit": 0.85,
    "cpu_tight": 0.70,            # load1 / 核数
    "cpu_crit": 1.00,
    "disk_eta_days": 90,          # 磁盘按当前斜率外推,少于这么多天触顶 = 需要动作
    "reserve_mem_mb": 500,        # 算余量时给系统留的内存
    "reserve_disk_b": 4 * 1024 ** 3,   # 算余量时给系统留的磁盘
}


def _slope_per_day(ts, vs, min_pts=8):
    """磁盘占用的日增长率(%/天),用 **Theil–Sen 中位数斜率**,不用最小二乘。

    ★ 为什么不用最小二乘:实测日序列是 `46 → 76 → 60 → 67` —— 构建缓存一次清理就能
      让磁盘掉 16 个点。最小二乘对这种跳变极敏感,一个异常点就能把外推拉出几天的假警报。
      Theil–Sen 取所有点对斜率的**中位数**,天然抗离群,几个跳变点动不了结论。
    """
    pts = [(t, v) for t, v in zip(ts, vs) if isinstance(v, (int, float))]
    if len(pts) < min_pts:
        return None
    sl = []
    for i in range(len(pts)):
        for j in range(i + 1, len(pts)):
            dt = (pts[j][0] - pts[i][0]) / 86400.0
            if dt > 0.02:                      # 相隔太近的两点比值噪声太大,跳过
                sl.append((pts[j][1] - pts[i][1]) / dt)
    if not sl:
        return None
    sl.sort()
    n = len(sl)
    return sl[n // 2] if n % 2 else (sl[n // 2 - 1] + sl[n // 2]) / 2


def capacity_advice(host, hist, prices, sw):
    """该不该升级 VPS、还能再部署多少 —— 全部由实测量 + 明写阈值判定。

    **不联网抓 OVH 价格。** VPS 档位与月价由 owner 在 `data/prices.json` 的 `vps_tiers`
    里登记;没登记就只给「要不要升级」的结论,不编造型号和差价。
    """
    mt, ma = host.get("mem_total_mb"), host.get("mem_avail_mb")
    st, su = host.get("swap_total_mb"), host.get("swap_used_mb")
    dt, du = host.get("disk_total_b"), host.get("disk_used_b")
    cores = host.get("cores") or 1
    def _f(k):
        try:
            return float(host.get(k) or 0)
        except (TypeError, ValueError):
            return 0.0
    load1, load15 = _f("load1"), _f("load15") or _f("load1")
    dock = docker_space()

    sig = []                                   # 每条信号:维度 / 级别 / 实测 / 阈值 / 判读

    def add(dim, level, fact, rule, read):
        sig.append({"dim": dim, "level": level, "fact": fact, "rule": rule, "read": read})

    avail_r = (ma / mt) if (ma and mt) else None
    swap_r = (su / st) if (su is not None and st) else None
    if avail_r is not None:
        lv = ("crit" if avail_r < THRESH["mem_avail_crit"] else
              "tight" if avail_r < THRESH["mem_avail_tight"] else "ok")
        add("内存", lv, "可用 %d MB / 共 %d MB(%.0f%%)" % (ma, mt, avail_r * 100),
            "可用率 <%.0f%% 紧张 / <%.0f%% 告急" % (THRESH["mem_avail_tight"] * 100,
                                                    THRESH["mem_avail_crit"] * 100),
            "含 buff/cache 可回收部分,这是内核口径的真实可用量")
    if swap_r is not None:
        lv = ("crit" if swap_r > THRESH["swap_crit"] else
              "tight" if swap_r > THRESH["swap_tight"] else "ok")
        # ★ swap 高 + 可用内存充足 = 历史峰值遗留,不是此刻缺内存。
        #   Linux 不会主动把 swap 换回内存,只看 swap 会把"曾经紧张过"误读成"现在告急"。
        if lv != "ok" and avail_r is not None and avail_r >= THRESH["mem_avail_tight"]:
            lv, note = "watch", "swap 高但可用内存充足 —— 是**历史峰值的遗留**,不是此刻缺内存。" \
                                "Linux 不会主动换回,重启即清零。真正要看的是可用内存。"
        else:
            note = "swap 与可用内存同时吃紧,才是真的内存不够"
        add("Swap", lv, "已用 %d MB / 共 %d MB(%.0f%%)" % (su, st, swap_r * 100),
            "使用率 >%.0f%% 紧张 / >%.0f%% 告急" % (THRESH["swap_tight"] * 100,
                                                    THRESH["swap_crit"] * 100), note)

    disk_r = (du / dt) if (du and dt) else None
    free_b = (dt - du) if (du and dt) else 0
    recl = dock["reclaim_b"]
    after_r = ((du - recl) / dt) if (du and dt) else None
    if disk_r is not None:
        lv = ("crit" if disk_r > THRESH["disk_crit"] else
              "tight" if disk_r > THRESH["disk_tight"] else "ok")
        if lv != "ok" and after_r is not None and after_r <= THRESH["disk_tight"]:
            lv = "watch"
        add("磁盘", lv, "已用 %s / %s(%.0f%%)" % (fmt_bytes(du), fmt_bytes(dt), disk_r * 100),
            "使用率 >%.0f%% 紧张 / >%.0f%% 告急" % (THRESH["disk_tight"] * 100,
                                                    THRESH["disk_crit"] * 100),
            ("其中 %s 是 Docker 可立刻回收的(构建缓存/悬空镜像/卷),回收后降到 %.0f%% —— "
             "**在回收之前谈升级是不理性的**" % (fmt_bytes(recl), after_r * 100)) if recl > 0
            else "无可回收空间")

    # 优先用小时序列(样本密、覆盖近 31 天),不够再退回日序列
    hr = (hist or {}).get("hour") or {}
    day = (hist or {}).get("day") or {}
    src, slope = "小时序列", _slope_per_day(hr.get("t") or [], hr.get("disk") or [], 24)
    if slope is None:
        src, slope = "日序列", _slope_per_day(day.get("t") or [], day.get("disk") or [], 6)
    n_pts = len(hr.get("t") or []) if src == "小时序列" else len(day.get("t") or [])
    eta_days = None
    if slope is not None and disk_r is not None:
        if slope <= 0.02:
            add("磁盘趋势", "ok", "中位斜率 %+.3f %%/天(%s,%d 点)" % (slope, src, n_pts),
                "增长 >0.02 %%/天 才外推触顶时间", "当前基本持平或在下降,不构成容量风险")
        else:
            eta_days = int(max(0, (THRESH["disk_crit"] * 100 - disk_r * 100) / slope))
            add("磁盘趋势", "tight" if eta_days < THRESH["disk_eta_days"] else "ok",
                "中位斜率 %+.3f %%/天,按此外推 %d 天后触及 %.0f%%"
                % (slope, eta_days, THRESH["disk_crit"] * 100),
                "少于 %d 天触顶即需动作" % THRESH["disk_eta_days"],
                "用 Theil–Sen 中位数斜率(抗离群):清一次构建缓存磁盘就能掉十几个点,"
                "最小二乘会被这种跳变带出假警报。样本 %d 点(%s)" % (n_pts, src))

    # ★ 判定必须用 **15 分钟均载**,不能用 load1。
    #   实测教训:采集器自己跑一次 4 分钟的深采就能把 load1 顶到 5.63(2 核),
    #   于是"该不该升级"被**我自己的测量动作**翻成了"建议升级" —— 典型观察者效应。
    #   我在判读里写着"持续高于 1 才有意义",却让一个瞬时采样决定了结论,这就是自打脸。
    # ★ 单次采样一律不作数。实测:采集器自己跑一次 4 分钟深采,就把 load1 顶到 5.63、
    #   load15 顶到 3.09(2 核),于是"该不该升级"被**我自己的测量动作**翻成了"建议升级"
    #   —— 典型观察者效应。所以判定改看 **24 小时 P90**;样本不够就如实说暂不判定,不硬下结论。
    mseries = [v for v in ((hist or {}).get("min") or {}).get("load", []) if isinstance(v, (int, float))]
    win = mseries[-1440:]
    if len(win) >= 60:
        srt = sorted(win)
        p90 = srt[int(len(srt) * 0.9) - 1]
        cpu_r = p90 / cores
        add("CPU", "crit" if cpu_r > THRESH["cpu_crit"] else
            ("tight" if cpu_r > THRESH["cpu_tight"] else "ok"),
            "24h P90 均载 %.2f / %d 核 = %.2f(当前 load1 %.2f / load15 %.2f)"
            % (p90, cores, cpu_r, load1, load15),
            "**24 小时 P90** 比值 >%.2f 紧张 / >%.2f 告急" % (THRESH["cpu_tight"], THRESH["cpu_crit"]),
            "按分位数判定,单次采集/构建造成的尖峰不会翻转结论(样本 %d 个)" % len(win))
    else:
        add("CPU", "ok", "当前 load15 %.2f / %d 核 = %.2f" % (load15, cores, load15 / cores),
            "需要 ≥60 个样本才按 24h P90 判定", "★负载样本仅 %d 个,**暂不作为升级依据**——"
            "单次采样会被采集器自身的负载污染,宁可不判也不误判" % len(win))

    # ---- 部署余量:还能再放几个「典型应用」----
    lines = [x for x in (sw or {}).get("lines", []) if x.get("kind") == "business"]
    typ_mem = 60                                  # MB:按现有业务容器实测中位数量级取整
    imgs = next((r["size_b"] for r in dock["rows"] if r["type"].lower().startswith("image")), 0)
    typ_disk = int(imgs / max(1, len(lines))) if imgs else 300 * 1024 ** 2
    slots_mem = int(max(0, (ma or 0) - THRESH["reserve_mem_mb"]) / typ_mem) if ma else 0
    slots_disk = int(max(0, free_b + recl - THRESH["reserve_disk_b"]) / max(1, typ_disk))
    slots = min(slots_mem, slots_disk)

    levels = [s["level"] for s in sig]
    crit = levels.count("crit")
    tight = levels.count("tight")
    tiers = (prices or {}).get("vps_tiers") or []
    if crit:
        verdict, vlevel = "建议升级", "bad"
        why = "有 %d 个维度已达告急阈值,且不是回收就能解决的。" % crit
    elif tight:
        verdict, vlevel = "先回收,暂不升级", "warn"
        why = "有 %d 个维度紧张,但回收 Docker 可用空间后即可缓解 —— 花钱之前先做免费的那步。" % tight
    else:
        verdict, vlevel = "无需升级", "ok"
        why = "所有维度都在阈值内,当前配置仍有余量。"
    # 当前档位一律由**实测**推导,不依赖登记 —— 登记表只用来提供可升级的目标档与差价
    cur = {"name": "当前机器", "cores": cores,
           "ram_gb": round((mt or 0) / 1024.0, 1),
           "disk_gb": round((dt or 0) / 1024.0 ** 3, 1), "current": True, "measured": True}
    target = None
    if crit and tiers:
        target = next((t for t in tiers if (t.get("ram_gb") or 0) > cur["ram_gb"]), None)

    return {
        "verdict": verdict, "level": vlevel, "why": why,
        "signals": sig,
        "thresholds": THRESH,
        "headroom": {
            "slots": slots, "by_mem": slots_mem, "by_disk": slots_disk,
            "typical_mem_mb": typ_mem, "typical_disk_b": typ_disk,
            "free_b": free_b, "reclaimable_b": recl,
            "note": "「典型应用」= 按现有 %d 条业务线的镜像均摊与容器内存量级估算;"
                    "已给系统预留 %d MB 内存与 %s 磁盘。这是数量级参考,不是保证。"
                    % (len(lines), THRESH["reserve_mem_mb"], fmt_bytes(THRESH["reserve_disk_b"])),
        },
        "docker": dock,
        "reclaim_hint": "docker builder prune -f  # 只清构建缓存,不动镜像与卷",
        "current_tier": cur,
        "tiers": tiers,
        "target": target,
        "tiers_note": "VPS 档位与月价由 owner 在 /admin 的价格库登记(`vps_tiers`);"
                      "本站**不联网抓取供应商价格**,没登记就不给型号与差价,只给要不要升级的结论。",
        "eta_days": eta_days,
        "at": int(time.time()),
    }


# ---------- 软件运行状态:自动探测 + 登记核对 + 业务基线纵向切片 ----------
# 平台底座也必须登记 —— 否则「未登记」告警会被底座组件刷屏,治理就形同虚设。
# 这些不是业务线,但同样是跑在 OVH 上的软件,同样要有归属和自愈。
PLATFORM = [
    {"name": "Coolify 平台", "role": "部署编排底座",
     "owns": {"container": ["coolify"], "cron": ["linze-coolify-backup"],
              "image": ["coolify-helper"]},            # 临时构建容器名是随机串,只能按镜像认领
     "heal": "容器 restart 策略 + 每日库备份"},
    {"name": "Traefik 入口", "role": "反向代理 / TLS", "owns": {"container": ["coolify-proxy"]},
     "heal": "restart 策略 + 证书自动续期"},
    {"name": "邮件网关", "role": "SMTP 中继", "owns": {"container": ["linze-smtp-bridge"]},
     "heal": "restart 策略"},
    {"name": "备份体系", "role": "异地备份 / 身份库备份",
     "owns": {"cron": ["linze-offsite-backup", "linze-identity-backup"]}, "heal": "cron 自运行 + 自愈看门狗"},
    {"name": "链路巡检", "role": "外链健康 / Access 席位熔断",
     "owns": {"cron": ["linze-link-health", "linze-cf-seat-fuse"]}, "heal": "cron 自运行"},
    # 自动探测把它挖出来之前,这个单元没出现在任何一张视图里 —— 正是这条治理规则要防的情况
    {"name": "Cloudflare 隧道", "role": "CF Tunnel 入站(部分域名不经公网源站)",
     "owns": {"systemd": ["cloudflared"]}, "heal": "systemd Restart + cloudflared-update 定时更新"},
]

# 业务基线的纵向切片 —— 一条业务线从代码到自愈,每一段都必须有实测证据,
# 任何一段是黑箱,这条线就不算「白箱受控」。
STAGES = [
    ("code", "代码源"), ("ci", "CI"), ("deploy", "部署"), ("run", "运行"), ("entry", "入口"),
    ("data", "数据"), ("backup", "备份"), ("monitor", "监控"), ("heal", "自愈"),
]
_ORDER = {"bad": 0, "warn": 1, "na": 2, "ok": 3}


def _cell(state, evidence):
    return {"s": state, "v": evidence}


def _systemd_state(kv):
    """把 systemctl show 的字段判成一个状态,单独抽出来是为了能被测试直接打。

    定时/路径触发的 oneshot 单元、以及 OnFailure 的 `xxx@yyy.service` 模板实例,
    平时就该是 inactive —— 判成 down 会造出一整列假红。
    """
    act, typ = kv.get("ActiveState", ""), kv.get("Type", "")
    trig, res = kv.get("TriggeredBy", ""), kv.get("Result", "success")
    if act == "active":
        return "active"
    if res != "success":
        return "failed"
    if typ == "oneshot" or trig or "@" in (kv.get("Id") or ""):
        return "scheduled"
    return "inactive"


def discover_units():
    """自动探测主机上**实际在跑的**部署单元,不依赖任何登记表。

    治理规则要求「凡是部署到 OVH / Cloudflare 的都必须登记」,
    而能执行这条规则的前提,是先能不看登记表就把东西找全——否则漏登记的永远发现不了。
    四个来源都是主机自带的事实:Coolify 库、Docker、systemd、cron。
    Cloudflare 侧没有只读令牌(owner 明确不建),所以**不假装能枚举**,
    只按登记表里声明的 CF 单元做对外可达性验证,并如实标注这一段是「凭 HTTP 实测,非账面枚举」。
    """
    units = []
    raw = run("for c in $(docker ps -a --format '{{.Names}}'); do "
              "docker inspect \"$c\" --format '{{.Name}}|{{.State.Status}}|"
              "{{.HostConfig.RestartPolicy.Name}}|{{.Config.Image}}|"
              "{{if .State.Health}}{{.State.Health.Status}}{{else}}-{{end}}|{{.State.StartedAt}}|"
              "{{json .Config.Labels}}' 2>/dev/null; done",
              timeout=60)
    for line in raw.splitlines():
        parts = line.split("|", 6)
        if len(parts) < 7:
            continue
        name, state, policy, image, health, started, labels = parts
        name = name.lstrip("/")
        m = re.search(r"Host\(`([^`]+)`\)", labels or "")
        age = None
        try:                                            # StartedAt 是 RFC3339,只取到秒
            age = int(time.time() - datetime.strptime(
                started[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc).timestamp())
        except ValueError:
            pass
        units.append({"kind": "container", "id": name, "state": state,
                      "domain": m.group(1) if m else None, "policy": policy,
                      "health": health, "age_s": age, "detail": image.split("@")[0]})
    # systemd 必须问清楚**单元类型**,不能只看 active/inactive。
    # ★ 实测教训:Alpha 的 13 个单元里有一半是 Type=oneshot + TriggeredBy=timer/path
    #   (盘前自检、账本备份、净值快照)以及 alpha-alert@* 失败告警模板实例——
    #   它们平时就该是 inactive,拿 inactive 当"挂了"会造出一整列假红。
    #   假红比没有告警更糟:一旦习惯了红色,真出事那次也不会有人看。
    names = [f.split()[0] for f in
             run("systemctl list-units --type=service --all --no-pager --no-legend "
                 "2>/dev/null").splitlines()
             if f.split() and f.split()[0].endswith(".service")
             and re.match(r"(alpha|eei|linze|kmfa|adp|cloudflared)[-.@]", f.split()[0])]
    if names:
        blob = run("systemctl show %s --property=Id --property=Type --property=ActiveState "
                   "--property=Result --property=TriggeredBy --property=Description 2>/dev/null"
                   % " ".join("'%s'" % n for n in names[:60]), timeout=30)
        for chunk in blob.split("\n\n"):
            kv = dict(l.split("=", 1) for l in chunk.splitlines() if "=" in l)
            if not kv.get("Id"):
                continue
            state = _systemd_state(kv)
            units.append({"kind": "systemd", "id": kv["Id"], "state": state,
                          "domain": None, "policy": "systemd",
                          "detail": (kv.get("Description") or "")[:60]})
    for f in (run("ls /etc/cron.d/ 2>/dev/null").splitlines()):
        if not f.startswith("linze"):
            continue
        n = run("grep -c '^[0-9*]' /etc/cron.d/%s 2>/dev/null" % f)
        units.append({"kind": "cron", "id": f, "state": "scheduled",
                      "domain": None, "policy": "cron", "detail": "%s 条计划" % (n or "?")})
    for line in psql("select name, coalesce(fqdn,''), status from applications;").splitlines():
        p = line.split("|")
        if len(p) < 3:
            continue
        units.append({"kind": "coolify", "id": p[0], "state": p[2],
                      "domain": (p[1] or "").replace("https://", "").rstrip("/") or None,
                      "policy": "coolify", "detail": "Coolify 应用"})
    return units


def _claims(entry):
    o = entry.get("owns") or {}
    host = (entry.get("url") or "").replace("https://", "").rstrip("/")
    return host, o


def _owner_of(unit, registry):
    """把探测到的单元认领给某条业务线。

    顺序:域名精确匹配 > **最长前缀**匹配 > Coolify 应用名 > 镜像名。
    ★ 最长前缀不能省:`coolify` 和 `coolify-proxy` 同时存在时,按登记顺序匹配会让
      "Coolify 平台"把 coolify-proxy 抢走,"Traefik 入口"就变成一条没有单元的空线。
    """
    for e in registry:
        host, o = _claims(e)
        if unit.get("domain") and host and unit["domain"] == host:
            return e["name"]
        if unit["kind"] == "coolify" and o.get("coolify") == unit["id"]:
            return e["name"]
    best, best_len = None, -1
    for e in registry:
        o = (e.get("owns") or {})
        for pre in (o.get(unit["kind"]) or []):
            if unit["id"].startswith(pre) and len(pre) > best_len:
                best, best_len = e["name"], len(pre)
        for pre in (o.get("image") or []):
            if pre in (unit.get("detail") or "") and len(pre) > best_len:
                best, best_len = e["name"], len(pre)
    return best


def software_runtime(projects, gh, backup, cert, ch, live, heal, dep):
    """业务基线纵向切片 + 登记合规。全部由**本轮实测数据**推导,不调模型、不新增外部请求。"""
    units = discover_units()
    registry = list(projects) + [dict(p, url="", status="run") for p in PLATFORM]
    for u in units:
        u["owner"] = _owner_of(u, registry)

    by_owner = {}
    for u in units:
        by_owner.setdefault(u["owner"], []).append(u)
    unregistered = [u for u in units if not u["owner"]]

    ghrepo = {}
    for r in ((gh or {}).get("public_repos") or []):
        ghrepo[r["name"]] = r
    monitored = {s["site"] for s in ((live or {}).get("sites") or [])}
    heal_rules = (heal or {}).get("rules") or []
    heal_ok = sum(1 for r in heal_rules if r.get("state") == "ok")
    last_dep = {}
    for d in ((dep or {}).get("log") or []):                 # 最近部署流水:每个应用只留最新一条
        last_dep.setdefault((d.get("app") or "").lower(), d)

    lines = []
    for e in registry:
        mine = by_owner.get(e["name"], [])
        host = (e.get("url") or "").replace("https://", "").rstrip("/")
        is_platform = "role" in e
        cells = {}

        repo = e.get("repo")
        cells["code"] = (_cell("ok", repo) if repo else
                         _cell("na", "平台组件·无独立仓" if is_platform else "无独立仓"))
        r = ghrepo.get(repo) if repo else None
        cells["ci"] = (_cell("ok" if r.get("ci_ok") else "bad",
                             ("最近 CI %s" % (r.get("ci_conclusion") or r.get("ci_state") or "—")))
                       if r else _cell("na", "无 CI 或私有仓"))

        cool = (e.get("owns") or {}).get("coolify")
        dp = last_dep.get((cool or e["name"]).lower())
        how = e.get("deploy") or e.get("role") or "—"
        # ★ in_progress 不是失败:Coolify 队列里正在跑的那条,判成红色就是冤枉它
        st_dep = (dp or {}).get("status") or ("finished" if (dp or {}).get("ok") else "")
        cells["deploy"] = (_cell({"finished": "ok", "in_progress": "warn"}.get(st_dep, "bad"),
                                 "%s · 最近 %s %s" % (how, dp["at"],
                                 {"finished": "成功", "in_progress": "进行中"}.get(st_dep, "失败")))
                           if dp else _cell("ok", how))

        run_units = [u for u in mine if u["kind"] in ("container", "systemd")]
        cron_units = [u for u in mine if u["kind"] == "cron"]
        alive = [u for u in run_units if u["state"] in ("running", "active")]
        waiting = [u for u in run_units if u["state"] == "scheduled"]   # 定时/事件触发,平时就该静默
        dead = [u for u in run_units if u["state"] in ("failed", "exited", "inactive", "dead")]
        if run_units:
            ev = "%d/%d 常驻存活" % (len(alive), len(run_units) - len(waiting))
            if waiting:
                ev += " · %d 个定时/事件触发待命" % len(waiting)
            cells["run"] = _cell("bad" if dead else "ok",
                                 ev + (" · %d 个异常" % len(dead) if dead else ""))
        elif cron_units:
            cells["run"] = _cell("na", "由 cron 触发 · 无常驻进程")
        elif (e.get("host") or "").startswith("Cloudflare"):
            cells["run"] = _cell("na", "跑在 Cloudflare 边缘 · 主机侧无单元")
        else:
            cells["run"] = _cell("warn", "未探测到任何运行单元")

        st = e.get("status")
        # ★ 刚重新部署的服务,Traefik 会先回 503 直到健康检查通过。
        #   实测 KMFA 部署完 1 分钟内就是这个状态——把它判成"挂了",等于每次上线都误报一次。
        booting = [u for u in mine if u["kind"] == "container"
                   and (u.get("health") == "starting"
                        or (u.get("age_s") is not None and u["age_s"] < 300))]
        if not e.get("url"):
            cells["entry"] = _cell("na", "无对外入口(内部组件)")
        elif st in ("run", "access"):
            cells["entry"] = _cell("ok", {"run": "对外 200", "access": "受 Access 保护"}[st])
        elif booting:
            cells["entry"] = _cell("warn", "刚完成部署 · 健康检查启动中(%d 秒前起)"
                                   % min(u.get("age_s") or 0 for u in booting))
        else:
            cells["entry"] = _cell("bad", "对外不可达")
        if e.get("url") and cert.get("days") is not None:
            cells["entry"]["v"] += " · 证书剩 %s 天" % cert["days"]

        db = e.get("db") or ""
        if is_platform:
            cells["data"] = _cell("na", "平台组件")
        elif db.startswith("无"):
            cells["data"] = _cell("na", db)
        else:
            dbu = [u for u in mine if u["kind"] == "container" and
                   re.search(r"(postgres|db|redis)", u["id"])]
            cells["data"] = (_cell("ok" if all(u["state"] == "running" for u in dbu) else "bad",
                                   "%s · %d 个库容器在跑" % (db, len(dbu))) if dbu
                             else _cell("ok", db))

        cells["backup"] = (_cell("ok" if backup.get("ok") else "warn",
                                 "%s · 最近 %s" % (e.get("backup") or e.get("heal") or "—",
                                                   backup.get("at") or "无记录"))
                           if not is_platform else
                           _cell("ok" if backup.get("ok") else "warn",
                                 "%s · 主机整体备份 %s" % (e.get("heal") or "—",
                                                          backup.get("at") or "无记录")))

        cells["monitor"] = (_cell("ok", "Gatus 存活探测 + 本站日志逐分钟") if host in monitored
                            else _cell("ok", "Gatus 存活探测") if e.get("url")
                            else _cell("ok", "本采集器每分钟核对单元存活") if mine
                            else _cell("warn", "既无对外入口也无可探测单元"))

        # 临时构建容器(restart=no)本来就该跑完即退,把它算进自愈覆盖率是苛责 ——
        # container_health() 早就踩过这个坑,这里用同一套口径,别让两个视图互相打架
        persist = [u for u in run_units if u.get("policy") != "no"]
        pol = [u for u in persist if u.get("policy") in ("always", "unless-stopped", "systemd")]
        eph = len(run_units) - len(persist)
        if persist:
            cells["heal"] = _cell("ok" if len(pol) == len(persist) else "warn",
                                  "%d/%d 常驻单元配了自动拉起" % (len(pol), len(persist))
                                  + (" · %d 个临时单元无需" % eph if eph else ""))
        elif run_units:
            cells["heal"] = _cell("na", "%d 个单元均为临时任务 · 无需常驻自愈" % len(run_units))
        elif cron_units:
            cells["heal"] = _cell("ok", "cron 自运行 + 采集器看门狗")
        elif (e.get("host") or "").startswith("Cloudflare"):
            # 无状态边缘函数由平台自身托管重启,主机侧本来就不该有自愈单元 —— 判 warn 是苛责
            cells["heal"] = _cell("na", "CF 边缘托管 · 平台自动重启,主机侧无自愈单元")
        else:
            cells["heal"] = _cell("warn", "无自愈单元")

        bad = sum(1 for k, _ in STAGES if cells[k]["s"] == "bad")
        warn = sum(1 for k, _ in STAGES if cells[k]["s"] == "warn")
        # ★ na(不适用)不扣分**也不该被当成达标**:ADP 跑在 CF 边缘,九段里四段是 na,
        #   照旧是满分,于是 17 条线全 100 —— 分数对谁都一样就等于没有分数。
        #   这里把「实际判过几段」一起送出去,页面按覆盖率如实呈现。
        na = sum(1 for k, _ in STAGES if cells[k]["s"] == "na")
        lines.append({
            "name": e["name"], "kind": "platform" if is_platform else "business",
            "url": e.get("url") or "", "repo": repo or "", "role": e.get("role") or "",
            "host": e.get("host") or "OVH VPS-1", "agent": e.get("agent") or "—",
            "cells": cells, "units": len(mine),
            "unit_ids": [u["id"] for u in mine][:8],
            "na": na, "judged": len(STAGES) - na, "stages_total": len(STAGES),
            "score": max(0, 100 - bad * 26 - warn * 8),
            "state": "bad" if bad else ("warn" if warn else "ok"),
        })
    lines.sort(key=lambda x: (_ORDER[x["state"]], x["kind"] != "business", x["name"]))

    # 爆炸半径:共享资源一旦出事会连累几条业务线
    blast = {}
    for ln in lines:
        for key, label in (("host", "主机"), ("repo", "代码仓")):
            v = ln.get(key)
            if v:
                blast.setdefault("%s:%s" % (label, v), []).append(ln["name"])
    radius = sorted(({"res": k, "lines": v, "n": len(v)} for k, v in blast.items() if len(v) > 1),
                    key=lambda x: -x["n"])

    covered = len([u for u in units if u["owner"]])
    return {
        "stages": [{"k": k, "n": n} for k, n in STAGES],
        "lines": lines,
        "units_total": len(units),
        "units_registered": covered,
        "unregistered": [{"kind": u["kind"], "id": u["id"], "state": u["state"],
                          "detail": u["detail"]} for u in unregistered],
        # 完整单元清单(不截断):页面上的「自动探测到的运行单元」要能对齐 units_total,
        # 否则表里少几行,合规率就无法用肉眼复核
        "units": sorted(({"kind": u["kind"], "id": u["id"], "state": u["state"],
                          "owner": u["owner"], "domain": u.get("domain") or "",
                          "policy": u.get("policy") or "", "detail": u["detail"]}
                         for u in units), key=lambda x: (x["kind"], x["id"])),
        "by_kind": {k: len([u for u in units if u["kind"] == k])
                    for k in ("container", "systemd", "cron", "coolify")},
        "blast_radius": radius[:8],
        "score": round(sum(x["score"] for x in lines) / max(1, len(lines))),
        "heal_rules_ok": heal_ok,
        "cloudflare_note": "Cloudflare 侧没有只读令牌(owner 明确不建),因此 CF 单元"
                           "不做账面枚举,只按登记表做对外可达性实测——这一段如实标为实测而非枚举。",
        "note": "单元来自 Docker / systemd / cron / Coolify 库四路自动探测,"
                "与登记表比对得出合规状态。未登记 = 治理违规,必须补登记。",
        "at": int(time.time()),
    }


def baseline_history(sw):
    """每条业务线的端到端健康分逐小时归档,长期留存,用来看「一直白箱」还是「时好时坏」。"""
    path = os.path.join(DATA_DIR, "baseline_history.json")
    store = load_json(path, {}) or {}
    ep = int(time.time())
    hour = str(ep - ep % 3600)
    slot = store.setdefault(hour, {})
    for ln in sw["lines"]:
        slot[ln["name"]] = ln["score"]                 # 同一小时内后写覆盖:取该小时最后一次观测
    cutoff = ep - 400 * 86400
    store = {h: v for h, v in store.items() if int(h) >= cutoff}
    try:
        with open(path, "w") as f:
            json.dump(store, f)
    except OSError:
        pass
    hours = sorted(store, key=int)[-720:]
    names = [ln["name"] for ln in sw["lines"]]
    return {
        "hours": [int(h) for h in hours],
        "overall": [round(sum(store[h].get(n, 0) for n in store[h]) / max(1, len(store[h])))
                    for h in hours],
        "series": {n: [store[h].get(n) for h in hours] for n in names},
        "since": int(hours[0]) if hours else None,
        "note": "逐小时归档,保留 400 天。分数 = 纵向切片九段的达标情况",
    }


# ---------- 本站真实时访问(读容器 access log,只读、零成本、零 token)----------
LIVE_PATH = lambda: os.path.join(DATA_DIR, "live_traffic.json")
_LOG_RE = re.compile(
    r'\[(\d{2})/(\w{3})/(\d{4}):(\d{2}):(\d{2}):\d{2}\s+([+-]\d{4})\]\s+'
    r'"([A-Z]+)\s+(\S+)[^"]*"\s+(\d{3})\s+\S+\s+"[^"]*"\s+"([^"]*)"')
_MON = {m: i + 1 for i, m in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"])}
# 三类流量分开数,不混为一谈:
#   probe = Gatus 存活探测 / 自愈脚本的 curl(它本身在动 = 监控活着,有价值)
#   self  = 页面自己每 15 秒拉 /data/*(是本站自转,不是访客)
#   human = 其余真实浏览
_PROBE_UA = re.compile(r"Gatus|curl|wget|python-requests|Go-http|monitor|healthcheck", re.I)
_SELF_PATH = re.compile(r"^/(healthz|health|data/|favicon\.ico|robots\.txt)")


def live_traffic(raw=None):
    """逐分钟统计本站各站点的真实访问量。`raw` 仅供测试注入合成日志。

    ★ 为什么要有这个:GitHub 的仓库浏览/克隆**上游一天才发布一次且滞后约 2 天**,
      物理上做不到实时。而本站自己的访问日志是**当下就有**的——所以「实时」这件事
      放在这里做才成立。数据来源 = `docker logs --since`(只读,不落盘、不占磁盘、
      不调任何外部接口),站点域名从 Traefik 标签自动发现,**换容器不用改代码**。

    只统计「请求数」,不统计访客身份:日志里最后一段是 Cloudflare 边缘 IP 而非真实访客 IP,
    拿它算 UV 就是编造,所以不做。
    """
    raw = raw if raw is not None else run(
        "for c in $(docker ps --format '{{.Names}}'); do "
        "h=$(docker inspect \"$c\" --format '{{json .Config.Labels}}' 2>/dev/null "
        "| tr ',' '\\n' | grep -oE 'Host\\(`[^`]+`\\)' | head -1 | sed 's/.*`\\(.*\\)`.*/\\1/'); "
        "[ -z \"$h\" ] && continue; "
        "docker logs --since 3m \"$c\" 2>&1 | grep -E '\\\"(GET|POST|HEAD|PUT|DELETE) [^\\\"]*\\\" [0-9]{3} ' "
        "| sed \"s|^|$h\\t|\"; done", timeout=45)

    store = load_json(LIVE_PATH(), {}) or {}
    minutes = {int(k): v for k, v in (store.get("min") or {}).items()}
    hours = {int(k): v for k, v in (store.get("hour") or {}).items()}
    # 本轮重新数到的分钟要整桶覆盖:上一轮读到的可能是**还没走完的那一分钟**,
    # 累加会重复计数,覆盖才是对的(同一分钟第二次读必然更完整)。
    rebuilt = {}
    for line in raw.splitlines():
        if "\t" not in line:
            continue
        site, rest = line.split("\t", 1)
        m = _LOG_RE.search(rest)
        if not m:
            continue
        dd, mon, yy, hh, mi, tz, meth, path, code, ua = m.groups()
        try:
            t = datetime(int(yy), _MON[mon], int(dd), int(hh), int(mi),
                         tzinfo=timezone(timedelta(hours=int(tz[:3]), minutes=int(tz[0] + tz[3:]))))
        except (KeyError, ValueError):
            continue
        kind = ("p" if _PROBE_UA.search(ua) else
                "s" if _SELF_PATH.match(path) else "h")
        bucket = rebuilt.setdefault(int(t.timestamp()), {})
        cell = bucket.setdefault(site, {"h": 0, "p": 0, "s": 0, "e": 0})
        cell[kind] += 1
        if code[0] == "5" or (code[0] == "4" and code not in ("401", "403")):
            cell["e"] += 1                          # 401/403 是 CF Access 正常拦截,不算错误

    now_ep = int(time.time())
    cur_min = now_ep - now_ep % 60
    for t, b in rebuilt.items():
        minutes[t] = b
    for t in list(minutes):
        if t < now_ep - 86400:                     # 分钟级留 24h
            minutes.pop(t)
    # 小时桶由分钟桶**重算**(幂等):补采或重跑都不会把同一分钟算两遍。
    # 分钟档只留 24h,更早的小时桶是唯一副本,原样保留。
    rebuilt_hours = {}
    for t, b in minutes.items():
        hb = rebuilt_hours.setdefault(t - t % 3600, {})
        for s, cell in b.items():
            acc = hb.setdefault(s, {"h": 0, "p": 0, "s": 0, "e": 0})
            for k in acc:
                acc[k] += cell.get(k, 0)
    floor = min(rebuilt_hours, default=cur_min)
    hours = {t: v for t, v in hours.items() if t < floor and t >= now_ep - 30 * 86400}
    hours.update(rebuilt_hours)

    try:
        with open(LIVE_PATH(), "w") as f:
            json.dump({"min": {str(k): v for k, v in minutes.items()},
                       "hour": {str(k): v for k, v in hours.items()}}, f)
    except OSError:
        pass

    KINDS = ("h", "p", "s", "e")

    def roll(buckets, ts):
        acc = {k: 0 for k in KINDS}
        for t in ts:
            for cell in buckets[t].values():
                for k in KINDS:
                    acc[k] += cell.get(k, 0)
        return acc

    def flatten(buckets, keep):
        out = []
        for t in sorted(buckets):
            if t < keep:
                continue
            acc = {k: 0 for k in KINDS}
            for cell in buckets[t].values():
                for k in KINDS:
                    acc[k] += cell.get(k, 0)
            out.append({"t": t, **acc})
        return out

    sites = sorted({s for b in minutes.values() for s in b})
    span60 = [t for t in minutes if t >= now_ep - 3600]
    per_site = sorted(
        ({"site": s,
          "h60": sum(minutes[t].get(s, {}).get("h", 0) for t in span60),
          "all60": sum(sum(minutes[t].get(s, {}).values()) for t in span60),
          "h24": sum(minutes[t].get(s, {}).get("h", 0) for t in minutes),
          "all24": sum(sum(minutes[t].get(s, {}).values()) for t in minutes),
          "err24": sum(minutes[t].get(s, {}).get("e", 0) for t in minutes)} for s in sites),
        key=lambda x: (-x["h24"], -x["all24"]))
    series = flatten(minutes, now_ep - 10800)
    last5 = [x for x in series if x["t"] >= cur_min - 300]
    r60, r24 = roll(minutes, span60), roll(minutes, list(minutes))
    return {
        "available": bool(sites),
        "sites": per_site,
        "minutes": series[-180:],                  # 3 小时逐分钟
        "hours": flatten(hours, 0)[-720:],         # 30 天逐小时
        "r60": r60, "r24": r24,
        "rpm": round(sum(x["h"] + x["p"] + x["s"] for x in last5) / max(len(last5), 1), 1),
        "peak_min": max((x["h"] + x["p"] + x["s"] for x in series), default=0),
        "at": now_ep,
        "legend": {"h": "真实浏览", "p": "存活探测", "s": "页面自轮询", "e": "错误响应"},
        "note": "本站容器 access log 逐分钟统计(docker logs 只读,不落盘不占磁盘)。"
                "访客/探测/自轮询分开计数;只数请求数不数访客——日志里只有 Cloudflare "
                "边缘 IP,拿它算 UV 就是编造。401/403 是 Access 正常拦截,不计入错误。",
    }


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



# ---------- 全仓全项目关系图(纯派生:0 agent / 0 token / 不新增任何 API 调用)----------
def project_graph(projects, gh):
    """把「已经采到的数据」推导成节点+连线。

    数据全部来自本次快照与 GitHub 采集结果,**不额外请求任何接口、不调用任何模型**。
    另维护 graph_state.json 记录每个节点的首次出现时间,从而能识别
    **新增 / 删除 / 变更**,让前端做动态演示。私有仓只出一个匿名聚合节点,不出名字。
    """
    nodes, edges = {}, []
    def node(nid, label, kind, **meta):
        nodes.setdefault(nid, {"id": nid, "label": label, "kind": kind, **meta})
        return nid
    def edge(a, b, rel):
        if a and b:
            edges.append({"s": a, "t": b, "rel": rel})

    # 供应商层
    v_ovh = node("v:ovh", "OVH VPS-1", "vendor", role="云服务器")
    v_cf = node("v:cf", "Cloudflare", "vendor", role="门口/边缘")
    v_gh = node("v:github", "GitHub", "vendor", role="代码+备份")
    v_oci = node("v:oci", "OCI", "vendor", role="异地备份")

    # 项目层 + 存储层
    for p in projects:
        pid = node("p:" + p["name"], p["name"], "project",
                   status=p.get("status"), url=p.get("url") or "",
                   agent=p.get("agent") or "", deploy=p.get("deploy") or "")
        host = (p.get("host") or "")
        if "OVH" in host:
            edge(pid, v_ovh, "运行在")
        elif "Cloudflare" in host or "Workers" in host:
            edge(pid, v_cf, "运行在")
        for raw in (p.get("db") or "") .split("+"):
            raw = raw.strip()
            if not raw or raw.startswith("无"):
                continue
            sid = node("s:" + raw, raw, "store")
            edge(pid, sid, "数据存于")
            edge(sid, v_cf if ("CF " in raw or "D1" in raw or "R2" in raw) else v_ovh, "属于")
        if p.get("repo"):
            edge(pid, "r:" + p["repo"], "源码在")

    # 代码仓层(公开仓出名字;私有仓只出一个匿名聚合节点)
    if gh and gh.get("available"):
        coup = gh.get("coupling") or {}
        degree = coup.get("degree") or {}
        cdays = gh.get("commit_days") or {}
        # 每仓访问/克隆动能。★ 只取公开仓那几条 —— 私有仓在公开快照里根本不存在,
        #   这里也绝不按名字去补,不变量不放松。
        traf = {t["name"]: t for t in ((gh.get("traffic") or {}).get("per_repo") or [])
                if not t.get("private")}
        for r in gh.get("public_repos", []) or []:
            tr = traf.get(r["name"]) or {}
            langs = r.get("languages") or {}
            tot = sum(langs.values()) or 1
            mix = [{"n": k, "p": round(v / tot * 100, 1)}
                   for k, v in sorted(langs.items(), key=lambda x: -x[1])[:5]]
            d14 = cdays.get(r["name"]) or []
            rid = node("r:" + r["name"], r["name"], "repo",
                       lang=r.get("top_lang") or "", commits30=r.get("commits_30d") or 0,
                       commits7=r.get("commits_7d") or 0, pushed_at=r.get("pushed_at") or "",
                       branches=r.get("branches") or 0, open_pr=r.get("open_pr") or 0,
                       open_issue=r.get("open_issue") or 0,
                       ci_ok=bool(r.get("ci_ok", True)),
                       ci_conclusion=r.get("ci_conclusion") or "",
                       coupling=round(degree.get(r["name"], 0), 3),
                       url=r.get("url") or "", size_kb=r.get("size_kb") or 0,
                       release_bytes=r.get("release_bytes") or 0,
                       default_branch=r.get("default_branch") or "",
                       lang_mix=mix, days14=d14,
                       active_days14=sum(1 for x in d14 if x["c"]),
                       views14=tr.get("views_14d"), views_uniq14=tr.get("views_uniq_14d"),
                       clones14=tr.get("clones_14d"), clones_uniq14=tr.get("clones_uniq_14d"))
            edge(rid, v_gh, "托管在")
        npriv = gh.get("private") or 0
        if npriv:
            pid = node("r:__private__", "%d 个私有仓" % npriv, "repo_private", count=npriv)
            edge(pid, v_gh, "托管在")
        for sp in gh.get("subprojects", []) or []:
            sid = node("sp:%s/%s" % (sp["repo"], sp["project"]), sp["project"], "subproject",
                       path=sp.get("path") or "", commits30=sp.get("commits_30d") or 0)
            edge(sid, "r:" + sp["repo"], "属于")
        # 仓与仓的耦合边(共变 / 同栈),由 collect_github 从逐日提交推导,已做公开安全过滤
        for e in coup.get("edges", []) or []:
            if e.get("rel") == "contains":
                continue                       # 归属关系上面已经画过,别重复
            a = e["s"] if e["s"].startswith("sp:") else "r:" + e["s"]
            b = e["t"] if e["t"].startswith("sp:") else "r:" + e["t"]
            if a in nodes and b in nodes:
                edges.append({"s": a, "t": b, "rel": e["rel"], "w": e.get("w"),
                              "trend": e.get("trend"), "days": e.get("days"),
                              "why": e.get("why")})
    # 备份链
    edge(v_ovh, v_gh, "每日备份")
    edge(v_ovh, v_oci, "每周备份")

    # 只保留两端都存在的连线(避免悬空)
    edges = [e for e in edges if e["s"] in nodes and e["t"] in nodes]

    # ---- 增删改识别 ----
    path = os.path.join(DATA_DIR, "graph_state.json")
    st = load_json(path, {}) or {}
    seen, now = st.get("nodes", {}), int(time.time())
    changes = st.get("changes", [])
    cur_ids = set(nodes)
    for nid, n in nodes.items():
        prev = seen.get(nid)
        sig = "%s|%s" % (n["label"], n["kind"])
        if not prev:
            seen[nid] = {"first": now, "sig": sig}
            n["change"] = "added"
            changes.append({"t": now, "op": "added", "id": nid, "label": n["label"], "kind": n["kind"]})
        else:
            n["first_seen"] = prev.get("first")
            if prev.get("sig") != sig:
                prev["sig"] = sig
                n["change"] = "changed"
                changes.append({"t": now, "op": "changed", "id": nid, "label": n["label"], "kind": n["kind"]})
            elif now - prev.get("first", now) < 900:      # 15 分钟内算“新”
                n["change"] = "added"
    for nid in list(seen):
        if nid not in cur_ids:
            changes.append({"t": now, "op": "removed", "id": nid,
                            "label": seen[nid].get("sig", "").split("|")[0], "kind": "gone"})
            seen.pop(nid, None)
    changes = changes[-60:]
    try:
        with open(path, "w") as f:
            json.dump({"nodes": seen, "changes": changes}, f)
    except Exception:
        pass

    kinds = {}
    for n in nodes.values():
        kinds[n["kind"]] = kinds.get(n["kind"], 0) + 1
    return {
        "generated_at": fmt(now_cn()), "generated_epoch": now,
        "nodes": list(nodes.values()), "edges": edges,
        "counts": {"nodes": len(nodes), "edges": len(edges), "by_kind": kinds},
        "changes": list(reversed(changes))[:20],
        # ★ 画像里的浏览/克隆必须带上游滞后一起显示:GitHub 的 traffic 接口一天只发布一次
        #   且恒定滞后约 2 天,任何采集频率都改不了。不标出来,读者会把两天前的数当成此刻。
        "traffic_freshness": ((gh or {}).get("traffic") or {}).get("freshness") or {},
        "provenance": "由服务器 cron 从既有采集数据纯派生;不调用任何模型、不新增任何外部请求",
    }


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
        hist[tier].setdefault("load", [])           # 15 分钟均载:容量判定要按分位数看,不能看单次
        # 旧档没有 load 列,长度对不上 —— 先左侧补 None 对齐,再写入,否则索引越界
        while len(hist[tier]["load"]) < len(hist[tier]["t"]):
            hist[tier]["load"].insert(0, None)
    ep = int(time.time())
    mem_v, disk_v = host.get("mem_pct"), host.get("disk_pct")
    try:
        load_v = float(host.get("load15") or 0) or None
    except (TypeError, ValueError):
        load_v = None
    m = hist["min"]
    m["t"].append(ep); m["mem"].append(mem_v); m["disk"].append(disk_v); m["load"].append(load_v)
    for k in ("t", "mem", "disk", "load"):
        m[k] = m[k][-1440:]                      # 24h @ 1min
    h = hist["hour"]
    cur_hour = ep - (ep % 3600)
    if not h["t"] or h["t"][-1] != cur_hour:
        h["t"].append(cur_hour); h["mem"].append(mem_v); h["disk"].append(disk_v); h["load"].append(load_v)
    else:
        h["mem"][-1] = mem_v; h["disk"][-1] = disk_v
        h["load"][-1] = max(h["load"][-1] or 0, load_v or 0) or None   # 小时桶取峰值,不掩盖高负载
    for k in ("t", "mem", "disk", "load"):
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
        "live": live_traffic(),
    }
    snap["software"] = software_runtime(projects, snap["github"], backup, cert, ch,
                                        snap["live"], snap["selfheal"], dep)
    snap["baseline"] = baseline_history(snap["software"])
    snap["capacity"] = capacity_advice(host, hist, prices, snap["software"])
    snap["flow"] = flow_state()
    snap["graph"] = project_graph(projects, snap["github"])

    # 关系图单独出一份小文件,供 home 站跨域拉取(比整份 80KB 快照轻得多)
    try:
        gtmp = os.path.join(DATA_DIR, "graph.json.tmp")
        with open(gtmp, "w") as f:
            json.dump(snap["graph"], f, ensure_ascii=False, separators=(",", ":"))
        os.replace(gtmp, os.path.join(DATA_DIR, "graph.json"))
    except Exception:
        pass

    tmp = os.path.join(DATA_DIR, "snapshot.json.tmp")
    with open(tmp, "w") as f:
        json.dump(snap, f, ensure_ascii=False, indent=1)
    os.replace(tmp, os.path.join(DATA_DIR, "snapshot.json"))
    print("snapshot written:", snap["updated_at"], "online", online, "rate", dep["rate"])


if __name__ == "__main__":
    main()
