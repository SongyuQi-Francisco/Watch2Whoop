import Foundation
import GRDB

/// A write of the user's own records that the server's views do not show yet (N5, docs/ios-native-app.md section 5.4):
/// its local row in `records` (id `c:<client_id>`), joined with its offline-queue entry.
struct PendingRecord: Equatable, Identifiable {
  var kind: String
  var clientId: String
  var date: String?
  var payload: Data
  var createdAt: Double
  /// The queue entry's state; nil = sent, waiting for the next refresh to bring the server's copy.
  var state: OutboxItem.State?
  var lastError: String?

  var id: String { clientId }
  var json: [String: Any] { (try? JSONSerialization.jsonObject(with: payload)) as? [String: Any] ?? [:] }
  /// Failed or conflicting: kept, shown as 「没提交」 until the user retries or drops it.
  var needsAttention: Bool { state == .failed || state == .conflict }
}

/// Local-first writes of the user's records (food, water, journal, training, grocery …). One write = a `records` row
/// (pending) + an outbox entry with the same `client_id`, in one transaction; pages lay the pending rows over the
/// server's cached views until a refresh brings the server's copy, then the rows go (`prune`).
enum LocalRecords {
  /// Writes the local row and queues the request. `body` is what the endpoint takes (`client_id` added unless
  /// `clientIdInBody` is false, for DELETEs); `overlay` is what the page needs to draw it before the server has it
  /// (default: the body). Returns the client id.
  @discardableResult
  static func write(_ db: AppDatabase, kind: String, date: String?, group: String, method: String,
                    path: String, body: [String: Any]?, overlay: [String: Any]? = nil, clientIdInBody: Bool = true,
                    clientId: String = UUID().uuidString.lowercased(), now: Double = Date().timeIntervalSince1970) throws -> String {
    var sent = body
    if clientIdInBody, sent != nil { sent?["client_id"] = clientId }
    let bodyData = try sent.map { try JSONSerialization.data(withJSONObject: $0, options: [.sortedKeys]) }
    let rowData = try JSONSerialization.data(withJSONObject: overlay ?? body ?? [:], options: [.sortedKeys])
    try db.writer.write { db in
      try db.execute(sql: """
        INSERT INTO records (kind, id, date, client_id, payload, updated_at, pending) VALUES (?, ?, ?, ?, ?, ?, 1)
        ON CONFLICT(kind, id) DO UPDATE SET payload = excluded.payload, updated_at = excluded.updated_at, pending = 1
        """, arguments: [kind, "c:\(clientId)", date, clientId, rowData, now])
      var item = OutboxItem(id: nil, clientId: clientId, kind: kind, groupKey: group, method: method, path: path,
                            body: bodyData, createdAt: now)
      try item.insert(db)
    }
    return clientId
  }

  /// Last write wins (a training day's draft, a ticked grocery line): drops the group's queued entries that are not on
  /// their way to the server, and their local rows, before the new state is written.
  static func supersede(_ db: AppDatabase, group: String) throws {
    try db.writer.write { db in
      let ids = try String.fetchAll(db, sql: "SELECT client_id FROM outbox WHERE group_key = ? AND state <> 'sending'",
                                    arguments: [group])
      guard !ids.isEmpty else { return }
      try db.execute(sql: "DELETE FROM outbox WHERE group_key = ? AND state <> 'sending'", arguments: [group])
      for id in ids { try db.execute(sql: "DELETE FROM records WHERE client_id = ? AND pending = 1", arguments: [id]) }
    }
  }

  /// The pending rows of these kinds, oldest first, with their queue state.
  static func pending(_ db: Database, kinds: [String]) throws -> [PendingRecord] {
    guard !kinds.isEmpty else { return [] }
    let marks = Array(repeating: "?", count: kinds.count).joined(separator: ",")
    let rows = try Row.fetchAll(db, sql: """
      SELECT r.kind, r.client_id, r.date, r.payload, r.updated_at, o.state, o.last_error
      FROM records r LEFT JOIN outbox o ON o.client_id = r.client_id
      WHERE r.pending = 1 AND r.kind IN (\(marks)) ORDER BY r.updated_at, r.rowid
      """, arguments: StatementArguments(kinds))
    return rows.map { r in
      let kind: String = r["kind"]
      let clientId: String? = r["client_id"]
      let date: String? = r["date"]
      let payload: Data? = r["payload"]
      let at: Double? = r["updated_at"]
      let state: String? = r["state"]
      let error: String? = r["last_error"]
      return PendingRecord(kind: kind, clientId: clientId ?? "", date: date, payload: payload ?? Data(), createdAt: at ?? 0,
                           state: state.flatMap(OutboxItem.State.init(rawValue:)), lastError: error)
    }
  }

  /// Client ids of pending rows whose queue entry is gone (the server has the write): a refresh that starts now brings
  /// them, so they can be pruned once its views are saved.
  static func sent(_ db: Database, kinds: [String]) throws -> Set<String> {
    Set(try pending(db, kinds: kinds).filter { $0.state == nil }.map(\.clientId))
  }

  static func prune(_ db: Database, clientIds: Set<String>) throws {
    for id in clientIds { try db.execute(sql: "DELETE FROM records WHERE client_id = ? AND pending = 1", arguments: [id]) }
  }

  /// Undo of a write the server has not got yet (section 5.4: only these undo offline): the queue entry and the local
  /// row go. False when it is being sent or already sent; then the undo is an online request.
  static func cancel(_ db: AppDatabase, clientId: String) throws -> Bool {
    try db.writer.write { db in
      let state = try String.fetchOne(db, sql: "SELECT state FROM outbox WHERE client_id = ?", arguments: [clientId])
      guard let state, state != OutboxItem.State.sending.rawValue else { return false }
      try db.execute(sql: "DELETE FROM outbox WHERE client_id = ?", arguments: [clientId])
      try db.execute(sql: "DELETE FROM records WHERE client_id = ? AND pending = 1", arguments: [clientId])
      return true
    }
  }

  /// The queue entry of a write, after a send attempt (nil = sent).
  static func outboxState(_ db: AppDatabase, clientId: String) -> OutboxItem? {
    try? db.writer.read { db in try OutboxItem.filter(Column("client_id") == clientId).fetchOne(db) }
  }
}

/// Server views kept as documents plus the pending rows laid over them: one read, compared before a page redraws.
struct LocalView: Equatable {
  var docs: [String: DocRecord] = [:]
  var pending: [PendingRecord] = []

  static func read(_ db: Database, keys: [String], kinds: [String]) throws -> LocalView {
    var v = LocalView()
    for r in try DocRecord.fetchAll(db, keys: keys) { v.docs[r.key] = r }
    v.pending = try LocalRecords.pending(db, kinds: kinds)
    return v
  }

  func json(_ key: String) -> [String: Any]? { docs[key]?.json }
  func list(_ key: String) -> [[String: Any]]? {
    docs[key].flatMap { (try? JSONSerialization.jsonObject(with: $0.body)) as? [[String: Any]] }
  }
  func decode<T: Decodable>(_ key: String, as type: T.Type) -> T? {
    docs[key].flatMap { try? JSONDecoder().decode(T.self, from: $0.body) }
  }
  func pending(_ kind: String, date: String? = nil) -> [PendingRecord] {
    pending.filter { $0.kind == kind && (date == nil || $0.date == date) }
  }
}

/// Pulls a page's documents in parallel and saves them in one transaction with the pruning of rows the server now has,
/// so a pending row and the server's copy of it are never both on screen. Like `DocPuller`: no connection keeps the
/// cached copies, 403 / 404 (no share, a feature that is off) remove a document so the page hides that block.
struct RecordsPuller {
  let api: APIClient
  let db: AppDatabase

  func pull(_ fetches: [DocFetch], kinds: [String]) async -> DocPuller.Outcome {
    let sent = (try? await db.writer.read { db in try LocalRecords.sent(db, kinds: kinds) }) ?? []
    let results = await withTaskGroup(of: (String, Result<Data, APIError>).self) { group in
      for f in fetches {
        group.addTask {
          do { return (f.key, .success(try await api.data(f.request))) }
          catch let e as APIError { return (f.key, .failure(e)) }
          catch { return (f.key, .failure(.network(-1))) }
        }
      }
      var out: [(String, Result<Data, APIError>)] = []
      for await r in group { out.append(r) }
      return out
    }
    let now = Date().timeIntervalSince1970
    let outcome = try? await db.writer.write { db -> DocPuller.Outcome in
      var outcome = DocPuller.Outcome()
      var failed = false
      for (key, result) in results {
        switch result {
        case .success(let data):
          try DocRecord(key: key, version: 0, body: data, fetchedAt: now).save(db)
          outcome.saved.append(key)
        case .failure(.notFound), .failure(.forbidden):
          _ = try DocRecord.deleteOne(db, key: key)
          outcome.removed.append(key)
        case .failure(.network):
          outcome.offline = true
          failed = true
        case .failure:
          failed = true
        }
      }
      if !failed { try LocalRecords.prune(db, clientIds: sent) }
      return outcome
    }
    return outcome ?? DocPuller.Outcome()
  }
}

extension AppDatabase {
  /// The user's today: the date of the current today document (the server's view of the user's time zone), else the
  /// phone's.
  func userToday() -> String {
    if let d = (try? doc(DocKey.todayCurrent))?.json?["date"] as? String { return d }
    return RecordDay.string(Date())
  }
}
