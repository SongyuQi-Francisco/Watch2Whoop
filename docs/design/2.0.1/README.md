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
- `logo/`: app icon concepts for the redesign. `logo.html` draws three concepts (A dial, B dial + pulse, C tick ring) in dark and light; `logo-board.png` is the comparison board; `?set=2` draws the second round (D dial + core, E two readings, F green ground, G core) → `logo-board-2.png`; `icon-{A..G}-1024.png` are the 1024 px dark icons. Shapes are plain geometry from the app's own dial plus the Lucide `activity` and `triangle` glyphs (ISC).
- `jarvis-key.html` → `jarvis-key-board.png`: versions of the Jarvis key glyph as an inverted-triangle core (Lucide `triangle` rotated 180°), next to the current `orbit` key.
- `logo/logo.html?set=3` → `logo/logo-board-3.png`, `icon-{H..M}-1024.png`: third icon round, variants of the dial (A) and the tick ring (C).
- Decided by the owner on 2026-11-11: the app icon is **L** (two tick rings, `logo/icon-L-1024.png`) and the Jarvis key was **K1** (one inverted triangle); on 2026-11-12 the owner changed the key to the **first-pass cockpit core** (tick ring, three segments, inverted triangle with a lit inner triangle, soft ice-blue glow). The real `AppIcon` and the app's key change with the SwiftUI work.
- `jarvis-cockpit.html` → `jarvis-cockpit-board.png`, `jarvis-cockpit.gif` (30 fps), `jarvis-cockpit.mp4` (60 fps): a moving prototype of the Jarvis key in a cockpit style, third pass, **approved by the owner on 2026-11-12** (function keys, motion and the flows below; the screens behind 拍照, 提问, 补记, 计划 and 更多 are not drawn yet). The key is the first-pass core and the listening effect is the one of the second pass. In this pass: the function keys are five panels of one instrument ring that fan out of the core on springs, with a tick ring outside; 记一餐 and 记训练 open a second ring (the tick ring turns one notch, the chosen key docks above the core as the way back); the full voice flow (live transcript → pause → one-second auto-send ring → thinking → reply); replies are a structured card that unfolds above the core (a verdict with a gauge, or 「已记到…」 with 撤销). Open it in a browser and tap the key (speech is simulated); `?demo=1&t=ms` gives a frame of the scripted film (three scenes, 21 s), `?tone=ink` the version without glow. Plain geometry and Lucide icons only; the motion ideas (assemble, sweep, stagger) follow open-source sci-fi UI work such as Arwes (MIT), no code or artwork is copied.
- `_template.html`: starting point for a new screen.
- `render.sh`: renders a page at iPhone 17 Pro size (402 × 874 pt, @3x) with headless Chrome on macOS.

```bash
bash docs/design/2.0.1/render.sh docs/design/2.0.1/01-today.html
```

```bash
bash docs/design/2.0.1/render.sh "docs/design/2.0.1/01-today.html?theme=light" docs/design/2.0.1/01-today-light.png
```

Fonts: the pages use `system-ui` only (SF Pro and PingFang SC on macOS). Icons: Lucide only. The only other asset is the muscle body map the app already ships.
