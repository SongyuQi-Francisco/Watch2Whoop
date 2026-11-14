import Foundation

/// The three scores the 身体 hero pages through (docs/design/2.0.1 `05-body`: swipe = change score, page dots).
enum BodyScore: String, CaseIterable, Identifiable {
  case recovery, sleep, strain
  var id: String { rawValue }

  /// The `/trends` series behind it.
  var seriesKey: String {
    switch self {
    case .recovery: return "recovery"
    case .sleep: return "sleep_score"
    case .strain: return "strain"
    }
  }

  /// The label above the mean.
  var label: String {
    switch self {
    case .recovery: return tr("平均恢复", "Avg recovery")
    case .sleep: return tr("平均睡眠", "Avg sleep")
    case .strain: return tr("平均负荷", "Avg strain")
    }
  }

  /// Top of the scale (recovery and sleep 0–100, strain 0–21).
  var max: Double { self == .strain ? 21 : 100 }
  var digits: Int { self == .strain ? 1 : 0 }
  var percent: Bool { self == .recovery }
  /// 1 = higher is better, 0 = neither (strain): only a better / worse change takes a colour.
  var better: Int { self == .strain ? 0 : 1 }
  /// The score's own explanation key (explain sheet).
  var explainKey: String { seriesKey }
}

/// What the 身体 hero draws for one score over the tab's range.
struct BodyHero: Equatable {
  var score: BodyScore
  /// Mean of the range (the one big number); nil = no reading in the range.
  var mean: Double?
  /// Mean minus the previous period's mean, rounded to the score's digits; nil without a previous period.
  var delta: Double?
  var deltaTone: Tone
  /// Oldest to newest (weekly means above `BodyParams.weeklyBarsAbove` days).
  var bars: [Double?]
  /// Your usual (P25–P75 of the bars before the last), on the score's scale.
  var usual: ClosedRange<Double>?
  /// Colour of the newest bar.
  var lastTone: Tone
  /// Three date labels under the bars: first, middle, last day of the range.
  var dates: [String]
}

/// Pure rules of the 身体 tab (unit-tested in `BodyGlanceTests`).
enum BodyGlanceRules {
  /// Days the hero covers for a range (「今天」 has no period of its own: the last 30 days of its 61-day document).
  static func window(_ range: TrendRange) -> Int {
    range == .today ? BodyParams.todayWindow : range.rawValue
  }

  static func hero(_ d: TrendsData, score: BodyScore, range: TrendRange) -> BodyHero {
    let n = window(range)
    let series = d.series[score.seriesKey]
    let all = series?.values ?? []
    let values = Array(all.suffix(n))
    let dates = Array(d.dates.suffix(n))
    let xs = values.compactMap { $0 }
    let mean: Double? = xs.isEmpty ? nil : xs.reduce(0, +) / Double(xs.count)
    // the server's previous-period mean belongs to the document's own range; 「今天」's 61 days have none of ours
    var delta: Double?
    if range != .today, let m = mean, let p = series?.prevMean {
      delta = round(m - p, digits: score.digits)
    }
    let bars = bucketed(values)
    let last = bars.last ?? nil
    return BodyHero(score: score, mean: mean, delta: delta, deltaTone: deltaTone(delta, better: score.better),
                    bars: bars, usual: TodayGlanceRules.usual(bars), lastTone: tone(last, score: score),
                    dates: dateLabels(dates))
  }

  /// Daily values, or weekly means (newest week ending on the last day) when the range is long.
  static func bucketed(_ values: [Double?], above: Int = BodyParams.weeklyBarsAbove,
                       size: Int = BodyParams.barBucket) -> [Double?] {
    guard values.count > above, size > 1 else { return values }
    var out: [Double?] = []
    var end = values.count
    while end > 0 {
      let start = Swift.max(0, end - size)
      let xs = values[start..<end].compactMap { $0 }
      out.append(xs.isEmpty ? nil : xs.reduce(0, +) / Double(xs.count))
      end = start
    }
    return out.reversed()
  }

  /// A better change is good, a worse one bad; strain (neither) and no change stay ink.
  static func deltaTone(_ delta: Double?, better: Int) -> Tone {
    guard let d = delta, d != 0, better != 0 else { return .text }
    return d * Double(better) > 0 ? .good : .bad
  }

  /// The newest bar's colour: recovery by its zone (34 / 67), sleep and strain by category.
  static func tone(_ v: Double?, score: BodyScore) -> Tone {
    switch score {
    case .sleep: return .sleep
    case .strain: return .train
    case .recovery:
      guard let v else { return .text }
      if v < TodayGlanceRules.recoveryZones[0] { return .bad }
      if v < TodayGlanceRules.recoveryZones[1] { return .mid }
      return .good
    }
  }

  /// 「10/12」 … : first, middle and last date of the range.
  static func dateLabels(_ dates: [String]) -> [String] {
    guard let first = dates.first, let last = dates.last else { return [] }
    if dates.count < 3 { return [TodayModel.shortDay(first), TodayModel.shortDay(last)] }
    let mid = dates[dates.count / 2]
    return [TodayModel.shortDay(first), TodayModel.shortDay(mid), TodayModel.shortDay(last)]
  }

  /// 「+3」 / 「−0.4」 / 「0」 at the score's digits.
  static func signed(_ v: Double, digits: Int) -> String {
    let text = TodayModel.fmt(abs(v), digits: digits)
    if v > 0 { return "+" + text }
    if v < 0 { return "−" + text }
    return text
  }

  static func round(_ v: Double, digits: Int) -> Double {
    let f = pow(10, Double(digits))
    return (v * f).rounded() / f
  }

  // MARK: faces

  /// The 周报 face: the last `days` days up to the end day, true = trained (aerobic minutes or strength volume), nil = no
  /// reading of either that day.
  static func trainingDays(_ d: TrendsData, days: Int = BodyParams.weekDays) -> [Bool?] {
    let aerobic = Array((d.series["aerobic_min"]?.values ?? []).suffix(days))
    let muscle = Array((d.series["muscle_load"]?.values ?? []).suffix(days))
    var out: [Bool?] = []
    for i in 0..<days {
      let a = at(aerobic, i, count: days)
      let m = at(muscle, i, count: days)
      if a == nil && m == nil {
        out.append(nil)
      } else {
        let trained = (a ?? 0) >= BodyParams.trainingMinMinutes || (m ?? 0) > 0
        out.append(trained)
      }
    }
    return out
  }

  /// The value of day `i` of the last `count` days, the list being aligned to its end (it may be shorter).
  private static func at(_ xs: [Double?], _ i: Int, count: Int) -> Double? {
    let k = i - (count - xs.count)
    guard k >= 0, k < xs.count else { return nil }
    return xs[k]
  }

  /// The 心肺 face: the latest HRV, its last 30 days and its usual; nil without any reading.
  static func hrv(_ d: TrendsData, days: Int = BodyParams.hrvDays) -> (last: Double, values: [Double?], usual: ClosedRange<Double>?)? {
    guard let s = d.series["hrv"], let last = s.last else { return nil }
    let values = Array(s.values.suffix(days))
    return (last, values, TodayGlanceRules.usual(values))
  }

  /// Body age face: where the body age and the calendar age sit on a scale of calendar age ± `span` (0…1 each).
  static func agePositions(body: Double, chrono: Double, span: Double = BodyParams.ageSpan) -> (body: Double, chrono: Double) {
    let lo = chrono - span, w = span * 2
    let b = Swift.min(1, Swift.max(0, (body - lo) / w))
    return (b, 0.5)
  }
}
