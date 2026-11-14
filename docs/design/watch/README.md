# Apple Watch app: UI mockups for review

Static mockups and two moving prototypes for the watchOS app planned in [`docs/watch-app-plan.md`](../../watch-app-plan.md).
They are **pictures, not the app**: nothing here is built in Swift, and every number, name and meal is synthetic
(nickname 「小林」).

The design language is the phone's 2.0.1 direction (`docs/design/2.0.1/direction.md` on the `design/ui-2.0.1` branch):
one subject per screen, numbers and graphics instead of sentences, colour only for state (good / mid / bad) or category
(sleep / train). The ice-blue accent and the cockpit core belong to Jarvis only; every other screen is ink on black.

## Screens

| Flow | Screens | Page |
|---|---|---|
| Logging sets during training | T1 start → T2 set → T3 / T4 crown (reps / weight) → T5 logged (1.2 s) → T6 rest → T7 next exercise → T8 last set: reps left → T9 / T10 summary (facts, no score) | `board.html?flow=train` |
| A meal by voice | M1 hold to talk → M2 transcript, 1.5 s to cancel → M3 receipt → M4 undo asks first → M5 four quick templates | `board.html?flow=meal` |
| One question to Jarvis | J1 listen → J2 think → J3 answer card → J4 "continue on the phone" | `board.html?flow=ask` |
| Today at a glance | D1 training day, D2 rest day | `board.html?flow=today` |
| Watch face and Smart Stack | C1 inline + circular + rectangular, C1B corners, C2–C4 Smart Stack (morning, training time, in a session) | `board.html?flow=face` |
| Sync and offline states | S1 linked, S2 syncing, S3 stale, S4 phone away, S5 catching up, S6 failed, S7 no network, S8 not signed in, S9 no plan | `board.html?flow=state` |

- `board.html` (no query): the overview board, every screen at 46 mm.
- `board.html?flow=<name>`: one flow at 46 mm with what the subject is and what a tap, a Double Tap and the Digital Crown
  do on each screen, then the same screens at 42 mm.
- `board.html?id=t2`: one screen as a card (46 mm and 42 mm side by side, full notes including haptics).
- `film.html?film=sets` and `film.html?film=meal`: the two moving prototypes. They play in a loop in the browser; the
  chips under the watch light up when a tap, a Double Tap, the crown or a haptic is in play. `&t=<ms>` gives one frame.

## Sizes

46 mm = 208 × 248 pt, 42 mm = 187 × 223 pt. The two have the same aspect ratio, so the 42 mm watch shows the same layout
at 0.899; the smallest text (11 pt) is 9.9 pt there. The outline around the screen (body, crown, side button) is plain
geometry; the corner radius is an approximation, to be checked on a real watch before any Swift is written.

## Files

- `watch.js`: tokens, drawing helpers, every screen as a pure function that returns SVG, the device outline and the
  catalogue (`LIST`) with each screen's notes. The films call the same functions with other parameters.
- `lucide.js`: Lucide 1.61.0 icons (ISC): the ones vendored in the app plus `play`, `pause`, `wifi-off`, `unlink`,
  `arrow-down`, `arrow-right`, `chevron-up`, `trending-up`, `trending-down`, `equal`, `mic-off`, `pointer`, `rotate-cw`,
  `skip-forward`, `square`, `vibrate`, `watch`, taken unchanged from the same release. No icon is drawn by us; the app
  would add the new ones through `apps/ios/scripts/lucide_assets.py`.
- `watch.css`: the page around the screens (boards, cards, captions).
- `render.sh`, `render-film.sh`: headless Chrome on macOS → PNG; one screenshot per frame + ffmpeg → 60 fps mp4 and
  30 fps gif. Output goes to `out/` (not in git; the rendered images are on the `assets/pr-446-screenshots` branch).

```bash
bash docs/design/watch/render.sh "docs/design/watch/board.html?flow=train" docs/design/watch/out/flow-train.png
```

```bash
bash docs/design/watch/render-film.sh sets 21000 docs/design/watch/out
```

Fonts: `system-ui` only. Icons: Lucide only. The Jarvis core is the geometry approved for the phone (tick ring, three
segments, inverted triangle with a lit inner triangle).
