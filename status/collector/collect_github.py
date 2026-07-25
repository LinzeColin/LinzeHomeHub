#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GitHub Engineering Plane 采集器 —— 被 collect.py 以 30 分钟节流调用(GitHub 是慢变量)。
用现有只读 PAT 采集账号下全部仓库的工程指标,产出**两份**输出:
  1) public-safe 聚合(不含任何私有仓名/明细)→ 交给 collect.py 写进公开 snapshot.json。
  2) 私有全量(含 3 个私有仓 + monorepo 子项目)→ 写到 status-private/github.json,
     该目录**不在 nginx 公开挂载内**,只读挂进 admin 容器,经 Cloudflare Access 才可看。
口径遵循设计基线:PR 与 Issue 分开计数(Issue 用 search 排除 PR);存储只标"Git 仓占用";
Traffic/Billing 当前令牌 403 → 标 UNAVAILABLE,绝不编造。所有请求带 API version,不记录 Authorization。
"""
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone, timedelta

CN = timezone(timedelta(hours=8))
API = "https://api.github.com"
UA = "linze-status-github-monitor"
APIV = "2022-11-28"

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


def _hdr(token):
    return {"Authorization": "Bearer " + token, "User-Agent": UA,
            "X-GitHub-Api-Version": APIV, "Accept": "application/vnd.github+json"}


def _get(url, token, timeout=20):
    """返回 (json, headers)。失败返回 (None, None),绝不抛。"""
    try:
        req = urllib.request.Request(url, headers=_hdr(token))
        resp = urllib.request.urlopen(req, timeout=timeout)
        raw = resp.read()
        return (json.loads(raw) if raw else None), resp.headers
    except Exception:
        return None, None


def _count_via_link(path, token):
    """per_page=1 + Link rel=last 页码 = 总数(1 次请求拿计数)。失败 None。"""
    sep = "&" if "?" in path else "?"
    data, hdrs = _get(API + path + sep + "per_page=1", token)
    if hdrs is None:
        return None
    link = hdrs.get("Link", "") or ""
    m = re.search(r'[?&]page=(\d+)>;\s*rel="last"', link)
    if m:
        return int(m.group(1))
    return len(data) if isinstance(data, list) else 0


def _search_count(q, token):
    """search/issues 的 total_count(用于把 Issue 与 PR 干净分开)。"""
    data, _ = _get(API + "/search/issues?q=" + urllib.parse.quote(q) + "&per_page=1", token)
    if isinstance(data, dict):
        return data.get("total_count")
    return None


def _iso(days_ago):
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _repo_metrics(full, default_branch, token):
    """单仓工程指标。每项独立失败不影响其它。"""
    owner_name = full
    since7, since30 = _iso(7), _iso(30)
    c7 = _count_via_link(f"/repos/{owner_name}/commits?sha={default_branch}&since={since7}", token)
    c30 = _count_via_link(f"/repos/{owner_name}/commits?sha={default_branch}&since={since30}", token)
    branches = _count_via_link(f"/repos/{owner_name}/branches", token)
    open_pr = _count_via_link(f"/repos/{owner_name}/pulls?state=open", token)
    open_issue = _search_count(f"repo:{owner_name} type:issue state:open", token)
    langs, _ = _get(f"{API}/repos/{owner_name}/languages", token)
    langs = langs if isinstance(langs, dict) else {}
    # releases 资产字节
    rel, _ = _get(f"{API}/repos/{owner_name}/releases?per_page=100", token)
    rel_bytes = 0
    if isinstance(rel, list):
        for r in rel:
            for a in r.get("assets", []) or []:
                rel_bytes += a.get("size", 0)
    # 最近 Actions(默认分支)结论 + 近 10 次通过率
    runs, _ = _get(f"{API}/repos/{owner_name}/actions/runs?branch={default_branch}&per_page=10", token)
    ci = {"last_conclusion": None, "pass_rate": None, "last_at": None, "has_ci": False}
    if isinstance(runs, dict):
        rr = runs.get("workflow_runs", []) or []
        if rr:
            ci["has_ci"] = True
            ci["last_conclusion"] = rr[0].get("conclusion")
            ci["last_at"] = (rr[0].get("updated_at") or "")[:16].replace("T", " ")
            done = [x for x in rr if x.get("conclusion")]
            if done:
                ok = sum(1 for x in done if x["conclusion"] == "success")
                ci["pass_rate"] = round(ok / len(done) * 100)
    return {"commits_7d": c7, "commits_30d": c30, "branches": branches,
            "open_pr": open_pr, "open_issue": open_issue, "languages": langs,
            "release_bytes": rel_bytes, "ci": ci}


def _subprojects_for(repo, default_branch, token):
    out = []
    for sp in SUBPROJECTS.get(repo, []):
        p = urllib.parse.quote(sp["path"])
        c30 = _count_via_link(f"/repos/LinzeColin/{repo}/commits?sha={default_branch}&path={p}&since={_iso(30)}", token)
        last, _ = _get(f"{API}/repos/LinzeColin/{repo}/commits?sha={default_branch}&path={p}&per_page=1", token)
        last_at = None
        if isinstance(last, list) and last:
            last_at = (last[0].get("commit", {}).get("committer", {}).get("date") or "")[:10]
        out.append({"repo": repo, "project": sp["project"], "path": sp["path"],
                    "commits_30d": c30, "last_commit_at": last_at})
    return out


def gather(token):
    """返回 (public_safe_dict, private_full_dict)。"""
    me, _ = _get(f"{API}/user", token)
    login = (me or {}).get("login", "LinzeColin")
    name = (me or {}).get("name", "")
    repos, _ = _get(f"{API}/user/repos?per_page=100&affiliation=owner&sort=pushed", token)
    if not isinstance(repos, list):
        return None, None

    # 能力探测:traffic / billing 是否可读(带状态码,便于如实标注 403/授权缺失)
    cap = {"traffic": "UNAVAILABLE", "billing": "UNAVAILABLE"}
    cap_traffic = _probe(f"{API}/repos/{repos[0]['full_name']}/traffic/views", token) if repos else 0
    cap_billing = _probe(f"{API}/users/{login}/settings/billing/actions", token)
    cap["traffic"] = "OK" if cap_traffic == 200 else ("UNAVAILABLE (需 Administration:read 授权)" if cap_traffic == 403 else "UNKNOWN")
    cap["billing"] = "OK" if cap_billing == 200 else ("UNAVAILABLE (需 Plan:read 授权)" if cap_billing == 403 else "UNKNOWN")

    full_repos, tot = [], {"repos": 0, "public": 0, "private": 0, "archived": 0,
                           "commits_7d": 0, "commits_30d": 0, "open_pr": 0, "open_issue": 0,
                           "ci_fail": 0, "git_size_kb": 0, "release_bytes": 0}
    subprojects = []
    for r in repos:
        fn = r["full_name"]
        db = r.get("default_branch", "main")
        m = _repo_metrics(fn, db, token)
        row = {
            "name": r["name"], "full_name": fn, "private": r["private"],
            "archived": r["archived"], "default_branch": db,
            "pushed_at": (r.get("pushed_at") or "")[:10],
            "size_kb": r.get("size", 0), "url": r.get("html_url"),
            "languages": m["languages"],
            "top_lang": (max(m["languages"], key=m["languages"].get) if m["languages"] else (r.get("language") or "—")),
            "commits_7d": m["commits_7d"], "commits_30d": m["commits_30d"],
            "branches": m["branches"], "open_pr": m["open_pr"], "open_issue": m["open_issue"],
            "release_bytes": m["release_bytes"], "ci": m["ci"],
        }
        full_repos.append(row)
        tot["repos"] += 1
        tot["public" if not r["private"] else "private"] += 1
        if r["archived"]:
            tot["archived"] += 1
        for k in ("commits_7d", "commits_30d", "open_pr", "open_issue"):
            if isinstance(row[k], int):
                tot[k] += row[k]
        tot["git_size_kb"] += row["size_kb"]
        tot["release_bytes"] += row["release_bytes"]
        if row["ci"]["has_ci"] and row["ci"]["last_conclusion"] not in ("success", None):
            tot["ci_fail"] += 1
        if r["name"] in SUBPROJECTS:
            subprojects += _subprojects_for(r["name"], db, token)

    rl, _ = _get(f"{API}/rate_limit", token)
    core = ((rl or {}).get("resources", {}) or {}).get("core", {}) if isinstance(rl, dict) else {}
    now = datetime.now(CN)
    stamp = {"collected_at": _fmt(now), "collected_epoch": int(time.time())}

    private_full = {
        **stamp, "account": {"login": login, "name": name},
        "totals": tot, "capability": cap,
        "rate": {"remaining": core.get("remaining"), "limit": core.get("limit")},
        "repos": full_repos, "subprojects": subprojects,
    }
    # public-safe:仅公开仓明细,私有仓只进计数
    pub_rows = []
    for row in full_repos:
        if row["private"]:
            continue
        pub_rows.append({k: row[k] for k in ("name", "default_branch", "pushed_at", "size_kb",
                                             "top_lang", "commits_7d", "commits_30d",
                                             "open_pr", "open_issue", "branches", "url")}
                        | {"ci_ok": row["ci"]["last_conclusion"] in ("success", None),
                           "ci_conclusion": row["ci"]["last_conclusion"]})
    public_safe = {
        **stamp,
        "repos_total": tot["repos"], "public": tot["public"], "private": tot["private"],
        "archived": tot["archived"], "commits_7d": tot["commits_7d"], "commits_30d": tot["commits_30d"],
        "open_pr": tot["open_pr"], "open_issue": tot["open_issue"], "ci_fail": tot["ci_fail"],
        "git_size_kb": tot["git_size_kb"], "release_bytes": tot["release_bytes"],
        "capability": cap, "public_repos": pub_rows,
        "note": "私有仓明细仅登录 /github 可见",
    }
    # 子项目:仅公开父仓的子项目属于公开信息,可进公开面
    pub_names = {r["name"] for r in full_repos if not r["private"]}
    public_safe["subprojects"] = [s for s in subprojects if s["repo"] in pub_names]
    return public_safe, private_full


def _probe(url, token):
    try:
        req = urllib.request.Request(url, headers=_hdr(token))
        return urllib.request.urlopen(req, timeout=12).getcode()
    except urllib.error.HTTPError as e:
        return e.code
    except Exception:
        return 0


APP_DIR = os.environ.get("STATUS_APP_DIR", "/srv/linze/apps/status")
PUBLIC_OUT = os.path.join(APP_DIR, "data", "github_public.json")      # nginx 公开(仅脱敏聚合)
PRIVATE_OUT = os.environ.get("STATUS_GH_PRIVATE",
                             os.path.join(APP_DIR, "private", "github.json"))  # 不在 nginx 挂载内


def _atomic_write(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def main():
    tok_path = os.path.join(APP_DIR, ".secrets", "github_pat")
    tok = open(tok_path).read().strip() if os.path.exists(tok_path) else os.environ.get("GH_TOKEN", "")
    if not tok:
        print("no github token")
        return
    pub, priv = gather(tok)
    if not pub:
        print("gather failed (keeping last-known-good)")
        return
    _atomic_write(PUBLIC_OUT, pub)
    _atomic_write(PRIVATE_OUT, priv)
    try:
        os.chmod(PRIVATE_OUT, 0o640)
    except Exception:
        pass
    t = priv["totals"]
    print("github written: repos=%d pub=%d priv=%d c7=%d fail=%d rate=%s" % (
        t["repos"], t["public"], t["private"], t["commits_7d"], t["ci_fail"],
        priv["rate"].get("remaining")))


if __name__ == "__main__":
    main()
