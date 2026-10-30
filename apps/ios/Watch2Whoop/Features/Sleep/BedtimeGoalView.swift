import Charts
import GRDB
import SwiftUI

/// Observes the view the page draws (BedtimeGoal.shown) and the settings it edits; refreshes from the server.
@MainActor
final class BedtimeGoalStore: ObservableObject {
  struct Snapshot: Equatable {
    var shown: BedtimeGoal.Shown
    var settings: Data?
  }

  @Published private(set) var model: BedtimeGoalModel?
  @Published private(set) var settings: [String: Any]?
  @Published private(set) var pending = false
  @Published private(set) var loading = true
  @Published private(set) var offline = false
  private var cancellable: AnyDatabaseCancellable?

  func start(_ session: AccountSession) {
    guard cancellable == nil else { return }
    cancellable = ValueObservation.tracking { db -> Snapshot in
      let s = try BedtimeGoal.settings(db)
      return Snapshot(shown: try BedtimeGoal.shown(db),
                      settings: try s.map { try JSONSerialization.data(withJSONObject: $0, options: [.sortedKeys]) })
    }
    .removeDuplicates()
    .start(in: session.db.writer, scheduling: .immediate, onError: { _ in }, onChange: { [weak self] snap in
      guard let self else { return }
      let json = snap.shown.body.flatMap { (try? JSONSerialization.jsonObject(with: $0)) as? [String: Any] }
      let m = json.flatMap(BedtimeGoalModel.init(json:))
      if m != self.model { self.model = m }
      self.settings = snap.settings.flatMap { (try? JSONSerialization.jsonObject(with: $0)) as? [String: Any] }
      self.pending = snap.shown.pending
      if m != nil { self.loading = false }
    })
  }

  func refresh(_ session: AccountSession) async {
    let out = await BedtimeGoal.refresh(api: session.api, db: session.db, queue: session.sync.queue,
                                        tz: TrendsStore.timezone(session))
    offline = out.offline
    loading = false
  }
}

/// 入睡目标 (the Today page's bedtime-goal card and sheet): tonight's target, the last 7 nights against the plan's
/// targets, the plan's numbers, and set / change / clear (local first, through the offline queue). Opened from the
/// sleep detail page and from 我; content only, the sheet or the page around it scrolls.
struct BedtimeGoalView: View {
  @EnvironmentObject private var session: AccountSession
  @StateObject private var store = BedtimeGoalStore()
  @State private var target = BedtimeGoalView.date("00:00")
  @State private var wakeOn = false
  @State private var wake = BedtimeGoalView.date("08:00")
  @State private var pace = 15
  @State private var formFor: String?
  @State private var busy = false
  @State private var message: String?
  @State private var confirmClear = false

  var body: some View {
    VStack(alignment: .leading, spacing: 0) {
      if let m = store.model {
        hero(m)
        SectionHeader(title: tr("最近 7 晚", "Last 7 nights")).padding(.top, 22).padding(.bottom, 10)
        Card { BedtimeChart(nights: m.nights, tonight: m.hasGoal && !m.paused ? m.tonight : nil) }
        tiles(m).padding(.top, 8)
      } else if store.loading {
        ProgressView().tint(Palette.text3).frame(maxWidth: .infinity).padding(.vertical, 40)
      } else if store.offline {
        Banner(text: tr("离线，连上网络后重试", "Offline, retry when connected"))
      }
      form.padding(.top, 22)
    }
    .onAppear {
      store.start(session)
      fillForm()
    }
    .onChange(of: store.settings.map { BedtimeGoalView.formKey($0) }) { _ in fillForm() }
    .task { await store.refresh(session) }
    .confirmationDialog(tr("清除入睡目标？", "Clear the bedtime goal?"), isPresented: $confirmClear, titleVisibility: .visible) {
      Button(tr("清除", "Clear"), role: .destructive) { save(nil) }
      Button(tr("取消", "Cancel"), role: .cancel) {}
    }
  }

  // MARK: - parts

  @ViewBuilder private func hero(_ m: BedtimeGoalModel) -> some View {
    Card {
      HStack(alignment: .bottom) {
        VStack(alignment: .leading, spacing: 2) {
          HStack(spacing: 6) {
            Text(tr("今晚", "Tonight")).font(Typo.label).foregroundColor(Palette.text2)
            if m.paused { ToneBadge(text: tr("已暂停", "Paused")) }
            if store.pending { ToneBadge(text: tr("待提交", "Not sent"), tone: .mid) }
          }
          Text(tonightText(m)).font(.system(size: 28, weight: .bold).monospacedDigit())
            .foregroundColor(m.tonight == nil ? Palette.text3 : Palette.sleep)
          if let w = m.tonightWake, m.tonight != nil {
            Text("\(tr("起床", "Wake")) \(w)").font(Typo.caption.monospacedDigit()).foregroundColor(Palette.text2)
          }
        }
        Spacer()
        VStack(alignment: .trailing, spacing: 2) {
          Text(tr("最终", "Final")).font(Typo.label).foregroundColor(Palette.text2)
          Text(m.target ?? "—").font(.system(size: 22, weight: .semibold).monospacedDigit())
            .foregroundColor(m.target == nil ? Palette.text3 : Palette.text)
        }
      }
    }
  }

  private func tonightText(_ m: BedtimeGoalModel) -> String {
    if let t = m.tonight { return t }
    return m.hasGoal ? "—" : tr("未设置", "Not set")
  }

  private func tiles(_ m: BedtimeGoalModel) -> some View {
    LazyVGrid(columns: Array(repeating: GridItem(.flexible(), spacing: 8), count: 3), spacing: 8) {
      MetricTile(label: tr("平均入睡", "Avg bedtime"), value: m.avg7)
      MetricTile(label: tr("平均醒来", "Avg wake"), value: m.avgWake7)
      MetricTile(label: tr("达标", "On target"), value: m.onTarget7.map { "\($0)/\(m.nights7)" }, unit: tr("晚", "nights"),
                 tone: (m.onTarget7 ?? 0) * 2 >= max(1, m.nights7) ? .good : .text)
      MetricTile(label: tr("距最终", "To final"), value: m.gap.map { TodayModel.fmt($0) }, unit: tr("分", "min"))
      MetricTile(label: tr("比计划晚", "Behind plan"), value: m.behind.map { TodayModel.fmt(max(0, $0)) }, unit: tr("分", "min"),
                 tone: (m.behind ?? 0) > 0 ? .mid : .text)
      MetricTile(label: tr("预计达成", "Expected"), value: m.eta.map { TodayModel.shortDay($0) })
      if m.hasGoal {
        MetricTile(label: tr("阶段", "Step"), value: m.stage.map { "\($0)/\(m.stages)" })
        MetricTile(label: tr("下一档", "Next step"), value: m.next.map { "\(TodayModel.shortDay($0.date)) \($0.bedtime)" })
        MetricTile(label: tr("每档", "Per step"), value: m.pace.map { "−\($0)" },
                   unit: m.stepDays.map { tr("分 / \($0) 天", "min / \($0) d") })
      }
    }
  }

  private var form: some View {
    VStack(alignment: .leading, spacing: 12) {
      RowGroup(title: tr("设置", "Settings")) {
        HStack {
          Text(tr("期望入睡", "Bedtime")).font(Typo.body).foregroundColor(Palette.text)
          Spacer()
          DatePicker("", selection: $target, displayedComponents: .hourAndMinute).labelsHidden()
            .accessibilityLabel(tr("期望入睡", "Bedtime"))
        }
        .padding(.vertical, 8)
        RowDivider(inset: 0)
        ToggleRow(title: tr("固定起床", "Fixed wake time"), isOn: $wakeOn)
        if wakeOn {
          RowDivider(inset: 0)
          HStack {
            Text(tr("起床", "Wake")).font(Typo.body).foregroundColor(Palette.text)
            Spacer()
            DatePicker("", selection: $wake, displayedComponents: .hourAndMinute).labelsHidden()
              .accessibilityLabel(tr("起床", "Wake"))
          }
          .padding(.vertical, 8)
        }
        RowDivider(inset: 0)
        VStack(alignment: .leading, spacing: 8) {
          HStack {
            Text(tr("节奏", "Pace")).font(Typo.body).foregroundColor(Palette.text)
            Spacer()
            Text(tr("每档 −\(pace) 分", "−\(pace) min per step")).font(Typo.caption.monospacedDigit()).foregroundColor(Palette.text2)
          }
          SegmentedControl(items: paceItems, selection: $pace)
        }
        .padding(.vertical, 10)
      }
      PrimaryButton(title: tr("保存", "Save"), busy: busy) { save(BedtimeGoal.body(target: Self.hhmm(target), wake: wakeOn ? Self.hhmm(wake) : nil, pace: pace)) }
      if store.settings != nil {
        SecondaryButton(title: tr("清除目标", "Clear goal"), tone: .bad) { confirmClear = true }
      }
      if let message { Text(message).font(Typo.caption).foregroundColor(Palette.text2) }
    }
  }

  private var paceItems: [(Int, String)] {
    var items: [(Int, String)] = [(10, tr("温和", "Gentle")), (15, tr("标准", "Standard")), (20, tr("积极", "Faster"))]
    if !BedtimeGoal.paces.contains(pace) { items.append((pace, tr("\(pace) 分", "\(pace) min"))) }   // set elsewhere (AI, web)
    return items
  }

  // MARK: - form state

  /// The form follows the stored settings when they change (another device, the AI), not while the user edits.
  private static func formKey(_ s: [String: Any]) -> String {
    "\(s["target_bedtime"] ?? "")|\(s["target_wake"] ?? "")|\(s["pace_min_per_step"] ?? "")"
  }

  private func fillForm() {
    guard let s = store.settings else { return }
    let key = Self.formKey(s)
    guard key != formFor else { return }
    formFor = key
    if let t = s["target_bedtime"] as? String { target = Self.date(t) }
    if let w = s["target_wake"] as? String { wakeOn = true; wake = Self.date(w) } else { wakeOn = false }
    if let p = TodayModel.num(s["pace_min_per_step"]) { pace = Int(p) }
  }

  static func date(_ hhmm: String) -> Date {
    let p = hhmm.split(separator: ":").compactMap { Int($0) }
    return Calendar.current.date(bySettingHour: p.first ?? 0, minute: p.count > 1 ? p[1] : 0, second: 0, of: Date()) ?? Date()
  }

  static func hhmm(_ d: Date) -> String {
    let c = Calendar.current.dateComponents([.hour, .minute], from: d)
    return String(format: "%02d:%02d", c.hour ?? 0, c.minute ?? 0)
  }

  // MARK: - writing

  /// `body` nil = clear. Local draft + queue entry first, then one send and a fresh view; Today's sleep coach follows.
  private func save(_ body: [String: Any]?) {
    busy = true
    message = nil
    let zone = TimeZone(identifier: TrendsStore.timezone(session)) ?? .current
    let queue = session.sync.queue
    Task {
      defer { busy = false }
      guard let cid = try? BedtimeGoal.write(session.db, queue: queue, body: body, tz: zone) else {
        message = tr("没存上，再试一次", "Not saved, try again"); return
      }
      if body == nil { formFor = nil }
      await store.refresh(session)
      let item = (try? queue.all())?.first { $0.clientId == cid }
      switch item?.state {
      case nil: message = body == nil ? tr("已清除", "Cleared") : tr("已保存", "Saved")
      case .failed, .conflict: message = item?.lastError ?? tr("没提交成功", "Not accepted by the server")
      default: message = tr("离线，已存在本机，联网后自动提交", "Offline: saved on this phone, sent when you're back online")
      }
      CoachLocal.refresh(session: session, day: LocalDay.string(Date(), zone), userToday: nil)
    }
  }
}

/// The bedtime goal as a pushed page (from the sleep detail page).
struct BedtimeGoalPage: View {
  var body: some View {
    ScrollView(.vertical, showsIndicators: false) {
      BedtimeGoalView()
        .padding(.horizontal, Metrics.gutter)
        .padding(.top, 8)
        .padding(.bottom, 32)
        .frame(maxWidth: Metrics.maxContentWidth)
        .frame(maxWidth: .infinity)
    }
    .background(Palette.bg.ignoresSafeArea())
    .navigationTitle(tr("入睡目标", "Bedtime goal"))
    .navigationBarTitleDisplayMode(.inline)
  }
}

/// The row that opens the page: tonight's target (or the final one while paused), or 「设置」 without a goal.
struct BedtimeGoalRow: View {
  @EnvironmentObject private var session: AccountSession
  @State private var model: BedtimeGoalModel?

  var body: some View {
    Card {
      HStack(spacing: 12) {
        Icon(.moon, size: 20).foregroundColor(Palette.sleep)
        Text(tr("入睡目标", "Bedtime goal")).font(Typo.body).foregroundColor(Palette.text)
        Spacer(minLength: 8)
        Text(value).font(Typo.bodyStrong.monospacedDigit()).foregroundColor(model?.hasGoal == true ? Palette.text : Palette.text2)
        Icon(.chevronRight, size: 16).foregroundColor(Palette.text3)
      }
    }
    .onAppear {
      let shown = try? session.db.writer.read { try BedtimeGoal.shown($0) }
      model = shown?.body.flatMap { (try? JSONSerialization.jsonObject(with: $0)) as? [String: Any] }.flatMap(BedtimeGoalModel.init(json:))
    }
  }

  private var value: String {
    guard let m = model, m.hasGoal else { return tr("设置", "Set") }
    if let t = m.tonight { return "\(tr("今晚", "Tonight")) \(t)" }
    return m.target ?? "—"
  }
}

/// The last 7 nights' bedtimes (dots: on target / late / no target) against each night's target (short bars); when no
/// night had a target yet, tonight's target as a dashed line. Later = lower, across midnight.
struct BedtimeChart: View {
  let nights: [BedtimeGoalModel.Night]
  let tonight: String?

  private struct Dot: Identifiable { var i: Int; var y: Double; var tone: Tone; var id: Int { i } }
  private struct Tick: Identifiable { var i: Int; var y: Double; var id: Int { i } }

  var body: some View {
    let dots = nights.enumerated().compactMap { i, n in
      BedtimeGoalModel.offset18(n.bedtime).map { Dot(i: i, y: $0, tone: n.onTarget.map { $0 ? .good : .mid } ?? .text) }
    }
    let ticks = nights.enumerated().compactMap { i, n in BedtimeGoalModel.offset18(n.target).map { Tick(i: i, y: $0) } }
    let ref = ticks.isEmpty ? BedtimeGoalModel.offset18(tonight) : nil
    let all = dots.map(\.y) + ticks.map(\.y) + (ref.map { [$0] } ?? [])
    VStack(spacing: 4) {
      if let lo = all.min(), let hi = all.max() {
        Chart {
          if let ref {
            RuleMark(y: .value("m", -ref)).foregroundStyle(Palette.text3).lineStyle(StrokeStyle(lineWidth: 1, dash: [3, 3]))
              .annotation(position: .top, alignment: .trailing) {
                Text("\(tr("今晚", "Tonight")) \(tonight ?? "")").font(.system(size: 9)).foregroundColor(Palette.text3)
              }
          }
          ForEach(ticks) { t in
            RuleMark(xStart: .value("n", Double(t.i) - 0.32), xEnd: .value("n", Double(t.i) + 0.32), y: .value("m", -t.y))
              .foregroundStyle(Palette.text3).lineStyle(StrokeStyle(lineWidth: 2, lineCap: .round))
          }
          ForEach(dots) { d in
            PointMark(x: .value("n", Double(d.i)), y: .value("m", -d.y))
              .foregroundStyle(d.tone == .text ? Palette.sleep : d.tone.color).symbolSize(70)
          }
        }
        .chartXScale(domain: -0.5...6.5)
        .chartYScale(domain: (-(hi + 20))...(-(lo - 20)))
        .chartXAxis(.hidden)
        .chartYAxis {
          AxisMarks(position: .trailing, values: .automatic(desiredCount: 3)) { v in
            AxisGridLine().foregroundStyle(Palette.hair)
            AxisValueLabel { Text(Self.clock(-(v.as(Double.self) ?? 0))).font(.system(size: 10).monospacedDigit()).foregroundColor(Palette.text3) }
          }
        }
        .frame(height: 140)
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(tr("最近 7 晚入睡时间和目标", "Bedtimes of the last 7 nights against the targets"))
      } else {
        Text("—").font(Typo.bodyStrong).foregroundColor(Palette.text3).frame(maxWidth: .infinity, minHeight: 60)
      }
      HStack {
        Text(TodayModel.shortDay(nights.first?.night))
        Spacer()
        Text(TodayModel.shortDay(nights.last?.night))
      }
      .font(.system(size: 10).monospacedDigit())
      .foregroundColor(Palette.text3)
      .accessibilityHidden(true)
    }
  }

  /// Minutes after 18:00 → "HH:MM".
  static func clock(_ m: Double) -> String { BedtimeGoal.fmt(Int(m.rounded()) - 360) }
}
