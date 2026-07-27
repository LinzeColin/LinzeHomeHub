#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GitHub Engineering Plane 采集器 —— 双层设计,兼顾「1 分钟新鲜度」与「API 预算安全」。

  fast(默认,cron 每 1 分钟):**单条 GraphQL 请求**,cost=1 点、约 2.5 秒。
      拿:仓库清单/可见性/占用/分支数/PR/Issue/7d·30d 提交/CI 结论/语言字节
        + **365 天贡献日历(贡献网格)**。
      每小时 60 点 / 预算 5000 点 = 1.2%,远比原来 90 次 REST(48 秒)安全。
  deep(cron 每 60 分钟):REST 深采,补 GraphQL 拿不到或被令牌拒绝的部分——
      release 资产字节、monorepo 子项目 path 提交、Issue 精确数(search 排除 PR)、
      以及 GraphQL `viewer.repositories` 看不见的仓(实测少 1 个私有仓)。

合并策略 = last-known-good:canonical 私有档 private/github.json 常驻,
fast 只覆盖它认识的字段;它看不到的仓/字段保留 deep 的旧值并标 stale,绝不清零。
公开面 data/github_public.json 由 canonical 派生,**永不含私有仓名**。
Traffic/Billing 令牌 403 时如实标 UNAVAILABLE,绝不编造。
"""
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone, timedelta

CN = timezone(timedelta(hours=8))
API = "https://api.github.com"
GQL = "https://api.github.com/graphql"
UA = "linze-status-github-monitor"
APIV = "2022-11-28"

APP_DIR = os.environ.get("STATUS_APP_DIR", "/srv/linze/apps/status")
DATA_DIR = os.path.join(APP_DIR, "data")
PUBLIC_OUT = os.path.join(DATA_DIR, "github_public.json")
PRIVATE_OUT = os.environ.get("STATUS_GH_PRIVATE", os.path.join(APP_DIR, "private", "github.json"))

# monorepo 子项目权威登记(只读;GitHub 只知 repo 不知子项目身份)
SUBPROJECTS = {
    "KMOS": [
        {"project": "KMFA", "path": "KMFA/"},
        {"project": "KMIDS", "path": "KM_IDSystem/"},
        {"project": "whkmSalary", "path": "whkmSalary/"},
        {"project": "KMDatabase", "path": "KMDatabase/"},
    ],
    "MetaDatabase": [
        {"project": "Alpha", "path": "Alpha/"},
        {"project": "EEI", "path": "EEI/"},
        {"project": "ADP", "path": "arxiv-daily-push/"},
    ],
}


def _fmt(dt):
    return dt.strftime("%Y-%m-%d %H:%M")


def _iso(days_ago):
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _hdr(token):
    return {"Authorization": "Bearer " + token, "User-Agent": UA,
            "X-GitHub-Api-Version": APIV, "Accept": "application/vnd.github+json"}


def _get(url, token, timeout=20):
    try:
        req = urllib.request.Request(url, headers=_hdr(token))
        resp = urllib.request.urlopen(req, timeout=timeout)
        raw = resp.read()
        return (json.loads(raw) if raw else None), resp.headers
    except Exception:
        return None, None


def _probe(url, token):
    try:
        req = urllib.request.Request(url, headers=_hdr(token))
        return urllib.request.urlopen(req, timeout=12).getcode()
    except urllib.error.HTTPError as e:
        return e.code
    except Exception:
        return 0


def load_json(path, default=None):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return default


def _atomic_write(path, obj, mode=None):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
    if mode:
        try:
            os.chmod(path, mode)
        except Exception:
            pass


# ======================= FAST 层:单条 GraphQL =======================
FAST_QUERY = """
query{
  rateLimit{ cost remaining limit }
  viewer{
    login name
    contributionsCollection{
      contributionCalendar{ totalContributions
        weeks{ contributionDays{ date contributionCount } } } }
    repositories(first:100, ownerAffiliations:OWNER, orderBy:{field:PUSHED_AT,direction:DESC}){
      totalCount
      nodes{
        name isPrivate isArchived diskUsage pushedAt url
        primaryLanguage{ name }
        languages(first:8, orderBy:{field:SIZE,direction:DESC}){ edges{ size node{ name } } }
        refs(refPrefix:"refs/heads/"){ totalCount }
        defaultBranchRef{ name target{ ... on Commit{
          h7: history(since:"%s"){ totalCount }
          h30: history(since:"%s"){ totalCount }
          statusCheckRollup{ state } } } }
      }
    }
  }
}"""


def _gql(token, query):
    try:
        req = urllib.request.Request(GQL, data=json.dumps({"query": query}).encode(),
                                     headers={"Authorization": "Bearer " + token,
                                              "User-Agent": UA,
                                              "Content-Type": "application/json"})
        return json.loads(urllib.request.urlopen(req, timeout=40).read())
    except Exception:
        return None


def _counts_query(token, names):
    """PR/Issue 计数单独一条:**按仓起别名**。
    某仓无权限时只会让该别名为 null,不会像放进 nodes 里那样把整个仓节点吞掉。"""
    if not names:
        return {}
    parts = ['r%d: repository(owner:"LinzeColin", name:"%s"){ '
             'pullRequests(states:OPEN){totalCount} issues(states:OPEN){totalCount} }' % (i, n)
             for i, n in enumerate(names)]
    d = _gql(token, "query{ " + " ".join(parts) + " }")
    out = {}
    if not d:
        return out
    data = d.get("data") or {}
    for i, n in enumerate(names):
        node = data.get("r%d" % i)
        if not node:
            continue                                  # 无权限/不可见 -> 保留 last-known-good
        out[n] = {"open_pr": (node.get("pullRequests") or {}).get("totalCount"),
                  "open_issue": (node.get("issues") or {}).get("totalCount")}
    return out


def gather_fast(token):
    """GraphQL 快层(基础查询 + 计数查询,合计 cost 约 2 点)。失败返回 None 保留旧值。"""
    d = _gql(token, FAST_QUERY % (_iso(7), _iso(30)))
    if not d:
        return None
    data = d.get("data") or {}
    v = data.get("viewer") or {}
    if not v:
        return None
    rl = data.get("rateLimit") or {}

    # 贡献网格(365 天)
    cal = ((v.get("contributionsCollection") or {}).get("contributionCalendar")) or {}
    days = []
    for w in cal.get("weeks", []) or []:
        for dd in w.get("contributionDays", []) or []:
            days.append({"d": dd["date"], "c": dd.get("contributionCount", 0)})
    calendar = {"total": cal.get("totalContributions"), "days": days,
                "max": max((x["c"] for x in days), default=0)}

    repos = {}
    node_list = ((v.get("repositories") or {}).get("nodes")) or []
    for r in node_list:
        if not r:
            continue
        tgt = ((r.get("defaultBranchRef") or {}).get("target")) or {}
        langs = {}
        for e in ((r.get("languages") or {}).get("edges")) or []:
            nd = e.get("node") or {}
            if nd.get("name"):
                langs[nd["name"]] = e.get("size", 0)
        repos[r["name"]] = {
            "name": r["name"], "private": r.get("isPrivate"), "archived": r.get("isArchived"),
            "size_kb": r.get("diskUsage"), "pushed_at": (r.get("pushedAt") or "")[:10],
            "url": r.get("url"), "default_branch": (r.get("defaultBranchRef") or {}).get("name"),
            "top_lang": ((r.get("primaryLanguage") or {}).get("name")) or "—",
            "languages": langs or None,
            "branches": (r.get("refs") or {}).get("totalCount"),
            "commits_7d": (tgt.get("h7") or {}).get("totalCount"),
            "commits_30d": (tgt.get("h30") or {}).get("totalCount"),
            "ci_state": (tgt.get("statusCheckRollup") or {}).get("state"),
        }
    for name, c in _counts_query(token, list(repos)).items():
        repos[name].update(c)
    return {"repos": repos, "calendar": calendar,
            "rate": {"remaining": rl.get("remaining"), "limit": rl.get("limit"), "cost": rl.get("cost")},
            "account": {"login": v.get("login"), "name": v.get("name")},
            "repo_total": (v.get("repositories") or {}).get("totalCount")}


# ======================= DEEP 层:REST 深采 =======================
def _count_via_link(path, token):
    sep = "&" if "?" in path else "?"
    data, hdrs = _get(API + path + sep + "per_page=1", token)
    if hdrs is None:
        return None
    m = re.search(r'[?&]page=(\d+)>;\s*rel="last"', hdrs.get("Link", "") or "")
    if m:
        return int(m.group(1))
    return len(data) if isinstance(data, list) else 0


def _search_count(q, token):
    data, _ = _get(API + "/search/issues?q=" + urllib.parse.quote(q) + "&per_page=1", token)
    return data.get("total_count") if isinstance(data, dict) else None


def _deep_repo(full, default_branch, token):
    rel, _ = _get(f"{API}/repos/{full}/releases?per_page=100", token)
    rel_bytes = 0
    if isinstance(rel, list):
        for r in rel:
            for a in r.get("assets", []) or []:
                rel_bytes += a.get("size", 0)
    runs, _ = _get(f"{API}/repos/{full}/actions/runs?branch={default_branch}&per_page=10", token)
    ci = {"last_conclusion": None, "pass_rate": None, "last_at": None, "has_ci": False}
    if isinstance(runs, dict):
        rr = runs.get("workflow_runs", []) or []
        if rr:
            ci["has_ci"] = True
            ci["last_conclusion"] = rr[0].get("conclusion")
            ci["last_at"] = (rr[0].get("updated_at") or "")[:16].replace("T", " ")
            done = [x for x in rr if x.get("conclusion")]
            if done:
                ci["pass_rate"] = round(sum(1 for x in done if x["conclusion"] == "success") / len(done) * 100)
    return {"release_bytes": rel_bytes, "ci": ci,
            "open_pr": _count_via_link(f"/repos/{full}/pulls?state=open", token),
            "open_issue": _search_count(f"repo:{full} type:issue state:open", token)}


def _subprojects_for(repo, default_branch, token):
    out = []
    for sp in SUBPROJECTS.get(repo, []):
        p = urllib.parse.quote(sp["path"])
        c30 = _count_via_link(
            f"/repos/LinzeColin/{repo}/commits?sha={default_branch}&path={p}&since={_iso(30)}", token)
        last, _ = _get(f"{API}/repos/LinzeColin/{repo}/commits?sha={default_branch}&path={p}&per_page=1", token)
        last_at = None
        if isinstance(last, list) and last:
            last_at = (last[0].get("commit", {}).get("committer", {}).get("date") or "")[:10]
        out.append({"repo": repo, "project": sp["project"], "path": sp["path"],
                    "commits_30d": c30, "last_commit_at": last_at})
    return out


def _day(offset):
    return (datetime.now(timezone.utc) - timedelta(days=offset)).strftime("%Y-%m-%d")


def _runs_count(full, token, query=""):
    d, _ = _get(f"{API}/repos/{full}/actions/runs?per_page=1{query}", token)
    if isinstance(d, dict):
        return d.get("total_count")
    return None


def gather_actions(token, repos):
    """Actions 用量与**收费风险**。

    GitHub 计费规则:**公开仓的标准 runner 免费且不限量**;只有私有仓消耗
    免费账户每月 2000 分钟额度。已实测公开仓 run 的 `timing.billable` 为 0 ms,
    据此把公开仓计费分钟如实记为 0,不做估算。私有仓当前令牌 403 -> 标 UNAVAILABLE。
    """
    since = _day(30)
    by_repo, daily = [], {}
    pub_runs = priv_unreadable = 0
    concl_total = {"success": 0, "failure": 0, "cancelled": 0, "skipped": 0}
    billable_ms_sampled, sampled_runs = 0, 0

    for r in repos:
        name, full, private = r["name"], r.get("full_name") or ("LinzeColin/" + r["name"]), r.get("private")
        total = _runs_count(full, token, f"&created=%3E%3D{since}")
        if total is None:
            if private:
                priv_unreadable += 1
            continue
        row = {"name": name, "private": bool(private), "runs_30d": total}
        for c in ("success", "failure", "cancelled", "skipped"):
            n = _runs_count(full, token, f"&created=%3E%3D{since}&status={c}")
            row[c] = n
            if isinstance(n, int):
                concl_total[c] += n
        if not private:
            pub_runs += total
        by_repo.append(row)
        # 近 14 天日频(只对真有运行的仓查,省请求)
        if total:
            for i in range(13, -1, -1):
                d = _day(i)
                n = _runs_count(full, token, f"&created={d}")
                if isinstance(n, int):
                    daily[d] = daily.get(d, 0) + n
        # 计费抽样:公开仓取最近 3 次 run 的 timing 核实是否真为 0
        if not private and total:
            runs, _ = _get(f"{API}/repos/{full}/actions/runs?per_page=3&status=completed", token)
            for wr in (runs or {}).get("workflow_runs", [])[:3] if isinstance(runs, dict) else []:
                t, _ = _get(f"{API}/repos/{full}/actions/runs/{wr['id']}/timing", token)
                if isinstance(t, dict):
                    billable_ms_sampled += sum(v.get("total_ms", 0) for v in (t.get("billable") or {}).values())
                    sampled_runs += 1

    by_repo.sort(key=lambda x: -(x["runs_30d"] or 0))
    days = [{"d": _day(i), "c": daily.get(_day(i), 0)} for i in range(13, -1, -1)]
    return {
        "window_days": 30,
        "public_runs_30d": pub_runs,
        "conclusions": concl_total,
        "by_repo": by_repo,
        "daily14": days,
        "daily_max": max((x["c"] for x in days), default=0),
        "public_billable_minutes": 0,
        "billable_verified": {"sampled_runs": sampled_runs, "billable_ms": billable_ms_sampled},
        "private_repos_unreadable": priv_unreadable,
        "private_status": ("UNAVAILABLE (需 Actions:read 授权,当前 403)" if priv_unreadable
                           else "无私有仓 Actions"),
        "free_tier_minutes": 2000,
        "cost_note": "公开仓标准 runner 免费不限量(已实测 timing 计费为 0);免费账户 2000 分钟/月额度只被私有仓消耗。",
    }


def gather_throughput(token):
    """PR / Issue 吞吐(整账号聚合,search API,每条 1 次请求)。"""
    d30, d7 = _day(30), _day(7)
    q = lambda s: _search_count(s, token)
    return {
        "pr_created_30d": q(f"owner:LinzeColin is:pr created:>={d30}"),
        "pr_merged_30d": q(f"owner:LinzeColin is:pr is:merged merged:>={d30}"),
        "pr_merged_7d": q(f"owner:LinzeColin is:pr is:merged merged:>={d7}"),
        "pr_open": q("owner:LinzeColin is:pr is:open"),
        "issue_created_30d": q(f"owner:LinzeColin is:issue created:>={d30}"),
        "issue_closed_30d": q(f"owner:LinzeColin is:issue is:closed closed:>={d30}"),
        "issue_open": q("owner:LinzeColin is:issue is:open"),
        "window": {"d30": d30, "d7": d7},
    }


# ======================= Traffic(GitHub 只留 14 天,必须归档)=======================
# ★ 逐仓归档**必须放私有目录**:它按仓名分桶,天然含私有仓名。
#   曾经放在 DATA_DIR(= nginx 公开根),等于把私有仓名和它们的流量数字挂在公网上。
#   凡是「按仓名分桶」的原始档,一律 PRIVATE_DIR;公开面只吃派生后的聚合结果。
PRIVATE_DIR = os.path.dirname(PRIVATE_OUT)
TRAFFIC_HISTORY = os.path.join(PRIVATE_DIR, "traffic_history.json")
COMMIT_HISTORY = os.path.join(PRIVATE_DIR, "commit_history.json")
LEGACY_TRAFFIC_HISTORY = os.path.join(DATA_DIR, "traffic_history.json")
PRIVATE_NAMES_GUARD = {"Private-Database", "Governance", "KMFA-App-State-Backup"}


def _load_traffic_history():
    """读私有归档;首次运行时把历史数据从公开目录搬过来并**删掉公开副本**。"""
    hist = load_json(TRAFFIC_HISTORY, None)
    if hist is None:
        hist = load_json(LEGACY_TRAFFIC_HISTORY, {}) or {}
        if hist:
            _atomic_write(TRAFFIC_HISTORY, hist, 0o640)
    if os.path.exists(LEGACY_TRAFFIC_HISTORY):
        try:
            os.remove(LEGACY_TRAFFIC_HISTORY)
            print("traffic: removed publicly-served legacy archive")
        except OSError as e:
            print("traffic: cannot remove legacy archive: %s" % e)
    return hist or {}


def _traffic_freshness(hist, arrivals):
    """把「GitHub 上游到底给到哪一天」算清楚,并据此估下一批到达时间。

    ★ 这是本页最容易被误解成 bug 的地方:实测 GitHub 的 traffic 接口**恒定滞后约 2 天**
      (今天 07-27,窗口只到 07-25),且**一天才发布一次**。所以逐日浏览/克隆
      在物理上就不可能实时,任何采集频率都改变不了。我们能做的是:
        1) 把滞后天数与「上游截止到哪天」如实标出来,别让人以为页面坏了;
        2) 把还没发布的日子标成「待发布」,而不是画成 0(画 0 就是编造数据);
        3) 记录每一天数据**真正到达的时刻**,据此估下一批 ETA,并让前端高亮「刚到达」。
    """
    latest = ""
    for h in hist.values():
        for key in ("views", "clones"):
            for d in h.get(key, {}):
                if d > latest:
                    latest = d
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    pending = []
    if latest:
        cur = datetime.strptime(latest, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        end = datetime.strptime(today, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        while cur < end:
            cur += timedelta(days=1)
            pending.append(cur.strftime("%Y-%m-%d"))
    # 观测到的到达间隔中位数 -> ETA(样本不足则退回 24h,并如实标成 estimated)
    ats = [a["at"] for a in arrivals[-8:]]
    gaps = sorted(b - a for a, b in zip(ats, ats[1:]) if b > a)
    period = gaps[len(gaps) // 2] if gaps else 86400
    last_at = ats[-1] if ats else None
    return {
        "upstream_through": latest or None,
        "lag_days": len(pending),
        "pending_days": pending,
        "last_arrival_epoch": last_at,
        "next_eta_epoch": (last_at + period) if last_at else None,
        "arrival_period_sec": period,
        "arrivals": arrivals[-30:],
        "why": "GitHub 的 traffic 接口每天只发布一次且滞后约 2 天,这是上游限制,"
               "不是本站故障。想看真正实时的访问,请看「本站实时访问」。",
    }


def gather_traffic(token, repos):
    """访问流量。**GitHub 只返回最近 14 天,过期永久丢失**,所以逐日归档进
    traffic_history.json(只增不减),这样时间越久历史越完整。

    同时记录每一天**首次被我们看到的时刻**(`t`),用来算上游发布节奏与「刚到达」高亮。
    """
    hist = _load_traffic_history()
    arrivals = list((hist.pop("__arrivals__", None) or []))   # 与逐仓数据同档保存,不额外开文件
    known = {d["d"] for d in arrivals}
    now_epoch = int(time.time())
    per_repo, unreadable = [], 0
    for r in repos:
        name = r["name"]
        full = r.get("full_name") or ("LinzeColin/" + name)
        v, _ = _get(f"{API}/repos/{full}/traffic/views", token)
        c, _ = _get(f"{API}/repos/{full}/traffic/clones", token)
        if not isinstance(v, dict) and not isinstance(c, dict):
            unreadable += 1
            continue
        h = hist.setdefault(name, {"views": {}, "clones": {}})
        for key, data, field in (("views", v, "views"), ("clones", c, "clones")):
            for row in ((data or {}).get(field) or []):
                d = (row.get("timestamp") or "")[:10]
                if not d:
                    continue
                prev = h[key].get(d) or {}
                # t 只在第一次见到这一天时落定,之后即使数值被上游修订也不改
                h[key][d] = {"c": row.get("count", 0), "u": row.get("uniques", 0),
                             "t": prev.get("t", now_epoch)}
                if d not in known:
                    known.add(d)
                    arrivals.append({"d": d, "at": now_epoch})
        paths, _ = _get(f"{API}/repos/{full}/traffic/popular/paths", token)
        refs, _ = _get(f"{API}/repos/{full}/traffic/popular/referrers", token)
        per_repo.append({
            "name": name, "private": bool(r.get("private")),
            "views_14d": (v or {}).get("count"), "views_uniq_14d": (v or {}).get("uniques"),
            "clones_14d": (c or {}).get("count"), "clones_uniq_14d": (c or {}).get("uniques"),
            "top_paths": [{"p": x.get("path"), "c": x.get("count")} for x in (paths or [])[:5]]
                         if isinstance(paths, list) else [],
            "referrers": [{"r": x.get("referrer"), "c": x.get("count")} for x in (refs or [])[:5]]
                         if isinstance(refs, list) else [],
        })
    # 归档保 400 天
    cutoff = (datetime.now(timezone.utc) - timedelta(days=400)).strftime("%Y-%m-%d")
    for name, h in hist.items():
        for key in ("views", "clones"):
            h[key] = {d: x for d, x in h[key].items() if d >= cutoff}
    arrivals = sorted((a for a in arrivals if a["d"] >= cutoff), key=lambda a: a["d"])
    fresh = _traffic_freshness(hist, arrivals)
    hist["__arrivals__"] = arrivals
    _atomic_write(TRAFFIC_HISTORY, hist, 0o640)
    hist.pop("__arrivals__", None)

    # 逐日汇总(只汇总公开仓,供公开面用)
    pub = {r["name"] for r in per_repo if not r["private"]}
    daily = {}
    for name, h in hist.items():
        if name not in pub:
            continue
        for key in ("views", "clones"):
            for d, x in h[key].items():
                slot = daily.setdefault(d, {"v": 0, "c": 0, "t": x.get("t")})
                slot["v" if key == "views" else "c"] += x.get("c", 0)
    days = sorted(daily)
    per_repo.sort(key=lambda x: -(x.get("views_14d") or 0))
    return {
        "per_repo": per_repo,
        "archived_days": len(days),
        "archive_since": days[0] if days else None,
        "daily": [{"d": d, "v": daily[d]["v"], "c": daily[d]["c"], "t": daily[d].get("t")}
                  for d in days[-90:]],
        "totals_14d": {
            "views": sum(r.get("views_14d") or 0 for r in per_repo if not r["private"]),
            "clones": sum(r.get("clones_14d") or 0 for r in per_repo if not r["private"]),
        },
        "unreadable_repos": unreadable,
        "freshness": fresh,
        "checked_at": int(time.time()),
        "note": "GitHub 只提供最近 14 天,本站逐日归档累积,越久越完整",
    }


# ======================= 逐日提交 & 仓库耦合(仓库宇宙的数据层)=======================
def _commit_days_query(names, back=14):
    """每仓 × 每天一个 `history(since,until){totalCount}` 别名。

    **按仓起别名**(和 _counts_query 同一条教训):某个仓不可见时只让该别名为 null,
    不会把整份结果吞掉。实测 9 仓 × 14 天 = 126 个连接,`rateLimit.cost` 仍然是 **1**——
    因为 totalCount 不取节点,不计入 GraphQL 的节点成本。
    """
    today = datetime.now(timezone.utc).date()
    blocks = []
    for i, n in enumerate(names):
        days = " ".join(
            'd%d: history(since:"%sT00:00:00Z", until:"%sT00:00:00Z"){totalCount}'
            % (k, today - timedelta(days=k), today - timedelta(days=k - 1))
            for k in range(back)
        )
        blocks.append('r%d: repository(owner:"LinzeColin", name:"%s"){ defaultBranchRef{ '
                      'target{ ... on Commit { %s } } } }' % (i, n, days))
    return "query{ rateLimit{cost remaining} " + " ".join(blocks) + " }"


def gather_commit_days(token, names, back=14):
    """逐仓逐日提交数,归档进 commit_history.json(私有:按仓名分桶)。只增不减保 400 天。"""
    d = _gql(token, _commit_days_query(names, back))
    hist = load_json(COMMIT_HISTORY, {}) or {}
    if not d:
        return hist
    data = d.get("data") or {}
    today = datetime.now(timezone.utc).date()
    for i, n in enumerate(names):
        node = data.get("r%d" % i)
        if not node:
            continue                       # 该仓不可见:保留旧值,不清零
        tgt = ((node.get("defaultBranchRef") or {}).get("target")) or {}
        bucket = hist.setdefault(n, {})
        for k in range(back):
            cell = tgt.get("d%d" % k)
            if isinstance(cell, dict) and cell.get("totalCount") is not None:
                bucket[str(today - timedelta(days=k))] = cell["totalCount"]
    cutoff = str(today - timedelta(days=400))
    for n in list(hist):
        hist[n] = {k: v for k, v in hist[n].items() if k >= cutoff}
    _atomic_write(COMMIT_HISTORY, hist, 0o640)
    return hist


def _recent_days(hist, back=14):
    """把逐日提交归档压成「最近 back 天」的定长序列,给画像画柱子用。
    缺的那天补 0 是对的 —— 这里的 0 是**实测的「那天没提交」**,
    与 traffic 那边「上游还没发布」完全不同,后者补 0 就是编造。"""
    today = datetime.now(timezone.utc).date()
    out = {}
    for name, bucket in (hist or {}).items():
        out[name] = [{"d": str(today - timedelta(days=k)),
                      "c": int((bucket or {}).get(str(today - timedelta(days=k))) or 0)}
                     for k in range(back - 1, -1, -1)]
    return out


def _jaccard(a, b):
    u = len(a | b)
    return (len(a & b) / u) if u else 0.0


def build_coupling(repos_rows, hist, subprojects):
    """从**已经采到的数据**推导仓与仓之间的耦合,不额外请求接口、不调用任何模型。

    三类边,各自独立可解释:
      co_change  共变 —— 同一天都有提交。用活跃日集合的 Jaccard 做强度,
                 并对比「近 7 天 vs 前 7 天」得出**增强/减弱**趋势,这就是「动态」。
      stack      同栈 —— 主语言相同,改一个的技能/工具链会牵动另一个。
      contains   归属 —— 子项目属于哪个仓(monorepo 里真实存在的包含关系)。
    """
    priv_of = {r["name"]: bool(r.get("private")) for r in repos_rows}
    lang_of = {r["name"]: (r.get("top_lang") or "—") for r in repos_rows}
    today = datetime.now(timezone.utc).date()

    def active(name, lo, hi):
        """[lo,hi) 天前窗口内有提交的日期集合。"""
        b = hist.get(name) or {}
        return {d for d, c in b.items()
                if c and str(today - timedelta(days=hi - 1)) <= d <= str(today - timedelta(days=lo))}

    names = [n for n in hist if n in priv_of]
    edges = []
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            aa, bb = active(a, 0, 14), active(b, 0, 14)
            both = aa & bb
            if len(both) < 2:
                continue                    # 只同框一天说明不了耦合,不画
            w = _jaccard(aa, bb)
            recent = _jaccard(active(a, 0, 7), active(b, 0, 7))
            prior = _jaccard(active(a, 7, 14), active(b, 7, 14))
            edges.append({
                "s": a, "t": b, "rel": "co_change", "w": round(w, 3),
                "days": len(both), "trend": round(recent - prior, 3),
                "why": "近 14 天有 %d 天同时改动" % len(both),
            })
    # 同栈边只在「没有共变边」时才画:已经同框改动的两个仓,再叠一条同语言线纯属噪音
    paired = {frozenset((e["s"], e["t"])) for e in edges}
    by_lang = {}
    for n in names:
        by_lang.setdefault(lang_of.get(n) or "—", []).append(n)
    for lang, group in by_lang.items():
        if lang in ("—", "") or len(group) < 2:
            continue
        group = sorted(group)
        for i, a in enumerate(group):
            for b in group[i + 1:]:
                if frozenset((a, b)) in paired:
                    continue
                edges.append({"s": a, "t": b, "rel": "stack", "w": 0.2,
                              "days": None, "trend": 0.0, "why": "同为 %s 主栈" % lang})
    for sp in subprojects or []:
        edges.append({"s": "sp:%s/%s" % (sp["repo"], sp["project"]), "t": sp["repo"],
                      "rel": "contains", "w": 1.0, "days": None, "trend": 0.0,
                      "why": "子项目位于 %s" % (sp.get("path") or "")})

    # 每个仓的耦合度 = 它所有共变边的强度和,用来在星图里定"引力质量"
    degree = {}
    for e in edges:
        if e["rel"] == "co_change":
            for k in (e["s"], e["t"]):
                degree[k] = round(degree.get(k, 0) + e["w"], 3)
    return {
        "edges": edges,
        "degree": degree,
        "window_days": 14,
        "archived_days": max((len(v) for v in hist.values()), default=0),
        "note": "共变耦合由逐日提交推导(Jaccard),同栈由主语言推导,归属由子项目登记推导",
    }


def _public_coupling(c, repos_rows):
    """公开派生:**只保留两端都是公开仓的边**,私有仓不出名、不出边。"""
    if not c:
        return {}
    pub = {r["name"] for r in repos_rows if not r.get("private")}
    ok = lambda k: (k in pub) or (k.startswith("sp:") and k.split("/")[0][3:] in pub)
    out = dict(c)
    out["edges"] = [e for e in c.get("edges", []) if ok(e["s"]) and ok(e["t"])]
    out["degree"] = {k: v for k, v in (c.get("degree") or {}).items() if k in pub}
    return out


# ======================= 账单(新版 billing/usage 端点)=======================
def gather_billing(token, login):
    """真实账单用量。旧 `/settings/billing/actions` 已 410,改用 `/settings/billing/usage`。
    净费用直接取 GitHub 的 netAmount,不做任何估算。"""
    d, _ = _get(f"{API}/users/{login}/settings/billing/usage", token)
    if not isinstance(d, dict) or "usageItems" not in d:
        return {"available": False, "note": "账单接口不可用(需 Plan:read 授权)"}
    items = d.get("usageItems") or []
    by_sku, by_month, net_total, gross_total = {}, {}, 0.0, 0.0
    for it in items:
        sku = it.get("sku") or "?"
        a = by_sku.setdefault(sku, {"sku": sku, "product": it.get("product"),
                                    "unit": it.get("unitType"), "qty": 0.0,
                                    "gross": 0.0, "discount": 0.0, "net": 0.0})
        a["qty"] += it.get("quantity") or 0
        a["gross"] += it.get("grossAmount") or 0
        a["discount"] += it.get("discountAmount") or 0
        a["net"] += it.get("netAmount") or 0
        net_total += it.get("netAmount") or 0
        gross_total += it.get("grossAmount") or 0
        if it.get("product") == "actions" and (it.get("unitType") or "").lower() == "minutes":
            by_month[(it.get("date") or "")[:7]] = by_month.get((it.get("date") or "")[:7], 0) + (it.get("quantity") or 0)
    months = sorted(by_month)
    cur = months[-1] if months else None
    repos_in_bill = sorted({(i.get("repositoryName") or "").strip()
                            for i in items if i.get("repositoryName")})
    return {
        "available": True,
        "billed_repos": repos_in_bill,
        "net_total": round(net_total, 2),
        "gross_total": round(gross_total, 2),
        "saved": round(gross_total - net_total, 2),
        "by_sku": sorted(by_sku.values(), key=lambda x: -x["qty"]),
        "actions_minutes_by_month": [{"m": m, "q": round(by_month[m], 1)} for m in months],
        "current_month": cur,
        "current_month_minutes": round(by_month.get(cur, 0), 1) if cur else None,
        "note": "netAmount 为 GitHub 实际计费口径;公开仓用量由折扣全额抵消",
    }


# ======================= 功能基线:软件内部的功能纵向切片 =======================
# 「软件运行状态」页看的是**运维**纵向切片(代码→CI→部署→…→自愈);
# 这里看的是**功能**纵向切片:每个项目声明了哪些功能、处于什么状态、
# 有没有实据、以及**实据是不是还成立**。
#
# 数据源不是猜的:CodexProject 治理规定每个 active project 必须把 canonical facts 写进
# `<项目>/docs/governance/project.yaml`(features / limitations / current_status)
# 与 `ASSURANCE_STATUS.yaml`(各保障维度 VERIFIED / PARTIAL)。实测 9 个项目都有。
FEATURE_PROJECTS = [
    ("KMOS", "KMFA"), ("KMOS", "KM_IDSystem"), ("KMOS", "whkmSalary"),
    ("MetaDatabase", "Alpha"), ("MetaDatabase", "EEI"), ("MetaDatabase", "arxiv-daily-push"),
    ("MetaDatabase", "PFI"), ("MetaDatabase", "Serenity-Alipay"),
    ("AgentDatabase", "OpenAIDatabase"),
]
FEATURE_CACHE = os.path.join(PRIVATE_DIR, "feature_cache.json")
# ★ 状态词表必须按**实际数据**定,不能凭想象。实测 9 个项目用了十几种写法:
#   completed_validated_local_only / uploaded_to_github_main / review_passed_upload_ready_local_only
#   / remediation_rereview_verified / accepted_release_frozen_waiting_final_delivery …
#   词表太窄就会把一大批已完成功能误判成「存疑」(第一版误判了 44 条)。
_ST_OK_HINT = ("active", "done", "complete", "ship", "verified", "released", "uploaded",
               "accepted", "review_passed", "closed")
_ST_PLAN_HINT = ("planned", "proposed", "todo", "pending", "draft", "not_started")
_ST_WARN_HINT = ("in_progress", "blocked", "partial", "wip")
# 证据等级由强到弱:VERIFIED 比 EXTRACTED 更强,第一版只认 EXTRACTED,把 44 条 VERIFIED 误降级了
_FACT_STRONG = ("VERIFIED", "EXTRACTED")
_FACT_WEAK = ("RECONSTRUCTED", "INFERRED")


def _feature_verdict(st, fact, has_path):
    if any(h in st for h in _ST_PLAN_HINT) or fact in ("PROPOSED", "UNKNOWN", ""):
        return "plan"
    if any(h in st for h in _ST_WARN_HINT):
        return "warn"
    if fact in _FACT_WEAK:
        return "warn"                       # 重建/推断出来的证据弱一档,不能算跑得通
    if any(h in st for h in _ST_OK_HINT) and fact in _FACT_STRONG and has_path:
        return "ok"
    return "warn"


def _blob_query(entries, field):
    parts = ['a%d: repository(owner:"LinzeColin", name:"%s"){ o: object(expression:"HEAD:%s")'
             '{... on Blob{ %s }} }' % (i, r, p.replace('"', ''), field)
             for i, (r, p) in enumerate(entries)]
    return "query{ rateLimit{cost remaining} " + " ".join(parts) + " }"


def _blobs(token, entries, field, chunk=120):
    """按别名批量取 blob 的某个字段(oid 或 text)。一次查上百个,cost 仍是 1。"""
    out = {}
    for k in range(0, len(entries), chunk):
        part = entries[k:k + chunk]
        d = _gql(token, _blob_query(part, field))
        data = (d or {}).get("data") or {}
        for i, key in enumerate(part):
            node = ((data.get("a%d" % i) or {}).get("o")) or None
            out[key] = node.get(field) if node else None
    return out


def _yaml_load(text):
    try:
        import yaml
        return yaml.safe_load(text) or {}
    except Exception as e:                       # 缺 pyyaml 或文件损坏:如实返回空,不猜
        print("features: yaml parse failed: %s" % e)
        return {}


def gather_features(token):
    """抓各项目的功能基线,并**验证证据链是否还成立**。

    ★ 「跑不跑得通」不能只看它自己声明的 status —— 那是自述。
      真正能机器判定的是:功能声称的 evidence_refs 指向的文件**现在还在不在**。
      文件被删/改名而功能清单没跟着改,这条功能的「有实据」就已经过期了,
      这是纯派生、零模型、可复核的判据。
    """
    cache = load_json(FEATURE_CACHE, {}) or {}
    pj_keys = [(r, ("" if d == "." else d + "/") + "docs/governance/project.yaml")
               for r, d in FEATURE_PROJECTS]
    as_keys = [(r, ("" if d == "." else d + "/") + "docs/governance/ASSURANCE_STATUS.yaml")
               for r, d in FEATURE_PROJECTS]
    oids = _blobs(token, pj_keys + as_keys, "oid")

    need = [k for k in pj_keys + as_keys if oids.get(k) and oids[k] not in cache]
    texts = _blobs(token, need, "text", chunk=6) if need else {}   # 单个 YAML 可达 400KB,少量多批
    for k in need:
        t = texts.get(k)
        if t:
            cache[oids[k]] = _yaml_load(t)
    for oid in list(cache):                       # 只保留本轮仍被引用的解析结果
        if oid not in set(v for v in oids.values() if v):
            cache.pop(oid, None)

    projects, ev_want = [], []
    for i, (repo, d) in enumerate(FEATURE_PROJECTS):
        pj = cache.get(oids.get(pj_keys[i]) or "") or {}
        asr = cache.get(oids.get(as_keys[i]) or "") or {}
        if not pj:
            continue
        # ★ 证据引用有两种写法,混为一谈就会造出一片假红(实测:186 个功能里 105 个被误判断链)。
        #   多数项目在 project.yaml 顶层维护一张 evidence 注册表
        #     evidence_refs: [{evidence_id: EVID-XXX, ref: 实际路径, ...}]
        #   功能里写的是 **evidence_id 符号**,要先查表才拿得到路径;
        #   KMFA 是老写法,功能里直接写路径。两种都得支持。
        evmap = {}
        for e in (pj.get("evidence_refs") or []):
            if isinstance(e, dict) and e.get("evidence_id"):
                evmap[e["evidence_id"]] = e.get("ref") or ""
        feats = []
        for f in (pj.get("features") or []):
            if not isinstance(f, dict):
                continue
            refs = [x for x in (f.get("evidence_refs") or []) if isinstance(x, str)]
            paths, unres = [], []
            for rf in refs:
                raw = evmap.get(rf) or (rf if ("/" in rf or "." in rf) else None)
                if raw is None:
                    unres.append(rf)                  # 符号但查不到表 —— 记「未解析」,不等于断链
                    continue
                # ★ 一条 ref 里可能用 ; 或换行串了**多个**路径,也可能夹着散文描述
                #   (实测 PFI 的 52 条"断链"全是这么来的)。拆开逐个判,
                #   只有"没空格且带 /"的 token 才是能机器核验的路径;
                #   其余是人读的说明,归「不可机器核验」而不是「断链」。
                for tok in re.split(r"[;\n]+", raw):
                    tok = tok.strip().strip(",")
                    if not tok:
                        continue
                    if "/" in tok and " " not in tok:
                        paths.append(tok)
                    else:
                        unres.append(tok)
            feats.append({"id": f.get("feature_id") or "", "name": f.get("name") or "",
                          "status": (f.get("status") or "").lower(),
                          "fact": (f.get("fact_level") or "").upper(),
                          "refs": refs, "paths": paths, "unres": unres})
            for pp in paths:
                ev_want.append((repo, pp))
                ev_want.append(("CodexProject", pp))  # 项目是迁移来的,老证据可能还留在原仓
        dims = {}
        for k, v in (asr.get("dimensions") or {}).items():
            if isinstance(v, dict):
                dims[k] = (v.get("status") or "").upper()
        projects.append({
            "project": d if d != "." else repo, "repo": repo,
            "summary": (pj.get("summary") or "")[:300],
            "status": pj.get("current_status") or "", "version": pj.get("version") or "",
            "fact_level": (pj.get("fact_level") or "").upper(),
            "features": feats, "dimensions": dims,
            "limitations": len(pj.get("limitations") or []),
        })

    # 证据链核验:同一路径只查一次
    uniq = sorted(set(ev_want))
    exists = _blobs(token, uniq, "oid") if uniq else {}
    ok_path = {k: bool(v) for k, v in exists.items()}

    for p in projects:
        cnt = {"ok": 0, "warn": 0, "bad": 0, "plan": 0}
        for f in p["features"]:
            # 证据在本仓或原仓(CodexProject)任一处存在即算成立 —— 迁移不该被算成断链
            miss = [r for r in f["paths"]
                    if not (ok_path.get((p["repo"], r)) or ok_path.get(("CodexProject", r)))]
            f["miss"] = miss
            f["v"] = "bad" if miss else _feature_verdict(f["status"], f["fact"], bool(f["paths"]))
            # local_only = 只在本地验证过、还没进 GitHub —— 算达标但要单独标出来,
            # 因为"本地跑得通"和"云端跑得通"不是一回事
            f["local"] = "local_only" in f["status"]
            cnt[f["v"]] += 1
        p["counts"] = cnt
        p["local_only"] = sum(1 for f in p["features"] if f.get("local"))
        live = cnt["ok"] + cnt["warn"] + cnt["bad"]
        p["health"] = round(cnt["ok"] / live * 100) if live else None
        p["state"] = "bad" if cnt["bad"] else ("warn" if cnt["warn"] else "ok")
        vd = [v for v in p["dimensions"].values()]
        p["verified_dims"] = sum(1 for v in vd if v == "VERIFIED")
        p["total_dims"] = len(vd)

    _atomic_write(FEATURE_CACHE, cache, 0o640)
    projects.sort(key=lambda x: (0 if x["state"] == "bad" else 1 if x["state"] == "warn" else 2,
                                 -(x["health"] or 0)))
    tot = {"projects": len(projects),
           "features": sum(len(p["features"]) for p in projects),
           "ok": sum(p["counts"]["ok"] for p in projects),
           "warn": sum(p["counts"]["warn"] for p in projects),
           "bad": sum(p["counts"]["bad"] for p in projects),
           "plan": sum(p["counts"]["plan"] for p in projects),
           "evidence_checked": len(set(p for _, p in uniq)),
           "evidence_missing": sum(1 for p in projects for f in p["features"] for _ in f["miss"]),
           "local_only": sum(p["local_only"] for p in projects),
           "unresolved_symbols": sum(1 for p in projects for f in p["features"] for _ in f["unres"])}
    return {"projects": projects, "totals": tot,
            "note": "功能与状态来自各项目 docs/governance/project.yaml(治理 canonical facts);"
                    "「证据链」= 该功能声明的 evidence_refs 指向的文件此刻是否仍存在 —— "
                    "文件被删或改名而清单没跟着改,这条功能的「有实据」就已经过期。"
                    "本判据纯派生、零模型、可复核。",
            "at": int(time.time())}


FLOW_DOCS = os.path.join(PRIVATE_DIR, "flow_docs.json")
# 业务流登记范围 = 有治理事实的项目 + status 自己(自己也必须被同一套规则管住)
# ★ 这个写死的清单只是**兜底**,不是发现机制。
#   真正的发现走 discover_projects():扫全部仓的 git tree,凡是有
#   `<项目>/docs/governance/project.yaml` 的就自动纳入。
#   为什么必须这样:写死清单意味着**新建项目在有人想起来改这行之前是隐形的** ——
#   它不在分母里,所以覆盖率不会掉、未登记数不会涨、看板一切正常。
#   这正是本域反复出现的假绿形态:被丢掉的东西不参与任何总量校验,所以总量永远对。
FLOW_PROJECTS_FALLBACK = FEATURE_PROJECTS + [("LinzeHomeHub", "status")]
FLOW_PROJECTS = list(FLOW_PROJECTS_FALLBACK)   # 运行时被 discover_projects() 覆盖

# 这些目录不是业务项目,扫到也不算(全是仓自己的骨架/归档位)
_NOT_A_PROJECT = {"docs", "scripts", "tests", "governance", "templates", "archive",
                  "_archive", "_protected", "node_modules", "vendor", "third_party"}


# ★ 明确判定「这个目录不是业务项目」的白名单。**每一条都必须写理由** ——
#   没有理由的豁免过两个月就没人知道当初为什么豁免，等同于永久黑洞。
NOT_PROJECT = {
    ("AgentDatabase", "CodexSkills"): "技能注册表,不是业务项目;由 skill-github-sync 自己管",
    ("KMOS", "KMDatabase"): "数据目录,不是软件项目;数据治理走 Private-Database 那条线",
    ("CodexProject", "GOLDEN_PATH"): "上云配方文档目录",
    ("CodexProject", "INVENTORY"): "资产清单文档目录",
    ("MetaDatabase", "LinzeDatabase"): "数据目录,不是软件项目",
    ("MetaDatabase", "FINAL_ACCEPTANCE_BUNDLE"): "一次性验收产物归档",
}
_PROJECT_MARKERS = ("README.md", "AGENTS.md", "VERSION")


def discover_ungoverned(token, repos, governed):
    """找出**长得像项目、却完全没纳入治理**的目录。

    ★ 为什么还需要这一层:discover_projects() 找的是**已经有** project.yaml 的目录。
      一个新项目在写出治理文件之前，对本站是**彻底隐形**的 ——
      它不在分母里，所以覆盖率不掉、未登记数不涨、看板一切正常。
      实测(2026-07-27)全仓有 10 个这样的目录，其中 CyberBoss 有 634 个文件、
      owner 明确说它是**在跑的活跃项目**，而本站一个字都看不到它。

    ★ 判定原则:**不自动下结论,强制表态。**
      扫到的目录只要没被 NOT_PROJECT 显式豁免,就一直挂在「未纳入治理」里(红)。
      要么给它补治理文件,要么在白名单里写明为什么它不是项目。
      **沉默不是选项** —— 沉默正是过去它能隐形的原因。
    """
    gset = set(governed or [])
    out, scanned = [], 0
    for r in repos or []:
        name = r.get("name") if isinstance(r, dict) else str(r)
        branch = (r.get("default_branch") if isinstance(r, dict) else None) or "main"
        if not name:
            continue
        tree, _ = _get("%s/repos/%s/%s/git/trees/%s?recursive=1"
                       % (API, "LinzeColin", name, branch), token)
        if not isinstance(tree, dict) or not isinstance(tree.get("tree"), list):
            continue
        scanned += 1
        tops, gov, marks = set(), set(), {}
        for node in tree["tree"]:
            path = node.get("path") or ""
            if "/" not in path:
                if node.get("type") == "tree" and not path.startswith("."):
                    tops.add(path)
                continue
            head = path.split("/", 1)[0]
            if path.endswith("docs/governance/project.yaml") or \
               path.endswith("docs/governance/flow.yaml"):
                gov.add(head if path.count("/") > 2 else ".")
            base = path.rsplit("/", 1)[-1]
            if base in _PROJECT_MARKERS and path.count("/") == 1:
                marks.setdefault(head, set()).add(base)
        for d in sorted(tops):
            if d in _NOT_A_PROJECT or d in gov:
                continue
            if (name, d) in gset or (name, d) in NOT_PROJECT:
                continue
            if not marks.get(d):
                continue                      # 连 README/AGENTS/VERSION 都没有,不像项目
            out.append({"repo": name, "dir": d,
                        "markers": sorted(marks[d]),
                        "why": "有 %s 但没有 docs/governance/project.yaml —— "
                               "本站看不到它的任何业务状态" % "/".join(sorted(marks[d]))})
    return {"items": out, "scanned": scanned, "count": len(out),
            "exempt": [{"repo": k[0], "dir": k[1], "why": v}
                       for k, v in sorted(NOT_PROJECT.items())]}


def discover_projects(token, repos):
    """扫全部仓,找出所有带治理文件的项目 —— **新项目自动纳入,不用改代码**。

    命中两种形态:
      · `<项目>/docs/governance/project.yaml`  (单仓多项目,本域主流)
      · `docs/governance/project.yaml`         (整仓就是一个项目,名字取仓名)

    ★ 只增不减:发现失败(限流/接口变更/网络)时**回落到写死清单**,
      绝不返回空 —— 返回空会让「未登记」瞬间归零、看板一片绿,
      而真相是「这一轮没看见」。看不见必须表现为看不见,不能表现为没问题。
    """
    found, scanned, failed = set(), 0, []
    for r in repos or []:
        name = r.get("name") if isinstance(r, dict) else str(r)
        branch = (r.get("default_branch") if isinstance(r, dict) else None) or "main"
        if not name:
            continue
        tree, _ = _get("%s/repos/%s/%s/git/trees/%s?recursive=1"
                       % (API, "LinzeColin", name, branch), token)
        if not isinstance(tree, dict) or not isinstance(tree.get("tree"), list):
            failed.append(name)
            continue
        scanned += 1
        for node in tree["tree"]:
            path = node.get("path") or ""
            if not path.endswith("docs/governance/project.yaml"):
                continue
            head = path[:-len("docs/governance/project.yaml")].strip("/")
            if not head:
                found.add((name, "."))                  # 整仓一个项目
            elif "/" not in head and head not in _NOT_A_PROJECT:
                found.add((name, head))                 # 单仓多项目
    if not found:
        # 一个都没扫到 = 发现机制本身坏了,不是「真的没有项目」
        return list(FLOW_PROJECTS_FALLBACK), {"mode": "fallback", "scanned": scanned,
                                              "failed": failed,
                                              "why": "自动发现一个项目都没扫到,已回落到兜底清单"}
    merged = sorted(found | set(FLOW_PROJECTS_FALLBACK))
    new = sorted(found - set(FLOW_PROJECTS_FALLBACK))
    gone = sorted(set(FLOW_PROJECTS_FALLBACK) - found)
    return merged, {"mode": "discovered", "scanned": scanned, "failed": failed,
                    "found": len(found), "newly_discovered": ["%s/%s" % x for x in new],
                    # ★ 兜底清单里有、这轮没扫到的,**单独留去向账**,不静默吞掉
                    "in_fallback_but_not_found": ["%s/%s" % x for x in gone]}


# KMFA 已有机器可读事实档,**不要求它再多维护一份 flow.yaml** —— 两份登记必然漂移。
# 这里放「项目 -> 自有事实档路径」的适配登记;适配层把它规范化成统一接口。
NATIVE_FACTS = {"KMFA": ("KMOS", "KMFA/machine/facts/business_baselines.json")}


def _iso_ts(raw):
    """解析自报时间戳。**认显式时区偏移** —— 这一条是 collect.py 里踩过的坑:
    截断成 19 位再当 UTC，会把 `+08:00` 的时间算旧 8 小时,
    刚跑完的步骤会被误报成超期。没有偏移的才回落到北京时间。
    """
    s = str(raw or "").strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(s)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone(timedelta(hours=8)))
    except ValueError:
        return None


_LIVE_STATES = ("healthy", "degraded", "blocked", "blocked_by_input",
                "blocked_by_policy", "not_built", "unknown")
_LIVE_KEY = re.compile(r"^[A-Za-z0-9_.:-]{1,80}$")


def _parse_flow_state(raw, project):
    """解析项目自报的 `flow_state.json`。**整份文件都是不可信输入**。

    只接受三样东西:已知状态词、能解析的时间戳、截断后的一句话说明。
    任何路径、命令、表达式一律不读 —— 这份文件永远只是数据,不是指令。
    解析失败如实返回空,不是当成通过(静默丢弃 = 假绿)。
    """
    if not raw:
        return {}, None
    try:
        doc = json.loads(raw)
    except Exception:
        return {}, "flow_state.json 解析失败"
    if not isinstance(doc, dict) or not isinstance(doc.get("steps"), dict):
        return {}, "flow_state.json 缺 steps 段"
    out = {}
    for k, v in list(doc["steps"].items())[:400]:
        if not (isinstance(k, str) and _LIVE_KEY.match(k) and isinstance(v, dict)):
            continue
        st = v.get("state")
        if st not in _LIVE_STATES:
            continue                       # 认不出的状态词直接不收,不猜
        at = _iso_ts(v.get("at") or "")
        out[k] = {"state": st, "at": at,
                  "note": str(v.get("note") or "")[:120],
                  "n": v.get("n") if isinstance(v.get("n"), int) else None}
    return out, (None if out else "flow_state.json 里没有一条能用的记录")


def _adapt_business_baselines(doc, name, repo, path):
    """把 KMFA 的 business_baselines.json 规范化成统一接口。

    它的阶段键是**英文**(intake/parse/compute/verify/output/deliver),显示名在 stage_model;
    状态用 healthy/degraded/blocked/not_built —— 全域五态沿用这套词汇,只把 blocked 拆成
    blocked_by_policy(按规定就不该通,不需要任何人做事)与 blocked_by_input(缺输入,必须催人),
    因为**两者的处置动作是反的**,合成一态就排不出优先级。
    尚未拆分的裸 `blocked` 保持原样显示为「阻断(未细分)」,不替它猜。
    """
    sm = doc.get("stage_model") or []
    stages = [x.get("id") for x in sm if isinstance(x, dict) and x.get("id")]
    names = {x["id"]: x.get("name") or x["id"] for x in sm if isinstance(x, dict) and x.get("id")}
    means = {x["id"]: x.get("meaning") or "" for x in sm if isinstance(x, dict) and x.get("id")}
    out_bl, defects = [], []
    for b in (doc.get("baselines") or []):
        cells = {}
        for k, v in (b.get("stages") or {}).items():
            if not isinstance(v, dict):
                continue
            cells[k] = {"state": v.get("status"), "evidence": v.get("evidence") or "",
                        "probe": (v.get("probe_hint") or {}).get("kind"),
                        "args": (v.get("probe_hint") or {}).get("args") or {},
                        # ★ 缺失一律 None,**绝不用 "" 当哨兵**:前端是拿 d.id == c.defect 找缺陷的,
                        #   空串会和空串相等 —— 实测后果是 KMFA 全部 54 格都挂上了同一条
                        #   「项目成本毛利算不出」,连钉钉考勤、工资发放、红圈合同都挂着。
                        #   看起来完全正常,所以一直没人发现。缺失就是缺失,不能是可比较的值。
                        "defect": v.get("defect") or None}
        for d in (b.get("known_defects") or []):
            if isinstance(d, dict):
                defects.append({"id": d.get("id") or None, "baseline": b.get("id"),
                                "stage": d.get("stage") or None, "desc": d.get("desc") or "",
                                "since": d.get("since") or None})
            elif isinstance(d, str):    # 还是裸字符串数组:如实收下,ID 留空
                defects.append({"id": None, "baseline": b.get("id"), "stage": None,
                                "desc": d, "since": None})
        out_bl.append({"id": b.get("id") or "", "name": b.get("name") or b.get("skill") or "",
                       "priority": (b.get("priority") or "P3").upper(),
                       "note": b.get("owner_note") or "",
                       "upstream": b.get("upstream") or [], "downstream": b.get("downstream") or [],
                       "cells": cells})
    return {"schema": "adapted:" + str(doc.get("schema_version") or ""),
            # 五态中文含义由被测方在自己的事实档里给,适配层不硬编码
            "status_semantics": doc.get("status_semantics") or {},
            "project": name, "repo": repo, "source": "%s/%s" % (repo, path),
            "stages": stages, "stage_names": names, "stage_meaning": means,
            "baselines": out_bl, "defects": defects,
            "sources": [s if isinstance(s, str) else s.get("id")
                        for s in (doc.get("sources") or [])],
            "coupling_rule": doc.get("coupling_rule") or "",
            "authority": doc.get("authority") or ""}


def gather_flows(token, projects_list=None, discovery=None):
    """抓各项目登记的业务流 `flow.yaml`,并检出**有治理文件却没登记**的项目。

    ★ 登记覆盖不靠自觉:凡是有 `docs/governance/project.yaml` 的项目就必须发布 flow.yaml,
      没发布的直接列进 unregistered,和「部署即登记」是同一套执行逻辑。
    ★ **以 main 的 HEAD 为准**:还在 PR 或本地 worktree 里的登记看不到,如实算未登记 ——
      数据源必须可复核,不能把未合并的东西当成事实。
    """
    projects_list = projects_list or list(FLOW_PROJECTS_FALLBACK)
    discovery = discovery or {"mode": "not_run",
                              "why": "未跑自动发现,用的是兜底清单 —— 新建项目可能没被看见"}
    keys = [(r, ("" if d == "." else d + "/") + "docs/governance/flow.yaml")
            for r, d in projects_list]
    # ★ 双向的那一半:各项目**自己**把「这一步刚跑完、产出是什么」写进
    #   docs/governance/flow_state.json,由它自己的 CI/cron 提交,本站只读不写。
    #   这是三个「主机上一个程序都没有」的系统唯一可能被实测到的通道 ——
    #   没有它,自动核查覆盖率的天花板只有 72%,永远够不到 85%。
    live_keys = [(r, ("" if d == "." else d + "/") + "docs/governance/flow_state.json")
                 for r, d in projects_list]
    nat = {n: (r, p) for n, (r, p) in NATIVE_FACTS.items()}
    texts = _blobs(token, keys + live_keys + [(r, p) for r, p in nat.values()],
                   "text", chunk=8)
    projects, missing = [], []
    for i, (repo, d) in enumerate(projects_list):
        name = d if d != "." else repo
        # 有自有事实档的项目优先走适配层,不要求它再维护一份 flow.yaml
        if name in nat:
            nr, np_ = nat[name]
            raw = texts.get((nr, np_))
            if raw:
                try:
                    projects.append(_adapt_business_baselines(json.loads(raw), name, nr, np_))
                    continue
                except Exception as e:
                    missing.append({"project": name, "repo": nr, "expect": np_,
                                    "why": "自有事实档解析失败:%s" % str(e)[:60]})
                    continue
        live, live_why = _parse_flow_state(texts.get(live_keys[i]), name)
        t = texts.get(keys[i])
        if not t:
            missing.append({"project": name, "repo": repo,
                            "expect": keys[i][1],
                            "why": "有治理文件但未发布 flow.yaml(以 main 为准;"
                                   "若已在 PR/worktree,合并后即自动纳入)"})
            continue
        doc = _yaml_load(t)
        if not isinstance(doc, dict) or not doc.get("baselines"):
            missing.append({"project": name, "repo": repo, "expect": keys[i][1],
                            "why": "flow.yaml 存在但缺 baselines 段,无法解析"})
            continue
        doc["project"] = doc.get("project") or name
        doc["repo"] = repo
        doc.setdefault("stage_names", {})
        doc["source"] = "%s/%s" % (repo, keys[i][1])
        # 项目自报的实时步骤状态(双向的回流那一半)。为空就是为空,如实标原因,
        # **不静默丢弃** —— 丢掉的东西不进任何总量校验,总量就永远是对的(假绿)。
        doc["live"] = live
        doc["live_at"] = max([v["at"].isoformat() for v in live.values() if v["at"]] or [""]) or None
        doc["live_why"] = live_why
        doc["live_expect"] = live_keys[i][1]
        projects.append(doc)
    return {"projects": projects, "unregistered": missing,
            "registered": len(projects), "expected": len(projects_list),
            "discovery": discovery,
            "at": int(time.time()),
            "note": "业务流登记以各仓 main 的 docs/governance/flow.yaml 为准"}


def gather_deep(token):
    """REST 深采:仓库清单(权威,含 GraphQL 看不到的仓)+ release/CI/子项目。"""
    me, _ = _get(f"{API}/user", token)
    login = (me or {}).get("login", "LinzeColin")
    repos, _ = _get(f"{API}/user/repos?per_page=100&affiliation=owner&sort=pushed", token)
    if not isinstance(repos, list):
        return None
    cap_traffic = _probe(f"{API}/repos/{repos[0]['full_name']}/traffic/views", token) if repos else 0
    cap_billing = _probe(f"{API}/users/{login}/settings/billing/actions", token)
    cap = {
        "traffic": "OK" if cap_traffic == 200 else
                   ("UNAVAILABLE (需 Administration:read 授权)" if cap_traffic == 403 else "UNKNOWN"),
        "billing": "OK" if cap_billing == 200 else
                   ("UNAVAILABLE (需 Plan:read 授权)" if cap_billing == 403 else "UNKNOWN"),
    }
    out, subs = {}, []
    for r in repos:
        fn, db = r["full_name"], r.get("default_branch", "main")
        d = _deep_repo(fn, db, token)
        out[r["name"]] = {
            "name": r["name"], "full_name": fn, "private": r["private"], "archived": r["archived"],
            "default_branch": db, "pushed_at": (r.get("pushed_at") or "")[:10],
            "size_kb": r.get("size", 0), "url": r.get("html_url"),
            "top_lang": r.get("language") or "—",
            "release_bytes": d["release_bytes"], "ci": d["ci"],
            "open_pr": d["open_pr"], "open_issue": d["open_issue"],
        }
        if r["name"] in SUBPROJECTS:
            subs += _subprojects_for(r["name"], db, token)
    repo_rows = list(out.values())
    chist = gather_commit_days(token, [r["name"] for r in repo_rows])
    # ★ 自动纳入:先扫全部仓找出所有带治理文件的项目,再据此抓业务流登记。
    #   新建项目一有 docs/governance/project.yaml 就自动进这张表 —— 不用改代码。
    #   没发布 flow.yaml 的会直接落进 unregistered(红),而**不是隐形**。
    disc_list, disc_meta = discover_projects(token, repo_rows)
    # ★ 第二层:长得像项目却完全没治理文件的目录。没有这一层，新项目在写出
    #   治理文件之前对本站是彻底隐形的 —— 隐形不会让任何指标变红。
    ungov = discover_ungoverned(token, repo_rows, disc_list)
    return {"repos": out, "subprojects": subs, "capability": cap,
            "actions": gather_actions(token, repo_rows),
            "throughput": gather_throughput(token),
            "traffic": gather_traffic(token, repo_rows),
            "billing": gather_billing(token, login),
            "coupling": build_coupling(repo_rows, chist, subs),
            "commit_days": _recent_days(chist),
            "features": gather_features(token),
            "flows": gather_flows(token, disc_list, disc_meta),
            "ungoverned": ungov,
            "account": {"login": login, "name": (me or {}).get("name", "")}}


# ======================= 合并 & 派生 =======================
FAIL_CONCLUSIONS = ("failure", "timed_out", "startup_failure", "action_required")


def ci_failed(r):
    """只有「真失败」才算失败:从没跑过(no runs)、cancelled/skipped 都不算。"""
    concl = (r.get("ci") or {}).get("last_conclusion")
    if concl:
        return concl in FAIL_CONCLUSIONS
    return r.get("ci_state") == "FAILURE"


def recompute_totals(doc):
    t = {"repos": 0, "public": 0, "private": 0, "archived": 0, "commits_7d": 0,
         "commits_30d": 0, "open_pr": 0, "open_issue": 0, "ci_fail": 0,
         "git_size_kb": 0, "release_bytes": 0, "branches": 0}
    for r in doc.get("repos", []):
        t["repos"] += 1
        t["private" if r.get("private") else "public"] += 1
        if r.get("archived"):
            t["archived"] += 1
        for k in ("commits_7d", "commits_30d", "open_pr", "open_issue", "branches"):
            if isinstance(r.get(k), int):
                t[k] += r[k]
        t["git_size_kb"] += r.get("size_kb") or 0
        t["release_bytes"] += r.get("release_bytes") or 0
        if ci_failed(r):
            t["ci_fail"] += 1
    doc["totals"] = t
    return t


def _public_traffic(t):
    """公开派生:per_repo 只留公开仓。"""
    if not t:
        return {}
    out = dict(t)
    out["per_repo"] = [r for r in t.get("per_repo", []) if not r.get("private")]
    return out


def _public_billing(b):
    """账单是账号级聚合,本身不含仓名;防御性再过滤一次。"""
    if not b or not b.get("available"):
        return b or {}
    out = dict(b)
    out["by_sku"] = [x for x in b.get("by_sku", []) if x.get("sku") not in PRIVATE_NAMES_GUARD]
    billed = set(b.get("billed_repos") or [])
    out["private_billed_count"] = len(billed & PRIVATE_NAMES_GUARD)
    out["billed_repos"] = sorted(billed - PRIVATE_NAMES_GUARD)   # 公开面只留非私有仓名
    return out


def _public_actions(a):
    """Actions 公开派生:by_repo 只留公开仓,私有仓只保留计数。"""
    if not a:
        return {}
    out = dict(a)
    out["by_repo"] = [r for r in a.get("by_repo", []) if not r.get("private")]
    return out


def build_public(priv):
    """公开安全派生:私有仓只留计数,绝不出现名字/明细。"""
    t = priv.get("totals", {})
    pub_rows, pub_names = [], set()
    for r in priv.get("repos", []):
        if r.get("private"):
            continue
        pub_names.add(r["name"])
        pub_rows.append({
            "name": r["name"], "default_branch": r.get("default_branch"),
            "pushed_at": r.get("pushed_at"), "size_kb": r.get("size_kb"),
            "top_lang": r.get("top_lang"), "languages": r.get("languages") or {},
            "commits_7d": r.get("commits_7d"), "commits_30d": r.get("commits_30d"),
            "open_pr": r.get("open_pr"), "open_issue": r.get("open_issue"),
            "branches": r.get("branches"), "url": r.get("url"),
            "release_bytes": r.get("release_bytes"),
            "ci_conclusion": (r.get("ci") or {}).get("last_conclusion"),
            "ci_state": r.get("ci_state"),
            "ci_ok": not ci_failed(r),
        })
    return {
        "collected_at": priv.get("collected_at"), "collected_epoch": priv.get("collected_epoch"),
        "deep_at": priv.get("deep_at"),
        "repos_total": t.get("repos"), "public": t.get("public"), "private": t.get("private"),
        "archived": t.get("archived"), "commits_7d": t.get("commits_7d"),
        "commits_30d": t.get("commits_30d"), "open_pr": t.get("open_pr"),
        "open_issue": t.get("open_issue"), "ci_fail": t.get("ci_fail"),
        "branches": t.get("branches"),
        "git_size_kb": t.get("git_size_kb"), "release_bytes": t.get("release_bytes"),
        "capability": priv.get("capability", {}), "rate": priv.get("rate", {}),
        "calendar": priv.get("calendar", {}),          # 贡献网格:仅逐日计数,无仓名
        "actions": _public_actions(priv.get("actions") or {}),
        "throughput": priv.get("throughput", {}),
        "traffic": _public_traffic(priv.get("traffic") or {}),
        "billing": _public_billing(priv.get("billing") or {}),
        "coupling": _public_coupling(priv.get("coupling") or {}, priv.get("repos", [])),
        # 功能基线全部来自**公开仓**的治理文件,不含私有仓;仍走一次防御性过滤
        "features": priv.get("features") or {},
        "public_repos": pub_rows,
        # 仓库画像用:每个**公开**仓的近 14 天逐日提交数。
        # ★ 私有仓不出现在这里,连键都不出现 —— 公开面永不出现私有仓名这条不变量不放松。
        "commit_days": {k: v for k, v in (priv.get("commit_days") or {}).items()
                        if k in pub_names},
        "subprojects": [s for s in priv.get("subprojects", []) if s.get("repo") in pub_names],
        "note": "私有仓明细仅登录 /admin/github 可见",
    }


def write_all(priv):
    recompute_totals(priv)
    _atomic_write(PRIVATE_OUT, priv, 0o640)
    _atomic_write(PUBLIC_OUT, build_public(priv))


def read_token():
    p = os.path.join(APP_DIR, ".secrets", "github_pat")
    if os.path.exists(p):
        return open(p).read().strip()
    return os.environ.get("GH_TOKEN", "")


def run_deep(token):
    deep = gather_deep(token)
    if not deep:
        print("deep: gather failed (keep last-known-good)")
        return False
    priv = load_json(PRIVATE_OUT, {}) or {}
    by_name = {r["name"]: r for r in priv.get("repos", [])}
    merged = []
    for name, d in deep["repos"].items():
        row = dict(by_name.get(name, {}))
        row.update(d)
        row.pop("stale", None)
        merged.append(row)
    now = datetime.now(CN)
    # 业务流登记写**私有档**:里面有主机路径、容器名、库表名这类基础设施细节,不该上公开面
    if deep.get("flows"):
        _atomic_write(FLOW_DOCS, deep["flows"], 0o640)
    priv.update({"repos": merged, "subprojects": deep["subprojects"],
                 "capability": deep["capability"], "account": deep["account"],
                 "actions": deep["actions"], "throughput": deep["throughput"],
                 "traffic": deep["traffic"], "billing": deep["billing"],
                 "coupling": deep["coupling"], "features": deep["features"],
                 # ★ 检出结果必须落盘。第一版加进了 gather_deep() 的返回值,
                 #   但这里的白名单没带上它 —— 于是每轮都算了、每轮都丢掉,
                 #   页面永远拿不到,而且**没有任何报错**。
                 #   典型的「算了但没人接」:上游有产出、下游不取,中间静默蒸发。
                 "ungoverned": deep.get("ungoverned") or {"count": 0, "items": [],
                                                          "exempt": [], "scanned": 0},
                 "deep_at": _fmt(now), "collected_at": _fmt(now),
                 "collected_epoch": int(time.time())})
    write_all(priv)
    print("deep ok: repos=%d subprojects=%d" % (len(merged), len(deep["subprojects"])))
    return True


def run_fast(token):
    fast = gather_fast(token)
    if not fast:
        print("fast: graphql failed (keep last-known-good)")
        return False
    priv = load_json(PRIVATE_OUT, None)
    if not priv or not priv.get("repos"):
        print("fast: no canonical yet -> running deep first")
        if not run_deep(token):
            return False
        priv = load_json(PRIVATE_OUT, {}) or {}

    by_name = {r["name"]: r for r in priv.get("repos", [])}
    seen = set()
    for name, g in fast["repos"].items():
        seen.add(name)
        row = by_name.setdefault(name, {"name": name})
        for k, v in g.items():
            if v is None:
                continue                      # 令牌拒绝的字段保留旧值,不清零
            row[k] = v
        row.pop("stale", None)
    for name, row in by_name.items():
        if name not in seen:
            row["stale"] = True               # GraphQL 看不到的仓:标注但保留 deep 旧值
    now = datetime.now(CN)
    priv.update({"repos": list(by_name.values()), "calendar": fast["calendar"],
                 "rate": fast["rate"], "collected_at": _fmt(now),
                 "collected_epoch": int(time.time())})
    if (fast.get("account") or {}).get("login"):
        priv["account"] = fast["account"]
    write_all(priv)
    t = priv["totals"]
    print("fast ok: repos=%d c7=%d cal_total=%s rate=%s/%s" % (
        t["repos"], t["commits_7d"], (fast["calendar"] or {}).get("total"),
        fast["rate"].get("remaining"), fast["rate"].get("limit")))
    return True


def run_traffic(token):
    """中层(cron 每 15 分钟):只打 traffic 两个端点 + 逐日提交 GraphQL。

    为什么单独一层:GitHub 的 traffic **一天只发布一次、且滞后约 2 天**,
    小时级的 deep 层最坏会让新数据晚 1 小时才出现在页面上。这一层把「新数据一发布
    就被抓到」压到 15 分钟内,页面就能在到达当刻高亮提示——**这已经是上游允许的极限**,
    再密也不会让 GitHub 提前发布。开销:REST 18 请求 + GraphQL cost 1。
    """
    priv = load_json(PRIVATE_OUT, None)
    if not priv or not priv.get("repos"):
        return run_deep(token)
    rows = priv["repos"]
    before = ((priv.get("traffic") or {}).get("freshness") or {}).get("upstream_through")
    priv["traffic"] = gather_traffic(token, rows)
    chist = gather_commit_days(token, [r["name"] for r in rows])
    priv["coupling"] = build_coupling(rows, chist, priv.get("subprojects") or [])
    priv["commit_days"] = _recent_days(chist)
    priv["collected_at"] = _fmt(datetime.now(CN))
    priv["collected_epoch"] = int(time.time())
    write_all(priv)
    f = priv["traffic"].get("freshness") or {}
    print("traffic ok: through=%s lag=%sd%s edges=%d" % (
        f.get("upstream_through"), f.get("lag_days"),
        "  <-- NEW DAY ARRIVED" if before and f.get("upstream_through") != before else "",
        len((priv["coupling"] or {}).get("edges") or [])))
    return True


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "fast"
    token = read_token()
    if not token:
        print("no github token")
        return
    {"deep": run_deep, "traffic": run_traffic}.get(mode, run_fast)(token)


if __name__ == "__main__":
    main()
