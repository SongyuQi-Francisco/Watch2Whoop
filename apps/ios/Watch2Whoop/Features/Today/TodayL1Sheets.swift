import SwiftUI

/// The L1 sheets of Today (docs/design/2.0.1 `02-today-open`, `03-strain-open`, `04-sleep-open`): one chart, at most four
/// rows, two exits (全部指标 · 问 Jarvis), 「非诊断」 in the title row. Rows open the metric's explain sheet.
struct TodayL1Sheet: View {
  let kind: TodayL1
  let model: TodayModel
  let session: AccountSession
  let close: () -> Void
  /// A row: the metric's explain sheet (key, title); the training state row opens the load sheet.
  let explain: (String, String) -> Void
  let load: () -> Void
  /// The attention sheet: one of its items, and 体征 (the health monitor).
  let openItem: (TodayOpen) -> Void
  let monitor: () -> Void
  /// 全部指标.
  let allMetrics: (TodayL1) -> Void
  @Environment(\.askAI) private var askAI

  var body: some View {
    VStack(alignment: .leading, spacing: 0) {
      titleRow
      ScrollView(.vertical, showsIndicators: false) {
        VStack(alignment: .leading, spacing: 0) {
          switch kind {
          case .recovery: recovery
          case .strain: strain
          case .sleep: TodaySleepL1(session: session, date: model.sleepDate, explain: explain)
          case .attention: attention
          }
          exits.padding(.top, 16)
        }
        .padding(.horizontal, Metrics.gutter)
        .padding(.bottom, 24)
      }
    }
    .background(Palette.sheet.ignoresSafeArea())
    .sheetSurface()
  }

  private var title: String {
    switch kind {
    case .recovery: return tr("恢复", "Recovery")
    case .strain: return tr("负荷", "Strain")
    case .sleep: return tr("睡眠", "Sleep")
    case .attention: return tr("关注", "Attention")
    }
  }

  private var titleRow: some View {
    HStack(spacing: 10) {
      Text(title).font(.system(size: 20, weight: .semibold)).foregroundColor(Palette.text)
      DisclaimerTag(kind: .medical)
      Spacer()
      Button(action: close) {
        Icon(.x, size: 18).foregroundColor(Palette.text2)
          .frame(width: 32, height: 32)
          .background(Circle().fill(Palette.inSheet))
          .frame(width: 44, height: 44)
          .contentShape(Rectangle())
      }
      .buttonStyle(PressScale())
      .accessibilityLabel(tr("关闭", "Close"))
    }
    .padding(.leading, Metrics.gutter).padding(.trailing, Metrics.gutter - 6).padding(.top, 14).padding(.bottom, 4)
  }

  // MARK: recovery: 30 days on your usual band + HRV, resting HR, breathing

  @ViewBuilder private var recovery: some View {
    let g = model.glance
    if let u = g.recovery.usual {
      HStack(spacing: 6) {
        RoundedRectangle(cornerRadius: 1).fill(Palette.band)
          .overlay(RoundedRectangle(cornerRadius: 1).stroke(Palette.bandLine, style: StrokeStyle(lineWidth: 1, dash: [2, 2])))
          .frame(width: 14, height: 8)
        Text("\(tr("常态", "Usual")) \(TodayModel.fmt(u.lowerBound))–\(TodayModel.fmt(u.upperBound))")
          .font(.system(size: 12, weight: .medium).monospacedDigit()).foregroundColor(Palette.text2)
      }
      .padding(.top, 6)
    }
    if !g.month.isEmpty {
      UsualBars(values: g.month, usual: g.recovery.usual, tone: g.recovery.tone).frame(height: 76).padding(.top, 10)
      DateLabels(dates: g.monthDates).padding(.top, 6)
    }
    rows(g.recoveryRows).padding(.top, 12)
  }

  // MARK: strain: today's number against the target + steps, active energy, exercise, training state

  @ViewBuilder private var strain: some View {
    let s = model.glance.strain
    HStack(alignment: .firstTextBaseline, spacing: 6) {
      Text(s.text ?? "—").font(.system(size: 40, weight: .light).monospacedDigit()).foregroundColor(Palette.text)
      Text(model.isPast ? tr("全天", "Day") : tr("现在", "Now")).font(Typo.label).foregroundColor(Palette.text2)
      Spacer()
      if let t = s.target {
        Text(tr("目标", "Target")).font(Typo.label).foregroundColor(Palette.text2)
        Text("\(TodayModel.fmt(t.lowerBound, digits: 1))–\(TodayModel.fmt(t.upperBound, digits: 1))")
          .font(.system(size: 22, weight: .regular).monospacedDigit()).foregroundColor(Palette.text)
      }
    }
    .padding(.top, 4)
    BulletGauge(value: s.value, max: s.max, target: s.target, step: 1, majors: s.majors, tone: s.tone).padding(.top, 14)
    HStack {
      ForEach(["0", "7", "14", "21"], id: \.self) { l in
        Text(l).font(Typo.axis).foregroundColor(Palette.text2)
        if l != "21" { Spacer() }
      }
    }
    .padding(.top, 4)
    rows(model.glance.strainRows).padding(.top, 12)
  }

  // MARK: attention: what is off on the day (worst first), then 体征

  @ViewBuilder private var attention: some View {
    AttentionList(items: model.attention) { openItem($0.open) }.padding(.top, 8)
    if !model.vitals.isEmpty {
      let c = TodayGlanceRules.vitalsCount(model.vitals)
      Button(action: monitor) {
        HStack(spacing: 10) {
          Icon(.heartPulse, size: 16).foregroundColor(Palette.text2)
          Text(tr("体征", "Vitals")).font(.system(size: 15, weight: .medium)).foregroundColor(Palette.text2)
          Spacer()
          Text("\(c.ok)/\(c.total)").font(Typo.row).foregroundColor(Palette.text)
          Icon(.chevronRight, size: 13).foregroundColor(Palette.text3)
        }
        .frame(minHeight: 46)
        .contentShape(Rectangle())
      }
      .buttonStyle(PressScale())
      .overlay(alignment: .bottom) { Rectangle().fill(Palette.hair).frame(height: 1) }
      .padding(.top, 8)
    }
  }

  // MARK: rows and exits

  private func rows(_ list: [TodayGlance.Row]) -> some View {
    VStack(spacing: 0) {
      ForEach(list) { r in
        L1RowView(row: r) {
          if r.id == "tsb" { load() } else if let e = r.explain { explain(e.key, e.title) }
        }
      }
    }
  }

  private var exits: some View {
    HStack(spacing: 12) {
      Button { allMetrics(kind) } label: {
        HStack(spacing: 4) {
          Text(tr("全部指标", "All metrics")).font(.system(size: 16, weight: .semibold))
          Icon(.chevronRight, size: 14).foregroundColor(Palette.text2)
        }
        .foregroundColor(Palette.text)
        .frame(maxWidth: .infinity, minHeight: 50)
        .background(Capsule().fill(Palette.inSheet))
      }
      .buttonStyle(PressScale())
      Button { close(); DispatchQueue.main.asyncAfter(deadline: .now() + 0.3) { askAI(prompt) } } label: {
        HStack(spacing: 6) {
          Icon(.orbit, size: 17)
          Text(tr("问 Jarvis", "Ask Jarvis")).font(.system(size: 16, weight: .semibold))
        }
        .foregroundColor(Palette.text)
        .frame(maxWidth: .infinity, minHeight: 50)
        .background(Capsule().fill(Palette.inSheet))
      }
      .buttonStyle(PressScale())
    }
  }

  private var prompt: String {
    switch kind {
    case .recovery: return TodayGlancePage.recoveryPrompt(model)
    case .strain:
      let v = model.glance.strain.text ?? "—"
      return tr("我今天负荷 \(v)，接下来怎么练合适？", "My strain is \(v) today. How should I train for the rest of the day?")
    case .sleep: return tr("帮我看看昨晚睡得怎么样", "How did I sleep last night?")
    case .attention: return tr("帮我看看今天需要注意的指标", "Which of my numbers need attention today?")
    }
  }
}

/// Three date labels under a chart: first, middle, last.
struct DateLabels: View {
  let dates: [String]

  var body: some View {
    HStack {
      Text(TodayModel.shortDay(dates.first))
      Spacer()
      Text(TodayModel.shortDay(dates.isEmpty ? nil : dates[dates.count / 2]))
      Spacer()
      Text(TodayModel.shortDay(dates.last))
    }
    .font(Typo.axis).foregroundColor(Palette.text2)
  }
}

/// One L1 row: label · graphic (spark on the usual band, or a bullet) · value + unit · change vs usual · chevron.
struct L1RowView: View {
  let row: TodayGlance.Row
  let tap: () -> Void

  var body: some View {
    Button(action: tap) {
      HStack(spacing: 10) {
        Text(row.label).font(.system(size: 15, weight: .medium)).foregroundColor(Palette.text2).lineLimit(1)
          .minimumScaleFactor(0.8).frame(width: 74, alignment: .leading)
        graphic.frame(height: 22).frame(maxWidth: .infinity)
        HStack(alignment: .firstTextBaseline, spacing: 2) {
          Text(row.value ?? "—").font(Typo.row).foregroundColor(Palette.text)
          if let u = row.unit { Text(u).font(Typo.unit).foregroundColor(Palette.text2) }
        }
        .lineLimit(1)
        .frame(minWidth: 80, alignment: .trailing)
        Text(row.delta ?? "").font(.system(size: 15, weight: .medium).monospacedDigit())
          .foregroundColor(row.deltaTone == .text ? Palette.text2 : row.deltaTone.color)
          .lineLimit(1).minimumScaleFactor(0.7)
          .frame(width: 44, alignment: .trailing)
        Icon(.chevronRight, size: 13).foregroundColor(Palette.text3)
      }
      .frame(minHeight: 46)
      .contentShape(Rectangle())
    }
    .buttonStyle(PressScale())
    .overlay(alignment: .bottom) { Rectangle().fill(Palette.hair).frame(height: 1) }
    .accessibilityElement(children: .ignore)
    .accessibilityLabel("\(row.label) \(row.value ?? "—") \(row.unit ?? "") \(row.delta ?? "")")
    .accessibilityAddTraits(.isButton)
  }

  @ViewBuilder private var graphic: some View {
    if let b = row.bullet {
      if b.centred { CentredBar(value: b.value, range: b.max) }
      else { BulletGauge(value: b.value, max: b.max, usual: b.usual, step: 0, tone: .text, scale: false) }
    } else if row.spark.contains(where: { $0 != nil }) {
      BandSpark(values: row.spark, usual: row.usual)
    } else {
      Color.clear
    }
  }
}

/// A value around 0 on a −range…+range track (training state): ticks at the ends, the middle and the halves.
struct CentredBar: View {
  let value: Double
  let range: Double

  var body: some View {
    GeometryReader { g in
      let w = g.size.width, mid = w / 2
      let x = mid + CGFloat(max(-1, min(1, value / range))) * mid
      ZStack(alignment: .leading) {
        Rectangle().fill(Palette.track).frame(height: 1.5)
        ForEach([0.25, 0.75], id: \.self) { f in Rectangle().fill(Palette.tick).frame(width: 1, height: 8).offset(x: w * f) }
        Rectangle().fill(Palette.tickMajor).frame(width: 1.5, height: 12).offset(x: mid - 0.75)
        Capsule().fill(Palette.text).frame(width: max(4, abs(x - mid)), height: 4).offset(x: min(x, mid))
      }
      .frame(height: g.size.height)
    }
    .accessibilityHidden(true)
  }
}

// MARK: - sleep L1

/// The sleep sheet: last night's duration, the hypnogram on a clock axis, need / deep / regularity / lowest HR.
struct TodaySleepL1: View {
  let session: AccountSession
  let date: String?
  let explain: (String, String) -> Void
  @StateObject private var store = SleepNightStore()

  var body: some View {
    Group {
      if let m = store.model, m.hasMain {
        content(m)
      } else if store.model != nil || store.error != nil {
        Text("—").font(Typo.reading).foregroundColor(Palette.text2).frame(maxWidth: .infinity, minHeight: 120)
      } else {
        ProgressView().tint(Palette.text3).frame(maxWidth: .infinity, minHeight: 120)
      }
    }
    .onAppear { if store.model == nil { store.show(session, date: date) } }
  }

  @ViewBuilder private func content(_ m: SleepNightModel) -> some View {
    if let mins = m.asleep ?? m.actual {
      HoursMinutes(minutes: mins, size: 40).padding(.top, 4)
    }
    if m.staged, !m.stages.isEmpty {
      StageChart(stages: m.stages, total: max(1, m.inBed ?? 1), start: m.start, end: m.end).padding(.top, 14)
    }
    VStack(spacing: 0) {
      ForEach(Self.rows(m)) { r in
        L1RowView(row: r) { if let e = r.explain { explain(e.key, e.title) } }
      }
    }
    .padding(.top, 12)
  }

  /// 需求 (asleep against need), 深睡 (vs the 30-night average), 规律性 (7 vs 30 nights), 最低心率 (the night's vital).
  static func rows(_ m: SleepNightModel) -> [TodayGlance.Row] {
    var out: [TodayGlance.Row] = []
    if let need = m.need {
      let asleep = m.asleep ?? m.actual ?? 0
      out.append(.init(id: "need", label: tr("需求", "Need"), value: TodayModel.fmt(need / 60, digits: 1), unit: tr("时", "h"),
                       bullet: .init(value: asleep, max: max(need, asleep) * 1.15, usual: need...need),
                       explain: ("sleep_need", tr("睡眠需求", "Sleep need"))))
    }
    if let d = m.deepMin {
      out.append(.init(id: "deep", label: tr("深睡", "Deep"), value: TodayModel.hm(d), unit: nil,
                       delta: TodayGlanceRules.delta(d, mid: m.deepAvg30, digits: 0),
                       deltaTone: TodayGlanceRules.deltaTone(d, usual: m.deepAvg30.map { ($0 * (1 - TodayParams.deepUsualShare))...($0 * (1 + TodayParams.deepUsualShare)) }, better: 1),
                       explain: ("stages", tr("深睡", "Deep sleep"))))
    }
    if let r = m.regularity7 {
      out.append(.init(id: "regularity", label: tr("规律性", "Regularity"), value: TodayModel.fmt(r), unit: "%",
                       delta: TodayGlanceRules.delta(r, mid: m.regularity30, digits: 0),
                       deltaTone: TodayGlanceRules.deltaTone(r, usual: m.regularity30.map { ($0 - TodayParams.regularityUsualPoints)...($0 + TodayParams.regularityUsualPoints) }, better: 1),
                       explain: ("sleep_consistency", tr("睡眠规律性", "Sleep regularity"))))
    }
    if let v = m.vitals.first(where: { $0.key == "rhr" }) {
      out.append(.init(id: "lowest_hr", label: tr("最低心率", "Lowest HR"), value: v.value, unit: v.unit,
                       delta: v.delta.map { $0.replacingOccurrences(of: "-", with: "−") }, deltaTone: v.worse ? .mid : .text,
                       explain: ("resting_hr", tr("静息心率", "Resting HR"))))
    }
    return out
  }
}

/// 「7 时 24 分」: numbers big, units small.
struct HoursMinutes: View {
  let minutes: Double
  var size: CGFloat = 40

  var body: some View {
    let r = Int(minutes.rounded())
    HStack(alignment: .firstTextBaseline, spacing: 3) {
      Text("\(r / 60)").font(.system(size: size, weight: .light).monospacedDigit()).foregroundColor(Palette.text)
      Text(tr("时", "h")).font(.system(size: size * 0.4, weight: .medium)).foregroundColor(Palette.text2)
      Text("\(r % 60)").font(.system(size: size, weight: .light).monospacedDigit()).foregroundColor(Palette.text).padding(.leading, 4)
      Text(tr("分", "m")).font(.system(size: size * 0.4, weight: .medium)).foregroundColor(Palette.text2)
    }
    .accessibilityElement(children: .ignore)
    .accessibilityLabel(tr("\(r / 60) 小时 \(r % 60) 分", "\(r / 60) h \(r % 60) min"))
  }
}

/// The hypnogram of the sleep sheet: four lanes (清醒 · REM · 核心 · 深睡) on a clock axis from falling asleep to waking;
/// stages in the sleep colour (deeper = fuller), awake in ink.
struct StageChart: View {
  let stages: [SleepNightModel.Stage]
  let total: Double
  let start: String
  let end: String

  var body: some View {
    VStack(spacing: 6) {
      HStack(alignment: .top, spacing: 8) {
        VStack(alignment: .leading, spacing: 0) {
          ForEach(SleepNightModel.lanes, id: \.self) { l in
            Text(SleepNightModel.stageName(l)).font(.system(size: 11, weight: .medium)).foregroundColor(Palette.text2)
              .frame(height: 26, alignment: .center)
          }
        }
        .frame(width: 32, alignment: .leading)
        GeometryReader { g in
          let w = g.size.width
          ZStack(alignment: .topLeading) {
            ForEach(Array(stages.enumerated()), id: \.offset) { _, s in
              let lane = SleepNightModel.lanes.firstIndex(of: s.stage == "unspecified" ? "core" : s.stage) ?? 2
              RoundedRectangle(cornerRadius: 3, style: .continuous)
                .fill(Self.color(s.stage))
                .frame(width: max(2, CGFloat(max(s.minutes, 1.2) / total) * w), height: s.stage == "awake" ? 8 : 14)
                .offset(x: CGFloat(s.offset / total) * w, y: CGFloat(lane) * 26 + (s.stage == "awake" ? 9 : 6))
            }
            Rectangle().fill(Palette.hairStrong).frame(width: w, height: 1).offset(y: 104)
          }
        }
        .frame(height: 105)
      }
      HStack {
        Text(start); Spacer(); Text(end)
      }
      .font(Typo.axis).foregroundColor(Palette.text2)
      .padding(.leading, 40)
    }
    .accessibilityElement(children: .ignore)
    .accessibilityLabel(tr("睡眠阶段图 \(start) 到 \(end)", "Sleep stages \(start) to \(end)"))
  }

  static func color(_ stage: String) -> Color {
    switch stage {
    case "deep": return Palette.sleep
    case "rem": return Palette.sleep.opacity(0.55)
    case "awake": return Palette.text2
    default: return Palette.sleep.opacity(0.75)
    }
  }
}
