# Linze Home Hub

Premium personal project gateway built with Vite, TypeScript, Three.js, Rapier, GSAP, Lenis, and Cloudflare Workers Static Assets.

## 📦 数据落地政策（长期有效 · 自运行分仓治理）

**本仓只存前端代码与展示配置，长期/业务/运行时数据不入本仓。** 开发中产生的任何需长期存储的数据
一律写入私有仓 `LinzeColin/Private-Database`（其余项目数据 → `Private-MetaDatabase/`），用 `private_db_client.py` 免 clone 读写；
Private-Database 禁止 `git clone`；派生/临时物走 `.gitignore`。**一次分清、长期自运行，不再需要人工反复迁移。**
> 例外：`status/` 面板的公开监控数据（价格快照等）按设计随每日备份入仓，属展示所需的公开数据，不含 PII。

## What It Does

- Recreates the supplied `LinzeHomeHub-preview-v0.3.html` default Archive experience as a modular app.
- Provides four distinct systems: `Archive 档案`, `Nebula 星云`, `Voyage 夜航`, `Garden 花园`.
- Supports six hero models: `星图仪`, `漂浮岛`, `档案书`, `宇宙罗盘`, `黑金花园`, `能量核心`.
- Uses scroll direction and speed as a shared gravity signal for particles, readouts, and Rapier bodies.
- Renders project planets from `src/data/projects.json`; whole-card links use `liveUrl` first and `fallbackUrl` second.
- Presents a three-surface Launch Constellation for EEI, PFI, and Serenity-Alipay (plus the Account entry). MemoryAtlas and Archive/nab were retired on 2026-09-30 (Owner decision) and their cards removed; restore the two entries in `src/data/projects.json` and `scripts/validate-homehub.mjs` if either comes back.
- Supports `?quality=low|medium|ultra` and `prefers-reduced-motion`.

## Local Development

```bash
npm install
npm run dev
```

## Validation

```bash
npm run validate
npm run build
npm run preview
npm run acceptance:visual
npx wrangler deploy --dry-run
```

The visual acceptance script expects a running preview server at `http://127.0.0.1:4173` unless `HOMEHUB_URL` is set.

## Deployment

### 线上部署（拉取式，免令牌）

**怎么部署：把改动合入 `main`，约 5 分钟（最长 8 分钟）内自动上线，不用点任何东西，也不需要任何令牌。**
生产机上的 systemd 定时器每 5 分钟看一眼 `main`；有新提交就构建新镜像、起新容器、健康检查通过再切流量并停掉旧的，任何一步失败旧版本原样保持在线。

**怎么看它活着：**

- `curl https://home.linzezhang.com/version.txt` —— 线上这一版的提交号，应等于 `main` 最新提交。
- 服务器上 `sudo linze-pull-deploy.sh status home-hub` —— 状态、容器、下次触发时间；`systemctl list-timers 'linze-pull-deploy@*'`。

脚本、unit、配置都在 [`deploy/pull/`](deploy/pull/README.md)（参数化模板，别的项目改一份 `.env` 即可套用）。

### 早期方案（仅供参考，线上不走这条）

Cloudflare Workers Static Assets is configured in `wrangler.jsonc`:

```jsonc
{
  "name": "linze-home-hub",
  "compatibility_date": "2026-07-06",
  "assets": {
    "directory": "./dist",
    "not_found_handling": "single-page-application"
  }
}
```

Deploy with:

```bash
npm run build
npm run deploy
```

Suggested domain: `home.linzezhang.com`, with `linzezhang.com` available as a later apex route.

## Launch Constellation fact rules

- `Live` requires a URL that was actually reached and verified.
- `Deploy-ready` means build, safety scan, and Wrangler dry-run are ready, but the public deployment is not yet verified.
- `Protected` requires a verified URL plus an explicit access-control boundary (no card currently uses it; MemoryAtlas, the previous user, was retired on 2026-09-30).
- Empty `liveUrl` values fall back to the public GitHub source path.
- Every card remains L2 static-first; future L3 data, auth, write, and automation capabilities stay gated.

Current verified routing: EEI, PFI, and Serenity-Alipay are `Live`. No card currently relies on a deploy-ready fallback.

## Safety

This repository should not contain secrets, raw exports, browser state, cookies, sessions, or private data. Only verified live or access-protected URLs are stored; any future unverified project card must point to a public source fallback.
