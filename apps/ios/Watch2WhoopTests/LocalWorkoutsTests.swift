import GRDB
import XCTest
@testable import Watch2Whoop

/// 2.0.0 M1c (docs/ios-native-2.0.md section 4): the phone's workouts. The fixture is the golden file's `new_york` case
/// (packages/openbodyscore-js/test/golden/workouts.json: four synthetic workouts over the start of DST, made-up values;
/// its `want` is the server's training/importer.workout_days, which body_engine/tests/test_native_workouts.py checks equals
/// the server's own import + enrich + hrr on the same samples). Here the same HKWorkouts and heart rate go through the
/// local tables (`hk_workouts`, `hk_samples`, `daily_scores`) and WorkoutsStage.
final class LocalWorkoutsTests: XCTestCase {
  private func api() -> APIClient {
    let c = APIClient(baseURL: URL(string: "https://body.example.com")!, token: { "example-token" }, session: StubProtocol.session(),
                      sleep: { _ in })
    c.maxAttempts = 1
    return c
  }

  private struct Fixture {
    var src: [String: Any]
    var want: [String: Any]
    var zone: TimeZone
    var days: [String]
  }

  private static func fixture() throws -> Fixture {
    let url = try XCTUnwrap(Bundle(for: LocalWorkoutsTests.self).url(forResource: "workouts", withExtension: "json"),
                            "workouts.json is a resource of the test bundle (project.yml)")
    let g = try XCTUnwrap(JSONSerialization.jsonObject(with: Data(contentsOf: url)) as? [String: Any])
    let c = try XCTUnwrap((g["workouts"] as? [[String: Any]])?.first { $0["src"] != nil })
    let src = try XCTUnwrap(c["src"] as? [String: Any])
    return Fixture(src: src, want: try XCTUnwrap(c["want"] as? [String: Any]),
                   zone: try XCTUnwrap(TimeZone(identifier: try XCTUnwrap(src["tz"] as? String))),
                   days: try XCTUnwrap(src["days"] as? [String]))
  }

  /// The fixture's HKWorkouts (the sync engine's shape), heart rate and the days' HRmax in the local tables.
  private static func load(_ f: Fixture, into db: AppDatabase) throws {
    try db.writer.write { db in
      var sources: [String: Int64] = [:]
      for w in (f.src["hk_workouts"] as? [[String: Any]]) ?? [] {
        let name = w["source_name"] as? String ?? ""
        if sources[name] == nil {
          sources[name] = try LocalRawStore.sourceId(db, .init(name: name, bundle: "org.example.\(sources.count)",
                                                               deviceType: w["device_type"] as? String ?? "other",
                                                               deviceName: w["device_name"] as? String ?? ""))
        }
        let meta = w["meta"] as? [String: Any] ?? [:]
        try db.execute(sql: """
          INSERT INTO hk_workouts (uuid, activity, activity_name, start, end, duration_s, stats, events, meta, source_id, uploaded)
          VALUES (?, ?, ?, ?, ?, ?, ?, '[]', ?, ?, 1)
          """, arguments: [(w["uuid"] as? String)?.lowercased(), (meta["activity"] as? NSNumber)?.intValue, w["value_text"] as? String,
                           LocalRawStore.num(w["start"]), LocalRawStore.num(w["end"]), (LocalRawStore.num(w["value"]) ?? 0) * 60,
                           try JSONSerialization.data(withJSONObject: meta["stats"] ?? [String: Any]()),
                           try JSONSerialization.data(withJSONObject: ["tz": w["tz"] ?? NSNull()]), sources[name]])
      }
      let watch = sources.values.first
      let hr = (f.src["hr"] as? [String: Any])?["rows"] as? [[Any]] ?? []
      for (i, r) in hr.enumerated() {
        try db.execute(sql: """
          INSERT OR IGNORE INTO hk_samples (uuid, type, start, end, value, source_id) VALUES (?, 'HKQuantityTypeIdentifierHeartRate', ?, ?, ?, ?)
          """, arguments: ["hr-\(i)", LocalRawStore.num(r[0]), LocalRawStore.num(r[0]), LocalRawStore.num(r[1]), watch])
      }
      for (d, v) in (f.src["hr_max"] as? [String: Any]) ?? [:] {
        try db.execute(sql: """
          INSERT INTO daily_scores (date, payload, origin, computed_at) VALUES (?, ?, 'server', 1)
          """, arguments: [d, try JSONSerialization.data(withJSONObject: ["hr_max": v])])
      }
    }
  }

  /// JSON equality, floats within 1e-9 relative (the cross-language tolerance of packages/openbodyscore-js).
  private static func diff(_ a: Any?, _ b: Any?, _ path: String = "") -> String? {
    switch (a, b) {
    case let (x as [String: Any], y as [String: Any]):
      if Set(x.keys) != Set(y.keys) { return "\(path): keys \(x.keys.sorted()) != \(y.keys.sorted())" }
      for k in x.keys.sorted() { if let d = diff(x[k], y[k], "\(path)/\(k)") { return d } }
      return nil
    case let (x as [Any], y as [Any]):
      if x.count != y.count { return "\(path): \(x.count) != \(y.count) items" }
      for i in x.indices { if let d = diff(x[i], y[i], "\(path)[\(i)]") { return d } }
      return nil
    case let (x as NSNumber, y as NSNumber):
      if CFGetTypeID(x) == CFBooleanGetTypeID() || CFGetTypeID(y) == CFBooleanGetTypeID() {
        return x.boolValue == y.boolValue && CFGetTypeID(x) == CFGetTypeID(y) ? nil : "\(path): \(x) != \(y)"
      }
      let p = x.doubleValue, q = y.doubleValue
      return abs(p - q) <= max(1e-9 * max(abs(p), abs(q)), 1e-9) ? nil : "\(path): \(p) != \(q)"
    case let (x as String, y as String):
      return x == y ? nil : "\(path): \(x) != \(y)"
    case (is NSNull, is NSNull), (nil, nil), (is NSNull, nil), (nil, is NSNull):
      return nil
    default:
      return "\(path): \(String(describing: a)) != \(String(describing: b))"
    }
  }

  /// The four workouts through the local tables and the bundle give the server's rows; the local tables follow.
  func testWorkoutsThroughTheLocalTablesGiveTheServersRows() throws {
    let f = try Self.fixture()
    let db = try AppDatabase.inMemory()
    try Self.load(f, into: db)
    let rt = try OpenBodyScoreRuntime.builtin()
    XCTAssertEqual(rt.algoVersions["workouts"], f.want["algo_version"] as? String)
    let written = try WorkoutsStage.computeChunk(db: db, zone: f.zone, runtime: rt, days: f.days, birthDate: nil)
    XCTAssertEqual(written.count, 4)
    let want = try XCTUnwrap(f.want["days"] as? [String: Any])
    try db.writer.read { db in
      for d in f.days {
        let data = try XCTUnwrap(Data.fetchOne(db, sql: "SELECT rows FROM derived_rows WHERE kind = 'workouts' AND date = ?", arguments: [d]))
        XCTAssertNil(Self.diff(try JSONSerialization.jsonObject(with: data), want[d], d))
      }
      XCTAssertEqual(try Int.fetchOne(db, sql: "SELECT count(*) FROM workouts WHERE computed_by = 'phone' AND trimp IS NOT NULL"), 4)
      XCTAssertEqual(try Int.fetchOne(db, sql: "SELECT count(*) FROM workout_hrr WHERE computed_by = 'phone'"), 4)
      XCTAssertGreaterThan(try Int.fetchOne(db, sql: "SELECT count(*) FROM score_dirty WHERE kind = 'derive'") ?? 0, 0,
                           "the days' workout energy changed: derived again")
    }
    // the same inputs again: nothing to upload anew
    XCTAssertEqual(try WorkoutsStage.computeChunk(db: db, zone: f.zone, runtime: rt, days: f.days, birthDate: nil), [])
  }

  /// A pulled server row of another source that duplicates the phone's workout is marked as its copy; the calendar of
  /// that month counts the workout once and carries its TRIMP.
  func testServerCopyIsADuplicateAndTheCalendarCountsOnce() throws {
    let f = try Self.fixture()
    let db = try AppDatabase.inMemory()
    try Self.load(f, into: db)
    let first = try XCTUnwrap((f.src["hk_workouts"] as? [[String: Any]])?.first)
    let d = f.days[0]
    try db.writer.write { db in
      try WorkoutsStage.applyServerDay(db, day: d, items: [[
        "id": 7, "source": "ow", "external_id": "ow-1", "type": "walking", "name_zh": "步行 / 爬坡", "local_date": d,
        "start_at": try XCTUnwrap(LocalRawStore.num(first["start"])) + 30, "end_at": try XCTUnwrap(LocalRawStore.num(first["end"])),
        "duration_min": 44.5, "active_kcal": 300.0, "is_xml": false, "device": "Apple Watch", "excluded": NSNull(),
        "dup_of": NSNull(), "flags": [String: Any](), "trimp": 10.0, "hr_zone_min": NSNull(), "hr_max_used": NSNull(),
        "avg_hr": 140.0, "max_hr": 170.0, "total_kcal": 350.0, "distance_km": NSNull(), "origin": "Apple Watch",
      ]])
    }
    let rt = try OpenBodyScoreRuntime.builtin()
    _ = try WorkoutsStage.computeChunk(db: db, zone: f.zone, runtime: rt, days: f.days, birthDate: nil)
    try db.writer.read { db in
      XCTAssertEqual(try String.fetchOne(db, sql: "SELECT dup_of FROM workouts WHERE key = 'ow:ow-1'")?.hasPrefix("app:"), true)
    }
    let month = String(d.prefix(7))
    let cal = try XCTUnwrap(TrainingLocal.month(month, db: db, runtime: rt, zone: f.zone, today: f.days[3]))
    let day = try XCTUnwrap((cal["days"] as? [[String: Any]])?.first { $0["date"] as? String == d })
    XCTAssertEqual(day["sessions_count"] as? Int, 1)
    let detail = try XCTUnwrap(TrainingLocal.day(d, db: db, runtime: rt, zone: f.zone, today: f.days[3]))
    let s = try XCTUnwrap((detail["sessions"] as? [[String: Any]])?.first)
    XCTAssertNotNil(LocalRawStore.num(s["trimp"]))
    XCTAssertEqual(s["source"] as? String, "app")
    XCTAssertNotNil(try db.doc(TrainingLocal.calendarKey(month)))
  }

  /// The stage end to end in shadow: computes, uploads the settled days once (newest first), nothing the second time.
  func testStageComputesInShadowAndUploadsOnce() async throws {
    let f = try Self.fixture()
    let db = try AppDatabase.inMemory()
    try Self.load(f, into: db)
    try db.saveDoc(ScoringSync.contextDoc, json: Data(#"{"tz": "\#(f.zone.identifier)"}"#.utf8))
    let saved = (WorkoutsStage.readHealth, WorkoutsStage.pullServer, WorkoutsStage.uploadGate)
    WorkoutsStage.readHealth = nil
    WorkoutsStage.pullServer = nil
    WorkoutsStage.uploadGate = { true }
    defer { (WorkoutsStage.readHealth, WorkoutsStage.pullServer, WorkoutsStage.uploadGate) = saved }
    let ok = #"{"status": "ok", "state": "shadow", "stored": 0}"#
    StubProtocol.reset([.init(status: 200, body: ok)])
    let state = ComputeState(packages: ["workouts": .init(state: .shadow, flag: true, implemented: true, kinds: [:])], primaryFlag: false)
    let now = try XCTUnwrap(LocalDay.parse(try XCTUnwrap(f.days.last), f.zone))
    let ctx = ComputeContext(db: db, api: api(), state: state, reason: "test", now: now)
    let rep = await WorkoutsStage().run(ctx)
    XCTAssertEqual(rep.state, "done", rep.detail ?? "")
    XCTAssertEqual(rep.written.count, 4)
    XCTAssertEqual(StubProtocol.requests.map { $0.url!.path }, [Endpoint.derivedUpload])
    let body = try XCTUnwrap(JSONSerialization.jsonObject(with: XCTUnwrap(StubProtocol.requests[0].httpBodyStream.map(DeltaRemoteTests.read)
                                                                           ?? StubProtocol.requests[0].httpBody)) as? [String: Any])
    XCTAssertEqual(body["kind"] as? String, "workouts")
    XCTAssertEqual((body["days"] as? [[String: Any]])?.first?["date"] as? String, f.days.last, "newest first")
    StubProtocol.reset([])
    let again = await WorkoutsStage().run(ctx)
    XCTAssertEqual(again.written, [])
    XCTAssertEqual(StubProtocol.requests.count, 0)
  }
}
