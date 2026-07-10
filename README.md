# Linze Home Hub

Premium personal project gateway built with Vite, TypeScript, Three.js, Rapier, GSAP, Lenis, and Cloudflare Workers Static Assets.

## What It Does

- Recreates the supplied `LinzeHomeHub-preview-v0.3.html` default Archive experience as a modular app.
- Provides four distinct systems: `Archive 档案`, `Nebula 星云`, `Voyage 夜航`, `Garden 花园`.
- Supports six hero models: `星图仪`, `漂浮岛`, `档案书`, `宇宙罗盘`, `黑金花园`, `能量核心`.
- Uses scroll direction and speed as a shared gravity signal for particles, readouts, and Rapier bodies.
- Renders project planets from `src/data/projects.json`; whole-card links use `liveUrl` first and `fallbackUrl` second.
- Presents a five-surface Launch Constellation for EEI, OpenAIDatabase / MemoryAtlas, PFI, Serenity-Alipay, and Archive/nab.
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
- `Protected` requires a verified URL plus an explicit access-control boundary; MemoryAtlas uses owner-allowlist Cloudflare Access and is not presented as anonymous public access.
- Empty `liveUrl` values fall back to the public GitHub source path.
- Every card remains L2 static-first; future L3 data, auth, write, and automation capabilities stay gated.

Current verified routing: EEI, PFI, Serenity-Alipay, and Archive/nab are `Live`; MemoryAtlas is `Protected`. No card currently relies on a deploy-ready fallback.

## Safety

This repository should not contain secrets, raw exports, browser state, cookies, sessions, or private data. Only verified live or access-protected URLs are stored; any future unverified project card must point to a public source fallback.
