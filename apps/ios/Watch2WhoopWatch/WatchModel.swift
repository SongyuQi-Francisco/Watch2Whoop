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
}
