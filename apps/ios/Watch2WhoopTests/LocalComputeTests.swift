import GRDB
import XCTest
@testable import Watch2Whoop

/// 2.0.0 P0 seams (docs/ios-native-2.0.md section 2.2): the compute pipeline, the compute state answer, the endpoints and
/// the `ext_records` pull. Fictional data only.
final class LocalComputeTests: XCTestCase {
  private func api() -> APIClient {
    let c = APIClient(baseURL: URL(string: "https://body.example.com")!, token: { "example-token" }, session: StubProtocol.session(),
                      sleep: { _ in })
    c.maxAttempts = 1
    return c
  }

  private static func json(_ o: [String: Any]) -> String {
    String(decoding: try! JSONSerialization.data(withJSONObject: o, options: [.sortedKeys]), as: UTF8.self)
  }

  private static func query(_ i: Int) -> [String: String] {
    let items = URLComponents(url: StubProtocol.requests[i].url!, resolvingAgainstBaseURL: false)?.queryItems ?? []
    return Dictionary(uniqueKeysWithValues: items.map { ($0.name, $0.value ?? "") })
  }

  // MARK: - compute state and pipeline

  func testComputeStateDecodesTheAnswerAndDefaultsToOff() throws {
    let derive: [String: Any] = ["state": "shadow", "flag": true, "implemented": true,
                                 "kinds": ["daily": ["state": "shadow"], "sleep": ["state": "primary"]]]
    let series: [String: Any] = ["state": "primary", "flag": true, "implemented": true, "kinds": [String: Any]()]
    let rules: [String: Any] = ["state": "someday", "flag": true, "implemented": false,
                                "kinds": ["reminders": ["state": "off"]]]
    let compute: [String: Any] = ["primary_flag": false, "packages": ["derive": derive, "series": series, "rules": rules]]
    let answer: [String: Any] = ["tz": "Australia/Sydney", "algo_version": "v1", "compute": compute]
    let s = ComputeState.decode(Data(Self.json(answer).utf8))
    XCTAssertEqual(s.mode("derive"), .shadow)
    XCTAssertEqual(s.packages["derive"]?.kinds, ["daily": .shadow, "sleep": .primary])
    XCTAssertEqual(s.mode("series"), .primary)
    XCTAssertEqual(s.mode("rules"), .off, "an unknown state counts as off")
    XCTAssertEqual(s.mode("coach"), .off, "a package the server did not name is off")
    XCTAssertFalse(s.primaryFlag)
    XCTAssertEqual(ComputeState.decode(Data("not json".utf8)), .off)
    XCTAssertEqual(ComputeState.decode(Data(#"{"tz": "UTC"}"#.utf8)), .off, "a /v1/sync/scoring-only answer: all off")
  }

  func testEndpoints() {
    XCTAssertEqual(Endpoint.compute().path, "/v1/sync/compute")
    XCTAssertEqual(Endpoint.compute(["from": "2026-01-01"]).query, [URLQueryItem(name: "from", value: "2026-01-01")])
    XCTAssertEqual(Endpoint.derivedUpload, "/v1/users/me/derived/app")
    let fp = Endpoint.extRecords(from: "2026-01-01", to: "2026-01-31", fingerprintsOnly: true)
    XCTAssertEqual(fp.path, "/v1/users/me/ext-records")
    XCTAssertEqual(fp.query.map(\.name), ["fingerprints", "from", "to"])
    XCTAssertFalse(Endpoint.extRecords(from: "2026-01-01", to: "2026-01-31").query.contains { $0.name == "fingerprints" })
  }

  /// P0: no stage is implemented, so a run asks the server nothing and returns the scoring report as it came.
  func testPipelineWithoutStagesIsTheScoringRunAlone() async throws {
    let db = try AppDatabase.inMemory()
    StubProtocol.reset([])
    XCTAssertEqual(LocalCompute.order, ["derive", "workouts", "scores", "stress", "healthspan", "rules"])
    let lc = LocalCompute(db: db, api: api())
    XCTAssertEqual((lc.before + lc.after).map { type(of: $0).name }, ["derive", "workouts", "stress", "healthspan", "rules"])
    XCTAssertFalse((lc.before + lc.after).contains { type(of: $0).implemented })
    var ran = 0
    let want = ScoringSync.Report(at: 1, reason: "test", state: "scored")
    let got = await lc.run(reason: "test") { ran += 1; return want }
    XCTAssertEqual(got, want)
    XCTAssertEqual(ran, 1)
    XCTAssertEqual(StubProtocol.requests.count, 0)
    XCTAssertNil(try db.meta(LocalCompute.lastKey))
    XCTAssertEqual(Schema.migrator.migrations, ["v1_initial", "v2_local_scoring", "v3_local_history"], "the schema stubs add nothing")
  }

  /// A stage runs only when it is implemented and the server says its package is not off.
  func testStageGateAndStateFetch() async throws {
    let db = try AppDatabase.inMemory()
    var lc = LocalCompute(db: db, api: api())
    lc.before = [ProbeStage()]
    lc.after = []
    let pkg: [String: Any] = ["state": "shadow", "flag": true, "implemented": true, "kinds": [String: Any]()]
    let shadow: [String: Any] = ["compute": ["packages": ["derive": pkg]]]
    StubProtocol.reset([.init(status: 200, body: Self.json(shadow))])
    _ = await lc.run(reason: "test") { ScoringSync.Report(at: 1, reason: "test", state: "idle") }
    XCTAssertEqual(StubProtocol.requests.first?.url?.path, "/v1/sync/compute")
    XCTAssertTrue(try XCTUnwrap(db.meta(LocalCompute.lastKey)).contains(#""state":"done""#))
    XCTAssertEqual(LocalCompute.cachedState(db).mode("derive"), .shadow)
    // offline: the cached state; an older server (404): off and the cache goes
    StubProtocol.reset([.init(status: 0, error: .notConnectedToInternet)])
    let offline = await lc.fetchState()
    XCTAssertEqual(offline.mode("derive"), .shadow)
    StubProtocol.reset([.init(status: 404)])
    let gone = await lc.fetchState()
    XCTAssertEqual(gone, .off)
    XCTAssertNil(try db.doc(LocalCompute.stateDoc))
    let skipped = await LocalCompute.runStage(ProbeStage(), ComputeContext(db: db, api: api(), state: .off, reason: "t", now: Date()))
    XCTAssertEqual(skipped.state, "skipped")
  }

  // MARK: - ext_records

  private static func day(_ d: String, kg: Double) -> [String: Any] {
    let weight: [String: Any] = ["date": d, "weight_kg": kg]
    return ["date": d, "fingerprint": "fp-\(d)-\(kg)", "strength_sets": [Any](), "weights": [weight],
            "nutrition_totals": NSNull()]
  }

  func testExtRecordsFetchesOnlyChangedDaysAndDropsGoneOnes() async throws {
    let db = try AppDatabase.inMemory()
    let puller = DocPuller(api: api(), db: db)
    let (d1, d2) = ("2026-01-10", "2026-01-11")
    // first pull: both days are new
    StubProtocol.reset([
      .init(status: 200, body: Self.json(["available": true, "fingerprints": [d1: "fp-\(d1)-80.0", d2: "fp-\(d2)-79.8"]])),
      .init(status: 200, body: Self.json(["available": true, "days": [Self.day(d1, kg: 80.0), Self.day(d2, kg: 79.8)]])),
    ])
    var out = await puller.pullExtRecords(from: "2026-01-01", to: "2026-01-31")
    XCTAssertEqual(out.fetched.sorted(), [d1, d2])
    XCTAssertEqual(Self.query(0)["fingerprints"], "1")
    XCTAssertEqual(Self.query(1)["from"], d1)
    XCTAssertEqual(Self.query(1)["to"], d2)
    XCTAssertNil(Self.query(1)["fingerprints"])
    let w = try XCTUnwrap((db.extRecords(d2)?["weights"] as? [[String: Any]])?.first?["weight_kg"] as? Double)
    XCTAssertEqual(w, 79.8)
    // second pull: only d2 changed -> only d2 is fetched
    StubProtocol.reset([
      .init(status: 200, body: Self.json(["available": true, "fingerprints": [d1: "fp-\(d1)-80.0", d2: "fp-\(d2)-79.0"]])),
      .init(status: 200, body: Self.json(["available": true, "days": [Self.day(d2, kg: 79.0)]])),
    ])
    out = await puller.pullExtRecords(from: "2026-01-01", to: "2026-01-31")
    XCTAssertEqual(out.fetched, [d2])
    XCTAssertEqual(out.unchanged, 1)
    XCTAssertEqual(Self.query(1)["from"], d2)
    XCTAssertEqual(Self.query(1)["to"], d2)
    XCTAssertEqual(try db.extFingerprints(from: "2026-01-01", to: "2026-01-31"), [d1: "fp-\(d1)-80.0", d2: "fp-\(d2)-79.0"])
    // nothing changed: one request only
    StubProtocol.reset([.init(status: 200, body: Self.json(["available": true, "fingerprints": [d1: "fp-\(d1)-80.0", d2: "fp-\(d2)-79.0"]]))])
    out = await puller.pullExtRecords(from: "2026-01-01", to: "2026-01-31")
    XCTAssertEqual(out.fetched, [])
    XCTAssertEqual(StubProtocol.requests.count, 1)
    // the log cannot be read now / no connection: the cache stays
    StubProtocol.reset([.init(status: 200, body: Self.json(["available": false, "error": "unreadable", "fingerprints": [String: String]()]))])
    out = await puller.pullExtRecords(from: "2026-01-01", to: "2026-01-31")
    XCTAssertTrue(out.unavailable)
    StubProtocol.reset([.init(status: 0, error: .notConnectedToInternet)])
    out = await puller.pullExtRecords(from: "2026-01-01", to: "2026-01-31")
    XCTAssertTrue(out.offline)
    XCTAssertEqual(try db.extFingerprints(from: "2026-01-01", to: "2026-01-31").count, 2)
    // d2 is no longer listed: removed; days outside the range are not touched
    try db.saveDoc(DocPuller.extRecordsKey("2025-12-01"), json: Data(Self.json(Self.day("2025-12-01", kg: 81)).utf8))
    StubProtocol.reset([.init(status: 200, body: Self.json(["available": true, "fingerprints": [d1: "fp-\(d1)-80.0"]]))])
    out = await puller.pullExtRecords(from: "2026-01-01", to: "2026-01-31")
    XCTAssertEqual(out.removed, [d2])
    XCTAssertNil(db.extRecords(d2))
    XCTAssertNotNil(db.extRecords("2025-12-01"))
  }
}

/// A fictional implemented stage (P0 ships none).
private struct ProbeStage: ComputeStage {
  static let name = "derive"
  static let package = "derive"
  static let implemented = true
  func run(_ ctx: ComputeContext) async -> ComputeStageReport { .init(stage: Self.name, state: "done") }
}
