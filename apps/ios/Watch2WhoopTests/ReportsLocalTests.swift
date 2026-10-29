import GRDB
import XCTest
@testable import Watch2Whoop

/// M5 (docs/ios-native-2.0.md section 4): the weekly / monthly report and the journal analysis computed on the phone. The
/// fixture is written by body_engine/tests/test_js_golden_reports.py (BODY_WRITE_GOLDEN=1): a synthetic person as the
/// phone's local tables (scores, sleep, metrics, stress, workouts, the log's `ext_records`, the pulled journal) and what the
/// server's Python answers for the same data. The tables go through `ReportsLocal`'s loader and the built-in bundle; every
/// value must be within 1e-9 of the server's (the numbers are rounded, so in fact equal).
final class ReportsLocalTests: XCTestCase {
  private static func fixture() throws -> [String: Any] {
    let url = URL(fileURLWithPath: #filePath).deletingLastPathComponent()
      .appendingPathComponent("../../../packages/openbodyscore-js/test/golden/reports_local.json").standardized
    guard let data = try? Data(contentsOf: url) else { throw XCTSkip("fixture not readable at \(url.path)") }
    return try XCTUnwrap(try JSONSerialization.jsonObject(with: data) as? [String: Any])
  }

  private static func data(_ o: Any) throws -> Data { try JSONSerialization.data(withJSONObject: o, options: [.sortedKeys]) }

  /// The fixture's tables in a fresh local database; `stress` primary (the phone's stress rows are used) and the log /
  /// journal pulled once.
  private static func database(_ f: [String: Any]) throws -> AppDatabase {
    let db = try AppDatabase.inMemory()
    let t = try XCTUnwrap(f["tables"] as? [String: Any])
    let rows = { (k: String) in t[k] as? [[String: Any]] ?? [] }
    try db.writer.write { db in
      for r in rows("daily_scores") {
        try db.execute(sql: """
          INSERT INTO daily_scores (date, recovery, sleep_score, strain, payload, origin, computed_at) VALUES (?, ?, ?, ?, ?, 'server', 0)
          """, arguments: [r["date"] as? String, TodayModel.num(r["recovery"]), TodayModel.num(r["sleep_score"]),
                           TodayModel.num(r["strain"]), try data(r["payload"] ?? [String: Any]())])
      }
      for r in rows("sleep_sessions") {
        try db.execute(sql: """
          INSERT INTO sleep_sessions (id, date, start, end, is_main, source, updated_at, payload) VALUES (?, ?, ?, ?, ?, ?, 0, ?)
          """, arguments: [r["id"] as? String, r["date"] as? String, TodayModel.num(r["start"]), TodayModel.num(r["end"]),
                           r["is_main"] as? Bool ?? true, r["source"] as? String, try data(r["payload"] ?? [String: Any]())])
      }
      for r in rows("daily_metrics") {
        try db.execute(sql: "INSERT INTO daily_metrics (date, metric, value, unit, source, updated_at) VALUES (?, ?, ?, NULL, 'server', 0)",
                       arguments: [r["date"] as? String, r["metric"] as? String, TodayModel.num(r["value"])])
      }
      for r in rows("stress_daily") {
        try db.execute(sql: """
          INSERT INTO stress_daily (date, score, high_h, hours, flags, algo_version, bundle_version, computed_at)
          VALUES (?, ?, ?, ?, ?, 'test', 'test', 0)
          """, arguments: [r["date"] as? String, TodayModel.num(r["score"]), (r["high_h"] as? Int) ?? 0,
                           try data([Any]()), try data([String: Any]())])
      }
      for r in rows("workouts") {
        try db.execute(sql: """
          INSERT INTO workouts (key, source, external_id, local_date, type, start, end, duration_min, computed_by, row, updated_at)
          VALUES (?, 'app', ?, ?, 'other', ?, ?, ?, 'phone', ?, 0)
          """, arguments: [r["key"] as? String, r["key"] as? String, r["local_date"] as? String, TodayModel.num(r["start"]),
                           TodayModel.num(r["start"]), TodayModel.num(r["duration_min"]), try data(r["row"] ?? [String: Any]())])
      }
      for r in rows("journal") {
        let d = r["date"] as? String ?? "", k = r["key"] as? String ?? ""
        try db.execute(sql: "INSERT INTO records (kind, id, date, payload, updated_at) VALUES ('journal', ?, ?, ?, 0)",
                       arguments: ["\(d):\(k)", d, try data(r)])
      }
      for r in rows("journal_items") {
        try db.execute(sql: "INSERT INTO records (kind, id, payload, updated_at) VALUES ('journal_items', ?, ?, 0)",
                       arguments: [r["key"] as? String, try data(r)])
      }
    }
    for r in rows("ext_records") {
      try db.saveDoc(DocPuller.extRecordsKey(r["date"] as? String ?? ""), json: try data(r))
    }
    let packages: [String: Any] = ["stress": ["state": "primary", "flag": true, "implemented": true] as [String: Any],
                                   "reports": ["state": "shadow", "flag": true, "implemented": true] as [String: Any]]
    try db.saveDoc(LocalCompute.stateDoc, json: try data(["compute": ["packages": packages] as [String: Any]] as [String: Any]))
    try db.setMeta(ReportsLocal.cursorKey, "test")
    try db.setMeta(ReportsLocal.extOkMeta, "1")
    return db
  }

  private func expect(_ local: [String: Any]?, _ server: Any?, file: StaticString = #filePath, line: UInt = #line) throws {
    let l = try XCTUnwrap(local, file: file, line: line), s = try XCTUnwrap(server as? [String: Any], file: file, line: line)
    let c = SeriesLocal.compare(local: l, server: s)
    XCTAssertTrue(c.ok, "differs: \(c.fieldsOver)", file: file, line: line)
    XCTAssertGreaterThan(c.compared, 20, file: file, line: line)
  }

  /// Contract acceptance: the report (week, month) and the journal analysis (both versions) from the local tables equal
  /// the server's answers on the same data; nutrition is the server's block.
  func testLocalReportsMatchTheServer() throws {
    let f = try Self.fixture(), db = try Self.database(f)
    let exp = try XCTUnwrap(f["expected"] as? [String: Any])
    let rt = try OpenBodyScoreRuntime.builtin()
    XCTAssertNotNil(rt.algoVersions["reports"], "the built-in bundle carries M5")
    let ctx = ReportsLocal.Context(tz: f["tz"] as? String ?? "UTC", today: f["today"] as? String ?? "")
    for p in ["week", "month"] {
      let out = try ReportsLocal.compute(.report(period: p, end: nil), db: db, runtime: rt, ctx: ctx, server: nil)
      XCTAssertEqual(out?["server_only"] as? [String], ["nutrition"])
      XCTAssertEqual(out?["algo_version"] as? String, rt.algoVersions["reports"])
      try expect(out, exp[p])
    }
    for (key, regression) in [("analysis", false), ("analysis_regression", true)] {
      let out = try XCTUnwrap(try ReportsLocal.compute(.analysis(end: nil, regression: regression), db: db, runtime: rt, ctx: ctx, server: nil))
      try expect(["results": out["results"] ?? NSNull()], ["results": exp[key] ?? NSNull()])
      let list = BehaviorRow.list(try JSONSerialization.data(withJSONObject: out))
      XCTAssertEqual(BehaviorRow.adjusted(list), regression)
      XCTAssertFalse(BehaviorRow.parse(list).rows.isEmpty)
    }
  }

  /// Blocks without their input on the phone come from the server's document; without one they are left out.
  func testServerOnlyBlocks() throws {
    let f = try Self.fixture(), db = try Self.database(f)
    try db.setMeta(ReportsLocal.extOkMeta, "0")
    try db.writer.write { db in try db.execute(sql: "DELETE FROM meta WHERE key = ?", arguments: [ReportsLocal.cursorKey]) }
    let rt = try OpenBodyScoreRuntime.builtin()
    let ctx = ReportsLocal.Context(tz: f["tz"] as? String ?? "UTC", today: f["today"] as? String ?? "")
    let current: [String: Any] = ["training": ["sessions": 3], "weight": ["last_kg": 80.0], "nutrition": ["days_logged": 2]]
    let server: [String: Any] = ["current": current, "previous_values": ["training": ["sessions": 1]],
                                 "change": ["training": ["sessions": 2]], "journal_findings": [["key": "sauna"]]]
    let out = try XCTUnwrap(try ReportsLocal.compute(.report(period: "week", end: nil), db: db, runtime: rt, ctx: ctx, server: server))
    XCTAssertEqual(Set(out["server_only"] as? [String] ?? []), ["nutrition", "training", "weight", "journal_findings"])
    let cur = try XCTUnwrap(out["current"] as? [String: Any])
    XCTAssertEqual((cur["training"] as? [String: Any])?["sessions"] as? Int, 3)
    XCTAssertEqual((cur["weight"] as? [String: Any])?["last_kg"] as? Double, 80.0)
    XCTAssertNotNil(cur["recovery"])
    XCTAssertEqual((out["journal_findings"] as? [[String: Any]])?.count, 1)
    let bare = try XCTUnwrap(try ReportsLocal.compute(.report(period: "week", end: nil), db: db, runtime: rt, ctx: ctx, server: nil))
    XCTAssertNil((bare["current"] as? [String: Any])?["training"], "never a block computed from missing inputs")
    XCTAssertNil(try ReportsLocal.compute(.analysis(end: nil, regression: false), db: db, runtime: rt, ctx: ctx, server: nil),
                 "no journal before the first pull")
  }

  /// The filtered journal pull's whole-day replace leaves pending local answers alone; the items list is replaced.
  func testJournalApply() throws {
    let db = try AppDatabase.inMemory()
    try db.writer.write { db in
      try db.execute(sql: "INSERT INTO records (kind, id, date, payload, updated_at, pending) VALUES ('journal', 'c:x', '2026-10-01', ?, 0, 1)",
                     arguments: [Data("{}".utf8)])
      try ReportsLocal.applyJournalDay(db, day: "2026-10-01", items: [["date": "2026-10-01", "key": "a", "value": 1],
                                                                      ["date": "2026-10-01", "key": "b", "value": 0]])
      try ReportsLocal.applyJournalDay(db, day: "2026-10-01", items: [["date": "2026-10-01", "key": "a", "value": 0]])
      try ReportsLocal.applyJournalItems(db, items: [["key": "a", "label_zh": "A", "active": true, "sort": 2],
                                                     ["key": "b", "label_zh": "B", "active": false, "sort": 1]])
    }
    let n = try db.writer.read { db in
      (try Int.fetchOne(db, sql: "SELECT count(*) FROM records WHERE kind = 'journal' AND pending = 0") ?? -1,
       try Int.fetchOne(db, sql: "SELECT count(*) FROM records WHERE kind = 'journal' AND pending = 1") ?? -1)
    }
    XCTAssertEqual(n.0, 1)
    XCTAssertEqual(n.1, 1)
    let data = try db.writer.read { try ReportsLocal.journalData($0, end: "2026-10-01", days: 90) }
    XCTAssertEqual((data["items"] as? [[String: Any]])?.compactMap { $0["key"] as? String }, ["a"], "inactive items are left out")
    XCTAssertEqual((data["entries"] as? [[String: Any]])?.first?["value"] as? Double, 0)
  }
}
