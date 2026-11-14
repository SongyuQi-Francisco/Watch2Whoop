import Foundation

/// The pure rules behind the 2.0.1 记录 tab (docs/design/2.0.1 screens 10–15): what the set ring, the bodies, the week dots,
/// the weekly muscle rows, the history calendar and the food dial draw, from the stores' models. No views here, so the
/// tests feed made-up models (RecordGlanceTests).
enum RecordGlance {
  // MARK: - set ring

  /// One arc of the set ring: where it starts and ends (0…1 from 12 o'clock), which exercise it belongs to, done or not.
  struct Arc: Equatable {
    var start: Double
    var end: Double
    var exercise: Int
    var done: Bool
  }

  /// The sets of the day, one per arc, in plan order: the loggable exercises' rows (banned ones have none).
  static func sets(_ s: TrainingSession, rows: [String: [SetRow]]) -> [(exercise: Int, done: Bool)] {
    var out: [(exercise: Int, done: Bool)] = []
    for (i, e) in s.loggable.enumerated() {
      for r in rows[e.slug] ?? [] {
        out.append((exercise: i, done: r.done))
      }
    }
    return out
  }

  /// The arcs of the ring: equal arcs, a small gap between sets, a wider one between exercises and at 12 o'clock.
  static func arcs(_ sets: [(exercise: Int, done: Bool)], setGap: Double = RecordParams.setGap,
                   exerciseGap: Double = RecordParams.exerciseGap) -> [Arc] {
    let n = sets.count
    guard n > 0 else { return [] }
    var gaps: [Double] = []
    for i in 0..<n {
      let last = i == n - 1
      let newExercise = !last && sets[i + 1].exercise != sets[i].exercise
      gaps.append(last || newExercise ? exerciseGap : setGap)
    }
    let free = 1 - gaps.reduce(0, +)
    let length = max(0.001, free / Double(n))
    var out: [Arc] = []
    var at = exerciseGap / 2
    for i in 0..<n {
      out.append(Arc(start: at, end: at + length, exercise: sets[i].exercise, done: sets[i].done))
      at += length + gaps[i]
    }
    return out
  }

  /// Where the exercises meet on a set scale (the 组 reading's long ticks): the running total after each exercise but the last.
  static func exerciseEdges(_ sets: [(exercise: Int, done: Bool)]) -> [Double] {
    var out: [Double] = []
    for i in 1..<max(1, sets.count) where sets[i].exercise != sets[i - 1].exercise {
      out.append(Double(i))
    }
    return out
  }

  // MARK: - muscles inside the ring

  /// Body region → share of the day's work (0…1, the most worked region is 1): each set credits the exercise's primary
  /// muscles fully and its secondary muscles by `secondaryWeight`. `doneOnly`: only the sets ticked off (the session page
  /// fills set by set).
  static func regionShares(_ exercises: [PlannedExercise], rows: [String: [SetRow]], doneOnly: Bool = false) -> [String: Double] {
    var raw: [String: Double] = [:]
    for e in exercises where !e.banned {
      let list = rows[e.slug] ?? []
      let n = Double(doneOnly ? list.filter(\.done).count : list.count)
      guard n > 0 else { continue }
      for m in e.primaryMuscles {
        for r in MuscleLoad.bodyRegions[m] ?? [] { raw[r, default: 0] += n }
      }
      for m in e.secondaryMuscles {
        for r in MuscleLoad.bodyRegions[m] ?? [] { raw[r, default: 0] += n * RecordParams.secondaryWeight }
      }
    }
    let top = raw.values.max() ?? 0
    guard top > 0 else { return [:] }
    return raw.mapValues { $0 / top }
  }

  /// Opacity of a region's `train` colour for its share (0 = not drawn in colour).
  static func shareOpacity(_ share: Double?) -> Double {
    guard let share, share > 0 else { return 0 }
    let r = RecordParams.shareOpacity
    return r.lowerBound + (r.upperBound - r.lowerBound) * min(1, share)
  }

  // MARK: - the week (reading 2)

  enum WeekDot: Equatable {
    /// Trained that day (`train`).
    case trained
    /// Today, not trained yet (an open ring).
    case today
    /// A day ahead with a plan (grey).
    case planned
    /// Nothing: a past day without training, a rest day, a day ahead without a plan.
    case none
  }

  static func weekDots(_ w: TrainingWeek, today: String) -> [WeekDot] {
    w.days.map { d in
      if d.trained { return .trained }
      if d.date == today { return .today }
      if d.date > today && !d.planned.isEmpty { return .planned }
      return .none
    }
  }

  // MARK: - weekly muscle map (13)

  /// One body area of the weekly map: its sets, the summed weekly range of its muscles and where it stands.
  struct GroupRow: Equatable, Identifiable {
    var key: String
    var sets: Double
    var low: Double?
    var high: Double?
    /// none (no sets) · low · ok · high, as the server's muscle statuses.
    var status: String
    var id: String { key }

    var title: String { RecordGlance.groupTitle(key) }
  }

  static func groupTitle(_ key: String) -> String {
    switch key {
    case "chest": return tr("胸", "Chest")
    case "back": return tr("背", "Back")
    case "shoulders": return tr("肩", "Shoulders")
    case "arms": return tr("手臂", "Arms")
    case "legs": return tr("腿", "Legs")
    default: return tr("核心", "Core")
    }
  }

  static func groups(_ load: MuscleLoad, range: MuscleLoad.Range) -> [GroupRow] {
    let byKey = Dictionary(load.muscles.map { ($0.key, $0) }, uniquingKeysWith: { a, _ in a })
    var out: [GroupRow] = []
    for g in RecordParams.muscleGroups {
      let ms = g.muscles.compactMap { byKey[$0] }
      guard !ms.isEmpty else { continue }
      var sets = 0.0, low = 0.0, high = 0.0, withTarget = 0
      for m in ms {
        sets += m.sets[range] ?? 0
        if let lo = m.low, let hi = m.high {
          low += lo
          high += hi
          withTarget += 1
        }
      }
      let hasTarget = withTarget > 0
      out.append(GroupRow(key: g.key, sets: sets, low: hasTarget ? low : nil, high: hasTarget ? high : nil,
                          status: status(sets: sets, low: hasTarget ? low : nil, high: hasTarget ? high : nil)))
    }
    return out
  }

  static func status(sets: Double, low: Double?, high: Double?) -> String {
    guard sets > 0 else { return "none" }
    guard let low, let high else { return "ok" }
    if sets < low { return "low" }
    if sets > high { return "high" }
    return "ok"
  }

  /// 「9/17 达标」: the muscles in their weekly range, of the muscles that have one.
  static func onTarget(_ load: MuscleLoad, range: MuscleLoad.Range) -> (ok: Int, total: Int) {
    let targeted = load.muscles.filter { $0.low != nil && $0.high != nil }
    let ok = targeted.filter { ($0.status[range] ?? "none") == "ok" }.count
    return (ok, targeted.count)
  }

  /// The common scale of the rows: the largest of their ranges' tops and their sets, with a little room.
  static func groupScale(_ rows: [GroupRow]) -> Double {
    var top = 1.0
    for r in rows {
      top = max(top, r.sets, r.high ?? 0)
    }
    return top * 1.15
  }

  // MARK: - history (14)

  /// One cell of the month: nil before the 1st (the grid starts on Monday).
  struct HistoryCell: Equatable {
    var date: String
    var trained: Bool
    var today: Bool
    var future: Bool
    /// Diameter of the dot (pt); a day without training is a small dot.
    var diameter: Double
    var opacity: Double
  }

  static func historyCells(_ m: TrainingMonth, today: String) -> [HistoryCell?] {
    let first = "\(m.month)-01"
    let lead = RecordDay.weekday(first) - 1
    var out: [HistoryCell?] = Array(repeating: nil, count: max(0, lead))
    let byDate = Dictionary(m.days.map { ($0.date, $0) }, uniquingKeysWith: { a, _ in a })
    for i in 0..<TrainingCalendarView.daysIn(m.month) {
      let date = RecordDay.add(first, i)
      let d = byDate[date]
      let trained = (d?.sessions ?? 0) > 0 && d?.intensity != "rest"
      out.append(HistoryCell(date: date, trained: trained, today: date == today, future: date > today,
                             diameter: trained ? Double(dotDiameter(d?.strain)) : 4,
                             opacity: trained ? RecordParams.intensityOpacity[d?.intensity ?? ""] ?? 0.75 : 1))
    }
    return out
  }

  static func dotDiameter(_ strain: Double?) -> CGFloat {
    let r = RecordParams.dotDiameter
    let f = min(1, max(0, (strain ?? 0) / RecordParams.strainScaleMax))
    return r.lowerBound + (r.upperBound - r.lowerBound) * CGFloat(f)
  }

  /// The trained days of the month, newest first (the session cards).
  static func trainedDays(_ m: TrainingMonth) -> [TrainingMonth.Day] {
    m.days.filter { $0.sessions > 0 && $0.intensity != "rest" && !$0.future }.sorted { $0.date > $1.date }
  }

  /// The month's average strain: the server's, else the trained days' own.
  static func averageStrain(_ m: TrainingMonth) -> Double? {
    if let a = m.avgStrain { return a }
    let xs = trainedDays(m).compactMap(\.strain)
    return xs.isEmpty ? nil : xs.reduce(0, +) / Double(xs.count)
  }

  /// A day of strength training draws its muscles, any other day a sport mark (`training/params.py STRENGTH_TYPES`).
  static func isStrength(_ types: [String]) -> Bool {
    types.contains { ["strength_training", "fitness_equipment"].contains($0) }
  }

  // MARK: - food (10)

  struct FoodDial: Equatable {
    /// 0…1 of the scale.
    var fraction: Double
    /// The goal's tick on the scale (nil = no goal).
    var target: Double?
    /// kcal left to the goal (negative = over), nil without a goal.
    var left: Double?
  }

  static func foodDial(kcal: Double?, goal: Double?) -> FoodDial {
    let k = max(0, kcal ?? 0), g = goal ?? 0
    let top = g > 0 ? max(g * RecordParams.goalHeadroom, k) : max(RecordParams.noGoalKcal, k * RecordParams.goalHeadroom)
    return FoodDial(fraction: min(1, k / top), target: g > 0 ? g / top : nil, left: g > 0 ? g - k : nil)
  }

  /// A macro reading's scale (g): past its goal, else a fixed scale.
  static func macroScale(_ value: Double?, goal: Double?, key: String) -> Double {
    let v = value ?? 0
    if let g = goal, g > 0 { return max(g * RecordParams.goalHeadroom, v) }
    return max(RecordParams.noGoalMacro[key] ?? 100, v * RecordParams.goalHeadroom)
  }

  /// Energy split of a meal (protein · carbs · fat, 4 / 4 / 9 kcal a gram), as shares; empty without macros.
  static func energySplit(_ m: Macros) -> [Double] {
    let p = (m.protein ?? 0) * 4, c = (m.carbs ?? 0) * 4, f = (m.fat ?? 0) * 9
    let sum = p + c + f
    guard sum > 0 else { return [] }
    return [p / sum, c / sum, f / sum]
  }
}
