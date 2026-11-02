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

/// One day of training (`/training/day/{day}`): watch workouts and logged strength sessions. A future day of this week opens
/// its plan instead (PlanDayView).
struct TrainingDaySheet: View {
  @ObservedObject var store: TrainingStore
  let day: String
  @State private var detail: [String: Any]?
  @State private var loaded = false

  init(store: TrainingStore, day: String) {
    self.store = store
    self.day = day
  }

  var body: some View {
    VStack(alignment: .leading, spacing: 12) {
      if let d = detail {
        let daily = d["daily"] as? [String: Any]
        HStack(spacing: 8) {
          MetricTile(label: tr("负荷", "Strain"), value: RecordFormat.double(daily?["strain"]).map { RecordFormat.num($0, 1) })
          MetricTile(label: tr("活动", "Active"), value: RecordFormat.double(daily?["active_kcal"]).map { RecordFormat.num($0) }, unit: "kcal")
          MetricTile(label: tr("步数", "Steps"), value: RecordFormat.double(daily?["steps"]).map { RecordFormat.num($0) })
        }
        let sessions = (d["sessions"] as? [[String: Any]]) ?? []
        if sessions.isEmpty { EmptyNote(text: tr("这天没有训练", "No training that day")) }
        ForEach(Array(sessions.enumerated()), id: \.offset) { _, s in sessionCard(s) }
        dayRows(daily)
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
            RecordFormat.double(s["max_hr"]).map { tr("最高 \(RecordFormat.num($0)) bpm", "max \(RecordFormat.num($0)) bpm") },
            RecordFormat.double(s["volume_kg"]).map { tr("训练量 \(RecordFormat.num($0)) kg", "volume \(RecordFormat.num($0)) kg") }]
        .compactMap { $0 }.joined(separator: " · "))
        .font(Typo.caption.monospacedDigit()).foregroundColor(Palette.text2)
      if ["active_kcal", "avg_hr", "max_hr"].allSatisfy({ RecordFormat.double(s[$0]) == nil }) {
        Badge(label: tr("无手表数据", "No watch data"))
      }
      ZoneBar(minutes: Self.zones(s["hr_zone_min"]))
      ForEach(Array(Self.listed(s).enumerated()), id: \.offset) { _, x in
        HStack(alignment: .firstTextBaseline) {
          Text(x["name"] as? String ?? "—").font(Typo.body).foregroundColor(Palette.text)
          ExerciseStatusBadge(status: (x["status_key"] as? String) ?? "active")
          Spacer()
          Text(Self.setsText(x)).font(Typo.caption.monospacedDigit()).foregroundColor(Palette.text2).multilineTextAlignment(.trailing)
        }
        .padding(.vertical, 4)
      }
    }
    .padding(14)
    .background(RoundedRectangle(cornerRadius: Metrics.tileRadius, style: .continuous).fill(Palette.inSheet))
  }

  /// The rest of the day's numbers (app.js daySheet); the tiles above hold strain, activity and steps.
  @ViewBuilder private func dayRows(_ daily: [String: Any]?) -> some View {
    VStack(alignment: .leading, spacing: 0) {
      if let v = RecordFormat.double(daily?["workout_kcal"]) { KeyValueRow(label: tr("训练消耗", "Workout energy"), value: "\(RecordFormat.num(v)) kcal") }
      if let v = RecordFormat.double(daily?["total_kcal"]) { KeyValueRow(label: tr("全天总消耗", "Total energy"), value: "\(RecordFormat.num(v)) kcal") }
      if let v = RecordFormat.double(daily?["exercise_min"]) { KeyValueRow(label: tr("锻炼分钟", "Exercise minutes"), value: tr("\(RecordFormat.num(v)) 分钟", "\(RecordFormat.num(v)) min")) }
      if let v = RecordFormat.double(daily?["muscle_load"]) { KeyValueRow(label: tr("肌肉负荷", "Muscle load"), value: "\(RecordFormat.num(v)) kg") }
      if let v = RecordFormat.double(daily?["recovery_next_day"]) { KeyValueRow(label: tr("次日恢复", "Next-day recovery"), value: RecordFormat.num(v)) }
    }
  }

  /// Minutes per heart-rate zone: a list, or an object keyed by zone (app.js zonesHtml).
  static func zones(_ raw: Any?) -> [Double] {
    if let list = raw as? [Any] { return list.map { RecordFormat.double($0) ?? 0 } }
    if let o = raw as? [String: Any] { return o.keys.sorted().map { RecordFormat.double(o[$0]) ?? 0 } }
    return []
  }

  /// A workout's exercises; a line with the workout's own name and no sets (the cardio line) is not listed again.
  static func listed(_ s: [String: Any]) -> [[String: Any]] {
    let name = (s["name"] as? String) ?? (s["type_name"] as? String)
    return ((s["exercises"] as? [[String: Any]]) ?? []).filter { x in
      let sets = (x["sets"] as? [[String: Any]]) ?? []
      return (x["name"] as? String) != name || sets.contains { RecordFormat.double($0["reps"]) != nil || RecordFormat.double($0["weight_kg"]) != nil }
    }
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

/// Minutes in each heart-rate zone: a bar, darker by zone, and the numbers (app.js zonesHtml).
private struct ZoneBar: View {
  let minutes: [Double]

  private var total: Double { minutes.reduce(0, +) }

  var body: some View {
    if total > 0 {
      VStack(alignment: .leading, spacing: 4) {
        GeometryReader { g in
          HStack(spacing: 1) {
            ForEach(Array(minutes.enumerated()), id: \.offset) { i, m in
              Rectangle().fill(Palette.train.opacity(0.25 + Double(i) * 0.18))
                .frame(width: max(0, (g.size.width - CGFloat(minutes.count - 1)) * CGFloat(m / total)))
            }
          }
        }
        .frame(height: 6)
        .clipShape(Capsule())
        Text(minutes.enumerated().map { "Z\($0.offset + 1) \(RecordFormat.num($0.element))′" }.joined(separator: " · "))
          .font(.system(size: 11).monospacedDigit()).foregroundColor(Palette.text2)
      }
      .accessibilityElement(children: .combine)
    }
  }
}
