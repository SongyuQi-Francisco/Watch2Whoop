import SwiftUI

/// Training history (docs/design/2.0.1 `14-training-history`), pushed from the 历史 face: the month as dots (size = the
/// day's strain, shade = intensity, today an open ring), the month's sessions, hours and average strain, then one card per
/// trained day (newest first). A dot or a card opens that day (workouts + logged strength); a planned day ahead opens its
/// plan. The month picker on the right goes back up to a year.
struct TrainingHistoryPage: View {
  @ObservedObject var store: TrainingStore
  @Binding var toast: Toast?
  @State private var detailDay: String?
  @State private var sheet: TrainingSheet?

  var body: some View {
    RecordPage(title: tr("训练历史", "History"), trailing: { monthPicker }) {
      if let m = store.calendar {
        content(m)
      } else {
        EmptyNote(text: store.refreshing ? "…" : tr("暂无", "None")).padding(.top, 24)
      }
    }
    .task { await store.refresh() }
    .trainingSheets(store: store, sheet: $sheet, toast: $toast)
    .trainingDaySheet(store: store, day: $detailDay)
  }

  private var monthPicker: some View {
    let now = String(store.today.prefix(7))
    return Menu {
      ForEach(0..<12, id: \.self) { k in
        let m = TrainingMonth.add(now, -k)
        Button(Self.monthTitle(m)) { store.showMonth(m) }
      }
    } label: {
      HStack(spacing: 4) {
        Text(Self.monthTitle(store.month)).font(Typo.header).foregroundColor(Palette.text)
        Icon(.chevronDown, size: 13).foregroundColor(Palette.text2)
      }
      .frame(minHeight: 44)
      .contentShape(Rectangle())
    }
    .accessibilityLabel(tr("选择月份", "Pick a month"))
  }

  /// 「11月」 this year, 「2025年11月」 before; "Nov" / "Nov 2025".
  static func monthTitle(_ m: String) -> String {
    let p = m.split(separator: "-").compactMap { Int($0) }
    guard p.count == 2 else { return m }
    let thisYear = Calendar.current.component(.year, from: Date())
    if p[0] == thisYear { return tr("\(p[1])月", "\(TodayModel.monthName(p[1]))") }
    return tr("\(p[0])年\(p[1])月", "\(TodayModel.monthName(p[1])) \(p[0])")
  }

  private func content(_ m: TrainingMonth) -> some View {
    let cells = RecordGlance.historyCells(m, today: store.today)
    let cards = Array(RecordGlance.trainedDays(m).prefix(RecordParams.historyCards))
    return VStack(alignment: .leading, spacing: 0) {
      calendar(cells).padding(.top, 20)
      summary(m).padding(.top, 28)
      VStack(spacing: 10) {
        ForEach(cards) { d in card(d) }
      }
      .padding(.top, 24)
    }
  }

  private func calendar(_ cells: [RecordGlance.HistoryCell?]) -> some View {
    let cols = Array(repeating: GridItem(.flexible(), spacing: 0), count: 7)
    return LazyVGrid(columns: cols, spacing: 6) {
      ForEach(0..<7, id: \.self) { i in
        Text(TrainingSection.weekdayName(i + 1)).font(.system(size: 13, weight: .medium)).foregroundColor(Palette.text2)
          .frame(height: 30)
      }
      ForEach(Array(cells.enumerated()), id: \.offset) { _, c in
        if let c { dot(c) } else { Color.clear.frame(height: 44) }
      }
    }
  }

  private func dot(_ c: RecordGlance.HistoryCell) -> some View {
    Button { open(c) } label: {
      ZStack {
        if c.trained {
          Circle().fill(Palette.train.opacity(c.opacity)).frame(width: CGFloat(c.diameter), height: CGFloat(c.diameter))
        } else if c.today {
          Circle().stroke(Palette.text, lineWidth: 1.5).frame(width: 26, height: 26)
        } else {
          Circle().fill(c.future ? Palette.text3.opacity(0.6) : Palette.text3).frame(width: 4, height: 4)
        }
      }
      .frame(maxWidth: .infinity, minHeight: 44)
      .contentShape(Rectangle())
    }
    .buttonStyle(PressScale())
    .disabled(c.future && store.week?.days.contains { $0.date == c.date } != true)
    .accessibilityLabel(RecordDay.short(c.date) + (c.trained ? tr("，练了", ", trained") : ""))
  }

  private func open(_ c: RecordGlance.HistoryCell) {
    if c.future { sheet = .planDay(c.date) } else { detailDay = c.date }
  }

  private func summary(_ m: TrainingMonth) -> some View {
    HStack(alignment: .firstTextBaseline, spacing: 0) {
      number(m.trainingDays.map(String.init), unit: tr("次", "times"))
      Spacer(minLength: 8)
      number(m.totalMinutes.map { RecordFormat.num($0 / 60, 1) }, unit: tr("小时", "h"))
      Spacer(minLength: 8)
      number(RecordGlance.averageStrain(m).map { RecordFormat.num($0, 1) }, unit: tr("均负荷", "avg strain"))
    }
    .padding(.horizontal, 12)
  }

  private func number(_ v: String?, unit: String) -> some View {
    HStack(alignment: .firstTextBaseline, spacing: 4) {
      Text(v ?? "—").font(.system(size: 38, weight: .light).monospacedDigit()).foregroundColor(v == nil ? Palette.text2 : Palette.text)
        .lineLimit(1).minimumScaleFactor(0.6)
      Text(unit).font(.system(size: 14, weight: .medium)).foregroundColor(Palette.text2).lineLimit(1)
    }
    .accessibilityElement(children: .combine)
  }

  private func card(_ d: TrainingMonth.Day) -> some View {
    let strength = RecordGlance.isStrength(d.types)
    let name = d.labels.isEmpty ? (d.label ?? tr("训练", "Workout")) : d.labels.map(TrainingWeek.shortPlan).joined(separator: " + ")
    return Button { detailDay = d.date } label: {
      HStack(spacing: 14) {
        Icon(strength ? .dumbbell : .flame, size: 24).foregroundColor(Palette.train).frame(width: 44)
        VStack(alignment: .leading, spacing: 8) {
          HStack(alignment: .firstTextBaseline, spacing: 10) {
            Text(name).font(.system(size: 18, weight: .semibold)).foregroundColor(Palette.text).lineLimit(1).minimumScaleFactor(0.8)
            Text(RecordDay.short(d.date)).font(.system(size: 15).monospacedDigit()).foregroundColor(Palette.text2).lineLimit(1)
          }
          IntensityBar(intensity: d.intensity)
        }
        Spacer(minLength: 6)
        VStack(alignment: .trailing, spacing: 4) {
          Text(d.strain.map { RecordFormat.num($0, 1) } ?? "—").font(.system(size: 28, weight: .regular).monospacedDigit())
            .foregroundColor(Palette.text)
          if d.minutes > 0 {
            Text(tr("\(RecordFormat.num(d.minutes)) 分", "\(RecordFormat.num(d.minutes)) min")).font(.system(size: 15).monospacedDigit())
              .foregroundColor(Palette.text2)
          }
        }
        Icon(.chevronRight, size: 14).foregroundColor(Palette.text3)
      }
      .padding(.horizontal, 16).padding(.vertical, 16)
      .background(RoundedRectangle(cornerRadius: Metrics.cardRadius, style: .continuous).fill(Palette.surface))
      .contentShape(Rectangle())
    }
    .buttonStyle(PressScale())
    .accessibilityElement(children: .ignore)
    .accessibilityLabel("\(name) \(RecordDay.short(d.date)) \(tr("负荷", "strain")) \(d.strain.map { RecordFormat.num($0, 1) } ?? "—")")
    .accessibilityAddTraits(.isButton)
  }
}

/// A day's intensity as four steps (light · moderate · hard · max), the reached ones in `train`.
private struct IntensityBar: View {
  let intensity: String?

  var body: some View {
    let levels = ["light", "moderate", "hard", "max"]
    let reached = (levels.firstIndex(of: intensity ?? "") ?? -1) + 1
    HStack(spacing: 4) {
      ForEach(0..<levels.count, id: \.self) { i in
        Capsule().fill(i < reached ? Palette.train.opacity(0.45 + 0.18 * Double(i)) : Palette.surface2).frame(height: 6)
      }
    }
    .frame(maxWidth: 200)
    .accessibilityHidden(true)
  }
}
