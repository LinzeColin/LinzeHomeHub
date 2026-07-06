# AGENTS.md — LinzeHomeHub

## Language

默认中文回复；代码、API、库名、错误信息可保留英文。

## Product Rules

- 首屏标题必须是 `Linze Home Hub`。
- 禁止显示 `A living atlas of systems, memory, research, and tools.`。
- `Archive / Nebula / Voyage / Garden` 必须是四套不同系统，不只是换色。
- 项目入口是整张星球卡点击。
- 不显示显式 `Open / Docs / GitHub` 按钮。
- 不显示最后更新时间字段。
- 滚动方向和速度必须影响粒子、物理和读数。
- 支持 `prefers-reduced-motion` 和质量分档。

## Validation

优先运行：

```bash
npm run validate
npm run build
npm run preview
npx wrangler deploy --dry-run
```

## Safety

不要提交 secrets、token、私有数据、原始导出、浏览器状态、cookie、session、本机凭据或不必要的本机绝对路径。
