import SwiftUI

/// The Today tab, 2.1.0 (docs/design/2.0.1 `01-today`, drilldown option C′): level 1 is the first glance only, one recovery
/// dial, two readings (sleep, strain), one row of faces (体征 or 关注, 饮食, 圈子), and nothing below it. The dial (or the
/// ladder in the header, or a swipe up) opens level 2, 今日详情: every number of the day. A number opens level 3, its
/// metric page (chart, range, related metrics, explanation). Back is one step; the small dial in a level's header goes up
/// one level. Everything is read from the local database (first frame from the cache), refreshed through the sync layer;
/// numbers and short labels only. The date on top looks back at any day, a swipe changes it.
struct TodayView: View {
  @EnvironmentObject private var session: AccountSession
  @EnvironmentObject private var app: AppModel
  @StateObject private var store: TodayStore
  @State private var sheet: TodaySheet?
  @State private var showConsent = false
  @State private var path: [LevelRoute] = []
  @Namespace private var zoomSpace
  @StateObject private var zoom = ZoomTrail()
  @State private var toast: Toast?
  @SceneStorage("w2w.tab") private var tab: AppTab = .today
  @SceneStorage("w2w.record.segment") private var recordSegment: RecordTab.Segment = .training
  @State private var trainingOpen = false
  // Flag `slim_mode` (#126, DailyQuote.swift). Off, or with the normal look, nothing of it is drawn.
  @StateObject private var quotes = DailyQuoteStore()
  @State private var quoteLink: QuoteLink?

  private var showsQuote: Bool { session.appearance?.showsQuote == true }

  init(session: AccountSession) {
    _store = StateObject(wrappedValue: TodayStore(session: session))
  }

  var body: some View {
    NavigationStack(path: $path) {
      page
        .toolbar(.hidden, for: .navigationBar)
        .navigationDestination(for: LevelRoute.self) { r in level(r).zoomLevel(r) }
    }
    .zoomScope(zoomSpace, zoom)
    .sheet(item: $sheet) { s in
      TodaySheetView(sheet: s, model: store.model, store: store, open: { sheet = $0 }, close: { sheet = nil })
        .environmentObject(session)
    }
    .fullScreenCover(isPresented: $showConsent) {
      ConsentView(reconfirm: false) {
        showConsent = false
        await store.refresh()
      }
      .environmentObject(session)
      .environmentObject(app)
    }
    .sheet(isPresented: $trainingOpen) {
      if let t = store.model.training {
        TodayTrainingSheet(session: session, training: t, date: store.model.date, canLog: !store.model.isPast,
                           close: { trainingOpen = false }, openLog: {
          trainingOpen = false
          openTraining()
        })
        .presentationDetents([.medium, .large])
      }
    }
    .onAppear { if !store.isObserving { store.observe(session) } }
    .task { await store.refresh() }
    // The page was read before the consent and still says 「需要先确认同意书」: read it again once it is given.
    .onReceive(HealthSyncService.shared.$consentRequired.removeDuplicates().dropFirst()) { required in
      if !required { Task { await store.refresh() } }
    }
    .task(id: showsQuote) { if showsQuote { await quotes.refresh(session.api) } }
    .sheet(item: $quoteLink) { SafariView(url: $0.url) { quoteLink = nil }.ignoresSafeArea() }
  }

  /// Level 1: the first glance and nothing below it (no false floor: drilldown README section 9).
  private var page: some View {
    ScrollViewReader { proxy in
      ScrollView(.vertical, showsIndicators: false) {
        // Lazy: the first frame builds only what is on screen (rings and the first tiles)
        LazyVStack(alignment: .leading, spacing: 0) {
          TodayGlancePage(model: store.model, isPast: store.model.isPast, circlesOn: session.circlesTab, day: store.date,
                          session: session, run: glanceActions)
            .equatable()
            .id("top")
            .onAppear { if store.hasData { PerfProbe.shared.ringsShown() } }
            .onChange(of: store.hasData) { if $0 { PerfProbe.shared.ringsShown() } }
          Color.clear.frame(height: 1).id("bottom")
        }
        .padding(.horizontal, Metrics.gutter)
        .frame(maxWidth: Metrics.maxContentWidth)
        .frame(maxWidth: .infinity)
      }
      .background(Palette.bg.ignoresSafeArea())
      .statusBarCover()
      .clearOfJarvisKey()
      .refreshable { await store.refresh() }
      .toast($toast)
      .modifier(DaySwipe(step: { store.step($0) }, up: { openLevel(.day(anchor: nil), "glance.dial") }))
      .onAppear {
        if store.hasData && PerfProbe.autoScroll { autoScroll(proxy) }
        #if DEBUG
        PerfScript.today = PerfScript.TodayHooks(sheet: { sheet = $0 }, push: { r, door in
          if let r { push(r, door) } else { path.removeAll() }
        })
        DevShot.apply(sheet: { sheet = $0 }, path: { path = $0 }, day: { store.select($0) })
        #endif
      }
      .onChange(of: store.hasData) { has in if has && PerfProbe.autoScroll { autoScroll(proxy) } }
    }
  }

  // MARK: - levels 2 and 3

  @ViewBuilder private func level(_ r: LevelRoute) -> some View {
    switch r {
    case .day(let anchor):
      TodayDayPage(anchor: anchor, store: store, run: dayActions, footer: showsQuote ? AnyView(dailyQuote) : nil)
    case .metric(let id):
      MetricPage(id: id, end: store.date, range: LevelParams.metricDefaultRange,
                 crumb: .dial(store.model.glance.recovery.value, store.model.glance.recovery.tone), today: store.model,
                 push: push, up: { path = LevelNav.up(path, second: .day(anchor: nil)) },
                 explain: { key, title in sheet = .explain(kind: "metric", key: key, title: title) },
                 load: { sheet = .load }, bedtime: store.model.isPast ? nil : { sheet = .detail(.bedtimeGoal) })
    case .night(let d):
      SleepDetailView(initialDate: d) { key, title in sheet = .explain(kind: "metric", key: key, title: title) }
    case .all:
      EmptyView()   // 身体's own level
    }
  }

  private var dayActions: TodayDayActions {
    TodayDayActions(
      push: push,
      up: { path = [] },
      sheet: { sheet = $0 },
      calendar: { sheet = .calendar },
      backToToday: { store.select(nil) },
      step: { store.step($0) },
      training: { trainingOpen = true })
  }

  /// A door of the first glance: level 2 or a metric page straight from it (back then returns to the first glance).
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

  // MARK: - daily quote (flag slim_mode): the same quote on any day looked at, the user's today decides which

  @ViewBuilder private var dailyQuote: some View {
    let day = store.userToday ?? store.model.date ?? ""
    if let q = quotes.quote(on: day) {
      DailyQuoteCard(quote: q, next: { quotes.next(on: day) }, open: { quoteLink = QuoteLink(url: $0) }, close: {
        Task {
          do {
            try await session.saveAppearance(AppearancePatch(quotes: false))
          } catch let e as APIError {
            toast = Toast(e.userMessage)
          } catch {}
        }
      })
    }
  }

  static func weekday(_ iso: String) -> String {
    let utc = TimeZone(identifier: "UTC")!
    guard let d = LocalDay.parse(iso, utc) else { return "" }
    var cal = Calendar(identifier: .gregorian)
    cal.timeZone = utc
    let i = cal.component(.weekday, from: d) - 1
    let zh = [tr("周日", "Sun"), tr("周一", "Mon"), tr("周二", "Tue"), tr("周三", "Wed"), tr("周四", "Thu"), tr("周五", "Fri"), tr("周六", "Sat")]
    return zh[max(0, min(6, i))]
  }

  // MARK: - what the first glance's taps do

  private var glanceActions: TodayGlanceActions {
    TodayGlanceActions(
      level: openLevel,
      calendar: { sheet = .calendar },
      backToToday: { store.select(nil) },
      step: { store.step($0) },
      syncTap: { Task { _ = await HealthSyncService.shared.syncNow(); await store.refresh() } },
      syncCenter: { app.route = .syncCenter },
      recoveryNext: runRecoveryNext,
      food: {
        // 饮食 is a segment of 记录 (2.0.1): saved for a 记录 page still to be built, sent to one already there
        recordSegment = .food
        JarvisPageLink.shared.segment = .food
        tab = .record
      },
      circles: { tab = .circles })
  }

  /// 记录 → 训练 (the muscle card, 训练历史, 逐组记录).
  private func openTraining() {
    recordSegment = .training
    tab = .record
  }

  /// The button under an empty recovery ring (app.js recoveryNext).
  private func runRecoveryNext(_ a: RecoveryNext.Action) {
    switch a {
    case .consent: showConsent = true
    case .sync: app.route = .syncCenter
    case .sleepHelp: sheet = .detail(.sleepHelp)
    }
  }

  /// `W2W_PERF_SCROLL=1`: scroll down and up three times while the frame meter counts (PerfProbe).
  private func autoScroll(_ proxy: ScrollViewProxy) {
    Task { @MainActor in
      try? await Task.sleep(nanoseconds: 1_500_000_000)
      PerfProbe.shared.startFrames()
      for _ in 0..<3 {
        withAnimation(.easeInOut(duration: 1.2)) { proxy.scrollTo("bottom", anchor: .bottom) }
        try? await Task.sleep(nanoseconds: 1_400_000_000)
        withAnimation(.easeInOut(duration: 1.2)) { proxy.scrollTo("top", anchor: .top) }
        try? await Task.sleep(nanoseconds: 1_400_000_000)
      }
      PerfProbe.shared.stopFrames(label: "today_scroll")
    }
  }
}

#if DEBUG
/// Debug builds only, for simulator screenshots: `SIMCTL_CHILD_W2W_DEV_LEVEL=day|day:<anchor>|metric:<id>|night`
/// (`day:attention`, `metric:hrv`: a metric page straight from the first glance; `day,metric:hrv`: through 今日详情),
/// `SIMCTL_CHILD_W2W_DEV_SHEET=load|calendar|weight|reminders|sleepCoach|sleepHelp|lab:<key>|explain:<kind>:<key>`,
/// `SIMCTL_CHILD_W2W_DEV_SLEEP=1` (the night page), `SIMCTL_CHILD_W2W_DEV_DATE=YYYY-MM-DD` (look back).
enum DevShot {
  static func apply(sheet: @escaping (TodaySheet) -> Void, path: @escaping ([LevelRoute]) -> Void, day: @escaping (String) -> Void) {
    let env = ProcessInfo.processInfo.environment
    if let d = env["W2W_DEV_DATE"], d.count == 10 { day(d) }
    DispatchQueue.main.asyncAfter(deadline: .now() + 1.2) {
      if let l = env["W2W_DEV_LEVEL"] {
        path(l.split(separator: ",").compactMap { route(String($0)) })
      } else if env["W2W_DEV_SLEEP"] == "1" {
        path([.night(nil)])   // the App Store screenshots' sleep page (scripts/appstore_screenshots.sh)
      }
      switch env["W2W_DEV_SHEET"] ?? "" {
      case "load": sheet(.load)
      case "calendar": sheet(.calendar)
      case "weight": sheet(.weight)
      case "reminders": sheet(.reminders)
      case "sleepCoach": sheet(.detail(.sleepCoach))
      case "sleepHelp": sheet(.detail(.sleepHelp))
      case let x where x.hasPrefix("lab:"): sheet(.detail(.lab(key: String(x.dropFirst(4)), title: String(x.dropFirst(4)))))
      case let x where x.hasPrefix("explain:"):
        let p = x.split(separator: ":").map(String.init)
        if p.count == 3 { sheet(.explain(kind: p[1], key: p[2], title: p[2])) }
      default: break
      }
    }
  }

  /// `day`, `day:<anchor>`, `metric:<id>`, `night`.
  static func route(_ s: String) -> LevelRoute? {
    if s == "day" { return .day(anchor: nil) }
    if s.hasPrefix("day:") { return .day(anchor: String(s.dropFirst(4))) }
    if s.hasPrefix("metric:") { return .metric(String(s.dropFirst(7))) }
    if s == "night" { return .night(nil) }
    return nil
  }
}
#endif

// MARK: - cards

/// 需要注意 (#357): one row per item — tone dot, short label, the baseline / range, the value with its arrow.
struct AttentionList: View {
  let items: [TodayAttention]
  /// The items' zoom door prefix (`<door>.<item id>`).
  var door: String?
  let open: (TodayAttention) -> Void

  var body: some View {
    VStack(spacing: 0) {
      ForEach(Array(items.enumerated()), id: \.element.id) { i, it in
        Button { open(it) } label: {
          HStack(spacing: 8) {
            Circle().fill(it.tone.color).frame(width: 7, height: 7)
            Text(it.label).font(Typo.body).foregroundColor(Palette.text).lineLimit(1)
            Spacer(minLength: 6)
            if let b = it.base { Text(b).font(.system(size: 11).monospacedDigit()).foregroundColor(Palette.text3).lineLimit(1) }
            HStack(alignment: .firstTextBaseline, spacing: 2) {
              Text(it.value).font(Typo.bodyStrong.monospacedDigit()).foregroundColor(it.tone.color).lineLimit(1)
              if let u = it.unit { Text(u).font(Typo.unit).foregroundColor(Palette.text2).lineLimit(1) }
              if let a = it.arrow { Text(a).font(.system(size: 13, weight: .semibold)).foregroundColor(it.tone.color) }
            }
            Icon(.chevronRight, size: 13).foregroundColor(Palette.text3)
          }
          .padding(.vertical, 11)
          .overlay(i == items.count - 1 ? nil : Rectangle().fill(Palette.hair).frame(height: 1), alignment: .bottom)
          .contentShape(Rectangle())
        }
        .buttonStyle(PressScale())
        .zoomDoor(door.map { "\($0).\(it.id)" })
        .accessibilityElement(children: .combine)
      }
    }
    .padding(.horizontal, 14)
    .background(RoundedRectangle(cornerRadius: Metrics.cardRadius, style: .continuous).fill(Palette.surface))
  }
}

/// A 今日核心 card, drawn like the Trends status cards (label, value in its tone colour, the small line under it).
struct CoreCardView: View {
  let card: TodayCoreCard
  let action: () -> Void

  var body: some View {
    Button(action: action) {
      VStack(alignment: .leading, spacing: 4) {
        HStack(spacing: 4) {
          Text(card.label).font(Typo.label).foregroundColor(Palette.text2).lineLimit(1)
          if card.local { Circle().fill(Palette.text3).frame(width: 4, height: 4).accessibilityLabel(tr("本机计算", "On this phone")) }
        }
        HStack(alignment: .firstTextBaseline, spacing: 2) {
          Text(card.value).font(.system(size: 19, weight: .semibold).monospacedDigit())
            .foregroundColor(card.tone == .text ? Palette.text : card.tone.color).lineLimit(1).minimumScaleFactor(0.6)
          if let u = card.unit { Text(u).font(Typo.unit).foregroundColor(Palette.text2).lineLimit(1).minimumScaleFactor(0.7) }
        }
        if let s = card.sub { Text(s).font(.system(size: 11).monospacedDigit()).foregroundColor(Palette.text3).lineLimit(1) }
      }
      .frame(maxWidth: .infinity, alignment: .leading)
      .padding(12)
      .background(RoundedRectangle(cornerRadius: Metrics.tileRadius, style: .continuous).fill(Palette.surface))
    }
    .buttonStyle(PressScale())
    .accessibilityElement(children: .combine)
  }
}

/// One body status card: label with its tone dot, the value, trend arrow and a spark line (app.js statusHtml).
struct StatusCell: View {
  let item: TodayModel.StatusItem
  let action: () -> Void

  var body: some View {
    Button(action: action) {
      VStack(alignment: .leading, spacing: 6) {
        HStack(spacing: 6) {
          Circle().fill(item.tone == .text ? Palette.text3 : item.tone.color).frame(width: 6, height: 6)
          Text(item.label).font(Typo.label).foregroundColor(Palette.text2).lineLimit(1)
        }
        HStack(alignment: .firstTextBaseline, spacing: 2) {
          Text(item.value).font(.system(size: 19, weight: .semibold).monospacedDigit()).foregroundColor(Palette.text)
            .lineLimit(1).minimumScaleFactor(0.6)
          if let u = item.unit { Text(u).font(Typo.unit).foregroundColor(Palette.text2) }
          if let t = item.trend {
            Text(t).font(.system(size: 13, weight: .semibold)).foregroundColor(item.trendTone == .text ? Palette.text3 : item.trendTone.color)
          }
        }
        Sparkline(values: item.spark, tone: item.tone).frame(height: 18)
      }
      .frame(maxWidth: .infinity, alignment: .leading)
      .padding(12)
      .background(RoundedRectangle(cornerRadius: Metrics.tileRadius, style: .continuous).fill(Palette.surface))
    }
    .buttonStyle(PressScale())
    .accessibilityElement(children: .combine)
  }
}
