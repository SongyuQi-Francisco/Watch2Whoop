import Foundation

/// What a page asks the sync layer for. Pages never call the server for data they show: they read the local database
/// and ask for a refresh (docs/ios-native-app.md section 5).
enum PullScope: Equatable {
  /// The today document of a day (nil = the user's today).
  case today(date: String?)
  /// Everything that changed since the last pull (N2: /v1/sync/pull). Falls back to `.today(nil)` until N2 ships.
  case changes
}

/// The server side of pulling. N0 has the legacy implementation (existing GET endpoints → `docs`); N2 adds
/// `DeltaRemote` (`POST /v1/sync/pull`) and the coordinator prefers it when the server has it.
protocol RemoteSync {
  /// Fetches what `scope` covers and writes it into the database. Returns the document keys it wrote.
  func pull(_ scope: PullScope, into db: AppDatabase) async throws -> [String]
}

/// Existing endpoints, stored as documents (N0).
struct LegacyRemote: RemoteSync {
  let api: APIClient

  func pull(_ scope: PullScope, into db: AppDatabase) async throws -> [String] {
    // Before N2 "everything that changed" can only mean the today document.
    var date: String?
    if case .today(let d) = scope { date = d }
    let data = try await api.data(Endpoint.today(date: date))
    let obj = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any]
    let day = (obj?["date"] as? String) ?? date ?? "current"
    try db.saveDoc(DocKey.today(day), json: data)
    guard date == nil else { return [DocKey.today(day)] }
    try db.saveDoc(DocKey.todayCurrent, json: data)
    return [DocKey.today(day), DocKey.todayCurrent]
  }
}

/// Document keys in the `docs` table, in one place.
enum DocKey {
  static func today(_ date: String) -> String { "today:\(date)" }
  /// The user's current today (the document the Today tab opens on, before it knows the date).
  static let todayCurrent = "today:current"
}

/// One per signed-in account: pulls, drains the offline queue, publishes a small status for the UI.
@MainActor
final class SyncCoordinator: ObservableObject {
  enum Status: Equatable {
    case idle
    case syncing
    case offline
    case failed(String)
  }

  @Published private(set) var status: Status = .idle
  @Published private(set) var lastPull: Date?

  let db: AppDatabase
  let queue: OfflineQueue
  private let api: APIClient
  private let delta: RemoteSync
  private let legacy: RemoteSync
  /// The server has N2's sync for this user (flag `native_sync` in `/v1/web/me`; `AccountSession` sets it): the scoring
  /// inputs are pulled incrementally and the phone scores the user itself (`ScoringSync`, #340 N2b).
  var deltaAvailable = false
  /// The phone's own scoring (N2b).
  let scoring: ScoringSync
  @Published private(set) var lastScoring: ScoringSync.Report?
  /// The history download (local history): after scoring, a few pages in the foreground, the rest in the background.
  let history: HistoryDownload
  @Published private(set) var historyState: HistoryDownload.State

  init(db: AppDatabase, api: APIClient, delta: RemoteSync? = nil, legacy: RemoteSync? = nil, scoring: ScoringSync? = nil,
       history: HistoryDownload? = nil) {
    self.db = db
    self.api = api
    self.queue = OfflineQueue(db: db)
    self.delta = delta ?? DeltaRemote(api: api, zone: { ScoringSync.zone(db) })
    self.legacy = legacy ?? LegacyRemote(api: api)
    self.scoring = scoring ?? ScoringSync(db: db, api: api)
    self.history = history ?? HistoryDownload(db: db, api: api)
    self.lastScoring = ScoringSync.lastReport(db)
    self.historyState = HistoryDownload.state(db)
  }

  /// Pull `scope`, then send queued writes. Errors end up in `status`; cached data stays on screen.
  func refresh(_ scope: PullScope = .changes) async {
    status = .syncing
    do {
      if deltaAvailable, scope == .changes {
        // the scoring inputs (scores, daily, sleep; raw during the first restore), then the today document as before
        _ = try await delta.pull(scope, into: db)
      }
      _ = try await legacy.pull(scope == .changes ? .today(date: nil) : scope, into: db)
      lastPull = Date()
      try await queue.drain(send: OfflineQueue.apiSender(api))
      status = .idle
      if deltaAvailable, scope == .changes { scoreSoon(reason: "pull") }
    } catch let e as APIError {
      if case .network = e { status = .offline } else { status = .failed(e.userMessage) }
    } catch {
      status = .failed(String(describing: error))
    }
  }

  /// The phone's scoring in the background (one run at a time; a run asked for while one goes is folded into it).
  func scoreSoon(reason: String) {
    guard deltaAvailable else { return }
    let scoring = self.scoring, history = self.history, db = self.db
    let compute = LocalCompute(db: db, api: api)
    Task.detached(priority: .utility) { [weak self] in
      let h = await MemoryProbe.measure("foreground", db: db) { () -> HistoryDownload.State in
        // a full recompute (new bundle, whole history here) gets a short slice now; the background task does the rest.
        // 2.0.0: the package stages run around the scores (LocalCompute; all skipped until a package lands)
        let r = await compute.run(reason: reason) {
          await scoring.run(reason: reason, deadline: Date().addingTimeInterval(LocalHistoryParams.foregroundScoringSeconds))
        }
        await MainActor.run { self?.lastScoring = r }
        // then the history (local history): a few pages now when the network and power allow, the rest in the background
        return await history.run(reason: reason, maxPages: LocalHistoryParams.foregroundPages)
      }
      await MainActor.run { self?.historyState = h }
      if h.phase != "done" && h.phase != "server_mode" || (try? db.meta(ScoringSync.fullKey)) ?? nil != nil {
        HistoryBackground.schedule()
      }
      _ = try? db.maintain(onPower: false)   // incremental vacuum after pruning (cheap)
    }
  }

  /// Re-read the history's progress (the sync center while it is open).
  func reloadHistoryState() {
    historyState = HistoryDownload.state(db)
  }

  /// 清除本机缓存: empty the local copy (unsent writes stay) and pull it all again from the server.
  func clearLocalCache() async throws {
    let db = self.db
    try await Task.detached(priority: .userInitiated) { try db.clearCache() }.value
    historyState = HistoryDownload.state(db)
    lastScoring = nil
    await refresh(.changes)
  }

  /// Queue a write and try to send it right away (the page has already updated its local rows).
  func enqueue(kind: String, group: String, method: APIRequest.Method, path: String, body: Data?) {
    do {
      try queue.enqueue(kind: kind, group: group, method: method, path: path, body: body)
    } catch {
      status = .failed(String(describing: error))
      return
    }
    Task { try? await queue.drain(send: OfflineQueue.apiSender(api)) }
  }
}
