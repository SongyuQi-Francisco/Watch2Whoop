import GRDB
import XCTest
@testable import Watch2Whoop

/// #340 N2b: the phone scores the user itself. Made-up data only.
enum Synthetic {
  static let tz = "Australia/Sydney"
  static var zone: TimeZone { TimeZone(identifier: tz)! }

  /// `days` days up to `today`: watch sleep stages (23:00–07:00), three HRV readings a night, a resting heart rate,
  /// heart rate every 10 minutes (a hard hour every other evening); with `server` also what the server would have sent:
  /// the night as a sleep_sessions row (stages + session minutes) and the daily resting heart rate.
  static func fill(_ db: AppDatabase, today: String, days: Int, server: Bool = true) throws {
    var cal = Calendar(identifier: .gregorian)
    cal.timeZone = zone
    let todayNoon = LocalDay.parse(today, zone)!
    try db.writer.write { db in
      var n = 0
      func sample(_ type: String, _ a: Date, _ b: Date, _ v: Double) throws {
        n += 1
        try db.execute(sql: "INSERT INTO hk_samples (uuid, type, start, end, value) VALUES (?, ?, ?, ?, ?)",
                       arguments: ["s\(n)", type, a.timeIntervalSince1970, b.timeIntervalSince1970, v])
      }
      for back in (0..<days).reversed() {
        let noon = todayNoon.addingTimeInterval(-Double(back) * 86_400)
        let day = LocalDay.string(noon, zone)
        let wake = cal.date(bySettingHour: 7, minute: 0, second: 0, of: noon)!
        var t = wake.addingTimeInterval(-8 * 3600)
        let cycle: [(Int, Double)] = [(3, 30), (4, 35 + Double(back % 3) * 5), (3, 20), (5, 25), (2, 5)]
        var k = 0
        var segs: [LocalScoring.Segment] = []
        while t < wake {
          let (stage, mins) = cycle[k % cycle.count]
          let e = min(wake, t.addingTimeInterval(mins * 60))
          try sample(LocalScoring.sleepAnalysis, t, e, Double(stage))
          segs.append(LocalScoring.Segment(start: t.timeIntervalSince1970, end: e.timeIntervalSince1970,
                                           stage: LocalScoring.sleepStages[stage]!))
          t = e
          k += 1
        }
        let wiggle = Double((back * 7) % 5)
        for h in [1.0, 3.0, 5.0] {
          try sample("HKQuantityTypeIdentifierHeartRateVariabilitySDNN", wake.addingTimeInterval(-h * 3600),
                     wake.addingTimeInterval(-h * 3600), 42 + wiggle + Double(back % 4))
        }
        let rhr = 54 + wiggle / 2
        try sample(LocalScoring.restingHR, wake.addingTimeInterval(3 * 3600), wake.addingTimeInterval(3 * 3600), rhr)
        var m = wake.addingTimeInterval(-9 * 3600)
        let end = back == 0 ? wake.addingTimeInterval(4 * 3600) : wake.addingTimeInterval(15 * 3600)
        while m < end {
          let hour = cal.component(.hour, from: m)
          let workout = back % 2 == 0 && hour == 18
          let hr = workout ? 150.0 + Double(back % 5) : (hour < 7 ? 52.0 + wiggle : 74.0)
          try sample("HKQuantityTypeIdentifierHeartRate", m, m, hr)
          m = m.addingTimeInterval(600)
        }
        guard server else { continue }
        let s = LocalScoring.session(segs, start: segs.first!.start, end: segs.last!.end, wake: day, source: "app")
        var payload = s
        payload["start"] = segs.first!.start
        payload["end"] = segs.last!.end
        try db.execute(sql: """
          INSERT INTO sleep_sessions (id, date, start, end, is_main, stages, source, updated_at, payload) VALUES (?, ?, ?, ?, 1, ?, 'app', 0, ?)
          """, arguments: ["s:\(day)", day, segs.first!.start, segs.last!.end,
                           try JSONSerialization.data(withJSONObject: segs.map { [$0.stage, $0.start, $0.end] }),
                           try JSONSerialization.data(withJSONObject: payload)])
        try db.execute(sql: "INSERT INTO daily_metrics (date, metric, value, source, updated_at) VALUES (?, 'resting_heart_rate', ?, 'app', 0)",
                       arguments: [day, rhr])
      }
    }
  }

  static func rows(_ db: AppDatabase) throws -> [String: [String: Any]] {
    try db.writer.read { db in
      var out: [String: [String: Any]] = [:]
      for r in try Row.fetchAll(db, sql: "SELECT date, payload FROM daily_scores") {
        if let p = (r["payload"] as Data?).flatMap({ try? JSONSerialization.jsonObject(with: $0) as? [String: Any] }) {
          out[r["date"]] = p
        }
      }
      return out
    }
  }

  /// The server's spot-check tolerance (body_engine nativesync/params.CHECK_TOLERANCE).
  static let tolerance: [String: Double] = [
    "sleep_score": 1, "recovery": 1, "sleep_need_min": 1, "sleep_debt_min": 1, "sleep_actual_min": 1,
    "sleep_performance": 0.01, "hrv_night_ln": 0.001, "rhr_night": 0.1, "resp_rate_night": 0.1, "trimp": 0.1,
    "strain": 0.1, "muscle_load": 0.1, "ctl": 0.1, "atl": 0.1, "tsb": 0.1, "hr_max": 1,
  ]
}

final class LocalRawStoreTests: XCTestCase {
  func testIngestKeepsHistogramsAndMarksDays() throws {
    let db = try AppDatabase.inMemory()
    let store = LocalRawStore(db: db, zone: Synthetic.zone)
    let t = LocalDay.parse("2026-01-10", Synthetic.zone)!.timeIntervalSince1970
    let hr = LocalRawStore.heartRate
    let days = try store.ingest([
      .init(uuid: "a", type: hr, start: t, end: t, value: 60.5),          // rounds to 60, like Python's round()
      .init(uuid: "b", type: hr, start: t + 60, end: t + 60, value: 61),
      .init(uuid: "c", type: hr, start: t + 60, end: t + 60, value: 99),  // same instant: the first one counts
      .init(uuid: "d", type: LocalRawStore.sleepAnalysis, start: t + 14 * 3600, end: t + 15 * 3600, value: 3),   // wakes 03:00 next day → that day − 4 h
      .init(uuid: "e", type: "HKQuantityTypeIdentifierStepCount", start: t, end: t, value: 10),                // not a scoring type
    ])
    XCTAssertEqual(days, ["2026-01-10"])
    let hist = try db.writer.read { try Data.fetchOne($0, sql: "SELECT hist FROM hr_day_hist WHERE date = '2026-01-10'") }
    XCTAssertEqual(try JSONSerialization.jsonObject(with: XCTUnwrap(hist)) as? [String: Int], ["60": 1, "61": 1])
    XCTAssertEqual(try db.writer.read { try Int.fetchOne($0, sql: "SELECT count(*) FROM hk_samples") }, 4)
    // a deletion changes its day too; the server's histograms never replace the phone's own
    XCTAssertEqual(try store.ingest([], deleted: ["b"]), ["2026-01-10"])
    try store.storeServerHistograms(["2026-01-10": ["70": 5], "2025-12-01": ["80": 2]])
    let rows = try db.writer.read { try Row.fetchAll($0, sql: "SELECT date, source FROM hr_day_hist ORDER BY date") }
    XCTAssertEqual(rows.map { [$0["date"] as String, $0["source"] as String] }, [["2025-12-01", "server"], ["2026-01-10", "phone"]])
    // pruning keeps the histograms of the raw days it drops
    try store.prune(today: "2026-06-01")
    XCTAssertEqual(try db.writer.read { try Int.fetchOne($0, sql: "SELECT count(*) FROM hk_samples") }, 0)
    XCTAssertEqual(try db.writer.read { try Int.fetchOne($0, sql: "SELECT count(*) FROM hr_day_hist") }, 2)
  }

  func testHeartbeatsAreStoredThenAnalysedByTheBundle() throws {
    let db = try AppDatabase.inMemory()
    let store = LocalRawStore(db: db, zone: Synthetic.zone)
    var beats: [Double] = []
    var t = 0.0
    for i in 0..<80 { t += 0.95 + Double(i % 7) * 0.01; beats.append(t) }
    let days = try store.ingestBeats([["uuid": "ABC", "start": "2026-01-10T01:00:00.700Z", "end": "2026-01-10T01:01:00Z",
                                       "beats": beats, "gaps": [Bool](repeating: false, count: beats.count)]])
    XCTAssertEqual(days, ["2026-01-10"])
    XCTAssertEqual(try store.analyzeBeats(try OpenBodyScoreRuntime.builtin()), 1)
    let r = try XCTUnwrap(try db.writer.read { try Row.fetchOne($0, sql: "SELECT * FROM heartbeat_series") })
    XCTAssertEqual(r["uuid"] as String, "abc")
    XCTAssertEqual(r["start"] as Double, LocalDay.parse("2026-01-10", TimeZone(identifier: "UTC")!)!.timeIntervalSince1970 - 11 * 3600)
    XCTAssertNotNil(r["rmssd"] as Double?)
    XCTAssertNotNil(r["beats"] as Data?)   // the beats stay (local history: a new HRV algorithm can analyse them again)
    XCTAssertEqual(r["analyzed"] as Bool, true)
    XCTAssertEqual(try store.analyzeBeats(try OpenBodyScoreRuntime.builtin()), 0)   // once
  }
}

final class SeededScoringTests: XCTestCase {
  /// The phone's seeded run (a later first, stored rows, day histograms, a short raw window) gives the full recompute's
  /// rows within the server's spot-check tolerance, through JavaScriptCore.
  func testSeededRunMatchesTheFullRecompute() throws {
    let db = try AppDatabase.inMemory()
    let today = "2026-03-20", days = 70
    let day0 = LocalDay.add(today, -(days - 1))
    try Synthetic.fill(db, today: today, days: days)
    let rt = try OpenBodyScoreRuntime.builtin()
    let ctx = LocalScoring.Context(tz: Synthetic.tz, hrvKind: "sdnn", birthDate: "1990-05-05", origin: day0)
    let full = try LocalScoring.run(db: db, runtime: rt, context: ctx, first: day0, today: today)
    XCTAssertEqual(full.written.count, days)
    XCTAssertEqual(full.unsettled, [])
    let want = try Synthetic.rows(db)
    XCTAssertNotNil(want[today]?["recovery"] as? Double)
    XCTAssertNil((want[today]?["details"] as? [String: Any])?["load_state"])
    // the phone after a while: histograms for the past, raw samples only from two days before `first`
    let store = LocalRawStore(db: db, zone: Synthetic.zone)
    try db.writer.write { db in
      try store.refreshHistograms(db, days: Set((0..<days).map { LocalDay.add(today, -$0) }))
    }
    let first = LocalDay.add(today, -6)
    var cal = Calendar(identifier: .gregorian)
    cal.timeZone = Synthetic.zone
    let cut = cal.startOfDay(for: LocalDay.parse(LocalDay.add(first, -2), Synthetic.zone)!).timeIntervalSince1970
    try db.writer.write { db in try db.execute(sql: "DELETE FROM hk_samples WHERE start < ?", arguments: [cut]) }
    let seeded = try LocalScoring.run(db: db, runtime: rt, context: ctx, first: first, today: today)
    XCTAssertEqual(seeded.written, (0...6).map { LocalDay.add(first, $0) })
    let got = try Synthetic.rows(db)
    for d in seeded.written {
      let a = try XCTUnwrap(got[d]), b = try XCTUnwrap(want[d])
      for (f, tol) in Synthetic.tolerance {
        let x = LocalRawStore.num(a[f]), y = LocalRawStore.num(b[f])
        XCTAssertEqual(x == nil, y == nil, "\(d) \(f)")
        if let x, let y { XCTAssertLessThanOrEqual(abs(x - y), tol + 1e-9, "\(d) \(f): \(x) vs \(y)") }
      }
      XCTAssertNotNil((a["details"] as? [String: Any])?["load_state"], d)
    }
  }
}

final class DeltaRemoteTests: XCTestCase {
  private func api() -> APIClient {
    APIClient(baseURL: URL(string: "https://body.example.com")!, token: { "example-token" }, session: StubProtocol.session(),
              sleep: { _ in })
  }

  func testRestoreAppliesTheScoringStreamsAndStopsPullingRaw() async throws {
    let db = try AppDatabase.inMemory()
    let page = #"""
    {"changes": [
      {"stream": "scores", "op": "upsert", "kind": "", "key": "2026-01-09", "date": "2026-01-09", "version": 3,
       "body": {"recovery": 61, "sleep_score": 80, "strain": 9.5, "rhr_night": 52.1, "ctl": 40.2, "atl": 50.1,
                "algo_version": "v-test", "origin": "server", "computed_at": 1767900000, "flags": {}, "details": {}}},
      {"stream": "daily", "op": "upsert", "kind": "", "key": "2026-01-09", "date": "2026-01-09", "version": 4,
       "body": {"metrics": {"resting_heart_rate": {"value": 53, "unit": "count/min", "source": "app"}}}},
      {"stream": "sleep", "op": "upsert", "kind": "", "key": "2026-01-09", "date": "2026-01-09", "version": 5,
       "body": {"sessions": [{"id": 7, "start": 1767879000, "end": 1767906000, "asleep_min": 420, "deep_min": 60,
                              "rem_min": 90, "core_min": 270, "awake_min": 30, "is_main": true, "source": "app", "device": "Apple Watch"}],
                "segments": [[1767879000, 1767880800, "core"], [1767880800, 1767882600, "deep"]]}},
      {"stream": "raw", "op": "upsert", "kind": "", "key": "2026-01-09", "date": "2026-01-09", "version": 6,
       "body": {"samples": {"cols": ["uuid", "type", "start", "end", "value", "value_text", "source", "tz", "meta"],
                            "rows": [["AA-1", "HKQuantityTypeIdentifierHeartRate", 1767920400, 1767920400, 61, null, "Watch", null, null],
                                     ["AA-2", "HKQuantityTypeIdentifierOxygenSaturation", 1767920400, 1767920400, 97, null, "Watch", null, null],
                                     ["AA-3", "HKQuantityTypeIdentifierStepCount", 1767920400, 1767920400, 9, null, "Watch", null, null]]},
                "stats": {"cols": ["type", "start", "value"], "rows": []},
                "beats": {"cols": ["uuid", "start", "end", "rmssd", "sdnn", "origin"], "rows": [["BB-1", 1767885000, 1767885060, 45.5, 40, "app"]]}}}
    ],
     "cursors": {"scores": "c.9.10", "daily": "c.9.10", "sleep": "c.9.10", "raw": "c.9.10"}, "more": false, "data_version": 10}
    """#
    StubProtocol.reset([.init(status: 200, body: page), .init(status: 200, body: #"{"changes": [], "cursors": {}, "more": false}"#)])
    let remote = DeltaRemote(api: api(), zone: { Synthetic.zone })
    let out = try await remote.pullAll(into: db, today: "2026-01-12")
    XCTAssertEqual(out.changes, 4)
    XCTAssertEqual(out.dirty, ["2026-01-09"])
    let sent = try JSONSerialization.jsonObject(with: XCTUnwrap(StubProtocol.requests[0].httpBodyStream.map(Self.read)
                                                                 ?? StubProtocol.requests[0].httpBody)) as? [String: Any]
    XCTAssertEqual((sent?["cursors"] as? [String: String])?["raw"], "")
    XCTAssertEqual(sent?["raw_since"] as? String, LocalRawStore.rawStart(today: "2026-01-12"))
    try await db.writer.read { db in
      let s = try XCTUnwrap(try DailyScore.fetchOne(db, key: "2026-01-09"))
      XCTAssertEqual(s.recovery, 61)
      XCTAssertEqual(s.origin, "server")
      XCTAssertTrue(s.uploaded)
      XCTAssertEqual(try Double.fetchOne(db, sql: "SELECT value FROM daily_metrics WHERE metric = 'resting_heart_rate'"), 53)
      let sl = try XCTUnwrap(try Row.fetchOne(db, sql: "SELECT * FROM sleep_sessions"))
      XCTAssertEqual(sl["date"] as String, "2026-01-09")
      let stages = try JSONSerialization.jsonObject(with: sl["stages"] as Data) as? [[Any]]
      XCTAssertEqual(stages?.first?.first as? String, "core")
      XCTAssertEqual(try Double.fetchOne(db, sql: "SELECT value FROM hk_samples WHERE uuid = 'aa-2'"), 0.97)   // HealthKit's fraction
      XCTAssertEqual(try Int.fetchOne(db, sql: "SELECT count(*) FROM hk_samples"), 2)                          // not a scoring type: left out
      XCTAssertEqual(try Double.fetchOne(db, sql: "SELECT rmssd FROM heartbeat_series WHERE uuid = 'bb-1'"), 45.5)
      XCTAssertNotNil(try Data.fetchOne(db, sql: "SELECT hist FROM hr_day_hist"))
      XCTAssertEqual(try String.fetchAll(db, sql: "SELECT date FROM score_dirty"), ["2026-01-09"])
      XCTAssertEqual(try String.fetchAll(db, sql: "SELECT stream FROM sync_cursors ORDER BY stream"), ["daily", "scores", "sleep"])
    }
    XCTAssertEqual(try db.meta(DeltaRemote.rawRestoredKey), "1")
    // the next pull asks for the scoring streams only
    _ = try await remote.pullAll(into: db, today: "2026-01-12")
    let next = try JSONSerialization.jsonObject(with: XCTUnwrap(StubProtocol.requests[1].httpBodyStream.map(Self.read)
                                                                 ?? StubProtocol.requests[1].httpBody)) as? [String: Any]
    XCTAssertEqual((next?["cursors"] as? [String: String]).map { Set($0.keys) }, ["scores", "daily", "sleep"])
    XCTAssertNil(next?["raw_since"])
  }

  static func read(_ s: InputStream) -> Data {
    s.open()
    defer { s.close() }
    var out = Data(), buf = [UInt8](repeating: 0, count: 4096)
    while s.hasBytesAvailable {
      let n = s.read(&buf, maxLength: buf.count)
      if n <= 0 { break }
      out.append(buf, count: n)
    }
    return out
  }
}

final class ScoringSyncTests: XCTestCase {
  private func api() -> APIClient {
    APIClient(baseURL: URL(string: "https://body.example.com")!, token: { "example-token" }, session: StubProtocol.session(),
              sleep: { _ in })
  }

  private func context(_ rt: OpenBodyScoreRuntime, mode: String, origin: String) -> String {
    #"{"tz": "\#(Synthetic.tz)", "hrv_kind": "sdnn", "birth_date": "1990-05-05", "scoring_origin": "\#(origin)", "#
      + #""algo_version": "\#(rt.algoVersions["scores"]!)", "sync_mode": "\#(mode)", "raw_days": 60,"#
      + #""app_scoring": {"active": false}, "muscle": {"days": {}, "error": null}}"#
  }

  /// After a sync: the dirty days are scored on the phone and uploaded; the server says it now leaves the scoring to the app.
  func testScoresTheDirtyDaysAndUploadsThem() async throws {
    let db = try AppDatabase.inMemory()
    let today = "2026-03-20", day0 = LocalDay.add(today, -69)
    try Synthetic.fill(db, today: today, days: 70)
    let rt = try OpenBodyScoreRuntime.builtin()
    // the server's rows of the past (the full recompute) and what the phone knows already
    try LocalScoring.run(db: db, runtime: rt, context: .init(tz: Synthetic.tz, hrvKind: "sdnn", birthDate: "1990-05-05", origin: day0),
                         first: day0, today: today)
    try await db.writer.write { db in
      try db.execute(sql: "UPDATE daily_scores SET origin = 'server', uploaded = 1")
      try LocalRawStore.markDirty(db, [LocalDay.add(today, -2), LocalDay.add(today, -1), today])
    }
    try db.saveDoc(ScoringSync.contextDoc, json: Data(#"{"tz": "\#(Synthetic.tz)"}"#.utf8))
    try db.setMeta("algo.checked_at", String(Date().timeIntervalSince1970))
    try db.setMeta(ScoringSync.histKey, "done")
    StubProtocol.reset([
      .init(status: 200, body: context(rt, mode: "app", origin: day0)),
      .init(status: 200, body: #"{"status": "ok", "stored": 3, "app_scoring": true, "check": {"date": "x", "ok": true, "diffs": {}}}"#),
    ])
    let sync = ScoringSync(db: db, api: api())
    let now = LocalDay.parse(today, Synthetic.zone)!.addingTimeInterval(6 * 3600)
    let rep = await sync.run(reason: "test", now: now)
    XCTAssertEqual(rep.state, "scored", rep.detail ?? "")
    XCTAssertEqual(rep.first, LocalDay.add(today, -2))
    XCTAssertEqual(rep.uploaded, [LocalDay.add(today, -2), LocalDay.add(today, -1), today])
    XCTAssertEqual(rep.upload, "ok")
    XCTAssertEqual(rep.appScoring, true)
    XCTAssertEqual(StubProtocol.requests.map { $0.url!.path }, ["/v1/sync/scoring", "/v1/users/me/scores/app"])
    XCTAssertEqual(StubProtocol.requests[1].httpMethod, "PUT")
    let body = try JSONSerialization.jsonObject(with: XCTUnwrap(StubProtocol.requests[1].httpBodyStream.map(DeltaRemoteTests.read)
                                                                 ?? StubProtocol.requests[1].httpBody)) as? [String: Any]
    XCTAssertEqual(body?["algo_version"] as? String, rt.algoVersions["scores"])
    XCTAssertEqual(body?["bundle_version"] as? String, rt.version)
    let first = (body?["days"] as? [[String: Any]])?.first
    XCTAssertEqual(first?["date"] as? String, LocalDay.add(today, -2))
    let row = first?["row"] as? [String: Any]
    XCTAssertNil(row?["date"])
    XCTAssertNil(row?["bundle_version"])
    XCTAssertNotNil(row?["recovery"] as? Double)
    try await db.writer.read { db in
      XCTAssertEqual(try Int.fetchOne(db, sql: "SELECT count(*) FROM daily_scores WHERE origin = 'app' AND uploaded = 1"), 3)
      XCTAssertEqual(try Int.fetchOne(db, sql: "SELECT count(*) FROM score_dirty"), 0)
    }
    XCTAssertEqual(ScoringSync.lastReport(db)?.uploaded.count, 3)
  }

  /// A user the server scores from Open Wearables / the Shortcut: the phone does not.
  func testOnlyAppModeUsersAreScoredHere() async throws {
    let db = try AppDatabase.inMemory()
    try Synthetic.fill(db, today: "2026-03-20", days: 5)
    try await db.writer.write { db in try LocalRawStore.markDirty(db, ["2026-03-20"]) }
    try db.setMeta("algo.checked_at", String(Date().timeIntervalSince1970))
    try db.setMeta(ScoringSync.histKey, "done")
    let rt = try OpenBodyScoreRuntime.builtin()
    StubProtocol.reset([.init(status: 200, body: context(rt, mode: "ow", origin: "2026-03-16"))])
    let rep = await ScoringSync(db: db, api: api()).run(reason: "test", now: LocalDay.parse("2026-03-20", Synthetic.zone)!)
    XCTAssertEqual(rep.state, "server_mode")
    XCTAssertEqual(StubProtocol.requests.count, 1)
    let scored = try await db.writer.read { try Int.fetchOne($0, sql: "SELECT count(*) FROM daily_scores") }
    XCTAssertEqual(scored, 0)
  }

  /// A newer signed bundle replaces the built-in one after both checks; a tampered one never runs.
  func testBundleUpdateVerifiesThenSwitches() async throws {
    let db = try AppDatabase.inMemory()
    let dir = FileManager.default.temporaryDirectory.appendingPathComponent("n2b-algo-\(UUID().uuidString)")
    defer { try? FileManager.default.removeItem(at: dir) }
    let src = "/*! openbodyscore-js 9.9.9 | test */ globalThis.OpenBodyScore = {VERSION: '9.9.9', ALGO_VERSIONS: {scores: 'v-next'}};"
    let sha = SHA256Hex.of(Data(src.utf8))
    let bundles = AlgoBundles(db: db, dir: dir)
    XCTAssertEqual(try bundles.runtime().version, try OpenBodyScoreRuntime.builtin().version)
    StubProtocol.reset([
      .init(status: 200, body: #"{"version": "9.9.9", "sha256": "\#(String(repeating: "0", count: 64))", "url": "/v1/algo/openbodyscore-9.9.9.js", "signature": null}"#),
      .init(status: 200, body: src),
    ])
    let refused = await bundles.update(api: api(), force: true)
    XCTAssertNil(refused)
    XCTAssertNotEqual(try bundles.runtime().version, "9.9.9")
    StubProtocol.reset([
      .init(status: 200, body: #"{"version": "9.9.9", "sha256": "\#(sha)", "url": "/v1/algo/openbodyscore-9.9.9.js", "signature": null}"#),
      .init(status: 200, body: src),
    ])
    let v = await bundles.update(api: api(), force: true)
    XCTAssertEqual(v, "9.9.9")   // unsigned: a Debug build takes it (Release refuses, BundleVerifier)
    XCTAssertEqual(try bundles.runtime().version, "9.9.9")
    XCTAssertEqual(try AlgoBundles(db: db, dir: dir).runtime().algoVersions["scores"], "v-next")   // after a restart too
  }
}

import CryptoKit

enum SHA256Hex {
  static func of(_ d: Data) -> String { SHA256.hash(data: d).map { String(format: "%02x", $0) }.joined() }
}
