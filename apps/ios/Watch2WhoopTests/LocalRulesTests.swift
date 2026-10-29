import GRDB
import XCTest
@testable import Watch2Whoop

/// 2.0.0 M6 (docs/ios-native-2.0.md section 4): rule reminders, nutrition goals and explanation sheets on the phone.
/// rules_local.json (written by body_engine/tests/test_js_golden_rules.py): a made-up person's raw rule inputs — the
/// server's inputs document (exercise rules, plan, training and food log, goal versions, bedtime goal, diet constraints)
/// plus what the phone holds (scores, journal, body mass, sleep sessions over the change to daylight saving time) — and
/// the server's rules/engine.evaluate_data answer; test_native_rules.py checks that the server's own `load` gives the same
/// answer from the same tables. Here the inputs go through the local tables and RulesStage's loader.
final class LocalRulesTests: XCTestCase {
  override func setUp() {
    super.setUp()
    Lang.override = "zh"
  }

  override func tearDown() {
    Lang.override = nil
    super.tearDown()
  }

  private static func golden(_ name: String) throws -> [String: Any] {
    let url = URL(fileURLWithPath: #filePath).deletingLastPathComponent()
      .appendingPathComponent("../../../packages/openbodyscore-js/test/golden/\(name).json").standardized
    guard let data = try? Data(contentsOf: url) else { throw XCTSkip("fixture not readable at \(url.path)") }
    return try XCTUnwrap(try JSONSerialization.jsonObject(with: data) as? [String: Any])
  }

  private static func data(_ o: Any) throws -> Data { try JSONSerialization.data(withJSONObject: o, options: [.sortedKeys]) }
  private static func num(_ v: Any?) -> Double? { LocalRawStore.num(v) }

  private static func diff(_ a: Any?, _ b: Any?) -> [String] {
    SeriesLocal.compare(local: ["x": a ?? NSNull()], server: ["x": b ?? NSNull()]).fieldsOver
  }

  private static func instant(_ iso: String) -> Double? {
    let f = ISO8601DateFormatter()
    f.formatOptions = [.withInternetDateTime]
    return f.date(from: iso)?.timeIntervalSince1970
  }

  /// The fixture's raw input as the phone keeps it: the inputs document and its own tables.
  private static func load(_ raw: [String: Any], into db: AppDatabase) throws {
    let day = try XCTUnwrap(raw["day"] as? String)
    var inputs: [String: Any] = ["date": day]
    for k in RulesStage.inputKeys { inputs[k] = raw[k] ?? NSNull() }
    try db.writer.write { db in
      try DocRecord(key: RulesStage.inputsKey(day), version: 0, body: try data(inputs), fetchedAt: 0).insert(db)
      for r in raw["scores"] as? [[String: Any]] ?? [] {
        var payload = r
        payload.removeValue(forKey: "date")
        try db.execute(sql: "INSERT INTO daily_scores (date, recovery, payload, origin, computed_at) VALUES (?, ?, ?, 'app', 0)",
                       arguments: [r["date"] as? String, num(r["recovery"]), try data(payload)])
      }
      for (i, j) in (raw["journal"] as? [[String: Any]] ?? []).enumerated() {
        try db.execute(sql: "INSERT INTO records (kind, id, date, payload, updated_at) VALUES ('journal', ?, ?, ?, ?)",
                       arguments: ["j\(i)", j["date"] as? String, try data(["key": j["key"] ?? "", "value": j["value"] ?? 0]), Double(i)])
      }
      for r in raw["body_mass"] as? [[String: Any]] ?? [] {
        try db.execute(sql: "INSERT INTO daily_metrics (date, metric, value, unit, source, updated_at) VALUES (?, 'body_mass', ?, 'kg', 'app', 0)",
                       arguments: [r["date"] as? String, num(r["value"])])
      }
      for (i, s) in (raw["sleep_sessions"] as? [[String: Any]] ?? []).enumerated() {
        try db.execute(sql: """
          INSERT INTO sleep_sessions (id, date, start, end, is_main, source, payload, updated_at) VALUES (?, ?, ?, ?, ?, 'app', ?, 0)
          """, arguments: ["s\(i)", s["wake_date"] as? String, instant(s["start_at"] as? String ?? ""), instant(s["end_at"] as? String ?? ""),
                           s["is_main"] as? Bool ?? true, try data(["asleep_min": s["asleep_min"] ?? NSNull()])])
      }
    }
  }

  private static func strip(_ x: [String: Any]) -> [String: Any] {
    var o = x
    o.removeValue(forKey: "algo_version")
    o.removeValue(forKey: "bundle_version")
    return o
  }

  /// The bundle on the fixture's raw input gives the server's reminders and rule results.
  func testBundleOnTheRawInputGivesTheServersReminders() throws {
    let f = try Self.golden("rules_local")
    let raw = try XCTUnwrap(f["raw"] as? [String: Any]), want = try XCTUnwrap(f["result"] as? [String: Any])
    let rt = try OpenBodyScoreRuntime.builtin()
    XCTAssertNotNil(rt.algoVersions["rules"], "the built-in bundle carries M6")
    XCTAssertNotNil(rt.algoVersions["explain"])
    let got = try XCTUnwrap(try rt.invoke("rulesLocal", [raw]) as? [String: Any])
    XCTAssertEqual(Self.diff(Self.strip(got), want), [])
    XCTAssertFalse((want["reminders"] as? [Any] ?? []).isEmpty)
  }

  /// Contract acceptance: the same answer through the local tables and RulesStage's loader; the table, the upload rows
  /// and Today's document follow.
  func testRemindersThroughTheLocalTables() throws {
    let f = try Self.golden("rules_local")
    let raw = try XCTUnwrap(f["raw"] as? [String: Any]), want = try XCTUnwrap(f["result"] as? [String: Any])
    let day = try XCTUnwrap(raw["day"] as? String), zone = try XCTUnwrap(TimeZone(identifier: try XCTUnwrap(raw["tz"] as? String)))
    let now = Date(timeIntervalSince1970: try XCTUnwrap(Self.instant(try XCTUnwrap(raw["now"] as? String))))
    let db = try AppDatabase.inMemory()
    try Self.load(raw, into: db)
    let rt = try OpenBodyScoreRuntime.builtin()
    let loaded = try XCTUnwrap(try db.writer.read { try RulesStage.raw($0, day: day, now: now, zone: zone) })
    XCTAssertEqual(loaded["now"] as? String, raw["now"] as? String)
    XCTAssertEqual((loaded["journal"] as? [Any])?.count, (raw["journal"] as? [Any])?.count)
    XCTAssertEqual(Self.num(loaded["sleep_need"]), Self.num(raw["sleep_need"]))
    let got = try XCTUnwrap(try rt.invoke("rulesLocal", [loaded]) as? [String: Any])
    XCTAssertEqual(Self.diff(Self.strip(got), want), [])
    // the table, the upload rows, Today's document
    let written = try RulesStage.compute(db: db, runtime: rt, day: day, now: now, zone: zone)
    XCTAssertEqual(written, ["reminders:\(day)"])
    XCTAssertEqual(try RulesStage.compute(db: db, runtime: rt, day: day, now: now.addingTimeInterval(60), zone: zone), [],
                   "an unchanged answer is no new upload")
    let wantRem = want["reminders"] as? [[String: Any]] ?? []
    let n = try db.writer.read { try Int.fetchOne($0, sql: "SELECT count(*) FROM reminders WHERE date = ?", arguments: [day]) }
    XCTAssertEqual(n, wantRem.count)
    let rows = try XCTUnwrap(try db.writer.read { db in
      try Row.fetchOne(db, sql: "SELECT rows, uploaded FROM derived_rows WHERE kind = 'reminders' AND date = ?", arguments: [day])
    })
    let up = try XCTUnwrap(try JSONSerialization.jsonObject(with: rows["rows"] as Data) as? [[String: Any]])
    XCTAssertEqual(up.first?["role"] as? String, "run")
    XCTAssertEqual(up.first?["now"] as? String, raw["now"] as? String)
    XCTAssertEqual(up.count, wantRem.count + 1)
    XCTAssertTrue(up.dropFirst().allSatisfy { $0["role"] as? String == "reminder" && $0["status"] is NSNull })
    let doc = try XCTUnwrap(try db.doc(RulesStage.localKey(day))?.json)
    XCTAssertEqual((doc["reminders"] as? [Any])?.count, wantRem.count)
  }

  /// Dismissing on the phone: the local row and the next upload carry it; the server's copy only moves it forward.
  func testStatusOnThePhoneAndTheServersCopy() throws {
    let f = try Self.golden("rules_local")
    let raw = try XCTUnwrap(f["raw"] as? [String: Any])
    let day = try XCTUnwrap(raw["day"] as? String), zone = try XCTUnwrap(TimeZone(identifier: try XCTUnwrap(raw["tz"] as? String)))
    let now = Date(timeIntervalSince1970: try XCTUnwrap(Self.instant(try XCTUnwrap(raw["now"] as? String))))
    let db = try AppDatabase.inMemory()
    try Self.load(raw, into: db)
    let rt = try OpenBodyScoreRuntime.builtin()
    _ = try RulesStage.compute(db: db, runtime: rt, day: day, now: now, zone: zone)
    try db.writer.write { db in try db.execute(sql: "UPDATE derived_rows SET uploaded = 1") }
    let doc = try XCTUnwrap(try db.doc(RulesStage.localKey(day))?.json)
    let first = try XCTUnwrap((doc["reminders"] as? [[String: Any]])?.first)
    let id = try XCTUnwrap((first["id"] as? NSNumber)?.intValue)
    XCTAssertLessThan(id, 0, "no server id before the records pull")
    XCTAssertTrue(RulesStage.setStatus(db, id: id, status: "dismissed"))
    let after = try XCTUnwrap(try db.doc(RulesStage.localKey(day))?.json)
    XCTAssertEqual((after["reminders"] as? [Any])?.count, (doc["reminders"] as? [Any] ?? []).count - 1)
    let up = try XCTUnwrap(try db.writer.read { db in
      try Row.fetchOne(db, sql: "SELECT rows, uploaded FROM derived_rows WHERE kind = 'reminders' AND date = ?", arguments: [day])
    })
    XCTAssertEqual(up["uploaded"] as Bool, false)
    let rows = try XCTUnwrap(try JSONSerialization.jsonObject(with: up["rows"] as Data) as? [[String: Any]])
    XCTAssertEqual(rows.filter { $0["status"] as? String == "dismissed" }.count, 1)
    // the server still says "seen": the phone's dismissal stays (and the id arrives); then the server confirms it
    let item: [String: Any] = ["id": 4711, "rule_key": first["rule_key"] ?? "", "subject": first["subject"] ?? "", "status": "seen"]
    try db.writer.write { db in try RulesStage.applyServer(db, day: day, items: [item]) }
    var st = try XCTUnwrap(try db.writer.read { db in
      try Row.fetchOne(db, sql: "SELECT status, status_dirty, server_id FROM reminders WHERE server_id = 4711")
    })
    XCTAssertEqual(st["status"] as String, "dismissed")
    XCTAssertEqual(st["status_dirty"] as Bool, true)
    try db.writer.write { db in try RulesStage.applyServer(db, day: day, items: [item.merging(["status": "dismissed"]) { $1 }]) }
    st = try XCTUnwrap(try db.writer.read { db in try Row.fetchOne(db, sql: "SELECT status, status_dirty FROM reminders WHERE server_id = 4711") })
    XCTAssertEqual(st["status_dirty"] as Bool, false)
    // a recompute keeps the user's status
    _ = try RulesStage.compute(db: db, runtime: rt, day: day, now: now.addingTimeInterval(120), zone: zone)
    st = try XCTUnwrap(try db.writer.read { db in try Row.fetchOne(db, sql: "SELECT status FROM reminders WHERE server_id = 4711") })
    XCTAssertEqual(st["status"] as String, "dismissed")
  }

  /// No inputs document (never online since install): nothing is evaluated.
  func testNoInputsNoReminders() throws {
    let db = try AppDatabase.inMemory()
    let rt = try OpenBodyScoreRuntime.builtin()
    let zone = try XCTUnwrap(TimeZone(identifier: "Australia/Sydney"))
    XCTAssertEqual(try RulesStage.compute(db: db, runtime: rt, day: "2026-10-16", now: Date(), zone: zone), [])
  }

  /// The explanation sheet's personal part from the local tables (HRV against a 60-day baseline), on the server's
  /// static texts; the nutrition goals of the report resolve from the goal versions.
  func testExplainAndNutritionOnThePhone() throws {
    let db = try AppDatabase.inMemory()
    try db.writer.write { db in
      for k in 0..<67 {
        let d = LocalDay.add("2026-10-20", -k)
        let v = k < 7 ? 58.0 + Double(k % 3) : 50.0 + Double((k * 7) % 11 - 5) * 0.8
        try db.execute(sql: "INSERT INTO daily_metrics (date, metric, value, source, updated_at) VALUES (?, 'heart_rate_variability_sdnn', ?, 'app', 0)",
                       arguments: [d, v])
      }
      let server: [String: Any] = ["title": "HRV 心率变异性", "what": "示例", "read": [["range": "高", "label": "好", "tone": "good"]],
                                   "tips": ["示例"], "avoid": [String](), "current": ["value": 1]]
      try DocRecord(key: TodayDocs.explain("metric", "hrv", nil), version: 0, body: try Self.data(server), fetchedAt: 0).insert(db)
    }
    let rt = try OpenBodyScoreRuntime.builtin()
    let out = try XCTUnwrap(try ExplainLocal.compute(key: "hrv", day: nil, db: db, runtime: rt, tz: "Australia/Sydney", today: "2026-10-20"))
    XCTAssertEqual(out["title"] as? String, "HRV 心率变异性")
    XCTAssertEqual((out["current"] as? [String: Any])?["tone"] as? String, "good")
    XCTAssertEqual((out["read"] as? [[String: Any]])?.first?["label"] as? String, "好")
    XCTAssertTrue((out["ai_prompt"] as? String ?? "").contains("HRV 心率变异性"))
    let m = ExplainModel(json: out)
    XCTAssertFalse(m.advice.isEmpty)
    let cmp = try XCTUnwrap(try db.meta(ExplainLocal.compareMeta))
    XCTAssertTrue(cmp.contains("\"hrv\""))
    // goals: a version from 10-14 (Mon / Wed / Fri training), the day sums of the log
    let versions: [[String: Any]] = [["id": 3, "effective_from": "2026-10-14", "name": "减脂", "training_weekdays": [1, 3, 5],
                                      "training": ["calories": 2300, "protein_g": 170, "carbs_g": 230, "fat_g": 62],
                                      "rest": ["calories": 2000, "protein_g": 165, "carbs_g": 170, "fat_g": 60]]]
    let totals: [String: Any] = ["2026-10-16": ["entries": 3, "calories": 2280.0, "protein_g": 160.0]]
    let days = try XCTUnwrap(try rt.invoke("nutritionDays", [totals, versions, "2026-10-15", "2026-10-16", [String: Any]()]) as? [[String: Any]])
    XCTAssertEqual(days.count, 2)
    XCTAssertEqual(((days[1]["goal"] as? [String: Any])?["name"]) as? String, "训练日（减脂）")
    XCTAssertEqual(((days[1]["adherence"] as? [String: Any])?["calories"]) as? Bool, true)
    XCTAssertEqual(days[0]["logged"] as? Bool, false)
  }
}
