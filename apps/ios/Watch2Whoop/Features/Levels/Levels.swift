import Foundation

/// A pushed level of 今天 or 身体 (2.1.0, docs/design/2.0.1/drilldown option C′). Level 1 is the tab's first glance (the
/// root of its `NavigationStack`); these are the levels below it.
enum LevelRoute: Hashable {
  /// 今日详情 (level 2 of 今天): every metric of the day. `anchor` = the block to scroll to (`attention`, `vitals`).
  case day(anchor: String?)
  /// Level 2 of 身体: every trend of the range, and the 2.0.0 lists 睡眠 and 报告 (`TrendsView.Mode`).
  case all(TrendsView.Mode)
  /// Level 3: one metric (an id of 今日详情's rows or of 身体's range cards, `MetricCatalog`).
  case metric(String)
  /// The sleep night page (night by night), under the sleep metric or 身体 → 睡眠. `nil` = the latest night.
  case night(String?)

  /// Depth on the ladder (1 = the first glance, which is no route).
  var level: Int {
    switch self {
    case .day, .all: return 2
    case .metric, .night: return 3
    }
  }
}

/// The navigation rules of the levels (unit-tested in `LevelsTests`). Back is the system's: one step along the path, so
/// a metric opened straight from a first screen returns to it (owner's answer 2, 2026-11-19). The crumb in a level's
/// header (the small dial on 今天, the range on 身体) goes up one level instead.
enum LevelNav {
  /// The level a path shows (1 = the first glance).
  static func level(_ path: [LevelRoute]) -> Int { path.last?.level ?? 1 }

  /// One level up from the top of `path`. Level 2 goes to the first glance; level 3 to the nearest level 2 below it, or
  /// to `second` (the tab's own level 2) when the metric was opened straight from the first glance.
  static func up(_ path: [LevelRoute], second: LevelRoute) -> [LevelRoute] {
    guard let last = path.last, last.level > 2 else { return [] }
    if let i = path.lastIndex(where: { $0.level == 2 }) { return Array(path[...i]) }
    return [second]
  }

  /// The ladder's tap on a first glance (a visible door to level 2 even when the dial's own tap is taken).
  static func down(_ path: [LevelRoute], second: LevelRoute) -> [LevelRoute] {
    path.isEmpty ? [second] : path
  }

  /// A drag on a first glance that opens level 2: far enough, mostly upward (the 2.0.0 habit of scrolling for more).
  static func isSwipeUp(_ dx: CGFloat, _ dy: CGFloat, distance: CGFloat = LevelParams.swipeUpDistance) -> Bool {
    dy <= -distance && abs(dx) < abs(dy) * 0.6
  }
}

/// The fold rule of a tab root (drilldown README section 9, rule 1; unit-tested in `ReachabilityTests`): either nothing
/// below the first glance, or the next block cut by the fold with at least `peek` of it above the key band, and never a
/// blank gap taller than `maxGap` between two blocks. Anything else is a false floor: the page looks finished and is not.
enum FoldRule {
  enum Verdict: Equatable { case nothingBelow, cutByFold, falseFloor }

  /// `blocks`: each block's top…bottom in the scroll content, in order; `visible`: the content height above the key band.
  static func verdict(blocks: [ClosedRange<CGFloat>], visible: CGFloat, peek: CGFloat = LevelParams.peek,
                      maxGap: CGFloat = LevelParams.maxBlankGap) -> Verdict {
    for (a, b) in zip(blocks, blocks.dropFirst()) where b.lowerBound - a.upperBound > maxGap { return .falseFloor }
    guard let next = blocks.first(where: { $0.upperBound > visible }) else { return .nothingBelow }
    return visible - next.lowerBound >= peek ? .cutByFold : .falseFloor
  }
}

/// One group of rows on a level-2 page (恢复, 睡眠, 负荷, 压力, 长期).
struct LevelGroup: Equatable, Identifiable {
  var id: String
  var title: String
  var rows: [TodayGlance.Row]

  /// The category's icon (rail and group header; Lucide, from the set the app already ships).
  static func icon(_ group: String) -> Icon.Name {
    switch group {
    case "recovery": return .heartPulse
    case "sleep": return .moon
    case "strain": return .flame
    case "stress": return .activity
    case "long": return .chartLine
    default: return .circleGauge
    }
  }
}

/// What a metric page is about: its series, the range card that draws its chart, its title and explanation, its head and
/// its related metrics. Ids are 今日详情's row keys (`TrendDay.layout`) and 身体's range card ids (`TrendCards.groups`).
enum MetricCatalog {
  /// The head of a metric page: the day's number against the usual (`plain`), or a score's own detail on top of it.
  enum Head: Equatable { case plain, recovery, sleep, strain, stress, bodyAge }

  static func head(_ id: String) -> Head {
    switch id {
    case "recovery": return .recovery
    case "sleep_score", "sleep_hours": return .sleep
    case "strain": return .strain
    case "stress": return .stress
    case "body_age": return .bodyAge
    default: return .plain
    }
  }

  /// The `/trends` series behind a page (its day value, usual and range figures); nil for a card drawn from several.
  static func series(_ id: String) -> String? {
    switch id {
    case "training_load": return "tsb"
    case "azm": return "aerobic_min"
    case "vol": return "muscle_load"
    case "hr_zones", "sleep_regularity", "recovery_vs_strain": return nil
    default: return id
    }
  }

  /// The range card that draws the page's chart.
  static func card(_ id: String) -> String {
    switch id {
    case "tsb": return "training_load"
    case "muscle_load": return "vol"
    case "aerobic_min": return "azm"
    default: return id
    }
  }

  /// Label, unit and digits of the page's series (nil for a composite card).
  static func conf(_ id: String) -> TrendDay.Conf? { series(id).flatMap(TrendDay.conf) }

  static func title(_ id: String) -> String {
    if let c = conf(id) { return c.label }
    switch id {
    case "training_load": return tr("训练负荷", "Training load")
    case "azm": return tr("有氧分钟", "Aerobic minutes")
    case "vol": return tr("训练量", "Training volume")
    case "hr_zones": return tr("心率区间分布", "Heart-rate zones")
    case "sleep_regularity": return tr("睡眠规律性", "Sleep regularity")
    case "recovery_vs_strain": return tr("恢复 vs 负荷", "Recovery vs strain")
    default: return id
    }
  }

  /// The explanation at the bottom of the page (`/explain/metric/<key>`).
  static func explain(_ id: String) -> String {
    if let c = conf(id) { return c.explain }
    switch id {
    case "azm": return "aerobic_min"
    case "vol": return "muscle_load"
    case "sleep_regularity": return "sleep_consistency"
    default: return id
    }
  }

  /// A plain line (daily line, 7-day mean, the usual band) instead of the range card's own chart.
  static func drawsLine(_ id: String) -> Bool {
    guard let s = series(id), let c = TrendCards.conf[s] else { return false }
    return c.chart == .line
  }

  /// The category of a page's series in 今日详情's order (`TrendDay.layout`).
  static func group(_ id: String) -> String? {
    let key = series(id) ?? id
    return TrendDay.layout.first { $0.keys.contains(key) }?.id
  }

  /// The nearest metrics of the same category (by position, earlier first on a tie), at most `count`.
  static func related(_ id: String, count: Int = LevelParams.relatedCount) -> [String] {
    let key = series(id) ?? id
    guard let g = TrendDay.layout.first(where: { $0.keys.contains(key) }), let i = g.keys.firstIndex(of: key) else { return [] }
    let others = g.keys.enumerated().filter { $0.offset != i }
    return others.sorted { (abs($0.offset - i), $0.offset) < (abs($1.offset - i), $1.offset) }.prefix(count).map(\.element)
  }
}

/// The rows of the level-2 pages and the numbers of a metric page (unit-tested in `LevelsTests`).
enum LevelRows {
  /// 今日详情: every metric of the day in 2.0.0's groups and order (`TrendDay.layout`), each with its last 14 days on the
  /// usual band, the day's value and its change against the usual. `overrides`: the day's own numbers the first glance
  /// shows (recovery, sleep score, strain), so level 2 never contradicts level 1. `onlyPresent` (someone else's day): a
  /// metric the document does not carry at all is not shared with me, and gets no row (on my own day it is a 「—」 row).
  static func day(_ d: TrendsData, overrides: [String: Double] = [:], onlyPresent: Bool = false) -> [LevelGroup] {
    TrendDay.layout.compactMap { g in
      let rows = g.keys.compactMap { k in
        onlyPresent && d.series[k] == nil && overrides[k] == nil ? nil : dayRow(k, d, override: overrides[k])
      }
      return rows.isEmpty ? nil : LevelGroup(id: g.id, title: g.title, rows: rows)
    }
  }

  /// One metric of the day. Optional metrics with no reading in the last week are left out (as 2.0.0's list did).
  static func dayRow(_ key: String, _ d: TrendsData, override: Double? = nil) -> TodayGlance.Row? {
    guard let cf = TrendDay.conf(key) else { return nil }
    let s = d.series[key]
    let vs = s?.values ?? []
    if override == nil, TrendDay.optional.contains(key), !vs.suffix(TodayParams.sparkDays).contains(where: { $0 != nil }) { return nil }
    var v = override ?? (vs.last ?? nil)
    var dated = false
    // latest-only metrics (weight, VO₂max, body age): the latest reading, without a delta (it is not the day's)
    if override == nil, v == nil, TrendCards.latest.contains(key), let j = vs.lastIndex(where: { $0 != nil }) {
      v = vs[j]
      dated = true
    }
    let unit = cf.hours ? nil : (cf.unit.isEmpty ? (s?.unit).flatMap { $0.isEmpty ? nil : $0 } : cf.unit)
    let usual = TodayGlanceRules.usual(vs)
    var spark = TodayGlanceRules.tail(vs, LevelParams.rowSparkDays)
    if let o = override, !spark.isEmpty { spark[spark.count - 1] = o }
    return TodayGlance.Row(
      id: key, label: cf.label, value: v.map { TrendDay.format($0, cf) }, unit: unit,
      spark: spark.contains { $0 != nil } ? spark : [], usual: usual,
      delta: dated ? nil : delta(v, mid: TodayGlanceRules.usualMid(vs), cf),
      deltaTone: dated ? .text : TodayGlanceRules.deltaTone(v, usual: usual, better: cf.better),
      explain: (cf.explain, cf.label))
  }

  /// Change against the usual at the metric's digits; hours as 「+0:24」.
  static func delta(_ v: Double?, mid: Double?, _ cf: TrendDay.Conf) -> String? {
    guard cf.hours else { return TodayGlanceRules.delta(v, mid: mid, digits: cf.digits) }
    guard let v, let mid else { return nil }
    let m = (v - mid) * 60
    if abs(m) < 0.5 { return "±0" }
    return (m > 0 ? "+" : "−") + (TrendsFormat.hm(abs(m)) ?? "")
  }

  /// Level 2 of 身体: every range card as a row (its headline, its change against the previous period, the range's line).
  static func range(_ p: TrendsPage) -> [LevelGroup] {
    let d = p.data
    return TrendCards.groups(p).map { g in
      LevelGroup(id: g.id, title: g.title, rows: g.cards.map { c in
        let vals = MetricCatalog.series(c.id).flatMap { d.series[$0]?.values } ?? []
        // only a change goes in the delta column; other notes of a card (「3/4 周达标」, fitness) stay on its page
        let change = c.base.flatMap { b in ["↑", "↓", "→"].contains(where: { b.0.hasPrefix($0) }) ? b : nil }
        return TodayGlance.Row(id: c.id, label: c.title, value: c.value, unit: c.unit, spark: BodyGlanceRules.bucketed(vals),
                               delta: change?.0, deltaTone: change?.1 ?? .text, explain: (c.explain, c.title))
      })
    }
  }
}

/// The head of a metric page: the day's value against the usual (dd.js `metric()`: 「68 ms +6 比常态」).
struct MetricHeadModel: Equatable {
  var value: String?
  var unit: String?
  var delta: String?
  var deltaTone: Tone = .text
  var usual: ClosedRange<Double>?
  /// 「54–64」 at the metric's digits.
  var usualText: String?

  static func of(_ id: String, day d: TrendsData?, override: Double? = nil) -> MetricHeadModel? {
    guard let key = MetricCatalog.series(id), let d, let cf = TrendDay.conf(key),
          let row = LevelRows.dayRow(key, d, override: override) else { return nil }
    return MetricHeadModel(value: row.value, unit: row.unit, delta: row.delta, deltaTone: row.deltaTone, usual: row.usual,
                           usualText: row.usual.map { "\(TrendDay.format($0.lowerBound, cf))–\(TrendDay.format($0.upperBound, cf))" })
  }
}

/// The four figures under a metric page's chart (dd.js `.stats`): mean, high, low of the range, and the mean's change
/// against the previous period.
struct RangeStatsModel: Equatable {
  var mean: String?
  var high: String?
  var low: String?
  var vsPrev: String?
  var vsTone: Tone = .text
  var unit: String?

  static func of(_ id: String, range d: TrendsData) -> RangeStatsModel? {
    guard let key = MetricCatalog.series(id), let cf = TrendDay.conf(key), let s = d.series[key] else { return nil }
    let xs = s.values.compactMap { $0 }
    guard !xs.isEmpty else { return nil }
    let f = { (v: Double) in TrendDay.format(v, cf) }
    var out = RangeStatsModel(mean: f(s.mean ?? xs.reduce(0, +) / Double(xs.count)), high: xs.max().map(f), low: xs.min().map(f),
                              unit: cf.hours ? nil : (cf.unit.isEmpty ? (s.unit.isEmpty ? nil : s.unit) : cf.unit))
    if let m = s.mean, let p = s.prevMean {
      let dv = m - p
      out.vsPrev = cf.hours ? LevelRows.delta(m, mid: p, cf) : TrendsFormat.signed(dv, digits: cf.digits)
      if abs(dv) < 0.5 / pow(10, Double(cf.digits)) { out.vsPrev = "±0" }
      out.vsTone = BodyGlanceRules.deltaTone(abs(dv) < 0.5 / pow(10, Double(cf.digits)) ? 0 : dv, better: cf.better)
    }
    return out
  }
}
