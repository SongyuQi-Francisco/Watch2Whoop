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

/// N2's incremental pull. Not on the server yet: always "not available", so the coordinator uses `LegacyRemote`.
struct DeltaRemote: RemoteSync {
  let api: APIClient
  struct NotAvailable: Error {}

  func pull(_ scope: PullScope, into db: AppDatabase) async throws -> [String] {
    // N2: POST Endpoint.syncPull {"cursors": {stream: cursor}, "limit": n} → {"changes": [...], "cursors": {...},
    // "more": bool}; apply in one transaction per page, then save the cursors (section 5.2).
    throw NotAvailable()
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
  private var deltaAvailable = false   // N2: set from /v1/web/me (feature) once the server has it

  init(db: AppDatabase, api: APIClient, delta: RemoteSync? = nil, legacy: RemoteSync? = nil) {
    self.db = db
    self.api = api
    self.queue = OfflineQueue(db: db)
    self.delta = delta ?? DeltaRemote(api: api)
    self.legacy = legacy ?? LegacyRemote(api: api)
  }

  /// Pull `scope`, then send queued writes. Errors end up in `status`; cached data stays on screen.
  func refresh(_ scope: PullScope = .changes) async {
    status = .syncing
    do {
      if deltaAvailable, scope == .changes {
        _ = try await delta.pull(scope, into: db)
      } else {
        _ = try await legacy.pull(scope == .changes ? .today(date: nil) : scope, into: db)
      }
      lastPull = Date()
      try await queue.drain(send: OfflineQueue.apiSender(api))
      status = .idle
    } catch let e as APIError {
      if case .network = e { status = .offline } else { status = .failed(e.userMessage) }
    } catch {
      status = .failed(String(describing: error))
    }
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
