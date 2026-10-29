import GRDB
import XCTest
@testable import Watch2Whoop

/// #340 local history: the phone keeps and recomputes its whole history. Made-up data only.
final class LocalHistoryStoreTests: XCTestCase {
  /// A raw_history body as the server sends it (body_engine nativesync/history.py).
  static func body(_ day: String, hr: [(Double, Double)], complete: Bool = true) -> [String: Any] {
    let zone = Synthetic.zone
    let t0: Double = LocalRawStore(db: try! AppDatabase.inMemory(), zone: zone).dayStart(day)!
    let inputs: [String: Any] = ["hr": hr.map { [$0.0, $0.1] }, "hrv": [[3 * 3600.5, 41.25]], "spo2": [[4.0 * 3600, 96.0]]]
    let segments: [[Any]] = [[t0 + 3600, t0 + 7200, "deep"], [t0 + 7200, t0 + 9000, "rem"]]
    let a: Double = t0 + 3 * 3600, b: Double = t0 + 5 * 3600
    let rows: [[Any]] = [["AB-1", a, a + 60, 44.5, 40.0, "app", [0, 810, 1620], [false, false, true]],
                         [NSNull(), b, b + 60, 39.0, 37.0, "apple_export", [0, 790], NSNull()]]
    let beats: [String: Any] = ["cols": ["uuid", "start", "end", "rmssd", "sdnn", "origin", "beats_ms", "gaps"], "rows": rows]
    return ["t0": t0, "complete": complete, "inputs": inputs, "segments": segments, "beats": beats]
  }

  func testServerDaysAreStoredReplacedAndDeleted() throws {
    let db = try AppDatabase.inMemory()
    let store = LocalRawStore(db: db, zone: Synthetic.zone)
    let day = "2025-03-01"
    let n = try db.writer.write { db in
      try store.storeHistoryDay(db, day: day, body: Self.body(day, hr: [(43200, 60.5), (43260, 61), (43260, 99)]))
    }
    XCTAssertEqual(n, 2 + 1 + 1 + 2 + 2)   // the duplicate instant is kept once
    try db.writer.read { db in
      let t0 = store.dayStart(day)!
      XCTAssertEqual(try Double.fetchAll(db, sql: "SELECT t FROM hist_samples WHERE kind = 1 ORDER BY t"), [t0 + 43200, t0 + 43260])
      XCTAssertEqual(try Double.fetchOne(db, sql: "SELECT value FROM hist_samples WHERE kind = 4"), 96)   // SpO2 stays in percent
      XCTAssertEqual(try String.fetchAll(db, sql: "SELECT stage FROM hist_segments ORDER BY start"), ["deep", "rem"])
      let d = try XCTUnwrap(try Row.fetchOne(db, sql: "SELECT * FROM hist_days"))
      XCTAssertEqual(d["source"] as String, "server")
      XCTAssertEqual(d["complete"] as Bool, true)
      let h = try XCTUnwrap(try Row.fetchOne(db, sql: "SELECT hist, source FROM hr_day_hist WHERE date = ?", arguments: [day]))
      XCTAssertEqual(h["source"] as String, "history")
      XCTAssertEqual(try JSONSerialization.jsonObject(with: h["hist"] as Data) as? [String: Int], ["60": 1, "61": 1])
      let beats = try Row.fetchAll(db, sql: "SELECT uuid, rmssd, analyzed, beats FROM heartbeat_series ORDER BY start")
      XCTAssertEqual(beats.map { $0["uuid"] as String }, ["ab-1", "x:\(Int(t0 + 5 * 3600))"])
      XCTAssertEqual(beats[0]["rmssd"] as Double, 44.5)
      XCTAssertEqual(beats[0]["analyzed"] as Bool, true)
      XCTAssertEqual(LocalRawStore.beats(beats[0]["beats"] as Data)?.0, [0, 0.81, 1.62])   // ms → s for analyzeHrv
    }
    // the day again (a later change on the server): replaced as a whole
    try db.writer.write { db in _ = try store.storeHistoryDay(db, day: day, body: Self.body(day, hr: [(50000, 70)], complete: false)) }
    try db.writer.read { db in
      XCTAssertEqual(try Int.fetchOne(db, sql: "SELECT count(*) FROM hist_samples WHERE kind = 1"), 1)
      XCTAssertEqual(try Bool.fetchOne(db, sql: "SELECT complete FROM hist_days"), false)
    }
    // no inputs any more
    try db.writer.write { db in _ = try store.storeHistoryDay(db, day: day, body: nil) }
    try db.writer.read { db in
      for t in ["hist_samples", "hist_segments", "hist_days", "hr_day_hist"] {
        XCTAssertEqual(try Int.fetchOne(db, sql: "SELECT count(*) FROM \(t)"), 0, t)
      }
      XCTAssertEqual(try Int.fetchOne(db, sql: "SELECT count(*) FROM heartbeat_series WHERE origin = 'server'"), 0)
    }
  }

  /// Days leaving the hot window move into the history and score the same from there.
  func testRollOverKeepsTheScores() throws {
    let db = try AppDatabase.inMemory()
    let today = "2026-05-20", days = 90
    let day0 = LocalDay.add(today, -(days - 1))
    try Synthetic.fill(db, today: today, days: days)
    let rt = try OpenBodyScoreRuntime.builtin()
    let ctx = LocalScoring.Context(tz: Synthetic.tz, hrvKind: "sdnn", birthDate: "1990-05-05", origin: day0)
    try LocalScoring.run(db: db, runtime: rt, context: ctx, first: day0, today: today)
    let want = try Synthetic.rows(db)
    let store = LocalRawStore(db: db, zone: Synthetic.zone)
    let rolled = try store.prune(today: today)
    let cutDay = LocalDay.add(today, -(LocalRawStore.keepDays + 2))
    XCTAssertEqual(rolled.first, LocalDay.add(day0, -1))   // the first night started the evening before
    XCTAssertLessThanOrEqual(try XCTUnwrap(rolled.last), cutDay)   // a night before the cut wakes on the cut day at most
    XCTAssertEqual(store.hotFrom(today: today), cutDay)
    try db.writer.read { db in
      XCTAssertEqual(try Double.fetchOne(db, sql: "SELECT min(start) FROM hk_samples").map { $0 >= store.dayStart(cutDay)! }, true)
      XCTAssertGreaterThan(try Int.fetchOne(db, sql: "SELECT count(*) FROM hist_samples") ?? 0, 0)
      XCTAssertEqual(try String.fetchOne(db, sql: "SELECT DISTINCT source FROM hist_days"), "phone")
    }
    // the whole range again, now half from the history: the same rows
    try db.writer.write { db in try db.execute(sql: "DELETE FROM daily_scores") }
    try LocalScoring.run(db: db, runtime: rt, context: ctx, first: day0, today: today)
    let got = try Synthetic.rows(db)
    XCTAssertEqual(got.count, want.count)
    for (d, b) in want {
      let a = try XCTUnwrap(got[d])
      for f in Synthetic.tolerance.keys {
        XCTAssertEqual(LocalRawStore.num(a[f]), LocalRawStore.num(b[f]), "\(d) \(f)")
      }
    }
  }
}

final class HistoryDownloadTests: XCTestCase {
  private func api() -> APIClient {
    APIClient(baseURL: URL(string: "https://body.example.com")!, token: { "example-token" }, session: StubProtocol.session(),
              sleep: { _ in })
  }

  private func ready(_ db: AppDatabase, mode: String = "app") throws {
    try db.setMeta(DeltaRemote.rawRestoredKey, "1")
    try db.setMeta(LocalRawStore.hotFromKey, "2025-03-10")
    try db.saveDoc(ScoringSync.contextDoc, json: Data(#"{"tz": "\#(Synthetic.tz)", "sync_mode": "\#(mode)"}"#.utf8))
  }

  private static func page(_ days: [String], cursor: String, more: Bool, total: Int? = nil) -> String {
    let changes = days.map { d -> [String: Any] in
      ["stream": "raw_history", "op": "upsert", "kind": "", "key": d, "date": d, "version": 1,
       "body": LocalHistoryStoreTests.body(d, hr: [(43200, 60)])]
    }
    var j: [String: Any] = ["changes": changes, "cursors": ["raw_history": cursor], "more": more]
    if let total { j["history"] = ["days": total, "oldest": days.last ?? ""] }
    return String(decoding: try! JSONSerialization.data(withJSONObject: j), as: UTF8.self)
  }

  private static func sent(_ i: Int) -> [String: Any]? {
    let r = StubProtocol.requests[i]
    return (try? JSONSerialization.jsonObject(with: r.httpBodyStream.map(DeltaRemoteTests.read) ?? r.httpBody ?? Data())) as? [String: Any]
  }

  /// Newest first, page by page with the cursor; a short Retry-After is sat out; done at the end.
  func testDownloadsPageByPageAndResumes() async throws {
    let db = try AppDatabase.inMemory()
    try ready(db)
    var waited: [TimeInterval] = []
    StubProtocol.reset([
      .init(status: 200, body: Self.page(["2025-03-09", "2025-03-08"], cursor: "r.5.2025-03-08", more: true, total: 3)),
      .init(status: 429, body: #"{"detail": {"code": "slow_down"}}"#, headers: ["Retry-After": "3"]),
      .init(status: 200, body: Self.page(["2025-03-07"], cursor: "c.5.0", more: false)),
    ])
    let dl = HistoryDownload(db: db, api: api(), policy: { .init(unmetered: true, constrained: false, charging: true, cellularAllowed: false) },
                             sleep: { waited.append($0) })
    let s = await dl.run(reason: "test", today: "2025-05-08")
    XCTAssertEqual(s.phase, "done")
    XCTAssertEqual(s.fetched, 3)
    XCTAssertEqual(s.total, 3)
    XCTAssertEqual(s.oldest, "2025-03-07")
    XCTAssertEqual(s.fraction, 1)
    XCTAssertEqual(waited, [3])
    XCTAssertEqual(StubProtocol.requests.count, 3)
    XCTAssertEqual(Self.sent(0)?["history_before"] as? String, "2025-03-10")   // the hot window's first day
    XCTAssertEqual((Self.sent(0)?["cursors"] as? [String: String])?["raw_history"], "")
    XCTAssertEqual((Self.sent(2)?["cursors"] as? [String: String])?["raw_history"], "r.5.2025-03-08")
    let stored = try await db.writer.read { try Int.fetchOne($0, sql: "SELECT count(*) FROM hist_days") }
    XCTAssertEqual(stored, 3)
    XCTAssertTrue(HistoryDownload.isComplete(db))
    // done: the next run within a day asks nothing
    StubProtocol.reset([])
    _ = await dl.run(reason: "test", today: "2025-05-08")
    XCTAssertEqual(StubProtocol.requests.count, 0)
  }

  func testWaitsForWifiAndPowerOrCellularAndForTheAppMode() async throws {
    let db = try AppDatabase.inMemory()
    try ready(db)
    StubProtocol.reset([])
    let onBattery = HistoryDownload(db: db, api: api(),
                                    policy: { .init(unmetered: true, constrained: false, charging: false, cellularAllowed: false) })
    var s = await onBattery.run(reason: "test", today: "2025-05-08")
    XCTAssertEqual(s.phase, "waiting_network")
    XCTAssertEqual(s.detail, "power")
    let lowData = HistoryDownload(db: db, api: api(),
                                  policy: { .init(unmetered: false, constrained: true, charging: true, cellularAllowed: true) })
    s = await lowData.run(reason: "test", today: "2025-05-08")
    XCTAssertEqual(s.detail, "low_data")
    XCTAssertEqual(StubProtocol.requests.count, 0)
    XCTAssertTrue(HistoryDownload.Policy(unmetered: false, constrained: false, charging: false, cellularAllowed: true).allowed)
    // a long Retry-After ends the run; the cursor stays for the next one
    StubProtocol.reset([.init(status: 429, body: "{}", headers: ["Retry-After": "120"])])
    let cellular = HistoryDownload(db: db, api: api(),
                                   policy: { .init(unmetered: false, constrained: false, charging: false, cellularAllowed: true) },
                                   sleep: { _ in XCTFail("no inline wait") })
    s = await cellular.run(reason: "test", today: "2025-05-08")
    XCTAssertEqual(s.phase, "throttled")
    // a user the server scores from Open Wearables: no history download at all
    let other = try AppDatabase.inMemory()
    try ready(other, mode: "ow")
    s = await HistoryDownload(db: other, api: api(), policy: { .init(unmetered: true, constrained: false, charging: true, cellularAllowed: false) })
      .run(reason: "test", today: "2025-05-08")
    XCTAssertEqual(s.phase, "server_mode")
  }
}

final class FullRecomputeTests: XCTestCase {
  private func api() -> APIClient {
    APIClient(baseURL: URL(string: "https://body.example.com")!, token: { "example-token" }, session: StubProtocol.session(),
              sleep: { _ in })
  }

  private func context(_ rt: OpenBodyScoreRuntime, origin: String) -> StubProtocol.Reply {
    .init(status: 200, body: #"{"tz": "\#(Synthetic.tz)", "hrv_kind": "sdnn", "birth_date": "1990-05-05", "scoring_origin": "\#(origin)", "#
      + #""algo_version": "\#(rt.algoVersions["scores"]!)", "sync_mode": "app", "raw_days": 60,"#
      + #""app_scoring": {"active": true}, "muscle": {"days": {}, "error": null}}"#)
  }

  /// A full recompute in 30-day chunks (each seeded with the previous chunk's rows) gives the one-call recompute's rows
  /// within the server's spot-check tolerance; the upload goes in batches, newest first.
  func testChunkedFullRecomputeMatchesOneCallAndUploadsNewestFirst() async throws {
    let db = try AppDatabase.inMemory()
    let today = "2026-06-30", days = 150
    let day0 = LocalDay.add(today, -(days - 1))
    try Synthetic.fill(db, today: today, days: days)
    let rt = try OpenBodyScoreRuntime.builtin()
    let ctx = LocalScoring.Context(tz: Synthetic.tz, hrvKind: "sdnn", birthDate: "1990-05-05", origin: day0)
    try LocalScoring.run(db: db, runtime: rt, context: ctx, first: day0, today: today)
    let want = try Synthetic.rows(db)
    try await db.writer.write { db in try db.execute(sql: "DELETE FROM daily_scores") }
    try db.saveDoc(ScoringSync.contextDoc, json: Data(#"{"tz": "\#(Synthetic.tz)"}"#.utf8))
    try db.setMeta("algo.checked_at", String(Date().timeIntervalSince1970))
    try db.setMeta(ScoringSync.histKey, "done")
    try db.setMeta(LocalRawStore.hotFromKey, day0)   // everything is "hot" here; the rows come from the chunks either way
    StubProtocol.reset([
      context(rt, origin: day0), context(rt, origin: day0),   // two muscle windows of 120 days
      .init(status: 200, body: #"{"status": "ok", "app_scoring": true}"#),
      .init(status: 200, body: #"{"status": "ok", "app_scoring": true}"#),
    ])
    let sync = ScoringSync(db: db, api: api())
    await sync.startFullRecompute(from: day0)
    let now = LocalDay.parse(today, Synthetic.zone)!.addingTimeInterval(6 * 3600)
    let rep = await sync.run(reason: "test", now: now)
    XCTAssertEqual(rep.state, "scored", rep.detail ?? "")
    XCTAssertEqual(rep.written.count, days)
    XCTAssertNil(try db.meta(ScoringSync.fullKey))
    let got = try Synthetic.rows(db)
    for d in want.keys.sorted() {
      let a = try XCTUnwrap(got[d], d), b = try XCTUnwrap(want[d])
      for (f, tol) in Synthetic.tolerance {
        let x = LocalRawStore.num(a[f]), y = LocalRawStore.num(b[f])
        XCTAssertEqual(x == nil, y == nil, "\(d) \(f)")
        if let x, let y { XCTAssertLessThanOrEqual(abs(x - y), tol + 1e-9, "\(d) \(f): \(x) vs \(y)") }
      }
    }
    let paths = StubProtocol.requests.map { "\($0.httpMethod ?? "") \($0.url!.path)" }
    XCTAssertEqual(paths, ["GET /v1/sync/scoring", "GET /v1/sync/scoring", "PUT /v1/users/me/scores/app", "PUT /v1/users/me/scores/app"])
    let firstUpload = try JSONSerialization.jsonObject(with: XCTUnwrap(StubProtocol.requests[2].httpBodyStream.map(DeltaRemoteTests.read)
                                                                        ?? StubProtocol.requests[2].httpBody)) as? [String: Any]
    let sentDays = (firstUpload?["days"] as? [[String: Any]])?.compactMap { $0["date"] as? String } ?? []
    XCTAssertEqual(sentDays.count, LocalHistoryParams.uploadBatchDays)
    XCTAssertEqual(sentDays.last, today)   // the newest batch first, in date order inside
    XCTAssertEqual(rep.uploaded.count, days)
  }

  /// A deadline between chunks: the recompute stops and goes on from there next time.
  func testFullRecomputeResumes() async throws {
    let db = try AppDatabase.inMemory()
    let today = "2026-06-30", days = 70
    let day0 = LocalDay.add(today, -(days - 1))
    try Synthetic.fill(db, today: today, days: days)
    let rt = try OpenBodyScoreRuntime.builtin()
    try db.saveDoc(ScoringSync.contextDoc, json: Data(#"{"tz": "\#(Synthetic.tz)"}"#.utf8))
    try db.setMeta("algo.checked_at", String(Date().timeIntervalSince1970))
    try db.setMeta(ScoringSync.histKey, "done")
    try db.setMeta(LocalRawStore.hotFromKey, day0)
    let sync = ScoringSync(db: db, api: api())
    await sync.startFullRecompute(from: day0)
    StubProtocol.reset([context(rt, origin: day0)])
    let now = LocalDay.parse(today, Synthetic.zone)!.addingTimeInterval(6 * 3600)
    let rep = await sync.run(reason: "test", now: now, deadline: Date())   // passed: not even one chunk
    XCTAssertEqual(rep.state, "partial")
    XCTAssertEqual(try db.meta(ScoringSync.fullKey), day0)
    StubProtocol.reset([context(rt, origin: day0), .init(status: 200, body: #"{"status": "ok"}"#)])
    let again = await sync.run(reason: "test", now: now)
    XCTAssertEqual(again.state, "scored")
    XCTAssertEqual(again.written.count, days)
    XCTAssertNil(try db.meta(ScoringSync.fullKey))
  }
}

final class LocalStorageTests: XCTestCase {
  /// The account database: never backed up, freed pages given back, its size per area, the cache cleared without the
  /// unsent writes.
  func testBackupExclusionVacuumFootprintAndClearCache() throws {
    let server = "https://storage-test.example", uid = 990_001
    defer { try? AppDatabase.deleteAccount(server: server, userId: uid) }
    let db = try AppDatabase.open(server: server, userId: uid)
    for u in db.files.prefix(1) + [db.fileURL!.deletingLastPathComponent()] {
      XCTAssertEqual(try u.resourceValues(forKeys: [.isExcludedFromBackupKey]).isExcludedFromBackup, true, u.lastPathComponent)
    }
    XCTAssertEqual(try db.writer.read { try Int.fetchOne($0, sql: "PRAGMA auto_vacuum") }, 2)   // incremental
    try Synthetic.fill(db, today: "2026-03-20", days: 20)
    try db.writer.write { db in
      try db.execute(sql: "INSERT INTO records (kind, id, payload, updated_at, pending) VALUES ('water', 'c:1', x'7B7D', 0, 1)")
      try db.execute(sql: "INSERT INTO records (kind, id, payload, updated_at, pending) VALUES ('water', '7', x'7B7D', 0, 0)")
      try db.execute(sql: """
        INSERT INTO outbox (client_id, kind, group_key, method, path, created_at) VALUES ('c1', 'water', 'water', 'POST', '/v1/x', 0)
        """)
    }
    let f = db.footprint()
    XCTAssertGreaterThan(f.fileBytes, 0)
    if let raw = f.raw { XCTAssertGreaterThan(raw, 0) }   // dbstat is in Apple's SQLite; nil elsewhere
    try db.writer.write { db in try db.execute(sql: "DELETE FROM hk_samples") }
    XCTAssertEqual(try db.maintain(onPower: false), "incremental")
    XCTAssertEqual(try db.writer.read { try Int.fetchOne($0, sql: "PRAGMA freelist_count") }, 0)
    XCTAssertEqual(db.unsentWrites(), 1)
    try db.clearCache()
    try db.writer.read { db in
      XCTAssertEqual(try Int.fetchOne(db, sql: "SELECT count(*) FROM sleep_sessions"), 0)
      XCTAssertEqual(try Int.fetchOne(db, sql: "SELECT count(*) FROM meta WHERE key <> 'storage.vacuumed_at'"), 0)
      XCTAssertEqual(try String.fetchAll(db, sql: "SELECT id FROM records"), ["c:1"])
      XCTAssertEqual(try Int.fetchOne(db, sql: "SELECT count(*) FROM outbox"), 1)
    }
    XCTAssertLessThan(db.footprint().fileBytes, f.fileBytes)
  }
}
