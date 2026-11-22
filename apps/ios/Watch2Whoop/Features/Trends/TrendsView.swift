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
  case calendar

  var id: String {
    switch self {
    case .explain(let k, _, _): return "explain:\(k)"
    case .status(let t): return "status:\(t.key)"
    case .calendar: return "calendar"
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
  @Namespace private var zoomSpace
  @StateObject private var zoom = ZoomTrail()
  @SceneStorage("w2w.trends.mode") private var mode: Mode = .metrics

  init(session: AccountSession) {
    _store = StateObject(wrappedValue: TrendsStore(session: session))
  }

  var body: some View {
    NavigationStack(path: $path) {
      page
        .toolbar(.hidden, for: .navigationBar)
        .navigationDestination(for: LevelRoute.self) { r in level(r).zoomLevel(r) }
    }
    .zoomScope(zoomSpace, zoom)
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
      if LevelNav.isSwipeUp(v.translation.width, v.translation.height) { openLevel(.all(mode), "glance.bars") }
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
      BodyAllPage(store: store, mode: $mode, push: push, up: { path = [] }, sheet: { sheet = $0 })
    case .metric(let id):
      MetricPage(id: id, end: store.end, range: store.range, crumb: .range(BodyGlancePage.rangeTitle(store.range)), today: nil,
                 push: push, up: { path = LevelNav.up(path, second: .all(mode)) },
                 explain: { key, title in sheet = .explain(key: key, title: title) })
    case .night(let d):
      SleepDetailView(initialDate: d) { key, title in sheet = .explain(key: key, title: title) }
    case .day:
      EmptyView()   // 今天's own level
    }
  }

  private func openLevel(_ r: LevelRoute, _ door: String?) {
    guard path.isEmpty else { return }
    if let door { zoom.open(door, to: r) }
    path = [r]
  }

  /// The next level, grown out of `door` (nil: an ordinary push).
  private func push(_ r: LevelRoute, _ door: String?) {
    if let door { zoom.open(door, to: r) }
    path.append(r)
  }

  // MARK: - the first glance's taps

  private var glanceActions: BodyGlanceActions {
    BodyGlanceActions(
      setRange: { store.setRange($0) },
      pickEnd: { sheet = .calendar },
      backToToday: { store.select(nil) },
      level: { r, door in
        if case .all(let m) = r { mode = m }
        openLevel(r, door)
      },
      all: { openLevel(.all(mode), "glance.bars") },
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

  var body: some View {
    SheetContainer(title: title, close: close) { content }
      .presentationDetents(sheet.id == "calendar" ? [.large] : [.medium, .large])
      .presentationDragIndicator(.visible)
  }

  private var title: String {
    switch sheet {
    case .explain(_, let t, _): return t
    case .status(let t): return t.label
    case .calendar: return tr("选择结束日期", "Pick the end date")
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
    case .calendar:
      MonthPicker(selected: store.end, userToday: store.userToday) { day in
        store.select(day)
        close()
      }
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
/// from the first glance), `SIMCTL_CHILD_W2W_DEV_TRENDS_SHEET=explain:<key>|status:<key>|calendar`.
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
      else if s.hasPrefix("explain:") { sheet(.explain(key: String(s.dropFirst(8)), title: String(s.dropFirst(8)))) }
      else if s.hasPrefix("status:"), let t = page()?.status.tiles.first(where: { $0.key == String(s.dropFirst(7)) }) { sheet(.status(t)) }
    }
  }
}
#endif
