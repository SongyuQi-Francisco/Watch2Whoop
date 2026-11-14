import Combine
import Foundation

/// Glue of the watch app: the phone's context, the set session (`SetSession`), the event queue (`WatchEventQueue`),
/// the workout and the clock. The rules live in the shared plain-Swift types; this class carries out their effects.
@MainActor
final class WatchModel: ObservableObject {
  @Published private(set) var context: WatchContext?
  @Published private(set) var session: SetSession?
  @Published private(set) var queue: WatchEventQueue
  @Published private(set) var reachable = false
  @Published private(set) var now = Date()
  /// Screenshots only: the set screen shows which number the crown changes (T3) / the summary opens on its facts (T10).
  @Published private(set) var demoShowFocus = false
  @Published private(set) var demoFacts = false

  private let link = WatchLinkClient()
  private let workout = WorkoutController()
  private var ticker: AnyCancellable?
  private let defaults = UserDefaults.standard
  private static let contextKey = "w2w.watch.context.v1"
  private static let queueKey = "w2w.watch.queue.v1"

  init() {
    queue = WatchEventQueue.load(defaults.data(forKey: Self.queueKey))
    context = WatchCodec.decode(WatchContext.self, defaults.data(forKey: Self.contextKey))
    if let lang = context?.lang { Lang.override = lang }
  }

  private var started = false

  func start() {
    guard !started else { return }
    started = true
    #if DEBUG
    // simulator screenshots: `SIMCTL_CHILD_W2W_WATCH_DEMO=t6` shows that screen with synthetic data, frozen in time
    if let id = ProcessInfo.processInfo.environment["W2W_WATCH_DEMO"], !id.isEmpty { demo(id); return }
    #endif
    link.onContext = { [weak self] c in self?.received(c) }
    link.onReachable = { [weak self] r in
      self?.reachable = r
      if r { self?.flush() }
    }
    link.onDelivered = { [weak self] id in self?.delivered(id) }
    link.onFailed = { [weak self] id in
      self?.queue.markFailed(id)
      self?.saveQueue()
      Haptics.failed()
    }
    link.onActivated = { [weak self] in
      guard let self else { return }
      self.queue.restoreAfterLaunch(inFlight: self.link.inFlight)
      self.flush()
    }
    link.activate()
    workout.onHeartRate = { [weak self] bpm in self?.session?.recordHeartRate(bpm) }
    ticker = Timer.publish(every: 0.25, on: .main, in: .common).autoconnect().sink { [weak self] d in self?.tick(d) }
    Task { await workout.requestAuthorization() }
  }

  // MARK: - what the screens read

  /// Today in the watch's time zone (the phone sends the user's local date with the plan).
  var today: String {
    let f = DateFormatter()
    f.calendar = Calendar(identifier: .gregorian)
    f.locale = Locale(identifier: "en_US_POSIX")
    f.dateFormat = "yyyy-MM-dd"
    return f.string(from: now)
  }

  var running: Bool {
    guard let p = session?.phase else { return false }
    return p != .ready && p != .finished
  }

  var syncState: WatchSyncState {
    WatchSyncState.derive(context: context, queue: queue, reachable: reachable, today: today, sessionRunning: running, now: now)
  }

  /// No heart rate this session (another workout is running, or no permission): plan 4.2's light mode.
  var lightMode: Bool { workout.light }

  /// The plan the start screen offers: today's, with at least one exercise.
  var todaysPlan: WatchPlan? {
    guard let p = context?.plan, p.day == today, !p.exercises.isEmpty else { return nil }
    return p
  }

  // MARK: - inputs

  func primary() {
    if session == nil || session?.phase == .finished {
      guard let p = todaysPlan else { return }
      session = SetSession(plan: p)
    }
    guard var s = session else { return }
    let fx = s.primary(at: Date())
    session = s
    run(fx)
    if s.phase == .finished { session = nil }
  }

  func crown(_ steps: Int) {
    session?.crown(steps)
  }

  func select(_ f: SetSession.Focus) { session?.select(f) }
  func swipe(_ dir: Int) { session?.swipe(dir) }

  func end() {
    guard var s = session else { return }
    let fx = s.end(at: Date())
    session = s
    run(fx)
    if s.phase == .finished { session = nil }
  }

  func amend(weightKg: Double?, reps: Int?) {
    guard var s = session else { return }
    let fx = s.amendLast(weightKg: weightKg, reps: reps, at: Date())
    session = s
    run(fx)
  }

  /// S6 「重试」 / S1 「立即同步」.
  func retry() {
    queue.retryFailed()
    flush()
    link.requestPlan()
  }

  func requestPlan() { link.requestPlan() }

  // MARK: - private

  private func tick(_ d: Date) {
    // the clock redraws the screens only while a session runs (countdown, receipt); otherwise twice a minute is enough
    // for the sync mark's 「2分前」 (plan 4.7: no work while idle)
    if running || d.timeIntervalSince(now) >= 30 { now = d }
    guard var s = session else { return }
    let fx = s.tick(at: d)
    if !fx.isEmpty || s != session {
      session = s
      run(fx)
    }
  }

  private func run(_ effects: [SetSession.Effect]) {
    for e in effects {
      switch e {
      case .haptic(let h): Haptics.play(h)
      case .emit(let ev):
        queue.enqueue(ev)
        saveQueue()
        flush()
      case .startWorkout: workout.start()
      case .endWorkout: workout.end()
      }
    }
  }

  private func received(_ c: WatchContext) {
    context = c
    Lang.override = c.lang
    queue.heard(at: Date())
    saveQueue()
    defaults.set(WatchCodec.encode(c), forKey: Self.contextKey)
    flush()
  }

  private func delivered(_ id: String) {
    queue.markDelivered(id, at: Date())
    saveQueue()
    if queue.isEmpty && queue.deliveredInBatch > 1 { Haptics.delivered() }
  }

  /// Hands every pending event to WatchConnectivity (it keeps them until the phone is back).
  private func flush() {
    guard link.isActive else { return }
    for e in queue.takePending() { link.send(e) }
    saveQueue()
  }

  private func saveQueue() { defaults.set(queue.data(), forKey: Self.queueKey) }

  #if DEBUG
  /// The mockups' screens (docs/design/watch, T1–T10 and S1–S9) with a synthetic plan (nickname 小林); no phone, no
  /// HealthKit, the clock stopped. Debug builds only.
  private func demo(_ id: String) {
    let t0 = Date().addingTimeInterval(-3 * 3600)
    func ex(_ slug: String, _ name: String, _ sets: Int, _ kg: Double?, _ reps: Int, _ last: String) -> WatchExercise {
      WatchExercise(slug: slug, name: name, sets: sets, done: 0, reps: reps, weightKg: kg, durationS: nil, weighted: kg != nil,
                    restSec: 90, weightStepKg: 2.5, last: Array(repeating: last, count: sets))
    }
    let plan = WatchPlan(day: "", title: tr("上肢 A", "Upper A"), exercises: [
      ex("bench", tr("卧推", "Bench"), 4, 60, 8, "60×7"), ex("ohp", tr("肩推", "OH press"), 3, 25, 10, "22.5×10"),
      ex("row", tr("划船", "Row"), 4, 60, 10, "57.5×10"), ex("pulldown", tr("下拉", "Pulldown"), 3, 50, 10, "50×10"),
      ex("curl", tr("弯举", "Curl"), 2, 12, 12, "12×12"), ex("pushdown", tr("下压", "Pushdown"), 2, 20, 12, "20×13"),
    ])
    var p = plan
    p.day = today
    var q = WatchEventQueue()
    q.heard(at: Date().addingTimeInterval(-120))
    var ctx: WatchContext? = WatchContext(signedIn: true, plan: p, lang: Lang.code, sentAt: Date())
    var s = SetSession(plan: p)
    var clock = t0
    func at(_ dt: TimeInterval) -> Date { clock = clock.addingTimeInterval(dt); return clock }
    /// one set: optional crown change, log, receipt, rest; `skip` = leave the rest after 95 s
    func set(hr: Int, crown: Int = 0, restHr: Int? = nil) {
      s.recordHeartRate(hr)
      s.crown(crown)
      _ = s.primary(at: at(45))
      _ = s.tick(at: at(1.3))
      if let restHr { s.recordHeartRate(restHr) }
      _ = s.primary(at: at(93))
    }
    func event(_ id: String) -> WatchSetEvent {
      WatchSetEvent(id: id, kind: .set, day: p.day, slug: "bench", setIndex: 0, reps: 8, weightKg: 60, durationS: nil,
                    at: Date(), hrEnd: nil, restBeforeS: nil)
    }
    var frozen = Date()
    var keepSession = true
    switch id {
    case "t2", "t3", "t4", "s4":
      _ = s.primary(at: at(0))
      set(hr: 140, restHr: 112)
      s.recordHeartRate(132)
      if id == "t3" { s.crown(1); demoShowFocus = true }
      if id == "t4" { s.select(.weight); s.crown(1) }
      if id == "s4" {
        s.recordHeartRate(128)
        for i in 0..<6 { q.enqueue(event("q\(i)")) }
        reachable = false
      }
    case "t5":
      _ = s.primary(at: at(0))
      s.recordHeartRate(132)
      s.crown(1)
      _ = s.primary(at: at(45))
      frozen = clock.addingTimeInterval(0.6)
    case "t6":
      _ = s.primary(at: at(0))
      set(hr: 138, restHr: 110)
      s.recordHeartRate(132)
      _ = s.primary(at: at(45))
      _ = s.tick(at: at(1.3))
      s.recordHeartRate(118)
      frozen = clock.addingTimeInterval(16.7)
    case "t7":
      _ = s.primary(at: at(0))
      for _ in 0..<4 { set(hr: 135, restHr: 115) }
      s.recordHeartRate(124)
    case "t9", "t10":
      _ = s.primary(at: at(0))
      let hrs = [138, 142, 145, 140, 131, 133, 136, 128, 130, 134, 137, 126, 129, 131, 120, 123, 118, 121]
      for (i, h) in hrs.enumerated() {
        if i == hrs.count - 1 {
          s.recordHeartRate(h)
          _ = s.primary(at: at(45))
          _ = s.tick(at: at(1.3))
        } else {
          set(hr: h + 6, crown: i % 5 == 0 ? 1 : 0, restHr: h - 10)
        }
      }
      demoFacts = id == "t10"
      frozen = clock
    case "s2":
      keepSession = false
      q.enqueue(event("a"))
      _ = q.takePending()
      reachable = true
    case "s5":
      keepSession = false
      for i in 0..<6 { q.enqueue(event("q\(i)")) }
      _ = q.takePending()
      for i in 0..<4 { q.markDelivered("q\(i)", at: Date()) }
      reachable = true
    case "s6":
      keepSession = false
      q.enqueue(event("a"))
      _ = q.takePending()
      q.markFailed("a")
    case "s8":
      keepSession = false
      ctx = nil
    case "s9":
      keepSession = false
      ctx = WatchContext(signedIn: true, plan: nil, lang: Lang.code, sentAt: Date())
    default:   // t1, s1
      keepSession = false
    }
    context = ctx
    queue = q
    session = keepSession ? s : nil
    now = keepSession ? frozen : Date()
  }
  #endif
}
