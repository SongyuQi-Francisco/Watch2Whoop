import SwiftUI

/// What 今日详情 reads: my own day (`TodayStore`, the local database) or someone else's (`MemberDayStore`, memory only).
@MainActor
protocol TodayDaySource: ObservableObject {
  var model: TodayModel { get }
  var hasData: Bool { get }
  func refresh() async
}

extension TodayStore: TodayDaySource {}

/// The controls of 今日详情 that write, or lead to my own data (unit-tested in `MemberViewTests`): on my own day 记体重 (the ＋
/// on 体重 today, the weight card) and the sync nudges; a past day has neither the ＋ nor the nudges; someone else's day
/// (`memberView` set) none of them.
struct DayWrites: Equatable {
  var logWeight: Bool
  var weightCard: Bool
  var syncNudges: Bool

  static func of(isPast: Bool, member: Bool) -> DayWrites {
    DayWrites(logWeight: !isPast && !member, weightCard: !member, syncNudges: !isPast && !member)
  }
}

/// What 今日详情 asks of the page that owns the stack (`TodayView`, `MemberScreen`; their state stays there).
struct TodayDayActions {
  /// The next level and the door it grows out of (`.zoomDoor`).
  var push: (LevelRoute, String?) -> Void
  /// Up one level (the small dial): the first glance.
  var up: () -> Void
  var sheet: (TodaySheet) -> Void
  var calendar: () -> Void
  var backToToday: () -> Void
  var step: (Int) -> Void
  var training: () -> Void
}

/// Level 2 of 今天, 今日详情 (docs/design/2.0.1/drilldown `c-day`): every number of the day on one page. The dial has shrunk
/// into the header (it is the way up), the ladder shows two of three. Then 训练 · 今晚 · 提醒 as chips, a jump rail with one
/// icon per category, every metric of the day in 2.0.0's groups and order (each row a door to its metric page; 体重 also
/// has ＋ = 记体重), 当前状态, 需要注意, 体征, 今日核心 and the saved 身体状态 cards (lab history). A swipe changes the day.
///
/// Someone else's 今日详情 (`MemberScreen`, `memberView` set) is the same page, read-only: no sync nudges, no ＋ on 体重,
/// their weight card opens its explanation; what their share leaves out is simply not there.
struct TodayDayPage<Source: TodayDaySource>: View {
  let anchor: String?
  @ObservedObject var store: Source
  let run: TodayDayActions
  /// After the day: the slim look's quote (flag `slim_mode`) on my own page, the doors to their records on someone else's.
  var footer: AnyView?
  @Environment(\.dismiss) private var dismiss
  @Environment(\.memberView) private var member
  @State private var jumped: String?

  private var m: TodayModel { store.model }
  private var writes: DayWrites { .of(isPast: m.isPast, member: member != nil) }

  var body: some View {
    ScrollViewReader { proxy in
      ScrollView(.vertical, showsIndicators: false) {
        LazyVStack(alignment: .leading, spacing: 0) {
          header
          if writes.syncNudges {
            SyncNudges(card: m.sync, coverage: m.coverage) { await store.refresh() }.padding(.top, 8)
          }
          chips
          content(proxy)
          if let footer { footer.padding(.top, 22) }
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
      .refreshable { await store.refresh() }
      .modifier(DaySwipe(step: run.step, edge: LevelParams.edgeBack))
      .toolbar(.hidden, for: .navigationBar)
      .onAppear {
        guard let anchor else { return }
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.35) { withAnimation { proxy.scrollTo(anchor, anchor: .top) } }
      }
    }
  }

  // MARK: header: back · the small dial (= up) · the date (= the month calendar) · ladder (2 of 3)

  private var header: some View {
    let d = m.glance.recovery
    return LevelHeader(level: 2, back: { dismiss() }, up: run.up,
                       upLabel: "\(tr("恢复", "Recovery")) \(d.value.map { TodayModel.fmt($0) } ?? "—")") {
      MiniDial(value: d.value, tone: d.tone)
    } title: {
      Button(action: run.calendar) {
        Text(TodayGlancePage.dateTitle(m.date)).font(Typo.header).foregroundColor(Palette.text).lineLimit(1)
          .frame(minHeight: LevelParams.hitSide).contentShape(Rectangle())
      }
      .buttonStyle(PressScale())
      .accessibilityLabel("\(TodayGlancePage.dateTitle(m.date)), \(tr("选择日期", "Pick a day"))")
      if m.isPast {
        Button(action: run.backToToday) { Chip(label: tr("回到今天", "Today")) }.buttonStyle(PressScale())
      }
    } trailing: {
      EmptyView()
    }
    .accessibilityActions {
      Button(tr("前一天", "Previous day")) { run.step(-1) }
      if m.isPast { Button(tr("后一天", "Next day")) { run.step(1) } }
    }
  }

  // MARK: 训练 · 今晚 · 提醒 (each only when it has something)

  @ViewBuilder private var chips: some View {
    let newReminders = m.reminders.filter { $0.status == "new" }.count
    if m.training != nil || m.tonight?.bedtime != nil || !m.reminders.isEmpty {
      HStack(spacing: 8) {
        if let t = m.training {
          let planned = t.logged.isEmpty && !t.planned.isEmpty
          LevelChip(icon: .dumbbell, number: t.restDay ? tr("休息", "Rest") : "\(planned ? t.planned.count : t.logged.count)",
                    unit: t.restDay ? nil : (planned ? tr("项", "planned") : tr("个动作", "moves")),
                    label: tr("训练", "Training"), action: run.training)
        }
        if let b = m.tonight?.bedtime {
          LevelChip(icon: .moon, number: b, label: tr("今晚", "Tonight")) { run.sheet(.detail(.sleepCoach)) }
        }
        if !m.reminders.isEmpty {
          LevelChip(icon: .bell, number: "\(newReminders > 0 ? newReminders : m.reminders.count)",
                    dot: m.reminders.first { $0.status == "new" }?.tone == .bad ? .bad : (newReminders > 0 ? .mid : nil),
                    label: tr("提醒", "Reminders")) { run.sheet(.reminders) }
        }
      }
      .padding(.top, 8)
    }
  }

  // MARK: the day's metrics, status cards and the blocks of 2.0.0's day

  @ViewBuilder private func content(_ proxy: ScrollViewProxy) -> some View {
    let groups = m.trendsDay.map { LevelRows.day($0.data, overrides: m.levelOverrides, onlyPresent: member != nil) } ?? []
    let tiles = m.trendsDay?.status.tiles ?? []
    let rail = groups.map { JumpRail.Item(id: $0.id, title: $0.title, count: $0.rows.count) }
      + (tiles.isEmpty ? [] : [JumpRail.Item(id: "status", title: tr("当前状态", "Current status"), count: tiles.count)])
    if !rail.isEmpty {
      JumpRail(items: rail, selected: jumped) { id in
        jumped = id
        withAnimation { proxy.scrollTo("group.\(id)", anchor: .top) }
      }
      .padding(.top, 14)
    } else if m.trendsDay == nil {
      Text(store.hasData ? tr("这天没有指标", "No metrics for this day") : " ").font(Typo.body).foregroundColor(Palette.text3)
        .frame(maxWidth: .infinity).padding(.vertical, 40)
    }
    ForEach(groups) { g in
      LevelGroupHeader(icon: LevelGroup.icon(g.id), title: g.title, count: g.rows.count).padding(.top, 8).id("group.\(g.id)")
      LevelRowList(rows: g.rows, door: "day.row", open: { run.push(.metric($0.id), "day.row.\($0.id)") },
                   extra: writes.logWeight ? ["weight": (tr("记体重", "Log weight"), { run.sheet(.weight) })] : [:])
    }
    if !tiles.isEmpty {
      LevelGroupHeader(icon: .circleGauge, title: tr("当前状态", "Current status"), count: tiles.count).padding(.top, 8).id("group.status")
      LazyVGrid(columns: [GridItem(.flexible(), spacing: 8), GridItem(.flexible(), spacing: 8)], spacing: 8) {
        ForEach(tiles) { t in StatusTileView(tile: t) { run.sheet(.status(t)) } }
      }
      .padding(.top, 10)
    }
    if !m.attention.isEmpty {
      LevelGroupHeader(icon: .circleAlert, title: tr("需要注意", "Needs attention"), count: m.attention.count).padding(.top, 14).id("attention")
      AttentionList(items: m.attention, door: "day.attention") { open($0.open, door: "day.attention.\($0.id)") }.padding(.top, 10)
    }
    if !m.vitals.isEmpty {
      let c = TodayGlanceRules.vitalsCount(m.vitals)
      LevelGroupHeader(icon: .heartPulse, title: tr("体征", "Vitals"), count: nil).padding(.top, 14).id("vitals")
        .overlay(alignment: .trailing) {
          Text("\(c.ok)/\(c.total)").font(.system(size: 12, weight: .medium).monospacedDigit()).foregroundColor(Palette.text2)
        }
      VStack(alignment: .leading, spacing: 14) {
        ForEach(m.vitals) { v in
          MonitorRow(vital: v) { k in metricOrExplain(k, v.name, door: "day.vital.\(v.id)") }.zoomDoor("day.vital.\(v.id)")
        }
      }
      .padding(.top, 12)
    }
    if !m.core.isEmpty {
      LevelGroupHeader(icon: .zap, title: tr("今日核心", "Today's core"), count: nil).padding(.top, 14).id("core")
      LazyVGrid(columns: [GridItem(.flexible(), spacing: 8), GridItem(.flexible(), spacing: 8)], spacing: 8) {
        ForEach(m.core) { c in CoreCardView(card: c) { open(c.open, door: "day.core.\(c.id)") }.zoomDoor("day.core.\(c.id)") }
      }
      .padding(.top, 10)
    }
    if !m.isPast && !m.status.isEmpty {
      LevelGroupHeader(icon: .fileText, title: tr("身体状态", "Body status"), count: nil).padding(.top, 14).id("profile")
      VStack(alignment: .leading, spacing: 10) {
        ForEach(m.status) { g in
          if m.status.count > 1, !g.name.isEmpty {
            Text(g.name).font(Typo.label).foregroundColor(Palette.text3).padding(.top, 2)
          }
          LazyVGrid(columns: [GridItem(.flexible(), spacing: 8), GridItem(.flexible(), spacing: 8)], spacing: 8) {
            ForEach(g.items) { it in StatusCell(item: it) { openStatus(it) } }
          }
        }
      }
      .padding(.top, 10)
    }
  }

  // MARK: where the blocks' taps go

  /// An attention item or a core card: a metric of the day opens its page, a status card its sheet.
  private func open(_ o: TodayOpen, door: String) {
    switch o {
    case .coach: run.push(.metric("strain"), door)
    case .metric(let k, let t): metricOrExplain(k, t, door: door)
    case .status(let t): run.sheet(.status(t))
    }
  }

  private func metricOrExplain(_ key: String, _ title: String, door: String) {
    // someone else's metric that their day's document does not carry is not shared: its explanation, not an empty page
    let notShared = member != nil && MetricCatalog.series(key).map { m.trendsDay?.data.series[$0] == nil } == true
    if MetricCatalog.conf(key) != nil && !notShared { run.push(.metric(key), door) }
    else { run.sheet(.explain(kind: "metric", key: key, title: title)) }
  }

  /// A saved status card: weight = 记体重 (someone else's: its explanation), a lab value = its history, anything else its
  /// explanation.
  private func openStatus(_ it: TodayModel.StatusItem) {
    if it.key == "weight" && writes.weightCard { run.sheet(.weight) }
    else if let lab = it.labKey { run.sheet(.detail(.lab(key: lab, title: it.label))) }
    else { run.sheet(.explain(kind: "status", key: it.key, title: it.label)) }
  }
}
