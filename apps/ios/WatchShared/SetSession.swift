import Foundation

/// One set done on the watch.
struct LoggedSet: Codable, Equatable {
  var eventId: String
  var slug: String
  var setIndex: Int
  var weightKg: Double?
  var reps: Int?
  var durationS: Double?
  var at: Date
  var hrEnd: Int?
  var restBeforeS: Int?
}

/// How the set just logged compares with the same set last time (mockup T5 「比上次」).
enum SetDelta: Equatable {
  case weight(Double)
  case reps(Int)
  case seconds(Int)
  case same
  /// No last session to compare with.
  case none
}

/// The facts of the summary screens (mockups T9 / T10): numbers and directions, no score (plan 2.1).
struct SessionSummary: Equatable {
  var doneSets: Int
  var plannedSets: Int
  var minutes: Int
  var volumeKg: Double
  var avgHr: Int?
  /// Exercises whose best set beat / matched / fell short of the last session's best set.
  var better: Int
  var same: Int
  var worse: Int
  /// Mean heart-rate fall during the rests (bpm), nil without heart rate.
  var avgDropBpm: Int?
  /// Mean rest actually taken and mean rest planned, seconds.
  var avgRestS: Int?
  var plannedRestS: Int?
}

/// The training session on the watch (plan 2.1, mockups T1–T10): start → set → logged (1.2 s) → rest → next set …
/// → summary. A value type with no watchOS imports: the watch's model drives it with taps, the Digital Crown, a clock
/// tick and heart-rate samples, and carries out the effects it returns (haptics, events for the phone, the workout).
struct SetSession: Equatable {
  enum Phase: Equatable {
    /// T1: today's sets, 「开始」.
    case ready
    /// T2 / T3 / T4 / T7: the current set, two big numbers.
    case set
    /// T5: the receipt, until `until`.
    case logged(until: Date)
    /// T6: the countdown.
    case rest(RestTimer)
    /// T9 / T10.
    case summary
    /// 「完成」 tapped on the summary: the screen goes back to the start.
    case finished
  }

  /// Which number the crown changes on the set screen (default: reps, the one that most often differs from the plan).
  enum Focus: Equatable { case reps, weight }

  enum Haptic: Equatable { case start, success, restOver, end }

  enum Effect: Equatable {
    case haptic(Haptic)
    /// Hand to the phone (queued on the watch until delivered).
    case emit(WatchSetEvent)
    /// Start / end the HealthKit workout session (live heart rate, the workout in Health).
    case startWorkout
    case endWorkout
  }

  let plan: WatchPlan
  private(set) var phase: Phase = .ready
  /// Index of the exercise on screen.
  private(set) var exercise = 0
  /// Next set index per exercise (starts after the sets the phone already has).
  private(set) var nextSet: [Int]
  /// The values on the set screen.
  private(set) var weightKg: Double?
  private(set) var reps: Int?
  private(set) var durationS: Double?
  var focus: Focus = .reps
  private(set) var logged: [LoggedSet] = []
  private(set) var startedAt: Date?
  private(set) var endedAt: Date?
  /// Latest heart rate and the running sum for the mean.
  private(set) var heartRate: Int?
  private var hrSum = 0
  private var hrCount = 0
  /// Heart-rate falls measured at the end of each rest, and the rests taken.
  private(set) var restDrops: [Int] = []
  private(set) var restsTaken: [Int] = []
  private var restsPlanned: [Int] = []
  private let newId: () -> String

  init(plan: WatchPlan, newId: @escaping () -> String = { UUID().uuidString.lowercased() }) {
    self.plan = plan
    self.newId = newId
    nextSet = plan.exercises.map { min($0.done, max($0.sets, $0.done)) }
    exercise = plan.exercises.indices.first { nextSet[$0] < plan.exercises[$0].sets } ?? 0
    loadValues()
  }

  static func == (a: SetSession, b: SetSession) -> Bool {
    a.plan == b.plan && a.phase == b.phase && a.exercise == b.exercise && a.nextSet == b.nextSet && a.weightKg == b.weightKg
      && a.reps == b.reps && a.durationS == b.durationS && a.focus == b.focus && a.logged == b.logged && a.heartRate == b.heartRate
  }

  // MARK: - what the screens read

  var current: WatchExercise? { plan.exercises.indices.contains(exercise) ? plan.exercises[exercise] : nil }
  /// The set on screen, 0-based.
  var setIndex: Int { nextSet.indices.contains(exercise) ? nextSet[exercise] : 0 }
  var isTimed: Bool { current?.durationS != nil && current?.reps == nil }
  var allDone: Bool { plan.exercises.indices.allSatisfy { nextSet[$0] >= plan.exercises[$0].sets } }
  var lastLogged: LoggedSet? { logged.last }

  /// The same set of the last session, for 「上次 57.5×8」.
  func lastSet(_ ex: Int? = nil, index: Int? = nil) -> LastSet? {
    let e = ex ?? exercise
    guard plan.exercises.indices.contains(e) else { return nil }
    let last = plan.exercises[e].last
    guard !last.isEmpty else { return nil }
    let i = index ?? nextSet[e]
    return LastSet(last[min(i, last.count - 1)])
  }

  /// The set just logged against the same set last time (T5).
  func delta(of s: LoggedSet) -> SetDelta {
    guard let ex = plan.exercises.firstIndex(where: { $0.slug == s.slug }), let last = lastSet(ex, index: s.setIndex) else { return .none }
    if let w = s.weightKg, let lw = last.weightKg, abs(w - lw) >= 0.01 { return .weight(w - lw) }
    if let r = s.reps, let lr = last.reps, r != lr { return .reps(r - lr) }
    return .same
  }

  /// Heart rate now against the end of the set before the rest (T6 「↓14」).
  var restDrop: Int? {
    guard let end = logged.last?.hrEnd, let now = heartRate else { return nil }
    return end - now
  }

  /// The set after the rest (T6's small line under the clock): exercise index and set index.
  var upNext: (exercise: Int, set: Int)? { allDone ? nil : (exercise, setIndex) }

  /// Planned sets / sets already done, across the session (T1's ring, T9's 18/18).
  var doneSets: Int { plan.exercises.reduce(0) { $0 + $1.done } + Set(logged.map { "\($0.slug)#\($0.setIndex)" }).count }

  // MARK: - inputs

  /// The primary button, or Double Tap (plan 2.1): 开始 / 完成 / 跳过 / summary 完成.
  mutating func primary(at now: Date) -> [Effect] {
    switch phase {
    case .ready:
      startedAt = now
      phase = .set
      return [.startWorkout, .haptic(.start)]
    case .set:
      return logSet(at: now)
    case .logged:
      return []
    case .rest(let t):
      return leaveRest(t, at: now)
    case .summary:
      phase = .finished
      return []
    case .finished:
      return []
    }
  }

  /// Digital Crown detents (positive = up).
  mutating func crown(_ steps: Int) {
    guard steps != 0 else { return }
    switch phase {
    case .set:
      guard let ex = current else { return }
      if focus == .weight, ex.weighted {
        let w = (weightKg ?? 0) + Double(steps) * ex.weightStepKg
        weightKg = min(WatchParams.maxWeightKg, max(0, (w * 100).rounded() / 100))
      } else if isTimed {
        durationS = max(0, (durationS ?? 0) + Double(steps * 5))
      } else {
        reps = min(WatchParams.maxReps, max(0, (reps ?? 0) + steps))
      }
    case .rest(var t):
      t.adjust(steps: steps)
      phase = .rest(t)
    default:
      return
    }
  }

  /// Tap on a number: the crown now changes that one (T3 / T4).
  mutating func select(_ f: Focus) {
    guard phase == .set, current?.weighted == true || f == .reps else { return }
    focus = f
  }

  /// Horizontal swipe on the set screen: previous / next exercise (plan 2.1, the screen's only horizontal meaning).
  mutating func swipe(_ dir: Int) {
    guard phase == .set, dir != 0 else { return }
    let to = exercise + (dir > 0 ? 1 : -1)
    guard plan.exercises.indices.contains(to) else { return }
    exercise = to
    focus = .reps
    loadValues()
  }

  /// The clock: the receipt gives way to the rest, the rest ends with one haptic and goes back to the set.
  mutating func tick(at now: Date) -> [Effect] {
    switch phase {
    case .logged(let until) where now >= until:
      if allDone { return toSummary(at: now) }
      let total = logged.last.flatMap { s in plan.exercises.first { $0.slug == s.slug }?.restSec } ?? WatchParams.defaultRestSec
      phase = .rest(RestTimer(start: logged.last?.at ?? now, total: total))
      return []
    case .rest(let t) where t.isOver(at: now):
      // plan 2.1: one haptic at zero, then straight back to the set screen
      return [.haptic(.restOver)] + leaveRest(t, at: now)
    default:
      return []
    }
  }

  mutating func recordHeartRate(_ bpm: Int) {
    guard bpm > 0 else { return }
    heartRate = bpm
    hrSum += bpm
    hrCount += 1
  }

  /// 「结束」 (two taps: the button and its confirmation): to the summary. With nothing logged it just goes back.
  mutating func end(at now: Date) -> [Effect] {
    switch phase {
    case .ready, .summary, .finished:
      return []
    default:
      if logged.isEmpty {
        phase = .finished
        return [.endWorkout]
      }
      return toSummary(at: now)
    }
  }

  /// Tap on the rest ring: correct the set just logged (it has not left the watch's hands as far as the person is
  /// concerned; a new event with the same set index replaces it on the phone).
  mutating func amendLast(weightKg w: Double?, reps r: Int?, at now: Date) -> [Effect] {
    guard case .rest = phase, var s = logged.last else { return [] }
    s.weightKg = w
    s.reps = r
    s.eventId = newId()
    logged[logged.count - 1] = s
    return [.emit(event(for: s, day: plan.day))]
  }

  // MARK: - summary

  func summary(at now: Date = Date()) -> SessionSummary {
    let end = endedAt ?? now
    let minutes = startedAt.map { max(0, Int((end.timeIntervalSince($0) / 60).rounded())) } ?? 0
    let latest = latestPerSet()
    let volume = latest.reduce(0.0) { $0 + ($1.weightKg ?? 0) * Double($1.reps ?? 0) }
    var better = 0, same = 0, worse = 0
    for ex in plan.exercises {
      let mine = latest.filter { $0.slug == ex.slug }.map { LastSet(weightKg: $0.weightKg, reps: $0.reps) }
      let before = ex.last.compactMap(LastSet.init)
      guard let a = SetSession.best(mine), let b = SetSession.best(before) else { continue }
      switch SetSession.compare(a, b) {
      case 1: better += 1
      case 0: same += 1
      default: worse += 1
      }
    }
    func mean(_ xs: [Int]) -> Int? { xs.isEmpty ? nil : Int((Double(xs.reduce(0, +)) / Double(xs.count)).rounded()) }
    return SessionSummary(doneSets: doneSets, plannedSets: plan.totalSets, minutes: minutes, volumeKg: volume,
                          avgHr: hrCount > 0 ? Int((Double(hrSum) / Double(hrCount)).rounded()) : nil,
                          better: better, same: same, worse: worse, avgDropBpm: mean(restDrops),
                          avgRestS: mean(restsTaken), plannedRestS: mean(restsPlanned))
  }

  /// The heaviest set, more reps breaking a tie (body-weight sets: the most reps).
  static func best(_ sets: [LastSet]) -> LastSet? {
    sets.max { compare($0, $1) < 0 }
  }

  /// 1 = a beats b, 0 = equal, -1 = b beats a: weight first, then reps.
  static func compare(_ a: LastSet, _ b: LastSet) -> Int {
    let wa = a.weightKg ?? 0, wb = b.weightKg ?? 0
    if abs(wa - wb) >= 0.01 { return wa > wb ? 1 : -1 }
    let ra = a.reps ?? 0, rb = b.reps ?? 0
    return ra == rb ? 0 : (ra > rb ? 1 : -1)
  }

  // MARK: - private

  /// A corrected set counts once, with its newest values.
  private func latestPerSet() -> [LoggedSet] {
    var seen: [String: LoggedSet] = [:]
    var order: [String] = []
    for s in logged {
      let k = "\(s.slug)#\(s.setIndex)"
      if seen[k] == nil { order.append(k) }
      seen[k] = s
    }
    return order.compactMap { seen[$0] }
  }

  private mutating func logSet(at now: Date) -> [Effect] {
    guard let ex = current else { return [] }
    let restBefore = pendingRest
    let s = LoggedSet(eventId: newId(), slug: ex.slug, setIndex: setIndex, weightKg: ex.weighted ? weightKg : nil,
                      reps: isTimed ? nil : reps, durationS: isTimed ? durationS : nil, at: now, hrEnd: heartRate,
                      restBeforeS: restBefore)
    logged.append(s)
    pendingRest = nil
    nextSet[exercise] += 1
    if nextSet[exercise] >= ex.sets, let next = nextExerciseWithSets(after: exercise) {
      exercise = next
      focus = .reps
      loadValues()
    }
    phase = .logged(until: now.addingTimeInterval(WatchParams.receiptSec))
    return [.haptic(.success), .emit(event(for: s, day: plan.day))]
  }

  /// The rest taken before the next set (set when a rest ends, used by the next logged set).
  private var pendingRest: Int?

  private mutating func leaveRest(_ t: RestTimer, at now: Date) -> [Effect] {
    let took = t.elapsed(at: now)
    restsTaken.append(took)
    restsPlanned.append(t.total)
    pendingRest = took
    if let d = restDrop { restDrops.append(d) }
    phase = .set
    return []
  }

  private mutating func toSummary(at now: Date) -> [Effect] {
    endedAt = now
    phase = .summary
    let end = WatchSetEvent(id: newId(), kind: .end, day: plan.day, slug: nil, setIndex: nil, reps: nil, weightKg: nil,
                            durationS: nil, at: now, hrEnd: nil, restBeforeS: nil)
    return [.emit(end), .endWorkout, .haptic(.end)]
  }

  private func nextExerciseWithSets(after i: Int) -> Int? {
    let n = plan.exercises.count
    guard n > 0 else { return nil }
    for k in 1...n {
      let j = (i + k) % n
      if nextSet[j] < plan.exercises[j].sets { return j }
    }
    return nil
  }

  /// The set screen's numbers: what was just done in this exercise, else the plan's target.
  private mutating func loadValues() {
    guard let ex = current else { return }
    if let prev = logged.last(where: { $0.slug == ex.slug }) {
      weightKg = prev.weightKg ?? ex.weightKg
      reps = prev.reps ?? ex.reps
      durationS = prev.durationS ?? ex.durationS
    } else {
      weightKg = ex.weightKg
      reps = ex.reps
      durationS = ex.durationS
    }
  }

  private func event(for s: LoggedSet, day: String) -> WatchSetEvent {
    WatchSetEvent(id: s.eventId, kind: .set, day: day, slug: s.slug, setIndex: s.setIndex, reps: s.reps, weightKg: s.weightKg,
                  durationS: s.durationS, at: s.at, hrEnd: s.hrEnd, restBeforeS: s.restBeforeS)
  }
}
