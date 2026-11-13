import SwiftUI

/// Route of the sleep detail page (a sub-page of Today).
struct SleepRoute: Hashable {
  var date: String?
}

/// The Today tab, 2.0.1 (docs/design/2.0.1 `01-today`–`04-sleep-open`, `kit-states`): one recovery dial, two readings
/// (sleep, strain), one row of faces (体征 or 关注, 饮食, 圈子); below the fold 训练 · 今晚 · 提醒 and the sync nudges.
/// The dial and the readings open L1 half sheets (`TodayL1Sheet`); their rows open the explain sheets, 「全部指标」 leads to
/// 身体 (or the sleep night page). Everything is read from the local database (first frame from the cache), refreshed
/// through the sync layer; numbers and short labels only. The date on top looks back at any day, a swipe changes it.
struct TodayView: View {
  @EnvironmentObject private var session: AccountSession
  @EnvironmentObject private var app: AppModel
  @StateObject private var store: TodayStore
  @State private var sheet: TodaySheet?
  @State private var showConsent = false
  @State private var path: [SleepRoute] = []
  @State private var toast: Toast?
  @SceneStorage("w2w.tab") private var tab: AppTab = .today
  @SceneStorage("w2w.record.segment") private var recordSegment: RecordTab.Segment = .training
  @State private var trainingOpen = false
  @State private var l1: TodayL1?
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
        .navigationDestination(for: SleepRoute.self) { r in
          SleepDetailView(initialDate: r.date) { key, title in sheet = .explain(kind: "metric", key: key, title: title) }
        }
        .sheet(item: $l1) { k in
          TodayL1Sheet(kind: k, model: store.model, session: session, close: { l1 = nil },
                       explain: { key, title in after { sheet = .explain(kind: "metric", key: key, title: title) } },
                       load: { after { sheet = .load } },
                       openItem: { o in after { open(o) } },
                       monitor: { after { sheet = .monitor } },
                       allMetrics: { k in after { allMetrics(k) } })
            .environmentObject(session)
            .l1Detents(k.height)
        }
    }
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
          // below the fold (vertical scroll only): 训练 · 今晚 · 提醒, then the sync nudges and the slim look's quote
          // the first glance ends with the face row; the key sits in the gap under it, so this row starts below the fold
          TodayMoreFaces(model: store.model, run: glanceActions).equatable().padding(.top, 150)
          if !store.model.isPast {
            SyncNudges(card: store.model.sync, coverage: store.model.coverage) { await store.refresh() }.padding(.top, 16)
          }
          if showsQuote { dailyQuote.padding(.top, 16) }
          // scrolled to the end, nothing sits under the Jarvis key (direction.md 2.4)
          Color.clear.frame(height: 140)
          Color.clear.frame(height: 1).id("bottom")
        }
        .padding(.horizontal, Metrics.gutter)
        .padding(.bottom, 32)
        .frame(maxWidth: Metrics.maxContentWidth)
        .frame(maxWidth: .infinity)
      }
      .background(Palette.bg.ignoresSafeArea())
      .statusBarCover()
      .refreshable { await store.refresh() }
      .toast($toast)
      .modifier(DaySwipe { store.step($0) })
      .onAppear {
        if store.hasData && PerfProbe.autoScroll { autoScroll(proxy) }
        #if DEBUG
        PerfScript.today = PerfScript.TodayHooks(sheet: { sheet = $0 }, push: { r in if let r { path.append(r) } else { path.removeAll() } })
        DevShot.apply(proxy: proxy, sheet: { sheet = $0 }, push: { path.append($0) }, day: { store.select($0) })
        // `SIMCTL_CHILD_W2W_DEV_L1=recovery|strain|sleep`: opens that L1 sheet (2.0.1 screenshots)
        if let k = ProcessInfo.processInfo.environment["W2W_DEV_L1"].flatMap(TodayL1.init(rawValue:)) {
          DispatchQueue.main.asyncAfter(deadline: .now() + 2.5) { l1 = k }
        }
        #endif
      }
      .onChange(of: store.hasData) { has in if has && PerfProbe.autoScroll { autoScroll(proxy) } }
    }
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

  // MARK: - what the page's taps do

  private var glanceActions: TodayGlanceActions {
    TodayGlanceActions(
      l1: { l1 = $0 },
      sheet: { sheet = $0 },
      open: open,
      calendar: { sheet = .calendar },
      backToToday: { store.select(nil) },
      step: { store.step($0) },
      syncTap: { Task { _ = await HealthSyncService.shared.syncNow(); await store.refresh() } },
      syncCenter: { app.route = .syncCenter },
      recoveryNext: runRecoveryNext,
      food: {
        recordSegment = .food   // 饮食 is a segment of 记录 (2.0.1)
        tab = .record
      },
      circles: { tab = .circles },
      training: { trainingOpen = true })
  }

  /// After the L1 sheet has gone: one sheet at a time.
  private func after(_ f: @escaping () -> Void) {
    l1 = nil
    DispatchQueue.main.asyncAfter(deadline: .now() + 0.35, execute: f)
  }

  /// 「全部指标」 of an L1 sheet: 身体 for the day (recovery, strain), the sleep night page (sleep).
  private func allMetrics(_ k: TodayL1) {
    if k == .sleep {
      path.append(SleepRoute(date: store.model.sleepDate))
    } else {
      TrendsLink.shared.open(day: store.date)
      tab = .trends
    }
  }

  /// 记录 → 训练 (the muscle card, 训练历史, 逐组记录).
  private func openTraining() {
    recordSegment = .training
    tab = .record
  }

  private func open(_ o: TodayOpen) {
    switch o {
    case .coach: sheet = .coach
    case .metric("stress", _): sheet = .detail(.stress)
    case .metric(let k, let t): sheet = .explain(kind: "metric", key: k, title: t)
    case .status(let t): sheet = .status(t)
    }
  }

  /// The button under an empty recovery ring (app.js recoveryNext).
  private func runRecoveryNext(_ a: RecoveryNext.Action) {
    switch a {
    case .consent: showConsent = true
    case .sync: app.route = .syncCenter
    case .sleepHelp: sheet = .detail(.sleepHelp)
    }
  }

  private func handle(_ a: TodayModel.Action) {
    switch a {
    case .recovery: sheet = .recovery
    case .strain: sheet = .strain
    case .load: sheet = .load
    case .coach: sheet = .coach
    case .monitor: sheet = .monitor
    case .weight: sheet = .weight
    case .allReminders: sheet = .reminders
    case .reminder(let id): sheet = .reminder(id)
    case .sleepDetail(let d): path.append(SleepRoute(date: d ?? store.model.sleepDate))
    case .explainMetric(let k, let t): sheet = .explain(kind: "metric", key: k, title: t)
    case .explainStatus(let k, let t): sheet = .explain(kind: "status", key: k, title: t)
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

// MARK: - the day's cards

/// Everything a day shows, from the rings to the reminders: the Today tab's page and the read-only view of someone else
/// (`MemberTodayPage`) draw the same cards from a `TodayModel`. What happens on a tap belongs to the page. What leads to my own
/// data (the button under an empty ring, the training cards, the food card) is only there when the page passes it.
///
/// Equatable on what it draws (the model and which optional parts are there), never on the closures: opening a sheet,
/// pushing the sleep page or switching tabs changes the page's own state, and without this every card of the day was
/// built and laid out again before the sheet could move (2.0.1: 150–330 ms on the simulator). The closures only reach
/// the page's state, which lives outside the view value, so an older copy of them does the same.
struct TodayDayCards: View, Equatable {
  static func == (a: TodayDayCards, b: TodayDayCards) -> Bool {
    a.model == b.model && a.hasData == b.hasData && (a.recoveryNext == nil) == (b.recoveryNext == nil)
      && (a.openTraining == nil) == (b.openTraining == nil) && (a.openHistory == nil) == (b.openHistory == nil)
      && (a.foodCard == nil) == (b.foodCard == nil)
  }

  let model: TodayModel
  var hasData = true
  var onRingsShown: () -> Void = {}
  let handle: (TodayModel.Action) -> Void
  let open: (TodayOpen) -> Void
  let show: (TodaySheet) -> Void
  let openStatus: (TodayModel.StatusItem) -> Void
  let allMetrics: () -> Void
  var recoveryNext: ((RecoveryNext.Action) -> Void)?
  var openTraining: (() -> Void)?
  var openHistory: (() -> Void)?
  var foodCard: AnyView?

  // no container here: inside the page's LazyVStack each card stays its own lazy row
  var body: some View {
    let m = model
    if !m.rings.isEmpty { rings(m).padding(.top, 10) }
    if let w = m.word {
      Text(w).font(Typo.heroWord).foregroundColor(m.wordMuted ? Palette.text2 : Palette.text)
        .frame(maxWidth: .infinity).padding(.top, 12)
    }
    if let s = m.sub {
      Text(s).font(Typo.caption).foregroundColor(Palette.text2).frame(maxWidth: .infinity).padding(.top, 3)
    }
    if let n = m.recoveryNext, let recoveryNext {
      Button { recoveryNext(n.action) } label: { Chip(label: n.label) }
        .buttonStyle(PressScale())
        .frame(maxWidth: .infinity).padding(.top, 10)
        .id("recoveryNext")
    }
    if m.loadChip != nil || m.local {
      HStack(spacing: 8) {
        if let l = m.loadChip {
          Button { show(.load) } label: { Chip(label: "\(tr("负荷", "Load")) \(l)") }.buttonStyle(PressScale())
        }
        if m.local { Chip(label: tr("本机计算", "On this phone")) }
      }
      .frame(maxWidth: .infinity).padding(.top, 14)
    }
    if let e = m.error { ToneBadge(text: e).frame(maxWidth: .infinity).padding(.top, 10) }

    // 需要注意 (#357): only when something is off on the day
    if !m.attention.isEmpty {
      VStack(alignment: .leading, spacing: 10) {
        SectionHeader(title: tr("需要注意", "Needs attention"))
        AttentionList(items: m.attention) { open($0.open) }
      }
      .padding(.top, 22)
      .id("attention")
    }

    // 今日核心 (#357): the strain target (live) and our algorithms' results for the day
    if !m.core.isEmpty {
      VStack(alignment: .leading, spacing: 10) {
        SectionHeader(title: tr("今日核心", "Today's core"))
        LazyVGrid(columns: [GridItem(.flexible(), spacing: 8), GridItem(.flexible(), spacing: 8)], spacing: 8) {
          ForEach(m.core) { c in CoreCardView(card: c) { open(c.open) } }
        }
      }
      .padding(.top, 22)
      .id("core")
    }

    ForEach(m.sections) { s in
      VStack(alignment: .leading, spacing: 10) {
        SectionHeader(title: s.title)
        HStack(spacing: 8) { ForEach(s.metrics) { TapTile(metric: $0, action: handle) } }
      }
      .padding(.top, 22)
    }

    if let c = m.tonight {
      SleepCoachCard(coach: c, isPast: m.isPast) { show(.detail(.sleepCoach)) }.padding(.top, 22).id("sleepCoach")
    }

    // the strain target lives in 今日核心; the health monitor keeps its own card
    if !m.vitals.isEmpty {
      MonitorCard(vitals: m.vitals, off: m.vitalsOff) { show(.monitor) }.padding(.top, 22)
    }

    if m.date != nil {
      Button(action: allMetrics) {
        HStack(spacing: 4) {
          Text(tr("全部指标", "All metrics")).font(Typo.bodyStrong)
          Icon(.chevronRight, size: 14)
        }
        .foregroundColor(Palette.text2)
        .frame(maxWidth: .infinity, alignment: .trailing)
        .padding(.vertical, 6)
        .contentShape(Rectangle())
      }
      .buttonStyle(PressScale())
      .padding(.top, 12)
      .id("allMetrics")
    }

    if let t = m.training, let openTraining, let openHistory {
      TodayTrainingCard(training: t, open: openTraining, history: openHistory).padding(.top, 22).id("training")
    }
    if !m.isPast, let mm = m.muscle, let openHistory { TodayMuscleCard(muscle: mm, open: openHistory).padding(.top, 22).id("muscle") }

    if !m.isPast && !m.status.isEmpty { statusBlock(m.status).padding(.top, 22) }

    if let foodCard { foodCard }

    if !m.reminders.isEmpty {
      VStack(alignment: .leading, spacing: 8) {
        SectionTitle(title: tr("提醒", "Reminders"), trailing: m.reminders.count > 3 ? tr("全部 \(m.reminders.count)", "All \(m.reminders.count)") : nil) {
          show(.reminders)
        }
        ForEach(m.topReminders) { r in ReminderRow(reminder: r) { show(.reminder(r.id)) } }
      }
      .padding(.top, 22)
    }
  }

  /// Three rings side by side, recovery in the middle and 1.4× bigger (NativeToday.swift of 1.0.9).
  private func rings(_ m: TodayModel) -> some View {
    let width = min(UIScreen.main.bounds.width, Metrics.maxContentWidth) - Metrics.gutter * 2
    // 1 + 1.4 + 1 ring sides, plus each ring's stroke padding (≈ 0.24 side in all) and two 12 pt gaps
    let side = min(100, (max(width, 200) - 24) / 3.7), big = side * 1.4
    return HStack(alignment: .center, spacing: 12) {
      ForEach(m.rings, id: \.key) { r in
        let isBig = r.key == "recovery"
        Button { handle(r.key == "sleep" ? .sleepDetail(m.sleepDate) : r.action) } label: {
          RingView(value: r.value, label: r.label, fraction: r.fraction, tone: r.tone, side: isBig ? big : side, big: isBig)
        }
        .buttonStyle(PressScale())
      }
    }
    .frame(maxWidth: .infinity)
    .onAppear { if hasData { onRingsShown() } }
    .onChange(of: hasData) { if $0 { onRingsShown() } }
  }

  private func statusBlock(_ groups: [TodayModel.StatusGroup]) -> some View {
    VStack(alignment: .leading, spacing: 10) {
      SectionHeader(title: tr("身体状态", "Body status"))
      ForEach(groups) { g in
        if groups.count > 1, !g.name.isEmpty {
          Text(g.name).font(Typo.label).foregroundColor(Palette.text3).padding(.top, 2)
        }
        LazyVGrid(columns: [GridItem(.flexible(), spacing: 8), GridItem(.flexible(), spacing: 8)], spacing: 8) {
          ForEach(g.items) { it in
            StatusCell(item: it) { openStatus(it) }
          }
        }
      }
    }
  }
}

#if DEBUG
/// Debug builds only, for simulator screenshots: `SIMCTL_CHILD_W2W_DEV_SCROLL=<anchor id>` (`attention`, `core`,
/// `allMetrics`, `bottom`),
/// `SIMCTL_CHILD_W2W_DEV_SHEET=recovery|strain|load|coach|monitor|calendar|weight|stress|sleepCoach|bodyAge|sleepHelp|lab:<key>|explain:<kind>:<key>`,
/// `SIMCTL_CHILD_W2W_DEV_SLEEP=1` (open the sleep detail page), `SIMCTL_CHILD_W2W_DEV_DATE=YYYY-MM-DD` (look back).
enum DevShot {
  static func apply(proxy: ScrollViewProxy, sheet: @escaping (TodaySheet) -> Void, push: @escaping (SleepRoute) -> Void,
                    day: @escaping (String) -> Void) {
    let env = ProcessInfo.processInfo.environment
    if let d = env["W2W_DEV_DATE"], d.count == 10 { day(d) }
    DispatchQueue.main.asyncAfter(deadline: .now() + 1.2) {
      if let a = env["W2W_DEV_SCROLL"] { proxy.scrollTo(a, anchor: .bottom) }
      if env["W2W_DEV_SLEEP"] == "1" { push(SleepRoute(date: nil)) }
      switch env["W2W_DEV_SHEET"] ?? "" {
      case "recovery": sheet(.recovery)
      case "strain": sheet(.strain)
      case "load": sheet(.load)
      case "coach": sheet(.coach)
      case "monitor": sheet(.monitor)
      case "calendar": sheet(.calendar)
      case "weight": sheet(.weight)
      case "stress": sheet(.detail(.stress))
      case "sleepCoach": sheet(.detail(.sleepCoach))
      case "bodyAge": sheet(.detail(.bodyAge))
      case "sleepHelp": sheet(.detail(.sleepHelp))
      case let x where x.hasPrefix("lab:"): sheet(.detail(.lab(key: String(x.dropFirst(4)), title: String(x.dropFirst(4)))))
      case let x where x.hasPrefix("explain:"):
        let p = x.split(separator: ":").map(String.init)
        if p.count == 3 { sheet(.explain(kind: p[1], key: p[2], title: p[2])) }
      default: break
      }
    }
  }

}
#endif

// MARK: - cards

/// 需要注意 (#357): one row per item — tone dot, short label, the baseline / range, the value with its arrow.
struct AttentionList: View {
  let items: [TodayAttention]
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

/// Health monitor: normal / n off, and a dot per vital (app.js monitorHtml).
struct MonitorCard: View {
  let vitals: [TodayModel.Vital]
  let off: Int
  /// The AI-first page has the title (and why the card is there) above the card.
  var showTitle = true
  let action: () -> Void

  var body: some View {
    Button(action: action) {
      VStack(alignment: .leading, spacing: 10) {
        HStack {
          if showTitle { Text(tr("健康监测", "Health monitor")).font(Typo.label).foregroundColor(Palette.text2).lineLimit(1) }
          Spacer(minLength: 4)
          ToneBadge(text: off > 0 ? tr("\(off) 异常", "\(off) off") : tr("正常", "Normal"), tone: off > 0 ? .bad : .good)
        }
        HStack(spacing: 6) {
          ForEach(vitals) { v in
            VStack(spacing: 4) {
              Circle().fill(v.value == nil ? Palette.surface2 : (v.off ? Palette.bad : Palette.good)).frame(width: 8, height: 8)
              Text(v.short).font(.system(size: 10, weight: .medium)).foregroundColor(Palette.text2).lineLimit(1).minimumScaleFactor(0.7)
            }
            .frame(maxWidth: .infinity)
          }
        }
      }
      .frame(maxWidth: .infinity, alignment: .leading)
      .padding(14)
      .background(RoundedRectangle(cornerRadius: Metrics.cardRadius, style: .continuous).fill(Palette.surface))
    }
    .buttonStyle(PressScale())
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

/// Sync state: a dot, the server's short label, how long ago. The row opens the sync center; the button at its end syncs now.
struct SyncCardView: View {
  let card: TodayModel.SyncCard
  @ObservedObject var sync: SyncCoordinator
  @ObservedObject private var health = HealthSyncService.shared
  let open: () -> Void
  let syncNow: () -> Void

  var body: some View {
    HStack(spacing: 0) {
      Button(action: open) {
        HStack(spacing: 10) {
          Circle().fill(card.tone == .text ? Palette.text3 : card.tone.color).frame(width: 8, height: 8)
          Text(card.label).font(Typo.body).foregroundColor(Palette.text).lineLimit(1)
          Spacer(minLength: 6)
          if let a = card.ago { Text(a).font(Typo.caption).foregroundColor(Palette.text2) }
        }
        .padding(.leading, 14).padding(.vertical, 14)
        .contentShape(Rectangle())
      }
      .buttonStyle(PressScale())
      .accessibilityLabel("\(tr("同步中心", "Sync center")) \(card.label)")
      Button(action: syncNow) {
        Group {
          if sync.status == .syncing || health.running { ProgressView().scaleEffect(0.7).tint(Palette.text3) }
          else { Icon(.refreshCw, size: 16).foregroundColor(Palette.text3) }
        }
        .frame(width: 44, height: 44)
        .contentShape(Rectangle())
      }
      .buttonStyle(PressScale())
      .disabled(health.running)
      .accessibilityLabel(tr("立即同步", "Sync now"))
    }
    .background(RoundedRectangle(cornerRadius: Metrics.cardRadius, style: .continuous).fill(Palette.surface))
  }
}

/// Small line under the page: updating / offline / error, so cached data is never mistaken for fresh (N0).
struct SyncStatusLine: View {
  @ObservedObject var sync: SyncCoordinator
  let hasData: Bool

  var body: some View {
    HStack(spacing: 6) {
      switch sync.status {
      case .syncing:
        ProgressView().scaleEffect(0.7).tint(Palette.text3)
        Text(tr("更新中", "Updating"))
      case .offline:
        Text(hasData ? tr("离线，显示上次的数据", "Offline, showing the last data") : tr("离线", "Offline"))
      case .failed(let m):
        Text(m).lineLimit(2)
      case .idle:
        EmptyView()
      }
    }
    .font(Typo.label)
    .foregroundColor(Palette.text3)
    .frame(maxWidth: .infinity)
  }
}
