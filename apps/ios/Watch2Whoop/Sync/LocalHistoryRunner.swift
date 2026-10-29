import BackgroundTasks
import UIKit
import Foundation

/// The signed-in account's database, client and the two long jobs on it (#340 local history), shared by the UI's
/// `AccountSession` and the background task, so a background launch without any view uses the same database and a
/// recompute never runs twice at once.
final class AccountRuntime {
  let key: String
  let db: AppDatabase
  let api: APIClient
  let scoring: ScoringSync
  let history: HistoryDownload

  private static let lock = NSLock()
  private static var _current: AccountRuntime?

  static var current: AccountRuntime? {
    lock.lock()
    defer { lock.unlock() }
    return _current
  }

  init(key: String, db: AppDatabase, api: APIClient) {
    self.key = key
    self.db = db
    self.api = api
    scoring = ScoringSync(db: db, api: api)
    history = HistoryDownload(db: db, api: api)
  }

  /// The runtime of this account: the current one when it is the same account, else a new one (the database opened).
  static func open(server: String, userId: Int, api: APIClient) throws -> AccountRuntime {
    let key = AppDatabase.accountKey(server: server, userId: userId)
    lock.lock()
    defer { lock.unlock() }
    if let c = _current, c.key == key { return c }
    let rt = AccountRuntime(key: key, db: try AppDatabase.open(server: server, userId: userId), api: api)
    _current = rt
    return rt
  }

  /// Sign-out: forget it before the database directory goes.
  static func close() {
    lock.lock()
    _current = nil
    lock.unlock()
  }

  /// A background launch: the stored account (server, user id, token), without the UI.
  static func fromStore(_ store: CredentialStore = CredentialStore()) -> AccountRuntime? {
    if let c = current { return c }
    guard let cfg = store.config, let uid = cfg.userId, store.accountToken != nil, let base = URL(string: cfg.server) else {
      return nil
    }
    let api = APIClient(baseURL: base, token: { store.accountToken })
    return try? open(server: cfg.server, userId: uid, api: api)
  }
}

/// The history download and the full recompute in a `BGProcessingTask` (`LocalHistoryParams.backgroundTask`): iOS runs
/// it when the phone is idle — on power unless the user allows cellular, with a network. One task does a slice of the
/// work until iOS ends it (the expiration handler cancels; everything resumes from `meta` / the cursor next time) and
/// schedules the next while anything is left.
enum HistoryBackground {
  /// Before launch finishes (AppDelegate).
  static func register() {
    BGTaskScheduler.shared.register(forTaskWithIdentifier: LocalHistoryParams.backgroundTask, using: nil) { task in
      guard let task = task as? BGProcessingTask else { task.setTaskCompleted(success: false); return }
      handle(task)
    }
  }

  /// Ask iOS for a run (replaces an earlier request; harmless when there is nothing to do).
  static func schedule() {
    let r = BGProcessingTaskRequest(identifier: LocalHistoryParams.backgroundTask)
    r.requiresNetworkConnectivity = true
    r.requiresExternalPower = !EngineState.config().cellularHistory
    r.earliestBeginDate = Date().addingTimeInterval(LocalHistoryParams.backgroundDelay)
    try? BGTaskScheduler.shared.submit(r)
  }

  private static func handle(_ task: BGProcessingTask) {
    let work = start()
    task.expirationHandler = { work.cancel() }
    Task.detached {
      let done = await work.value
      if !done { schedule() }
      task.setTaskCompleted(success: true)
    }
  }

  /// The task's work (memory peak kept as `perf.peak.background`); true = nothing left.
  static func start() -> Task<Bool, Never> {
    Task.detached(priority: .utility) { () -> Bool in
      guard let rt = AccountRuntime.fromStore() else { return true }
      return await MemoryProbe.measure("background", db: rt.db) {
        await LocalHistoryWork.run(rt, reason: "background", onPower: true)
      }
    }
  }

  #if DEBUG
  /// The simulator has no BGTaskScheduler: with `SIMCTL_CHILD_W2W_DEV_BG=1` the same work runs whenever the app goes to
  /// the background, inside a background-task assertion (iOS ends it after ~30 s; the work stops there and resumes
  /// next time, as with a real BGProcessingTask). For measuring the background memory peak in the simulator.
  static func devHook() {
    guard ProcessInfo.processInfo.environment["W2W_DEV_BG"] == "1" else { return }
    NotificationCenter.default.addObserver(forName: UIApplication.didEnterBackgroundNotification, object: nil, queue: .main) { _ in
      var id = UIBackgroundTaskIdentifier.invalid
      let work = start()
      id = UIApplication.shared.beginBackgroundTask(withName: "w2w.history.dev") { work.cancel() }
      Task.detached {
        _ = await work.value
        await MainActor.run { UIApplication.shared.endBackgroundTask(id) }
      }
    }
  }
  #endif
}

/// One slice of the long work: download history pages, then a pending full recompute, then storage upkeep.
enum LocalHistoryWork {
  /// true when nothing is left (no reschedule needed).
  static func run(_ rt: AccountRuntime, reason: String, onPower: Bool, maxPages: Int = .max) async -> Bool {
    let h = await rt.history.run(reason: reason, maxPages: maxPages)
    var left = h.phase != "done" && h.phase != "server_mode" && h.phase != "waiting_restore"
    if !Task.isCancelled, (try? rt.db.meta(ScoringSync.fullKey)) ?? nil != nil {
      let r = await rt.scoring.run(reason: reason)
      if r.state == "partial" { left = true }
    }
    if !Task.isCancelled { _ = try? rt.db.maintain(onPower: onPower) }
    return !left
  }
}
