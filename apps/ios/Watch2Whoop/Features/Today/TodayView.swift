import SwiftUI

/// Route of the sleep detail page (a sub-page of Today).
struct SleepRoute: Hashable {
  var date: String?
}

/// The Today tab (#340 N3): three rings side by side (sleep score | recovery, centred and bigger | yesterday's strain),
/// then 需要注意 (only what is off on the day) and 今日核心 (strain target, recovery vs strain, stress, illness, body
/// battery, HRV status, training status; #357), the core numbers, health monitor, 「全部指标 →」 (Trends' 今天 for the same
/// day), body status, reminders, sync and circles. Everything is read
/// from the local database (first frame from the cache), refreshed through the sync layer; numbers and short labels
/// only — the words are in the explain sheets. The date on top looks back at any day.
struct TodayView: View {
  @EnvironmentObject private var session: AccountSession
  @StateObject private var store: TodayStore
  @State private var sheet: TodaySheet?
  @State private var path: [SleepRoute] = []
  @SceneStorage("w2w.tab") private var tab: AppTab = .today

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
    }
    .sheet(item: $sheet) { s in
      TodaySheetView(sheet: s, model: store.model, store: store, open: { sheet = $0 }, close: { sheet = nil })
        .environmentObject(session)
    }
    .onAppear { if !store.isObserving { store.observe(session) } }
    .task { await store.refresh() }
  }

  private var page: some View {
    ScrollViewReader { proxy in
      ScrollView(.vertical, showsIndicators: false) {
        // Lazy: the first frame builds only what is on screen (rings and the first tiles)
        LazyVStack(alignment: .leading, spacing: 0) {
          header.id("top")
          content
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
        if store.hasData && PerfProbe.autoScroll { autoScroll(proxy) }
        #if DEBUG
        DevShot.apply(proxy: proxy, sheet: { sheet = $0 }, push: { path.append($0) }, day: { store.select($0) })
        #endif
      }
      .onChange(of: store.hasData) { has in if has && PerfProbe.autoScroll { autoScroll(proxy) } }
    }
  }

  // MARK: - header: date (opens the look-back calendar) and title

  private var header: some View {
    HStack(alignment: .bottom) {
      Button { sheet = .calendar } label: {
        VStack(alignment: .leading, spacing: 1) {
          HStack(spacing: 4) {
            Text(overline).font(Typo.overline)
            Icon(.calendar, size: 12)
          }
          .foregroundColor(Palette.text2)
          Text(store.model.isPast ? TodayModel.shortDay(store.model.date) : tr("今天", "Today"))
            .font(Typo.title).titleTracking().foregroundColor(Palette.text)
        }
      }
      .buttonStyle(PressScale())
      .accessibilityLabel(tr("选择日期", "Pick a day"))
      Spacer()
      if store.date != nil {
        Button { store.select(nil) } label: { Chip(label: tr("回到今天", "Back to today")) }.buttonStyle(PressScale())
      }
    }
    .padding(.top, 10).padding(.bottom, 8)
  }

  private var overline: String {
    guard let d = store.model.date ?? store.userToday else { return " " }
    return "\(Self.weekday(d)) \(TodayModel.shortDay(d))"
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

  // MARK: - content

  @ViewBuilder private var content: some View {
    let m = store.model
    rings(m).padding(.top, 10)
    if let w = m.word {
      Text(w).font(Typo.heroWord).foregroundColor(m.wordMuted ? Palette.text2 : Palette.text)
        .frame(maxWidth: .infinity).padding(.top, 12)
    }
    if let s = m.sub {
      Text(s).font(Typo.caption).foregroundColor(Palette.text2).frame(maxWidth: .infinity).padding(.top, 3)
    }
    if m.loadChip != nil || m.local {
      HStack(spacing: 8) {
        if let l = m.loadChip {
          Button { sheet = .load } label: { Chip(label: "\(tr("负荷", "Load")) \(l)") }.buttonStyle(PressScale())
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

    // the strain target lives in 今日核心; the health monitor keeps its own card
    if !m.vitals.isEmpty {
      MonitorCard(vitals: m.vitals, off: m.vitalsOff) { sheet = .monitor }.padding(.top, 22)
    }

    if m.date != nil {
      Button {
        TrendsLink.shared.open(day: store.date)
        tab = .trends
      } label: {
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

    if !m.isPast && !m.status.isEmpty { statusBlock(m.status).padding(.top, 22) }

    if !m.reminders.isEmpty {
      VStack(alignment: .leading, spacing: 8) {
        SectionTitle(title: tr("提醒", "Reminders"), trailing: m.reminders.count > 3 ? tr("全部 \(m.reminders.count)", "All \(m.reminders.count)") : nil) {
          sheet = .reminders
        }
        ForEach(m.topReminders) { r in ReminderRow(reminder: r) { sheet = .reminder(r.id) } }
      }
      .padding(.top, 22)
    }

    if let s = m.sync { SyncCardView(card: s, sync: session.sync) { Task { await store.refresh() } }.padding(.top, 22) }
    if m.circles != nil { CircleSummaryCard { tab = .circles }.padding(.top, 22) }

    SyncStatusLine(sync: session.sync, hasData: store.hasData).padding(.top, 18)
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
    .onAppear { if store.hasData { PerfProbe.shared.ringsShown() } }
    .onChange(of: store.hasData) { if $0 { PerfProbe.shared.ringsShown() } }
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
            StatusCell(item: it) {
              if it.key == "weight" { sheet = .weight }
              else { sheet = .explain(kind: "status", key: it.key, title: it.label) }
            }
          }
        }
      }
    }
  }

  private func open(_ o: TodayOpen) {
    switch o {
    case .coach: sheet = .coach
    case .metric(let k, let t): sheet = .explain(kind: "metric", key: k, title: t)
    case .status(let t): sheet = .status(t)
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

#if DEBUG
/// Debug builds only, for simulator screenshots: `SIMCTL_CHILD_W2W_DEV_SCROLL=<anchor id>` (`attention`, `core`,
/// `allMetrics`, `bottom`),
/// `SIMCTL_CHILD_W2W_DEV_SHEET=recovery|strain|load|coach|monitor|calendar|weight|explain:<kind>:<key>`,
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
  let action: () -> Void

  var body: some View {
    Button(action: action) {
      VStack(alignment: .leading, spacing: 10) {
        HStack {
          Text(tr("健康监测", "Health monitor")).font(Typo.label).foregroundColor(Palette.text2).lineLimit(1)
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

/// Sync state: a dot, the server's short label, how long ago; tap = sync now (N8 adds the sync center).
struct SyncCardView: View {
  let card: TodayModel.SyncCard
  @ObservedObject var sync: SyncCoordinator
  let refresh: () -> Void

  var body: some View {
    Button(action: refresh) {
      HStack(spacing: 10) {
        Circle().fill(card.tone == .text ? Palette.text3 : card.tone.color).frame(width: 8, height: 8)
        Text(card.label).font(Typo.body).foregroundColor(Palette.text).lineLimit(1)
        Spacer(minLength: 6)
        if let a = card.ago { Text(a).font(Typo.caption).foregroundColor(Palette.text2) }
        if sync.status == .syncing { ProgressView().scaleEffect(0.7).tint(Palette.text3) }
        else { Icon(.refreshCw, size: 16).foregroundColor(Palette.text3) }
      }
      .padding(14)
      .background(RoundedRectangle(cornerRadius: Metrics.cardRadius, style: .continuous).fill(Palette.surface))
    }
    .buttonStyle(PressScale())
    .accessibilityLabel("\(tr("同步", "Sync")) \(card.label)")
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
