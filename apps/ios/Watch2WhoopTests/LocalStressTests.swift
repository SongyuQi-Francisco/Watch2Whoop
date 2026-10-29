import GRDB
import XCTest
@testable import Watch2Whoop

/// 2.0.0 M2 (docs/ios-native-2.0.md section 4): stress, the stress cards and the cardio view on the phone.
/// - stress_local.json (written by body_engine/tests/test_js_golden_stress_cards.py): 74 synthetic days of samples (made-up
///   values) and the server's insights/stress.days_local rows of the last 60; test_native_stress.py checks that the
///   server's own loader gives the same rows from the same uploads. Here the samples go through the local tables
///   (hk_samples with sources, hist_samples, sleep_sessions, hk_workouts, daily_scores, daily_metrics) and StressStage.
/// - series_local.json (M3's fixture): with package `stress` primary, the Trends document's resilience / body battery /
///   heart-rate recovery come from the phone and equal the server's.
final class LocalStressTests: XCTestCase {
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
  private static func num(_ v: Any?) -> Double { LocalRawStore.num(v) ?? .nan }

  /// The fixture's samples as the phone keeps them.
  private static func load(_ src: [String: Any], into db: AppDatabase) throws {
    let types = ["hr": StressStage.heartRate, "hrv": StressStage.hrvType, "steps": StressStage.stepType]
    let kinds: [String: LocalHistoryParams.Kind] = ["hr": .hr, "hrv": .hrv, "steps": .steps]
    try db.writer.write { db in
      try db.execute(sql: "INSERT INTO hk_sources (id, key, name, device_type) VALUES (1, 'w', 'Apple Watch', 'watch'), (2, 'p', 'iPhone', 'phone')")
      var n = 0
      for (g, list) in src["app"] as? [String: [[Any]]] ?? [:] {
        for x in list {
          n += 1
          let t = num(x[0]), source = (x.count > 2 ? x[2] as? String : nil) == "iPhone" ? 2 : 1
          try db.execute(sql: "INSERT INTO hk_samples (uuid, type, start, end, value, source_id) VALUES (?, ?, ?, ?, ?, ?)",
                         arguments: ["s\(n)", types[g], t, g == "steps" ? t + 60 : t, num(x[1]), source])
        }
      }
      for (g, list) in src["other"] as? [String: [[Any]]] ?? [:] {
        for x in list {
          try db.execute(sql: "INSERT INTO hist_samples (kind, t, value) VALUES (?, ?, ?)",
                         arguments: [kinds[g]!.rawValue, num(x[0]), num(x[1])])
        }
      }
      for (i, s) in (src["sleep"] as? [[Any]] ?? []).enumerated() {
        try db.execute(sql: "INSERT INTO sleep_sessions (id, date, start, end, is_main, source, updated_at) VALUES (?, ?, ?, ?, 1, 'app', 0)",
                       arguments: ["n\(i)", "2026-01-01", num(s[0]), num(s[1])])
      }
      for (i, w) in (src["workouts"] as? [[Any]] ?? []).enumerated() {
        try db.execute(sql: "INSERT INTO hk_workouts (uuid, start, end) VALUES (?, ?, ?)", arguments: ["w\(i)", num(w[0]), num(w[1])])
      }
      for (d, v) in src["hrv_night"] as? [String: Any] ?? [:] {
        try db.execute(sql: "INSERT INTO daily_scores (date, payload, origin, computed_at) VALUES (?, ?, 'server', 0)",
                       arguments: [d, try data(["hrv_night_ln": v])])
      }
      for d in src["shortcut"] as? [String] ?? [] {
        try db.execute(sql: "INSERT INTO daily_metrics (date, metric, value, source, updated_at) VALUES (?, 'step_count', 1000, 'shortcut', 0)",
                       arguments: [d])
      }
    }
  }

  private static func days(_ a: String, _ b: String) -> [String] {
    var out: [String] = [], d = a
    while d <= b { out.append(d); d = LocalDay.add(d, 1) }
    return out
  }

  private static func diff(_ a: Any?, _ b: Any?, _ path: String = "") -> String? {
    let c = SeriesLocal.compare(local: ["x": a ?? NSNull()], server: ["x": b ?? NSNull()])
    return c.ok ? nil : "\(path): \(c.fieldsOver)"
  }

  /// Contract acceptance: 60 synthetic days through the local tables and the bundle, a chunk at a time, give the
  /// server's stress rows.
  func testSixtyDaysThroughTheLocalTablesGiveTheServersRows() throws {
    let f = try Self.golden("stress_local")
    let src = try XCTUnwrap(f["src"] as? [String: Any]), want = try XCTUnwrap(f["rows"] as? [[String: Any]])
    let zone = try XCTUnwrap(TimeZone(identifier: try XCTUnwrap(src["tz"] as? String)))
    let db = try AppDatabase.inMemory()
    try Self.load(src, into: db)
    let store = LocalRawStore(db: db, zone: zone)
    let rt = try OpenBodyScoreRuntime.builtin()
    XCTAssertNotNil(rt.algoVersions["stress_cards"], "the built-in bundle carries M2")
    let all = Self.days(try XCTUnwrap(src["start"] as? String), try XCTUnwrap(src["end"] as? String))
    XCTAssertEqual(all.count, 60)
    for chunk in StressStage.chunks(all) {
      _ = try StressStage.computeChunk(db: db, store: store, runtime: rt, days: chunk)
    }
    let got: [String: [String: Any]] = try db.writer.read { db in
      var out: [String: [String: Any]] = [:]
      for r in try Row.fetchAll(db, sql: "SELECT date, rows FROM derived_rows WHERE kind = 'stress'") {
        out[r["date"] as String] = ((try JSONSerialization.jsonObject(with: r["rows"] as Data)) as? [[String: Any]])?.first
      }
      return out
    }
    XCTAssertEqual(got.count, 60)
    for w in want {
      let d = try XCTUnwrap(w["date"] as? String)
      XCTAssertNil(Self.diff(got[d], w.filter { $0.key != "date" }, d))
    }
    try db.writer.read { db in
      XCTAssertEqual(try Int.fetchOne(db, sql: "SELECT count(*) FROM stress_daily"), 60)
      let rows = try StressStage.rows(db, from: all[0], to: all[59])
      XCTAssertEqual(rows.count, 60)
      XCTAssertEqual(LocalRawStore.num(rows[59]["score"]), LocalRawStore.num(want[59]["score"]))
      XCTAssertGreaterThan(try Int.fetchOne(db, sql: "SELECT min(n_hr) FROM stress_daily WHERE score IS NOT NULL") ?? 0, 50)
    }
  }

  /// The stage end to end in shadow: every day on the first run, the rows uploaded once (newest first), then only the
  /// recent days; a heart-rate sample added to an old day brings it and the 14 days after it back.
  func testStageComputesInShadowAndUploadsOnce() async throws {
    let f = try Self.golden("stress_local")
    let src = try XCTUnwrap(f["src"] as? [String: Any])
    let tz = try XCTUnwrap(src["tz"] as? String), zone = try XCTUnwrap(TimeZone(identifier: tz))
    let end = try XCTUnwrap(src["end"] as? String)
    let db = try AppDatabase.inMemory()
    try Self.load(src, into: db)
    try db.saveDoc(ScoringSync.contextDoc, json: Data(#"{"tz": "\#(tz)"}"#.utf8))
    let saved = StressStage.uploadGate
    StressStage.uploadGate = { true }
    defer { StressStage.uploadGate = saved }
    let ok = #"{"status": "ok", "state": "shadow", "stored": 0}"#
    StubProtocol.reset([.init(status: 200, body: ok)])
    let state = ComputeState(packages: ["stress": .init(state: .shadow, flag: true, implemented: true, kinds: [:])], primaryFlag: false)
    let now = try XCTUnwrap(LocalDay.parse(end, zone))
    let ctx = ComputeContext(db: db, api: api(), state: state, reason: "test", now: now)
    let rep = await StressStage().run(ctx)
    XCTAssertEqual(rep.state, "done", rep.detail ?? "")
    XCTAssertEqual(StubProtocol.requests.map { $0.url!.path }, [Endpoint.derivedUpload])
    let req = StubProtocol.requests[0]
    let body = try XCTUnwrap(JSONSerialization.jsonObject(with: XCTUnwrap(req.httpBodyStream.map(DeltaRemoteTests.read) ?? req.httpBody)) as? [String: Any])
    XCTAssertEqual(body["kind"] as? String, "stress")
    let rt = try OpenBodyScoreRuntime.builtin()
    XCTAssertEqual(body["algo_version"] as? String, rt.algoVersions["stress_cards"])
    let sent = (body["days"] as? [[String: Any]])?.compactMap { $0["date"] as? String } ?? []
    XCTAssertEqual(sent.first, end, "newest first")
    XCTAssertEqual(sent.count, 74, "every day from the first heart-rate sample")
    XCTAssertNotNil(try db.doc(StressStage.cardioDoc(end))?.json?["recovery_index"], "the cardio view")
    // nothing new: only the recent days are computed again, unchanged, nothing sent
    StubProtocol.reset([])
    let again = await StressStage().run(ctx)
    XCTAssertEqual(again.written, [])
    XCTAssertEqual(StubProtocol.requests.count, 0)
    // a heart-rate sample on an old day (the history downloaded later): that day and the 14 after it
    let old = LocalDay.add(end, -50)
    let store = LocalRawStore(db: db, zone: zone)
    try db.writer.write { db in
      try db.execute(sql: "INSERT INTO hist_samples (kind, t, value) VALUES (1, ?, 70)", arguments: [store.dayStart(old)! + 3600 * 11 + 7])
      let days = try StressStage.daysToCompute(db, store: store, today: end, algo: rt.algoVersions["stress_cards"]!)
      XCTAssertEqual(days.first, old)
      XCTAssertTrue(days.contains(LocalDay.add(old, 14)) && !days.contains(LocalDay.add(old, 15)))
      XCTAssertEqual(days.filter { $0 >= LocalDay.add(end, -15) }.count, StressStage.recentDays)
    }
  }

  /// Chunks are runs of consecutive days, at most `chunkDays` long.
  func testChunks() {
    let days = Self.days("2026-01-01", "2026-02-15") + ["2026-03-01", "2026-03-02"]
    let c = StressStage.chunks(days)
    XCTAssertEqual(c.map(\.count), [30, 16, 2])
    XCTAssertEqual(c[2], ["2026-03-01", "2026-03-02"])
  }

  /// With package `stress` primary, the Trends document's stress cards are the phone's and equal the server's (M3's
  /// fixture: no stress rows on either side, Apple's heart-rate recovery); off, they stay the server's.
  func testCardsAreThePhonesInPrimary() throws {
    let f = try Self.golden("series_local")
    let db = try AppDatabase.inMemory()
    let t = try XCTUnwrap(f["tables"] as? [String: Any])
    try db.writer.write { db in
      for r in t["daily_scores"] as? [[String: Any]] ?? [] {
        try db.execute(sql: """
          INSERT INTO daily_scores (date, recovery, sleep_score, strain, payload, origin, computed_at) VALUES (?, ?, ?, ?, ?, 'server', 0)
          """, arguments: [r["date"] as? String, TodayModel.num(r["recovery"]), TodayModel.num(r["sleep_score"]),
                           TodayModel.num(r["strain"]), try Self.data(r["payload"] ?? [String: Any]())])
      }
      for r in t["daily_metrics"] as? [[String: Any]] ?? [] {
        try db.execute(sql: "INSERT INTO daily_metrics (date, metric, value, unit, source, updated_at) VALUES (?, ?, ?, NULL, 'server', 0)",
                       arguments: [r["date"] as? String, r["metric"] as? String, TodayModel.num(r["value"])])
      }
    }
    let tz = try XCTUnwrap(f["tz"] as? String), end = try XCTUnwrap(f["end"] as? String)
    let zone = try XCTUnwrap(TimeZone(identifier: tz))
    let want = try XCTUnwrap(((f["expected"] as? [String: Any])?["trends"] as? [String: Any])?["status"] as? [String: Any])
    let rt = try OpenBodyScoreRuntime.builtin()
    let hour = f["now_hour"] as? Int ?? 14
    let now = try XCTUnwrap(LocalDay.parse(end, zone)).addingTimeInterval(Double(hour - 12) * 3600)
    let cards = try TrendsLocal.stressCards(db, runtime: rt, end: end, tz: tz, now: now)
    XCTAssertEqual(cards["algo_version"] as? String, rt.algoVersions["stress_cards"])
    for k in TrendsLocal.stressKeys {
      XCTAssertNotNil(want[k] as? [String: Any], k)
      XCTAssertNil(Self.diff(cards[k], want[k], k))
    }
    // the shadow comparison of the three cards: equal
    XCTAssertTrue(TrendsLocal.compareStress(local: cards, server: want, today: true).ok)
    var other = want
    other["resilience"] = ["level": "limited"]
    XCTAssertEqual(TrendsLocal.compareStress(local: cards, server: other, today: true).fieldsOver.first?.hasPrefix("resilience"), true)
    // the Trends document: off = the server's cards, primary = the phone's
    let ctx = SeriesLocal.Context(tz: tz, today: end, nowHour: hour)
    let days = f["days"] as? Int ?? 30
    var doc = try XCTUnwrap(try SeriesLocal.compute(.trends(days: days, end: nil), db: db, runtime: rt, ctx: ctx, server: nil))
    XCTAssertTrue((doc["server_only_paths"] as? [String] ?? []).contains("status.resilience"))
    try db.saveDoc(LocalCompute.stateDoc, json: Data(#"{"compute": {"packages": {"stress": {"state": "primary", "kinds": {}}}}}"#.utf8))
    doc = try XCTUnwrap(try SeriesLocal.compute(.trends(days: days, end: nil), db: db, runtime: rt, ctx: ctx, server: nil))
    let only = doc["server_only_paths"] as? [String] ?? []
    XCTAssertFalse(only.contains("status.resilience") || only.contains("status.hr_recovery") || only.contains("series.stress"))
    let status = try XCTUnwrap(doc["status"] as? [String: Any])
    for k in TrendsLocal.stressKeys { XCTAssertNil(Self.diff(status[k], want[k], "trends/\(k)")) }
  }
}
