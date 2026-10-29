import GRDB
import XCTest
@testable import Watch2Whoop

/// 2.0.0 M1 (docs/ios-native-2.0.md section 4): the phone's derivation of daily values and sleep. The fixture is the
/// golden file's `new_york` case (packages/openbodyscore-js/test/golden/derive.json: 30 synthetic days over a DST change,
/// made-up values; its `want` is the server's rawdata/derive.derive_days, which body_engine/tests/test_native_derive.py
/// checks equals the server's own derive_day + resolve_daily on the same samples). Here the same HealthKit rows go
/// through the local tables (ingest with their sources, 15-minute buckets, pulled sources) and DeriveStage in chunks.
final class LocalDeriveTests: XCTestCase {
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
    var today: String
  }

  private static func fixture() throws -> Fixture {
    let url = try XCTUnwrap(Bundle(for: LocalDeriveTests.self).url(forResource: "derive", withExtension: "json"),
                            "derive.json is a resource of the test bundle (project.yml)")
    let g = try XCTUnwrap(JSONSerialization.jsonObject(with: Data(contentsOf: url)) as? [String: Any])
    let c = try XCTUnwrap((g["cases"] as? [[String: Any]])?.first { $0["src"] != nil })
    let src = try XCTUnwrap(c["src"] as? [String: Any])
    return Fixture(src: src, want: try XCTUnwrap(c["want"] as? [String: Any]),
                   zone: try XCTUnwrap(TimeZone(identifier: try XCTUnwrap(src["tz"] as? String))),
                   days: try XCTUnwrap(src["days"] as? [String]), today: try XCTUnwrap(src["today"] as? String))
  }

  private static func table(_ x: Any?) -> [[String: Any]] {
    guard let t = x as? [String: Any], let cols = t["cols"] as? [String], let rows = t["rows"] as? [[Any]] else { return [] }
    return rows.map { r in Dictionary(uniqueKeysWithValues: zip(cols, r).map { ($0, $1) }) }
  }

  /// The fixture's rows as the phone keeps them: samples (with sources) through `ingest`, buckets per type, the server's
  /// other sources as pulled.
  private static func load(_ f: Fixture, into db: AppDatabase) throws -> LocalRawStore {
    let store = LocalRawStore(db: db, zone: f.zone)
    let samples: [LocalRawStore.Sample] = table(f.src["samples"]).map { r in
      let sid = (r["source_id"] as? NSNumber)?.intValue ?? 0
      return LocalRawStore.Sample(uuid: r["uuid"] as! String, type: r["type"] as! String, start: LocalRawStore.num(r["start"])!,
                                  end: LocalRawStore.num(r["end"])!, value: LocalRawStore.num(r["value"])!,
                                  source: .init(name: r["source"] as? String ?? "", bundle: "org.example.\(sid)",
                                                deviceType: r["device_type"] as? String ?? "other",
                                                deviceName: r["device_name"] as? String ?? ""))
    }
    try store.ingest(samples)
    var byType: [String: [(Double, Double)]] = [:]
    for b in table(f.src["buckets"]) {
      byType[b["type"] as! String, default: []].append((LocalRawStore.num(b["start"])!, LocalRawStore.num(b["value"])!))
    }
    for (type, values) in byType {
      let lo = values.map(\.0).min()!, hi = values.map(\.0).max()! + LocalRawStore.bucketSeconds
      try store.ingestBuckets(type: type, from: lo, to: hi, values: values)
    }
    try db.writer.write { db in
      for r in (f.src["other"] as? [[String: Any]]) ?? [] {
        try db.execute(sql: """
          INSERT INTO daily_metric_sources (date, metric, source, origin, value, unit, agg, n, min, max, flags, updated_at)
          VALUES (?, ?, ?, 'server', ?, ?, ?, ?, ?, ?, ?, 1)
          """, arguments: [r["date"] as? String, r["metric"] as? String, r["source"] as? String, LocalRawStore.num(r["value"]),
                           r["unit"] as? String, r["agg"] as? String, (r["n"] as? NSNumber)?.intValue,
                           LocalRawStore.num(r["min_value"]), LocalRawStore.num(r["max_value"]),
                           try JSONSerialization.data(withJSONObject: r["flags"] as? [String: Any] ?? [:])])
      }
    }
    return store
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

  /// Synchronous database access from the async tests.
  private static func read(_ db: AppDatabase, _ f: (Database) throws -> Void) throws { try db.writer.read(f) }
  private static func write(_ db: AppDatabase, _ f: (Database) throws -> Void) throws { try db.writer.write(f) }

  private static func derivedRows(_ db: AppDatabase, kind: String) throws -> [String: Any] {
    try db.writer.read { db in
      var out: [String: Any] = [:]
      for r in try Row.fetchAll(db, sql: "SELECT date, rows FROM derived_rows WHERE kind = ?", arguments: [kind]) {
        out[r["date"] as String] = try JSONSerialization.jsonObject(with: r["rows"] as Data)
      }
      return out
    }
  }

  /// 30 synthetic days through the local tables and the bundle, a week at a time, give the server's rows.
  func testThirtyDaysThroughTheLocalTablesGiveTheServersRows() throws {
    let f = try Self.fixture()
    let db = try AppDatabase.inMemory()
    let store = try Self.load(f, into: db)
    let rt = try OpenBodyScoreRuntime.builtin()
    XCTAssertEqual(rt.algoVersions["derive"], f.want["algo_version"] as? String)
    for start in stride(from: 0, to: f.days.count, by: DeriveStage.chunkDays) {
      let chunk = Array(f.days[start..<min(f.days.count, start + DeriveStage.chunkDays)])
      _ = try DeriveStage.deriveChunk(db: db, store: store, runtime: rt, days: chunk, today: f.today, primary: true)
    }
    let daily = try Self.derivedRows(db, kind: "daily"), sleep = try Self.derivedRows(db, kind: "sleep")
    let wantDaily = try XCTUnwrap(f.want["daily"] as? [String: Any]), wantSleep = try XCTUnwrap(f.want["sleep"] as? [String: Any])
    XCTAssertEqual(daily.count, 30)
    for d in f.days {
      let w = try XCTUnwrap(wantDaily[d] as? [String: Any])
      let rows = (daily[d] as? [[String: Any]]) ?? []
      let strip = { (role: String) in rows.filter { $0["role"] as? String == role }.map { $0.filter { $0.key != "role" } } }
      XCTAssertNil(Self.diff(strip("source"), w["sources"], "\(d)/sources"))
      XCTAssertNil(Self.diff(strip("chosen"), w["chosen"], "\(d)/chosen"))
      XCTAssertNil(Self.diff(sleep[d], wantSleep[d], "\(d)/sleep"))
    }
    XCTAssertGreaterThanOrEqual(wantSleep.values.compactMap { ($0 as? [Any])?.count }.reduce(0, +), 25)
    // primary: the chosen values and the nights are the pages' rows now; the scores are told
    let d = f.days[10]
    let chosen = (try XCTUnwrap(wantDaily[d] as? [String: Any])["chosen"] as? [[String: Any]]) ?? []
    try db.writer.read { db in
      XCTAssertEqual(try Int.fetchOne(db, sql: "SELECT count(*) FROM daily_metrics WHERE date = ?", arguments: [d]), chosen.count)
      let steps = try Double.fetchOne(db, sql: "SELECT value FROM daily_metrics WHERE date = ? AND metric = 'step_count'", arguments: [d])
      XCTAssertEqual(steps, LocalRawStore.num(chosen.first { $0["metric"] as? String == "step_count" }?["value"]))
      XCTAssertGreaterThan(try Int.fetchOne(db, sql: "SELECT count(*) FROM sleep_sessions WHERE source = 'app'") ?? 0, 25)
      XCTAssertEqual(try Int.fetchOne(db, sql: """
        SELECT count(*) FROM (SELECT date FROM sleep_sessions GROUP BY date HAVING sum(is_main) <> 1)
        """), 0)
      XCTAssertGreaterThan(try Int.fetchOne(db, sql: "SELECT count(*) FROM score_dirty WHERE kind = 'scores'") ?? 0, 0)
      XCTAssertGreaterThan(try Int.fetchOne(db, sql: "SELECT count(*) FROM daily_metric_sources WHERE origin = 'phone'") ?? 0, 0)
    }
  }

  /// The stage end to end: shadow leaves the pages' rows alone, uploads the settled rows once, newest first; a second run
  /// with nothing new sends nothing.
  func testStageDerivesInShadowAndUploadsOnce() async throws {
    let f = try Self.fixture()
    let db = try AppDatabase.inMemory()
    let store = try Self.load(f, into: db)
    try Self.write(db) { db in
      try db.execute(sql: "INSERT INTO daily_metrics (date, metric, value, unit, source, updated_at) VALUES (?, 'step_count', 1, 'count', 'ow', 1)",
                     arguments: [f.days[3]])
    }
    try db.saveDoc(ScoringSync.contextDoc, json: Data(#"{"tz": "\#(f.zone.identifier)"}"#.utf8))   // the user's zone
    try db.setMeta("hk.scoring.since", String(try XCTUnwrap(store.dayStart(f.days[0]))))
    let saved = (DeriveStage.readHealth, DeriveStage.uploadGate)
    DeriveStage.readHealth = nil
    DeriveStage.uploadGate = { true }
    defer { (DeriveStage.readHealth, DeriveStage.uploadGate) = saved }
    let ok = #"{"status": "ok", "state": "shadow", "stored": 0}"#
    StubProtocol.reset([.init(status: 200, body: ok), .init(status: 200, body: ok)])
    let state = ComputeState(packages: ["derive": .init(state: .shadow, flag: true, implemented: true, kinds: [:])], primaryFlag: false)
    let now = try XCTUnwrap(LocalDay.parse(f.today, f.zone))
    let ctx = ComputeContext(db: db, api: api(), state: state, reason: "test", now: now)
    let rep = await DeriveStage().run(ctx)
    XCTAssertEqual(rep.state, "done", rep.detail ?? "")
    XCTAssertEqual(StubProtocol.requests.map { $0.url!.path }, [Endpoint.derivedUpload, Endpoint.derivedUpload])
    let body = try XCTUnwrap(JSONSerialization.jsonObject(with: XCTUnwrap(StubProtocol.requests[0].httpBodyStream.map(DeltaRemoteTests.read)
                                                                           ?? StubProtocol.requests[0].httpBody)) as? [String: Any])
    XCTAssertEqual(body["kind"] as? String, "daily")
    XCTAssertEqual(body["algo_version"] as? String, f.want["algo_version"] as? String)
    let sent = (body["days"] as? [[String: Any]])?.compactMap { $0["date"] as? String } ?? []
    XCTAssertEqual(sent.first, f.today, "newest first")
    XCTAssertEqual(sent.count, f.days.count + 1)
    try Self.read(db) { db in
      // shadow: the server's value stays on the page, no app night is shown
      XCTAssertEqual(try Double.fetchOne(db, sql: "SELECT value FROM daily_metrics WHERE date = ? AND metric = 'step_count'",
                                         arguments: [f.days[3]]), 1)
      XCTAssertEqual(try Int.fetchOne(db, sql: "SELECT count(*) FROM sleep_sessions"), 0)
      XCTAssertEqual(try Int.fetchOne(db, sql: "SELECT count(*) FROM derived_rows WHERE uploaded = 0"), 0)
      XCTAssertEqual(try Int.fetchOne(db, sql: "SELECT count(*) FROM score_dirty WHERE kind = 'derive' AND date <= ?",
                                      arguments: [f.today]), 0)
    }
    StubProtocol.reset([])
    let again = await DeriveStage().run(ctx)
    XCTAssertEqual(again.written, [])
    XCTAssertEqual(StubProtocol.requests.count, 0)
    // a pulled source of one day changes: only that day is derived again
    try Self.write(db) { db in
      try db.execute(sql: """
        INSERT OR REPLACE INTO daily_metric_sources (date, metric, source, origin, value, unit, agg, n, updated_at)
        VALUES (?, 'step_count', 'ow', 'server', 99999, 'count', 'sum', 1, ?)
        """, arguments: [f.days[5], Date().timeIntervalSince1970 + 10])
    }
    StubProtocol.reset([.init(status: 200, body: ok)])
    let third = await DeriveStage().run(ctx)
    XCTAssertEqual(third.written, ["daily:\(f.days[5])"])
    XCTAssertEqual(third.uploaded, ["daily:\(f.days[5])"])
  }
}
