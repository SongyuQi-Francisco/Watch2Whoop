import SwiftUI

// The sheets of TodayDetails.swift and the Today tab's small additions (the coach card, the day buttons and swipe).
// Numbers, charts and short labels; the words stay in the explain sheet at the bottom.

/// The body of a detail sheet.
struct TodayDetailBody: View {
  let detail: TodayDetail
  /// The day Today shows (nil = not known yet).
  let date: String?
  /// That day when it is a look-back day (explain sheets take nil for today).
  let past: String?
  let coach: SleepCoachModel?
  let open: (TodaySheet) -> Void
  let close: () -> Void

  var body: some View {
    switch detail {
    case .stress:
      if let date { StressDetailView(day: date, past: past) }
    case .sleepCoach:
      SleepCoachDetailView(coach: coach, past: past) { open(.detail(.bedtimeGoal)) }
    case .bodyAge:
      BodyAgeView()
    case .bedtimeGoal:
      BedtimeGoalView()
    case .sleepHelp:
      SleepHelpView(close: close)
    case .lab(let key, _):
      LabDetail(key: key)
    }
  }
}

// MARK: - stress

extension ScaleBar {
  static func stress(_ v: Double?) -> ScaleBar {
    ScaleBar(value: v, lo: 0, hi: 3,
             segments: [Segment(to: TodayParams.stressMedium, color: Palette.good.opacity(0.55)),
                        Segment(to: TodayParams.stressHigh, color: Palette.mid.opacity(0.55)), Segment(to: 3, color: Palette.bad.opacity(0.55))],
             labels: [(0, "0"), (1, "1"), (2, "2"), (3, "3")])
  }
}

struct StressDetailView: View {
  let day: String
  let past: String?
  @EnvironmentObject private var session: AccountSession
  @StateObject private var store = DetailStore<StressDayModel>()

  var body: some View {
    VStack(alignment: .leading, spacing: 0) {
      if let m = store.model {
        content(m)
      } else if store.loading {
        ProgressView().tint(Palette.text3).frame(maxWidth: .infinity).padding(.vertical, 24)
      } else {
        KVRow(label: tr("压力", "Stress"), value: nil, badge: store.offline ? (tr("离线", "Offline"), .text) : nil)
      }
      ExplainBody(kind: "metric", key: "stress", day: past, head: false).padding(.top, 8)
    }
    .onAppear {
      let day = self.day
      store.start(session, fetch: StressDetail.fetch(day), parse: StressDayModel.init(json:)) { try StressDetail.read($0, day: day) }
    }
  }

  @ViewBuilder private func content(_ m: StressDayModel) -> some View {
    if let score = m.score {
      ScaleBar.stress(score).padding(.vertical, 8)
      KVRow(label: tr("分数", "Score"), value: TodayModel.fmt(score, digits: 1), unit: "/ 3", badge: m.levelLabel.map { ($0, m.tone) })
      if m.hours.count > 1 { StressHourChart(values: m.curve).padding(.top, 12) }
      if m.totalHours > 0 { StressHoursBar(model: m).padding(.top, 12) }
      if let p = m.peakHour { KVRow(label: tr("峰值", "Peak"), value: "\(p):00") }
      if let c = m.calmHr { KVRow(label: tr("平静心率", "Calm HR"), value: TodayModel.fmt(c), unit: "bpm") }
    } else {
      KVRow(label: tr("压力", "Stress"), value: nil, badge: m.reason.map { ($0, .text) })
    }
  }
}

/// The day's hourly stress on a 0–3 axis (app.js stressHtml's curve), midnight to midnight.
struct StressHourChart: View {
  let values: [Double?]

  var body: some View {
    let spec = ChartSpec(id: "stress.hours", dates: (0..<24).map { "\($0):00" },
                         layers: [ChartLayer(kind: .avg, values: values, tone: .text, main: true)], min: 0, max: 3,
                         zone: 0...TodayParams.stressMedium, height: 90, label: tr("每小时压力", "Hourly stress"), xAxis: false)
    VStack(spacing: 2) {
      TrendChartBody(spec: spec, selection: ChartSelection()).equatable()
      HStack {
        Text("0:00"); Spacer(); Text("12:00"); Spacer(); Text("23:00")
      }
      .font(.system(size: 10).monospacedDigit()).foregroundColor(Palette.text3)
    }
  }
}

/// High / medium / low hours as one bar, the hours under it (app.js .hbar).
struct StressHoursBar: View {
  let model: StressDayModel

  private var parts: [(String, Double, Color)] {
    [(tr("高", "High"), model.highHours ?? 0, Palette.bad), (tr("中", "Medium"), model.mediumHours ?? 0, Palette.mid),
     (tr("低", "Low"), model.lowHours ?? 0, Palette.good)]
  }

  var body: some View {
    VStack(alignment: .leading, spacing: 6) {
      GeometryReader { g in
        HStack(spacing: 2) {
          ForEach(Array(parts.enumerated()), id: \.offset) { _, p in
            if p.1 > 0 { Capsule().fill(p.2).frame(width: max(2, g.size.width * p.1 / model.totalHours - 2)) }
          }
        }
      }
      .frame(height: 6)
      .accessibilityHidden(true)
      HStack(spacing: 14) {
        ForEach(Array(parts.enumerated()), id: \.offset) { _, p in
          Text("\(TodayModel.fmt(p.1, digits: 1)) h \(p.0)").font(.system(size: 11).monospacedDigit()).foregroundColor(Palette.text2)
        }
      }
    }
  }
}

// MARK: - sleep coach

/// The coach on the Today tab: when to go to bed tonight, how long to sleep, when to get up, how much is missing.
struct SleepCoachCard: View {
  let coach: SleepCoachModel
  let isPast: Bool
  let action: () -> Void

  var body: some View {
    Button(action: action) {
      VStack(alignment: .leading, spacing: 4) {
        HStack {
          Text(isPast ? tr("当晚入睡", "That night's bedtime") : tr("今晚入睡", "Tonight's bedtime")).font(Typo.label).foregroundColor(Palette.text2).lineLimit(1)
          Spacer(minLength: 4)
          Icon(.chevronRight, size: 14).foregroundColor(Palette.text3)
        }
        HStack(alignment: .firstTextBaseline, spacing: 10) {
          Text(coach.bedtime ?? "—").font(.system(size: 22, weight: .semibold).monospacedDigit())
            .foregroundColor(coach.bedtime == nil ? Palette.text3 : Palette.sleep)
          if let l = coach.line {
            Text(l).font(.system(size: 12).monospacedDigit()).foregroundColor(Palette.text2).lineLimit(1).minimumScaleFactor(0.75)
          }
        }
      }
      .frame(maxWidth: .infinity, alignment: .leading)
      .padding(14)
      .background(RoundedRectangle(cornerRadius: Metrics.cardRadius, style: .continuous).fill(Palette.surface))
    }
    .buttonStyle(PressScale())
    .accessibilityElement(children: .combine)
  }
}

struct SleepCoachDetailView: View {
  let coach: SleepCoachModel?
  /// The look-back day (nil = today).
  let past: String?
  let openGoal: () -> Void

  var body: some View {
    VStack(alignment: .leading, spacing: 0) {
      KVRow(label: past != nil ? tr("当晚入睡", "That night's bedtime") : tr("今晚入睡", "Tonight's bedtime"), value: coach?.bedtime,
            badge: coach?.fromGoal == true ? (tr("目标", "Goal"), .sleep) : nil)
      KVRow(label: tr("需要睡", "Sleep need"), value: coach?.need.map(TodayModel.hm))
      KVRow(label: tr("起床", "Wake"), value: coach?.wake)
      if let s = coach?.shortfall { KVRow(label: tr("还差", "Short by"), value: TodayModel.hm(s), tone: .mid) }
      KVRow(label: tr("睡眠债", "Sleep debt"), value: coach?.debt.map(TodayModel.hm))
      KVRow(label: tr("入睡目标", "Bedtime goal"), value: "", action: openGoal)
      ExplainBody(kind: "metric", key: "sleep_need", day: past, head: false).padding(.top, 8)
    }
  }
}

// MARK: - the empty recovery ring's next step

/// The steps to switch on sleep tracking on the watch (the person does it; data-status.js sleepTrackingSteps) and when sleep
/// last arrived.
struct SleepHelpView: View {
  let close: () -> Void
  @EnvironmentObject private var app: AppModel
  @EnvironmentObject private var session: AccountSession
  @State private var sleep: DataStatus.Kind?

  static var steps: [String] {
    [tr("在 iPhone 上打开「手表」app，点「睡眠」", "On your iPhone open the Watch app and tap Sleep"),
     tr("打开睡眠追踪（用 Apple Watch 追踪睡眠）", "Turn on sleep tracking (Track Sleep with Apple Watch)"),
     tr("睡前给手表充好电，睡觉时戴着它", "Charge the watch before bed and wear it while you sleep"),
     tr("醒来后打开一次 Watch2Whoop，睡眠就会同步上来", "After waking up, open Watch2Whoop once and your sleep syncs")]
  }

  var body: some View {
    VStack(alignment: .leading, spacing: 14) {
      if let s = sleep { KVRow(label: s.label, value: DataStatusView.since(s)) }
      VStack(alignment: .leading, spacing: 10) {
        ForEach(Array(Self.steps.enumerated()), id: \.offset) { i, s in
          HStack(alignment: .firstTextBaseline, spacing: 8) {
            Text("\(i + 1)").font(Typo.bodyStrong.monospacedDigit()).foregroundColor(Palette.text2)
            Text(s).font(Typo.body).foregroundColor(Palette.text).fixedSize(horizontal: false, vertical: true)
          }
        }
      }
      PrimaryButton(title: tr("同步中心", "Sync center")) {
        close()
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.4) { app.route = .syncCenter }   // the sheet goes first
      }
    }
    .task {
      if let s = try? await session.api.send(.get(Endpoint.dataStatus), as: DataStatus.self) { sleep = s.types.first { $0.key == "sleep" } }
    }
  }
}

// MARK: - body age

/// 身体年龄: age against the calendar, pace of aging, the weekly history, each item's years by domain (app.js healthspanSheet).
/// Content only; the sheet around it scrolls. Opened from Jarvis' card, from Trends' body-age card and from 我.
struct BodyAgeView: View {
  @EnvironmentObject private var session: AccountSession
  @StateObject private var store = DetailStore<BodyAgeModel>()

  var body: some View {
    VStack(alignment: .leading, spacing: 0) {
      if let m = store.model {
        content(m)
      } else if store.loading {
        ProgressView().tint(Palette.text3).frame(maxWidth: .infinity).padding(.vertical, 40)
      } else {
        KVRow(label: tr("身体年龄", "Body age"), value: nil, badge: store.offline ? (tr("离线", "Offline"), .text) : nil)
      }
      ExplainBody(kind: "metric", key: "body_age", day: nil, head: false).padding(.top, 8)
    }
    .onAppear {
      store.start(session, fetch: BodyAgeDetail.fetch, parse: BodyAgeModel.init(json:)) { try BodyAgeDetail.read($0) }
    }
  }

  @ViewBuilder private func content(_ m: BodyAgeModel) -> some View {
    HStack(spacing: 8) {
      MetricTile(label: tr("身体年龄", "Body age"), value: m.bodyAge.map { TodayModel.fmt($0, digits: 1) }, unit: tr("岁", "y"))
      MetricTile(label: tr("比实际", "vs actual"), value: m.delta.map { BodyAgeModel.signed($0) }, unit: tr("岁", "y"),
                 tone: (m.delta ?? 0) > 0 ? .bad : (m.delta ?? 0) < 0 ? .good : .text)
      MetricTile(label: tr("衰老速度", "Pace of aging"), value: m.pace.map { BodyAgeModel.signed($0) + "×" }, tone: BodyAgeModel.paceTone(m.pace))
    }
    KVRow(label: tr("实际年龄", "Actual age"), value: m.chronoAge.map { TodayModel.fmt($0, digits: 1) }, unit: tr("岁", "y")).padding(.top, 4)
    if m.history.count >= 2 { BodyAgeHistory(weeks: m.history).padding(.top, 14) }
    ForEach(m.groups) { g in
      Text(g.title).font(Typo.section).foregroundColor(Palette.text2).padding(.top, 18).padding(.bottom, 8)
        .accessibilityAddTraits(.isHeader)
      VStack(alignment: .leading, spacing: 12) {
        ForEach(g.items) { ContributorRow(item: $0, scale: m.scale) }
      }
    }
    if !m.insufficient.isEmpty {
      Text(tr("数据不足", "Not enough data")).font(Typo.section).foregroundColor(Palette.text2).padding(.top, 18).padding(.bottom, 8)
      FlowLayout(spacing: 6) { ForEach(m.insufficient, id: \.self) { ToneBadge(text: $0) } }
    }
  }
}

/// Weekly body age, oldest to newest.
struct BodyAgeHistory: View {
  let weeks: [BodyAgeModel.Week]

  var body: some View {
    let spec = ChartSpec(id: "body_age.history", dates: weeks.map(\.date),
                         layers: [ChartLayer(kind: .avg, values: weeks.map(\.age), tone: .text, main: true)], min: nil, max: nil,
                         height: 80, label: tr("身体年龄每周历史", "Weekly body age history"), xAxis: false)
    VStack(spacing: 2) {
      TrendChartBody(spec: spec, selection: ChartSelection()).equatable()
      HStack {
        Text(TodayModel.shortDay(weeks.first?.date)); Spacer(); Text(TodayModel.shortDay(weeks.last?.date))
      }
      .font(.system(size: 10).monospacedDigit()).foregroundColor(Palette.text3)
    }
  }
}

/// One item: its label and reading, its years, a bar growing either way from the middle.
struct ContributorRow: View {
  let item: BodyAgeModel.Contributor
  let scale: Double

  var body: some View {
    VStack(alignment: .leading, spacing: 5) {
      HStack(alignment: .firstTextBaseline, spacing: 6) {
        VStack(alignment: .leading, spacing: 1) {
          Text(item.label).font(Typo.body).foregroundColor(Palette.text).fixedSize(horizontal: false, vertical: true)
          if let v = item.value { Text(v).font(.system(size: 11).monospacedDigit()).foregroundColor(Palette.text3) }
        }
        if item.partial { ToneBadge(text: tr("记录可能不全", "May be incomplete"), tone: .mid) }
        Spacer(minLength: 6)
        Text(tr("\(BodyAgeModel.signed(item.years)) 年", "\(BodyAgeModel.signed(item.years)) y"))
          .font(Typo.bodyStrong.monospacedDigit()).foregroundColor(item.tone == .text ? Palette.text2 : item.tone.color)
      }
      DiffBar(fraction: max(-1, min(1, item.years / scale)), tone: item.tone)
    }
    .accessibilityElement(children: .combine)
  }
}

// MARK: - day buttons and swipe

extension TodayStore {
  /// The day `delta` away from the one shown (`from` nil = today). Never past the user's today.
  nonisolated static func stepTarget(from: String?, today: String?, delta: Int) -> String? {
    guard let cur = from ?? today else { return nil }
    let to = LocalDay.add(cur, delta)
    if let today, to > today { return nil }
    return to
  }

  func step(_ delta: Int) {
    if let to = Self.stepTarget(from: date, today: userToday, delta: delta) { select(to) }
  }
}

/// ‹ › beside the date (the page's day-prev / day-next). The next day only on a look-back day.
struct DayNavButtons: View {
  let canGoNext: Bool
  let step: (Int) -> Void

  var body: some View {
    HStack(spacing: 6) {
      button(.chevronLeft, enabled: true, label: tr("前一天", "Previous day")) { step(-1) }
      button(.chevronRight, enabled: canGoNext, label: tr("后一天", "Next day")) { step(1) }
    }
  }

  private func button(_ icon: Icon.Name, enabled: Bool, label: String, run: @escaping () -> Void) -> some View {
    Button(action: run) {
      Icon(icon, size: 16).foregroundColor(enabled ? Palette.text2 : Palette.text3)
        .frame(width: 32, height: 32)
        .background(Circle().fill(Palette.surface))
    }
    .buttonStyle(PressScale())
    .disabled(!enabled)
    .accessibilityLabel(label)
  }
}

/// Swipe right for the previous day, left for the next (app.js: at least 70 pt, mostly sideways). Vertical scrolling wins.
struct DaySwipe: ViewModifier {
  let step: (Int) -> Void

  func body(content: Content) -> some View {
    content.simultaneousGesture(DragGesture(minimumDistance: 40).onEnded { v in
      guard abs(v.translation.width) > 70, abs(v.translation.height) < abs(v.translation.width) * 0.6 else { return }
      step(v.translation.width > 0 ? -1 : 1)
    })
  }
}
