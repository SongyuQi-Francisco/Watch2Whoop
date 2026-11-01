import GRDB
import XCTest
@testable import Watch2Whoop

/// 2.0.0 M4 (docs/ios-native-2.0.md section 4): body age, the coach blocks and the sleep detail on the phone.
/// coach_local.json (written by body_engine/tests/test_js_golden_coach.py): a made-up person's body-age inputs over 420
/// days as the phone has them (score rows, daily values, nights, workouts, the SparkyFitness strength log and weigh-ins,
/// the journal's strength days, the profile) and the server's insights/healthspan_store.weekly_local rows;
/// test_native_coach.py checks that the server's own loader gives the same rows from the same tables. Here the inputs go
/// through the local tables and HealthspanStage's loader; the Today thresholds are compared with the bundle's block.
final class LocalCoachTests: XCTestCase {
  override func setUp() {
    super.setUp()
    Lang.override = "zh"
  }

  override func tearDown() {
    Lang.override = nil
    super.tearDown()
  }

  private func api() -> APIClient {
    let c = APIClient(baseURL: URL(string: "https://body.example.com")!, token: { "example-token" }, session: StubProtocol.session(),
                      sleep: { _ in })
    c.maxAttempts = 1
    return c
  }

  private static func golden(_ name: String) throws -> [String: Any] {
    let url = URL(fileURLWithPath: #filePath).deletingLastPathComponent()
      .appendingPathComponent("../../../packages/openbodyscore-js/test/golden/\(name).json").standardized
    guard let data = try? Data(contentsOf: url) else { throw XCTSkip("fixture not readable at \(url.path)") }
    return try XCTUnwrap(try JSONSerialization.jsonObject(with: data) as? [String: Any])
  }

  private static func data(_ o: Any) throws -> Data { try JSONSerialization.data(withJSONObject: o, options: [.sortedKeys]) }
  private static func num(_ v: Any?) -> Double? { LocalRawStore.num(v) }

  private static func diff(_ a: Any?, _ b: Any?, _ path: String = "") -> String? {
    let c = SeriesLocal.compare(local: ["x": a ?? NSNull()], server: ["x": b ?? NSNull()])
    return c.ok ? nil : "\(path): \(c.fieldsOver)"
  }

  /// The fixture's parts as the phone keeps them.
  private static func load(_ p: [String: Any], into db: AppDatabase) throws {
    try db.writer.write { db in
      for r in p["scores"] as? [[String: Any]] ?? [] {
        var payload: [String: Any] = ["sleep_actual_min": r["sleep_actual_min"] ?? NSNull(), "strain": r["strain"] ?? NSNull()]
        if let z = r["z"], !(z is NSNull) { payload["details"] = ["load": ["zone_minutes": z]] }
        try db.execute(sql: "INSERT INTO daily_scores (date, strain, payload, origin, computed_at) VALUES (?, ?, ?, 'server', 0)",
                       arguments: [r["date"] as? String, num(r["strain"]), try data(payload)])
      }
      for (metric, values) in p["metrics"] as? [String: [String: Any]] ?? [:] {
        for (d, v) in values {
          try db.execute(sql: "INSERT INTO daily_metrics (date, metric, value, source, updated_at) VALUES (?, ?, ?, 'app', 0)",
                         arguments: [d, metric, num(v)])
        }
      }
      for (i, s) in (p["sleep"] as? [[String: Any]] ?? []).enumerated() {
        var payload: [String: Any] = [:]
        for k in ["asleep_min", "deep_min", "core_min", "rem_min", "awake_min", "unspecified_min", "device"] { payload[k] = s[k] ?? NSNull() }
        try db.execute(sql: """
          INSERT INTO sleep_sessions (id, date, start, end, is_main, source, payload, updated_at) VALUES (?, ?, ?, ?, ?, 'app', ?, 0)
          """, arguments: ["s\(i)", s["wake_date"] as? String, (num(s["start_at"]) ?? 0) / 1000, (num(s["end_at"]) ?? 0) / 1000,
                           s["is_main"] as? Bool ?? true, try data(payload)])
      }
      for (i, w) in (p["workouts"] as? [[String: Any]] ?? []).enumerated() {
        try db.execute(sql: """
          INSERT INTO workouts (key, source, external_id, local_date, type, start, end, duration_min, computed_by, row, updated_at)
          VALUES (?, 'app', ?, ?, ?, ?, ?, ?, 'phone', ?, 0)
          """, arguments: ["app:w\(i)", "w\(i)", w["local_date"] as? String, w["type"] as? String, Double(i), Double(i) + 60,
                           num(w["duration_min"]), try data(["hr_zone_min": w["hr_zone_min"] ?? NSNull()])])
      }
      var ext: [String: [String: [Any]]] = [:]
      for s in p["sparky_sessions"] as? [[String: Any]] ?? [] {
        ext[String((s["date"] as? String ?? "").prefix(10)), default: ["strength_sets": [], "weights": []]]["strength_sets"]!.append(s)
      }
      for w in p["sparky_weights"] as? [[String: Any]] ?? [] {
        ext[w["date"] as? String ?? "", default: ["strength_sets": [], "weights": []]]["weights"]!.append(w)
      }
      for (d, body) in ext {
        var doc: [String: Any] = body
        doc["date"] = d
        try DocRecord(key: DocPuller.extRecordsKey(d), version: 0, body: try data(doc), fetchedAt: 0).insert(db)
      }
      for (i, d) in (p["journal"] as? [String] ?? []).enumerated() {
        try db.execute(sql: "INSERT INTO records (kind, id, date, payload, updated_at) VALUES ('journal', ?, ?, ?, 0)",
                       arguments: ["j\(i)", d, try data(["key": "training_legs", "value": 1])])
      }
      let profile: [String: Any] = ["birth_date": p["birth"] ?? NSNull(), "sex": p["sex"] ?? NSNull(),
                                    "height_cm": p["height"] ?? NSNull(), "goal_weight_kg": p["goal_kg"] ?? NSNull()]
      try DocRecord(key: SeriesLocal.profileDoc, version: 0, body: try data(profile), fetchedAt: 0).insert(db)
    }
  }

  /// Contract acceptance: body age from the local tables through HealthspanStage's loader and the bundle equals the
  /// server's rows (body age 0.1 year, pace 0.01: here every number within the shared 1e-9 relative tolerance).
  func testBodyAgeThroughTheLocalTablesGivesTheServersRows() throws {
    let f = try Self.golden("coach_local")
    let parts = try XCTUnwrap(f["parts"] as? [String: Any]), today = try XCTUnwrap(f["today"] as? String)
    let want = try XCTUnwrap(f["rows"] as? [[String: Any]])
    let zone = try XCTUnwrap(TimeZone(identifier: try XCTUnwrap(parts["tz"] as? String)))
    let rt = try OpenBodyScoreRuntime.builtin()
    XCTAssertNotNil(rt.algoVersions["coach"], "the built-in bundle carries M4")
    // the bundle on the fixture's own parts
    let direct = try XCTUnwrap(try rt.invoke("healthspanLocal", [parts, today]) as? [[String: Any]])
    XCTAssertEqual(direct.count, want.count)
    // the same through the local tables
    let db = try AppDatabase.inMemory()
    try Self.load(parts, into: db)
    let loaded = try XCTUnwrap(try db.writer.read { try HealthspanStage.parts($0, today: today, zone: zone) })
    XCTAssertEqual(loaded["origin"] as? String, parts["origin"] as? String)
    let written = try HealthspanStage.compute(db: db, runtime: rt, today: today, zone: zone)
    XCTAssertEqual(written.count, want.count)
    let rows = try XCTUnwrap(try rt.invoke("healthspanLocal", [loaded, today]) as? [[String: Any]])
    XCTAssertEqual(rows.count, want.count)
    for (i, (g, w)) in zip(rows, want).enumerated() {
      var g = g
      g.removeValue(forKey: "algo_version")
      g.removeValue(forKey: "bundle_version")
      XCTAssertNil(Self.diff(g, w, "week \(i) \(w["week_start"] ?? "")"))
    }
    try db.writer.read { db in
      XCTAssertEqual(try Int.fetchOne(db, sql: "SELECT count(*) FROM healthspan_weekly"), want.count)
      XCTAssertEqual(try Int.fetchOne(db, sql: "SELECT count(*) FROM derived_rows WHERE kind = 'healthspan' AND uploaded = 0"), want.count)
      let ages = try HealthspanStage.bodyAges(db, from: "2000-01-01", to: today)
      XCTAssertEqual(Self.num(ages.last?["body_age"]), Self.num(want.last?["body_age"]))
    }
  }

  /// The stage computes in shadow, uploads every week once (newest first) and computes again only after an hour.
  func testStageComputesInShadowAndUploadsOnce() async throws {
    let f = try Self.golden("coach_local")
    let parts = try XCTUnwrap(f["parts"] as? [String: Any]), today = try XCTUnwrap(f["today"] as? String)
    let tz = try XCTUnwrap(parts["tz"] as? String), zone = try XCTUnwrap(TimeZone(identifier: tz))
    let db = try AppDatabase.inMemory()
    try Self.load(parts, into: db)
    try db.saveDoc(ScoringSync.contextDoc, json: Data(#"{"tz": "\#(tz)"}"#.utf8))
    let savedGate = HealthspanStage.uploadGate, savedPull = HealthspanStage.pullExt, savedJournal = HealthspanStage.pullJournal
    HealthspanStage.uploadGate = { true }
    HealthspanStage.pullExt = { _, _, _, _ in }
    HealthspanStage.pullJournal = { _, _ in }
    defer {
      HealthspanStage.uploadGate = savedGate
      HealthspanStage.pullExt = savedPull
      HealthspanStage.pullJournal = savedJournal
    }
    let ok = #"{"status": "ok", "state": "shadow", "stored": 0}"#
    StubProtocol.reset([.init(status: 200, body: ok)])
    let state = ComputeState(packages: ["coach": .init(state: .shadow, flag: true, implemented: true, kinds: [:])], primaryFlag: false)
    let now = try XCTUnwrap(LocalDay.parse(today, zone)).addingTimeInterval(12 * 3600)
    let ctx = ComputeContext(db: db, api: api(), state: state, reason: "test", now: now)
    let rep = await HealthspanStage().run(ctx)
    XCTAssertEqual(rep.state, "done", rep.detail ?? "")
    XCTAssertEqual(StubProtocol.requests.map { $0.url!.path }, [Endpoint.derivedUpload])
    let req = StubProtocol.requests[0]
    let body = try XCTUnwrap(JSONSerialization.jsonObject(with: XCTUnwrap(req.httpBodyStream.map(DeltaRemoteTests.read) ?? req.httpBody)) as? [String: Any])
    XCTAssertEqual(body["kind"] as? String, "healthspan")
    let days = body["days"] as? [[String: Any]] ?? []
    let want = try XCTUnwrap(f["rows"] as? [[String: Any]])
    XCTAssertEqual(days.first?["date"] as? String, want.last?["week_start"] as? String, "newest week first")
    XCTAssertEqual(days.count, want.count)
    let row = try XCTUnwrap((days.first?["rows"] as? [[String: Any]])?.first)
    XCTAssertNil(row["week_start"])
    XCTAssertNil(row["notes"])
    XCTAssertNotNil(row["contributors"] as? [Any])
    // within the hour: nothing computed, nothing sent
    StubProtocol.reset([])
    let again = await HealthspanStage().run(ComputeContext(db: db, api: api(), state: state, reason: "test", now: now.addingTimeInterval(600)))
    XCTAssertEqual(again.written, [])
    XCTAssertEqual(StubProtocol.requests.count, 0)
  }

  /// The journal's strength days come down with the pull's kind filter on the stage's own cursor (whole-day replace).
  func testJournalStrengthDaysArePulledWithAKindFilter() async throws {
    let db = try AppDatabase.inMemory()
    let page = { (cursor: String, items: String) in """
      {"changes": [
        {"stream": "records", "op": "upsert", "kind": "journal", "key": "2026-10-18", "date": "2026-10-18", "version": 1,
         "body": {"items": [\(items)]}},
        {"stream": "records", "op": "upsert", "kind": "journal", "key": "2026-10-19", "date": "2026-10-19", "version": 1,
         "body": {"items": [{"key": "water", "value": 3}]}}],
       "cursors": {"records": "\(cursor)"}, "more": false}
      """ }
    StubProtocol.reset([.init(status: 200, body: page("c.5.0~journal", #"{"key": "training_legs", "value": 1}"#))])
    await HealthspanStage.pullJournalDays(db: db, api: api())
    let r = StubProtocol.requests[0]
    let sent = try XCTUnwrap(JSONSerialization.jsonObject(with: XCTUnwrap(r.httpBodyStream.map(DeltaRemoteTests.read) ?? r.httpBody)) as? [String: Any])
    XCTAssertEqual(sent["kinds"] as? [String: [String]], ["records": ["journal"]])
    XCTAssertEqual(try db.meta(HealthspanStage.journalCursorKey), "c.5.0~journal")
    XCTAssertEqual(try db.doc(HealthspanStage.journalDoc)?.json?["days"] as? [String: Bool], ["2026-10-18": true])
    // the day's strength entry removed on the server: the day drops out
    StubProtocol.reset([.init(status: 200, body: page("c.6.0~journal", ""))])
    await HealthspanStage.pullJournalDays(db: db, api: api())
    XCTAssertEqual(try db.doc(HealthspanStage.journalDoc)?.json?["days"] as? [String: Bool], [:])
  }

  /// The Today thresholds of TodayParams.swift are the bundle's `coach.TODAY_PARAMS` (web/params.py), and a strain coach
  /// call gives the server's answer.
  func testTodayParamsAndStrainCoachMatchTheBundle() throws {
    let f = try Self.golden("coach_local")
    let p = try XCTUnwrap(f["today_params"] as? [String: Any])
    XCTAssertEqual(p["TRENDS_DAYS"] as? Int, TodayParams.trendsDays)
    XCTAssertEqual(p["BASELINE_MIN_N"] as? Int, TodayParams.baselineMinN)
    XCTAssertEqual(p["SPARK_DAYS"] as? Int, TodayParams.sparkDays)
    XCTAssertEqual(Self.num(p["FLAT_Z"]), TodayParams.flatZ)
    XCTAssertEqual(Self.num(p["FLAT_RELATIVE"]), TodayParams.flatRelative)
    let watch = try XCTUnwrap(p["WATCH"] as? [[String: Any]])
    XCTAssertEqual(watch.count, TodayParams.watch.count)
    for (w, s) in zip(watch, TodayParams.watch) {
      XCTAssertEqual(w["key"] as? String, s.key)
      XCTAssertEqual((w["bad"] as? NSNumber)?.intValue, s.bad)
      XCTAssertEqual(Self.num(w["z"]), s.z)
      XCTAssertEqual(Self.num(w["min_delta"]), s.minDelta)
      XCTAssertEqual(Self.num(w["absolute"]), s.absolute)
    }
    let tones = try XCTUnwrap(p["STATUS_MIN_TONE"] as? [String: String])
    XCTAssertEqual(Set(tones.keys), Set(TodayParams.statusMinTone.keys))
    for (k, t) in tones { XCTAssertEqual(TodayParams.statusMinTone[k], t == "bad" ? .bad : .mid, k) }
    XCTAssertEqual(Self.num(p["SEVERITY_CAP"]), TodayParams.severityCap)
    XCTAssertEqual(Self.num(p["AT_THRESHOLD"]), TodayParams.atThreshold)
    XCTAssertEqual(Self.num(p["STATUS_BAD"]), TodayParams.statusBad)
    XCTAssertEqual(Self.num(p["STATUS_MID"]), TodayParams.statusMid)
    XCTAssertEqual(Self.num(p["MONITOR_OFF"]), TodayParams.monitorOff)
    XCTAssertEqual(p["MAX_ITEMS"] as? Int, TodayParams.maxItems)
    XCTAssertEqual(Self.num(p["STRESS_MEDIUM"]), TodayParams.stressMedium)
    XCTAssertEqual(Self.num(p["STRESS_HIGH"]), TodayParams.stressHigh)
    // the built-in bundle carries the same block
    let rt = try OpenBodyScoreRuntime.builtin()
    let s = try XCTUnwrap(f["strain"] as? [String: Any])
    var out = try XCTUnwrap(try rt.invoke("strainCoach", [s["today"] ?? NSNull(), s["yday"] ?? NSNull(), s["d"] ?? NSNull()]) as? [String: Any])
    XCTAssertEqual(out.removeValue(forKey: "algo_version") as? String, rt.algoVersions["coach"])
    out.removeValue(forKey: "bundle_version")
    XCTAssertNil(Self.diff(out, f["strain_out"], "strain"))
  }

  /// The coach document and the sleep detail compute from the local tables; in shadow they are compared with the
  /// server's documents (names only), in primary Today and the sleep page read them.
  func testCoachDocumentsComputeFromTheLocalTables() throws {
    let f = try Self.golden("coach_local")
    let parts = try XCTUnwrap(f["parts"] as? [String: Any]), today = try XCTUnwrap(f["today"] as? String)
    let tz = try XCTUnwrap(parts["tz"] as? String)
    let db = try AppDatabase.inMemory()
    try Self.load(parts, into: db)
    let rt = try OpenBodyScoreRuntime.builtin()
    let doc = try XCTUnwrap(try CoachLocal.coach(today, db: db, runtime: rt, tz: tz, today: today))
    for k in ["strain_coach", "health_monitor", "sleep_coach", "scores", "sleep"] { XCTAssertNotNil(doc[k] as? [String: Any], k) }
    XCTAssertEqual(doc["algo_version"] as? String, rt.algoVersions["coach"])
    let sleep = try XCTUnwrap(doc["sleep"] as? [String: Any])
    XCTAssertNotNil(sleep["main"] as? [String: Any], "the latest night of the fixture")
    XCTAssertEqual((sleep["recent"] as? [Any])?.count, 7)
    let night = try XCTUnwrap(try CoachLocal.night(today, db: db, runtime: rt, tz: tz, today: today))
    XCTAssertEqual(night["date"] as? String, today)
    XCTAssertNotNil(night["baseline"] as? [String: Any])
    // shadow: the server's strain coach equal to the phone's is `ok`; one with another target is caught by name
    let server = try XCTUnwrap(doc["strain_coach"] as? [String: Any])
    try db.saveDoc(TodayDocs.coach(today), json: try Self.data(server))
    CoachLocal.compareCoach(today, doc, db: db)
    var meta = try XCTUnwrap(try db.meta(CoachLocal.compareMeta)).data(using: .utf8).flatMap { try? JSONSerialization.jsonObject(with: $0) } as? [String: Any]
    XCTAssertEqual((meta?["strain_coach"] as? [String: Any])?["ok"] as? Bool, true)
    var other = server
    other["target_low"] = 3.0
    try db.saveDoc(TodayDocs.coach(today), json: try Self.data(other))
    CoachLocal.compareCoach(today, doc, db: db)
    meta = try XCTUnwrap(try db.meta(CoachLocal.compareMeta)).data(using: .utf8).flatMap { try? JSONSerialization.jsonObject(with: $0) } as? [String: Any]
    XCTAssertEqual((meta?["strain_coach"] as? [String: Any])?["fields_over"] as? [String], ["target_low"])
    // which document a page draws: the server's in shadow, the phone's in primary
    let shadow = #"{"compute": {"packages": {"coach": {"state": "shadow", "kinds": {}}}}}"#
    try db.saveDoc(LocalCompute.stateDoc, json: Data(shadow.utf8))
    try db.writer.read { db in XCTAssertNil(try CoachLocal.shownCoach(db, day: today)) }
    let primary = #"{"compute": {"packages": {"coach": {"state": "primary", "kinds": {}}}}}"#
    try db.saveDoc(LocalCompute.stateDoc, json: Data(primary.utf8))
    try db.writer.read { db in
      XCTAssertEqual(try CoachLocal.shownCoach(db, day: today)?.key, CoachLocal.coachKey(today))
      XCTAssertEqual(try CoachLocal.shownNight(db, serverKey: TodayDocs.sleepNight(today), day: today)?.key, CoachLocal.nightKey(today))
    }
  }

  /// In primary the Trends body-age series reads the phone's weeks; in shadow it stays the server's.
  func testBodyAgeSeriesFollowsTheMode() throws {
    let db = try AppDatabase.inMemory()
    try db.writer.write { db in
      try db.execute(sql: """
        INSERT INTO healthspan_weekly (week_start, as_of, chrono_age, body_age, contributors, insufficient, algo_version,
                                       bundle_version, computed_at) VALUES ('2026-10-12', '2026-10-18', 32.4, 30.1, '[]', '[]', 'a', 'b', 0)
        """)
    }
    let shadow = #"{"compute": {"packages": {"coach": {"state": "shadow", "kinds": {}}}}}"#
    try db.saveDoc(LocalCompute.stateDoc, json: Data(shadow.utf8))
    var (data, only) = try db.writer.read { try SeriesLocal.trendsData($0, end: "2026-10-20", days: 30) }
    XCTAssertTrue(only.series.contains("body_age"))
    XCTAssertNil(data["body_age"])
    let primary = #"{"compute": {"packages": {"coach": {"state": "primary", "kinds": {}}}}}"#
    try db.saveDoc(LocalCompute.stateDoc, json: Data(primary.utf8))
    (data, only) = try db.writer.read { try SeriesLocal.trendsData($0, end: "2026-10-20", days: 30) }
    XCTAssertFalse(only.series.contains("body_age"))
    XCTAssertEqual(Self.num((data["body_age"] as? [[String: Any]])?.first?["body_age"]), 30.1)
  }
}
