import SwiftUI

/// A range card's two numbers (#357): the range mean its headline shows and the end day's own value, side by side in
/// the explain sheet, so a 7-day mean of 12 and a day of 31 never look contradictory.
struct RangeFigures: Equatable {
  var meanLabel: String
  var mean: String?
  var dayLabel: String
  var day: String?
  var unit: String?
}

/// Sheets of the Trends tab (one at a time).
enum TrendsSheet: Identifiable {
  case explain(key: String, title: String, figures: RangeFigures? = nil)
  case status(TrendStatus.Tile)
  case recovery, regularity, calendar, bodyAge

  var id: String {
    switch self {
    case .explain(let k, _, _): return "explain:\(k)"
    case .status(let t): return "status:\(t.key)"
    case .recovery: return "recovery"
    case .regularity: return "regularity"
    case .calendar: return "calendar"
    case .bodyAge: return "bodyAge"
    }
  }
}

/// The 身体 tab, 2.1.0 (docs/design/2.0.1 `05-body`, drilldown option C′): level 1 is the first glance only (range menu, the
/// score over the range, 心肺 · 身体年龄 · 周报) and nothing below it. The bars (or the ladder, or a swipe up) open level 2,
/// `BodyAllPage`: 指标 · 睡眠 · 报告, the 2.0.0 lists of the tab. A number opens level 3, its metric page. Read from the
/// local database first, refreshed through the sync layer; the end date looks back. Numbers and charts only.
struct TrendsView: View {
  enum Mode: String, CaseIterable { case metrics, sleep, report
    var label: String {
      switch self {
      case .metrics: return tr("指标", "Metrics")
      case .sleep: return tr("睡眠", "Sleep")
      case .report: return tr("报告", "Report")
      }
    }
  }

  @EnvironmentObject private var session: AccountSession
  @EnvironmentObject private var app: AppModel
  @StateObject private var store: TrendsStore
  @State private var sheet: TrendsSheet?
  @State private var path: [LevelRoute] = []
  @SceneStorage("w2w.trends.mode") private var mode: Mode = .metrics

  init(session: AccountSession) {
    _store = StateObject(wrappedValue: TrendsStore(session: session))
  }

  var body: some View {
    NavigationStack(path: $path) {
      page
        .toolbar(.hidden, for: .navigationBar)
        .navigationDestination(for: LevelRoute.self) { r in level(r) }
    }
    .sheet(item: $sheet) { s in
      TrendsSheetView(sheet: s, page: store.page, end: store.end, store: store, close: { sheet = nil })
        .environmentObject(session)
    }
    .onAppear { if !store.isObserving { store.observe(session) } }
    .task { await store.refresh() }
    // a Jarvis card or a row of 全部功能 (also when this tab is created by that tap: the publisher replays the request)
    .onReceive(TrendsLink.shared.$pending) { r in
      guard r != nil else { return }
      DispatchQueue.main.async {
        guard let r = TrendsLink.shared.take() else { return }
        mode = r.mode
        path = [.all(r.mode)]
        store.apply(r)
      }
    }
  }

  /// Level 1: the first glance and nothing below it (no false floor: drilldown README section 9).
  private var page: some View {
    ScrollView(.vertical, showsIndicators: false) {
      BodyGlancePage(page: store.page, range: store.range, end: store.end, loading: store.loading, session: session,
                     run: glanceActions)
        .padding(.horizontal, Metrics.gutter)
        .frame(maxWidth: Metrics.maxContentWidth)
        .frame(maxWidth: .infinity)
    }
    .background(Palette.bg.ignoresSafeArea())
    .statusBarCover()
    .clearOfJarvisKey()
    .refreshable { await store.refresh() }
    .simultaneousGesture(DragGesture(minimumDistance: 40).onEnded { v in
      if LevelNav.isSwipeUp(v.translation.width, v.translation.height) { openLevel(.all(mode)) }
    })
    .onAppear {
      if PerfProbe.trendsScroll { autoLevel() }
      #if DEBUG
      TrendsDevShot.apply(path: { path = $0 }, mode: { mode = $0 }, range: { store.setRange($0) }, end: { store.select($0) },
                          sheet: { sheet = $0 }, page: { store.page })
      #endif
    }
  }

  // MARK: - levels 2 and 3

  @ViewBuilder private func level(_ r: LevelRoute) -> some View {
    switch r {
    case .all:
      BodyAllPage(store: store, mode: $mode, push: { path.append($0) }, up: { path = [] }, sheet: { sheet = $0 })
    case .metric(let id):
      MetricPage(id: id, end: store.end, range: store.range, crumb: .range(BodyGlancePage.rangeTitle(store.range)), today: nil,
                 push: { path.append($0) }, up: { path = LevelNav.up(path, second: .all(mode)) },
                 explain: { key, title in sheet = .explain(key: key, title: title) })
    case .night(let d):
      SleepDetailView(initialDate: d) { key, title in sheet = .explain(key: key, title: title) }
    case .day:
      EmptyView()   // 今天's own level
    }
  }

  private func openLevel(_ r: LevelRoute) {
    guard path.isEmpty else { return }
    path = [r]
  }

  // MARK: - the first glance's taps

  private var glanceActions: BodyGlanceActions {
    BodyGlanceActions(
      setRange: { store.setRange($0) },
      pickEnd: { sheet = .calendar },
      backToToday: { store.select(nil) },
      level: { r in
        if case .all(let m) = r { mode = m }
        openLevel(r)
      },
      all: { openLevel(.all(mode)) },
      syncNow: { Task { _ = await HealthSyncService.shared.syncNow(); await store.refresh() } },
      syncCenter: { app.route = .syncCenter })
  }

  /// `W2W_PERF=1 W2W_PERF_SCROLL_TRENDS=1`: open the level-2 list once the range's data is on screen (frame meter counts).
  private func autoLevel() {
    Task { @MainActor in
      for _ in 0..<40 where store.page == nil || store.loading { try? await Task.sleep(nanoseconds: 250_000_000) }
      try? await Task.sleep(nanoseconds: 1_500_000_000)
      PerfProbe.shared.startFrames()
      path = [.all(.metrics)]
      try? await Task.sleep(nanoseconds: 2_200_000_000)
      PerfProbe.shared.stopFrames(label: "trends_level2_\(store.range.rawValue)d")
    }
  }
}

/// 指标 of the Trends tab: the range control, then 今天's list or the range's cards. The tab and the read-only view of someone
/// else (`MemberTrendsPage`) draw the same thing from a `TrendsPage`.
///
/// Equatable without the closures (2.0.1, as TodayDayCards): opening a sheet or the sleep page no longer builds every
/// chart of the range again first.
struct TrendsMetricsContent: View, Equatable {
  static func == (a: TrendsMetricsContent, b: TrendsMetricsContent) -> Bool {
    a.range == b.range && a.loading == b.loading && a.offline == b.offline && a.foldOn == b.foldOn && a.page == b.page
  }

  let page: TrendsPage?
  let range: TrendRange
  let loading: Bool
  let offline: Bool
  let foldOn: Bool
  let setRange: (TrendRange) -> Void
  let open: (TrendsSheet) -> Void
  @AppStorage(TrendsFold.storageKey) private var foldedRaw = ""

  // no container here: inside the page's LazyVStack each card stays its own lazy row
  var body: some View {
    SegControl(items: TrendRange.allCases.map { ($0, $0.label) }, selected: range) { setRange($0) }
      .padding(.top, 8)
    if let p = page, range == .today {
      dayList(p)
    } else if let p = page {
      let d = p.data
      let cur = Text("\(TrendsFormat.day(d.start, year: d.withYear)) – \(TrendsFormat.day(d.end, year: d.withYear))")
        .font(Typo.bodyStrong.monospacedDigit()).foregroundColor(Palette.text)
      let prev = Text("vs \(TrendsFormat.day(d.prevStart, year: d.withYear)) – \(TrendsFormat.day(LocalDay.add(d.start, -1), year: d.withYear))")
        .font(Typo.caption.monospacedDigit()).foregroundColor(Palette.text2)
      Group {
        if d.withYear { VStack(alignment: .leading, spacing: 2) { cur; prev } } else { HStack(spacing: 4) { cur; prev } }
      }
      .lineLimit(1).minimumScaleFactor(0.8)
      .padding(.top, 14)
      ForEach(TrendCards.groups(p), id: \.id) { g in
        groupHeader(g.id, g.title, count: g.cards.count, page: p)
        if !isFolded(g.id) {
          ForEach(g.cards) { c in
            TrendCardView(card: c) { open($0) }.padding(.bottom, 8).id("card.\(c.id)")
          }
        }
      }
      if !p.status.tiles.isEmpty { statusBlock(p) }
    } else if loading {
      ProgressView().tint(Palette.text3).frame(maxWidth: .infinity).padding(.vertical, 60)
    } else {
      Text(offline ? tr("需要联网", "Needs a connection") : tr("暂无", "Nothing yet")).font(Typo.body).foregroundColor(Palette.text3)
        .frame(maxWidth: .infinity).padding(.vertical, 60)
    }
  }

  /// 「今天」 (#357): the end day, its status cards first (they are "today" by nature), then every metric as one row.
  @ViewBuilder private func dayList(_ p: TrendsPage) -> some View {
    // the day itself is in the header's overline (and next to the status cards)
    let d = p.data
    if !p.status.tiles.isEmpty { statusBlock(p) }
    ForEach(TrendDay.groups(d), id: \.id) { g in
      groupHeader(g.id, g.title, count: g.rows.count, page: p)
      if !isFolded(g.id) {
        TrendDayList(rows: g.rows) { r in open(r.id == "body_age" ? .bodyAge : .explain(key: r.explain, title: r.label)) }
          .id("day.\(g.id)")
      }
    }
  }

  // MARK: - foldable categories (flag trends_v2)

  private func isFolded(_ group: String) -> Bool { foldOn && TrendsFold.contains(foldedRaw, group) }

  @ViewBuilder private func groupHeader(_ id: String, _ title: String, count: Int, page: TrendsPage) -> some View {
    if foldOn {
      TrendGroupHeader(title: title, count: count, summary: TrendsFold.summary(id, page.status), folded: isFolded(id)) {
        foldedRaw = TrendsFold.toggled(foldedRaw, id)
      }
      .padding(.top, 14).padding(.bottom, isFolded(id) ? 0 : 6)
    } else {
      SectionHeader(title: title).padding(.top, 22).padding(.bottom, 10)
    }
  }

  /// Status cards describe the end date and do not change with the range, so they come after the charts.
  private func statusBlock(_ p: TrendsPage) -> some View {
    VStack(alignment: .leading, spacing: 10) {
      HStack {
        SectionHeader(title: tr("当前状态", "Current status"))
        if p.localCount > 0 {
          HStack(spacing: 4) {
            Circle().fill(Palette.text3).frame(width: 4, height: 4)
            Text(tr("本机计算", "On this phone")).font(Typo.label).foregroundColor(Palette.text3)
          }
        }
        Text(TodayModel.shortDay(p.data.end)).font(Typo.label.monospacedDigit()).foregroundColor(Palette.text3)
      }
      LazyVGrid(columns: [GridItem(.flexible(), spacing: 8), GridItem(.flexible(), spacing: 8)], spacing: 8) {
        ForEach(p.status.tiles) { t in StatusTileView(tile: t) { open(.status(t)) } }
      }
    }
    .padding(.top, 22)
    .id("status")
  }
}

extension PerfProbe {
  static let trendsScroll = ProcessInfo.processInfo.environment["W2W_PERF_SCROLL_TRENDS"] == "1"
}

/// The bodies of the Trends sheets.
struct TrendsSheetView: View {
  let sheet: TrendsSheet
  let page: TrendsPage?
  let end: String?
  @ObservedObject var store: TrendsStore
  let close: () -> Void
  @Environment(\.memberView) private var member

  var body: some View {
    SheetContainer(title: title, close: close) { content }
      .presentationDetents(sheet.id == "calendar" || sheet.id == "bodyAge" ? [.large] : [.medium, .large])
      .presentationDragIndicator(.visible)
  }

  private var title: String {
    switch sheet {
    case .explain(_, let t, _): return t
    case .status(let t): return t.label
    case .recovery: return tr("恢复贡献项", "Recovery contributors")
    case .regularity: return tr("睡眠规律性", "Sleep regularity")
    case .calendar: return tr("选择结束日期", "Pick the end date")
    case .bodyAge: return tr("身体年龄", "Body age")
    }
  }

  @ViewBuilder private var content: some View {
    switch sheet {
    case .explain(let key, _, let figures):
      if let f = figures {
        // a range card: its mean and the end day's value, labelled, instead of the end day's value alone
        VStack(alignment: .leading, spacing: 0) {
          KVRow(label: f.meanLabel, value: f.mean, unit: f.unit)
          KVRow(label: f.dayLabel, value: f.day, unit: f.unit)
          ExplainBody(kind: "metric", key: key, day: end, head: false).padding(.top, 8)
        }
      } else {
        ExplainBody(kind: "metric", key: key, day: end)
      }
      if TrendStatus.medicalKeys.contains(key) { DisclaimerTag(kind: .medical).padding(.top, 10) }
    case .status(let t):
      StatusTileBody(tile: t, day: end)
    case .recovery:
      recovery
    case .regularity:
      VStack(alignment: .leading, spacing: 0) {
        KVRow(label: tr("近 7 天", "Last 7 days"), value: page?.regularity?.pct7.map { "\(TodayModel.fmt($0))%" })
        KVRow(label: tr("近 30 天", "Last 30 days"), value: page?.regularity?.pct30.map { "\(TodayModel.fmt($0))%" })
        ExplainBody(kind: "metric", key: "sleep_consistency", day: end, head: false).padding(.top, 8)
      }
    case .calendar:
      MonthPicker(selected: store.end, userToday: store.userToday) { day in
        store.select(day)
        close()
      }
    case .bodyAge:
      if member == nil { BodyAgeView() }   // it reads my own tables
    }
  }

  /// The range mean, the end day and the previous period, then each contributor's z-score (app.js recoveryTrendHtml sheet).
  @ViewBuilder private var recovery: some View {
    let s = page?.data.series["recovery"]
    let fig = page.flatMap { p in s.map { TrendCards.figures($0, p.data, digits: 0, unit: nil) } }
    VStack(alignment: .leading, spacing: 0) {
      KVRow(label: fig?.meanLabel ?? tr("区间均值", "Range mean"), value: s?.mean.map { TodayModel.fmt($0) })
      if let fig { KVRow(label: fig.dayLabel, value: fig.day) }
      KVRow(label: tr("上期", "Previous"), value: s?.prevMean.map { TodayModel.fmt($0) })
      RecoveryContributors(series: s).padding(.top, 16)
      ExplainBody(kind: "metric", key: "recovery", day: end, head: false).padding(.top, 8)
    }
  }
}

/// Body battery through the day (estimate).
struct BatteryChart: View {
  let points: [TrendStatus.BatteryPoint]

  var body: some View {
    let first = points.first?.hour ?? 0, last = points.last?.hour ?? 24
    let spec = ChartSpec(id: "battery", dates: points.map { "\(Int($0.hour)):00" },
                         layers: [ChartLayer(kind: .avg, values: points.map(\.level), tone: .good, main: true)], min: 0, max: 100,
                         height: 90, label: tr("身体电量估算", "Body battery estimate"), xAxis: false)
    VStack(spacing: 2) {
      TrendChartBody(spec: spec, selection: ChartSelection()).equatable()
      HStack {
        Text("\(Int(first)):00"); Spacer(); Text("\(Int(last)):00")
      }
      .font(.system(size: 10).monospacedDigit()).foregroundColor(Palette.text3)
    }
  }
}

/// A status card's sheet (Trends' 当前状态 and Today's 今日核心 / 需要注意): its rows, the battery curve or load focus,
/// then the explanation. `day`: the end date (nil = today).
struct StatusTileBody: View {
  let tile: TrendStatus.Tile
  let day: String?

  var body: some View {
    VStack(alignment: .leading, spacing: 0) {
      if !tile.points.isEmpty { BatteryChart(points: tile.points).frame(height: 90).padding(.vertical, 8) }
      ForEach(Array(tile.rows.enumerated()), id: \.offset) { _, r in KVRow(label: r.label, value: r.value) }
      if !tile.focus.isEmpty { focus(tile.focus) }
      HStack(spacing: 6) {
        if tile.sub == tr("估算", "Estimate") { ToneBadge(text: tr("估算", "Estimate")) }
        if tile.local { ToneBadge(text: tr("本机计算", "On this phone")) }
        if TrendStatus.medicalKeys.contains(tile.key) { DisclaimerTag(kind: .medical) }
      }
      .padding(.top, 10)
      ExplainBody(kind: "metric", key: tile.key, day: day, head: false).padding(.top, 8)
    }
  }

  private func focus(_ groups: [(label: String, pct: Double)]) -> some View {
    VStack(alignment: .leading, spacing: 6) {
      Text(tr("负荷重点 · 28 天", "Load focus · 28 days")).font(Typo.section).foregroundColor(Palette.text2).padding(.top, 14)
      GeometryReader { g in
        HStack(spacing: 2) {
          ForEach(Array(groups.enumerated()), id: \.offset) { i, x in
            Capsule().fill([Palette.good, Palette.mid, Palette.bad][min(2, i)]).frame(width: max(2, g.size.width * x.pct / 100 - 2))
          }
        }
      }
      .frame(height: 6)
      HStack(spacing: 14) {
        ForEach(Array(groups.enumerated()), id: \.offset) { _, x in
          Text("\(TodayModel.fmt(x.pct))% \(x.label)").font(.system(size: 11).monospacedDigit()).foregroundColor(Palette.text2)
        }
      }
    }
  }
}

#if DEBUG
/// Debug builds only, for simulator screenshots: `SIMCTL_CHILD_W2W_DEV_TRENDS=<mode>[,<days>][,<end>]` (`metrics,365`,
/// `sleep`, `report`: opens level 2 in that mode), `SIMCTL_CHILD_W2W_DEV_TRENDS_LEVEL=metric:<id>` (a metric page straight
/// from the first glance), `SIMCTL_CHILD_W2W_DEV_TRENDS_SHEET=explain:<key>|status:<key>|calendar|bodyAge`.
enum TrendsDevShot {
  static func apply(path: @escaping ([LevelRoute]) -> Void, mode: @escaping (TrendsView.Mode) -> Void,
                    range: @escaping (TrendRange) -> Void, end: @escaping (String) -> Void, sheet: @escaping (TrendsSheet) -> Void,
                    page: @escaping () -> TrendsPage?) {
    let env = ProcessInfo.processInfo.environment
    var route: [LevelRoute] = []
    if let t = env["W2W_DEV_TRENDS"] {
      let p = t.split(separator: ",").map(String.init)
      if let m = p.first.flatMap(TrendsView.Mode.init(rawValue:)) {
        mode(m)
        route = [.all(m)]
      }
      if p.count > 1, let n = Int(p[1]), let r = TrendRange(rawValue: n) { range(r) }
      if p.count > 2, p[2].count == 10 { end(p[2]) }
    }
    if let l = env["W2W_DEV_TRENDS_LEVEL"], l.hasPrefix("metric:") { route.append(.metric(String(l.dropFirst(7)))) }
    DispatchQueue.main.asyncAfter(deadline: .now() + 2.0) {
      if !route.isEmpty { path(route) }
      let s = env["W2W_DEV_TRENDS_SHEET"] ?? ""
      if s == "calendar" { sheet(.calendar) }
      else if s == "bodyAge" { sheet(.bodyAge) }
      else if s.hasPrefix("explain:") { sheet(.explain(key: String(s.dropFirst(8)), title: String(s.dropFirst(8)))) }
      else if s.hasPrefix("status:"), let t = page()?.status.tiles.first(where: { $0.key == String(s.dropFirst(7)) }) { sheet(.status(t)) }
    }
  }
}
#endif
