import GRDB
import XCTest
@testable import Watch2Whoop

/// The bedtime goal page's store (2.0.0): the cached settings and the queued writes, the phone's view on the local tables
/// against the server's (golden coach.json `goals`, written by body_engine/tests/test_js_golden_coach.py from
/// goals/store.view_compute on M3's made-up person), and the sleep coach taking the real goal.
final class BedtimeGoalTests: XCTestCase {
  private let syd = TimeZone(identifier: "Australia/Sydney")!
  /// goals_case's goal of cases_coach.py with a wake time and a duration (case 10 of `goals`, night END = 2026-10-20).
  private let goal: [String: Any] = ["target_bedtime": "22:30", "target_wake": "06:45", "target_duration_min": 450,
                                     "pace_min_per_step": 20, "step_days": 3, "start_date": "2026-10-10",
                                     "start_bedtime_off": 30, "status": "active"]

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

  private func at(_ iso: String) -> Date { ISO8601DateFormatter().date(from: iso)! }

  private func serverDoc(_ db: AppDatabase, night: String, settings: [String: Any]?) throws {
    let doc: [String: Any] = ["night": night, "target_bedtime": settings?["target_bedtime"] ?? NSNull(), "plan": [Any](),
                              "progress": ["avg_bedtime_7d": "01:12", "nights_7d": 7], "recent_nights": [Any](),
                              "settings": settings ?? NSNull()]
    try db.saveDoc(BedtimeGoal.serverKey, json: try Self.data(doc))
  }

  func testSleepDayHelpers() {
    XCTAssertEqual(BedtimeGoal.tonight(at("2026-10-20T13:00:00Z"), tz: syd), "2026-10-20")   // 00:00 on the 21st, Sydney
    XCTAssertEqual(BedtimeGoal.tonight(at("2026-10-21T00:30:00Z"), tz: syd), "2026-10-20")   // 11:30: still last night
    XCTAssertEqual(BedtimeGoal.tonight(at("2026-10-21T01:00:00Z"), tz: syd), "2026-10-21")   // 12:00
    XCTAssertEqual(BedtimeGoal.toOff("23:00"), -60)
    XCTAssertEqual(BedtimeGoal.toOff("06:30"), 390)
    XCTAssertEqual(BedtimeGoal.toOff("12:00"), -720)
    XCTAssertNil(BedtimeGoal.toOff("24:00"))
    XCTAssertEqual(BedtimeGoal.fmt(-60), "23:00")
    XCTAssertEqual(BedtimeGoal.round5(72), 70)
    XCTAssertEqual(BedtimeGoal.round5(-58), -60)
    XCTAssertEqual(BedtimeGoalModel.offset18("18:00"), 0)
    XCTAssertEqual(BedtimeGoalModel.offset18("02:00"), 480)
    XCTAssertEqual(BedtimeChart.clock(480), "02:00")
  }

  /// The draft is what goals/store.set_goal stores: fallbacks to the old goal, then the defaults; the stepping restarts
  /// tonight from the rounded recent average (or the target without one).
  func testDraftFollowsSetGoal() throws {
    let body = BedtimeGoal.body(target: "00:00", wake: nil, pace: 15)
    var d = try XCTUnwrap(BedtimeGoal.draft(body: body, old: nil, avgBedtime: "01:12", tonight: "2026-10-20"))
    XCTAssertEqual(d["pace_min_per_step"] as? Int, 15)
    XCTAssertEqual(d["step_days"] as? Int, 3)
    XCTAssertEqual(d["start_bedtime_off"] as? Int, 70)
    XCTAssertEqual(d["start_date"] as? String, "2026-10-20")
    XCTAssertEqual(d["status"] as? String, "active")
    XCTAssertTrue(d["target_wake"] is NSNull)
    d = try XCTUnwrap(BedtimeGoal.draft(body: ["target_bedtime": "23:30"], old: goal, avgBedtime: nil, tonight: "2026-10-20"))
    XCTAssertEqual(d["pace_min_per_step"] as? Int, 20)
    XCTAssertEqual(d["target_duration_min"] as? Int, 450)
    XCTAssertEqual(d["start_bedtime_off"] as? Int, -30)
    XCTAssertNil(BedtimeGoal.draft(body: nil, old: goal, avgBedtime: "01:12", tonight: "2026-10-20"))
    XCTAssertEqual(BedtimeGoal.body(target: "00:00", wake: "07:30", pace: 90)["pace_min_per_step"] as? Int, 60)
  }

  /// Saving offline: the draft wins while the write is queued, a newer write (clear) replaces the queued one, and once
  /// the queue is empty the server's cached row is used again.
  func testWritesQueueAndTheSettingsFollowTheDraft() throws {
    let db = try AppDatabase.inMemory()
    let queue = OfflineQueue(db: db)
    try serverDoc(db, night: "2026-10-20", settings: goal)
    try db.writer.read { XCTAssertEqual(try BedtimeGoal.settings($0)?["target_bedtime"] as? String, "22:30") }
    let now = at("2026-10-20T10:00:00Z")   // 21:00 in Sydney
    try BedtimeGoal.write(db, queue: queue, body: BedtimeGoal.body(target: "00:00", wake: nil, pace: 15), now: now, tz: syd)
    var items = try queue.all()
    XCTAssertEqual(items.count, 1)
    XCTAssertEqual(items.first?.method, "PUT")
    XCTAssertEqual(items.first?.path, Endpoint.sleepGoalPath)
    XCTAssertEqual(items.first?.kind, BedtimeGoal.kind)
    let sent = try XCTUnwrap(items.first?.body.flatMap { try JSONSerialization.jsonObject(with: $0) as? [String: Any] })
    XCTAssertEqual(sent["target_bedtime"] as? String, "00:00")
    XCTAssertNil(sent["status"])
    try db.writer.read { db in
      let s = try XCTUnwrap(try BedtimeGoal.settings(db))
      XCTAssertEqual(s["target_bedtime"] as? String, "00:00")
      XCTAssertEqual(s["target_duration_min"] as? Int, 450, "kept from the old goal")
      XCTAssertEqual(s["start_bedtime_off"] as? Int, 70, "the server copy's 7-night average 01:12, rounded")
      XCTAssertEqual(s["start_date"] as? String, "2026-10-20")
      XCTAssertTrue(try BedtimeGoal.pendingWrite(db))
    }
    try BedtimeGoal.write(db, queue: queue, body: nil, now: now, tz: syd)
    items = try queue.all()
    XCTAssertEqual(items.map(\.method), ["DELETE"], "last write wins")
    XCTAssertNil(items.first?.body)
    try db.writer.read { XCTAssertNil(try BedtimeGoal.settings($0)) }
    // sent: the queue is empty, the server's copy counts again
    let sender: OfflineQueue.Sender = { _ in .done }
    _ = try awaitDrain(queue, sender)
    try db.writer.read { db in
      XCTAssertFalse(try BedtimeGoal.pendingWrite(db))
      XCTAssertEqual(try BedtimeGoal.settings(db)?["target_bedtime"] as? String, "22:30")
    }
  }

  private func awaitDrain(_ queue: OfflineQueue, _ send: @escaping OfflineQueue.Sender) throws -> Int {
    let done = expectation(description: "drain")
    var n = 0
    Task {
      n = (try? await queue.drain(send: send)) ?? -1
      done.fulfill()
    }
    wait(for: [done], timeout: 10)
    return n
  }

  /// The phone's view from the local tables equals the server's view_compute on the same person and goal (the golden
  /// `goals` cases 10 and 11), is compared in shadow, and the sleep coach takes it as its goal.
  func testLocalViewEqualsTheServersAndFeedsTheSleepCoach() throws {
    let f = try Self.golden("coach_local")
    let parts = try XCTUnwrap(f["parts"] as? [String: Any]), today = try XCTUnwrap(f["today"] as? String)
    let cases = try XCTUnwrap(try Self.golden("coach")["goals"] as? [[String: Any]])
    let db = try AppDatabase.inMemory()
    try db.writer.write { db in
      for (i, s) in (parts["sleep"] as? [[String: Any]] ?? []).enumerated() {
        let payload: [String: Any] = ["asleep_min": s["asleep_min"] ?? NSNull()]
        try db.execute(sql: """
          INSERT INTO sleep_sessions (id, date, start, end, is_main, source, payload, updated_at) VALUES (?, ?, ?, ?, ?, 'app', ?, 0)
          """, arguments: ["s\(i)", s["wake_date"] as? String, (LocalRawStore.num(s["start_at"]) ?? 0) / 1000,
                           (LocalRawStore.num(s["end_at"]) ?? 0) / 1000, s["is_main"] as? Bool ?? true, try Self.data(payload)])
      }
      for r in parts["scores"] as? [[String: Any]] ?? [] {
        let payload: [String: Any] = ["sleep_actual_min": r["sleep_actual_min"] ?? NSNull(), "strain": r["strain"] ?? NSNull()]
        try db.execute(sql: "INSERT INTO daily_scores (date, strain, payload, origin, computed_at) VALUES (?, ?, ?, 'server', 0)",
                       arguments: [r["date"] as? String, LocalRawStore.num(r["strain"]), try Self.data(payload)])
      }
    }
    let rt = try OpenBodyScoreRuntime.builtin()
    XCTAssertEqual(today, "2026-10-20")
    for (k, settings) in [(10, goal), (11, goal.merging(["status": "paused", "target_wake": NSNull(), "target_duration_min": NSNull()]) { $1 })] {
      try serverDoc(db, night: today, settings: settings)
      let local = try XCTUnwrap(try BedtimeGoal.compute(db, runtime: rt, tz: "Australia/Sydney", night: today))
      let c = SeriesLocal.compare(local: local, server: cases[k])
      XCTAssertTrue(c.ok, "case \(k): \(c.fieldsOver)")
      XCTAssertGreaterThan(c.compared, 20)
    }
    // shadow comparison against the server's cached view (its `settings` aside)
    try serverDoc(db, night: today, settings: goal)
    var server = try XCTUnwrap(try BedtimeGoal.compute(db, runtime: rt, tz: "Australia/Sydney", night: today))
    server["settings"] = goal
    try db.saveDoc(BedtimeGoal.serverKey, json: try Self.data(server))
    _ = try BedtimeGoal.compute(db, runtime: rt, tz: "Australia/Sydney", night: today)
    let meta = try XCTUnwrap(try db.meta(CoachLocal.compareMeta)).data(using: .utf8)
      .flatMap { try? JSONSerialization.jsonObject(with: $0) } as? [String: Any]
    XCTAssertEqual((meta?["sleep_goal"] as? [String: Any])?["ok"] as? Bool, true)
    // the coach document's sleep coach takes tonight's goal (21:00 in Sydney on the 20th)
    let now = at("2026-10-20T10:00:00Z")
    var doc = try XCTUnwrap(try CoachLocal.coach(today, db: db, runtime: rt, tz: "Australia/Sydney", today: today, now: now))
    var coach = try XCTUnwrap(doc["sleep_coach"] as? [String: Any])
    XCTAssertEqual(coach["source"] as? String, "goal")
    XCTAssertEqual(coach["goal_bedtime"] as? String, "00:15")
    XCTAssertEqual(coach["wake_source"] as? String, "goal")
    XCTAssertEqual(coach["wake_time"] as? String, "06:45")
    // no goal: the sleep need decides, as before
    try serverDoc(db, night: today, settings: nil)
    doc = try XCTUnwrap(try CoachLocal.coach(today, db: db, runtime: rt, tz: "Australia/Sydney", today: today, now: now))
    coach = try XCTUnwrap(doc["sleep_coach"] as? [String: Any])
    XCTAssertNotEqual(coach["source"] as? String, "goal")
    XCTAssertTrue(coach["goal_bedtime"] is NSNull || coach["goal_bedtime"] == nil)
  }

  /// The page model: seven slots before tonight, the plan step and the next one, the progress numbers.
  func testModelFromAView() throws {
    let cases = try XCTUnwrap(try Self.golden("coach")["goals"] as? [[String: Any]])
    let m = try XCTUnwrap(BedtimeGoalModel(json: cases[1]))
    XCTAssertTrue(m.hasGoal)
    XCTAssertFalse(m.paused)
    XCTAssertEqual(m.tonight, "00:15")
    XCTAssertEqual(m.target, "22:30")
    XCTAssertEqual(m.nights.count, 7)
    XCTAssertEqual(m.nights.first?.night, "2026-10-13")
    XCTAssertEqual(m.nights.last?.night, "2026-10-19")
    XCTAssertEqual(m.nights.last?.onTarget, true)
    XCTAssertEqual(m.stage, 4)
    XCTAssertEqual(m.stages, 6)
    XCTAssertEqual(m.next?.date, "2026-10-22")
    XCTAssertEqual(m.next?.bedtime, "22:50")
    XCTAssertEqual(m.onTarget7, 2)
    XCTAssertEqual(m.avg7, "00:35")
    let none = try XCTUnwrap(BedtimeGoalModel(json: cases[0]))
    XCTAssertFalse(none.hasGoal)
    XCTAssertNil(none.stage)
    XCTAssertTrue(none.nights.allSatisfy { $0.target == nil })
  }

  /// Which view the page draws: the server's with the package off or in shadow, the phone's in primary or while a
  /// write is queued (and the package computes).
  func testShownFollowsTheModeAndQueuedWrites() throws {
    let db = try AppDatabase.inMemory()
    try serverDoc(db, night: "2026-10-20", settings: goal)
    try db.saveDoc(BedtimeGoal.localKey, json: Data(#"{"night": "2026-10-20", "target_bedtime": "00:00"}"#.utf8))
    try db.writer.read { XCTAssertFalse(try BedtimeGoal.shown($0).local) }
    let shadow = #"{"compute": {"packages": {"coach": {"state": "shadow", "kinds": {}}}}}"#
    try db.saveDoc(LocalCompute.stateDoc, json: Data(shadow.utf8))
    try db.writer.read { XCTAssertFalse(try BedtimeGoal.shown($0).local) }
    try BedtimeGoal.write(db, queue: OfflineQueue(db: db), body: ["target_bedtime": "00:00"], tz: syd)
    try db.writer.read { db in
      let s = try BedtimeGoal.shown(db)
      XCTAssertTrue(s.local)
      XCTAssertTrue(s.pending)
    }
    let primary = #"{"compute": {"packages": {"coach": {"state": "primary", "kinds": {}}}}}"#
    try db.saveDoc(LocalCompute.stateDoc, json: Data(primary.utf8))
    try db.writer.write { try $0.execute(sql: "DELETE FROM outbox") }
    try db.writer.read { XCTAssertTrue(try BedtimeGoal.shown($0).local) }
  }
}
