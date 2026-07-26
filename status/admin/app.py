#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
LinzeStatus 价格编辑器后端(phase 2)。
挂在 status.linzezhang.com/admin,前面有 Cloudflare Access(仅 owner 邮箱)。
后端**再自己校验 CF Access JWT**(签名+aud+邮箱),所以即便有人绕过 CF 直连源站 IP 也会被 403。
唯一写动作:校验通过后原子写 prices.json;严格白名单校验,无 shell、无任意路径。
"""
import json
import os
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import jwt
from jwt import PyJWKClient

TEAM = os.environ["CF_TEAM_DOMAIN"]
AUD = os.environ["CF_ACCESS_AUD"]
OWNER = os.environ["OWNER_EMAIL"]
PRICES = os.environ.get("PRICES_PATH", "/srv/data/prices.json")
GITHUB_PRIVATE = os.environ.get("GITHUB_PRIVATE", "/srv/private/github.json")
PORT = int(os.environ.get("PORT", "8080"))

CURRENCIES = {"AUD", "USD", "CNY", "EUR", "SGD", "GBP", "HKD", "JPY"}
_jwks = PyJWKClient(f"https://{TEAM}/cdn-cgi/access/certs")
_lock = threading.Lock()


def verify_identity(headers):
    token = headers.get("Cf-Access-Jwt-Assertion")
    if not token:
        return None
    try:
        key = _jwks.get_signing_key_from_jwt(token).key
        data = jwt.decode(token, key, algorithms=["RS256"], audience=AUD)
    except Exception:
        return None
    email = (data.get("email") or "").lower()
    return email if email == OWNER.lower() else None


def load_prices():
    try:
        with open(PRICES) as f:
            return json.load(f)
    except Exception:
        return {"items": []}


def load_github_private():
    try:
        with open(GITHUB_PRIVATE) as f:
            return json.load(f)
    except Exception:
        return {"available": False, "note": "GitHub 私有采集尚未产出(每 30 分钟一次)"}


def validate(payload):
    if not isinstance(payload, dict):
        raise ValueError("bad payload")
    items = payload.get("items")
    if not isinstance(items, list) or len(items) > 50:
        raise ValueError("items 非法或过多")
    clean = []
    for it in items:
        if not isinstance(it, dict):
            raise ValueError("item 非对象")
        name = str(it.get("name", "")).strip()[:60]
        if not name:
            continue
        try:
            amount = round(float(it.get("amount", 0)), 4)
        except Exception:
            raise ValueError("金额非数字")
        if amount < 0 or amount > 1e7:
            raise ValueError("金额越界")
        currency = str(it.get("currency", "AUD")).upper()
        if currency not in CURRENCIES:
            raise ValueError("币种非法")
        cadence = it.get("cadence", "monthly")
        if cadence not in ("monthly", "yearly"):
            raise ValueError("周期非法")
        purchase = str(it.get("purchase", "")).strip()[:10]
        if purchase and not re.match(r"^\d{4}-\d{2}-\d{2}$", purchase):
            raise ValueError("日期格式应为 YYYY-MM-DD")
        note = str(it.get("note", ""))[:80]
        row = {"name": name, "amount": amount, "currency": currency, "cadence": cadence}
        if purchase:
            row["purchase"] = purchase
            row["track_renew"] = bool(it.get("track_renew", True))
        if note:
            row["note"] = note
        row["auto_renew"] = bool(it.get("auto_renew", False))
        clean.append(row)
    return {"note": "月度开支价格库,由 status /admin 编辑器写入。金额为原币种原周期,采集器按实时汇率折算。",
            "items": clean}


def atomic_write(obj):
    tmp = PRICES + ".tmp"
    with open(tmp, "w") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    os.replace(tmp, PRICES)


class H(BaseHTTPRequestHandler):
    server_version = "linze-status-admin"

    def _send(self, code, body=b"", ctype="application/json"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("X-Frame-Options", "SAMEORIGIN")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if body:
            self.wfile.write(body if isinstance(body, bytes) else body.encode())

    def _json(self, code, obj):
        self._send(code, json.dumps(obj, ensure_ascii=False), "application/json; charset=utf-8")

    def log_message(self, *a):
        pass

    def do_GET(self):
        who = verify_identity(self.headers)
        if not who:
            return self._json(403, {"error": "未通过 Cloudflare Access 校验"})
        if self.path.rstrip("/") == "/admin" or self.path.startswith("/admin?"):
            return self._send(200, EDITOR_HTML, "text/html; charset=utf-8")
        if self.path.startswith("/admin/api/prices"):
            return self._json(200, load_prices())
        if self.path.rstrip("/") == "/admin/github" or self.path.startswith("/admin/github?"):
            return self._send(200, GITHUB_HTML, "text/html; charset=utf-8")
        if self.path.startswith("/admin/api/github"):
            return self._json(200, load_github_private())
        return self._json(404, {"error": "not found"})

    def do_POST(self):
        who = verify_identity(self.headers)
        if not who:
            return self._json(403, {"error": "未通过 Cloudflare Access 校验"})
        n = int(self.headers.get("Content-Length", "0") or "0")
        if n > 100_000:
            return self._json(413, {"error": "过大"})
        try:
            payload = json.loads(self.rfile.read(n) or b"{}")
        except Exception:
            return self._json(400, {"error": "JSON 解析失败"})

        # 单项开关(自动续费):主页公开只读,owner 点击才走到这
        if self.path.startswith("/admin/api/toggle"):
            name = str(payload.get("name", "")).strip()
            val = bool(payload.get("auto_renew"))
            if not name:
                return self._json(400, {"error": "缺少 name"})
            with _lock:
                p = load_prices()
                changed = 0
                for it in p.get("items", []):
                    if str(it.get("name", "")).strip() == name:
                        it["auto_renew"] = val
                        changed += 1
                if changed:
                    atomic_write(p)
            return self._json(200, {"ok": True, "changed": changed, "auto_renew": val})

        if self.path.startswith("/admin/api/prices"):
            try:
                clean = validate(payload)
            except ValueError as e:
                return self._json(400, {"error": str(e)})
            with _lock:
                atomic_write(clean)
            return self._json(200, {"ok": True, "by": who, "items": clean["items"]})

        return self._json(404, {"error": "not found"})


EDITOR_HTML = """<!doctype html><html lang=zh-CN><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1"><title>价格设置 · LinzeStatus</title>
<link rel="icon" href="data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'><rect width='32' height='32' rx='7' fill='%232a78d6'/><path d='M5 17h4l3-8 4 14 3-9h8' fill='none' stroke='white' stroke-width='2.6' stroke-linecap='round' stroke-linejoin='round'/></svg>">
<style>
:root{--bg:#f6f6f4;--card:#fff;--soft:#f1f1ee;--line:#e4e3dc;--t1:#1b1b19;--t2:#57564f;--t3:#8b8a83;--accent:#2a78d6}
@media(prefers-color-scheme:dark){:root{--bg:#151513;--card:#1e1e1c;--soft:#242422;--line:#33332f;--t1:#f3f2ec;--t2:#c3c2b7;--t3:#8b8a83;--accent:#5a9bea}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--t1);font-family:-apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei",sans-serif;font-size:15px}
a{color:var(--accent);text-decoration:none}a:hover{text-decoration:underline}
.wrap{max-width:860px;margin:0 auto;padding:20px 18px 90px}
.nav{display:flex;gap:4px;border-bottom:.5px solid var(--line);margin-bottom:18px}
.nav a{padding:11px 16px;color:var(--t2);border-bottom:2px solid transparent;margin-bottom:-1px}
.nav a.active{color:var(--t1);border-bottom-color:var(--accent);font-weight:500}
.nav a:hover{color:var(--t1);text-decoration:none}
h1{font-size:20px;margin:0 0 4px}.muted{color:var(--t2);font-size:13px}
.card{background:var(--card);border:.5px solid var(--line);border-radius:12px;padding:14px;margin:12px 0}
.grid{display:grid;grid-template-columns:1fr 110px 92px 92px;gap:10px}
.grid2{display:grid;grid-template-columns:200px 1fr;gap:10px;margin-top:10px}
@media(max-width:600px){.grid{grid-template-columns:1fr 1fr}.grid2{grid-template-columns:1fr}}
.f{display:flex;flex-direction:column;gap:3px}label{font-size:11px;color:var(--t3)}
input,select{width:100%;padding:9px;border:.5px solid var(--line);border-radius:8px;background:var(--bg);color:var(--t1);font-size:14px}
.chead{display:flex;justify-content:space-between;align-items:center;margin-bottom:10px}
.del{background:none;border:.5px solid var(--line);color:#cf3a3a;border-radius:8px;padding:5px 12px;cursor:pointer;font-size:13px}
.bar{position:sticky;bottom:0;background:var(--bg);padding:14px 0;display:flex;gap:10px;align-items:center;border-top:.5px solid var(--line);margin-top:10px}
button.act{padding:10px 18px;border:.5px solid var(--line);border-radius:9px;background:var(--card);color:var(--t1);font-size:15px;cursor:pointer}
button.primary{background:var(--accent);color:#fff;border-color:var(--accent)}
#msg{margin-left:auto;font-size:14px}
__NAVCSS__</style></head><body>__NAVSTART__<div class=wrap>
__NAV__
<h1>价格设置</h1>
<div class=muted>金额填<b>原币种、原周期</b>(月付填月费、年付填年费),页面自动按实时汇率折算,并算出「本月续费日」与「当月成本」。改完点保存  <a href="/">总览</a> 1 分钟内更新。</div>
<div id=list></div>
<div class=bar>
 <button class=act onclick=addRow()>+ 新增开支项</button>
 <button class="act primary" onclick=save()>保存</button>
 <span id=msg></span>
</div></div>
<script>
const CUR=["AUD","USD","CNY","EUR","SGD","GBP","HKD","JPY"],CAD={monthly:"月付",yearly:"年付"};
let items=[];
function esc(s){return String(s==null?"":s).replace(/"/g,"&quot;")}
function copt(c){return CUR.map(x=>`<option ${x==c?"selected":""}>${x}</option>`).join("")}
function cadopt(v){return Object.entries(CAD).map(([k,l])=>`<option value=${k} ${k==v?"selected":""}>${l}</option>`).join("")}
function draw(){document.getElementById("list").innerHTML=items.map((it,i)=>`
<div class=card>
 <div class=chead><strong>#${i+1}</strong><button class=del onclick="items.splice(${i},1);draw()">删除</button></div>
 <div class=grid>
  <div class=f><label>名称</label><input value="${esc(it.name)}" oninput="items[${i}].name=this.value"></div>
  <div class=f><label>金额</label><input type=number step=0.01 value="${it.amount||0}" oninput="items[${i}].amount=this.value"></div>
  <div class=f><label>币种</label><select onchange="items[${i}].currency=this.value">${copt(it.currency||"AUD")}</select></div>
  <div class=f><label>周期</label><select onchange="items[${i}].cadence=this.value">${cadopt(it.cadence||"monthly")}</select></div>
 </div>
 <div class=grid2>
  <div class=f><label>最初购买日(YYYY-MM-DD,可空)</label><input placeholder=2026-07-17 value="${esc(it.purchase)}" oninput="items[${i}].purchase=this.value"></div>
  <div class=f><label>备注(可空)</label><input value="${esc(it.note)}" oninput="items[${i}].note=this.value"></div>
 </div>
 <label style="display:flex;align-items:center;gap:8px;margin-top:10px;font-size:13px;cursor:pointer">
   <input type=checkbox ${it.auto_renew?"checked":""} onchange="items[${i}].auto_renew=this.checked"> 自动续费(到期自动扣费)</label>
</div>`).join("")||'<div class=muted style="padding:16px 0">还没有开支项,点下面「新增开支项」添加。</div>'}
function addRow(){items.push({name:"",amount:0,currency:"AUD",cadence:"monthly",purchase:"",note:"",track_renew:true,auto_renew:false});draw();window.scrollTo(0,document.body.scrollHeight)}
function save(){
 const m=document.getElementById("msg");m.textContent="保存中…";m.style.color="var(--t2)";
 fetch("/admin/api/prices",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({items})})
 .then(r=>r.json().then(j=>({ok:r.ok,j})))
 .then(({ok,j})=>{if(ok){m.innerHTML='已保存 · <a href="/">去总览看</a>';m.style.color="#0f8a4d";items=j.items.map(x=>({track_renew:true,note:"",purchase:"",...x}));draw()}else{m.textContent="失败:"+(j.error||"");m.style.color="#cf3a3a"}})
 .catch(e=>{m.textContent="网络错误";m.style.color="#cf3a3a"})
}
fetch("/admin/api/prices").then(r=>r.json()).then(j=>{items=(j.items||[]).map(x=>({track_renew:true,note:"",purchase:"",auto_renew:false,...x}));draw()}).catch(e=>{document.getElementById("msg").textContent="加载失败"})
</script></body></html>"""


GITHUB_HTML = """<!doctype html><html lang=zh-CN><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1"><title>GitHub 私有全量 · LinzeStatus</title>
<link rel="icon" href="data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'><rect width='32' height='32' rx='7' fill='%232a78d6'/><path d='M5 17h4l3-8 4 14 3-9h8' fill='none' stroke='white' stroke-width='2.6' stroke-linecap='round' stroke-linejoin='round'/></svg>">
<style>
:root{--bg:#f6f6f4;--card:#fff;--soft:#f1f1ee;--line:#e4e3dc;--t1:#1b1b19;--t2:#57564f;--t3:#8b8a83;--accent:#2a78d6;--ok:#0f8a4d;--warn:#c98500;--danger:#cf3a3a;--pro:#4a3aa7;--pro-bg:#eeedfe;--pro-t:#26215c}
@media(prefers-color-scheme:dark){:root{--bg:#151513;--card:#1e1e1c;--soft:#242422;--line:#33332f;--t1:#f3f2ec;--t2:#c3c2b7;--t3:#8b8a83;--accent:#5a9bea;--ok:#3cc07f;--warn:#e0a53a;--danger:#e26b6a;--pro:#9085e9;--pro-bg:#2a2560;--pro-t:#cecbf6}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--t1);font-family:-apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei",sans-serif;font-size:15px}
a{color:var(--accent);text-decoration:none}a:hover{text-decoration:underline}
.wrap{max-width:1080px;margin:0 auto;padding:20px 18px 70px}
.nav{display:flex;gap:4px;border-bottom:.5px solid var(--line);margin-bottom:16px}
.nav a{padding:11px 16px;color:var(--t2);border-bottom:2px solid transparent;margin-bottom:-1px}
.nav a.active{color:var(--t1);border-bottom-color:var(--accent);font-weight:500}
.nav a:hover{color:var(--t1);text-decoration:none}
h1{font-size:20px;margin:0}.muted{color:var(--t3);font-size:13px}
h2{font-size:15px;margin:22px 0 10px}
.grid{display:grid;gap:12px;grid-template-columns:repeat(auto-fit,minmax(150px,1fr))}
.tile{background:var(--soft);border-radius:10px;padding:13px}.tile .l{font-size:13px;color:var(--t2)}.tile .v{font-size:22px;font-weight:600;margin-top:3px}.tile .s{font-size:13px;color:var(--t3);font-weight:400}
.card{background:var(--card);border:.5px solid var(--line);border-radius:12px;padding:6px 12px;overflow-x:auto}
table{width:100%;border-collapse:collapse;font-size:13px;white-space:nowrap}
th{text-align:left;font-weight:500;color:var(--t2);padding:9px 10px;border-bottom:.5px solid var(--line)}
td{padding:9px 10px;border-bottom:.5px solid var(--line)}
.pv{font-size:11px;padding:1px 7px;border-radius:6px;background:var(--pro-bg);color:var(--pro-t)}
.pub{font-size:11px;padding:1px 7px;border-radius:6px;background:var(--soft);color:var(--t3)}
.banner{background:#fbf0d8;color:#7a5200;padding:11px 14px;border-radius:10px;font-size:13px;margin:12px 0}
@media(prefers-color-scheme:dark){.banner{background:#3a2f12;color:#e0b055}}
__NAVCSS__</style></head><body>__NAVSTART__<div class=wrap>
__NAV__
<h1>GitHub 私有全量</h1>
<div class=muted id=sub>加载中…</div>
<div id=cap></div>
<div class=grid id=kpi style=margin-top:14px></div>
<h2>全部仓库(含私有)</h2>
<div class=card><table><thead><tr><th>仓库</th><th>可见性</th><th>语言</th><th>7d</th><th>30d</th><th>分支</th><th>PR</th><th>Issue</th><th>Git 占用</th><th>Release</th><th>CI</th><th>通过率</th><th>最近 push</th></tr></thead><tbody id=repos></tbody></table></div>
<h2>Monorepo 子项目活跃度</h2>
<div class=card><table><thead><tr><th>子项目</th><th>所在仓</th><th>路径</th><th>30 天提交</th><th>最近提交</th></tr></thead><tbody id=sub2></tbody></table></div>
<div class=muted style=margin-top:20px>仅本人(Cloudflare Access)可见 · 数据每 30 分钟采集 · 只读</div>
</div>
<script>
function fb(b){if(b==null)return '—';const u=['B','KB','MB','GB','TB'];let i=0,v=b;while(v>=1024&&i<u.length-1){v/=1024;i++}return v.toFixed(i>0&&v<100?1:0)+' '+u[i]}
function fkb(k){return fb((k||0)*1024)}
function tile(l,v,s){return '<div class=tile><div class=l>'+l+'</div><div class=v>'+v+(s?' <span class=s>· '+s+'</span>':'')+'</div></div>'}
fetch('/admin/api/github').then(r=>r.json()).then(g=>{
 if(!g||g.available===false){document.getElementById('sub').textContent=(g&&g.note)||'暂无数据';return;}
 const t=g.totals||{};
 document.getElementById('sub').textContent='账号 '+((g.account||{}).login||'')+' · 采集 '+g.collected_at+' · API 余量 '+((g.rate||{}).remaining||'—')+'/'+((g.rate||{}).limit||'—');
 const cap=g.capability||{};
 document.getElementById('cap').innerHTML='<div class=banner>Traffic:'+(cap.traffic||'—')+' · Billing:'+(cap.billing||'—')+' — 授权含 Administration:read 的只读令牌后这两项自动采集,不可用时如实标注。</div>';
 document.getElementById('kpi').innerHTML=[
   tile('仓库',t.repos,t.public+' 公开 / '+t.private+' 私有'),
   tile('提交 7d',t.commits_7d,'30天 '+t.commits_30d),
   tile('Open PR',t.open_pr,'Issue '+t.open_issue),
   tile('CI 失败',t.ci_fail,'仓'),
   tile('Git 占用',fkb(t.git_size_kb),'Release '+fb(t.release_bytes))
 ].join('');
 document.getElementById('repos').innerHTML=(g.repos||[]).map(r=>{
   const vis=r.private?'<span class=pv>私有</span>':'<span class=pub>公开</span>';
   const ci=r.ci&&r.ci.last_conclusion?(r.ci.last_conclusion==='success'?'<span style=color:var(--ok)>通过</span>':'<span style=color:var(--danger)>'+r.ci.last_conclusion+'</span>'):'<span class=muted>—</span>';
   const pr=r.ci&&r.ci.pass_rate!=null?r.ci.pass_rate+'%':'—';
   return '<tr><td><a href="'+r.url+'" target=_blank>'+r.name+'</a>'+(r.archived?' <span class=muted>归档</span>':'')+'</td><td>'+vis+'</td><td class=muted>'+(r.top_lang||'—')+'</td>'+
     '<td>'+(r.commits_7d??'—')+'</td><td>'+(r.commits_30d??'—')+'</td><td>'+(r.branches??'—')+'</td><td>'+(r.open_pr??'—')+'</td><td>'+(r.open_issue??'—')+'</td>'+
     '<td class=muted>'+fkb(r.size_kb)+'</td><td class=muted>'+fb(r.release_bytes)+'</td><td>'+ci+'</td><td class=muted>'+pr+'</td><td class=muted>'+(r.pushed_at||'—')+'</td></tr>';
 }).join('');
 document.getElementById('sub2').innerHTML=(g.subprojects||[]).map(s=>
   '<tr><td>'+s.project+'</td><td class=muted>'+s.repo+'</td><td class=muted>'+s.path+'</td><td>'+(s.commits_30d??'—')+'</td><td class=muted>'+(s.last_commit_at||'—')+'</td></tr>').join('')||'<tr><td colspan=5 class=muted>暂无</td></tr>';
}).catch(e=>{document.getElementById('sub').textContent='加载失败:'+e});
</script></body></html>"""



# ===== 全站统一顶栏(与主页 index.html 完全一致,消除页面间跳转割裂)=====
NAV_CSS = """
.topbar{position:sticky;top:0;z-index:20;background:var(--bg);border-bottom:.5px solid var(--line)}
.topbar .in{max-width:1120px;margin:0 auto;padding:0 20px;display:flex;align-items:center;gap:14px;flex-wrap:wrap}
.brand{display:flex;align-items:center;gap:8px;padding:12px 0;font-weight:600;font-size:15px;color:var(--t1)}
.brand:hover{text-decoration:none}
.brand .lg{width:20px;height:20px;border-radius:5px;background:var(--accent);display:inline-block;flex:none}
.tabs{display:flex;gap:2px;flex-wrap:wrap;flex:1}
.tabs a{padding:12px 12px;font-size:14px;color:var(--t2);border-bottom:2px solid transparent;margin-bottom:-1px;white-space:nowrap}
.tabs a:hover{color:var(--t1);text-decoration:none}
.tabs a.on{color:var(--t1);border-bottom-color:var(--accent);font-weight:600}
.exts{display:flex;gap:6px;align-items:center;padding:8px 0}
.exts a{font-size:12px;padding:5px 11px;border:.5px solid var(--line);border-radius:8px;color:var(--t2)}
.exts a:hover{background:var(--soft);color:var(--t1);text-decoration:none}
.exts a.on{background:var(--accent);color:#fff;border-color:var(--accent)}
"""

_TABS = [("/#/", "总览"), ("/#/projects", "项目"), ("/#/host", "主机"), ("/#/cost", "成本"),
         ("/#/usage", "用量"), ("/#/heal", "自愈"), ("/#/github", "GitHub")]


def nav_html(active):
    tabs = "".join('<a href="%s">%s</a>' % (h, t) for h, t in _TABS)
    ex = "".join('<a href="%s"%s>%s</a>' % (h, ' class="on"' if h == active else "", t)
                 for h, t in [("https://uptime.linzezhang.com", "运维健康"),
                              ("/admin", "价格设置"), ("/admin/github", "GitHub 私有")])
    return ('<div class="topbar"><div class="in">'
            '<a class="brand" href="/"><span class="lg"></span>LinzeCloud</a>'
            '<nav class="tabs">' + tabs + '</nav>'
            '<div class="exts">' + ex + '</div></div></div>')



EDITOR_HTML = (EDITOR_HTML.replace("__NAVCSS__", NAV_CSS)
               .replace("__NAVSTART__", nav_html("/admin"))
               .replace("__NAV__", ""))
GITHUB_HTML = (GITHUB_HTML.replace("__NAVCSS__", NAV_CSS)
               .replace("__NAVSTART__", nav_html("/admin/github"))
               .replace("__NAV__", ""))

if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", PORT), H).serve_forever()
