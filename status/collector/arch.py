#!/usr/bin/env python3
"""架构采集器（ZL00-261119-05，KM-需-1208）。

每 5 分钟由 /etc/cron.d/linze-status-arch 以 root 跑一次，实测这台机器上
「域名 → 对外通道 → 服务 → 底座（数据库、登录、发信、监控）」的真实连线，写出：
  data/arch.json   页面用的分层图（节点、连线、重复项、死项、计数）
  data/arch.mmd    同一张图的 Mermaid 文本，给 Archify / Graphify 等工具直接读

只读：不改任何被监控对象。不调用任何模型。不读 GitHub。
Cloudflare 隧道的入口规则用 /etc/linze/cf-zl00.env 里的令牌只读拉取；拿不到就照实标注「未取到」。
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import time
import urllib.request
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
OUT_JSON = DATA / "arch.json"
OUT_MMD = DATA / "arch.mmd"
SNAPSHOT = DATA / "snapshot.json"
CF_ENV = Path("/etc/linze/cf-zl00.env")
TRAEFIK_DYNAMIC = Path("/data/coolify/proxy/dynamic")
OWNER_UID = 1000

HOST_RE = re.compile(r"Host\(`([^`]+)`\)")

# 角色判定：按镜像和名字，只用确定的特征，认不出就归「应用」
ROLE_RULES = [
    ("proxy", lambda n, i: "traefik" in i),
    ("auth", lambda n, i: "authelia" in i or "keycloak" in i),
    ("db", lambda n, i: i.startswith("postgres") or "mysql" in i or "mariadb" in i or "/postgres" in i),
    ("cache", lambda n, i: i.startswith("redis") or "/redis" in i),
    ("mail", lambda n, i: "smtp" in n or "mail" in n),
    ("monitor", lambda n, i: "gatus" in i or "uptime" in n or "sentinel" in i or n.startswith("linze-status")),
    ("manager", lambda n, i: "coollabsio/coolify" in i),
    ("static", lambda n, i: i.startswith("nginx") and not n.startswith("linze-status")),
]
ROLE_NAME = {
    "domain": "域名", "tunnel": "隧道", "proxy": "反向代理", "auth": "登录", "db": "数据库",
    "cache": "缓存", "mail": "发信", "monitor": "监控", "manager": "部署管理", "static": "静态站",
    "app": "应用", "service": "系统服务", "timer": "定时任务", "external": "外部服务",
}
# 同一角色只该有一份的公共部件（服务器与部署规则第 2 节）
SINGLETON_ROLES = {"proxy": 1, "auth": 1, "tunnel": 2, "mail": 1, "manager": 0}


def sh(cmd: list[str], timeout: int = 30) -> str:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout).stdout
    except Exception:
        return ""


def role_of(name: str, image: str) -> str:
    n, i = name.lower(), image.lower()
    for role, test in ROLE_RULES:
        if test(n, i):
            return role
    return "app"


def docker_units() -> list[dict]:
    ids = sh(["docker", "ps", "-aq"]).split()
    if not ids:
        return []
    raw = sh(["docker", "inspect", *ids], timeout=60)
    try:
        items = json.loads(raw)
    except Exception:
        return []
    out = []
    for c in items:
        name = c["Name"].lstrip("/")
        labels = c["Config"].get("Labels") or {}
        image = c["Config"].get("Image", "")
        ports = []
        for _, binds in (c.get("NetworkSettings", {}).get("Ports") or {}).items():
            for b in binds or []:
                if b.get("HostPort"):
                    ports.append(int(b["HostPort"]))
        hosts = sorted({h for k, v in labels.items() if k.endswith(".rule") for h in HOST_RE.findall(v)})
        mids = " ".join(v for k, v in labels.items() if "middlewares" in k)
        out.append({
            "name": name,
            "image": image.split("@")[0][:80],
            "state": c["State"]["Status"],
            "restart": (c.get("HostConfig", {}).get("RestartPolicy") or {}).get("Name", ""),
            "project": labels.get("com.docker.compose.project") or labels.get("coolify.projectName") or "",
            "networks": sorted((c.get("NetworkSettings", {}).get("Networks") or {}).keys()),
            "ports": sorted(set(ports)),
            "hosts": hosts,
            "forwardauth": "authn" in mids or "authelia" in mids,
            "role": role_of(name, image),
            "env_hosts": sorted({m for e in (c["Config"].get("Env") or []) for m in re.findall(r"(?:HOST|URL|ADDR)[A-Z_]*=([a-zA-Z0-9_.-]+)", e)}),
        })
    return out


def systemd_units() -> list[dict]:
    out = []
    raw = sh(["systemctl", "list-units", "--type=service,timer", "--all", "--no-legend", "--plain", "--no-pager"])
    for line in raw.splitlines():
        parts = line.split(None, 4)
        if len(parts) < 4:
            continue
        unit, load, active, sub = parts[:4]
        desc = parts[4] if len(parts) > 4 else ""
        if not re.search(r"linze|kmbid|cloudflared|signal|whkmgx|alpha|km-|nas|status|ops|serenity|social|liekan|fa00|geo", unit):
            continue
        kind = "timer" if unit.endswith(".timer") else "service"
        out.append({"name": unit, "state": f"{active}/{sub}", "load": load, "desc": desc[:80], "kind": kind,
                    "role": kind, "connector": "cloudflared" in desc.lower() or "tunnel" in unit})
    return out


def listeners() -> dict[int, str]:
    """本机端口 → 占用它的 systemd 单元或容器名。"""
    m: dict[int, str] = {}
    for line in sh(["ss", "-ltnpH"]).splitlines():
        pm = re.search(r":(\d+)\s", line)
        pids = re.findall(r"pid=(\d+)", line)
        if not pm or not pids:
            continue
        port = int(pm.group(1))
        try:
            cg = Path(f"/proc/{pids[0]}/cgroup").read_text()
        except Exception:
            continue
        u = re.search(r"/([^/]+\.service)", cg)
        d = re.search(r"docker-([0-9a-f]{12})", cg)
        if u and "docker" not in u.group(1):
            m.setdefault(port, u.group(1))
        elif d:
            m.setdefault(port, "cid:" + d.group(1))
    return m


def cf_tunnels() -> tuple[list[dict], str]:
    try:
        tok = re.search(r"CF_ZL00_TOKEN=(\S+)", CF_ENV.read_text()).group(1)
    except Exception:
        return [], "没有令牌文件"

    def get(path: str):
        r = urllib.request.Request("https://api.cloudflare.com/client/v4" + path,
                                   headers={"Authorization": "Bearer " + tok, "User-Agent": "linze-status-arch"})
        return json.load(urllib.request.urlopen(r, timeout=20))

    try:
        zone = get("/zones?name=linzezhang.com")["result"][0]
        acct = zone["account"]["id"]
        tunnels = []
        for t in get(f"/accounts/{acct}/cfd_tunnel?is_deleted=false")["result"]:
            cfg = get(f"/accounts/{acct}/cfd_tunnel/{t['id']}/configurations")["result"] or {}
            ing = [{"host": i.get("hostname"), "service": i.get("service")}
                   for i in (cfg.get("config") or {}).get("ingress", []) if i.get("hostname")]
            tunnels.append({"id": t["id"][:8], "name": t["name"], "status": t["status"],
                            "connections": len(t.get("connections") or []), "ingress": ing})
        return tunnels, ""
    except Exception as e:  # 网络或权限问题：照实写，不编
        return [], f"未取到：{type(e).__name__}"


def file_routes() -> list[dict]:
    out = []
    if not TRAEFIK_DYNAMIC.is_dir():
        return out
    for f in sorted(TRAEFIK_DYNAMIC.glob("*.y*ml")):
        txt = f.read_text(errors="ignore")
        for h in sorted(set(HOST_RE.findall(txt))):
            url = re.search(r"url:\s*['\"]?([^'\"\s]+)", txt)
            out.append({"host": h, "file": f.name, "target": url.group(1) if url else ""})
    return out


def owners_from_snapshot() -> dict[str, str]:
    try:
        snap = json.loads(SNAPSHOT.read_text())
        return {u["id"]: (u.get("owner") or "") for u in snap.get("software", {}).get("units", [])}
    except Exception:
        return {}


def build() -> dict:
    now = time.time()
    cons = docker_units()
    units = systemd_units()
    ports = listeners()
    tunnels, cf_note = cf_tunnels()
    froutes = file_routes()
    owners = owners_from_snapshot()
    cid_to_name = {}
    for line in sh(["docker", "ps", "-a", "--format", "{{.ID}} {{.Names}}"]).splitlines():
        a = line.split()
        if len(a) == 2:
            cid_to_name[a[0][:12]] = a[1]

    nodes: dict[str, dict] = {}
    edges: list[dict] = []

    def node(nid: str, label: str, role: str, layer: int, **kw):
        if nid not in nodes:
            nodes[nid] = {"id": nid, "label": label, "role": role, "role_cn": ROLE_NAME.get(role, role),
                          "layer": layer, **kw}
        return nid

    def edge(a: str, b: str, kind: str):
        if a in nodes and b in nodes and not any(e["from"] == a and e["to"] == b for e in edges):
            edges.append({"from": a, "to": b, "kind": kind})

    # 第 2 层：对外通道（隧道＋反向代理）
    for t in tunnels:
        node("tun:" + t["name"], t["name"], "tunnel", 1, state="在跑" if t["connections"] else "停用（对象留 7 天）",
             detail=f"Cloudflare 隧道 {t['id']}，连接 {t['connections']} 条")
    # 第 3、4 层：容器
    for c in cons:
        layer = 3 if c["role"] in ("db", "cache", "auth", "mail", "monitor", "manager") else 2
        if c["role"] == "proxy":
            layer = 1
        alive = c["state"] == "running"
        node("c:" + c["name"], c["name"], c["role"], layer,
             state="在跑" if alive else ("已停（留 7 天回滚）" if c["restart"] == "no" else "已停"),
             owner=owners.get(c["name"], ""), detail=f"容器 · {c['image']}", project=c["project"],
             kind="container", hosts=c["hosts"])
    for u in units:
        node("u:" + u["name"], u["name"], u["role"], 1 if u["role"] == "tunnel" else 2,
             state="在跑" if u["state"].startswith("active") else u["state"], owner=owners.get(u["name"], ""),
             detail=f"systemd · {u['desc']}", kind=u["kind"])
    proxy = next((n for n in nodes if nodes[n]["role"] == "proxy" and nodes[n]["state"] == "在跑"), None)
    auth = next((n for n in nodes if n == "c:kmbid-auth"), None)

    # 第 1 层：域名，以及域名 → 通道 → 服务
    tunnel_hosts = set()
    for t in tunnels:
        if not t["connections"]:
            continue
        for i in t["ingress"]:
            h = i["host"]
            tunnel_hosts.add(h)
            node("d:" + h, h, "domain", 0, state="", detail="经 Cloudflare 隧道")
            edge("d:" + h, "tun:" + t["name"], "入口")
            pm = re.search(r":(\d+)", i["service"] or "")
            if pm:
                who = ports.get(int(pm.group(1)), "")
                if who.startswith("cid:"):
                    who = "c:" + cid_to_name.get(who[4:], who[4:])
                elif who:
                    who = "u:" + who
                if who in nodes:
                    edge("tun:" + t["name"], who, "转发")
                    hs = nodes[who].setdefault("hosts", [])
                    if h not in hs:
                        hs.append(h)
                elif int(pm.group(1)) in (80, 443) and proxy:
                    edge("tun:" + t["name"], proxy, "转发")
    live = [c for c in cons if c["state"] == "running"]
    for c in live:
        for h in c["hosts"]:
            node("d:" + h, h, "domain", 0, state="", detail="经反向代理")
            if h in tunnel_hosts:
                edge("d:" + h, "c:" + c["name"], "路由")
            elif proxy:
                edge("d:" + h, proxy, "入口")
                edge(proxy, "c:" + c["name"], "路由")
            if c["forwardauth"] and auth and "c:" + c["name"] != auth:
                edge("c:" + c["name"], auth, "登录")
    for r in froutes:
        node("d:" + r["host"], r["host"], "domain", 0, state="", detail=f"代理文件路由 {r['file']}")
        if proxy:
            edge("d:" + r["host"], proxy, "入口")
        tm = re.search(r"//([^:/]+)", r["target"])
        if tm and "c:" + tm.group(1) in nodes and nodes["c:" + tm.group(1)]["state"] == "在跑":
            edge(proxy, "c:" + tm.group(1), "路由")

    # 底座连线：同一个 compose 项目里应用 → 库/缓存；环境变量里点名的主机
    by_proj = defaultdict(list)
    for c in cons:
        if c["project"]:
            by_proj[c["project"]].append(c)
    for proj, cs in by_proj.items():
        cs = [c for c in cs if c["state"] == "running"]
        for a in cs:
            for b in cs:
                if a is not b and a["role"] in ("app", "auth", "manager", "static") and b["role"] in ("db", "cache"):
                    edge("c:" + a["name"], "c:" + b["name"], "数据")
    names = {c["name"] for c in cons}
    for c in live:
        for h in c["env_hosts"]:
            if h in names and h != c["name"]:
                edge("c:" + c["name"], "c:" + h, "依赖")
    # 发信：程序 → 本机 2525（ops-mail-smtp）→ Cloudflare Worker ops-mail
    if "c:ops-mail-smtp" in nodes:
        node("x:ops-mail", "ops-mail（Cloudflare Worker）", "external", 4, state="", detail="唯一发信口")
        edge("c:ops-mail-smtp", "x:ops-mail", "发信")
        for u in units:
            if u["name"].startswith("alpha") and u["kind"] == "service":
                edge("u:" + u["name"], "c:ops-mail-smtp", "发信")

    # 体检：重复、死项、孤儿
    running = [n for n in nodes.values() if n["role"] not in ("domain", "external") and n.get("state") == "在跑"]  # 隧道节点在跑时也算
    per_role = defaultdict(list)
    for n in running:
        per_role[n["role"]].append(n["label"])
    dup = [{"role": r, "role_cn": ROLE_NAME[r], "count": len(v), "allowed": SINGLETON_ROLES[r], "items": sorted(v)}
           for r, v in per_role.items() if r in SINGLETON_ROLES and len(v) > SINGLETON_ROLES[r]]
    timers = {u["name"][:-6] for u in units if u["kind"] == "timer"}

    def by_design(n: dict) -> bool:
        """一次性服务由定时器拉起、失败通知模板实例：平时停着是正常的，不算死。"""
        name = n["label"]
        base = name[:-8] if name.endswith(".service") else name
        return base in timers or "@" in name or name.endswith("-failed.service")

    stopped = [{"id": n["id"], "label": n["label"], "state": n["state"]} for n in nodes.values()
               if n.get("kind") == "container" and n.get("state") != "在跑"]
    stopped += [{"id": n["id"], "label": n["label"], "state": n["state"]} for n in nodes.values()
                if n["role"] == "tunnel" and n.get("state") != "在跑"]
    dead = [{"id": n["id"], "label": n["label"], "state": n["state"]} for n in nodes.values()
            if n.get("kind") in ("service", "timer") and n.get("state") != "在跑" and not by_design(n)]
    linked = {e["from"] for e in edges} | {e["to"] for e in edges}
    orphans = [n["label"] for n in nodes.values() if n["id"] not in linked and n.get("state") == "在跑"
               and n.get("kind") == "container"]
    counts = {
        "域名": sum(1 for n in nodes.values() if n["role"] == "domain"),
        "在跑容器": sum(1 for c in cons if c["state"] == "running"),
        "停着的容器": sum(1 for c in cons if c["state"] != "running"),
        "系统服务": sum(1 for u in units if u["kind"] == "service" and u["state"].startswith("active")),
        "定时任务": sum(1 for u in units if u["kind"] == "timer" and u["state"].startswith("active")),
        "隧道": sum(1 for t in tunnels if t["connections"]),
        "连线": len(edges),
    }
    return {
        "generated_epoch": int(now),
        "generated_at": time.strftime("%Y-%m-%d %H:%M", time.gmtime(now + 8 * 3600)) + "（北京时间）",
        "source": "架构采集器 status/collector/arch.py，每 5 分钟实测一次",
        "cloudflare_note": cf_note,
        "counts": counts,
        "duplicates": dup,
        "dead": dead,
        "stopped": stopped,
        "orphans": sorted(orphans),
        "nodes": sorted(nodes.values(), key=lambda n: (n["layer"], n["role"], n["label"])),
        "edges": edges,
        "tunnels": tunnels,
    }


def mermaid(a: dict) -> str:
    def nid(s: str) -> str:
        return re.sub(r"[^A-Za-z0-9_]", "_", s)

    lines = ["flowchart LR"]
    for n in a["nodes"]:
        lines.append(f'  {nid(n["id"])}["{n["label"]}<br/>{n["role_cn"]}"]')
    for e in a["edges"]:
        lines.append(f'  {nid(e["from"])} -- {e["kind"]} --> {nid(e["to"])}')
    return "\n".join(lines) + "\n"


def write(path: Path, text: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    try:
        os.chown(tmp, OWNER_UID, OWNER_UID)
    except PermissionError:
        pass
    os.chmod(tmp, 0o644)
    tmp.replace(path)


if __name__ == "__main__":
    arch = build()
    write(OUT_JSON, json.dumps(arch, ensure_ascii=False, indent=1))
    write(OUT_MMD, mermaid(arch))
    print(f"arch written: {arch['generated_at']} nodes {len(arch['nodes'])} edges {len(arch['edges'])}")
