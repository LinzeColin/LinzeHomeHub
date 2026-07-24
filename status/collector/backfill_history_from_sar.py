#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
一次性工具:从系统 sysstat/sar 日志倒灌**内存**历史到 status 的 history.json。
- 内存口径与采集器一致:used% = (总量 - 可用)/总量(sar 的 kbavail 与 free 一致)。
- 磁盘文件系统使用率 sar 默认没采(sar -F 空),故不倒灌;磁盘从采集器上线起累积。
- 幂等:按小时桶/时间戳去重合并,已有的 live 值(带 disk)优先。
用法(OVH 主机):python3 backfill_history_from_sar.py
"""
import subprocess, re, os, json, glob, time
from datetime import datetime, timezone

SDIR = "/var/log/sysstat"
HF = os.environ.get("STATUS_HISTORY", "/srv/linze/apps/status/data/history.json")


def file_date(fn):
    m = re.search(r'(\d{2})$', os.path.basename(fn))
    if not m:
        return None
    mt = datetime.fromtimestamp(os.path.getmtime(fn), timezone.utc)
    return (mt.year, mt.month, int(m.group(1)))


def parse(fn):
    ymd = file_date(fn)
    if not ymd:
        return []
    y, mo, day = ymd
    out = subprocess.run("LC_ALL=C sar -r -f %s" % fn, shell=True,
                         capture_output=True, text=True).stdout
    if "kbmemfree" not in out:
        out = open(fn, errors='ignore').read()
    pts, inmem = [], False
    for line in out.splitlines():
        if "kbmemfree" in line:
            inmem = True
            continue
        if not inmem:
            continue
        t = line.split()
        if len(t) < 5 or not re.match(r'^\d\d:\d\d:\d\d$', t[0]):
            if "Average" in line or line.strip() == "":
                inmem = False
            continue
        try:
            kbavail, kbmemused, pct = float(t[2]), float(t[3]), float(t[4])
            if pct <= 0:
                continue
            total = kbmemused / (pct / 100.0)
            used = round((total - kbavail) / total * 100, 1)
            hh, mm, ss = (int(x) for x in t[0].split(':'))
            ep = int(datetime(y, mo, day, hh, mm, ss, tzinfo=timezone.utc).timestamp())
            pts.append((ep, used))
        except Exception:
            pass
    return pts


def main():
    allpts = sorted(set(p for fn in glob.glob(SDIR + "/sa*") for p in parse(fn)))
    if not allpts:
        print("no sar data")
        return
    now = int(time.time())
    hour = {}
    for ep, u in allpts:
        hour[ep - (ep % 3600)] = u
    minb = [(ep, u) for ep, u in allpts if ep >= now - 86400]
    try:
        h = json.load(open(HF))
    except Exception:
        h = {"min": {"t": [], "mem": [], "disk": []}, "hour": {"t": [], "mem": [], "disk": []}}

    def tup(s):
        return list(zip(s.get("t", []), s.get("mem", []), s.get("disk", [])))

    mh = {hb: (m, None) for hb, m in hour.items()}
    for t, m, d in tup(h.get("hour", {})):
        mh[t - (t % 3600)] = (m, d)
    hs = sorted(mh.items())[-744:]
    h["hour"] = {"t": [k for k, _ in hs], "mem": [v[0] for _, v in hs], "disk": [v[1] for _, v in hs]}

    mm = {ep: (u, None) for ep, u in minb}
    for t, m, d in tup(h.get("min", {})):
        mm[t] = (m, d)
    ms = sorted(mm.items())[-1440:]
    h["min"] = {"t": [k for k, _ in ms], "mem": [v[0] for _, v in ms], "disk": [v[1] for _, v in ms]}

    day = {}
    for ep, u in allpts:
        day[ep - (ep % 86400)] = u
    md = {db: (m, None) for db, m in day.items()}
    for t, m, d in tup(h.get("day", {})):
        md[t - (t % 86400)] = (m, d)
    ds = sorted(md.items())            # 天级别不截断
    h["day"] = {"t": [k for k, _ in ds], "mem": [v[0] for _, v in ds], "disk": [v[1] for _, v in ds]}

    json.dump(h, open(HF, "w"))
    print("backfilled hour=%d min=%d  mem %s%% -> %s%%" %
          (len(h["hour"]["t"]), len(h["min"]["t"]), h["hour"]["mem"][0], h["hour"]["mem"][-1]))


if __name__ == "__main__":
    main()
