import Foundation
import GRDB

/// A server document stored as it came (table `docs`).
struct DocRecord: Codable, Equatable, FetchableRecord, PersistableRecord {
  static let databaseTableName = "docs"
  var key: String
  var version: Int
  var body: Data
  var fetchedAt: Double

  enum CodingKeys: String, CodingKey {
    case key, version, body
    case fetchedAt = "fetched_at"
  }

  var json: [String: Any]? { (try? JSONSerialization.jsonObject(with: body)) as? [String: Any] }
}

/// A queued write (table `outbox`).
struct OutboxItem: Codable, Equatable, FetchableRecord, MutablePersistableRecord {
  static let databaseTableName = "outbox"
  enum State: String, Codable { case pending, sending, failed, conflict }

  var id: Int64?
  var clientId: String
  var kind: String
  var groupKey: String
  var method: String
  var path: String
  var body: Data?
  var createdAt: Double
  var attempts: Int = 0
  var nextAttemptAt: Double = 0
  var state: State = .pending
  var lastError: String?

  enum CodingKeys: String, CodingKey {
    case id, kind, method, path, body, attempts, state
    case clientId = "client_id", groupKey = "group_key", createdAt = "created_at"
    case nextAttemptAt = "next_attempt_at", lastError = "last_error"
  }

  mutating func didInsert(_ inserted: InsertionSuccess) { id = inserted.rowID }
}

/// One day's scores (table `daily_scores`).
struct DailyScore: Codable, Equatable, FetchableRecord, PersistableRecord {
  static let databaseTableName = "daily_scores"
  var date: String
  var recovery: Double?
  var sleepScore: Double?
  var strain: Double?
  var hrvMs: Double?
  var rhr: Double?
  var payload: Data?
  var algoVersion: String?
  var origin: String
  var computedAt: Double
  var uploaded: Bool = false

  enum CodingKeys: String, CodingKey {
    case date, recovery, strain, rhr, payload, origin, uploaded
    case sleepScore = "sleep_score", hrvMs = "hrv_ms", algoVersion = "algo_version", computedAt = "computed_at"
  }
}

/// Pull cursor of one stream (table `sync_cursors`).
struct SyncCursor: Codable, Equatable, FetchableRecord, PersistableRecord {
  static let databaseTableName = "sync_cursors"
  var stream: String
  var cursor: String?
  var pulledAt: Double?

  enum CodingKeys: String, CodingKey {
    case stream, cursor
    case pulledAt = "pulled_at"
  }
}
