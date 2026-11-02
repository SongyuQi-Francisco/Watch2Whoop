import GRDB
import XCTest
@testable import Watch2Whoop

/// M3 (docs/ios-native-2.0.md section 4): the phone's own Trends series, month calendar, nights, sleep regularity and
/// VO₂max level. The fixture is written by body_engine/tests/test_js_golden_series.py (BODY_WRITE_GOLDEN=1): 120 synthetic
/// days as the phone's local tables, and what the server's Python answers for the same data. The local tables go through
/// `SeriesLocal`'s loader and the built-in bundle; every value must be within 1e-9 of the server's.
final class TrendsSeriesTests: XCTestCase {
  override func setUp() {
    super.setUp()
    Lang.override = "zh"
  }

  override func tearDown() {
    Lang.override = nil
    super.tearDown()
  }

  private static func fixture() throws -> [String: Any] {
    let url = URL(fileURLWithPath: #filePath).deletingLastPathComponent()
      .appendingPathComponent("../../../packages/openbodyscore-js/test/golden/series_local.json").standardized
    guard let data = try? Data(contentsOf: url) else { throw XCTSkip("fixture not readable at \(url.path)") }
    return try XCTUnwrap(try JSONSerialization.jsonObject(with: data) as? [String: Any])
  }

  private static func data(_ o: Any) throws -> Data { try JSONSerialization.data(withJSONObject: o, options: [.sortedKeys]) }

  /// The fixture's tables in a fresh local database.
  private static func database(_ f: [String: Any]) throws -> AppDatabase {
    let db = try AppDatabase.inMemory()
    let t = try XCTUnwrap(f["tables"] as? [String: Any])
    try db.writer.write { db in
      for r in t["daily_scores"] as? [[String: Any]] ?? [] {
        try db.execute(sql: """
          INSERT INTO daily_scores (date, recovery, sleep_score, strain, payload, origin, computed_at) VALUES (?, ?, ?, ?, ?, 'server', 0)
          """, arguments: [r["date"] as? String, TodayModel.num(r["recovery"]), TodayModel.num(r["sleep_score"]),
                           TodayModel.num(r["strain"]), try data(r["payload"] ?? [String: Any]())])
      }
      for r in t["daily_metrics"] as? [[String: Any]] ?? [] {
        try db.execute(sql: "INSERT INTO daily_metrics (date, metric, value, unit, source, updated_at) VALUES (?, ?, ?, NULL, 'server', 0)",
                       arguments: [r["date"] as? String, r["metric"] as? String, TodayModel.num(r["value"])])
      }
      for r in t["sleep_sessions"] as? [[String: Any]] ?? [] {
        try db.execute(sql: """
          INSERT INTO sleep_sessions (id, date, start, end, is_main, source, updated_at, payload) VALUES (?, ?, ?, ?, ?, ?, 0, ?)
          """, arguments: [r["id"] as? String, r["date"] as? String, TodayModel.num(r["start"]), TodayModel.num(r["end"]),
                           r["is_main"] as? Bool ?? true, r["source"] as? String, try data(r["payload"] ?? [String: Any]())])
      }
      for r in t["records_weight"] as? [[String: Any]] ?? [] {
        try db.execute(sql: "INSERT INTO records (kind, id, date, payload, updated_at) VALUES ('weight', ?, ?, ?, 0)",
                       arguments: [r["id"] as? String, r["date"] as? String, try data(r["payload"] ?? [String: Any]())])
      }
    }
    try db.saveDoc(SeriesLocal.profileDoc, json: try data(f["profile"] ?? [String: Any]()))
    return db
  }

  private static func context(_ f: [String: Any]) -> SeriesLocal.Context {
    SeriesLocal.Context(tz: f["tz"] as? String ?? "UTC", today: f["today"] as? String ?? "", nowHour: (f["now_hour"] as? Int) ?? 12)
  }

  private func expect(_ local: [String: Any]?, _ server: Any?, ignore: Set<String> = [], file: StaticString = #filePath, line: UInt = #line) throws {
    let l = try XCTUnwrap(local, file: file, line: line), s = try XCTUnwrap(server as? [String: Any], file: file, line: line)
    let c = SeriesLocal.compare(local: l, server: s, ignore: ignore)
    XCTAssertTrue(c.ok, "differs: \(c.fieldsOver)", file: file, line: line)
    XCTAssertGreaterThan(c.compared, 10, file: file, line: line)
  }

  /// Contract acceptance: `/trends` vs the local series on 120 synthetic days (the parts the phone computes itself).
  func testLocalSeriesMatchTheServer() throws {
    let f = try Self.fixture(), db = try Self.database(f), ctx = Self.context(f)
    let exp = try XCTUnwrap(f["expected"] as? [String: Any])
    let rt = try OpenBodyScoreRuntime.builtin()
    XCTAssertNotNil(rt.algoVersions["series"], "the built-in bundle carries M3")
    let days = f["days"] as? Int ?? 30
    let trends = try SeriesLocal.compute(.trends(days: days, end: nil), db: db, runtime: rt, ctx: ctx, server: nil)
    let only = Set(trends?["server_only_paths"] as? [String] ?? [])
    XCTAssertEqual(only, ["series.stress", "series.resilience", "series.body_age", "series.hr_recovery", "status.resilience",
                          "status.body_battery", "status.hr_recovery", "unavailable.stress", "unavailable.resilience",
                          "unavailable.body_age", "unavailable.hr_recovery"],
                   "no stress_daily / healthspan_weekly / workouts on the phone yet: those come from the server's document")
    try expect(trends, exp["trends"], ignore: only)
    XCTAssertEqual(trends?["algo_version"] as? String, rt.algoVersions["series"])
    XCTAssertNotNil(TrendsData(json: try XCTUnwrap(trends)), "the Trends page parses the phone's document")
    try expect(try SeriesLocal.compute(.month(f["month"] as? String ?? ""), db: db, runtime: rt, ctx: ctx, server: nil), exp["month"])
    try expect(try SeriesLocal.compute(.nights(end: nil), db: db, runtime: rt, ctx: ctx, server: nil), exp["nights"])
    try expect(try SeriesLocal.compute(.details(end: nil), db: db, runtime: rt, ctx: ctx, server: nil), exp["details"])
    try expect(try SeriesLocal.compute(.vo2(end: nil), db: db, runtime: rt, ctx: ctx, server: nil), exp["vo2"])
  }

  /// The server-only parts (stress, resilience, body age, heart-rate recovery) come from the server's document of the
  /// same range; without one they are "没有数据", never a number from missing inputs.
  func testServerOnlyPartsComeFromTheServersDocument() throws {
    let f = try Self.fixture(), db = try Self.database(f), ctx = Self.context(f)
    let rt = try OpenBodyScoreRuntime.builtin()
    let stress: [String: Any] = ["label": "压力", "values": [1.2], "n": 1]
    let server: [String: Any] = ["series": ["stress": stress], "unavailable": ["body_age": "没有授权查看 scores"],
                                 "status": ["resilience": ["level": "solid"]]]
    let out = try XCTUnwrap(try SeriesLocal.compute(.trends(days: 30, end: nil), db: db, runtime: rt, ctx: ctx, server: server))
    let series = try XCTUnwrap(out["series"] as? [String: Any]), unavailable = try XCTUnwrap(out["unavailable"] as? [String: Any])
    XCTAssertEqual((series["stress"] as? [String: Any])?["values"] as? [Double], [1.2])
    XCTAssertNil(series["resilience"])
    XCTAssertEqual(unavailable["resilience"] as? String, "没有数据")
    XCTAssertEqual(unavailable["body_age"] as? String, "没有授权查看 scores")
    XCTAssertNil(unavailable["stress"])
    XCTAssertEqual(((out["status"] as? [String: Any])?["resilience"] as? [String: Any])?["level"] as? String, "solid")
    XCTAssertTrue((out["status"] as? [String: Any])?["body_battery"] is NSNull)
    XCTAssertNotNil(series["recovery"], "the phone's own series stay")
  }

  /// off / shadow: the page draws the server's document; primary: the phone's once computed.
  func testThePageDrawsThePhonesDocumentOnlyInPrimary() throws {
    let db = try AppDatabase.inMemory()
    try db.saveDoc("trends:30", json: Data(#"{"who": "server"}"#.utf8))
    let shown: () throws -> String? = { try db.writer.read { try SeriesLocal.shown($0, server: "trends:30", local: SeriesLocal.trendsKey(30, end: nil)) }?.json?["who"] as? String }
    XCTAssertEqual(try shown(), "server")
    func state(_ s: String) throws {
      try db.saveDoc(LocalCompute.stateDoc, json: Data(#"{"compute": {"packages": {"series": {"state": "\#(s)", "kinds": {}}}}}"#.utf8))
    }
    try state("primary")
    XCTAssertEqual(try shown(), "server", "primary, but nothing computed yet: the server's")
    try db.saveDoc(SeriesLocal.trendsKey(30, end: nil), json: Data(#"{"who": "phone"}"#.utf8))
    XCTAssertEqual(try shown(), "phone")
    try state("shadow")
    XCTAssertEqual(try shown(), "server", "shadow: computed and compared, the server's still shown")
    XCTAssertEqual(SeriesLocal.trendsKey(30, end: "2026-10-01"), "trends.local.series:30:2026-10-01")
    XCTAssertEqual(SeriesLocal.daysKey("2026-10"), "days.local:2026-10")
    XCTAssertEqual(SeriesLocal.nightsKey(nil), "sleep.local:nights:")
  }

  /// Shadow comparison: names of the fields over the tolerance only, kept per job in meta.
  func testComparisonKeepsFieldNamesOnly() throws {
    let a: [String: Any] = ["series": ["hrv": ["values": [50.0, 51.000000000001, NSNull()], "label": "HRV"]], "algo_version": "x"]
    var b: [String: Any] = ["series": ["hrv": ["values": [50.0, 51.0, NSNull()], "label": "HRV"]]]
    XCTAssertTrue(SeriesLocal.compare(local: a, server: b).ok, "1e-9 relative")
    b = ["series": ["hrv": ["values": [50.0, 51.2, 3.0], "label": "HRV (SDNN)"]]]
    let c = SeriesLocal.compare(local: a, server: b)
    XCTAssertEqual(c.fieldsOver, ["series.hrv.label", "series.hrv.values"])
    XCTAssertFalse(c.ok)
    let f = try Self.fixture(), db = try Self.database(f), ctx = Self.context(f)
    let month = try XCTUnwrap((f["expected"] as? [String: Any])?["month"] as? [String: Any])
    try db.saveDoc(TodayDocs.days(month["month"] as? String ?? ""), json: try Self.data(month))
    let rt = try OpenBodyScoreRuntime.builtin()
    XCTAssertNotNil(try SeriesLocal.run(.month(month["month"] as? String ?? ""), db: db, runtime: rt, ctx: ctx))
    let meta = try XCTUnwrap(try db.meta(SeriesLocal.compareMeta))
    let rep = try XCTUnwrap((try JSONSerialization.jsonObject(with: Data(meta.utf8)) as? [String: Any])?["days"] as? [String: Any])
    XCTAssertEqual(rep["ok"] as? Bool, true, "\(rep)")
    XCTAssertEqual(rep["fields_over"] as? [String], [])
    XCTAssertNotNil(try db.doc(SeriesLocal.daysKey(month["month"] as? String ?? "")), "the phone's month is cached")
  }

  /// Weigh-ins: Apple's body mass < the weight log (records) < the log's raw rows (ext_records) < this phone's unsent entries.
  func testWeightsFollowTheServersSources() throws {
    let db = try AppDatabase.inMemory()
    try db.writer.write { db in
      for (d, v) in [("2026-10-01", 90.0), ("2026-10-02", 90.5), ("2026-10-03", 91.0), ("2026-10-04", 91.5)] {
        try db.execute(sql: "INSERT INTO daily_metrics (date, metric, value, unit, source, updated_at) VALUES (?, 'body_mass', ?, 'kg', 'app', 0)",
                       arguments: [d, v])
      }
      try db.execute(sql: "INSERT INTO records (kind, id, date, payload, updated_at) VALUES ('weight', '1', '2026-10-02', ?, 0)",
                     arguments: [Data(#"{"weight_kg": 89.0}"#.utf8)])
      try db.execute(sql: "INSERT INTO records (kind, id, date, payload, updated_at) VALUES ('weight', '2', '2026-10-03', ?, 0)",
                     arguments: [Data(#"{"weight_kg": 89.5}"#.utf8)])
      try db.execute(sql: "INSERT INTO records (kind, id, date, payload, updated_at, pending) VALUES ('weight', 'c:3', '2026-10-04', ?, 1, 1)",
                     arguments: [Data(#"{"weight_kg": 88.0}"#.utf8)])
    }
    try db.saveDoc(DocPuller.extRecordsKey("2026-10-03"), json: Data(#"{"date": "2026-10-03", "weights": [{"weight_kg": 89.9}]}"#.utf8))
    try db.saveDoc(DocPuller.extRecordsKey("2026-10-04"), json: Data(#"{"date": "2026-10-04", "weights": [{"weight_kg": 87.0}]}"#.utf8))
    let w = try db.writer.read { try SeriesLocal.weights($0, from: "2026-10-01", to: "2026-10-04") }
    XCTAssertEqual(w, ["2026-10-01": 90.0, "2026-10-02": 89.0, "2026-10-03": 89.9, "2026-10-04": 88.0])
  }
}
