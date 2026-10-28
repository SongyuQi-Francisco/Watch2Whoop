import SwiftUI

/// The Trends tab's cards, built from `TrendsData` the way the page builds them (app.js metricsHtml: TCARD, CHART_OPTS,
/// the special cards). Numbers, charts and short labels only; the words are in the explain sheets.
struct TrendCardModel: Identifiable {
  enum Extra { case none, zones(TrendSeries.Zones), hrZones(TrendsData.HRZones), regularity(TrendsPage.Regularity) }
  struct Legend: Hashable { var tone: Tone; var text: String; var faint = false }

  var id: String
  var title: String
  /// Explain key (`/explain/metric/<key>`).
  var explain: String
  var value: String?
  var unit: String?
  var sub: String?
  /// "vs 上期" text and its colour (the base line of the read-out).
  var base: (String, Tone)?
  var spec: ChartSpec?
  var tip: (Int) -> String = { _ in "" }
  var legend: [Legend] = []
  var extra: Extra = .none
  /// A sheet of its own instead of the plain explanation (recovery contributors, sleep regularity).
  var sheet: TrendsSheet?
  var empty: String?
}

enum TrendCards {
  /// key → (label, unit, digits, better direction, chart, explain key); the page's TCARD.
  struct Conf {
    enum Chart { case line, bars, sparse }
    var label: String
    var unit: String
    var digits: Int
    var better: Int
    var chart: Chart
    var explain: String
    var min: Double? = nil
    var max: Double? = nil
    var zone: ClosedRange<Double>? = nil
  }

  static var conf: [String: Conf] {
    [
      "hrv": Conf(label: "HRV", unit: "ms", digits: 0, better: 1, chart: .line, explain: "hrv"),
      "resting_hr": Conf(label: tr("静息心率", "Resting HR"), unit: "bpm", digits: 0, better: -1, chart: .line, explain: "resting_hr"),
      "resp_rate": Conf(label: tr("呼吸频率", "Respiratory rate"), unit: tr("次/分", "/min"), digits: 1, better: 0, chart: .line, explain: "resp_rate"),
      "spo2": Conf(label: tr("血氧", "SpO₂"), unit: "%", digits: 1, better: 1, chart: .line, explain: "spo2", max: 100),
      "wrist_temp": Conf(label: tr("手腕温度", "Wrist temperature"), unit: "°C", digits: 2, better: 0, chart: .sparse, explain: "wrist_temp", zone: -0.5...0.5),
      "illness": Conf(label: tr("生病风险", "Illness risk"), unit: "", digits: 0, better: -1, chart: .line, explain: "illness", min: 0, max: 100, zone: 0...30),
      "recovery_index": Conf(label: tr("最低心率出现", "Lowest HR timing"), unit: "%", digits: 0, better: -1, chart: .line, explain: "recovery_index", min: 0, max: 100, zone: 0...50),
      "sleep_performance": Conf(label: tr("睡眠表现", "Sleep performance"), unit: "%", digits: 0, better: 1, chart: .line, explain: "sleep_performance", min: 0, max: 110, zone: 85...110),
      "sleep_perf_whoop": Conf(label: tr("睡眠表现（综合）", "Sleep performance (overall)"), unit: "", digits: 0, better: 1, chart: .line, explain: "sleep_perf_whoop", min: 0, max: 100, zone: 85...100),
      "sleep_debt_hours": Conf(label: tr("睡眠债", "Sleep debt"), unit: tr("小时", "h"), digits: 1, better: -1, chart: .line, explain: "sleep_debt"),
      "restorative_hours": Conf(label: tr("恢复性睡眠", "Restorative sleep"), unit: tr("小时", "h"), digits: 1, better: 1, chart: .bars, explain: "restorative"),
      "sleep_efficiency": Conf(label: tr("睡眠效率", "Sleep efficiency"), unit: "%", digits: 0, better: 1, chart: .line, explain: "sleep_efficiency", max: 100),
      "sleep_score": Conf(label: tr("睡眠分", "Sleep score"), unit: "", digits: 0, better: 1, chart: .line, explain: "sleep_score", min: 0, max: 100),
      "awakenings": Conf(label: tr("夜间醒来", "Awakenings"), unit: tr("次", ""), digits: 1, better: -1, chart: .bars, explain: "awakenings"),
      "daylight": Conf(label: tr("日照", "Daylight"), unit: tr("分钟", "min"), digits: 0, better: 1, chart: .bars, explain: "daylight"),
      "acwr": Conf(label: tr("急慢性负荷比", "Acute:chronic ratio"), unit: "", digits: 2, better: 0, chart: .line, explain: "acwr", min: 0, zone: 0.8...1.3),
      "strain_combined": Conf(label: tr("负荷（含肌肉）", "Strain (with muscle)"), unit: "", digits: 1, better: 0, chart: .line, explain: "strain_combined", min: 0, max: 21),
      "steps": Conf(label: tr("步数", "Steps"), unit: "", digits: 0, better: 1, chart: .bars, explain: "steps"),
      "active_kcal": Conf(label: tr("活动消耗", "Active energy"), unit: "kcal", digits: 0, better: 0, chart: .bars, explain: "calories"),
      "total_kcal": Conf(label: tr("总消耗", "Total energy"), unit: "kcal", digits: 0, better: 0, chart: .bars, explain: "calories"),
      "hr_recovery": Conf(label: tr("运动后心率恢复", "HR recovery"), unit: "bpm", digits: 0, better: 1, chart: .sparse, explain: "hr_recovery", min: 0, zone: 20...60),
      "stress": Conf(label: tr("压力", "Stress"), unit: "", digits: 1, better: -1, chart: .line, explain: "stress", min: 0, max: 3, zone: 0...1),
      "resilience": Conf(label: tr("韧性", "Resilience"), unit: "", digits: 0, better: 1, chart: .line, explain: "resilience", zone: 42...100),
      "vo2max": Conf(label: "VO₂max", unit: "", digits: 1, better: 1, chart: .sparse, explain: "vo2max"),
      "weight": Conf(label: tr("体重", "Weight"), unit: "kg", digits: 1, better: -1, chart: .sparse, explain: "weight"),
      "body_age": Conf(label: tr("身体年龄", "Body age"), unit: tr("岁", "y"), digits: 1, better: -1, chart: .sparse, explain: "body_age"),
    ]
  }
  /// Hidden when the range and the previous one have no reading at all.
  static let optional: Set<String> = ["recovery_index", "hr_recovery", "wrist_temp", "strain_combined", "daylight", "body_age"]
  /// The headline is the latest reading, not the mean.
  static let latest: Set<String> = ["vo2max", "weight", "body_age"]

  /// The groups in the page's order, each with its cards (empty groups dropped).
  static func groups(_ p: TrendsPage) -> [(id: String, title: String, cards: [TrendCardModel])] {
    let d = p.data
    let c = { (k: String) in metric(k, d) }
    let restPct = d.series["restorative_pct"]?.mean
    var list: [(String, String, [TrendCardModel?])] = [
      ("recovery", tr("恢复", "Recovery"), [recovery(d), c("hrv"), c("resting_hr"), c("resp_rate"), c("spo2"), c("wrist_temp"), c("illness"),
                                            c("recovery_index")]),
      ("sleep", tr("睡眠", "Sleep"), [sleepNeed(d), c("sleep_performance"), c("sleep_perf_whoop"), c("sleep_debt_hours"),
                                      metric("restorative_hours", d, sub: restPct.map { " \(TodayModel.fmt($0))%" }), c("sleep_efficiency"),
                                      c("sleep_score"), regularity(p.regularity), c("awakenings"), c("daylight")]),
      ("strain", tr("负荷", "Strain"), [strain(d), recoveryStrain(d), load(d), c("acwr"), c("strain_combined"),
                                       weekly("azm", tr("有氧分钟", "Aerobic minutes"), "aerobic_min", d.aerobicWeeks, unit: tr("分钟", "min"), target: 150),
                                       weekly("vol", tr("训练量", "Training volume"), "muscle_load", d.muscleWeeks, unit: "kg", target: nil),
                                       hrZones(d), c("steps"), c("active_kcal"), c("total_kcal"), c("hr_recovery")]),
      ("stress", tr("压力", "Stress"), [c("stress"), c("resilience")]),
      ("long", tr("长期", "Long term"), [metric("vo2max", d, base: p.fitness), c("weight"), c("body_age")]),
    ]
    list = list.map { ($0.0, $0.1, $0.2.filter { $0 != nil }) }
    return list.compactMap { g in
      let cards = g.2.compactMap { $0 }
      return cards.isEmpty ? nil : (g.0, g.1, cards)
    }
  }

  // MARK: - helpers

  static func delta(_ cur: Double?, _ prev: Double?, digits: Int, better: Int) -> (String, Tone)? {
    guard let cur, let prev else { return nil }
    let dv = cur - prev, small = abs(dv) < pow(10, -Double(digits)) / 2
    let tone: Tone = small || better == 0 ? .text : dv * Double(better) > 0 ? .good : .bad
    return ("\(small ? "→" : dv > 0 ? "↑" : "↓") \(TodayModel.fmt(abs(dv), digits: digits))", tone)
  }

  static func tipDay(_ d: TrendsData, _ values: [Double?], digits: Int, unit: String) -> (Int) -> String {
    { i in
      let v = values[safe: i] ?? nil
      return "\(tipDate(d, i)) · " + (v.map { TodayModel.fmt($0, digits: digits) + (unit.isEmpty ? "" : " \(unit)") } ?? "—")
    }
  }

  /// The touched day; with the year on long ranges.
  static func tipDate(_ d: TrendsData, _ i: Int) -> String {
    d.dates[safe: i].map { TrendsFormat.day($0, year: d.withYear) } ?? ""
  }

  static func noData(_ d: TrendsData) -> String {
    let r = TrendRange(rawValue: d.days)?.label ?? tr("\(d.days) 天", "\(d.days) days")
    return tr("\(r) 无数据", "No data in \(r)")
  }

  static func spec(_ id: String, _ d: TrendsData, _ layers: [ChartLayer], conf: Conf? = nil, min: Double? = nil, max: Double? = nil,
                   zone: ClosedRange<Double>? = nil, guides: [Double] = [], label: String) -> ChartSpec {
    ChartSpec(id: id, dates: d.dates, layers: layers, min: min ?? conf?.min, max: max ?? conf?.max, zone: zone ?? conf?.zone,
              guides: guides, label: label)
  }

  // MARK: - the plain metric card

  static func metric(_ key: String, _ d: TrendsData, sub: String? = nil, base: String? = nil) -> TrendCardModel? {
    guard let cf = conf[key] else { return nil }
    guard let s = d.series[key] else {
      return optional.contains(key) ? nil : TrendCardModel(id: key, title: cf.label, explain: cf.explain, empty: noData(d))
    }
    if s.n == 0 && optional.contains(key) && s.prevN == 0 { return nil }
    let head = latest.contains(key) ? s.last : s.mean
    let unit = cf.unit.isEmpty ? s.unit : cf.unit
    let layers: [ChartLayer]
    switch cf.chart {
    case .bars: layers = [ChartLayer(kind: .bars, values: s.values, tone: .text, main: true)]
    case .sparse: layers = [ChartLayer(kind: .dots, values: s.values, tone: .text, main: true), ChartLayer(kind: .avg, values: s.avg7)]
    case .line:
      var l: [ChartLayer] = []
      if let b = s.baseline { l.append(ChartLayer(kind: .band, lo: b.lo, hi: b.hi)) }
      l += [ChartLayer(kind: .line, values: s.values, tone: .text, faint: true, main: true), ChartLayer(kind: .avg, values: s.avg7)]
      layers = l
    }
    var legend: [TrendCardModel.Legend] = []
    if s.baseline != nil, s.n > 0 { legend.append(.init(tone: .text, text: tr("基线", "Baseline"), faint: true)) }
    return TrendCardModel(
      id: key, title: cf.label, explain: cf.explain, value: head.map { TodayModel.fmt($0, digits: cf.digits) }, unit: unit.isEmpty ? nil : unit,
      sub: sub, base: base.map { ($0, .text) } ?? delta(head, s.prevMean, digits: cf.digits, better: cf.better),
      spec: s.n > 0 ? spec(key, d, layers, conf: cf, label: cf.label) : nil,
      tip: tipDay(d, s.values, digits: cf.digits, unit: unit), legend: legend, empty: s.n > 0 ? nil : noData(d))
  }

  // MARK: - special cards

  /// Recovery: bars in recovery colours, the day counts, contributors in its sheet.
  static func recovery(_ d: TrendsData) -> TrendCardModel? {
    let title = tr("恢复", "Recovery")
    guard let s = d.series["recovery"] else { return TrendCardModel(id: "recovery", title: title, explain: "recovery", empty: noData(d)) }
    let tones: [Tone] = s.values.map { v in v.map { $0 >= 67 ? .good : $0 >= 34 ? .mid : .bad } ?? .text }
    return TrendCardModel(
      id: "recovery", title: title, explain: "recovery", value: s.mean.map { TodayModel.fmt($0) },
      base: delta(s.mean, s.prevMean, digits: 0, better: 1),
      spec: s.n > 0 ? spec("recovery", d, [ChartLayer(kind: .bars, values: s.values, tones: tones, main: true)], min: 0, max: 100, label: title) : nil,
      tip: tipDay(d, s.values, digits: 0, unit: ""), extra: s.zones.map { .zones($0) } ?? .none, sheet: .recovery, empty: s.n > 0 ? nil : noData(d))
  }

  /// Strain: daily bars inside each day's Strain Coach band; days in range.
  static func strain(_ d: TrendsData) -> TrendCardModel? {
    let title = tr("负荷", "Strain")
    guard let s = d.series["strain"] else { return TrendCardModel(id: "strain", title: title, explain: "strain", empty: noData(d)) }
    let bands = s.target?.bands ?? []
    let tones: [Tone] = s.values.enumerated().map { i, v in
      guard let v, let b = bands[safe: i] ?? nil else { return .text }
      return b.contains(v) ? .good : .text
    }
    var legend: [TrendCardModel.Legend] = []
    if let t = s.target, t.daysWithStrain > 0 {
      legend = [.init(tone: .train, text: tr("目标区间", "Target band"), faint: true),
                .init(tone: .good, text: tr("\(t.inRange)/\(t.daysWithStrain) 天达标", "\(t.inRange)/\(t.daysWithStrain) days in band"))]
    }
    return TrendCardModel(
      id: "strain", title: title, explain: "strain", value: s.mean.map { TodayModel.fmt($0, digits: 1) },
      base: delta(s.mean, s.prevMean, digits: 1, better: 0),
      spec: s.n > 0 ? spec("strain", d, [ChartLayer(kind: .range, ranges: bands), ChartLayer(kind: .bars, values: s.values, tones: tones, main: true)],
                           min: 0, max: 21, label: title) : nil,
      tip: { i in
        let v = s.values[safe: i] ?? nil, b = bands[safe: i] ?? nil
        return "\(tipDate(d, i)) · \(v.map { TodayModel.fmt($0, digits: 1) } ?? "—")"
          + (b.map { " / \(TodayModel.fmt($0.lowerBound))–\(TodayModel.fmt($0.upperBound))" } ?? "")
      },
      legend: legend, empty: s.n > 0 ? nil : noData(d))
  }

  /// Recovery and strain on one chart (7-day means; strain on its own 0–21 scale).
  static func recoveryStrain(_ d: TrendsData) -> TrendCardModel? {
    guard let r = d.series["recovery"], let s = d.series["strain"], r.n > 0, s.n > 0 else { return nil }
    let title = tr("恢复 vs 负荷", "Recovery vs strain")
    return TrendCardModel(
      id: "recovery_vs_strain", title: title, explain: "recovery_vs_strain", value: r.mean.map { TodayModel.fmt($0) },
      unit: s.mean.map { "/ \(TodayModel.fmt($0, digits: 1))" },
      spec: spec("rs", d, [ChartLayer(kind: .avg, values: r.avg7, tone: .good, main: true),
                           ChartLayer(kind: .avg, values: s.avg7, tone: .train, rightAxis: true)], min: 0, max: 100, label: title),
      tip: { i in
        "\(tipDate(d, i)) · \(tr("恢复", "Recovery")) \((r.values[safe: i] ?? nil).map { TodayModel.fmt($0) } ?? "—")"
          + " · \(tr("负荷", "Strain")) \((s.values[safe: i] ?? nil).map { TodayModel.fmt($0, digits: 1) } ?? "—")"
      },
      legend: [.init(tone: .good, text: tr("恢复", "Recovery")), .init(tone: .train, text: tr("负荷", "Strain")),
               .init(tone: .text, text: tr("7 天均", "7-day mean"), faint: true)])
  }

  /// Sleep vs need: bars = actual hours, line = need.
  static func sleepNeed(_ d: TrendsData) -> TrendCardModel? {
    let title = tr("睡眠 vs 需求", "Sleep vs need")
    guard let a = d.series["sleep_hours"] else { return TrendCardModel(id: "sleep_hours", title: title, explain: "sleep_need", empty: noData(d)) }
    let need = d.series["sleep_need_hours"]
    var layers = [ChartLayer(kind: .bars, values: a.values, tone: .sleep, main: true)]
    if let need { layers.append(ChartLayer(kind: .line, values: need.values, tone: .mid, dots: false)) }
    var legend: [TrendCardModel.Legend] = []
    if let need {
      let h = need.mean.map { TodayModel.fmt($0, digits: 1) } ?? "—"
      legend = [.init(tone: .sleep, text: tr("实际", "Actual")), .init(tone: .mid, text: tr("需求 \(h) 小时", "Need \(h) h"))]
    }
    return TrendCardModel(
      id: "sleep_hours", title: title, explain: "sleep_need", value: a.mean.map { TodayModel.fmt($0, digits: 1) }, unit: tr("小时", "h"),
      base: delta(a.mean, a.prevMean, digits: 1, better: 1),
      spec: a.n > 0 ? spec("sleep_hours", d, layers, min: 0, label: title) : nil,
      tip: { i in
        "\(tipDate(d, i)) · \(TrendsFormat.hm((a.values[safe: i] ?? nil).map { $0 * 60 }) ?? "—")"
          + ((need?.values[safe: i] ?? nil).map { " / \(TrendsFormat.hm($0 * 60) ?? "")" } ?? "")
      },
      legend: legend, empty: a.n > 0 ? nil : noData(d))
  }

  /// Training load: CTL (fitness) and ATL (fatigue); headline the latest TSB.
  static func load(_ d: TrendsData) -> TrendCardModel? {
    guard let c = d.series["ctl"], c.n > 0 else { return nil }
    let a = d.series["atl"], t = d.series["tsb"]
    let title = tr("训练负荷", "Training load")
    var layers = [ChartLayer(kind: .avg, values: c.values, tone: .train, main: true)]
    if let a { layers.append(ChartLayer(kind: .avg, values: a.values, tone: .mid)) }
    return TrendCardModel(
      id: "training_load", title: title, explain: "training_load", value: t?.last.map { TrendsFormat.signed($0, digits: 0) }, unit: "TSB",
      base: delta(c.last, c.prevMean, digits: 0, better: 1), spec: spec("ctl", d, layers, min: 0, label: title),
      tip: { i in
        let f = { (s: TrendSeries?) in (s?.values[safe: i] ?? nil).map { TodayModel.fmt($0) } ?? "—" }
        return "\(tipDate(d, i)) · CTL \(f(c)) · ATL \(f(a)) · TSB \((t?.values[safe: i] ?? nil).map { TrendsFormat.signed($0, digits: 0) } ?? "—")"
      },
      legend: [.init(tone: .train, text: "CTL \(c.last.map { TodayModel.fmt($0) } ?? "—")"),
               .init(tone: .mid, text: "ATL \(a?.last.map { TodayModel.fmt($0) } ?? "—")")])
  }

  /// Weekly bars (aerobic minutes against 150 a week; strength volume).
  static func weekly(_ id: String, _ title: String, _ explain: String, _ weeks: [TrendsData.Week], unit: String, target: Double?) -> TrendCardModel? {
    guard !weeks.isEmpty, weeks.contains(where: { $0.value > 0 }) else { return nil }
    let vals: [Double?] = weeks.map(\.value)
    let hits = target.map { t in weeks.filter { $0.value >= t }.count }
    let tones: [Tone] = weeks.map { w in target.map { w.value >= $0 ? .good : .train } ?? .train }
    let avg = weeks.map(\.value).reduce(0, +) / Double(weeks.count)
    let top = Swift.max(target ?? 0, weeks.map(\.value).max() ?? 0) * 1.1
    let spec = ChartSpec(id: id, dates: weeks.map(\.week), layers: [ChartLayer(kind: .bars, values: vals, tones: tones, main: true)],
                         min: 0, max: top, zone: nil, guides: target.map { [$0] } ?? [], label: title)
    return TrendCardModel(
      id: id, title: title, explain: explain, value: TodayModel.fmt(avg), unit: "\(unit) / \(tr("周", "wk"))",
      base: hits.map { (tr("\($0)/\(weeks.count) 周达标", "\($0)/\(weeks.count) weeks on target"), .text) }, spec: spec,
      tip: { i in "\(tr("\(TodayModel.shortDay(weeks[safe: i]?.week)) 起", "Week of \(TodayModel.shortDay(weeks[safe: i]?.week))")) · \(TodayModel.fmt(weeks[safe: i]?.value ?? 0)) \(unit)" })
  }

  /// Heart-rate zones over the range (total hours, the five zones, the last 8 weeks).
  static func hrZones(_ d: TrendsData) -> TrendCardModel? {
    guard let z = d.hrZones, z.totalMin.contains(where: { $0 > 0 }) else { return nil }
    let total = z.totalMin.reduce(0, +)
    return TrendCardModel(id: "hr_zones", title: tr("心率区间分布", "Heart-rate zones"), explain: "hr_zones",
                          value: TodayModel.fmt(total / 60, digits: 1), unit: tr("小时", "h"), base: ("%HRmax", .text), extra: .hrZones(z))
  }

  /// Sleep regularity (sleep/details): 7- and 30-day figures, bedtime → wake bars of the last nights.
  static func regularity(_ r: TrendsPage.Regularity?) -> TrendCardModel? {
    guard let r, let v = r.pct7 ?? r.pct30 else { return nil }
    let tail = r.pct7 != nil && r.pct30 != nil ? " · \(tr("30 天", "30 d")) \(TodayModel.fmt(r.pct30!))%" : ""
    return TrendCardModel(id: "sleep_regularity", title: tr("睡眠规律性", "Sleep regularity"), explain: "sleep_consistency",
                          value: TodayModel.fmt(v), unit: "%", base: ((r.pct7 != nil ? tr("7 天", "7 d") : tr("30 天", "30 d")) + tail, .text),
                          extra: .regularity(r), sheet: .regularity)
  }
}
