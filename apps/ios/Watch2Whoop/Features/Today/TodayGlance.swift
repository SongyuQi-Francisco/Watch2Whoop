import Foundation

/// What the 2.0.1 Today draws (docs/design/2.0.1 `01-today`, `02`–`04`, `kit-states`): one recovery dial, two attached
/// readings (sleep score, strain so far), the face row, and the numbers of the three L1 sheets. Built from the same
/// documents as `TodayModel` (no new data source); anything the documents do not carry is left out, never made up.
/// Pure values, so SwiftUI can compare them and the rules can be tested.
struct TodayGlance: Equatable {
  /// The hero: recovery on a 0–100 tick dial.
  struct Dial: Equatable {
    var value: Double?
    var tone: Tone = .text
    /// Your usual (30-day P25–P75), drawn as a grey lane inside the dial; nil in the first two weeks.
    var usual: ClosedRange<Double>?
    /// The last 7 days, today last (nil = no score that day).
    var week: [Double?] = []
    /// The verdict word under the number (the server's `recovery_word`, else by zone).
    var verdict: String?
    /// Why there is no number (≤ 6 characters), e.g. 「昨晚没戴表」.
    var reason: String?
  }

  /// A reading under the hero: number + bullet gauge on a tick scale.
  struct Gauge: Equatable {
    var value: Double?
    var text: String?
    var max: Double
    var usual: ClosedRange<Double>?
    var target: ClosedRange<Double>?
    var step: Double
    var majors: [Double] = []
    var tone: Tone
  }

  /// One row of an L1 sheet: label · 14-day spark on the usual band · today · change vs usual.
  struct Row: Equatable, Identifiable {
    var id: String
    var label: String
    var value: String?
    var unit: String?
    var spark: [Double?] = []
    var usual: ClosedRange<Double>?
    var delta: String?
    var deltaTone: Tone = .text
    /// A bullet instead of a spark (strain rows): today against the usual and the scale's end.
    var bullet: Bullet?
    /// Explain sheet of the row (metric key, title).
    var explain: (key: String, title: String)?

    static func == (a: Row, b: Row) -> Bool {
      a.id == b.id && a.label == b.label && a.value == b.value && a.unit == b.unit && a.spark == b.spark && a.usual == b.usual
        && a.delta == b.delta && a.deltaTone == b.deltaTone && a.bullet == b.bullet && a.explain?.key == b.explain?.key
    }
  }

  struct Bullet: Equatable {
    var value: Double
    var max: Double
    var usual: ClosedRange<Double>?
    /// Centred scale (training state: TSB around 0).
    var centred = false
  }

  var recovery = Dial()
  var sleep = Gauge(value: nil, text: nil, max: 100, step: 10, tone: .sleep)
  var strain = Gauge(value: nil, text: nil, max: 21, step: 2, majors: TodayGlanceRules.strainMajors, tone: .train)
  /// Recovery L1: the last 30 days (today last) and their dates.
  var month: [Double?] = []
  var monthDates: [String] = []
  var recoveryRows: [Row] = []
  var strainRows: [Row] = []
  /// The attention face's last 7 days (its metric's series), when it has one.
  var attentionWeek: [Double?] = []
}

/// The rules behind the glance (tested in TodayGlanceTests). Thresholds are the design's (direction.md 2.5, 3, 7).
enum TodayGlanceRules {
  /// Zone edges of the recovery dial (long ticks) and of the strain scale.
  static let recoveryZones: [Double] = [34, 67]
  static let strainMajors: [Double] = [10, 14, 18]
  /// The usual lane needs this many days in the window (first two weeks: no usual line).
  static let usualMinDays = 14
  static let usualWindow = 30
  static let sparkDays = 14

  /// Your usual: P25–P75 of the last `usualWindow` days before the last one (today is not part of its own usual).
  static func usual(_ values: [Double?], includeLast: Bool = false) -> ClosedRange<Double>? {
    let hist = includeLast ? values : Array(values.dropLast())
    let xs = hist.suffix(usualWindow).compactMap { $0 }.sorted()
    guard xs.count >= usualMinDays else { return nil }
    let lo = percentile(xs, 0.25), hi = percentile(xs, 0.75)
    return lo...max(lo, hi)
  }

  /// The middle of your usual (median of the same window), what a delta is measured from.
  static func usualMid(_ values: [Double?]) -> Double? {
    let xs = values.dropLast().suffix(usualWindow).compactMap { $0 }.sorted()
    guard xs.count >= usualMinDays else { return nil }
    return percentile(xs, 0.5)
  }

  /// Linear-interpolated percentile of sorted values.
  static func percentile(_ sorted: [Double], _ p: Double) -> Double {
    guard !sorted.isEmpty else { return 0 }
    let r = p * Double(sorted.count - 1)
    let i = Int(r.rounded(.down)), f = r - Double(i)
    return i + 1 < sorted.count ? sorted[i] + (sorted[i + 1] - sorted[i]) * f : sorted[i]
  }

  /// Arc fraction of a recovery value (0…1, clamped).
  static func dialFraction(_ v: Double?) -> Double {
    guard let v else { return 0 }
    return min(1, max(0, v / 100))
  }

  /// Position of a value on a gauge's scale (0…1, clamped).
  static func fraction(_ v: Double, max: Double, min lo: Double = 0) -> Double {
    guard max > lo else { return 0 }
    return Swift.min(1, Swift.max(0, (v - lo) / (max - lo)))
  }

  /// The verdict word: the server's, else by zone (the server's words, recovery_word in web/views.py).
  static func verdict(server: String?, value: Double?) -> String? {
    if let s = server, !s.isEmpty { return s }
    guard let v = value else { return nil }
    switch TodayModel.zone(v) {
    case .good: return tr("可上强度", "Ready to push")
    case .mid: return tr("按计划练", "Train as planned")
    default: return tr("以恢复为主", "Focus on recovery")
    }
  }

  /// Why a day has no recovery, short (≤ 6 characters): from the score's flags, else the empty state's own label.
  static func reason(flags: [String: Any]?, emptyLabel: String?) -> String? {
    if let f = flags {
      if f["no_sleep"] != nil { return tr("昨晚没戴表", "Not worn") }
      if f["hrv_missing"] != nil { return tr("缺 HRV", "No HRV") }
      if f["baseline_insufficient"] != nil { return tr("基线不足", "Short baseline") }
    }
    guard let l = emptyLabel, !l.isEmpty else { return nil }
    return l.count <= 6 ? l : tr("暂无评分", "No score")
  }

  /// Signed change vs your usual ("+6", "−2", "±0"), digits like the value.
  static func delta(_ v: Double?, mid: Double?, digits: Int) -> String? {
    guard let v, let mid else { return nil }
    let d = v - mid
    if abs(d) < 0.5 / pow(10, Double(digits)) { return "±0" }
    return (d > 0 ? "+" : "−") + TodayModel.fmt(abs(d), digits: digits)
  }

  /// Colour of a delta: outside your usual in the better direction = good, in the worse = mid, else ink.
  /// `better`: +1 higher is better, −1 lower is better, 0 neither.
  static func deltaTone(_ v: Double?, usual: ClosedRange<Double>?, better: Int) -> Tone {
    guard let v, let u = usual, better != 0 else { return .text }
    if v > u.upperBound { return better > 0 ? .good : .mid }
    if v < u.lowerBound { return better < 0 ? .good : .mid }
    return .text
  }

  /// The last `n` values, padded with nil at the front (a series shorter than n, a new user).
  static func tail(_ values: [Double?], _ n: Int) -> [Double?] {
    let t = Array(values.suffix(n))
    return Array(repeating: nil, count: max(0, n - t.count)) + t
  }

  /// Vitals in range of those that have a value: (in range, total).
  static func vitalsCount(_ vitals: [TodayModel.Vital]) -> (ok: Int, total: Int) {
    let have = vitals.filter { $0.value != nil }
    return (have.filter { !$0.off }.count, have.count)
  }

  /// Where a vital's dot sits on its pill (0 = bottom of its range, 1 = top), clamped a little outside.
  static func vitalPosition(_ v: TodayModel.Vital) -> Double? {
    guard let x = v.value, let lo = v.low, let hi = v.high, hi > lo else { return nil }
    return min(1.1, max(-0.1, (x - lo) / (hi - lo)))
  }
}

/// The capsule at the top right of every tab root (kit-states: ok / syncing / behind / failed).
enum SyncCapsuleState: Equatable {
  case ok(ago: String?)
  case busy
  case behind(String)
  case failed

  /// From the sync service's overall state and the server's overview card. `behind` names the missing days when the
  /// overview's coverage says so, else the age.
  static func of(overall: SyncDiagnosis.Overall, card: TodayModel.SyncCard?, missingDays: Int?) -> SyncCapsuleState {
    switch overall {
    case .syncing, .processing: return .busy
    case .attention:
      if let card, card.state == "issue", card.tone == .bad { return .failed }
      return .behind(missingDays.map { tr("缺\($0)天", "\($0) d missing") } ?? card?.ago ?? tr("需处理", "Check"))
    case .ok, .off:
      if let card, card.state == "issue" {
        return card.tone == .bad ? .failed : .behind(card.ago ?? tr("需处理", "Check"))
      }
      return .ok(ago: card?.ago)
    }
  }
}

extension TodayGlance {
  /// Built inside `TodayModel.init` from the same inputs.
  init(model m: TodayModel, scores: [String: Any], today t: [String: Any], sleepScore: Double?, trends: TodayTrends?,
       daily: [String: Any]) {
    let R = TodayGlanceRules.self
    let series = trends?.data.series ?? [:]
    func values(_ key: String) -> [Double?] { series[key]?.values ?? [] }

    // ---- hero: recovery
    let rec = values("recovery")
    recovery.value = m.detail.recovery
    recovery.tone = m.detail.recovery == nil ? .text : m.detail.zone
    recovery.usual = R.usual(rec)
    var week = R.tail(rec, 7)
    if let v = m.detail.recovery, !week.isEmpty { week[week.count - 1] = v }   // the phone's own score fills today
    recovery.week = week.contains { $0 != nil } ? week : []
    recovery.verdict = m.detail.recovery == nil ? nil : R.verdict(server: scores["recovery_word"] as? String, value: m.detail.recovery)
    if m.detail.recovery == nil {
      recovery.reason = R.reason(flags: t["flags"] as? [String: Any], emptyLabel: (scores["recovery_empty"] as? [String: Any])?["label"] as? String)
    }
    var month = R.tail(rec, 30)
    if let v = m.detail.recovery, !month.isEmpty { month[month.count - 1] = v }
    self.month = month.contains { $0 != nil } ? month : []
    monthDates = Array((trends?.data.dates ?? []).suffix(30))

    // ---- readings: sleep score, strain so far against today's target
    let sleepVals = values("sleep_score")
    sleep.value = sleepScore
    sleep.text = sleepScore.map { TodayModel.fmt($0) }
    sleep.usual = R.usual(sleepVals)

    // the strain target's live number; without it the day's own strain in the series (never yesterday's)
    strain.value = m.coach?.current ?? (series["strain"]?.values.last ?? nil)
    if let c = m.coach { strain.target = c.low...max(c.low, c.high) }
    strain.text = strain.value.map { TodayModel.fmt($0, digits: 1) }

    // ---- recovery L1 rows: HRV, resting HR, breathing (14-day spark on the usual band, delta vs usual)
    func row(_ key: String, label: String, unit: String, digits: Int, better: Int, explain: String, today: Double?) -> Row? {
      let vs = values(key)
      let v = today ?? (vs.last ?? nil)
      guard v != nil || vs.contains(where: { $0 != nil }) else { return nil }
      let u = R.usual(vs)
      var spark = R.tail(vs, R.sparkDays)
      if let v, !spark.isEmpty { spark[spark.count - 1] = v }
      return Row(id: key, label: label, value: v.map { TodayModel.fmt($0, digits: digits) }, unit: unit,
                 spark: spark, usual: u, delta: R.delta(v, mid: R.usualMid(vs), digits: digits),
                 deltaTone: R.deltaTone(v, usual: u, better: better), explain: (explain, label))
    }
    recoveryRows = [
      row("hrv", label: "HRV", unit: "ms", digits: 0, better: 1, explain: "hrv", today: TodayModel.num(t["hrv_night_ms"])),
      row("resting_hr", label: tr("静息心率", "Resting HR"), unit: "bpm", digits: 0, better: -1, explain: "resting_hr",
          today: TodayModel.num(t["rhr_night"])),
      row("resp_rate", label: tr("呼吸", "Breathing"), unit: tr("次/分", "/min"), digits: 1, better: 0, explain: "resp_rate", today: nil),
    ].compactMap { $0 }

    // ---- strain L1 rows: steps, active energy, exercise minutes (bullet: today vs usual), training state (TSB)
    func bulletRow(_ key: String, label: String, unit: String, today: Double?, explain: String) -> Row? {
      let vs = values(key)
      guard let v = today ?? (vs.last ?? nil) else { return nil }
      let u = R.usual(vs)
      let top = Swift.max(v, u?.upperBound ?? 0) * 1.4
      return Row(id: key, label: label, value: TodayModel.fmt(v), unit: unit, usual: u,
                 delta: R.delta(v, mid: R.usualMid(vs), digits: 0), deltaTone: R.deltaTone(v, usual: u, better: 1),
                 bullet: Bullet(value: v, max: Swift.max(1, top), usual: u), explain: (explain, label))
    }
    var rows: [Row?] = [
      bulletRow("steps", label: tr("步数", "Steps"), unit: tr("步", "steps"), today: TodayModel.num(daily["steps"]), explain: "steps"),
      bulletRow("active_kcal", label: tr("活动能量", "Active energy"), unit: "kcal", today: TodayModel.num(daily["active_kcal"]),
                explain: "active_kcal"),
    ]
    if let ex = TodayModel.num(daily["exercise_min"]) {
      rows.append(Row(id: "exercise_min", label: tr("运动分钟", "Exercise"), value: TodayModel.fmt(ex), unit: tr("分", "min"),
                      bullet: Bullet(value: ex, max: Swift.max(60, ex * 1.4)), explain: ("exercise_min", tr("锻炼分钟", "Exercise minutes"))))
    }
    if let tsb = m.detail.tsb {
      rows.append(Row(id: "tsb", label: tr("训练状态", "Training state"), value: TodayGlanceRules.signed(tsb), unit: nil,
                      delta: m.detail.loadLabel, bullet: Bullet(value: tsb, max: 40, centred: true), explain: ("tsb", tr("训练状态", "Training state"))))
    }
    strainRows = rows.compactMap { $0 }

    if let a = m.attention.first, let s = series[a.id] {
      let w = R.tail(s.values, 7)
      attentionWeek = w.contains { $0 != nil } ? w : []
    }
  }
}

extension TodayGlanceRules {
  /// "+4" / "−12" (whole numbers, signed).
  static func signed(_ v: Double) -> String {
    let r = v.rounded()
    return r > 0 ? "+\(TodayModel.fmt(r))" : r < 0 ? "−\(TodayModel.fmt(-r))" : "0"
  }
}
