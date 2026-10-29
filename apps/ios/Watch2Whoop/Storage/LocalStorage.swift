import Foundation
import GRDB

/// Storage hygiene of the account database (#340 local history; docs/ios-native-app.md section 4.1):
/// - never in iCloud / device backups (`isExcludedFromBackup` on the directories and each SQLite file): the server keeps
///   the full copy, and a restored phone has no token anyway (this-device-only Keychain);
/// - `auto_vacuum = INCREMENTAL` (new databases at creation, older ones at their first full VACUUM):
///   `PRAGMA incremental_vacuum` gives freed pages back after pruning / a cache clear, a full `VACUUM` now and then on
///   power (LocalHistoryParams.vacuum*), the WAL truncated after both;
/// - what it takes on disk, per area (the data status page);
/// - clearing the cache: everything the server can send again goes; unsent writes (outbox, their records) and the
///   installed algorithm bundles stay. The next pull restores it all from the server.
extension AppDatabase {
  struct Footprint: Equatable {
    /// The SQLite file with its WAL and shared-memory files.
    var fileBytes: Int64 = 0
    /// Bytes per area (pages of its tables and indexes; nil when SQLite has no dbstat).
    var raw: Int64?
    var history: Int64?
    var scores: Int64?
    var other: Int64?
    var freeBytes: Int64 = 0
  }

  /// The tables of each area of the data status page.
  static let areas: [String: [String]] = [
    "raw": ["hk_samples", "hk_sources", "hk_stat_buckets", "hk_workouts", "hk_deleted", "score_dirty"],
    "history": ["hist_samples", "hist_segments", "hist_days", "heartbeat_series", "hr_day_hist"],
    "scores": ["daily_scores", "daily_metrics", "sleep_sessions"],
  ]

  /// The database file and its companions (nil for an in-memory database).
  var fileURL: URL? {
    let p = writer.path
    return p.isEmpty || p.hasPrefix(":memory:") ? nil : URL(fileURLWithPath: p)
  }

  var files: [URL] {
    guard let u = fileURL else { return [] }
    return [u, URL(fileURLWithPath: u.path + "-wal"), URL(fileURLWithPath: u.path + "-shm")]
  }

  /// Marks the database files (and their directory) as not to be backed up; idempotent, cheap.
  func excludeFromBackup() {
    for var u in files + (fileURL.map { [$0.deletingLastPathComponent()] } ?? []) where FileManager.default.fileExists(atPath: u.path) {
      var v = URLResourceValues()
      v.isExcludedFromBackup = true
      try? u.setResourceValues(v)
    }
  }

  func footprint() -> Footprint {
    var f = Footprint()
    for u in files {
      f.fileBytes += ((try? FileManager.default.attributesOfItem(atPath: u.path)[.size]) as? NSNumber)?.int64Value ?? 0
    }
    let sizes: [String: Int64]? = try? writer.read { db in
      var out: [String: Int64] = [:]
      for r in try Row.fetchAll(db, sql: """
        SELECT coalesce(m.tbl_name, s.name) AS t, sum(s.pgsize) AS bytes FROM dbstat s
        LEFT JOIN sqlite_master m ON m.name = s.name GROUP BY t
        """) {
        out[r["t"]] = r["bytes"]
      }
      let pageSize = try Int64.fetchOne(db, sql: "PRAGMA page_size") ?? 4096
      out["\u{0}free"] = (try Int64.fetchOne(db, sql: "PRAGMA freelist_count") ?? 0) * pageSize
      return out
    }
    if var sizes {
      f.freeBytes = sizes.removeValue(forKey: "\u{0}free") ?? 0
      func sum(_ area: String) -> Int64 { (Self.areas[area] ?? []).reduce(0) { $0 + (sizes[$1] ?? 0) } }
      f.raw = sum("raw")
      f.history = sum("history")
      f.scores = sum("scores")
      let named = Set(Self.areas.values.flatMap { $0 })
      f.other = sizes.filter { !named.contains($0.key) }.values.reduce(0, +)
    }
    return f
  }

  // MARK: - vacuum

  /// Gives free pages back: incremental vacuum when the database allows it; a full VACUUM (which also switches an older
  /// database to incremental) only `onPower` (a background task) and when due. Returns what it did (logs, tests).
  @discardableResult
  func maintain(onPower: Bool, now: Date = Date()) throws -> String {
    let (mode, pages, free) = try writer.read { db in
      (try Int.fetchOne(db, sql: "PRAGMA auto_vacuum") ?? 0, try Int.fetchOne(db, sql: "PRAGMA page_count") ?? 0,
       try Int.fetchOne(db, sql: "PRAGMA freelist_count") ?? 0)
    }
    let last = (try meta("storage.vacuumed_at")).flatMap(Double.init) ?? 0
    var did = "none"
    let due = now.timeIntervalSince1970 - last >= LocalHistoryParams.vacuumEvery
    if onPower && (mode != 2 || (due && pages > 0 && Double(free) / Double(pages) >= LocalHistoryParams.vacuumFreeShare)) {
      try writer.writeWithoutTransaction { db in
        try db.execute(sql: "PRAGMA auto_vacuum = INCREMENTAL")
        try db.execute(sql: "VACUUM")
      }
      try setMeta("storage.vacuumed_at", String(now.timeIntervalSince1970))
      did = "vacuum"
    } else if mode == 2 && free > 0 {
      try writer.writeWithoutTransaction { db in try db.execute(sql: "PRAGMA incremental_vacuum") }
      did = "incremental"
    }
    if fileURL != nil {
      try writer.writeWithoutTransaction { db in _ = try Row.fetchOne(db, sql: "PRAGMA wal_checkpoint(TRUNCATE)") }
    }
    return did
  }

  // MARK: - clearing the cache

  /// Empties everything the server can send again; keeps the outbox and the records it still has to send, and the
  /// algorithm bundles. Then a full VACUUM (the user asked for the space). Returns the rows removed.
  @discardableResult
  func clearCache() throws -> Int {
    let n = try writer.write { db -> Int in
      var n = 0
      for t in ["docs", "sync_cursors", "hk_samples", "hk_stat_buckets", "hk_workouts", "hk_deleted", "hk_sources",
                "daily_metrics", "sleep_sessions", "daily_scores", "heartbeat_series", "hr_day_hist", "score_dirty",
                "hist_samples", "hist_segments", "hist_days", "meta"] {
        try db.execute(sql: "DELETE FROM \(t)")
        n += db.changesCount
      }
      try db.execute(sql: "DELETE FROM records WHERE pending = 0")
      n += db.changesCount
      return n
    }
    try writer.writeWithoutTransaction { db in
      try db.execute(sql: "PRAGMA auto_vacuum = INCREMENTAL")
      try db.execute(sql: "VACUUM")
    }
    try setMeta("storage.vacuumed_at", String(Date().timeIntervalSince1970))
    if fileURL != nil {
      try writer.writeWithoutTransaction { db in _ = try Row.fetchOne(db, sql: "PRAGMA wal_checkpoint(TRUNCATE)") }
    }
    return n
  }

  /// Unsent writes: clearing the cache keeps them, the page says so.
  func unsentWrites() -> Int {
    (try? writer.read { db in try Int.fetchOne(db, sql: "SELECT count(*) FROM outbox") }) ?? nil ?? 0
  }
}

/// "12.3 MB" in the app's language rules (decimal units, like the Settings app).
enum ByteText {
  static func string(_ bytes: Int64) -> String {
    ByteCountFormatter.string(fromByteCount: bytes, countStyle: .file)
  }
}
