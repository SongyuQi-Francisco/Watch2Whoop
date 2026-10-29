import XCTest
@testable import Watch2Whoop

final class SchemaTests: XCTestCase {
  /// Migrations are append-only: a page agent adds a new one at the end, never renames or reorders (section 4.3).
  func testMigrationOrder() {
    XCTAssertEqual(Schema.migrator.migrations, ["v1_initial", "v2_local_scoring", "v3_local_history"])
  }

  func testDocumentsRoundTrip() throws {
    let db = try AppDatabase.inMemory()
    XCTAssertNil(try db.doc("today:current"))
    try db.saveDoc("today:current", json: Data(#"{"date":"2026-01-02"}"#.utf8), version: 3)
    let d = try XCTUnwrap(try db.doc("today:current"))
    XCTAssertEqual(d.version, 3)
    XCTAssertEqual(d.json?["date"] as? String, "2026-01-02")
    try db.setMeta("schema.owner", "n0")
    XCTAssertEqual(try db.meta("schema.owner"), "n0")
  }

  func testAccountKeyIsStableAndSeparatesAccounts() {
    let a = AppDatabase.accountKey(server: "https://a.example.com", userId: 1)
    XCTAssertEqual(a, AppDatabase.accountKey(server: "https://a.example.com", userId: 1))
    XCTAssertNotEqual(a, AppDatabase.accountKey(server: "https://a.example.com", userId: 2))
    XCTAssertNotEqual(a, AppDatabase.accountKey(server: "https://b.example.com", userId: 1))
    XCTAssertEqual(a.count, 16)
  }
}

final class OfflineQueueTests: XCTestCase {
  private var clock = 1_000.0
  private func queue() throws -> OfflineQueue {
    OfflineQueue(db: try AppDatabase.inMemory(), now: { [unowned self] in self.clock })
  }

  func testFifoPerGroupAndGroupsIndependent() async throws {
    let q = try queue()
    try q.enqueue(kind: "water", group: "water", method: .post, path: "/w/1", body: nil)
    try q.enqueue(kind: "water", group: "water", method: .post, path: "/w/2", body: nil)
    try q.enqueue(kind: "weight", group: "weight", method: .post, path: "/kg/1", body: nil)
    var order: [String] = []
    let n = try await q.drain { item in order.append(item.path); return .done }
    XCTAssertEqual(n, 3)
    XCTAssertEqual(order, ["/w/1", "/kg/1", "/w/2"])
    XCTAssertTrue(try q.all().isEmpty)
  }

  func testRetryWaitsAndKeepsOrder() async throws {
    let q = try queue()
    try q.enqueue(kind: "water", group: "water", method: .post, path: "/w/1", body: nil)
    try q.enqueue(kind: "water", group: "water", method: .post, path: "/w/2", body: nil)
    var calls: [String] = []
    _ = try await q.drain { item in calls.append(item.path); return .retry(after: 30) }
    XCTAssertEqual(calls, ["/w/1"])                    // /w/2 waits behind it
    XCTAssertTrue(try q.due().isEmpty)                 // not due before the backoff
    clock += 31
    _ = try await q.drain { item in calls.append(item.path); return .done }
    XCTAssertEqual(calls, ["/w/1", "/w/1", "/w/2"])
  }

  func testConflictBlocksItsGroupUntilTheUserDecides() async throws {
    let q = try queue()
    try q.enqueue(kind: "meal", group: "meal", method: .post, path: "/m/1", body: nil)
    try q.enqueue(kind: "meal", group: "meal", method: .post, path: "/m/2", body: nil)
    _ = try await q.drain { _ in .conflict("duplicate") }
    let stuck = try q.needsAttention()
    XCTAssertEqual(stuck.map(\.path), ["/m/1"])
    XCTAssertEqual(stuck.first?.lastError, "duplicate")
    XCTAssertTrue(try q.due().isEmpty)
    try q.discard(try XCTUnwrap(stuck.first?.id))
    var sent: [String] = []
    _ = try await q.drain { item in sent.append(item.path); return .done }
    XCTAssertEqual(sent, ["/m/2"])
  }

  func testUnauthorizedStopsEverythingAndDropsNothing() async throws {
    let q = try queue()
    try q.enqueue(kind: "water", group: "water", method: .post, path: "/w/1", body: nil)
    try q.enqueue(kind: "weight", group: "weight", method: .post, path: "/kg/1", body: nil)
    let n = try await q.drain { _ in .unauthorized }
    XCTAssertEqual(n, 0)
    XCTAssertEqual(try q.all().count, 2)
    XCTAssertEqual(try q.due().count, 2)
  }
}
