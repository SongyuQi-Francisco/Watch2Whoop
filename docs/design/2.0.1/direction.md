# Watch2Whoop 2.0.1 — final direction: "one instrument per tab"

## 1. The idea
- Every tab root is **one precision instrument** that answers the tab's one question; everything else on the screen is visibly smaller.
- **Shape is hierarchy**: one dial (the answer) → at most two attached readings (what it rests on) → one row of three module faces (the rest of the day).
- **Every stroke is data**: ticks are the scale, long ticks are zone edges, a grey line is *your usual*, ticks in colour are a *target*, dots are *planned*. Tap anything and it grows one level; nothing appears that you did not ask for.

Base: **hero** (ranked first by all three judges, highest on the owner lens). Grafted: from **bento** the zone-edge ticks (34 / 67), the verdict word, the attention-face grammar, the L1 delta column and a sync mark with icon + age; from **timeline** the "now + planned" reading on the strain gauge, the day line as the strain L1 chart, `text2` chart labels, the "全部指标 / 问 Jarvis" exits and the three-wide face row (no pager). Not taken: bento's tile wall and timeline's second hero on the first screen.

Files: `01-today.html` (first glance), `02-today-open.html` (tap on the dial), `kit-states.html` (states the screens do not show), `01b-today-d13.html` (three-ring comparison for the owner), `kit.css`, `kit.js`, `_template.html`.

## 2. Design language
### 2.1 Tokens (dark is default). No existing hex changes; new tokens are opacities of existing ones (Swift: `Palette.x.opacity(a)`).
| Token | Dark | Light | Use |
|---|---|---|---|
| bg | #000000 | #FFFFFF | page |
| surface | #121214 | #F4F4F6 | faces, sync capsule, groups |
| surface2 | #1D1D20 | #E9E9ED | avatar fill, inner buttons |
| text | #F4F4F6 | #111113 | numbers, ink marks |
| text2 | #8D8D94 | #6C6C73 | labels, units, **all chart text** |
| text3 | #55555B | #A3A3AA | chevrons only (never text that must be read) |
| hair | #FFFFFF a.07 | #000000 a.06 | row dividers |
| good / mid / bad | #72BF8E / #D3AE63 / #DE7F69 | #4E9A6B / #B58A34 / #BE5A44 | state only |
| sleep / train | #8D93E4 / #5FB3BD | #6870C4 / #3F8F9A | category only |
| sheet / inSheet | #161618 / #232326 | #FFFFFF / #F4F4F6 | L1 sheets, buttons inside them |
| accent / onAccent | #F4F4F6 / #000000 | #111113 / #FFFFFF | primary button |
| **track** (new) | text a.16 | text a.12 | 1.5 pt tracks of dial, gauge, ring |
| **tick** (new) | text2 a.50 | text2 a.55 | minor scale ticks |
| **tickMajor** (new) | text a.72 | text a.85 | origin, zone edges, majors |
| **bandLine** (new) | text2 a.62 | text2 a.62 | usual range beside a scale (dial lane, gauge line) |
| **band** (new) | text2 a.24 | text2 a.16 | usual range as an area behind a chart |
| **past** (new) | text2 a.42 | text2 a.34 | history bars; only today takes colour |
| **spark** (new) | text a.62 | text a.55 | sparkline stroke |
| **hairStrong** (new) | #FFFFFF a.14 | #000000 a.14 | chart baseline |
Soft tints, scrim, chrome unchanged. Target tint = series colour a.22. Slim look: same rules; see 7.

### 2.2 What changed against the 2.0.0 app, and why
- **Hero form**: three equal-ish rings (91 / 128 / 91) → one 280 pt tick dial + two bullet gauges. Gives the screen a subject (C3). *D13 departure, see 8.*
- **Type**: the hero number becomes large and light (100 / 200) instead of 37 bold; size carries hierarchy, weight stays quiet (precision, not shouting).
- **Rings and bars**: 5 pt arc on a 1.5 pt track (was 7–8 pt on a full `surface2` band); scales drawn as ticks; history in ink, only today in colour (was zone-coloured history).
- **Chart text** `text3` → `text2` (about 2.5:1 → 5:1).
- **No page title on tab roots** (the bar names the tab); header = context left, sync capsule right.
- **Faces** replace cards with section titles: no section headings anywhere on a tab root.
- **Sheets**: iOS 26 floating half sheet, solid; a hero's L1 does not dim the hero.

### 2.3 Type scale (system font, tabular digits)
| Role | Size / weight | Note |
|---|---|---|
| Hero number | 100 / 200, tracking −3 | one per screen; unit hangs (20 / 300 `text2`) |
| Reading number | 28 / 400 | attached to the hero; ratio to hero 1 : 3.6 |
| Face number | 22 / 500 | label 12 → ratio 1.8 |
| Row value (L1) | 19 / 500 + unit 11 | |
| Header date / sheet title | 17 / 600 | |
| Verdict word | 14 / 500, ink | never coloured, max 4 characters |
| Label / unit / chart text | 12 / 500, 11 / 500, 10 / 500 | all `text2` |
Fixed sizes. Dynamic Type is not promised in 2.0.1 (no `ScaledMetric` in the app); every label must fit in both languages at default size.

### 2.4 Shape and spacing (4 pt grid)
Gutter 20. Radii unchanged: face / group 18, button 14, field 12, capsule. Face gap 8, reading gap 20. Tab-root rhythm (402 x 874): status 0–54, header 54–98, **32**, hero 130–410, **40**, readings 450–509, **44**, faces 553–673, **49**, Jarvis key 722. The gap above the key is fixed at 40–56; never a hole, never content under the key. Small phones: dial = min(280, 0.36 x usable height) (667 pt → 220), faces 112, same order.

### 2.5 Chart and ring grammar (one language everywhere)
- **Value**: series colour (recovery = state, sleep / strain = category, everything else ink). Arc 5 pt round, bar 4 pt capsule, line 1.4 pt with a 3 pt end dot.
- **Usual (常态)**: grey. Beside a scale it is a line (`bandLine`, 3 pt inside a dial, 2.5 pt under a gauge); behind a chart it is an area (`band`) with dashed edges. First L1 appearance shows its numbers ("常态 58–74").
- **Target**: ticks in the series colour that rise above the track; a range adds a 22 % tint between them. Labelled with numbers in L1.
- **Planned**: dots in the series colour from "now" to the planned value (strain only today).
- **Scale**: minor ticks = units; long ticks = origin and zone edges (recovery 34 / 67; strain 10 / 14 / 18).
- No axes, no grid; a full chart has one baseline and three date labels (`text2` 10). No pies, 3D, dual axes, gradients, glows.
- New kinds (all iOS 16): tick dial (`Canvas` ticks + `Circle().trim`), bullet gauge (`Capsule` / `Rectangle` in a `GeometryReader`), vitals pills, lollipop (`RuleMark` + `PointMark`). Monotone interpolation only.

### 2.6 Motion (all off with Reduce Motion → cross-fade, values appear)
Arc draws from 12 o'clock (spring 0.6 s); numbers roll (`contentTransition(.numericText())`); the 7 mini bars rise with 20 ms stagger; swiping the hero to another day rolls the number and retargets the arc. Opening L1: system sheet + its chart grows from the right (today first), selection haptic. Sync: `refresh-cw` turns while syncing, success haptic at the end. L2 push: zoom from the tapped element on iOS 18, plain push on 16–17.

## 3. Module contract
| Size | Frame | Rule |
|---|---|---|
| Hero | full width, 280 pt (dial) or ≤ 220 pt (chart) | exactly one per tab root; the only number > 28 pt |
| Reading | half width x 59 | number + bullet gauge; max 2 (food: 3), attached under the hero |
| Face S / M / L | 1/3, 2/3, full width x 120 | S never narrower than 114 pt (fits "1,850 kcal" and two English words) |
| Row (L1) | full width x 46 | label 62 · spark · value · delta vs usual · chevron |
**Face anatomy**: icon 13 + label 12 (≤ 4 characters preferred, 6 max) → one number 22 + unit → one graphic 26 pt high at the bottom, max one colour. No chevrons, buttons or captions on a face (exception: invite face).
**States** (`kit-states.html`): normal · **attention** (6 pt mid / bad dot top right, label states the fact in ≤ 4 characters, latest mark in that colour, takes slot 1) · **stale** (icon → `refresh-cw`, number `text2`, age in `mid` at the right; tap = sync) · loading (keep the last value; skeleton only on first load) · **no data or flag off** (not rendered; the row reflows) · invite (circles without a circle: action instead of number). Core score without data: dial without arc, "—", reason ≤ 6 characters ("昨晚没戴表"). First two weeks: no usual line, mini bars only for existing days.
**Opening**: every hero, reading and face opens the same way on every OS: tap = L1 half sheet (`presentationDetents([.height(h), .large])`). Exception: the circles face switches to the 圈子 tab. Long press = context menu: "问 Jarvis：…" first, then 看详情, 固定 / 从首页移走.
**Disclosure levels**: **L0** glance (hero + readings + faces). **L1** one tap: one chart + ≤ 4 rows + exits (全部指标 / 问 Jarvis); "非诊断" tag on every score, "AI 生成" on AI text. **L2** 全部指标 or a row: pushed page with ranges 7 / 30 / 90 / 365, read-out by drag, sections. **L3**: explanation text, raw samples, sources, Jarvis with the module as context.
**Maximum counts on a tab root's first glance**: 7 blocks, 15 text runs, 6 numbers, 0 sentences, 0 control rows before data, 3 hues (one state colour + sleep + train; an attention face may add its one colour), one number > 28 pt. Today holds ≤ 6 modules: 3 on the first glance, 3 below the fold (vertical scroll only, no pager).

## 4. Tabs and the six open questions
**Bar (D1)**: 今天 `circle-gauge` · 身体 `activity` · 圈子 `users` · 我 `user` + the 58 pt Jarvis key. Flag `friends` off → 今天 · 身体 · 我 (no circles face). Food and log become second-level pages.
1. **Bar structure** → move to D1 now. One instrument per tab only works when food and log stop competing for a tab.
2. **One Today** → yes: one base, classic retired; `ui_ai_first` off = fixed module order (体征, 饮食, 圈子). HRV and resting HR leave the first glance: they are rows 1–2 of the recovery L1 and surface as an attention face when outside the usual.
3. **Asking** → the ask field leaves Today and 身体; the key is the one entry. Tap = chat opens directly (dial retired; camera, voice, quick log move into the composer); hold = voice.
4. **Food** → Today face + second-level page.
5. **Bar area** → no `tabBarMinimizeBehavior` on any page where the key is visible (the key is positioned from a fixed bar height); no bottom accessory; `scrollEdgeEffectStyle(.soft)` on iOS 26 is fine. D14 stays closed.
6. **Tech feel** → the look is open (this file lists every change); the feeling comes from precision (ticks, light numerals, thin tracks, tabular digits, black) and crisp motion. Palette meaning unchanged.

## 5. Screens (review order)
| Screen | One subject | Hero | First glance | Behind taps |
|---|---|---|---|---|
| **today** (done) | How ready am I? | recovery tick dial 82 %, zone ticks, usual lane, 7 bars, verdict | sleep 88 + strain 2.8 readings; faces 体征 (or 关注), 饮食, 圈子 | dial → recovery L1; readings → sleep / strain L1; faces → L1; date → month calendar; swipe hero → other day; capsule → sync center |
| **today-open** (done) | Why 82? | dial stays lit; sheet: 30-day bars on usual band | rows HRV, 静息心率, 呼吸 with delta; exits | row → metric L2; 全部指标 → body metric page; 问 Jarvis → chat with recovery attached |
| strain-open | Am I on track today? | day line 00:00–24:00: cumulative strain (solid), now marker, planned block 18:30, dots to bedtime, target band | rows 步数, 活动能量, 运动分钟, 训练状态 (DiffBar of CTL−ATL) | planned block → log-training; exits |
| sleep-open / sleep night | Last night | hypnogram on a clock axis (入睡 → 起床), duration as the big number | rows 时长 vs 需求 (bullet + need tick), 深睡, 规律性 (7-night window), 最低心率 | 全部指标 → sleep night page (hero hypnogram; faces 和平时比, 夜间指标, 入睡目标 staircase); swipe = other night |
| **body** (身体) | Better or worse? | 30-day bars of the selected score with period mean + delta vs previous period; page dots 恢复 / 睡眠 / 负荷 (swipe = score) | header left "近 30 天 ⌄" (7 / 30 / 90 / 365); faces 心肺 (HRV line), 身体年龄, 周报 | bar → day read-out; faces → L1; below fold: category rows (恢复, 睡眠, 负荷, 压力, 长期) → category L2 |
| log-training | Today's session | segmented arc: one segment per planned set, centre "0/18 组" + split label | exercise rows with set dots; primary "完成训练" pinned above the key | row → set edit sheet; below fold faces 本周 (7 dots), 肌肉 (body map), 历史 (month dots); library, split picker in L2 |
| food | Eaten vs goal | kcal dial (ink arc, target tick at 2,100) | 3 readings 蛋白 / 碳水 / 脂肪 with target ticks; faces 快记, 饮水, 食谱 | face → L1; meals as rows below the fold; logging through the Jarvis key (camera, voice) |
| **circles** (圈子) | How are my people today? | circle dial: members' initials placed at their recovery on the 0–100 scale, centre "3/6" 今天练了 | header left circle picker "跑团 ⌄"; faces 动态 (unread), 排行 (my rank bar), 邀请 | avatar → member day sheet (only shared scopes, ECG never); 动态 → feed; 排行 → boards; manage circles in L2 |
| **me** (我) | Is my data complete and safe? | sync dial: 30 ticks = last 30 days (missing days in mid), centre "30/30 天" or "缺2天" | faces 消息 (unread), AI (quota meter), 共享 (people); ≤ 4 groups / 12 rows below | dial → sync center; rows → health records, AI, preferences, help, account |
| sync center | Synced? If not, one button | sync dial 200 + verdict + one primary button (立即同步 / 重试 / the one self-help step) | 4 type lanes (心率, 睡眠, 训练, 步数) as 14-day dot strips | 更多工具 (L2: re-sync ranges, cellular, log); missing day → re-sync that day |
| jarvis | The answer | answer card first (one graphic + one number from the kit), short text second, tags AI 生成 / 非诊断 | composer: camera, mic, + (quick log); 3 context chips from the screen the key was tapped on | history and AI switcher in the sheet header; hold key = voice overlay |
| member day | How is this friend today? | their small dial + two readings (shared scopes only) | read-only tag | 全部 → member view pages |

## 6. Rules for builders
- Labels ≤ 6 Chinese characters or 2 English words; **zero sentences** on any screen (sentences live in L3 sheets and Jarvis).
- Every number carries its unit (kcal, ms, bpm, 次/分, 时 / 分); scores carry their drawn scale instead (recovery shows %). Changes are signed ("+6", "−2") and always mean "vs your usual".
- Colour = state (good / mid / bad) or category (sleep / train) next to a number or mark; everything else is ink. History is `past`; only today / now is coloured. Amber / red only for attention.
- Sync capsule at the top right of **every tab root** (`.sync`, 44 pt hit area, states ok / busy / stale / fail, icon shape + age, never a clock time). Tap = sync now or retry; long press = sync center.
- 「非诊断」 tag in the title row of every score L1 and health card; 「AI 生成」 on every AI answer; both open the sentence in a sheet.
- One horizontal swipe meaning per screen (Today: change day; 身体: change score). No horizontal pagers for modules.
- Hit areas ≥ 44 pt (close button drawn 32, icon buttons via `contentShape`). Chart text `text2`, never `text3`.
- Only Lucide 1.61.0 icons (stroke 1.75); only kit classes and tokens; page CSS sets spacing only.
- **Forbidden**: HUD frames, scan lines, glows, gradients, 3D, pies / donuts, dual axes, borders or shadows on cards, glass on content, a custom tab bar, page titles on tab roots, control rows before data, rows of equal tiles in L1, empty cards, coach marks, emoji, self-drawn icons, `tabBarMinimizeBehavior` with the key visible, matched geometry across a sheet, changed system dims.

## 7. SwiftUI notes
Dial: one `Canvas` pass for 100 ticks (50 under 200 pt) + `Circle().trim` arcs, `Equatable`, redrawn on day change. Gauge: `Capsule` + `Rectangle` in `GeometryReader`, whole reading is one 59 pt button. Hero L1: `.presentationDetents([.height(444), .large])` + `.presentationBackgroundInteraction(.enabled(upThrough: .height(444)))` (16.4); a tap on the background closes the sheet; the background ignores other gestures while the sheet is up. 16.0–16.3: plain dimmed sheet. Charts: `ChartSpec` bars + band + dashed `RuleMark` edges; rows reuse `Sparkline` with a band. Strain "planned" = the average strain of the last 4 sessions of today's planned type (new rule, rule-based, no AI); no plan → no dots. Usual lane = person's 30-day P25–P75 (exists for the explain sheet). VoiceOver: dial = one element ("恢复 82%，高于常态，可上强度"). Slim look: `train` becomes orange-red like `bad`; the strain gauge is never adjacent to a red mark, and the verdict word keeps recovery readable; check in a slim render before shipping.

## 8. Departures from standing decisions (owner to confirm)
1. **D13**: three rings side by side → one recovery dial + two attached gauges. Recovery stays dominant, all three scores stay on the first glance. Comparison: `01b-today-d13.html` (same page, only the hero differs). The owner chooses.
2. **Look reopened** (2026-11-10 feedback): type scale, light numerals, tick scales, thinner tracks, history in ink, chart text `text2`, 8 derived tokens. No existing hex changes.
3. **"Mini charts have no axes"** → readings carry fine tick scales (they are the scale, not an axis).
4. **Circles card** → a pinned face (slot 3) that switches to the 圈子 tab; still permanent, still mutable.
5. **重点 section and reason tags** → the attention slot (slot 1) and the long-press menu.
6. **Ask field leaves Today; the Jarvis dial retires** (key tap = chat, hold = voice).
7. **Food and log lose their tabs** (D1).
8. **Hero L1 sheet does not dim the hero** (background interaction on, tap outside closes).
