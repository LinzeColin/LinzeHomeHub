# Design

## Summary

Linze Home Hub uses a dark, image-led WebGL scene as the primary brand surface. The default Archive mode must echo the supplied v0.3 preview: fixed topbar, pill mode dock, gravity telemetry, large serif title, artifact stage, project planets, and a gravity lab. Nebula, Voyage, and Garden share the shell but diverge in particle behavior, model posture, hover response, and Space-key ritual.

## Color System

Use OKLCH custom properties as the authored design tokens, with fallbacks only where tooling requires them.

```css
--bg: oklch(0.070 0.010 265);
--surface: oklch(0.140 0.020 260);
--surface-strong: oklch(0.190 0.026 255);
--ink: oklch(0.930 0.018 250);
--muted: oklch(0.700 0.040 250);
--archive: oklch(0.760 0.125 82);
--nebula: oklch(0.780 0.115 238);
--voyage: oklch(0.760 0.110 226);
--garden: oklch(0.760 0.115 132);
--rose: oklch(0.780 0.115 18);
```

## Typography

Use system serif for display (`Georgia`, `Songti SC`, `Noto Serif SC`) and system sans for controls/body. Display letter spacing must not go tighter than `-0.04em`; body text should stay within 65-75ch where possible.

## Layout

The page is a single brand surface with five sections: hero, blueprint, projects, gravity, future. Cards are reserved for project planets and repeated feature items; page sections remain full-width unframed compositions.

## Motion

Motion is part of the identity: scroll velocity becomes gravity, Space triggers a mode-specific ritual, pointer position attracts particles, and mode/model switches produce a short energy response. Reduced motion keeps telemetry and state changes but lowers particle count, disables large camera movement, and avoids shockwave-scale transforms.

## Mode Worlds

- Archive: paper grain, index lines, slow dust, book/armillary posture, Archive Seal Space impulse.
- Nebula: volumetric blue-violet particle funnel, energy core, soft expansion/compression, black-hole Space impulse.
- Voyage: compass, route splines, directional drift, warp Space impulse.
- Garden: floating island, pollen, bioluminescent petals, bloom pulse Space impulse.

## Components

- Mode dock: four pill buttons with active state.
- Model switcher: six compact buttons, keyboard-cyclable with `M`.
- Gravity telemetry: vector dial plus direction, velocity, `gx`, `gy`.
- Project planets: accessible anchor cards backed only by `src/data/projects.json`.
- Debug/quality: query params may expose `quality=low|medium|ultra` and optional gravity diagnostics.
