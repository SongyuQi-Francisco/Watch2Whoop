# iOS 2.0.1 UI redesign: mockups for review

Static mockups (HTML rendered to PNG) for the native iOS redesign planned for 2.0.1. They are **pictures, not the app**: nothing here is built in Swift, and every number, nickname and date is synthetic.

The design rules are in [direction.md](direction.md): one instrument per tab, shape is hierarchy, every stroke is data, progressive disclosure L0 → L3.

## Screens (review order)

| File | Screen | PNG |
|---|---|---|
| `01-today.html` | Today, first glance | `01-today-dark.png`, `01-today-light.png` |
| `01b-today-d13.html` | Today with three rings (comparison for decision D13; the owner chose the dial on 2026-11-11) | `01b-today-d13-dark.png` |
| `02-today-open.html` | Today, recovery dial tapped (L1 sheet) | `02-today-open-dark.png`, `02-today-open-light.png` |
| `03-strain-open.html` | Today, strain reading tapped (day line) | `03-strain-open-dark.png` |
| `04-sleep-open.html` | Today, sleep reading tapped (hypnogram) | `04-sleep-open-dark.png` |
| `05-body.html` | 身体 tab | `05-body-dark.png` |
| `06-circles.html` | 圈子 tab | `06-circles-dark.png` |
| `07-me.html` | 我 tab | `07-me-dark.png` |
| `08-sync-center.html` | Sync center sheet (behind state) | `08-sync-dark.png` |
| `09-jarvis.html` | Jarvis chat opened from the key | `09-jarvis-dark.png` |
| `12-record.html` | 记录 tab, 训练 view (set ring with today's muscles inside) | `12-record-dark.png` |
| `10-food.html` | 记录 tab, 饮食 view | `10-food-dark.png` |
| `11-training.html` | Today's training session in progress (ring tapped) | `11-training-dark.png` |
| `13-muscle-week.html` | Weekly muscle load map | `13-muscle-week-dark.png` |
| `14-training-history.html` | Training history (month + session cards) | `14-training-history-dark.png` |
| `15-exercises.html` | Exercise library | `15-exercises-dark.png` |
| `kit-states.html` | States the screens do not show (sync, attention, stale, not worn, first days) | `kit-states-dark.png`, `kit-states-light.png` |

## Files

- `kit.css`: tokens (dark default, light via `html[data-theme="light"]`), device chrome (status bar, iOS 26 tab bar, Jarvis key) and shared components.
- `kit.js`: the Lucide 1.61.0 icons vendored in the app (ISC), `<i data-i="name">` → inline SVG, `?theme=light` support, and small drawing helpers (dial, gauge, bars, sparkline).
- `body.js`: muscle bodies (`data-body`, `data-lit`) and the set ring (`data-sets`), drawn from the app's own `muscle-body-data.js` (MIT, see `THIRD_PARTY_NOTICES.md`).
- `logo/`: app icon concepts for the redesign. `logo.html` draws three concepts (A dial, B dial + pulse, C tick ring) in dark and light; `logo-board.png` is the comparison board, `icon-{A,B,C}-1024.png` the 1024 px dark icons. A and C are plain geometry from the app's own dial; B's pulse is the Lucide `activity` glyph (ISC).
- `_template.html`: starting point for a new screen.
- `render.sh`: renders a page at iPhone 17 Pro size (402 × 874 pt, @3x) with headless Chrome on macOS.

```bash
bash docs/design/2.0.1/render.sh docs/design/2.0.1/01-today.html
```

```bash
bash docs/design/2.0.1/render.sh "docs/design/2.0.1/01-today.html?theme=light" docs/design/2.0.1/01-today-light.png
```

Fonts: the pages use `system-ui` only (SF Pro and PingFang SC on macOS). Icons: Lucide only. The only other asset is the muscle body map the app already ships.
