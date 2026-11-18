# Drill-down study (2.1.0): how the detailed data is reached

Mockups only, for the owner to pick from. No SwiftUI in this folder; every number, date and nickname is synthetic
(小林, 11月10日, the same day as screens `01`–`05`). The folder name stays `2.0.1` although the release is 2.1.0.

Three options are drawn: **A 露边** (peek), **B 抽屉** (drawer), **C 层级缩放** (levels), plus the variant **C′**.
Pictures are in [`png/`](png/); start with [`png/compare.png`](png/compare.png), then the three boards.

| Option | Board (tap path) | 今天 | 身体 | 今日详情 | One metric (HRV) |
|---|---|---|---|---|---|
| A 露边 | [`a-board`](png/a-board.png) | [`dark`](png/a-today-dark.png) · [`light`](png/a-today-light.png) · [`small`](png/a-today-se-dark.png) | [`dark`](png/a-body-dark.png) · [`light`](png/a-body-light.png) · [`small`](png/a-body-se-dark.png) | [`a-day`](png/a-day-dark.png) | [`a-metric`](png/a-metric-dark.png) |
| B 抽屉 | [`b-board`](png/b-board.png) | [`dark`](png/b-today-dark.png) · [`light`](png/b-today-light.png) · [`small`](png/b-today-se-dark.png) | [`dark`](png/b-body-dark.png) · [`light`](png/b-body-light.png) · [`small`](png/b-body-se-dark.png) | [`b-day`](png/b-day-dark.png) | [`b-metric`](png/b-metric-dark.png) |
| C 层级缩放 | [`c-board`](png/c-board.png) | [`dark`](png/c-today-dark.png) · [`light`](png/c-today-light.png) · [`small`](png/c-today-se-dark.png) · [`C′`](png/c-today-cprime-dark.png) | [`dark`](png/c-body-dark.png) · [`light`](png/c-body-light.png) · [`small`](png/c-body-se-dark.png) · [`C′`](png/c-body-cprime-dark.png) | [`c-day`](png/c-day-dark.png) | [`c-metric`](png/c-metric-dark.png) |

## 1. The problem, from first principles

The stated problem (acceptance, 2026-11-18): "the detailed data of the old version cannot be found". Nothing was deleted.
Read against the code of `main` (`826431b2`), there are four causes, and only the first is about scrolling:

1. **False floor.** `direction.md` 2.4 composes the first glance to end exactly above the Jarvis key ("never content
   under the key"), and the next block starts after a fixed blank spacer (`TodayView.swift:98` and
   `TrendsView.swift:105`: `.padding(.top, 150)`). On iOS the only signal that a page scrolls is content cut by the
   fold. A first glance that fits exactly plus a blank gap removes that signal by construction.
2. **The tap on a number arrives at the wrong thing.** The owner looks for detail by tapping the number they see. That
   tap opens an L1 half sheet (`TodayL1Sheet`): a summary. Its rows open explanation text (`ExplainBody`), not the
   metric's chart over time, and the full list is a button (「全部指标」) that switches to another tab
   (`TrendsLink.shared.open(day:)`, then `tab = .trends`) and lands above a blank gap there.
3. **The day's metrics have no home of their own.** The full list of the day lives in the 身体 tab as the range 「今天」
   of a segmented control below the fold (`TrendsMetricsContent.dayList`). The screen is organised by how it was built
   (new glance on top, the 2.0.0 views underneath), not by the user's question.
4. **Some views have no door at all on the owner's own pages.** Found while taking the inventory (section 7):
   「记体重」 (`TodaySheet.weight`) is only opened by the debug screenshot hook; 今日核心 cards, the 身体状态 cards and
   through them the lab history and the stress hour chart are only drawn by `TodayDayCards`, which since 2.1.0 is used
   only for the read-only view of another member.

So the definition used here is not "add an entry to the old list" but: **every number on screen is a door to its own
depth, the screen shows that doors exist without words, and the app can prove that nothing is left without a door.**

## 2. What the owner decided (common to all three options)

- Every number on a first screen is tappable and leads to that metric's own detail (chart, trend line, range switch).
- Tapping the dial opens a second-level page 「今日详情」 with all of the day's metrics; 今天's first screen stays one dial.
- The blank gap under the first screen goes away.
- The hint that more exists is graphics only (no text row).

Two pages are therefore new in every option (only their container differs):

- **今日详情** (`*-day`): the three scores small, a jump rail (one icon per category with its count), then every metric
  of the day in the 2.0.0 groups and order (恢复 8, 睡眠 9, 负荷 9, 压力 2, 长期 3 = the 31 rows of `TrendDay.layout`),
  then 当前状态 (the 9 status cards), 需要注意 and 体征 when present, and 档案 (the 身体状态 cards) when present. Each row:
  label, 14-day line on the usual band, value, change vs usual, chevron. Built from existing parts: `TrendDay.groups`,
  `TrendDayRowView`, `StatusTileView`, `AttentionList`, `MonitorRow`.
- **One metric** (`*-metric`): today against the usual, range switch 7 / 30 / 90 / 365, the chart (daily line, usual
  band, 7-day mean as the trend line, drag read-out), four figures of the range (mean, high, low, vs previous period),
  related metrics (each a door to its own page), and at the bottom of the same page the explanation text
  (`ExplainBody`), so it costs no extra tap. Built from `TrendCardView` / `TrendChartBody` + `ChartTouchLayer`,
  `RangeFigures`, `SeriesLocal`. The three scores and a few metrics keep their own head on this page: sleep = the night
  (`SleepDetailView`), strain = the day curve (`StrainDayChart`), recovery = the contributors
  (`TrendsSheetView.recovery`), stress = the hour chart (`StressDetailView`), body age = `BodyAgeView`.

Two consequences the options share:

- The 「今天」 range leaves the 身体 tab (the header menu of 身体 already offers only 7 / 30 / 90 / 365:
  `BodyParams.menuRanges`). The day belongs to 今天, the period to 身体; the tab jump of 「全部指标」 is removed.
- The dial opens 今日详情, so "recovery's own detail" is the first group of that page (the day) and the hero of 身体 (the
  period). This is the one place where "tap the number → its own chart" takes two taps (open question 1).

## 3. Option A 露边 (peek)

Pictures: [`a-board`](png/a-board.png).

**On screen.** The first glance keeps its content and tightens its gaps (21 / 23 / 23 instead of 32 / 40 / 44); the
150 pt spacer becomes a 24 pt joint with one down chevron; the next block starts right after it and is cut by the fold
(56 pt of it on the standard phone: the faces' labels and the upper part of their numbers; on 身体 the first trend card,
cut through its chart). Every number that opens something carries a disclosure chevron after its label (dial, both
readings, the three faces). The segmented control 指标 | 睡眠 | 报告 at the fold of 身体 goes: a row of words would be the
hint, which the owner ruled out. Below the fold of 身体 come the range's cards directly; the last 14 nights become the
first card of the sleep group, the report stays behind the 周报 face.

**The Jarvis key.** The key keeps its opaque band (`clearOfJarvisKey`, 88 pt: `JarvisLayer.keyClearance`), so nothing
ever sits under it. The fold is the band's upper edge (y 704 on 402 × 874), with a 10 pt soft edge. What changes is the
rule above it: instead of "the first glance ends above the key" it becomes "at least 48 pt of the next block show above
the band", as a shared constant instead of a literal per tab.

**Existing code.** Smallest change. Reused as they are: the vertical `ScrollView` of both tabs, `TodayMoreFaces`,
`SyncNudges`, the sleep / strain / attention half sheets (`TodayL1Sheet`), all range cards, `TrendsSleepView`,
`ReportView`. Changed: the two spacers; `Rhythm` gets a peek constant; `TickDial` label, the readings and `Face` get an
optional mark; the dial pushes 今日详情 instead of opening the recovery sheet; rows of the half sheets push the metric
page instead of the explanation sheet; the sheets' 「全部指标」 becomes a push of that metric's page. Removed:
`TodayL1.recovery` (its bars and rows are the top of 今日详情), `TodayView.allMetrics`' tab switch, the mode switch at
the fold of 身体 (`TrendsView.Mode` stays as a route for `TrendsLink.open(mode:)`), the inner range control of
`TrendsMetricsContent`. Duplicated: one model, two presentations (rows on 今日详情, cards below 身体).
One tap on 睡眠 or 负荷 still gives the half sheet (the day's chart); the range chart is the second tap.

**Second and third order.**
- Other tabs: the rule transfers as it is to 记录 (200 pt spacers) and 圈子 (110 pt), which have the same bug
  (section 8). 我 already behaves this way and has no problem, which is evidence from our own app that the mechanism works.
- Gestures: day swipe (`DaySwipe`), pull to refresh, the hero swipe of 身体 and the chart read-out are untouched. On
  the pushed 今日详情 a day swipe must ignore drags that start at the left edge (system back); `SleepDetailView` already
  does the same for nights.
- Complexity: three mechanisms stay (scroll, half sheet, push), and the user still has to guess which one holds what.
- Maintenance: the peek depends on screen height, language and which faces exist. Any later change of the first glance
  can silently turn it back into a false floor, so this option needs the automated fold check (section 9) to be safe.
- Behaviour: 2.0.0 users keep their scrolling habit. The space below the fold stays the place where new features get
  appended, which is how the 2.0.0 page grew long.

**From another field: the newspaper fold.** Front pages are laid out so that a headline or a photo straddles the fold
of the paper on the stand; a fold that runs through white space reads as "the page ends here". Lesson: the cut must
pass through something recognisable and worth having. On 今天 the cut shows three small faces, a modest promise; on
身体 it shows a chart, a strong one.

**Strongest objection.** It is a patch. The owner said they find detail by tapping numbers, not by browsing, so the peek
solves a problem the owner does not have while keeping two ways to more. Six chevrons turn an instrument into a
settings list and reverse `direction.md` 3 ("no chevrons on a face"). The approved rhythm loses its air.

**Edge cases.**
- No data / stale: a face with stale data means "tap = sync", so it must not show the chevron. If nothing follows the
  first glance (past day without training, reminders or nudges) the joint chevron must not be drawn.
- Small phone (375 × 667): dial 200, gaps 6 / 10 / 10, only 32 pt peek: the labels of the next row, no numbers.
- English: "Recovery ›", "Sleep ›", "Strain ›" fit; on a 106 pt face "Body age" plus icon plus chevron is at the limit,
  so the label must truncate before the chevron does.
- VoiceOver: the chevrons and the joint are decorative (hidden); nothing else changes. VoiceOver users never met the
  false floor, they move through the page in order.

## 4. Option B 抽屉 (drawer)

Pictures: [`b-board`](png/b-board.png).

**On screen.** Nothing scrolls below the first glance. At the bottom sits a drawer: a grabber and one row of five bare
trend lines (今天: HRV, 静息心率, 呼吸, 血氧, 体温; 身体: HRV, 静息心率, 睡眠, 负荷, VO₂max over the header's range). No
labels: the hint is graphics only. Tapping a number (or a line) raises the drawer to half height with that metric
while the dial stays visible; dragging up or tapping the dial opens it fully = 今日详情 (on 身体: all trends). 训练 · 今晚
· 提醒 and the sync nudges move into the drawer.

**Bottom sharing.** The drawer's edge is at y 648; it runs on under the key and under the tab bar to the bottom of
the screen. Key and tab bar stay where they are and float on the drawer's surface. Above the key band the drawer shows
56 pt at rest and only 300 pt at half height (y 404–704): enough for the value, the range switch and an 88 pt chart;
the figures and related metrics need the full height. The first glance gives up 56 pt (gaps 20 / 24 / 24).

**Existing code.** The system sheet cannot be used: a sheet is presented above the `TabView` and above `JarvisLayer`,
so it would cover the tab bar and the key, and `tabViewBottomAccessory` exists only on iOS 26 (and `direction.md` 4.5
excludes a bottom accessory). The drawer is therefore a custom container inside the page: drag with three heights,
hand-over between the drag and the inner scroll view, rubber band, keyboard, rotation of content with the day. New:
that container and the strip. Reused as content: the bodies of `TodayL1Sheet`, 今日详情, the metric page (with a back
chevron inside the drawer). Removed: `.sheet(item: $l1)` and `l1Detents`, everything below the fold of both tabs, the
key band on these two tabs (the drawer owns the bottom and needs its own). Every other sheet (explanation, calendar,
reminders) then appears on top of an open drawer: two stacked sheets.

**Second and third order.**
- Gestures: a downward drag means "refresh" on the first glance and "close" on the drawer, depending on where the
  finger starts. The day swipe must be excluded from the drawer and the drawer's content must follow the day. Inside
  the drawer three recognisers share one area (drawer drag, list scroll, chart read-out).
- The Jarvis cockpit fans its panels out of the key; with the drawer half open they fan out over a second surface.
- Other tabs: either every tab gets a drawer (five custom bottoms) or 今天 and 身体 behave unlike the rest.
- Behaviour: people park drawers half open. Then the first glance is covered every time, unless the drawer resets on
  tab switch and at the start of a day, which is one more rule to keep.
- First glance: five more graphics every day for an occasional need (`direction.md` 3 allows 7 blocks).
- Maintenance: a custom bottom sheet has to be re-checked with every iOS release for good.

**From another field: the bedside monitor.** An intensive-care monitor shows the live number large and a small trend
strip beside it; touching the strip opens the full trend. It works because someone watches continuously and acts
within seconds. The same wards teach the cost: what is always on gets tuned out (alarm fatigue), and Airbus builds
cockpits "dark" for that reason, with nothing lit while all is normal. A page that is looked at a few times a day is
closer to the dark cockpit: our own attention face already follows that rule.

**Strongest objection.** It contradicts the owner's third answer (今日详情 is a separate second-level page, not a layer
over the first screen) and it is the most fragile piece of UI in the app for the smallest gain: the half sheets of
today already give "tap, look, flick away"; what the drawer adds is only the always-visible strip.

**Edge cases.**
- No data: no lines, the drawer shows a grabber on an empty edge. Stale: lines dimmed, the capsule carries the state.
- Small phone: the edge needs 56 pt, so the dial shrinks to 188 pt, below today's floor of 200
  (`TodayGlancePage.dialSide`), and the dial touches the header.
- English: the strip has no text; nothing changes.
- VoiceOver: all by hand: the grabber as a button with "expand / collapse" actions, an escape gesture, a decision
  where focus goes when the drawer moves, a label per line ("HRV, rising"). Reduce Motion: heights cross-fade.

## 5. Option C 层级缩放 (levels)

Pictures: [`c-board`](png/c-board.png); variant C′: [`c-today-cprime`](png/c-today-cprime-dark.png).

**On screen.** Three explicit levels: **1** the glance, **2** 今日详情 (on 身体: all trends of the range), **3** one
metric. The first screen keeps the approved rhythm (32 / 40 / 44) untouched and does not scroll: nothing lives below
the fold, so a false floor cannot exist. In the middle of the header a three-segment ladder shows the depth (lit
segments = level). Tapping the dial zooms into level 2: the dial shrinks into the header, where it stays as the way up
([`c-board`](png/c-board.png), top right). Tapping a number goes straight to level 3. The half sheets are folded into
the levels: their day charts head the level-3 pages of sleep, strain and recovery.

**Where 训练 · 今晚 · 提醒 go.** They are the first row of level 2 (three chips: icon + number). A new reminder or a
session planned for today also takes the attention slot of the first glance (slot 1, the existing rule), so what needs
action is still one tap away. The sync nudges become states of the sync capsule and the attention face.

**Refinements of the brief.**
- *Back.* The brief says "back goes up one level". That holds on the path glance → 今日详情 → metric. When a number on
  the first screen is tapped (straight to level 3), back returns to the first screen: "tap a number, look, come back"
  is the owner's main use, and a strict "up" would cost two backs every time. The level above is one tap away on the
  small dial in the header (open question 2).
- *The ladder is a door.* It has a 44 pt hit area; a tap goes one level down. That gives a visible door even when the
  dial's own tap is taken (no score: the dial's tap is the one self-help step).
- *Swipe up on the first screen also opens level 2.* It is what a 2.0.0 user will try; it only repeats a visible door.
- **C′.** Pure C signals depth only with the ladder and the motion. C′ adds option A's chevron to the three elements
  that have no card fill (dial, both readings; on 身体 the hero label). Faces stay unmarked: a filled card already has
  the shape of a button, and `direction.md` 3 ("no chevrons on a face") stays true.

**Existing code.** Reused: the `NavigationStack(path:)` both tabs already have (the route type grows from `SleepRoute`
to an enum), `SleepDetailView`, `StrainDayChart`, `ReportView`, `BodyAgeView`, `TrendsSleepView` as level-3 pages; on
iOS 18+ `matchedTransitionSource` + `navigationTransition(.zoom)`, on 16–17 a plain push. New: the ladder, the header
with the small dial, level 2 of 身体 (the same row component as 今日详情, range from the header). Removed: the
`TodayL1Sheet` container with `.sheet(item: $l1)` and `l1Detents`, `TodayMoreFaces` as a row of the first screen, both
spacers, the mode switch and the card list below the fold of 身体, `TrendsFold` (the jump rail replaces folding).
`direction.md` changes in two places: "tap = L1 half sheet" becomes "tap = the next level", and "3 modules below the
fold" becomes "3 modules on level 2". `TodayDayCards` and `TrendsMetricsContent` live on only for the member view
unless that view moves to the same pages (follow-up).

**Second and third order.**
- Gestures: the first screen keeps a scroll view for pull to refresh and the day swipe; on levels 2 and 3 the day
  swipe ignores the left edge (system back). No drag recogniser is added.
- The Jarvis key on a level-3 page knows the metric as context (the three context chips of screen `09`) for free.
- Other tabs: 记录 could follow (the rows now under its 200 pt spacer become level 2 of the ring and of the food dial).
  圈子 cannot: a feed has to scroll. So the project rule needs two legal forms (section 9), and C alone does not fix
  the other tabs.
- Maintenance: one mechanism (push) instead of three; the sheet layer of 今天 disappears. Level 2 becomes the place
  where new modules go, which is what a page called "everything of the day" is for; the first glance cannot grow.
- Behaviour: seeing 训练 · 今晚 · 提醒 costs one tap where it cost one scroll. A 2.0.0 user who scrolls gets level 2.

**From another field: map scales and the microscope.** A map does not enlarge the same drawing: each scale shows what
belongs to it (a city is a dot at one scale, streets at another), and the scale bar always says where you are. A
microscope's objectives are parfocal: switch magnification and the object stays centred and in focus. Lessons: what is
shown must change with the level, not only its size; the thing that was tapped must stay in place while everything
else changes (the dial into the header); the current scale must always be visible (the ladder).

**Strongest objection.** Without a fold and without words, a new user cannot see that the dial opens anything: the
first screen looks finished, which is exactly what failed in acceptance, now by design. The ladder is a new sign that
has to be learned; if it is not noticed it is noise. And three things the owner looks at daily (训练, 今晚, 提醒) move
one tap further away.

**Edge cases.**
- No data: dial without arc; its tap is the self-help step (`recoveryNext`), level 2 opens from the ladder or a swipe
  up. Stale: pushed pages have no sync capsule (kit rule), so on levels 2 and 3 the age is shown in `mid` beside the
  date. First two weeks: rows without a usual band.
- Small phone: identical to today's build (dial 213, short rhythm); the only option that asks nothing of it.
- English: the ladder has no text; chips "5 planned", "23:40", "2"; header "Nov 10".
- VoiceOver: ladder = "Level 1 of 3", adjustable; the dial's hint names the destination; back button "Today". Reduce
  Motion: the zoom becomes a cross-fade.

## 6. Recommendation

My favourite is **C′**. Its strongest opponent says:

1. *"You remove scrolling from the two main tabs and bet everything on a tap that nothing announces. That is the
   failure of the acceptance test turned into the design."* Largely right about pure C, which is why I do not
   recommend pure C. What failed in acceptance was that the taps led to a summary, to text and to another tab, and that
   content sat behind a blank gap. C removes both. C′ marks the three doors that do not look like buttons, the ladder
   is a door, and the scroll-up of a 2.0.0 user opens level 2. Still open: whether three chevrons and a ladder are read
   by someone who has never seen the app. That needs five minutes with two testers, not an argument.
2. *"You throw away half sheets that were approved and have only just been built."* The container goes; the charts in it
   (30-day bars, day curve, hypnogram) become the heads of the level-3 pages. What is lost is "the dial stays visible
   while I look at the reason"; the small dial in the header is a weaker substitute. Real cost, accepted.
3. *"Levels do not fit a feed. You will have two models in one app."* Correct, and it changes the recommendation: C is
   the answer for 今天 and 身体, not for the app. 记录 and 圈子 have the same false floor today (section 8) and need
   option A's rule now, whatever is chosen here.
4. *"Daily friction: 今晚 and 提醒 one tap deeper."* Correct. Softened by the attention slot, not removed.

What survives: on the two instrument tabs C′ is the only option that makes the false floor impossible instead of
unlikely, keeps the approved first screen and its small-phone layout, uses only system navigation and leaves one
mechanism instead of three. A is cheaper and safer for old habits but keeps the first screen dependent on per-device
tuning. B costs the most for the least and contradicts the owner's third answer.

**Recommendation: C′ for 今天 and 身体; option A's peek rule for the tabs that must scroll (记录, 圈子); both under one
project rule with an automated check (section 9). Not B.** If 2.1.0 has no room for the restructuring, A is the
fallback: 今日详情 and the metric page are the same work in every option, so nothing built for A is lost when C′ follows.

## 7. Inventory: every detail view of 2.0.0 and its door

T = first screen of 今天, S = first screen of 身体. The number in brackets is the count of taps from that first
screen; a scroll or a drag is not a tap and is named. "none" in the second column means: no door on the owner's own
pages in `main` today.

| # | Detail view of 2.0.0 | As built in 2.1.0 (`main`) | A 露边 | B 抽屉 | C / C′ 层级 |
|---|---|---|---|---|---|
| 1 | All metrics of the day (31 rows by category) | T: number → sheet → 全部指标 → jumps to 身体 (2 + tab switch), or S: scroll past the gap → 指标 → 今天 | T: dial → 今日详情 (1) | T: dial or drag → drawer full (1) | T: dial → level 2 (1) |
| 2 | One metric over a range (29 cards: chart, read-out, mean, vs previous) | S: scroll past the gap | S: scroll (first card peeks); or T: dial → row (2) | S: drag → row (1); or number → half (1) | S: bars → level 2 → row (2); T: dial → row (2) |
| 3 | Recovery of the day (score, zone, contributors) | T: dial → sheet (1) | T: dial → first group (1) | T: dial → first group (1) | T: dial → first group (1) |
| 4 | Recovery trend and contributors against baseline (z) | T: dial → sheet (bars); S: hero → sheet (1) | S: hero (0), tap → page (1) | S: hero (0), tap → half (1) | S: hero (0); bars → level 2 → 恢复 (2) |
| 5 | Strain of the day (curve, target, planned, muscle load) | T: 负荷 → sheet (1) | T: 负荷 → sheet (1) | T: 负荷 → half (1) | T: 负荷 → level 3 (1) |
| 6 | Training load (CTL, ATL, TSB) | T: 负荷 → sheet → row (2) | same (2) | T: 负荷 → half → row (2) | T: 负荷 → row (2) |
| 7 | Strain target (coach) | only from an attention item | part of the strain detail (1) | part of the strain detail (1) | part of the strain page (1) |
| 8 | Sleep night (hypnogram, stages, score, vs usual, overnight, notes) | T: 睡眠 → sheet → 全部指标 (2) | T: 睡眠 → sheet → › (2) | T: 睡眠 → half, drag up (1) | T: 睡眠 → level 3 (1) |
| 9 | Last 14 nights (averages, stages, schedule) | S: scroll past the gap → 睡眠 | S: scroll → sleep group; or dot 睡眠 → hero (2) | S: dot 睡眠 → hero → half (2) | S: dot 睡眠 → bars → level 2 (2) |
| 10 | Weekly / monthly report, behaviour impact | S: 周报 (1); or scroll → 报告 | S: 周报 (1) | S: 周报 (1) | S: 周报 (1) |
| 11 | Status cards 当前状态 (9) and their sheets (battery curve, load focus) | S: scroll past the gap, after all cards | T: dial → card (2) | T: dial → card (2) | T: dial → card (2) |
| 12 | Health monitor 体征 (vitals with ranges) | T: 体征 → sheet (1) | same (1); rows push the metric | T: 体征 → half (1) | T: 体征 → level 2 at 体征 (1) |
| 13 | 需要注意 (what is off today) | T: attention face → sheet (1) | same (1) | T: face → half (1) | T: face → level 2 at 需要注意 (1) |
| 14 | 今日核心 cards | none (member view only) | rows of 今日详情 (1) | rows of the drawer (1) | rows of level 2 (1) |
| 15 | Stress of the day (hour chart) | only when stress is an attention item | T: dial → 压力 (2) | T: dial → 压力 (2) | T: dial → 压力 (2) |
| 16 | Explanation of a metric (what, how to read, advice) | T: number → sheet → row (2) | bottom of the metric page (2) | bottom of the drawer's metric (2) | bottom of level 3 (2) |
| 17 | Body age (history, contributors) | S: 身体年龄 (1) | S: face (1) | S: face (1) | S: face → level 3 (1) |
| 18 | Tonight (sleep coach) | T: scroll past the gap → 今晚 (1) | T: scroll (row peeks) → 今晚 (1) | T: dial → chip (2) | T: dial → chip (2) |
| 19 | Bedtime goal | via 今晚, via the sleep night page, or 我 → 偏好设置 | T: scroll → 今晚 → row (2) | T: 睡眠 → drag up → row (2) | T: 睡眠 → row (2) |
| 20 | Training of the day (logged, planned, muscles) | T: scroll past the gap → 训练 (1) | T: scroll → 训练 (1) | T: dial → chip (2) | T: dial → chip (2) |
| 21 | Reminders (list, one reminder, mark seen / dismiss) | T: scroll past the gap → 提醒 (1) → row (2) | T: scroll → 提醒 (1) → row (2) | T: dial → chip (2); the list shows each reminder in full | T: dial → chip (2); same; a new one is on the attention face (1) |
| 22 | Log weight | none (debug hook only) | T: dial → ＋ on the 体重 row (2) | same (2) | same (2) |
| 23 | 身体状态 cards (saved status items) and lab history | none on 今天 (member view only); 我 → 健康档案 | T: dial → 档案 group → item (2) | same (2) | same (2) |
| 24 | Month calendar (another day) | T: date (1) | T: date (1) | T: date (1) | T: date (1) |
| 25 | End date of the range | S: range menu → 选择结束日期 (2) | same | same | same |
| 26 | Sleep-tracking help (no score) | T: empty dial (1) | same | same | same |

Rows 21 B / C: the separate sheet for one reminder is folded into the list (each row shows its text and both actions),
otherwise it would be the third tap. Rows 14, 22 and 23 are today's views without a door (cause 4 in section 1).

## 8. The same problem in 记录, 圈子 and 我 (findings only)

| Tab | Blank spacer | Reachable only below it | Only through a gesture or a second sheet |
|---|---|---|---|
| 记录 · 训练 | `TrainingRoot.swift:27`: `.padding(.top, 200)`; the first screen ends flush | 切换分化, 训练设置, 训练记录 (each also has another door) | long press on the ring: 问 Jarvis |
| 记录 · 饮食 | `FoodView.swift:173`: `.padding(.top, 200)` | the day stepper (the only way to another day's food), 今日餐单, 采购, 我的饮食档案, merge / save-as-template | 快记 face → sheet → 全部 → template editor; water add / undo only in a sheet |
| 圈子 | `CirclesTab.swift:44`: `.padding(.top, 110)` | the 动态 / 成员 / 排行榜 switch and the member list (the 动态 and 排行 faces scroll to it; 成员 has no face) | comment long press: delete, report, block (no visible alternative); circle settings: menu → sheet → push |
| 我 | none (40 pt; one row of the list shows above the fold) | nothing hidden | inbox: swipe to hide has no visible alternative |

- No shared scaffold holds the spacer: five literals in five files (150, 150, 200, 200, 110) and none goes through
  `Rhythm`. A fix in one place does not fix the others.
- Views that are no longer instantiated anywhere: `TrainingSection` and with it `TrainingCalendarView`,
  `TodayFoodCard`, `CircleSummaryCard`.
- Every sync capsule opens the sync center on long press; 我 has a visible row for it, the other tabs only the tap
  while the state is not ok.

## 9. Proposed project rule and automated check

**Rule (for `CONTRIBUTING.md` and `direction.md` 6).**
1. *No false floor.* A tab root has one of two forms: nothing below the first glance, or the next block cut by the
   fold with at least `Rhythm.peek` (48 pt) of it above the key band on every supported screen height. A fixed blank
   spacer of more than 56 pt between two blocks is not allowed.
2. *Every view has a visible door.* Each detail view is reachable from its tab root in at most two taps on visible
   controls. A gesture (long press, swipe, double tap, context menu) may repeat a visible door, never be the only one.
3. *A number opens its own depth, in its own tab.* Only a face that names another tab's subject (饮食, 圈子) switches tabs.
4. *The inventory is part of the change.* A pull request that adds, moves or removes a screen updates the
   reachability inventory in the same change.

**Check 1: reachability inventory (would have caught rows 14, 22, 23 and the four dead views).**
- `apps/ios/reachability.json`: one entry per destination (every case of `TodaySheet`, `TodayDetail`, `TodayL1`,
  `TrendsSheet`, every route and every pushed page), with its tab, its door (the element), the tap count and the door
  kind (`tap` or `gesture`).
- A source test in CI, in the style of `tests/test_i18n.py` (it reads the Swift files, it does not run the app):
  every case of those enums must be constructed at least once outside `#if DEBUG` and outside the enum's own `switch`
  statements; every `View` struct under `Features/` must be instantiated outside its own file and outside `#if DEBUG`;
  every case must have an inventory entry with `taps ≤ 2` and at least one door of kind `tap`. The test prints the
  missing ones by name.

**Check 2: the fold (would have caught the five spacers).**
- First step, cheap: the same source test fails on `.padding(.top, N)` and `Color.clear.frame(height: N)` with
  `N > 56` inside a tab root's scroll content.
- Second step, exact: a unit test hosts each tab root at 375 × 667, 393 × 852, 402 × 874 and 440 × 956 with synthetic
  data, reads the frames of the blocks (a `foldBlock(id)` preference key) and asserts form 1 or form 2 of rule 1 and
  that no gap between two blocks exceeds 56 pt.

**Later, optional:** a UI test that walks the inventory on the synthetic account and asserts each destination's
accessibility identifier appears.

## 10. Open questions for the owner

1. The dial opens 今日详情, so recovery's own range chart is the hero of 身体, not one tap from the dial. Accept, or
   should the number 82 and the ring be two targets?
2. Option C, back from a metric that was opened straight from the first screen: back to the first screen (as drawn)
   or strictly up to 今日详情?
3. Marks: on every number (A), only on the dial and the readings (C′), or none (pure C)?
4. Is the ladder wanted, or should C′ ship without it and rely on the marks?
5. Options B and C fold the one-reminder sheet into the list (row 21). Acceptable?
6. Should the member view move to the same pages (it is the last user of `TodayDayCards` and of the card list)?

## Files

- `a-*.html`, `b-*.html`, `c-*.html`: the screens (`today`, `body`, `day`, `metric`) and one board per option;
  `compare.html`: the comparison board.
- `dd.css`: additions to `../kit.css` (variable rhythm of the first glance, small phone, marks, fold, drawer, ladder,
  the two new pages, board styles). `dd.js`: shared parts and the synthetic data; query options `?size=se`,
  `?theme=light`, `?opt=ladder,bare`, `?state=zoom`. `board.js`: frames, tap markers and wires of the boards.
- `../kit.css`, `../kit.js`, `../core.js` are unchanged copies of the files on `design/ui-2.0.1`.
- Icons: Lucide only, from the set already vendored in the app. The tap markers and wires on the boards are
  annotations of the review pictures, not app UI.

```bash
bash docs/design/2.0.1/drilldown/render.sh
```

```bash
bash docs/design/2.0.1/drilldown/render.sh c-today
```
