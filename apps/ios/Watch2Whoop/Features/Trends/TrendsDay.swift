import SwiftUI

/// The personal baseline of one day (#357): the server's band when the series has one (trends/series.py `baseline_band`:
/// mean ± 1 SD of the 60 days before the day), else the same window computed from the cached series itself.
struct DayBaseline: Equatable {
  var mean: Double
  /// nil when the readings do not spread (or the band has no width).
  var sd: Double?
  var fromBand: Bool

  static func of(_ s: TrendSeries, at i: Int) -> DayBaseline? {
    guard i >= 0, i < s.values.count else { return nil }
    if let b = s.baseline, let m = b.mean[safe: i] ?? nil {
      var sd: Double?
      if let lo = b.lo[safe: i] ?? nil, let hi = b.hi[safe: i] ?? nil, hi > lo { sd = (hi - lo) / 2 }
      return DayBaseline(mean: m, sd: sd, fromBand: true)
    }
    let xs = s.values[max(0, i - 60)..<i].compactMap { $0 }
    guard xs.count >= TodayParams.baselineMinN else { return nil }
    let m = xs.reduce(0, +) / Double(xs.count)
    let v = xs.map { ($0 - m) * ($0 - m) }.reduce(0, +) / Double(xs.count - 1)
    return DayBaseline(mean: m, sd: v > 0 ? v.squareRoot() : nil, fromBand: false)
  }

  func z(_ v: Double) -> Double? { sd.map { (v - mean) / $0 } }

  /// −1 / 0 / +1: below, about at, above the baseline (flat within `TodayParams.flatZ` SD).
  func direction(_ v: Double) -> Int {
    let d = v - mean
    if let z = z(v) { return abs(z) < TodayParams.flatZ ? 0 : (d > 0 ? 1 : -1) }
    let rel = mean == 0 ? abs(d) : abs(d / mean)
    return rel < TodayParams.flatRelative ? 0 : (d > 0 ? 1 : -1)
  }
}

/// One row of Trends' 「今天」 list: the day's value, its direction against the user's baseline, a 7-day spark line.
struct TrendDayRow: Identifiable, Equatable {
  var id: String
  var label: String
  var value: String?
  var unit: String?
  /// The reading's own date when it is not the day itself (latest-only metrics such as weight).
  var date: String?
  var arrow: String?
  var tone: Tone = .text
  /// 「基线 48」: the baseline mean, short.
  var base: String?
  var spark: [Double?] = []
  var explain: String
}

/// Trends' 「今天」 (#357): every metric the range views show, for one day, grouped the same way. No single-point charts.
enum TrendDay {
  struct Conf {
    var label: String
    var unit: String
    var digits: Int
    var better: Int
    var explain: String
    /// Hours shown as h:mm.
    var hours = false
  }

  /// The range cards' metrics plus the special cards' own series.
  static func conf(_ key: String) -> Conf? {
    if let c = TrendCards.conf[key] { return Conf(label: c.label, unit: c.unit, digits: c.digits, better: c.better, explain: c.explain) }
    switch key {
    case "recovery": return Conf(label: tr("恢复", "Recovery"), unit: "", digits: 0, better: 1, explain: "recovery")
    case "strain": return Conf(label: tr("负荷", "Strain"), unit: "", digits: 1, better: 0, explain: "strain")
    case "sleep_hours": return Conf(label: tr("睡眠", "Sleep"), unit: "", digits: 1, better: 1, explain: "sleep_need", hours: true)
    case "tsb": return Conf(label: tr("训练负荷 TSB", "Training load TSB"), unit: "", digits: 0, better: 0, explain: "training_load")
    case "muscle_load": return Conf(label: tr("训练量", "Training volume"), unit: "kg", digits: 0, better: 0, explain: "muscle_load")
    default: return nil
    }
  }

  /// Same groups and order as `TrendCards.groups`.
  static var layout: [(id: String, title: String, keys: [String])] {
    [
      ("recovery", tr("恢复", "Recovery"), ["recovery", "hrv", "resting_hr", "resp_rate", "spo2", "wrist_temp", "illness", "recovery_index"]),
      ("sleep", tr("睡眠", "Sleep"), ["sleep_hours", "sleep_performance", "sleep_perf_whoop", "sleep_debt_hours", "restorative_hours",
                                      "sleep_efficiency", "sleep_score", "awakenings", "daylight"]),
      ("strain", tr("负荷", "Strain"), ["strain", "tsb", "acwr", "strain_combined", "muscle_load", "steps", "active_kcal", "total_kcal", "hr_recovery"]),
      ("stress", tr("压力", "Stress"), ["stress", "resilience"]),
      ("long", tr("长期", "Long term"), ["vo2max", "weight", "body_age"]),
    ]
  }

  /// Hidden when there is no reading in the last week (as the range views hide them when the range has none).
  static let optional: Set<String> = TrendCards.optional.union(["tsb", "muscle_load"])

  static func groups(_ d: TrendsData) -> [(id: String, title: String, rows: [TrendDayRow])] {
    layout.compactMap { g in
      let rows = g.keys.compactMap { row($0, d) }
      return rows.isEmpty ? nil : (g.id, g.title, rows)
    }
  }

  static func row(_ key: String, _ d: TrendsData) -> TrendDayRow? {
    guard let cf = conf(key) else { return nil }
    guard let s = d.series[key], !s.values.isEmpty else {
      return optional.contains(key) ? nil : TrendDayRow(id: key, label: cf.label, explain: cf.explain)
    }
    let i = s.values.count - 1
    let spark = Array(s.values.suffix(TodayParams.sparkDays))
    if optional.contains(key) && !spark.contains(where: { $0 != nil }) { return nil }
    var v = s.values[i], date: String?
    if TrendCards.latest.contains(key), v == nil, let j = s.values.lastIndex(where: { $0 != nil }) {
      v = s.values[j]
      date = d.dates[safe: j]
    }
    let unit = cf.unit.isEmpty ? s.unit : cf.unit
    var row = TrendDayRow(id: key, label: cf.label, value: v.map { format($0, cf) }, unit: cf.hours || unit.isEmpty ? nil : unit,
                          date: date, spark: spark, explain: cf.explain)
    if let v, date == nil, let b = DayBaseline.of(s, at: i) {
      let dir = b.direction(v)
      row.arrow = dir > 0 ? "↑" : dir < 0 ? "↓" : "→"
      row.tone = dir == 0 || cf.better == 0 ? .text : (dir == cf.better ? .good : .bad)
      row.base = tr("基线 \(format(b.mean, cf))", "base \(format(b.mean, cf))")
    }
    return row
  }

  static func format(_ v: Double, _ cf: Conf) -> String {
    cf.hours ? (TrendsFormat.hm(v * 60) ?? "—") : TodayModel.fmt(v, digits: cf.digits)
  }
}

/// A day's metric list in one card per group: label and baseline, the 7-day spark line, the value with its arrow.
struct TrendDayList: View {
  let rows: [TrendDayRow]
  let open: (TrendDayRow) -> Void

  var body: some View {
    VStack(spacing: 0) {
      ForEach(Array(rows.enumerated()), id: \.element.id) { i, r in
        Button { open(r) } label: { TrendDayRowView(row: r, last: i == rows.count - 1) }.buttonStyle(PressScale())
      }
    }
    .padding(.horizontal, 14)
    .background(RoundedRectangle(cornerRadius: Metrics.cardRadius, style: .continuous).fill(Palette.surface))
  }
}

struct TrendDayRowView: View {
  let row: TrendDayRow
  var last = false

  var body: some View {
    HStack(spacing: 10) {
      VStack(alignment: .leading, spacing: 1) {
        Text(row.label).font(Typo.body).foregroundColor(Palette.text).lineLimit(1).minimumScaleFactor(0.8)
        if let b = row.base ?? row.date.map({ TodayModel.shortDay($0) }) {
          Text(b).font(.system(size: 11).monospacedDigit()).foregroundColor(Palette.text3).lineLimit(1)
        }
      }
      Spacer(minLength: 6)
      Sparkline(values: row.spark, tone: .text).frame(width: 52, height: 18)
      HStack(alignment: .firstTextBaseline, spacing: 2) {
        Text(row.value ?? "—").font(Typo.bodyStrong.monospacedDigit())
          .foregroundColor(row.value == nil ? Palette.text3 : Palette.text).lineLimit(1)
        if let u = row.unit, row.value != nil { Text(u).font(Typo.unit).foregroundColor(Palette.text2).lineLimit(1) }
        Text(row.arrow ?? " ").font(.system(size: 13, weight: .semibold))
          .foregroundColor(row.tone == .text ? Palette.text3 : row.tone.color).frame(width: 12)
      }
      .frame(minWidth: 84, alignment: .trailing)
    }
    .padding(.vertical, 10)
    .overlay(last ? nil : Rectangle().fill(Palette.hair).frame(height: 1), alignment: .bottom)
    .contentShape(Rectangle())
    .accessibilityElement(children: .combine)
  }
}
