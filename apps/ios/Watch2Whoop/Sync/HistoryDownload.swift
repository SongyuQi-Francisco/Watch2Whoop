import Foundation
import GRDB
import Network
import UIKit

/// Downloads the user's whole history into the compact history tables (#340 local history; docs/ios-native-app.md
/// section 9.1 「本机完整历史」): stream `raw_history` of `POST /v1/sync/pull` (body_engine nativesync/history.py).
///
/// - Starts once the first restore is done (`DeltaRemote`: the last 60 days of raw samples, so scores come in seconds)
///   and the server scores this user from the app (`sync_mode` = app in the scoring context): the others' history comes
///   from Open Wearables / the Shortcut, which the phone cannot recompute anyway.
/// - Newest first, one page (≈14 days) per request, each page in one transaction with its cursor: an interrupted run
///   resumes where it stopped (foreground, `BGProcessingTask`, the next launch).
/// - Only on Wi-Fi while charging, or on any network when the user allows cellular for history (the sync center's
///   switch, shared with the upload backfill: `EngineState.config().cellularHistory`). Never on Low Data Mode.
/// - The server throttles (429 + Retry-After): short waits are sat out, longer ones end the run.
/// - `history_before` is fixed at the first run (the hot window's first day then): the hot window and the history never
///   overlap. After the download, days the server changes before it come again (at most once a day).
/// Progress (`State`) is kept in `meta` for the sync center and the data status page.
actor HistoryDownload {
  struct State: Codable, Equatable {
    /// idle | waiting_restore | server_mode | waiting_network | downloading | throttled | done | failed
    var phase = "idle"
    var before: String?
    /// The oldest day stored so far, days stored, days the server has in all (first page's `history.days`).
    var oldest: String?
    var fetched = 0
    var total: Int?
    var incomplete = 0
    var at: Double = 0
    var detail: String?

    var fraction: Double? {
      guard let total, total > 0 else { return phase == "done" ? 1 : nil }
      return phase == "done" ? 1 : min(1, Double(fetched) / Double(total))
    }
  }

  /// When the phone may download history now.
  struct Policy {
    var unmetered: Bool
    var constrained: Bool
    var charging: Bool
    var cellularAllowed: Bool

    var allowed: Bool { !constrained && ((unmetered && charging) || cellularAllowed) }
    var reason: String { constrained ? "low_data" : !unmetered ? "wifi" : "power" }
  }

  static let stream = "raw_history"
  static let stateKey = "history.state"
  static let beforeKey = "history.before"

  private let db: AppDatabase
  private let api: APIClient
  private let policy: () async -> Policy
  private let sleep: (TimeInterval) async -> Void
  private var running = false

  init(db: AppDatabase, api: APIClient, policy: (() async -> Policy)? = nil,
       sleep: @escaping (TimeInterval) async -> Void = { try? await Task.sleep(nanoseconds: UInt64($0 * 1_000_000_000)) }) {
    self.db = db
    self.api = api
    self.policy = policy ?? { await HistoryDownload.currentPolicy() }
    self.sleep = sleep
  }

  // MARK: - state

  nonisolated static func state(_ db: AppDatabase) -> State {
    (try? db.meta(stateKey)).flatMap { $0 }.flatMap { try? JSONDecoder().decode(State.self, from: Data($0.utf8)) } ?? State()
  }

  /// The whole history is on the phone and every day had all the server's sources: a full recompute may replace the
  /// server's rows.
  nonisolated static func isComplete(_ db: AppDatabase) -> Bool {
    let s = state(db)
    return s.phase == "done" && s.incomplete == 0 && s.before != nil
  }

  private func save(_ s: State) -> State {
    var s = s
    s.at = Date().timeIntervalSince1970
    if let d = try? JSONEncoder().encode(s) { try? db.setMeta(Self.stateKey, String(decoding: d, as: UTF8.self)) }
    return s
  }

  // MARK: - running

  /// Downloads pages until the history is complete, `maxPages` were fetched, `deadline` passed, the task was cancelled
  /// or the network / the server says stop. One run at a time.
  @discardableResult
  func run(reason: String, today: String? = nil, maxPages: Int = .max, deadline: Date? = nil) async -> State {
    var s = Self.state(db)
    guard !running else { return s }
    running = true
    defer { running = false }
    guard (try? db.meta(DeltaRemote.rawRestoredKey)) == "1" else { s.phase = "waiting_restore"; return save(s) }
    let mode = (try? db.doc(ScoringSync.contextDoc))?.json?["sync_mode"] as? String
    guard mode == "app" else { s.phase = mode == nil ? "waiting_restore" : "server_mode"; return save(s) }
    let zone = ScoringSync.zone(db)
    let day = today ?? LocalDay.string(Date(), zone)
    if s.before == nil {
      // the hot window's first day: everything before it is the history's
      let before = (try? db.meta(LocalRawStore.hotFromKey)) ?? nil ?? LocalRawStore.rawStart(today: day)
      s.before = before
      try? db.setMeta(LocalRawStore.hotFromKey, before)
    }
    if s.phase == "done", Date().timeIntervalSince1970 - s.at < LocalHistoryParams.refreshEvery { return s }
    let p = await policy()
    guard p.allowed else {
      s.detail = p.reason
      if s.phase != "done" { s.phase = "waiting_network" }
      return save(s)
    }
    let store = LocalRawStore(db: db, zone: zone)
    var pages = 0
    while pages < maxPages {
      if Task.isCancelled || deadline.map({ Date() >= $0 }) == true { break }
      let cursor = (try? await db.writer.read { db in
        try String.fetchOne(db, sql: "SELECT cursor FROM sync_cursors WHERE stream = ?", arguments: [Self.stream])
      }) ?? nil
      if s.phase != "done" { s.phase = "downloading" }
      var body: [String: Any] = ["cursors": [Self.stream: cursor ?? ""], "limit": 500]
      if let b = s.before { body["history_before"] = b }
      let page: [String: Any]
      do {
        page = try await api.json(APIRequest(method: .post, path: Endpoint.syncPull,
                                             body: try JSONSerialization.data(withJSONObject: body), idempotent: false, timeout: 90))
      } catch APIError.rateLimited(let after) {
        let wait = after ?? LocalHistoryParams.maxInlineWait
        if wait <= LocalHistoryParams.maxInlineWait, deadline.map({ Date().addingTimeInterval(wait) < $0 }) ?? true, !Task.isCancelled {
          await sleep(wait)
          continue
        }
        s.phase = s.phase == "done" ? "done" : "throttled"
        s.detail = "retry after \(Int(wait)) s"
        return save(s)
      } catch APIError.http(422, let detail) where cursor?.isEmpty == false {
        // a cursor the server no longer understands: start again from the newest day (pages replace whole days)
        try? await db.writer.write { db in try db.execute(sql: "DELETE FROM sync_cursors WHERE stream = ?", arguments: [Self.stream]) }
        s.detail = detail
        continue
      } catch APIError.notFound {
        s.phase = "server_mode"
        return save(s)
      } catch {
        s.phase = s.phase == "done" ? "done" : "failed"
        s.detail = String(describing: error)
        return save(s)
      }
      pages += 1
      let changes = (page["changes"] as? [[String: Any]]) ?? []
      let next = ((page["cursors"] as? [String: String]) ?? [:])[Self.stream]
      if let h = page["history"] as? [String: Any], let n = (h["days"] as? NSNumber)?.intValue { s.total = n }
      let applied: (Int, String?, Int) = (try? await db.writer.write { db -> (Int, String?, Int) in
        var n = 0, oldest: String?, incomplete = 0
        for ch in changes where ch["stream"] as? String == Self.stream {
          guard let d = ch["key"] as? String else { continue }
          let b = ch["body"] as? [String: Any]
          try autoreleasepool { _ = try store.storeHistoryDay(db, day: d, body: (ch["op"] as? String) == "delete" ? nil : b) }
          n += 1
          if b?["complete"] as? Bool == false { incomplete += 1 }
          oldest = min(oldest ?? d, d)
        }
        if let next { try SyncCursor(stream: Self.stream, cursor: next, pulledAt: Date().timeIntervalSince1970).save(db) }
        return (n, oldest, incomplete)
      }) ?? (0, nil, 0)
      if s.phase != "done" {
        s.fetched += applied.0
        if let o = applied.1 { s.oldest = min(s.oldest ?? o, o) }
      }
      s.incomplete += applied.2
      s = save(s)
      if page["more"] as? Bool != true {
        if next?.hasPrefix("c.") == true {
          s.phase = "done"
          s.detail = nil
        }
        try? await db.writer.write { db in
          let n = try Int.fetchOne(db, sql: "SELECT count(*) FROM hist_days WHERE source = 'server' AND complete = 0") ?? 0
          s.incomplete = n
        }
        return save(s)
      }
    }
    return save(s)
  }

  /// Forget the download (the cache was cleared): the next run starts from the newest day.
  func reset() {
    try? db.writer.write { db in
      try db.execute(sql: "DELETE FROM sync_cursors WHERE stream = ?", arguments: [Self.stream])
      try db.execute(sql: "DELETE FROM meta WHERE key IN (?, ?)", arguments: [Self.stateKey, Self.beforeKey])
    }
  }

  // MARK: - the device's state

  /// Wi-Fi (not expensive, not constrained), charging (or full on power; the simulator reports unknown and counts as
  /// charging), the user's cellular choice.
  static func currentPolicy() async -> Policy {
    let path = await currentPath()
    let charging = await MainActor.run { () -> Bool in
      UIDevice.current.isBatteryMonitoringEnabled = true
      switch UIDevice.current.batteryState {
      case .charging, .full, .unknown: return true
      case .unplugged: return false
      @unknown default: return false
      }
    }
    return Policy(unmetered: path.status == .satisfied && !path.isExpensive, constrained: path.isConstrained,
                  charging: charging, cellularAllowed: EngineState.config().cellularHistory)
  }

  private static func currentPath() async -> NWPath {
    await withCheckedContinuation { cont in
      let monitor = NWPathMonitor()
      let once = OnceFlag()
      monitor.pathUpdateHandler = { path in
        guard once.take() else { return }
        monitor.cancel()
        cont.resume(returning: path)
      }
      monitor.start(queue: DispatchQueue(label: "w2w.history.path"))
    }
  }
}

/// A flag that is taken once (thread-safe).
final class OnceFlag {
  private var taken = false
  private let lock = NSLock()

  func take() -> Bool {
    lock.lock()
    defer { lock.unlock() }
    if taken { return false }
    taken = true
    return true
  }
}
