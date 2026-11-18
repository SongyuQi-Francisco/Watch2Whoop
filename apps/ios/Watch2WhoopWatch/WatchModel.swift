import Combine
import Foundation

/// Glue of the watch app: the phone's context, the home (`WatchHome`), the set session (`SetSession`), the quick logs, the
/// event queue (`WatchEventQueue`), the workout and the clock. The rules live in the shared plain-Swift types; this class
/// carries out their effects.
@MainActor
final class WatchModel: ObservableObject {
  @Published private(set) var context: WatchContext?
  @Published private(set) var session: SetSession? { didSet { sessionChanged(from: oldValue) } }
  @Published private(set) var queue: WatchEventQueue
  @Published private(set) var quickLogs: WatchQuickLogs
  @Published private(set) var reachable = false
  @Published private(set) var now = Date()
  /// The screens above home (home is the root whenever no session runs; plan 2.4).
  @Published var path: [WatchRoute] = []
  /// 「计划没同步」 was tapped: the key shows that it asked, for a moment.
  @Published private(set) var askedPlanAt: Date?
  /// The core was held: voice is not on the watch yet (mockup S7's dimmed core), shown for a moment.
  @Published private(set) var voiceRefusedAt: Date?
  /// Screenshots only: the set screen shows which number the crown changes (T3) / the summary opens on its facts (T10).
  @Published private(set) var demoShowFocus = false
  @Published private(set) var demoFacts = false

  private let link = WatchLinkClient()
  private let workout = WorkoutController()
  private var ticker: AnyCancellable?
  /// All the watch keeps (one place, so the widgets' step can move it to an App Group: `WatchStore`).
  private var store = WatchStore()
  /// Debug builds: the interactive seed (`W2W_WATCH_SEED`) plays the phone; frozen demos (`W2W_WATCH_DEMO`) stop the clock.
  private var seed: String?
  private var frozen = false

  init() {
    #if DEBUG
    let env = ProcessInfo.processInfo.environment
    if let s = env["W2W_WATCH_SEED"], !s.isEmpty, (env["W2W_WATCH_DEMO"] ?? "").isEmpty {
      seed = s
      // its own storage, so the seed never lands in (or reads) a real phone's context
      store = WatchStore(defaults: UserDefaults(suiteName: "w2w.watch.seed") ?? .standard)
      if s == "reset" { store.clear() }
    }
    #endif
    queue = store.queue()
    quickLogs = store.quickLogs()
    context = store.context()
    if let lang = context?.lang { Lang.override = lang }
  }

  private var started = false

  func start() {
    guard !started else { return }
    started = true
    #if DEBUG
    // simulator screenshots: `SIMCTL_CHILD_W2W_WATCH_DEMO=d1` shows that screen with synthetic data, frozen in time
    if let id = ProcessInfo.processInfo.environment["W2W_WATCH_DEMO"], !id.isEmpty {
      frozen = true
      demo(id)
      return
    }
    #endif
    quickLogs.prune(now: Date())
    restoreSession()
    ticker = Timer.publish(every: 0.25, on: .main, in: .common).autoconnect().sink { [weak self] d in self?.tick(d) }
    #if DEBUG
    if let seed {
      startSeed(seed)
      return
    }
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
    Task { await workout.requestAuthorization() }
  }

  // MARK: - what the screens read

  /// Today in the watch's time zone (the phone sends the user's local date with its numbers and the plan).
  var today: String { Self.day(now) }

  nonisolated static func day(_ d: Date) -> String {
    let f = DateFormatter()
    f.calendar = Calendar(identifier: .gregorian)
    f.locale = Locale(identifier: "en_US_POSIX")
    f.dateFormat = "yyyy-MM-dd"
    return f.string(from: d)
  }

  var running: Bool {
    guard let p = session?.phase else { return false }
    return p != .ready && p != .finished
  }

  var syncState: WatchSyncState {
    WatchSyncState.derive(context: context, queue: queue, reachable: reachable, now: now)
  }

  var home: WatchHome { WatchHome.derive(context: context, today: today, sync: syncState) }

  /// No heart rate this session (another workout is running, or no permission): plan 4.2's light mode.
  var lightMode: Bool { workout.light }

  /// The plan the start screen offers: today's, with at least one exercise.
  var todaysPlan: WatchPlan? {
    guard let p = context?.plan, p.day == today, !p.exercises.isEmpty else { return nil }
    return p
  }

  /// The quick-log tiles: the phone's meals (at most three) and the water tile.
  var quick: WatchQuick? {
    guard let q = context?.quick else { return nil }
    var out = q
    out.meals = Array(q.meals.prefix(WatchParams.quickMealsMax))
    return out
  }

  /// Seconds since a moment, for the short-lived marks (「问过手机」, 「语音不可用」).
  func shows(_ at: Date?, for seconds: TimeInterval) -> Bool { at.map { now.timeIntervalSince($0) < seconds } ?? false }

  // MARK: - inputs: home

  /// Home's left capsule (and Double Tap on home).
  func homeKey() {
    switch home.key {
    case .train: path = [.start]
    case .meal: path = [.quick]
    case .planMissing: askPhone()
    }
  }

  /// The Jarvis core: tap = the quick-log tiles (plan 2.2; the owner's choice: tap = quick log, hold = talk).
  func coreTap() { path = [.quick] }

  /// Hold on the core = talk (the owner's choice: tap = quick log, hold = talk). THE entry point of voice: phase 2b
  /// starts listening (mockup M1 / J1) in the `.available` case; `WatchVoice.derive` decides. Until then the core is
  /// dimmed and a hold only says so (mockup S7: a mark on the core and the failure haptic).
  func coreHold() {
    switch home.voice {
    case .available:
      break   // phase 2b: start the voice flow here (record → the watch's own token → /v1/assistant/transcribe)
    case .unavailable:
      voiceRefusedAt = Date()
      now = Date()
      Haptics.failed()
    }
  }

  /// 「计划没同步」 / the sync mark: ask the phone for today's plan and numbers again (S9's retry; S1 「立即同步」).
  func askPhone() {
    askedPlanAt = Date()
    now = Date()
    requestPlan()
  }

  /// S6 「重试」: the failed transfers again, and a fresh context.
  func retry() {
    queue.retryFailed()
    saveQueue()
    flush()
    requestPlan()
  }

  func requestPlan() {
    #if DEBUG
    if let seed { seedContext(seed); return }
    #endif
    link.requestPlan()
  }

  // MARK: - inputs: training

  /// T1 「开始」.
  func startSession() {
    guard let p = todaysPlan, !running else { return }
    var s = SetSession(plan: p)
    let fx = s.primary(at: Date())
    path = []
    session = s
    run(fx)
  }

  func primary() {
    guard var s = session else { return }
    let fx = s.primary(at: Date())
    session = s
    run(fx)
    if s.phase == .finished { finishSession() }
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
    if s.phase == .finished { finishSession() }
  }

  func amend(weightKg: Double?, reps: Int?) {
    guard var s = session else { return }
    let fx = s.amendLast(weightKg: weightKg, reps: reps, at: Date())
    session = s
    run(fx)
  }

  // MARK: - inputs: quick log (plan 2.2, mockups M3–M5)

  func logMeal(_ m: WatchQuickMeal) {
    let l = WatchQuickLog.meal(m, id: Self.newId(), day: today, at: Date())
    record(l)
  }

  func logWater(_ ml: Int) {
    record(WatchQuickLog.water(ml, id: Self.newId(), day: today, at: Date()))
  }

  /// M4 「撤销」: an event the phone has not got is simply taken back; otherwise the phone gets an `undo` for it.
  func undo(_ id: String) {
    guard let l = quickLogs.log(id), !l.undone else { path = []; return }
    quickLogs.markUndone(id)
    saveLogs()
    if !queue.cancel(id) {
      queue.enqueue(.quick(.undo, id: Self.newId(), day: l.day, at: Date(), target: id))
    }
    saveQueue()
    flush()
    Haptics.undone()
    path = []
  }

  private func record(_ l: WatchQuickLog) {
    quickLogs.add(l)
    saveLogs()
    queue.enqueue(l.event)
    saveQueue()
    flush()
    Haptics.play(.success)
    path = [.receipt(l.id)]
  }

  // MARK: - private

  private static func newId() -> String { UUID().uuidString.lowercased() }

  private func tick(_ d: Date) {
    guard !frozen else { return }
    // the clock redraws the screens only while a session runs (countdown, receipt) or a short mark is up; otherwise twice
    // a minute is enough for the sync mark's 「2分前」 (plan 4.7: no work while idle)
    let marks = shows(askedPlanAt, for: 3) || shows(voiceRefusedAt, for: 2)
    if running || marks || d.timeIntervalSince(now) >= 30 { now = d }
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
      case .haptic(let h): if !frozen { Haptics.play(h) }
      case .emit(let ev):
        queue.enqueue(ev)
        saveQueue()
        flush()
      case .startWorkout: if !frozen && seed == nil { workout.start() }
      case .endWorkout: if !frozen && seed == nil { workout.end() }
      }
    }
  }

  /// The summary's 「完成」 (or ending with nothing logged): back to home.
  private func finishSession() {
    session = nil
    path = []
  }

  private func received(_ c: WatchContext) {
    context = c
    Lang.override = c.lang
    queue.heard(at: Date())
    now = Date()
    saveQueue()
    store.save(c)
    flush()
  }

  private func delivered(_ id: String) {
    queue.markDelivered(id, at: Date())
    saveQueue()
    if queue.isEmpty && queue.deliveredInBatch > 1 { Haptics.delivered() }
  }

  /// Hands every pending event to WatchConnectivity (it keeps them until the phone is back).
  private func flush() {
    #if DEBUG
    if seed != nil { seedDeliver(); return }
    #endif
    guard link.isActive else { return }
    for e in queue.takePending() { link.send(e) }
    saveQueue()
  }

  private func saveQueue() { store.save(queue) }
  private func saveLogs() { store.save(quickLogs) }

  // MARK: - a running session survives a relaunch

  private func sessionChanged(from old: SetSession?) {
    guard !frozen else { return }
    guard let s = session, s.phase != .finished, s.phase != .ready else {
      if old != nil { store.save(nil as SavedSession?) }
      return
    }
    // heart-rate samples alone are not worth a write; every step of the session is
    if let o = old, o.phase == s.phase, o.logged == s.logged, o.exercise == s.exercise, o.nextSet == s.nextSet,
       o.weightKg == s.weightKg, o.reps == s.reps, o.durationS == s.durationS, o.focus == s.focus {
      return
    }
    store.save(SavedSession(session: s, savedAt: Date()))
  }

  /// On launch: a session that was running comes back where it was (its workout too); one left for hours is ended, so
  /// its day is submitted on the phone like any other ending.
  private func restoreSession() {
    guard let saved = store.session() else { return }
    if saved.resumable(now: Date()) {
      session = saved.session
      if seed == nil { workout.recover() }
      return
    }
    var s = saved.session
    let fx = s.end(at: saved.savedAt)
    for case .emit(let e) in fx { queue.enqueue(e) }
    saveQueue()
    if seed == nil { workout.recover(thenEnd: true) }
    store.save(nil as SavedSession?)
  }

  #if DEBUG
  // MARK: - interactive seed (Debug only): `SIMCTL_CHILD_W2W_WATCH_SEED=1 | rest | noplan | stale | signedout | reset`

  /// A synthetic phone for simulator acceptance: today's context for 小林 dated today (scores, a plan, quick meals),
  /// rebuilt at every launch so it can never be yesterday's; the clock runs and every flow can be tapped. Events are
  /// "delivered" after a moment and the quick logs counted, as the real phone would.
  private func startSeed(_ kind: String) {
    reachable = true   // the synthetic phone is always near
    queue.heard(at: Date().addingTimeInterval(-120))
    seedContext(kind)
    flush()
  }

  private func seedContext(_ kind: String) {
    let t = Date()
    let day = Self.day(t)
    var c = WatchDemoData.context(day: day, sentAt: t, rest: kind == "rest", planMissing: kind == "noplan",
                                  stale: kind == "stale", lang: Lang.code)
    if kind == "signedout" { c = WatchContext(signedIn: false, plan: nil, lang: Lang.code, sentAt: t) }
    // keep what the "phone" already counted
    if let old = context?.quick, var q = c.quick, old.day == q.day {
      q.kcal = old.kcal
      q.results = old.results
      c.quick = q
    }
    context = c
    now = t
  }

  /// The seed's phone takes every event after 0.8 s and counts the quick logs into the day's intake.
  private func seedDeliver() {
    let sent = queue.takePending()
    saveQueue()
    guard !sent.isEmpty else { return }
    Task { @MainActor in
      try? await Task.sleep(nanoseconds: 800_000_000)
      for e in sent {
        self.seedApply(e)
        self.delivered(e.id)
      }
      self.now = Date()
    }
  }

  private func seedApply(_ e: WatchSetEvent) {
    guard var c = context, var q = c.quick else { return }
    func kcal(_ id: String?) -> Double {
      guard let id, let l = quickLogs.log(id), l.kind == .meal else { return 0 }
      return l.kcal ?? 0
    }
    switch e.kind {
    case .meal:
      q.kcal = (q.kcal ?? 0) + kcal(e.id)
      q.results[e.id] = true
    case .water:
      q.results[e.id] = true
    case .undo:
      if let t = e.target, q.results[t] == true {
        q.kcal = (q.kcal ?? 0) - kcal(t)
        q.results[t] = false
      }
    case .set, .end:
      return
    }
    c.quick = q
    context = c
  }

  // MARK: - frozen demos (screenshots): `SIMCTL_CHILD_W2W_WATCH_DEMO=<id>`

  /// The mockups' screens (docs/design/watch: T1–T10, D1 / D2, M3–M5, S1–S9) with synthetic data (nickname 小林); no
  /// phone, no HealthKit, the clock stopped. Debug builds only.
  private func demo(_ id: String) {
    let t = Date()
    let day = Self.day(t)
    let base = WatchDemoData.context(day: day, sentAt: t, rest: id == "d2", planMissing: id == "s9", stale: id == "s3",
                                     lang: Lang.code)
    let p = base.plan ?? WatchDemoData.plan(day: day)
    var q = WatchEventQueue()
    q.heard(at: t.addingTimeInterval(-120))
    var ctx: WatchContext? = base
    var s = SetSession(plan: p)
    var clock = t.addingTimeInterval(-3 * 3600)
    func at(_ dt: TimeInterval) -> Date { clock = clock.addingTimeInterval(dt); return clock }
    /// one set: optional crown change, log, receipt, rest
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
                    at: t, hrEnd: nil, restBeforeS: nil)
    }
    var frozenAt = t
    var keepSession = true
    var route: [WatchRoute] = []
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
      frozenAt = clock.addingTimeInterval(0.6)
    case "t6":
      _ = s.primary(at: at(0))
      set(hr: 138, restHr: 110)
      s.recordHeartRate(132)
      _ = s.primary(at: at(45))
      _ = s.tick(at: at(1.3))
      s.recordHeartRate(118)
      frozenAt = clock.addingTimeInterval(16.7)
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
      frozenAt = clock
    case "t1":
      keepSession = false
      route = [.start]
    case "s2":
      keepSession = false
      ctx?.today?.phone = WatchPhoneSync(.busy)
    case "s5":
      keepSession = false
      for i in 0..<6 { q.enqueue(event("q\(i)")) }
      _ = q.takePending()
      for i in 0..<4 { q.markDelivered("q\(i)", at: t) }
      reachable = true
    case "s6":
      keepSession = false
      q.enqueue(event("a"))
      _ = q.takePending()
      q.markFailed("a")
    case "s7":
      keepSession = false
      ctx?.today?.phone = WatchPhoneSync(.offline, tr("离线", "Offline"))
    case "s8":
      keepSession = false
      ctx = nil
    case "m5":
      keepSession = false
      route = [.quick]
    case "m3", "m4", "m3w":
      keepSession = false
      let meal = base.quick?.meals.first { $0.id == 3 } ?? base.quick!.meals[0]
      let l = id == "m3w" ? WatchQuickLog.water(250, id: "demo-log", day: day, at: t) : WatchQuickLog.meal(meal, id: "demo-log", day: day, at: t)
      quickLogs = WatchQuickLogs()
      quickLogs.add(l)
      route = id == "m4" ? [.receipt(l.id), .undo(l.id)] : [.receipt(l.id)]
    default:   // d1, d2, s1, s3, s9
      keepSession = false
    }
    context = ctx
    queue = q
    session = keepSession ? s : nil
    now = keepSession ? frozenAt : t
    path = route
  }
  #endif
}
