# Jarvis cockpit, softer and roomier: three options

Design only, for the owner to pick from. No SwiftUI is changed here.

The owner used the built panel on 2026-11-20 and said: the style is fine, but the lines look rigid and dated, the keys
look squeezed into fixed compartments and crowded, each key should have more distance, and the lines should be rounder.
What stays: the style, the core key, the springs, the flows (photo, ask, backfill, plan, quick log, voice), the
listening ring, the colours, the glow. What changes: the geometry of the panel and its keys.

| | |
|---|---|
| Moving prototypes | `before.html`, `option-a.html`, `option-b.html`, `option-c.html` (open in a browser, tap the key, then the keys) |
| Films, 60 fps, 21 s | `option-a.mp4`, `option-b.mp4`, `option-c.mp4` (voice question → reply card; 记一餐 → second ring → logged card; 记训练 → second ring → training screen) |
| Previews for the PR | `option-a.gif`, `option-b.gif`, `option-c.gif` (20 fps, the lower part of the screen; judge motion on the mp4) |
| Boards | `png/board-a.png`, `png/board-b.png`, `png/board-c.png`, `png/board-compare.png` |
| Source | `soften.js` (one script, the geometry is a parameter), `board.html`, `record.mjs`, `render.sh` |

`soften.js` is a copy of the approved `../jarvis-cockpit.html` (third pass) in which only the block `GEO` and the three
`keys…` functions differ per option. Parameters: `?opt=before|a|b|c`, `size=se` (375 × 667), `theme=light`, `lang=en`,
`demo=1&t=<ms>` (a frame of the film), `still=1`.

```bash
bash docs/design/2.0.1/jarvis-soften/render.sh
```

"Before" is the prototype run with the numbers of `CockpitGeometry` on `main` and the built key sets (three keys in the
记训练 ring). The SwiftUI canvas is a line-by-line port of that prototype, so the geometry is the same; it was not
re-photographed from the simulator, because other sessions were building on this Mac.

## 1. Diagnosis: why the built panel reads as rigid and crowded

Measured on `CockpitGeometry` / `CockpitCanvas` (`apps/ios/Watch2Whoop/Features/Jarvis/Cockpit/`), core at the centre,
keys 36° apart.

| What | Built | Why it hurts |
|---|---|---|
| Key shape | ring sector, radius 80 → 136 (56 pt deep), 32° wide | the shape is a slice of the ring, not an object |
| Corner radius | 0 (a 1 pt round join) | 20 hard corners on screen |
| Edges | 2 of 4 edges per key are straight, 56 pt long | 10 straight radial walls; 8 of them face a neighbour |
| Gap between keys | 4°: 5.6 pt at the inner edge, 7.5 pt at mid radius, 9.5 pt outside | **12 % of the key's width** (60.3 pt at mid radius) |
| Share of the arc that is filled | 160° of 176° = **91 %** | no ground between the figures: five keys read as one cut-up slab |
| Strokes per key | outline 1 pt at 50 % + lit outer edge 2 pt at 95 % + glow 6 pt at 40 % | every key is drawn three times |
| Concentric lines in 73 pt | inner edge 80, outer edge 136, lit edge 136, tick ring 143–150 (7 pt outside the keys), selection arc 153 | five lines stacked behind each other |
| Content in the key | icon 22 pt + label 10.5 pt = 39 pt of the 56 pt depth; a three-character Chinese label has about 10 pt to each wall, "Workout" about 4 pt | text touches the walls in English |
| Touch area | a circle of radius 31 (62 pt) at radius 108; circles 4.7 pt apart | fine in size, but about **13 % of each drawn key (its four corners) is outside the circle: a tap there closes the panel** |
| Way back (second ring) | circle of 26 pt, touch radius 18 (36 pt) | below 44 pt |

The real cause is one decision, not five details: **the keys were defined by dividing the ring** (five slices, thin
cuts between them), so each key's outline is the wall it shares with its neighbour. Walls 5–9 pt apart are what the
eye reads as "compartments", and a wall cannot be made to feel roomy by rounding it. The owner's four remarks (rigid,
crowded, more distance, rounder) are the same remark. The fix that follows: a key is an object placed in space, its
outline belongs to it alone, and the empty space between keys is wide enough to be seen as space (a third of the key
or more) or there are no walls at all.

Two things follow from first principles, in every option:

- **The touch area is the wedge, not the drawing.** Each key answers in its whole 36° wedge from radius 72 to 164
  (44 pt wide at its narrowest, 92 pt deep). The drawn key can then shrink without the target shrinking, and the dead
  corners disappear. This is how pie menus get their speed (Callahan et al., 1988): the target is the direction.
- **Fewer concentric lines.** The glow drops from 40 % to 30 %, the outline from 50 % to 30 %.

Design history that limits the options: the second pass ("one arc and five nodes, nothing else") was rejected as too
ordinary. The task's example (b), round keys on an orbit joined by a hairline, is that second pass again. Option B
below is drawn so that it is not, and section 5 says where it still risks being.

## 2. The three options

All numbers in points; angles in degrees; "mid radius" is where the icon sits.

### A · soft keycaps (the same ring, cut loose and rounded)

The five panels of one instrument ring stay, as approved in the third pass, but each becomes a separate keycap.

| Rule | Value | Was |
|---|---|---|
| Ring | radius 82 → 134 (52 deep) | 80 → 136 |
| Key span / gap | 26° / 10° | 32° / 4° |
| Gap | 14.3 inside, **18.8 at mid radius (38 % of the 49.0 pt key)**, 23.4 outside | 5.6 / 7.5 (12 %) / 9.5 |
| Arc filled | 74 % | 91 % |
| Corners | radius 13 on all four | 0 |
| Straight wall | 26 pt | 56 pt |
| Outline / lit edge | 1 pt at 30 % / 1.5 pt, only between the round corners | 1 pt at 50 % / 2 pt |
| Tick ring | 146–152, 12 pt outside the keys | 143–150, 7 pt |
| Reply card | radius 22, no cut corner | radius 6, 18 pt chamfer |

SwiftUI: `CockpitGeometry.panelInner/panelOuter/panelHalfAngle`, the tick radii, one new constant `panelCorner`;
`CockpitCanvas.sector` gets a rounded version (`Path.addArc(tangent1End:tangent2End:radius:)` at the four corners,
about 15 lines); the lit edge shortens; `CockpitCardShape` becomes a rounded rectangle; `CockpitMachine.tap` tests the
wedge. Motion: unchanged, the same springs drive the same values. Performance: the same five fills. Small screens:
outermost line at 156 pt, 31 pt from the edge of a 375 pt screen. Accessibility: VoiceOver elements unchanged.

Weak point: the label is still inside the key, and the key is narrower (42 pt wide where the label sits), so
"Workout" and "Backfill" at 10.5 pt touch the walls; A needs 10 pt labels in English or labels of at most six
letters.

### B · satellites (round keys on one orbit, no walls)

Every element of Jarvis is already a ring (the core, its three segments, the listening ring, the auto-send ring); the
sectors were the only shape that was not. B makes the keys small relatives of the core.

| Rule | Value |
|---|---|
| Orbit | radius 116, a 1 pt hairline at 26 %; it shows only between the keys |
| Key | circle of 48 pt, panel fill, ring 1 pt at 42 % |
| Gap | centres 71.7 apart → **23.7 pt of empty space, 49 % of the key**; share of the orbit covered by keys 67 % |
| Lit rim | 84° of the key's own ring, 1.8 pt, on the side away from the core (the core's segment, repeated) |
| Label | outside, 15 pt under the key, 10.5 pt at 72 %; a saved meal shows its kcal in the key and its name under it |
| Tick ring | 148–154, 14 pt outside the keys |
| Straight edges / corners | 0 / 0 |

SwiftUI: new constants `orbitRadius`, `keyRadius`; the panel loop in `CockpitCanvas.draw` draws a circle and an arc
instead of a sector (shorter than today); labels move to `c.y + keyRadius + 15`; `panelMid` becomes 116 (VoiceOver
positions follow). Motion: the same fan springs; the key travels from the core to the orbit while it grows.
Performance: circles are cheaper than sectors. Small screens: side labels end 59 pt from the edge of a 375 pt screen;
the top key's label sits 10 pt above the way-back key in the second ring, the tightest spot. English: labels are
outside, so length is not limited by a wall (the best of the three). Accessibility: the outside label has room to grow with Dynamic Type (to be
measured in SwiftUI); VoiceOver elements move with the keys.

### C · ribbon (one band, the keys are marks on it)

No compartments at all: one soft band with round ends, the icons sit on it, and the instrument scale moves onto the
band's own edge.

| Rule | Value |
|---|---|
| Band | centre line radius 110, 56 pt wide, round ends of radius 28, from −74° to 74° (five keys), ±38° (three keys) |
| Walls between keys | **0** |
| Distance | icons 65.5 pt apart, 43 pt of empty band between two icons |
| Scale | ticks every 4° on the inside of the outer edge; every ninth is longer and stands exactly over a key, in both rings (the ring turns 36° per level) |
| Outline / lit edge | 1 pt at 30 % / 1.5 pt along the outer edge |
| Pressed key | a lit node of 46 pt appears under the icon; the rest of the band dims |
| Straight edges / corners | 0 / 0 |

SwiftUI: the five panel springs stay for the icons; one more spring (`band`) and one value (`extent`) drive a single
path (two arcs and two half circles); the separate tick ring is removed; `CockpitGeometry` loses `panelHalfAngle`,
gains `bandHalfWidth`. Motion: the band sweeps out of the core from the middle to both ends, the icons follow; between
rings it closes and re-opens to the new length. This is the most fluid of the three and the largest change to the
approved motion. Performance: one fill instead of five, the cheapest. Small screens: 138 pt outer radius, 49 pt from
the edge. Accessibility: the keys have no outline of their own, so "Button Shapes" and low-vision users get less;
VoiceOver is unchanged.

## 3. Effects after the first one

- **Fewer hard-coded walls means the key count can change.** B and C take three, four or five keys without a new
  layout rule (the band just gets shorter, the orbit emptier); A and the built panel look gap-toothed with three.
- **Wedge touch areas change what a miss means.** Today a near miss closes the panel. With wedges, the only way to
  close is the core or a tap outside radius 164 or inside 72; closing by accident gets rarer, closing on purpose needs
  the core. The second-ring way back (36 pt today) should become a 44 pt target in the same change.
- **Labels outside (B) free the key for a number.** Saved meals show `570` in the key; the same slot can later show a
  timer or a set count. Labels inside (A, C) cannot.
- **C hides the boundary between two actions.** With no wall, 记一餐 and 记训练 are told apart by icon and position
  only; the long tick over each key carries that load.
- **Third order:** whichever shape is chosen becomes Jarvis's shape on the watch and in the widget. A circle (B)
  already exists there (complications are round); a band (C) maps onto the watch bezel; a keycap (A) maps to neither.

## 4. A comparable solution from another field

- **A, typesetting.** Crowded text is not fixed by redrawing the letters but by tracking and leading; the letters
  stay, the white space is set as a share of the letter. A does exactly that and nothing more: a rule (gap ≥ ⅓ of the
  key) rather than a new shape. It teaches that A is a correction, not a new idea.
- **B, the transit map.** Beck's Underground map draws a station as a ring on a line and puts its name beside it. The
  line carries continuity, the ring carries "you can stop here", the name never sits inside the mark. It teaches where
  the label belongs, and why the hairline may be faint: it only has to be continuous.
- **C, the ruler and the watch bezel.** A ruler has no boxes between centimetres; a tachymeter bezel is one band with
  marks. Positions are separated by a mark and by distance (Gestalt proximity), not by walls. It teaches that the
  long tick over each key is not decoration: it is what replaces the wall.

## 5. The strongest objection to each

- **A:** it is the same thing with more air. The keys are still slices of a ring, each still has two straight sides,
  and the shield-like outline (wide outside, narrow inside) is a shape of its own that some will read as heavier than
  the slab it replaces. If "dated" was about the sector itself, A does not answer it.
- **B:** round buttons on an arc are what every radial menu looks like, and the second pass was rejected for exactly
  that. The lit rim and the tick ring give it more character than the second pass had, but as a still picture it is
  the least distinctive of the three; its character is in the family resemblance with the core, which is subtle.
- **C:** it is the opposite of "more distance between keys" read literally: the filled share goes up to 100 %, a
  large dark mass sits over the page, and the keys stop being objects. It also changes the approved opening motion
  the most, and keys without an outline are weaker for accessibility.

## 6. Recommendation

My first favourite was C: it is the only one that removes the cause (no walls, so nothing to be squeezed into) rather
than treating it, it is the most fluid in motion and the cheapest to draw. Arguing against it: the owner asked for
distance and for keys that are not squeezed, which presumes there are still keys; C answers by dissolving them, and on
the board it is the heaviest object of the three, a slab again, only without cuts. It also spends the most of what was
approved on 2026-11-12 (panels that fan out one by one). And B's objection, "ordinary", is a risk about taste that
only the owner can settle, while C's objections (mass, affordance, accessibility) are measurable.

Arguing against B in turn: it resembles the rejected second pass. But the second pass was rejected for having no
character, not for being round, and what the owner asked for on 2026-11-20 (rounder, more distance, not in
compartments) is B's definition: 49 % gaps, no straight edge, no corner, the label out of the key, and the only shape
language that is already Jarvis's own.

**Recommendation: B, with A as the fallback.** B gives the most room (49 % against 38 %), has no straight edge at
all, frees the label from the key (the only option where English is not tight), and repeats the core's ring instead
of adding a second shape language. If B's film still feels ordinary to the owner, take A: it is the smallest change
(three numbers and one rounded path), keeps everything approved in the third pass, and can ship first. C is worth
keeping as the idea for the watch, where a bezel band is native.

Whichever is picked, two changes are independent of the choice and should go in with it: wedge touch areas (removes
the dead corners) and a 44 pt way-back target.

## 7. Open questions

1. The reply card: all three options draw it with radius 22 and no cut corner, to match the rounder keys. The cut
   corner was part of the approved third pass. Keep the chamfer, or round it?
2. B: label under the key (as drawn), or no label at all after the first few uses (text tends to zero)?
3. Wedge touch areas make an accidental close rarer and a deliberate close by "tapping anywhere" narrower. Is closing
   by the core and by a tap well outside the ring enough?
4. Light theme: the panel sits on an 86 % black wash in both themes (as built), so light and dark differ only in what
   shows through. Is that still wanted, or should the light theme get its own wash in a later pass?
5. Should the watch and widget follow the phone's choice in 2.1.0, or later?

## Assets and licences

Geometry only (circle, arc, rounded sector, band). Icons: Lucide (ISC), from `../kit.js`. Backgrounds: the kit's own
mockups (`../01-today-*.png`, `../11-training-dark.png`). Data: synthetic.
