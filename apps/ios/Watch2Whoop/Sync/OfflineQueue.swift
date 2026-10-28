import Foundation
import GRDB

/// Writes go to the local database first and are sent from here (docs/ios-native-app.md section 5.4).
/// - FIFO per `group_key`, one entry in flight per group; different groups do not block each other.
/// - Every entry carries a `client_id` the server uses to answer a resend with the first result (idempotency).
/// - Network error / 5xx / 429 → stays `pending`, next try after 2, 4, 8 … s (max 5 min, ±20 %), 429 after Retry-After.
/// - 401 → stop the whole drain (signed out); nothing is dropped.
/// - 409 → `conflict`, other 4xx → `failed`: kept and shown to the user (「没提交」: retry or discard); the rest of that
///   group waits behind it so the order is kept.
final class OfflineQueue {
  /// What sending one entry came to.
  enum SendResult: Equatable {
    case done
    case retry(after: TimeInterval?)
    case conflict(String?)
    case failed(String?)
    case unauthorized
  }

  typealias Sender = (OutboxItem) async -> SendResult

  private let db: AppDatabase
  private let now: () -> Double

  init(db: AppDatabase, now: @escaping () -> Double = { Date().timeIntervalSince1970 }) {
    self.db = db
    self.now = now
  }

  /// Queues a write. `body` is the JSON the endpoint takes; the queue adds nothing to it (put `client_id` in the body
  /// when the endpoint reads it from there).
  @discardableResult
  func enqueue(kind: String, group: String, method: APIRequest.Method, path: String, body: Data?,
               clientId: String = UUID().uuidString.lowercased()) throws -> OutboxItem {
    var item = OutboxItem(id: nil, clientId: clientId, kind: kind, groupKey: group, method: method.rawValue, path: path,
                          body: body, createdAt: now())
    try db.writer.write { db in try item.insert(db) }
    return item
  }

  func all() throws -> [OutboxItem] {
    try db.writer.read { db in try OutboxItem.order(Column("id")).fetchAll(db) }
  }

  /// Entries that need the user (failed / conflict), for the 「没提交」 list.
  func needsAttention() throws -> [OutboxItem] {
    try db.writer.read { db in
      try OutboxItem.filter([OutboxItem.State.failed.rawValue, OutboxItem.State.conflict.rawValue].contains(Column("state")))
        .order(Column("id")).fetchAll(db)
    }
  }

  func discard(_ id: Int64) throws {
    _ = try db.writer.write { db in try OutboxItem.deleteOne(db, key: id) }
  }

  func retry(_ id: Int64) throws {
    try db.writer.write { db in
      try db.execute(sql: "UPDATE outbox SET state = 'pending', next_attempt_at = 0, attempts = 0 WHERE id = ?", arguments: [id])
    }
  }

  /// The next sendable entry of every group: the group's oldest entry, if it is pending and due.
  func due() throws -> [OutboxItem] {
    let t = now()
    return try db.writer.read { db in
      let heads = try OutboxItem.fetchAll(db, sql: """
        SELECT * FROM outbox o WHERE o.id = (SELECT min(id) FROM outbox WHERE group_key = o.group_key) ORDER BY o.id
        """)
      return heads.filter { $0.state == .pending && $0.nextAttemptAt <= t }
    }
  }

  /// Sends what is due until nothing is due or the server said 401. Returns how many entries went through.
  @discardableResult
  func drain(send: Sender) async throws -> Int {
    var sent = 0
    while true {
      let batch = try due()
      if batch.isEmpty { return sent }
      for var item in batch {
        item.state = .sending
        try await save(item)
        let result = await send(item)
        switch result {
        case .done:
          let done = item
          _ = try await db.writer.write { db in try done.delete(db) }
          sent += 1
        case .retry(let after):
          item.attempts += 1
          item.state = .pending
          item.nextAttemptAt = now() + (after ?? Self.backoff(item.attempts))
          try await save(item)
        case .conflict(let why):
          item.state = .conflict
          item.lastError = why
          try await save(item)
        case .failed(let why):
          item.state = .failed
          item.lastError = why
          try await save(item)
        case .unauthorized:
          item.state = .pending
          try await save(item)
          return sent
        }
      }
    }
  }

  private func save(_ item: OutboxItem) async throws {
    try await db.writer.write { db in try item.update(db) }
  }

  static func backoff(_ attempts: Int) -> TimeInterval {
    min(300, 2 * pow(2, Double(max(0, attempts - 1)))) * Double.random(in: 0.8...1.2)
  }

  /// The usual sender: the entry's method / path / body through the API client.
  static func apiSender(_ api: APIClient) -> Sender {
    { item in
      var r = APIRequest(method: APIRequest.Method(rawValue: item.method) ?? .post, path: item.path, body: item.body,
                         idempotent: false)
      r.timeout = 30
      do {
        _ = try await api.data(r)
        return .done
      } catch let e as APIError {
        switch e {
        case .unauthorized, .notConfigured: return .unauthorized
        case .conflict(let d): return .conflict(d)
        case .rateLimited(let after): return .retry(after: after)
        case .network: return .retry(after: nil)
        case .http(let s, let d): return s >= 500 || s == 408 ? .retry(after: nil) : .failed(d)
        case .forbidden(let d): return .failed(d)
        case .notFound: return .failed("not_found")
        case .decoding: return .done   // the server took it; the answer is the page's business
        }
      } catch {
        return .retry(after: nil)
      }
    }
  }
}
