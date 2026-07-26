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
TRAFFIC_HISTORY = os.path.join(DATA_DIR, "traffic_history.json")
PRIVATE_NAMES_GUARD = {"Private-Database", "Governance", "KMFA-App-State-Backup"}


def gather_traffic(token, repos):
    """访问流量。**GitHub 只返回最近 14 天,过期永久丢失**,所以逐日归档进
    traffic_history.json(只增不减),这样时间越久历史越完整。"""
    hist = load_json(TRAFFIC_HISTORY, {}) or {}
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
                if d:
                    h[key][d] = {"c": row.get("count", 0), "u": row.get("uniques", 0)}
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
    _atomic_write(TRAFFIC_HISTORY, hist)

    # 逐日汇总(只汇总公开仓,供公开面用)
    pub = {r["name"] for r in per_repo if not r["private"]}
    daily = {}
    for name, h in hist.items():
        if name not in pub:
            continue
        for key in ("views", "clones"):
            for d, x in h[key].items():
                slot = daily.setdefault(d, {"v": 0, "c": 0})
                slot["v" if key == "views" else "c"] += x.get("c", 0)
    days = sorted(daily)
    per_repo.sort(key=lambda x: -(x.get("views_14d") or 0))
    return {
        "per_repo": per_repo,
        "archived_days": len(days),
        "archive_since": days[0] if days else None,
        "daily": [{"d": d, "v": daily[d]["v"], "c": daily[d]["c"]} for d in days[-90:]],
        "totals_14d": {
            "views": sum(r.get("views_14d") or 0 for r in per_repo if not r["private"]),
            "clones": sum(r.get("clones_14d") or 0 for r in per_repo if not r["private"]),
        },
        "unreadable_repos": unreadable,
        "note": "GitHub 只提供最近 14 天,本站逐日归档累积,越久越完整",
    }


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
    return {"repos": out, "subprojects": subs, "capability": cap,
            "actions": gather_actions(token, repo_rows),
            "throughput": gather_throughput(token),
            "traffic": gather_traffic(token, repo_rows),
            "billing": gather_billing(token, login),
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
        "public_repos": pub_rows,
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
    priv.update({"repos": merged, "subprojects": deep["subprojects"],
                 "capability": deep["capability"], "account": deep["account"],
                 "actions": deep["actions"], "throughput": deep["throughput"],
                 "traffic": deep["traffic"], "billing": deep["billing"],
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


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "fast"
    token = read_token()
    if not token:
        print("no github token")
        return
    run_deep(token) if mode == "deep" else run_fast(token)


if __name__ == "__main__":
    main()
