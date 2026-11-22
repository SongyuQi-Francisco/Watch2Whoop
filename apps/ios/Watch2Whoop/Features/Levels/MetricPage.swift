import SwiftUI

/// What a metric page's header carries as its way up: the small recovery dial (今天) or the range (身体).
enum MetricCrumb: Equatable {
  case dial(Double?, Tone)
  case range(String)
}

/// Level 3: one metric (docs/design/2.0.1/drilldown `c-metric`). The day's value against the usual, the range switch
/// (7 / 30 / 90 / 1 year), the chart (daily line on the usual band with the 7-day mean, or the range card's own chart),
/// a finger read-out, the range's four figures, the related metrics (each a door to its own page) and, at the bottom of
/// the same page, the explanation. Recovery, sleep, strain, stress and body age keep their own detail on top.
///
/// Reached by tapping that number anywhere: a first glance, 今日详情, 身体's list, the related rows of another metric.
///
/// Someone else's metric page (`member`): the same page from their documents in memory (`TrendsStore(member:)`); the heads
/// that read my own tables (body age, the stress hours) give way to the plain head.
struct MetricPage: View {
  let id: String
  /// The day the page describes (nil = the user's today).
  let end: String?
  let crumb: MetricCrumb
  /// The 今天 tab's day: the scores' own numbers (so this page never contradicts the dial) and the strain head.
  let today: TodayModel?
  /// The next level and the door it grows out of (`.zoomDoor`).
  let push: (LevelRoute, String?) -> Void
  let up: () -> Void
  let explain: (String, String) -> Void
  /// The training state (TSB) row of the strain head: the load sheet (CTL, ATL, TSB).
  var load: (() -> Void)?
  /// The sleep head's 入睡目标 (my own 今天 only).
  var bedtime: (() -> Void)?
  /// Someone else's page (read-only, nothing on the phone).
  let member: MemberViewStore?
  @EnvironmentObject private var session: AccountSession
  @Environment(\.dismiss) private var dismiss
  @Environment(\.zoomArrival) private var zoomArrival
  /// Grown out of its door: the chart and everything under the range switch wait until the zoom lands (`ZoomTransition.settle`).
  @State private var settled = false
  @StateObject private var dayStore: TrendsStore
  @StateObject private var rangeStore: TrendsStore
  @State private var selection = ChartSelection()

  init(id: String, end: String?, range: TrendRange, crumb: MetricCrumb, today: TodayModel?,
       push: @escaping (LevelRoute, String?) -> Void, up: @escaping () -> Void, explain: @escaping (String, String) -> Void,
       load: (() -> Void)? = nil, bedtime: (() -> Void)? = nil, member: MemberViewStore? = nil) {
    self.id = id
    self.end = end
    self.crumb = crumb
    self.today = today
    self.push = push
    self.up = up
    self.explain = explain
    self.load = load
    self.bedtime = bedtime
    self.member = member
    // the day (its value, the usual) reads the 61-day document the Today tab already keeps; the chart its own range
    let r = LevelParams.metricRanges.contains(range) ? range : LevelParams.metricDefaultRange
    if let member {
      _dayStore = StateObject(wrappedValue: TrendsStore(member: member, range: .today, end: end))
      _rangeStore = StateObject(wrappedValue: TrendsStore(member: member, range: r, end: end))
    } else {
      _dayStore = StateObject(wrappedValue: TrendsStore(range: .today, end: end, remember: false))
      _rangeStore = StateObject(wrappedValue: TrendsStore(range: r, end: end, remember: false))
    }
  }

  private var title: String { MetricCatalog.title(id) }
  private var overrides: [String: Double] { today?.levelOverrides ?? [:] }
  private var headModel: MetricHeadModel? {
    MetricHeadModel.of(id, day: dayStore.page?.data, override: MetricCatalog.series(id).flatMap { overrides[$0] })
  }

  var body: some View {
    ScrollView(.vertical, showsIndicators: false) {
      VStack(alignment: .leading, spacing: 0) {
        header
        head.padding(.top, 8)
        SegControl(items: LevelParams.metricRanges.map { ($0, Self.rangeLabel($0)) }, selected: rangeStore.range) { r in
          selection.index = nil
          rangeStore.setRange(r)
        }
        .padding(.top, 16)
        if ready {
          Group {
            chartBlock.padding(.top, 14)
            if let p = rangeStore.page, let st = RangeStatsModel.of(id, range: p.data) { stats(st).padding(.top, 18) }
            if id == "recovery" { RecoveryContributors(series: rangeStore.page?.data.series["recovery"]).padding(.top, 22) }
            related
            ExplainBody(kind: "metric", key: MetricCatalog.explain(id), day: end, head: false).padding(.top, 8)
          }
          .transition(.opacity)
        } else {
          Color.clear.frame(height: LevelParams.metricChartHeight + 40)
        }
        Color.clear.frame(height: 24)
      }
      .padding(.horizontal, Metrics.gutter)
      .padding(.bottom, 32)
      .frame(maxWidth: Metrics.maxContentWidth)
      .frame(maxWidth: .infinity)
    }
    .background(Palette.bg.ignoresSafeArea())
    .statusBarCover()
    .clearOfJarvisKey()
    .toolbar(.hidden, for: .navigationBar)
    .onAppear {
      // someone else's stores have no database to observe (`isObserving` is true for them)
      if !dayStore.isObserving { dayStore.observe(session) }
      if !rangeStore.isObserving { rangeStore.observe(session) }
    }
    .task {
      if zoomArrival && !settled && ZoomTransition.settle > 0 {
        try? await Task.sleep(nanoseconds: UInt64(ZoomTransition.settle * 1_000_000_000))
        withAnimation(.easeOut(duration: 0.2)) { settled = true }
      }
      // the range first: the day's 61-day document is usually cached (its first value arrives meanwhile, off the main thread)
      await rangeStore.refresh()
      if dayStore.page == nil { await dayStore.refresh() }
    }
  }

  /// Everything under the range switch is drawn: at once, or once the zoom it grew out of has landed.
  private var ready: Bool { settled || !zoomArrival || ZoomTransition.settle <= 0 }

  /// 「7 天」 … 「1 年」.
  static func rangeLabel(_ r: TrendRange) -> String { r == .year ? tr("1 年", "1 y") : r.label }

  // MARK: header: back · the way up · › title · 非诊断 · ladder (3 of 3)

  private var header: some View {
    LevelHeader(level: 3, back: { dismiss() }, up: up, upLabel: upLabel) {
      switch crumb {
      case .dial(let v, let t): MiniDial(value: v, tone: t)
      case .range(let s): Text(s).font(Typo.header).foregroundColor(Palette.text2).lineLimit(1)
      }
    } title: {
      HStack(spacing: 4) {
        Icon(.chevronRight, size: 14).foregroundColor(Palette.text3).accessibilityHidden(true)
        Text(title).font(Typo.header).foregroundColor(Palette.text).lineLimit(1).minimumScaleFactor(0.6)
          .accessibilityAddTraits(.isHeader)
      }
    } trailing: {
      DisclaimerTag(kind: .medical)
    }
  }

  private var upLabel: String {
    switch crumb {
    case .dial: return tr("今日详情", "Today's details")
    case .range: return tr("全部趋势", "All trends")
    }
  }

  // MARK: head: the day against the usual, or the score's own detail

  @ViewBuilder private var head: some View {
    if !ready {
      plainHead   // while zooming in: the number only; the score's own detail (charts, the night) once it has landed
    } else {
      fullHead
    }
  }

  @ViewBuilder private var fullHead: some View {
    switch MetricCatalog.head(id) {
    case .strain where today != nil:
      StrainDayHead(model: today!, open: openRow)
    case .bodyAge where member == nil:
      // its own tiles (body age, against the calendar, pace) are the head; they read my own tables
      BodyAgeView(explains: false).padding(.top, 10)
    default:
      plainHead
      if MetricCatalog.head(id) == .sleep {
        SleepNightHead(session: session, date: today?.sleepDate ?? end, explain: explain,
                       night: { push(.night($0), "metric.\(id).night") }, bedtime: bedtime, nightDoor: "metric.\(id).night")
          .padding(.top, 6)
      }
      // the stress hours read my own tables (no such path for someone else)
      if MetricCatalog.head(id) == .stress, member == nil, let day = end ?? dayStore.page?.data.end ?? today?.date {
        StressDetailView(day: day, past: end, explains: false).padding(.top, 10)
      }
    }
  }

  /// The day's value against the usual; without a reading that day, the range card's headline with what it is (「30 天均」).
  private var plainHead: some View {
    let h = headModel.flatMap { $0.value == nil ? nil : $0 }
    let card = h != nil ? nil : rangeStore.page.flatMap { p in TrendCards.groups(p).flatMap(\.cards).first { $0.id == MetricCatalog.card(id) } }
    let value = h?.value ?? card?.value
    return HStack(alignment: .firstTextBaseline, spacing: 8) {
      Text(value ?? "—").font(.system(size: 56, weight: .ultraLight).monospacedDigit()).tracking(-1.5)
        .foregroundColor(value == nil ? Palette.text2 : Palette.text).lineLimit(1).minimumScaleFactor(0.6)
      if let u = h?.unit ?? card?.unit, value != nil {
        Text(u).font(.system(size: 15, weight: .medium)).foregroundColor(Palette.text2).padding(.leading, -4).lineLimit(1)
      }
      if let h, let d = h.delta {
        Text(d).font(.system(size: 17, weight: .semibold).monospacedDigit())
          .foregroundColor(h.deltaTone == .text ? Palette.text : h.deltaTone.color).padding(.leading, 8)
        Text(tr("比常态", "vs usual")).font(Typo.label).foregroundColor(Palette.text2)
      } else if let b = card?.basis, card?.value != nil {
        Text(b).font(Typo.label).foregroundColor(Palette.text2).padding(.leading, 8)
      }
      Spacer(minLength: 0)
    }
    .frame(minHeight: 60)
    .accessibilityElement(children: .combine)
  }

  private func openRow(_ r: TodayGlance.Row) {
    if r.id == "tsb", let load { load() }
    else if TrendDay.conf(r.id) != nil { push(.metric(r.id), "metric.\(id).head.\(r.id)") }
    else if let e = r.explain { explain(e.key, e.title) }
  }

  // MARK: chart

  private struct ChartModel {
    var spec: ChartSpec?
    var tip: (Int) -> String
    var card: TrendCardModel?
    var line: Bool
    var empty: String?
  }

  private func chartModel(_ p: TrendsPage, head h: MetricHeadModel?) -> ChartModel {
    let d = p.data
    let card = TrendCards.groups(p).flatMap(\.cards).first { $0.id == MetricCatalog.card(id) }
    if MetricCatalog.drawsLine(id), let k = MetricCatalog.series(id), let s = d.series[k], s.n > 0, let cf = TrendCards.conf[k] {
      let unit = cf.unit.isEmpty ? s.unit : cf.unit
      let spec = ChartSpec(id: "metric.\(id).\(d.days)", dates: d.dates,
                           layers: [ChartLayer(kind: .line, values: s.values, tone: .text, faint: true, main: true),
                                    ChartLayer(kind: .avg, values: s.avg7)],
                           min: cf.min, max: cf.max, height: LevelParams.metricChartHeight, label: cf.label,
                           usual: h?.usual, lastTone: h?.deltaTone ?? .text)
      return ChartModel(spec: spec, tip: TrendCards.tipDay(d, s.values, digits: cf.digits, unit: unit), card: card, line: true)
    }
    var spec = card?.spec
    spec?.height = LevelParams.metricChartHeight
    return ChartModel(spec: spec, tip: card?.tip ?? { _ in "" }, card: card, line: false,
                      empty: card == nil ? TrendCards.noData(d) : card?.empty)
  }

  @ViewBuilder private var chartBlock: some View {
    if let p = rangeStore.page {
      let h = headModel
      let c = chartModel(p, head: h)
      HStack(alignment: .center, spacing: 14) {
        if c.line {
          if let u = h?.usualText { usualSwatch(u) }
          HStack(spacing: 5) {
            RoundedRectangle(cornerRadius: 1).fill(Palette.text).frame(width: 14, height: 2)
            Text(tr("7 天均", "7-day mean")).font(.system(size: 12, weight: .medium)).foregroundColor(Palette.text2)
          }
        } else if let l = c.card?.legend, !l.isEmpty {
          TrendLegend(items: l)
        }
        Spacer(minLength: 4)
        TrendReadOut(base: nil, tip: c.tip, selection: selection)
      }
      .frame(minHeight: 16)
      if let spec = c.spec {
        TrendChartBody(spec: spec, selection: selection).equatable().padding(.top, 6)
      } else if let e = c.empty {
        Text(e).font(Typo.caption).foregroundColor(Palette.text3).frame(maxWidth: .infinity, minHeight: 80)
      }
      if let x = c.card?.extra { TrendCardExtra(extra: x).padding(.top, 10) }
    } else if rangeStore.loading {
      ProgressView().tint(Palette.text3).frame(maxWidth: .infinity, minHeight: LevelParams.metricChartHeight)
    } else {
      Text(rangeStore.offline ? tr("需要联网", "Needs a connection") : tr("暂无", "Nothing yet")).font(Typo.body)
        .foregroundColor(Palette.text3).frame(maxWidth: .infinity, minHeight: LevelParams.metricChartHeight)
    }
  }

  private func usualSwatch(_ text: String) -> some View {
    HStack(spacing: 6) {
      RoundedRectangle(cornerRadius: 1).fill(Palette.band)
        .overlay(RoundedRectangle(cornerRadius: 1).stroke(Palette.bandLine, style: StrokeStyle(lineWidth: 1, dash: [2, 2])))
        .frame(width: 14, height: 8)
      Text("\(tr("常态", "Usual")) \(text)").font(.system(size: 12, weight: .medium).monospacedDigit()).foregroundColor(Palette.text2)
        .lineLimit(1)
    }
  }

  // MARK: the range's figures

  private func stats(_ s: RangeStatsModel) -> some View {
    HStack(alignment: .top, spacing: 0) {
      figure(s.mean, unit: s.unit, label: tr("均值", "Mean"))
      figure(s.high, label: tr("最高", "High"))
      figure(s.low, label: tr("最低", "Low"))
      figure(s.vsPrev, label: tr("比上期", "vs before"), tone: s.vsTone)
    }
  }

  private func figure(_ v: String?, unit: String? = nil, label: String, tone: Tone = .text) -> some View {
    VStack(alignment: .leading, spacing: 6) {
      HStack(alignment: .firstTextBaseline, spacing: 2) {
        Text(v ?? "—").font(Typo.row).foregroundColor(v == nil ? Palette.text3 : (tone == .text ? Palette.text : tone.color))
          .lineLimit(1).minimumScaleFactor(0.7)
        if let unit, v != nil { Text(unit).font(Typo.unit).foregroundColor(Palette.text2).lineLimit(1) }
      }
      Text(label).font(.system(size: 12, weight: .medium)).foregroundColor(Palette.text2)
    }
    .frame(maxWidth: .infinity, alignment: .leading)
    .accessibilityElement(children: .combine)
  }

  // MARK: related metrics, each a door to its own page

  @ViewBuilder private var related: some View {
    if let d = dayStore.page?.data {
      // someone else's: only the related metrics their document carries (the others are not shared)
      let rows = MetricCatalog.related(id).filter { member == nil || d.series[$0] != nil || overrides[$0] != nil }
        .compactMap { LevelRows.dayRow($0, d, override: overrides[$0]) }
      if !rows.isEmpty {
        LevelGroupHeader(icon: .link, title: tr("相关", "Related"), count: nil).padding(.top, 22)
        LevelRowList(rows: rows, door: "metric.\(id).related") { push(.metric($0.id), "metric.\(id).related.\($0.id)") }
      }
    }
  }
}

/// What a range card draws under its chart (recovery's zone days, heart-rate zones, the regularity bars).
struct TrendCardExtra: View {
  let extra: TrendCardModel.Extra

  var body: some View {
    switch extra {
    case .none: EmptyView()
    case .zones(let z): ZoneCounts(zones: z)
    case .hrZones(let z): HRZonesView(zones: z)
    case .regularity(let r): RegularityBars(nights: r.nights).frame(height: 64)
    }
  }
}
