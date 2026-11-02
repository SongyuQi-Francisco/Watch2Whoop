import Foundation

/// What a 需要注意 item or a 今日核心 card opens.
enum TodayOpen: Equatable {
  case metric(key: String, title: String)
  case status(TrendStatus.Tile)
  case coach
}

/// One 「需要注意」 item (#357): numbers and a short label only; the words are in the sheet it opens.
struct TodayAttention: Equatable, Identifiable {
  var id: String
  var label: String
  var value: String
  var unit: String?
  var arrow: String?
  /// 「基线 48」 / the status card's small line / the monitor's range.
  var base: String?
  var tone: Tone
  var severity: Double
  var open: TodayOpen
}

/// One 「今日核心」 card (#357), drawn like the Trends status cards.
struct TodayCoreCard: Equatable, Identifiable {
  var id: String
  var label: String
  var value: String
  var unit: String?
  var sub: String?
  var tone: Tone
  var local = false
  var open: TodayOpen
}

/// The day's Trends view (the 61-day `/trends` document and the phone's status cards), as the Today tab reads it.
struct TodayTrends: Equatable {
  var data: TrendsData
  var status: TrendStatus

  /// Only a document that ends on `day` describes it (the cached one may still be yesterday's after midnight).
  init?(json: [String: Any]?, local: [String: Any]?, day: String?) {
    guard let json, let day, let data = TrendsData(json: json), data.end == day else { return nil }
    self.data = data
    let merged = TrendsLocal.merge(server: data.status, local: TrendsLocal.Output(json: local))
    status = TrendStatus(status: merged.status, local: merged.keys)
  }

  /// The day's value of a series (its last element).
  func value(_ key: String) -> Double? { data.series[key]?.values.last ?? nil }
}

/// 「需要注意」: what is abnormal on the day, worst first, at most `TodayParams.maxItems` (thresholds in `TodayParams`).
enum TodayAttentionRules {
  static func select(trends: TodayTrends?, vitals: [TodayModel.Vital]) -> [TodayAttention] {
    var items: [TodayAttention] = []
    if let t = trends {
      items += TodayParams.watch.compactMap { metric($0, t.data) }
      items += t.status.tiles.compactMap(status)
    }
    items += vitals.compactMap(vital)
    // one item per metric: the most severe reading of it (a vital off its range and off the baseline is the same thing)
    var best: [String: TodayAttention] = [:], order: [String] = []
    for it in items {
      if let cur = best[it.id] {
        if it.severity > cur.severity { best[it.id] = it }
      } else {
        best[it.id] = it
        order.append(it.id)
      }
    }
    let ranked = order.enumerated().compactMap { i, k in best[k].map { (i, $0) } }
      .sorted { a, b in a.1.severity != b.1.severity ? a.1.severity > b.1.severity : a.0 < b.0 }
    return Array(ranked.map(\.1).prefix(TodayParams.maxItems))
  }

  /// A metric clearly off the user's own baseline, in its bad direction.
  static func metric(_ w: TodayParams.Watch, _ d: TrendsData) -> TodayAttention? {
    guard let s = d.series[w.key], let v = s.values.last ?? nil, let cf = TrendCards.conf[w.key] else { return nil }
    let severity: Double
    var base: String?
    if let limit = w.absolute {
      guard v * Double(w.bad) >= limit else { return nil }
      severity = min(TodayParams.severityCap, TodayParams.atThreshold * v * Double(w.bad) / limit)
    } else {
      guard let b = DayBaseline.of(s, at: s.values.count - 1), let z = b.z(v) else { return nil }
      guard z * Double(w.bad) >= w.z, (v - b.mean) * Double(w.bad) >= w.minDelta else { return nil }
      severity = min(TodayParams.severityCap, abs(z))
      base = tr("基线 \(TodayModel.fmt(b.mean, digits: cf.digits))", "base \(TodayModel.fmt(b.mean, digits: cf.digits))")
    }
    let unit = cf.unit.isEmpty ? s.unit : cf.unit
    let value = w.absolute != nil ? TrendsFormat.signed(v, digits: cf.digits) : TodayModel.fmt(v, digits: cf.digits)
    return TodayAttention(id: w.key, label: cf.label, value: value, unit: unit.isEmpty ? nil : unit, arrow: w.bad > 0 ? "↑" : "↓",
                          base: base, tone: severity >= 2.5 ? .bad : .mid, severity: severity, open: .metric(key: cf.explain, title: cf.label))
  }

  /// A status card in yellow / red (which cards count, and from which colour: `TodayParams.statusMinTone`).
  static func status(_ t: TrendStatus.Tile) -> TodayAttention? {
    guard let min = TodayParams.statusMinTone[t.key], rank(t.tone) >= rank(min), rank(t.tone) > 0 else { return nil }
    return TodayAttention(id: t.key, label: t.label, value: t.value, unit: t.unit, base: t.sub, tone: t.tone,
                          severity: t.tone == .bad ? TodayParams.statusBad : TodayParams.statusMid, open: .status(t))
  }

  /// A health-monitor vital outside the server's range for it.
  static func vital(_ v: TodayModel.Vital) -> TodayAttention? {
    guard v.off, let x = v.value else { return nil }
    let key = v.explainKey ?? v.id
    let cf = TrendCards.conf[key]
    let range: String? = v.low != nil && v.high != nil
      ? "\(TodayModel.fmt(v.low!, digits: cf?.digits ?? 1))–\(TodayModel.fmt(v.high!, digits: cf?.digits ?? 1))" : nil
    var arrow: String?
    if let h = v.high, x > h { arrow = "↑" } else if let l = v.low, x < l { arrow = "↓" }
    return TodayAttention(id: key, label: cf?.label ?? v.name, value: TodayModel.fmt(x, digits: cf?.digits ?? 1), unit: v.unit,
                          arrow: arrow, base: range, tone: .bad, severity: TodayParams.monitorOff,
                          open: .metric(key: cf?.explain ?? key, title: cf?.label ?? v.name))
  }

  static func rank(_ t: Tone) -> Int {
    switch t {
    case .bad: return 2
    case .mid: return 1
    default: return 0
    }
  }
}

/// 「今日核心」: the strain target (live), recovery vs strain, stress, illness risk, body battery, HRV status, training
/// status. A card without data is left out.
enum TodayCoreCards {
  static let statusKeys = ["illness", "body_battery", "hrv_status", "training_status"]

  static func build(coach: TodayModel.Coach?, trends: TodayTrends?) -> [TodayCoreCard] {
    var out: [TodayCoreCard] = []
    if let c = coach {
      out.append(TodayCoreCard(id: "coach", label: tr("负荷 / 目标", "Strain / target"), value: c.current.map { TodayModel.fmt($0, digits: 1) } ?? "—",
                               unit: "/ \(TodayModel.fmt(c.low))–\(TodayModel.fmt(c.high))", sub: c.stateLabel, tone: c.tone, open: .coach))
    }
    guard let t = trends else { return out }
    if let r = t.data.series["recovery"]?.avg7.last ?? nil, let s = t.data.series["strain"]?.avg7.last ?? nil {
      out.append(TodayCoreCard(id: "recovery_vs_strain", label: tr("恢复 vs 负荷", "Recovery vs strain"), value: TodayModel.fmt(r),
                               unit: "/ \(TodayModel.fmt(s, digits: 1))", sub: tr("7 天均", "7-day mean"), tone: .text,
                               open: .metric(key: "recovery_vs_strain", title: tr("恢复 vs 负荷", "Recovery vs strain"))))
    }
    if let v = t.value("stress") {
      let tone: Tone = v >= TodayParams.stressHigh ? .bad : v >= TodayParams.stressMedium ? .mid : .good
      let level = v >= TodayParams.stressHigh ? tr("高", "High") : v >= TodayParams.stressMedium ? tr("中", "Medium") : tr("低", "Low")
      out.append(TodayCoreCard(id: "stress", label: tr("压力", "Stress"), value: TodayModel.fmt(v, digits: 1), unit: "/ 3", sub: level,
                               tone: tone, open: .metric(key: "stress", title: tr("压力", "Stress"))))
    }
    for k in statusKeys {
      guard let tile = t.status.tiles.first(where: { $0.key == k }) else { continue }
      out.append(TodayCoreCard(id: k, label: tile.label, value: tile.value, unit: tile.unit, sub: tile.sub, tone: tile.tone,
                               local: tile.local, open: .status(tile)))
    }
    return out
  }
}
