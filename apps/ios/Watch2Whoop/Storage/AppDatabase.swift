import CryptoKit
import Foundation
import GRDB

/// The signed-in account's local database (GRDB / SQLite), one file per account (docs/ios-native-app.md section 4).
/// Pages read only from here; the sync layer fills it from the server and HealthKit. Every schema change is a new
/// migration in `Schema.migrator`, never an edit of a shipped one.
final class AppDatabase {
  let writer: any DatabaseWriter

  init(_ writer: any DatabaseWriter) throws {
    self.writer = writer
    try Schema.migrator.migrate(writer)
  }

  /// `Application Support/W2W/accounts/<key>/body.sqlite`; `key` = first 16 hex digits of sha256("server|userId").
  /// File protection "until first unlock" (background sync reads it) and excluded from iCloud / device backups: the
  /// server keeps the full copy and a restored phone has no token anyway (Keychain items are this-device-only).
  static func open(server: String, userId: Int) throws -> AppDatabase {
    let dir = try accountDirectory(server: server, userId: userId)
    var config = Configuration()
    config.label = "w2w.account"
    config.prepareDatabase { db in
      try db.execute(sql: "PRAGMA foreign_keys = ON")
    }
    let pool = try DatabasePool(path: dir.appendingPathComponent("body.sqlite").path, configuration: config)
    return try AppDatabase(pool)
  }

  /// Empty in-memory database with the full schema (tests, previews).
  static func inMemory() throws -> AppDatabase {
    try AppDatabase(DatabaseQueue())
  }

  static func accountKey(server: String, userId: Int) -> String {
    let h = SHA256.hash(data: Data("\(server)|\(userId)".utf8))
    return h.prefix(8).map { String(format: "%02x", $0) }.joined()
  }

  static func accountDirectory(server: String, userId: Int) throws -> URL {
    let fm = FileManager.default
    let base = try fm.url(for: .applicationSupportDirectory, in: .userDomainMask, appropriateFor: nil, create: true)
    var dir = base.appendingPathComponent("W2W/accounts/\(accountKey(server: server, userId: userId))", isDirectory: true)
    try fm.createDirectory(at: dir, withIntermediateDirectories: true,
                           attributes: [.protectionKey: FileProtectionType.completeUntilFirstUserAuthentication])
    var values = URLResourceValues()
    values.isExcludedFromBackup = true
    try? dir.setResourceValues(values)
    return dir
  }

  /// Sign-out: the account's whole directory goes (after the offline queue was confirmed empty or discarded).
  static func deleteAccount(server: String, userId: Int) throws {
    let dir = try accountDirectory(server: server, userId: userId)
    try FileManager.default.removeItem(at: dir)
  }
}

// MARK: - documents (server-computed views kept as they came)

extension AppDatabase {
  func saveDoc(_ key: String, json: Data, version: Int = 0) throws {
    try writer.write { db in
      try DocRecord(key: key, version: version, body: json, fetchedAt: Date().timeIntervalSince1970).save(db)
    }
  }

  func doc(_ key: String) throws -> DocRecord? {
    try writer.read { db in try DocRecord.fetchOne(db, key: key) }
  }

  /// Emits the document now and after every change (pages subscribe; the sync layer writes).
  func observeDoc(_ key: String) -> AsyncValueObservation<DocRecord?> {
    ValueObservation.tracking { db in try DocRecord.fetchOne(db, key: key) }
      .removeDuplicates()
      .values(in: writer)
  }

  func meta(_ key: String) throws -> String? {
    try writer.read { db in try String.fetchOne(db, sql: "SELECT value FROM meta WHERE key = ?", arguments: [key]) }
  }

  func setMeta(_ key: String, _ value: String) throws {
    try writer.write { db in
      try db.execute(sql: "INSERT INTO meta (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                     arguments: [key, value])
    }
  }
}
