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
  case recovery, regularity, calendar

  var id: String {
    switch self {
    case .explain(let k, _, _): return "explain:\(k)"
    case .status(let t): return "status:\(t.key)"
    case .recovery: return "recovery"
    case .regularity: return "regularity"
    case .calendar: return "calendar"
    }
  }
}

/// The Trends tab (#340 N4), like the page's: 指标 (今天 = the end day's full metric list against the personal baseline,
/// status cards first (#357); 7 / 30 / 90 / 365 days = every WHOOP metric with its range mean and "vs 上期", status cards
/// as of the end date), 睡眠 (the last 14 nights) and 报告 (weekly / monthly report). Read from the local database
/// first, refreshed through the sync layer; the end date looks back. Numbers and charts only; words in the sheets.
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
  @StateObject private var store: TrendsStore
  @State private var sheet: TrendsSheet?
  @State private var path: [SleepRoute] = []
  @SceneStorage("w2w.trends.mode") private var mode: Mode = .metrics

  init(session: AccountSession) {
    _store = StateObject(wrappedValue: TrendsStore(session: session))
  }

  var body: some View {
    NavigationStack(path: $path) {
      page
        .toolbar(.hidden, for: .navigationBar)
        .navigationDestination(for: SleepRoute.self) { r in
          SleepDetailView(initialDate: r.date) { key, title in sheet = .explain(key: key, title: title) }
        }
    }
    .sheet(item: $sheet) { s in
      TrendsSheetView(sheet: s, page: store.page, end: store.end, store: store, close: { sheet = nil })
        .environmentObject(session)
    }
    .onAppear { if !store.isObserving { store.observe(session) } }
    .task { await store.refresh() }
    // 「全部指标 →」 on Today (also when this tab is created by that tap: the publisher replays the pending request)
    .onReceive(TrendsLink.shared.$pending) { r in
      guard r != nil else { return }
      DispatchQueue.main.async {
        guard let r = TrendsLink.shared.take() else { return }
        mode = r.mode
        path = []
        store.apply(r)
      }
    }
  }

  private var page: some View {
    ScrollViewReader { proxy in
      ScrollView(.vertical, showsIndicators: false) {
        LazyVStack(alignment: .leading, spacing: 0) {
          header.id("top")
          if session.flags.contains("ui_ai_first") {
            AskJarvisBar(example: tr("这周为什么恢复低？", "why is recovery low?")).padding(.bottom, 12)
          }
          SegControl(items: Mode.allCases.map { ($0, $0.label) }, selected: mode) { mode = $0 }
          switch mode {
          case .metrics: metrics
          case .sleep: TrendsSleepView(end: store.end) { day in path.append(SleepRoute(date: day)) }.padding(.top, 14)
          case .report: ReportView(end: store.end) { key, title in sheet = .explain(key: key, title: title) }.padding(.top, 10)
          }
          Color.clear.frame(height: 1).id("bottom")
        }
        .padding(.horizontal, Metrics.gutter)
        .padding(.bottom, 32)
        .frame(maxWidth: Metrics.maxContentWidth)
        .frame(maxWidth: .infinity)
      }
      .background(Palette.bg.ignoresSafeArea())
      .refreshable { await store.refresh() }
      .onAppear {
        if PerfProbe.trendsScroll { autoScroll(proxy) }
        #if DEBUG
        TrendsDevShot.apply(proxy: proxy, mode: { mode = $0 }, range: { store.setRange($0) }, end: { store.select($0) },
                            sheet: { sheet = $0 }, page: { store.page })
        #endif
      }
    }
  }

  // MARK: - header: end date (opens the calendar) and title

  private var header: some View {
    HStack(alignment: .bottom) {
      Button { sheet = .calendar } label: {
        VStack(alignment: .leading, spacing: 1) {
          HStack(spacing: 4) {
            Text(overline).font(Typo.overline)
            Icon(.calendar, size: 12)
          }
          .foregroundColor(Palette.text2)
          Text(tr("趋势", "Trends")).font(Typo.title).titleTracking().foregroundColor(Palette.text)
        }
      }
      .buttonStyle(PressScale())
      .accessibilityLabel(tr("选择结束日期", "Pick the end date"))
      Spacer()
      if store.end != nil {
        Button { store.select(nil) } label: { Chip(label: tr("回到今天", "Back to today")) }.buttonStyle(PressScale())
      }
    }
    .padding(.top, 10).padding(.bottom, 10)
  }

  private var overline: String {
    if let e = store.end { return tr("截至 \(TodayModel.shortDay(e))", "Up to \(TodayModel.shortDay(e))") }
    guard let d = store.userToday else { return " " }
    return "\(TodayView.weekday(d)) \(TodayModel.shortDay(d))"
  }

  // MARK: - metrics

  @ViewBuilder private var metrics: some View {
    SegControl(items: TrendRange.allCases.map { ($0, $0.label) }, selected: store.range) { store.setRange($0) }
      .padding(.top, 8)
    if let p = store.page, store.range == .today {
      dayList(p)
    } else if let p = store.page {
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
        SectionHeader(title: g.title).padding(.top, 22).padding(.bottom, 10)
        ForEach(g.cards) { c in
          TrendCardView(card: c) { sheet = $0 }.padding(.bottom, 8).id("card.\(c.id)")
        }
      }
      if !p.status.tiles.isEmpty { statusBlock(p) }
    } else if store.loading {
      ProgressView().tint(Palette.text3).frame(maxWidth: .infinity).padding(.vertical, 60)
    } else {
      Text(store.offline ? tr("需要联网", "Needs a connection") : tr("暂无", "Nothing yet")).font(Typo.body).foregroundColor(Palette.text3)
        .frame(maxWidth: .infinity).padding(.vertical, 60)
    }
    SyncStatusLine(sync: session.sync, hasData: store.page != nil).padding(.top, 18)
  }

  /// 「今天」 (#357): the end day, its status cards first (they are "today" by nature), then every metric as one row.
  @ViewBuilder private func dayList(_ p: TrendsPage) -> some View {
    // the day itself is in the header's overline (and next to the status cards)
    let d = p.data
    if !p.status.tiles.isEmpty { statusBlock(p) }
    ForEach(TrendDay.groups(d), id: \.id) { g in
      SectionHeader(title: g.title).padding(.top, 22).padding(.bottom, 10)
      TrendDayList(rows: g.rows) { r in sheet = .explain(key: r.explain, title: r.label) }
        .id("day.\(g.id)")
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
        ForEach(p.status.tiles) { t in StatusTileView(tile: t) { sheet = .status(t) } }
      }
    }
    .padding(.top, 22)
    .id("status")
  }

  /// `W2W_PERF=1 W2W_PERF_SCROLL_TRENDS=1`: scroll the tab down and up three times while the frame meter counts.
  private func autoScroll(_ proxy: ScrollViewProxy) {
    Task { @MainActor in
      // after the range's data is on screen and the refresh is done
      for _ in 0..<40 where store.page == nil || store.loading { try? await Task.sleep(nanoseconds: 250_000_000) }
      try? await Task.sleep(nanoseconds: 1_500_000_000)
      PerfProbe.shared.startFrames()
      for _ in 0..<3 {
        withAnimation(.easeInOut(duration: 2)) { proxy.scrollTo("bottom", anchor: .bottom) }
        try? await Task.sleep(nanoseconds: 2_200_000_000)
        withAnimation(.easeInOut(duration: 2)) { proxy.scrollTo("top", anchor: .top) }
        try? await Task.sleep(nanoseconds: 2_200_000_000)
      }
      PerfProbe.shared.stopFrames(label: "trends_scroll_\(store.range.rawValue)d")
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
    case .recovery: return tr("恢复贡献项", "Recovery contributors")
    case .regularity: return tr("睡眠规律性", "Sleep regularity")
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
    }
  }

  /// Mean z-score of each contributor over the range, and the latest day's (app.js recoveryTrendHtml sheet).
  @ViewBuilder private var recovery: some View {
    let s = page?.data.series["recovery"]
    let names: [(String, String)] = [("hrv", "HRV"), ("rhr", tr("静息心率", "Resting HR")), ("resp_rate", tr("呼吸频率", "Respiratory rate")),
                                     ("sleep_performance", tr("睡眠表现", "Sleep performance"))]
    let zs = (s?.contributors ?? []).compactMap { $0 }
    let last = (s?.contributors ?? []).last { $0 != nil } ?? nil
    let fig = page.flatMap { p in s.map { TrendCards.figures($0, p.data, digits: 0, unit: nil) } }
    VStack(alignment: .leading, spacing: 0) {
      KVRow(label: fig?.meanLabel ?? tr("区间均值", "Range mean"), value: s?.mean.map { TodayModel.fmt($0) })
      if let fig { KVRow(label: fig.dayLabel, value: fig.day) }
      KVRow(label: tr("上期", "Previous"), value: s?.prevMean.map { TodayModel.fmt($0) })
      let rows = names.compactMap { k, label -> (String, Double, Double?)? in
        let xs = zs.compactMap { $0[k] }
        return xs.isEmpty ? nil : (label, xs.reduce(0, +) / Double(xs.count), last?[k])
      }
      if !rows.isEmpty {
        Text(tr("相对基线（z）", "Against baseline (z)")).font(Typo.section).foregroundColor(Palette.text2).padding(.top, 16).padding(.bottom, 6)
        VStack(alignment: .leading, spacing: 12) {
          ForEach(Array(rows.enumerated()), id: \.offset) { _, r in
            VStack(alignment: .leading, spacing: 5) {
              HStack {
                Text(r.0).font(Typo.body).foregroundColor(Palette.text)
                Spacer()
                Text(TrendsFormat.signed(r.1, digits: 2)).font(Typo.bodyStrong.monospacedDigit()).foregroundColor(r.1 > 0 ? Palette.good : r.1 < 0 ? Palette.bad : Palette.text2)
                Text("· \(tr("最近", "latest")) \(r.2.map { TrendsFormat.signed($0, digits: 2) } ?? "—")").font(Typo.caption.monospacedDigit()).foregroundColor(Palette.text3)
              }
              DiffBar(fraction: max(-1, min(1, r.1 / 2)), tone: r.1 > 0 ? .good : r.1 < 0 ? .bad : .text)
            }
          }
        }
      }
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
/// Debug builds only, for simulator screenshots: `SIMCTL_CHILD_W2W_DEV_TRENDS=<mode>[,<days>][,<end>]`
/// (`metrics,1` = 今天, `metrics,365`, `sleep`, `report`),
/// `SIMCTL_CHILD_W2W_DEV_TRENDS_SHEET=explain:<key>|status:<key>|card:<card id>|recovery|calendar` (`card:` opens a range
/// card's own sheet, with its mean and end-day figures), `SIMCTL_CHILD_W2W_DEV_TRENDS_SCROLL=<anchor>` (`status`,
/// `card.<key>`, `day.<group>`, `bottom`).
enum TrendsDevShot {
  static func apply(proxy: ScrollViewProxy, mode: @escaping (TrendsView.Mode) -> Void, range: @escaping (TrendRange) -> Void,
                    end: @escaping (String) -> Void, sheet: @escaping (TrendsSheet) -> Void, page: @escaping () -> TrendsPage?) {
    let env = ProcessInfo.processInfo.environment
    if let t = env["W2W_DEV_TRENDS"] {
      let p = t.split(separator: ",").map(String.init)
      if let m = p.first.flatMap(TrendsView.Mode.init(rawValue:)) { mode(m) }
      if p.count > 1, let n = Int(p[1]), let r = TrendRange(rawValue: n) { range(r) }
      if p.count > 2, p[2].count == 10 { end(p[2]) }
    }
    DispatchQueue.main.asyncAfter(deadline: .now() + 2.0) {
      if let a = env["W2W_DEV_TRENDS_SCROLL"] { proxy.scrollTo(a, anchor: .top) }
      let s = env["W2W_DEV_TRENDS_SHEET"] ?? ""
      if s == "recovery" { sheet(.recovery) }
      else if s == "calendar" { sheet(.calendar) }
      else if s.hasPrefix("explain:") { sheet(.explain(key: String(s.dropFirst(8)), title: String(s.dropFirst(8)))) }
      else if s.hasPrefix("status:"), let t = page()?.status.tiles.first(where: { $0.key == String(s.dropFirst(7)) }) { sheet(.status(t)) }
      else if s.hasPrefix("card:"), let p = page(),
              let c = TrendCards.groups(p).flatMap(\.cards).first(where: { $0.id == String(s.dropFirst(5)) }) {
        sheet(c.sheet ?? .explain(key: c.explain, title: c.title, figures: c.figures))
      }
    }
  }
}
#endif
