import Charts
import SwiftUI

/// Body cards (body_engine ai/cards_body.py). The server builds each from the same function as the page's endpoint, so
/// they go through the pages' own parsers and views where those exist (`TrendsData`, `ReportModel`, `LabHistory`,
/// `BehaviorRow`, `MonitorRow`): a card here reads like the card on its page. Numbers, charts and short labels only.

// MARK: - trends: strain, trend, weight, VO₂max, heart-rate zones

extension JarvisCards {
  /// `/trends` cut to the card's series (cards_body._trends), in the Trends tab's model.
  static func trendsData(_ c: JSONValue) -> TrendsData? {
    (c["trend"]?.any as? [String: Any]).flatMap(TrendsData.init(json:))
  }

  /// The Trends tab's card for a key: its special card, its plain card, or (a series without a card there) the
  /// series' own label and unit as a line. `TrendCards.metric` is nil only for those.
  static func trendModel(_ key: String, _ d: TrendsData, base: String? = nil) -> TrendCardModel? {
    switch key {
    case "recovery": return TrendCards.recovery(d)
    case "strain": return TrendCards.strain(d)
    case "sleep_hours": return TrendCards.sleepNeed(d)
    default: break
    }
    if let m = TrendCards.metric(key, d, base: base) { return m }
    guard let s = d.series[key] else { return nil }
    let digits = abs(s.mean ?? 0) < 100 ? 1 : 0
    let unit = s.unit.isEmpty ? nil : s.unit
    return TrendCardModel(
      id: key, title: s.label, explain: key, value: s.mean.map { TodayModel.fmt($0, digits: digits) }, unit: unit,
      basis: s.n > 0 ? TrendCards.meanBasis(d) : nil, base: TrendCards.delta(s.mean, s.prevMean, digits: digits, better: 0),
      spec: s.n > 0 ? TrendCards.spec(key, d, [ChartLayer(kind: .line, values: s.values, tone: .text, faint: true, main: true), ChartLayer(kind: .avg, values: s.avg7)], label: s.label) : nil,
      tip: TrendCards.tipDay(d, s.values, digits: digits, unit: s.unit), empty: s.n > 0 ? nil : TrendCards.noData(d))
  }
}

/// A Trends card as it is on the tab; its title opens the same explanation, over the chat.
struct TrendCardBody: View {
  let model: TrendCardModel
  let open: (String, JSONValue) -> Void

  var body: some View {
    TrendCardView(card: model) { sheet in
      switch sheet {
      case .explain(let key, let title, _): open("explain.\(key)", .object(["title": .string(title)]))
      case .recovery: open("explain.recovery", .object(["title": .string(model.title)]))
      default: break
      }
    }
  }
}

/// A framed note when a card's data is not there yet: the name and 「暂无数据」, as on the page.
struct NoDataCard: View {
  let title: String
  var date: String?

  var body: some View {
    VStack(alignment: .leading, spacing: 8) {
      CardHead(title: title, date: date)
      Text(tr("暂无数据", "No data yet")).font(Typo.caption).foregroundColor(Palette.text2)
    }
    .jarvisCardFrame()
  }
}

/// trend / weight: the Trends card of that metric. VO₂max carries its level as the base line.
struct TrendJarvisCard: View {
  let c: JSONValue
  let key: String
  var base: String?
  let open: (String, JSONValue) -> Void

  var body: some View {
    if let d = JarvisCards.trendsData(c), let m = JarvisCards.trendModel(key, d, base: base) {
      TrendCardBody(model: m, open: open)
    } else {
      NoDataCard(title: TrendCards.conf[key]?.label ?? JarvisCards.title("trend"))
    }
  }
}

/// strain: today's target (Strain Coach) above the last two weeks.
struct StrainJarvisCard: View {
  let c: JSONValue
  let open: (String, JSONValue) -> Void

  /// The state label and its colour (app.js strainCoachHtml, same as `TodayModel.Coach`).
  static func state(_ coach: JSONValue) -> (String, Tone) {
    let cur = coach["current_strain"]?.double, low = coach["target_low"]?.double ?? 0
    switch coach["status"]?.string {
    case "in_range": return (tr("达标", "On target"), .good)
    case "above": return (tr("超出", "Over"), .mid)
    default:
      guard let cur else { return ("—", .text) }
      let left = TodayModel.fmt(coach["remaining_to_low"]?.double ?? low - cur, digits: 1)
      return (tr("差 \(left)", "\(left) to go"), .text)
    }
  }

  var body: some View {
    let d = JarvisCards.trendsData(c), trend = d.flatMap(TrendCards.strain)
    let coach = c["coach"]
    VStack(alignment: .leading, spacing: 8) {
      if let coach, let low = coach["target_low"]?.double, let high = coach["target_high"]?.double {
        let cur = coach["current_strain"]?.double, s = Self.state(coach)
        VStack(alignment: .leading, spacing: 8) {
          CardHead(title: tr("负荷目标", "Strain target"), date: c["date"]?.string, badge: s.0, badgeTone: s.1)
          BigNumber(value: JarvisCards.num(cur, 1), note: "\(JarvisCards.num(low))–\(JarvisCards.num(high))")
          ScaleBar.coach(cur, low: low, high: high)
        }
        .jarvisCardFrame()
      }
      if let trend {
        TrendCardBody(model: trend, open: open)
      } else if coach?["target_low"]?.double == nil {
        NoDataCard(title: JarvisCards.title("strain"), date: c["date"]?.string)
      }
    }
  }
}

/// vo2max: the Trends card with the level the page shows on it.
struct VO2JarvisCard: View {
  let c: JSONValue
  let open: (String, JSONValue) -> Void

  static func level(_ c: JSONValue) -> String? {
    let s = Lang.isZh ? c["level_zh"]?.string : (c["level_label"]?.string ?? c["level"]?.string)
    return s?.isEmpty == false ? s : nil
  }

  var body: some View { TrendJarvisCard(c: c, key: "vo2max", base: Self.level(c), open: open) }
}

/// hr_zones: the Trends card of the range's zones.
struct HRZonesJarvisCard: View {
  let c: JSONValue
  let open: (String, JSONValue) -> Void

  /// Same shape as `/trends`' `hr_zones` (`TrendsData.init`).
  static func zones(_ c: JSONValue) -> TrendsData.HRZones? {
    guard let z = c["zones"], let total = z["total_min"]?.array?.map({ $0.double ?? 0 }), total.contains(where: { $0 > 0 }) else { return nil }
    return TrendsData.HRZones(labels: z["labels"]?.array?.compactMap(\.string) ?? [], totalMin: total,
                              weeks: (z["weeks"]?.array ?? []).map { w in
                                (String((w["week"]?.string ?? "").prefix(10)), (w["minutes"]?.array ?? []).map { $0.double ?? 0 })
                              })
  }

  var body: some View {
    if let z = Self.zones(c) {
      TrendCardBody(model: TrendCardModel(id: "hr_zones", title: JarvisCards.title("hr_zones"), explain: "hr_zones",
                                          value: TodayModel.fmt(z.totalMin.reduce(0, +) / 60, digits: 1), unit: tr("小时", "h"),
                                          base: ("%HRmax", .text), extra: .hrZones(z)), open: open)
    } else {
      NoDataCard(title: JarvisCards.title("hr_zones"), date: c["date"]?.string)
    }
  }
}

// MARK: - stress

struct StressJarvisCard: View {
  let c: JSONValue

  /// A reason in a few words (app.js STRESS_SHORT); the server's longer `flags` text stays for the AI.
  static func reason(_ flags: JSONValue?) -> String? {
    let keys = (flags?.object ?? [:]).keys
    return [("no_raw_hr", tr("无心率", "No heart rate")), ("baseline_insufficient", tr("基线不足", "Baseline too short")),
            ("no_calm_hours", tr("无安静时段", "No calm hours")), ("no_motion_data", tr("无步数", "No step data"))]
      .first { keys.contains($0.0) }?.1
  }

  static func level(_ l: String?) -> (String, Tone)? {
    switch l {
    case "low": return (tr("低", "Low"), .good)
    case "medium": return (tr("中", "Medium"), .mid)
    case "high": return (tr("高", "High"), .bad)
    default: return nil
    }
  }

  var body: some View {
    let d = c["days"]?[0]
    let level = Self.level(d?["level"]?.string)
    VStack(alignment: .leading, spacing: 8) {
      CardHead(title: JarvisCards.title("stress"), date: c["date"]?.string, badge: level?.0, badgeTone: level?.1)
      if let d, let score = d["score"]?.double {
        BigNumber(value: JarvisCards.num(score, 1), unit: "/ 3",
                  note: d["peak_hour"]?.int.map { tr("峰值", "Peak") + " \($0):00" })
        StressCurve(hours: d["hours"]?.array ?? [])
        StressHours(d: d)
        if let calm = d["baseline"]?["calm_hr"]?.double {
          Text(tr("平静心率", "Calm HR") + " \(JarvisCards.num(calm)) bpm").font(Typo.caption.monospacedDigit()).foregroundColor(Palette.text2)
        }
      } else {
        BigNumber(value: "—", note: Self.reason(d?["flags"]) ?? tr("暂无数据", "No data yet"))
      }
    }
  }
}

/// The day's hourly stress, 0–3 (app.js stressHtml).
private struct StressCurve: View {
  let hours: [JSONValue]

  var body: some View {
    let pts = hours.compactMap { h -> (Double, Double)? in
      guard let x = h["hour"]?.double, let y = h["score"]?.double else { return nil }
      return (x, min(3, max(0, y)))
    }
    if pts.count > 1 {
      VStack(spacing: 2) {
        Canvas { ctx, size in
          let pt = { (p: (Double, Double)) in CGPoint(x: size.width * p.0 / 23, y: size.height * (1 - p.1 / 3)) }
          var line = Path()
          line.addLines(pts.map(pt))
          var area = line
          area.addLine(to: CGPoint(x: pt(pts[pts.count - 1]).x, y: size.height))
          area.addLine(to: CGPoint(x: pt(pts[0]).x, y: size.height))
          area.closeSubpath()
          ctx.fill(area, with: .color(Palette.text.opacity(0.08)))
          ctx.stroke(line, with: .color(Palette.text), style: StrokeStyle(lineWidth: 1.5, lineCap: .round, lineJoin: .round))
        }
        .frame(height: 44)
        HStack {
          ForEach([0, 6, 12, 18, 23], id: \.self) { h in
            Text("\(h)")
            if h != 23 { Spacer() }
          }
        }
        .font(.system(size: 10, weight: .medium).monospacedDigit()).foregroundColor(Palette.text3)
      }
      .accessibilityHidden(true)
    }
  }
}

/// Hours at each level as one bar (the page's `.hbar`).
private struct StressHours: View {
  let d: JSONValue

  var body: some View {
    let parts: [(String, Double, Tone)] = [(tr("高", "High"), d["high_h"]?.double ?? 0, .bad), (tr("中", "Medium"), d["medium_h"]?.double ?? 0, .mid),
                                           (tr("低", "Low"), d["low_h"]?.double ?? 0, .good)]
    let total = parts.map(\.1).reduce(0, +)
    if total > 0 {
      VStack(alignment: .leading, spacing: 5) {
        GeometryReader { g in
          HStack(spacing: 2) {
            ForEach(Array(parts.enumerated()), id: \.offset) { _, p in
              if p.1 > 0 { Capsule().fill(p.2.color).frame(width: max(2, g.size.width * p.1 / total - 2)) }
            }
          }
        }
        .frame(height: 6)
        HStack(spacing: 14) {
          ForEach(Array(parts.enumerated()), id: \.offset) { _, p in
            Text("\(JarvisCards.num(p.1, 1)) h \(p.0)")
          }
        }
        .font(.system(size: 11).monospacedDigit()).foregroundColor(Palette.text2)
      }
    }
  }
}

// MARK: - body age

struct HealthspanJarvisCard: View {
  let c: JSONValue

  /// Fixed decimals with a real minus (app.js signed).
  static func signed(_ v: Double, _ digits: Int = 1) -> String {
    (v > 0 ? "+" : v < 0 ? "−" : "") + String(format: "%.\(digits)f", abs(v))
  }

  /// A contributor's own number in a few characters (app.js contribValue).
  static func value(_ x: JSONValue) -> String? {
    guard let v = x["value"]?.double else { return nil }
    if x["key"]?.string == "body_comp" {
      let w = x["measure"]?.string == "waist_to_height"
      return "\(w ? tr("腰围身高比", "Waist:height") : "BMI") \(JarvisCards.num(v, w ? 2 : 1))"
    }
    let digits = abs(v) >= 100 || v.rounded() == v ? 0 : 1
    return TodayModel.fmt(v, digits: digits) + (x["unit"]?.string.map { " \($0)" } ?? "")
  }

  static func category(_ k: String) -> String {
    switch k {
    case "sleep": return tr("睡眠", "Sleep")
    case "activity": return tr("活动", "Activity")
    case "fitness": return tr("体能", "Fitness")
    default: return tr("其他", "Other")
    }
  }

  var body: some View {
    VStack(alignment: .leading, spacing: 10) {
      CardHead(title: JarvisCards.title("healthspan"), date: c["as_of"]?.string)
      if let age = c["body_age"]?.double {
        HStack(alignment: .firstTextBaseline, spacing: 14) {
          HStack(alignment: .firstTextBaseline, spacing: 3) {
            Text(JarvisCards.num(age, 1)).font(.system(size: 28, weight: .bold).monospacedDigit()).foregroundColor(Palette.text)
            Text(tr("岁", "y")).font(Typo.unit).foregroundColor(Palette.text2)
          }
          Spacer(minLength: 4)
          if let d = c["delta_years"]?.double {
            side(Self.signed(d), tr("和实际比", "vs actual"), d > 0 ? .bad : d < 0 ? .good : .text)
          }
          if let p = c["pace_of_aging"]?.double {
            side("\(Self.signed(p))×", tr("衰老速度", "Pace"), p < 0.95 ? .good : p > 1.05 ? .bad : .text)
          }
        }
        contributors
        DisclaimerTag(kind: .medical)
      } else {
        Text(tr("数据不足", "Not enough data")).font(Typo.caption).foregroundColor(Palette.text2)
      }
    }
  }

  private func side(_ v: String, _ l: String, _ tone: Tone) -> some View {
    VStack(alignment: .trailing, spacing: 1) {
      Text(v).font(Typo.bodyStrong.monospacedDigit()).foregroundColor(tone.color)
      Text(l).font(Typo.label).foregroundColor(Palette.text2).lineLimit(1)
    }
  }

  /// Each item's contribution in years, by domain, in the server's order (app.js healthspanDetailHtml).
  @ViewBuilder private var contributors: some View {
    let list = c["contributors"]?.array ?? []
    let top = max(0.5, list.compactMap { $0["years"]?.double }.map(abs).max() ?? 0.5)
    let cats = list.reduce(into: [String]()) { out, x in
      let k = x["category"]?.string ?? "other"
      if !out.contains(k) { out.append(k) }
    }
    ForEach(cats, id: \.self) { cat in
      VStack(alignment: .leading, spacing: 8) {
        Text(Self.category(cat)).font(Typo.label).foregroundColor(Palette.text2)
        ForEach(Array(list.filter { ($0["category"]?.string ?? "other") == cat }.enumerated()), id: \.offset) { _, x in
          contributor(x, top: top)
        }
      }
    }
    let lack = (c["insufficient"]?.array ?? []).compactMap { $0["label"]?.string }
    if !lack.isEmpty {
      VStack(alignment: .leading, spacing: 6) {
        Text(tr("数据不足", "Not enough data")).font(Typo.label).foregroundColor(Palette.text2)
        FlowRow(spacing: 6) { ForEach(lack, id: \.self) { ToneBadge(text: $0) } }
      }
    }
  }

  private func contributor(_ x: JSONValue, top: Double) -> some View {
    let y = x["years"]?.double ?? 0
    let tone: Tone = y > 0 ? .bad : y < 0 ? .good : .text
    return VStack(alignment: .leading, spacing: 4) {
      HStack(alignment: .firstTextBaseline, spacing: 6) {
        Text(x["label"]?.string ?? "").font(Typo.caption).foregroundColor(Palette.text).lineLimit(1)
        if let v = Self.value(x) { Text(v).font(Typo.label.monospacedDigit()).foregroundColor(Palette.text2).lineLimit(1) }
        if x["coverage"]?.string == "possibly_incomplete" { ToneBadge(text: tr("记录可能不全", "May be incomplete"), tone: .mid) }
        Spacer(minLength: 4)
        Text(tr("\(Self.signed(y)) 年", "\(Self.signed(y)) y")).font(Typo.label.monospacedDigit()).foregroundColor(tone == .text ? Palette.text2 : tone.color)
      }
      // younger to the right, as on the page (`.dbar`), so it reads the way the behaviour bars do
      DiffBar(fraction: -y / top, tone: tone)
    }
  }
}

// MARK: - lab

struct LabJarvisCard: View {
  let c: JSONValue

  var body: some View {
    VStack(alignment: .leading, spacing: 8) {
      if let h = LabHistory.parse(c.any as? [String: Any]), let last = h.rows.last {
        CardHead(title: h.name, date: nil)
        HStack(alignment: .firstTextBaseline, spacing: 4) {
          Text(last.value.map { RecordFormat.num($0, 2) } ?? last.valueText ?? "—").font(.system(size: 28, weight: .bold).monospacedDigit()).foregroundColor(Palette.text)
          if let u = h.unit, last.value != nil { Text(u).font(Typo.unit).foregroundColor(Palette.text2) }
          if let st = LabSummary.statusTitle(last.status) { ToneBadge(text: st, tone: LabSummary.tone(last.status)).padding(.leading, 4) }
          Spacer(minLength: 4)
          Text(RecordDay.short(last.date)).font(Typo.caption.monospacedDigit()).foregroundColor(Palette.text2)
        }
        LabCardChart(h: h)
        rows(h)
      } else {
        CardHead(title: c["test_name"]?.string ?? JarvisCards.title("lab"), date: nil)
        Text(tr("暂无数据", "No data yet")).font(Typo.caption).foregroundColor(Palette.text2)
      }
    }
  }

  /// The last four, newest first, with the change from the one before (app.js labCardHtml).
  private func rows(_ h: LabHistory) -> some View {
    VStack(spacing: 0) {
      ForEach(h.rows.suffix(4).reversed()) { r in
        HStack(alignment: .firstTextBaseline, spacing: 8) {
          Text(RecordDay.short(r.date)).font(Typo.caption).foregroundColor(Palette.text2).frame(width: 52, alignment: .leading)
          Text(r.value.map { RecordFormat.num($0, 2) } ?? r.valueText ?? "—").font(Typo.bodyStrong.monospacedDigit()).foregroundColor(Palette.text)
          if let d = r.delta {
            Text("\(d > 0 ? "+" : "")\(RecordFormat.num(d, 2))").font(Typo.caption.monospacedDigit()).foregroundColor(h.deltaTone(d).color)
          }
          Spacer(minLength: 4)
          if r.homeDevice { Badge(label: tr("家测", "Home"), tone: .mid) }
          if let f = r.fasting { Badge(label: f ? tr("空腹", "Fasting") : tr("饭后", "After a meal")) }
          if let st = LabSummary.statusTitle(r.status) { Badge(label: st, tone: LabSummary.tone(r.status)) }
        }
        .padding(.vertical, 8)
        .overlay(Rectangle().fill(Palette.hair).frame(height: 1), alignment: .top)
      }
    }
  }
}

/// The readings as a line against the reference band (the same scale as `LabTrendView`, smaller).
private struct LabCardChart: View {
  let h: LabHistory

  var body: some View {
    let pts = h.rows.compactMap { r -> (Date, Double, Bool)? in
      guard let v = r.value, let d = RecordDay.date(r.date) else { return nil }
      return (d, v, r.homeDevice)
    }
    if pts.count >= 2 {
      VStack(alignment: .leading, spacing: 6) {
        Chart {
          if let band = LabTrendView.band(h, pts.map(\.1)), let first = pts.first?.0, let last = pts.last?.0 {
            RectangleMark(xStart: .value("from", first), xEnd: .value("to", last), yStart: .value("low", band.0), yEnd: .value("high", band.1))
              .foregroundStyle(Palette.good.opacity(0.12))
          }
          ForEach(Array(pts.enumerated()), id: \.offset) { _, p in
            LineMark(x: .value("date", p.0), y: .value("value", p.1)).foregroundStyle(Palette.text2)
            PointMark(x: .value("date", p.0), y: .value("value", p.1)).foregroundStyle(p.2 ? Palette.mid : Palette.text).symbolSize(36)
          }
        }
        .chartYScale(domain: LabTrendView.domain(h, pts.map(\.1)))
        .chartXAxis(.hidden)
        .frame(height: 96)
        .accessibilityHidden(true)
        HStack(spacing: 12) {
          legend(Palette.text, tr("化验", "Lab"))
          if pts.contains(where: { $0.2 }) { legend(Palette.mid, tr("家测", "Home")) }
          if let r = Self.reference(h) {
            Text(tr("参考 \(r)", "Range \(r)")).font(.system(size: 11)).foregroundColor(Palette.text2)
          }
        }
      }
    }
  }

  private func legend(_ color: Color, _ text: String) -> some View {
    HStack(spacing: 4) {
      Circle().fill(color).frame(width: 6, height: 6)
      Text(text).font(.system(size: 11)).foregroundColor(Palette.text2)
    }
  }

  static func reference(_ h: LabHistory) -> String? {
    switch (h.refLow, h.refHigh) {
    case let (lo?, hi?): return "\(RecordFormat.num(lo, 2))–\(RecordFormat.num(hi, 2))"
    case let (nil, hi?): return "≤ \(RecordFormat.num(hi, 2))"
    case let (lo?, nil): return "≥ \(RecordFormat.num(lo, 2))"
    default: return nil
    }
  }
}

// MARK: - report

/// The period against the previous one: `ReportModel`, as on Trends → 报告.
struct ReportJarvisCard: View {
  let c: JSONValue

  var body: some View {
    let month = c["period"]?.string == "month"
    let title = month ? tr("月报", "Monthly") : tr("周报", "Weekly")
    VStack(alignment: .leading, spacing: 10) {
      if let r = (c.any as? [String: Any]).flatMap(ReportModel.init(json:)), !r.tiles.isEmpty {
        CardHead(title: title)
        HStack(spacing: 4) {
          Text(r.range).font(Typo.bodyStrong.monospacedDigit()).foregroundColor(Palette.text)
          if let p = r.previous { Text("vs \(p)").font(Typo.caption.monospacedDigit()).foregroundColor(Palette.text2) }
        }
        ForEach(r.findings) { f in
          HStack {
            Text(f.label).font(Typo.caption).foregroundColor(Palette.text).lineLimit(1)
            Spacer(minLength: 4)
            Text("\(tr("次日恢复", "Next-day recovery")) \(TrendsFormat.signed(f.diff, digits: 0))").font(Typo.label.monospacedDigit())
              .foregroundColor(f.diff > 0 ? Palette.good : f.diff < 0 ? Palette.bad : Palette.text2)
          }
        }
        LazyVGrid(columns: [GridItem(.flexible(), spacing: 8), GridItem(.flexible(), spacing: 8)], spacing: 8) {
          ForEach(r.tiles) { tile($0) }
        }
      } else {
        CardHead(title: title)
        Text(tr("暂无数据", "No data yet")).font(Typo.caption).foregroundColor(Palette.text2)
      }
    }
  }

  private func tile(_ t: ReportModel.Tile) -> some View {
    VStack(alignment: .leading, spacing: 3) {
      Text(t.label).font(Typo.label).foregroundColor(Palette.text2)
      HStack(alignment: .firstTextBaseline, spacing: 2) {
        Text(t.value).font(.system(size: 18, weight: .semibold).monospacedDigit()).foregroundColor(Palette.text).lineLimit(1).minimumScaleFactor(0.6)
        if let u = t.unit { Text(u).font(Typo.unit).foregroundColor(Palette.text2) }
      }
      Text(t.delta ?? " ").font(Typo.label.monospacedDigit()).foregroundColor(t.tone == .text ? Palette.text3 : t.tone.color)
    }
    .frame(maxWidth: .infinity, alignment: .leading)
    .padding(10)
    .background(RoundedRectangle(cornerRadius: 10, style: .continuous).fill(Palette.surface2))
    .accessibilityElement(children: .combine)
  }
}

// MARK: - health monitor

/// Five vital signs against the personal normal range (`MonitorRow`, as in Today's sheet).
struct MonitorJarvisCard: View {
  let c: JSONValue
  let open: (String, JSONValue) -> Void

  /// The Today page's vitals from the card's `metrics`; an item without data does not count as off.
  static func vitals(_ c: JSONValue) -> [TodayModel.Vital] {
    (c["metrics"]?.array ?? []).map { x in
      let key = x["key"]?.string ?? "", value = x["value"]?.double, st = x["status"]?.string, label = x["label"]?.string
      return TodayModel.Vital(id: key, name: TodayModel.vitalName(key, label), short: TodayModel.vitalShort(key, label), value: value,
                              unit: x["unit"]?.string, low: x["low"]?.double, high: x["high"]?.double,
                              off: value != nil && (st == "high" || st == "low"), missing: x["missing_reason"]?.string,
                              explainKey: ["rhr": "resting_hr", "hrv": "hrv"][key])
    }
  }

  var body: some View {
    let list = Self.vitals(c), off = list.filter(\.off).count, hasData = list.contains(where: { $0.value != nil })
    VStack(alignment: .leading, spacing: 12) {
      CardHead(title: JarvisCards.title("monitor"), date: c["date"]?.string,
               badge: !hasData ? nil : off > 0 ? tr("\(off) 异常", "\(off) off") : tr("正常", "Normal"), badgeTone: off > 0 ? Tone.bad : Tone.good)
      if hasData {
        ForEach(list) { v in
          MonitorRow(vital: v) { key in open("explain.\(key)", .object(["title": .string(v.name)])) }
        }
        DisclaimerTag(kind: .medical)
      } else {
        Text(tr("暂无数据", "No data yet")).font(Typo.caption).foregroundColor(Palette.text2)
      }
    }
  }
}

// MARK: - journal

/// Each journal item against next-day recovery (`BehaviorRow`, as on Trends → 报告).
struct JournalJarvisCard: View {
  let c: JSONValue

  var body: some View {
    let b = BehaviorRow.parse((c["results"]?.any as? [[String: Any]]) ?? [])
    let top = max(5, b.rows.map { abs($0.diff) }.max() ?? 5)
    VStack(alignment: .leading, spacing: 10) {
      CardHead(title: tr("行为影响 · 次日恢复", "Behaviour · next-day recovery"), badge: c["days"]?.int.map { tr("\($0) 天", "\($0) days") })
      if b.rows.isEmpty && b.lacking == 0 {
        Text(tr("暂无数据", "No data yet")).font(Typo.caption).foregroundColor(Palette.text2)
      }
      ForEach(b.rows) { r in
        VStack(alignment: .leading, spacing: 5) {
          HStack {
            Text(r.label).font(Typo.caption).foregroundColor(Palette.text).lineLimit(1)
            Spacer(minLength: 4)
            Text(TrendsFormat.signed(r.diff, digits: 0) + (r.reliable ? "" : " ?")).font(Typo.bodyStrong.monospacedDigit())
              .foregroundColor(r.tone == .text ? Palette.text2 : r.tone.color)
          }
          DiffBar(fraction: r.diff / top, tone: r.tone)
        }
      }
      if b.lacking > 0 { ToneBadge(text: tr("\(b.lacking) 项样本不足", "\(b.lacking) items with too few days")) }
    }
  }
}

// MARK: - data status

struct DataStatusJarvisCard: View {
  let c: JSONValue

  /// Never first, then stale, then fine (ai.js dataStatusBody).
  static func sorted(_ c: JSONValue) -> [JSONValue] {
    let rank = ["never": 0, "stale": 1, "ok": 2]
    return (c["types"]?.array ?? []).enumerated().sorted {
      let a = rank[$0.element["status"]?.string ?? ""] ?? 3, b = rank[$1.element["status"]?.string ?? ""] ?? 3
      return a != b ? a < b : $0.offset < $1.offset
    }
    .map(\.element)
  }

  static func state(_ s: String?) -> (String, Tone) {
    switch s {
    case "ok": return (tr("正常", "OK"), .good)
    case "stale": return (tr("过期", "Stale"), .mid)
    case "never": return (tr("没收到", "Never"), .text)
    default: return ("—", .text)
    }
  }

  var body: some View {
    VStack(alignment: .leading, spacing: 8) {
      let days = c["window_days"]?.int ?? 7
      CardHead(title: JarvisCards.title("data_status"), badge: tr("近 \(days) 天", "Last \(days) days"))
      FlowRow(spacing: 6) {
        ForEach(["ok", "stale", "never"], id: \.self) { k in
          let s = Self.state(k)
          ToneBadge(text: "\(s.0) \(c["summary"]?[k]?.int ?? 0)", tone: s.1)
        }
      }
      ForEach(Array(Self.sorted(c).prefix(10).enumerated()), id: \.offset) { _, t in
        HStack(spacing: 6) {
          Text(t["label"]?.string ?? "").font(Typo.body).foregroundColor(Palette.text).lineLimit(1)
          Spacer(minLength: 4)
          if let d = JarvisCards.mmdd(t["last_date"]?.string) { Text(d).font(Typo.label.monospacedDigit()).foregroundColor(Palette.text3) }
          let s = Self.state(t["status"]?.string)
          ToneBadge(text: s.0, tone: s.1)
        }
      }
    }
  }
}
