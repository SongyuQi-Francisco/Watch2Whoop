import SwiftUI

/// 训练历史: a month grid, each trained day dotted by intensity; the month's totals; tap a day → its detail.
struct TrainingCalendarView: View {
  @ObservedObject var store: TrainingStore
  let openDay: (String) -> Void

  var body: some View {
    VStack(alignment: .leading, spacing: 10) {
      HStack {
        SectionHeader(title: tr("训练历史", "Training history"))
        Spacer()
        Button { store.showMonth(TrainingMonth.add(store.month, -1)) } label: {
          Icon(.chevronLeft, size: 16).foregroundColor(Palette.text2).frame(width: 32, height: 28)
        }
        .accessibilityLabel(tr("上个月", "Previous month"))
        Text(monthTitle).font(Typo.label.monospacedDigit()).foregroundColor(Palette.text)
        Button { store.showMonth(TrainingMonth.add(store.month, 1)) } label: {
          Icon(.chevronRight, size: 16).foregroundColor(store.month >= String(store.today.prefix(7)) ? Palette.text3 : Palette.text2)
            .frame(width: 32, height: 28)
        }
        .disabled(store.month >= String(store.today.prefix(7)))
        .accessibilityLabel(tr("下个月", "Next month"))
      }
      Card {
        VStack(spacing: 12) {
          grid
          if let m = store.calendar {
            HStack(spacing: 8) {
              MetricTile(label: tr("训练", "Trained"), value: m.trainingDays.map(String.init), unit: tr("天", "d"))
              MetricTile(label: tr("时长", "Time"), value: m.totalMinutes.map { RecordFormat.num($0 / 60, 1) }, unit: "h")
              MetricTile(label: tr("消耗", "Burned"), value: m.totalKcal.map { RecordFormat.num($0) }, unit: "kcal")
            }
          }
        }
      }
    }
  }

  private var monthTitle: String {
    let p = store.month.split(separator: "-")
    guard p.count == 2, let m = Int(p[1]) else { return store.month }
    return tr("\(p[0]) 年 \(m) 月", "\(m)/\(p[0])")
  }

  private var grid: some View {
    let days = store.calendar?.days ?? []
    let byDate = Dictionary(days.map { ($0.date, $0) }, uniquingKeysWith: { a, _ in a })
    let first = "\(store.month)-01"
    let lead = RecordDay.weekday(first) - 1
    let count = Self.daysIn(store.month)
    let cells: [String?] = Array(repeating: nil, count: lead) + (0..<count).map { RecordDay.add(first, $0) }
    return VStack(spacing: 6) {
      HStack(spacing: 0) {
        ForEach(1...7, id: \.self) { wd in
          Text(TrainingSection.weekdayName(wd)).font(.system(size: 11, weight: .medium)).foregroundColor(Palette.text3).frame(maxWidth: .infinity)
        }
      }
      LazyVGrid(columns: Array(repeating: GridItem(.flexible(), spacing: 0), count: 7), spacing: 6) {
        ForEach(Array(cells.enumerated()), id: \.offset) { _, iso in
          if let iso {
            let d = byDate[iso]
            let future = iso > store.today
            Button { if !future { openDay(iso) } } label: {
              VStack(spacing: 3) {
                Text(String(Int(iso.suffix(2)) ?? 0)).font(.system(size: 14, weight: iso == store.today ? .bold : .regular).monospacedDigit())
                  .foregroundColor(future ? Palette.text3 : Palette.text)
                Circle().fill((d?.sessions ?? 0) > 0 ? (TrainingMonth.tone(d?.intensity) ?? .good).color : .clear).frame(width: 6, height: 6)
              }
              .frame(maxWidth: .infinity, minHeight: 38)
              .background(RoundedRectangle(cornerRadius: 9, style: .continuous).fill(iso == store.today ? Palette.bg : .clear))
            }
            .buttonStyle(PressScale())
            .disabled(future)
            .accessibilityLabel("\(RecordDay.short(iso)) \(d?.label ?? "")")
          } else {
            Color.clear.frame(minHeight: 38)
          }
        }
      }
    }
  }

  static func daysIn(_ month: String) -> Int {
    let p = month.split(separator: "-").compactMap { Int($0) }
    guard p.count == 2 else { return 30 }
    var c = Calendar(identifier: .gregorian)
    c.timeZone = TimeZone(identifier: "UTC")!
    guard let d = c.date(from: DateComponents(year: p[0], month: p[1], day: 1)) else { return 30 }
    return c.range(of: .day, in: .month, for: d)?.count ?? 30
  }
}

/// One day of training (`/training/day/{day}`): watch workouts and logged strength sessions; a future day shows its plan.
struct TrainingDaySheet: View {
  @ObservedObject var store: TrainingStore
  let day: String
  let plan: [String]
  @State private var detail: [String: Any]?
  @State private var loaded = false

  init(store: TrainingStore, day: String, plan: [String]) {
    self.store = store
    self.day = day
    self.plan = plan
  }

  var body: some View {
    VStack(alignment: .leading, spacing: 12) {
      if day > store.today {
        if plan.isEmpty { EmptyNote(text: tr("无计划", "No plan")) }
        ForEach(plan, id: \.self) { Text($0).font(Typo.body).foregroundColor(Palette.text) }
      } else if let d = detail {
        let daily = d["daily"] as? [String: Any]
        HStack(spacing: 8) {
          MetricTile(label: tr("负荷", "Strain"), value: RecordFormat.double(daily?["strain"]).map { RecordFormat.num($0, 1) })
          MetricTile(label: tr("活动", "Active"), value: RecordFormat.double(daily?["active_kcal"]).map { RecordFormat.num($0) }, unit: "kcal")
          MetricTile(label: tr("步数", "Steps"), value: RecordFormat.double(daily?["steps"]).map { RecordFormat.num($0) })
        }
        let sessions = (d["sessions"] as? [[String: Any]]) ?? []
        if sessions.isEmpty { EmptyNote(text: tr("这天没有训练", "No training that day")) }
        ForEach(Array(sessions.enumerated()), id: \.offset) { _, s in sessionCard(s) }
      } else {
        EmptyNote(text: loaded ? tr("需要联网", "Needs a connection") : "…")
      }
    }
    .task {
      detail = await store.dayDetail(day)
      loaded = true
    }
  }

  private func sessionCard(_ s: [String: Any]) -> some View {
    VStack(alignment: .leading, spacing: 6) {
      HStack {
        Text(s["name"] as? String ?? s["type_name"] as? String ?? "—").font(Typo.bodyStrong).foregroundColor(Palette.text)
        if let label = s["intensity_label"] as? String { Badge(label: label, tone: TrainingMonth.tone(s["intensity"] as? String) ?? .text) }
        Spacer()
        if let start = s["start"] as? String, start.count >= 16 { Text(String(start.dropFirst(11).prefix(5))).font(Typo.caption).foregroundColor(Palette.text2) }
      }
      Text([RecordFormat.double(s["duration_min"]).map { tr("\(RecordFormat.num($0)) 分钟", "\(RecordFormat.num($0)) min") },
            RecordFormat.double(s["active_kcal"]).map { "\(RecordFormat.num($0)) kcal" },
            RecordFormat.double(s["avg_hr"]).map { tr("平均 \(RecordFormat.num($0)) bpm", "avg \(RecordFormat.num($0)) bpm") },
            RecordFormat.double(s["volume_kg"]).map { tr("训练量 \(RecordFormat.num($0)) kg", "volume \(RecordFormat.num($0)) kg") }]
        .compactMap { $0 }.joined(separator: " · "))
        .font(Typo.caption.monospacedDigit()).foregroundColor(Palette.text2)
      ForEach(Array(((s["exercises"] as? [[String: Any]]) ?? []).enumerated()), id: \.offset) { _, x in
        HStack(alignment: .firstTextBaseline) {
          Text(x["name"] as? String ?? "—").font(Typo.body).foregroundColor(Palette.text)
          Spacer()
          Text(Self.setsText(x)).font(Typo.caption.monospacedDigit()).foregroundColor(Palette.text2).multilineTextAlignment(.trailing)
        }
        .padding(.vertical, 4)
      }
    }
    .padding(14)
    .background(RoundedRectangle(cornerRadius: Metrics.tileRadius, style: .continuous).fill(Palette.inSheet))
  }

  static func setsText(_ x: [String: Any]) -> String {
    let sets = (x["sets"] as? [[String: Any]]) ?? []
    return sets.map { s in
      let r = RecordFormat.double(s["reps"]).map { RecordFormat.num($0) }
      let w = RecordFormat.double(s["weight_kg"] ?? s["weight"]).map { RecordFormat.num($0, 1) }
      switch (r, w) {
      case let (r?, w?): return "\(w)×\(r)"
      case let (r?, nil): return "×\(r)"
      default: return w.map { "\($0) kg" } ?? "—"
      }
    }.joined(separator: " · ")
  }
}
