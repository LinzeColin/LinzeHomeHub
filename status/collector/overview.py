"""Read-only projections for the status dashboard and the public lock widget."""

import json
import math
import os
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

SYDNEY = ZoneInfo("Australia/Sydney")
DAILY_DAYS = 31
LOCK_DAYS = 7
LOCK_MAX_BYTES = 2048


def number(value):
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        return None
    try:
        value = float(value)
    except (ValueError, OverflowError):
        return None
    return value if math.isfinite(value) else None


def percent(value):
    value = number(value)
    return value if value is not None and 0 <= value <= 100 else None


def sydney_day(epoch):
    return datetime.fromtimestamp(epoch, SYDNEY).date()


def running_containers(snapshot):
    host = snapshot.get("host") or {}
    if "containers_running" in host:
        return host["containers_running"]
    # Older snapshots have the exact Docker states in the runtime inventory.
    containers = [u for u in (snapshot.get("software") or {}).get("units", [])
                  if u.get("kind") == "container"]
    if containers:
        return sum(u.get("state") == "running" for u in containers)
    return None


def attention(snapshot):
    """Same alert predicates and ordering as legacy.html's insights()."""
    alerts = []

    def add(level, title, detail=""):
        alerts.append({"lv": level, "t": title, "d": detail})

    def at_least(value, limit):
        value = number(value)
        return value is not None and value >= limit

    projects = snapshot.get("projects") or []
    host = snapshot.get("host") or {}
    ops = snapshot.get("ops") or {}
    github = snapshot.get("github") or {}

    for p in projects:
        if p.get("status") == "down":
            note = f"({p['health_note']})" if p.get("health_note") else ""
            add("bad", f"{p['name']} 探测失败", f"地址 {p.get('url') or '内部服务'} 未返回可用状态{note}")
    for p in projects:
        if p.get("status") == "stale":
            add("warn", f"{p['name']} 业务判据未通过", f"服务在线但{p.get('health_note') or '数据不新鲜'}")

    disk, mem = host.get("disk_pct"), host.get("mem_pct")
    if at_least(disk, 85):
        add("bad", "磁盘吃紧", f"已用 {disk}%,自愈会在 85% 自动清理")
    elif at_least(disk, 75):
        add("warn", "磁盘偏高", f"已用 {disk}%,低于 85% 触发线")
    if at_least(mem, 90):
        add("warn", "内存偏高", f"已用 {mem}%")

    for usage in snapshot.get("usage") or []:
        limit = number(usage.get("limit"))
        if not limit:
            continue
        label = usage["label"]
        if usage.get("bounded"):
            epoch = snapshot.get("updated_epoch")
            latest, max_age = usage.get("latest_epoch"), usage.get("max_age_h")
            if epoch and latest and max_age:
                hours = (epoch - latest) / 3600
                if hours > max_age:
                    add("bad", f"{label} 已 {math.floor(hours + .5)} 小时没更新",
                        f"最新一份备份超过 {max_age} 小时,备份可能停了")
            code = usage.get("last_upload_code")
            if code and not str(code).startswith("2"):
                add("bad", f"{label} 最近一次上传失败", f"GitHub 返回 {code}")
            continue
        used = number(usage.get("used"))
        if used is None:
            continue
        ratio = used / limit * 100
        if ratio >= 80:
            add("bad", f"{label} 接近额度", f"{ratio:.1f}% 已用")
        elif ratio >= 50:
            eta = f" · 预计触顶 {usage['eta_date']}" if usage.get("eta_date") else ""
            add("warn", f"{label} 过半", f"{ratio:.1f}% 已用{eta}")

    cert = ops.get("cert") or {}
    if cert.get("days") is not None and cert["days"] <= 14:
        add("warn", "证书临近到期", f"{cert['date']} · 剩 {cert['days']} 天(自动续期)")
    subscriptions = ops.get("subs") or {}
    for sub in subscriptions.get("items") or []:
        if sub["level"] != "ok":
            action = "已到期" if sub["days"] <= 0 else ("即将到期停机" if sub.get("end_only") else "即将扣费")
            end = " · 到期直接停机" if sub.get("end_only") else ""
            renew = "开着" if sub.get("auto_renew") else "没开"
            add(sub["level"], f"{sub['name']} {action}",
                f"{sub['date']} · 剩 {sub['days']} 天{end} · 自动续费{renew}")
    blind = subscriptions.get("blind") or []
    if blind:
        names = "、".join(sub["name"] for sub in blind)
        add("warn", f"{len(blind)} 条订阅盯不住", f"{names} —— 没登记购买日期，到期前不会有任何提醒")
    backup = ops.get("backup")
    if backup and not backup.get("ok"):
        add("bad", "备份异常", f"上次备份 {backup.get('at') or '无记录'}")
    if github.get("available") and at_least(github.get("ci_fail"), 1):
        add("warn", f"{github['ci_fail']} 个仓 CI 失败", "默认分支最近一次运行未通过")
    for rule in (snapshot.get("selfheal") or {}).get("rules") or []:
        if not rule.get("armed"):
            add("warn", f"自愈未就绪:{rule['name']}", rule.get("detail") or "")

    # Snapshot age is evaluated by the reader. It cannot be overdue when written.
    capacity = snapshot.get("capacity") or {}
    if capacity.get("level") in ("bad", "warn"):
        add(capacity["level"], ("容量告急:" if capacity["level"] == "bad" else "容量紧张:")
            + capacity["verdict"], capacity.get("why") or "")
    units = (snapshot.get("software") or {}).get("unregistered") or []
    if units:
        add("bad", f"{len(units)} 个运行单元未登记", "部署即登记:主机上真在跑但没有归属,属治理违规")
    flow = snapshot.get("flow") or {}
    unregistered = flow.get("unregistered") or []
    if unregistered:
        add("warn", f"{len(unregistered)} 个项目未登记业务流", "有治理文件却没发布 flow.yaml 到 main")
    totals = flow.get("totals") or {}
    blocked, missing, unbuilt = (totals.get(k) or 0 for k in ("blocked", "blocked_by_input", "not_built"))
    if blocked + missing + unbuilt:
        add("bad", f"业务流 {blocked + missing + unbuilt} 格不通",
            f"等你给材料 {missing} · 断了 {blocked} · 没做 {unbuilt}")
    mismatch, coupling = (totals.get(k) or 0 for k in ("mismatch", "coupling_violation"))
    if mismatch + coupling:
        add("warn", f"业务流 {mismatch + coupling} 格可疑", f"自报≠实测 {mismatch} · 违反耦合 {coupling}")
    measurable, verified = flow.get("measurable_total") or 0, flow.get("verified_total") or 0
    if measurable and verified * 2 < measurable:
        add("warn", "业务流实测覆盖不足一半", f"{verified}/{measurable} 格可测的有机器实测,其余仅被测方自报")
    return sorted(alerts, key=lambda alert: {"bad": 0, "warn": 1}.get(alert["lv"], 2))


def record_day(history, epoch, snapshot):
    """Increment a Sydney calendar day using this minute's collected sample."""
    daily = history.setdefault("daily_sydney", {})
    today = sydney_day(epoch)
    cutoff = (today - timedelta(days=DAILY_DAYS - 1)).isoformat()
    for key in list(daily):
        if key < cutoff:
            del daily[key]
    day = daily.setdefault(today.isoformat(), {"first_epoch": epoch})
    if day.get("last_epoch", -60) // 60 >= epoch // 60:
        return
    day["last_epoch"] = epoch
    host = snapshot["host"]
    for prefix, value in (("mem", percent(host.get("mem_pct"))),
                          ("load", number(host.get("load15")))):
        if value is not None:
            day[f"{prefix}_sum"] = day.get(f"{prefix}_sum", 0) + value
            day[f"{prefix}_samples"] = day.get(f"{prefix}_samples", 0) + 1
            day[f"{prefix}_peak"] = max(day.get(f"{prefix}_peak", value), value)
            if prefix == "mem":
                day["mem_last"] = value
    for key, value in (("disk_pct", percent(host.get("disk_pct"))),
                       ("disk_used_b", host.get("disk_used_b")),
                       ("containers_running", running_containers(snapshot))):
        if value is not None:
            day[key] = value
    # The probe rate uses the same numerator and denominator as the legacy summary.
    online, sep, total = snapshot["summary"].get("services_online", "").partition("/")
    if sep and online.isdecimal() and total.isdecimal() and int(total) > 0 and int(online) <= int(total):
        day["probes_up"] = day.get("probes_up", 0) + int(online)
        day["probes_total"] = day.get("probes_total", 0) + int(total)


def _historical_samples(history, start, end, epoch):
    """Recover old daily points from actual minute/hour samples, never UTC day buckets."""
    days = {}
    for tier in ("hour", "min"):
        series = history.get(tier) or {}
        for index, t in enumerate(series.get("t") or []):
            if not isinstance(t, int) or t > epoch:
                continue
            date = sydney_day(t)
            if not start <= date <= end:
                continue
            row = days.setdefault(date, {"disk_pct": None, "mem_peak_pct": None, "disk_epoch": -1})
            for field in ("disk", "mem"):
                values = series.get(field) or []
                value = percent(values[index]) if index < len(values) else None
                if value is None:
                    continue
                if field == "mem":
                    peak = row["mem_peak_pct"]
                    row["mem_peak_pct"] = max(value, peak) if peak is not None else value
                elif t >= row["disk_epoch"]:
                    row.update(disk_pct=value, disk_epoch=t)
    return days


def build_lock(snapshot, history):
    """Allowlist only numeric readings and seven calendar date keys."""
    epoch = int(snapshot["updated_epoch"])
    end = sydney_day(epoch)
    start = end - timedelta(days=LOCK_DAYS - 1)
    old = _historical_samples(history, start, end, epoch)
    daily = history.get("daily_sydney") or {}
    points = []
    for offset in range(LOCK_DAYS):
        day = start + timedelta(days=offset)
        date = day.isoformat()
        measured = daily.get(date) or {}
        fallback = old.get(day) or {}
        disk = percent(measured.get("disk_pct"))
        peaks = [value for value in (percent(measured.get("mem_peak")), fallback.get("mem_peak_pct"))
                 if value is not None]
        points.append({"date": date, "disk_pct": disk if disk is not None else fallback.get("disk_pct"),
                       "mem_peak_pct": max(peaks) if peaks else None})

    host = snapshot.get("host") or {}
    count = number(running_containers(snapshot))
    alerts = snapshot.get("attention")
    if alerts is None:
        alerts = attention(snapshot)
    return {
        "updated_epoch": epoch,
        "mem_pct": percent(host.get("mem_pct")),
        "disk_pct": percent(host.get("disk_pct")),
        "load1": number(host.get("load1")),
        "containers_running": int(count) if count is not None and count.is_integer() and count >= 0 else None,
        "pending_count": sum(alert.get("lv") == "bad" for alert in alerts),
        "days": points,
    }


def write_json_atomic(path, document, max_bytes=None):
    data = json.dumps(document, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
    if max_bytes is not None and len(data) > max_bytes:
        raise ValueError(f"{Path(path).name} exceeds {max_bytes} bytes")
    path = Path(path)
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=f".{path.name}.", delete=False) as tmp:
        temp_path = Path(tmp.name)
        try:
            tmp.write(data)
            tmp.close()
            temp_path.chmod(0o644)
            os.replace(temp_path, path)
        finally:
            temp_path.unlink(missing_ok=True)


def write_lock(path, snapshot, history):
    write_json_atomic(path, build_lock(snapshot, history), max_bytes=LOCK_MAX_BYTES)
