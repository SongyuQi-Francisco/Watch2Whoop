import GRDB
import XCTest
@testable import Watch2Whoop

// N3: Today, sleep detail, explain sheets, weigh-ins, the phone's own scores. Every value here is made up.

/// Answers by path (requests run in parallel, so the order is not fixed).
final class PathStub: URLProtocol {
  static var replies: [String: (Int, String)] = [:]
  static var paths: [String] = []
  private static let lock = NSLock()

  override class func canInit(with request: URLRequest) -> Bool { true }
  override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }

  override func startLoading() {
    let path = request.url?.path ?? ""
    Self.lock.lock()
    Self.paths.append(path)
    let r = Self.replies[path]
    Self.lock.unlock()
    guard let r else {
      client?.urlProtocol(self, didFailWithError: URLError(.notConnectedToInternet))
      return
    }
    let resp = HTTPURLResponse(url: request.url!, statusCode: r.0, httpVersion: "HTTP/1.1", headerFields: [:])!
    client?.urlProtocol(self, didReceive: resp, cacheStoragePolicy: .notAllowed)
    client?.urlProtocol(self, didLoad: Data(r.1.utf8))
    client?.urlProtocolDidFinishLoading(self)
  }

  override func stopLoading() {}

  static func client() -> APIClient {
    let c = URLSessionConfiguration.ephemeral
    c.protocolClasses = [PathStub.self]
    return APIClient(baseURL: URL(string: "https://body.example.com")!, token: { "example-token" },
                     session: URLSession(configuration: c), sleep: { _ in })
  }
}

final class TodayModelFullTests: XCTestCase {
  private func json(_ s: String) -> [String: Any] {
    (try? JSONSerialization.jsonObject(with: Data(s.utf8))) as? [String: Any] ?? [:]
  }

  private var inputs: TodayInputs {
    TodayInputs(
      today: json("""
        {"date": "2026-01-02", "user": {"timezone": "Europe/Example"},
         "scores": {"today": {"recovery": 1, "sleep_score": 0, "hrv_night_ms": 48.6, "rhr_night": 52, "sleep_actual_min": 400,
                              "sleep_need_min": 470, "sleep_debt_min": 70, "hrv_kind": "sdnn", "flags": {"hrv_missing": "x"}},
                    "yesterday": {"date": "2026-01-01", "strain": 9.96, "ctl": 40, "atl": 52, "tsb": -12},
                    "recovery_zone": "red", "recovery_word": "Rest", "load_status": {"key": "tired", "label": "Tired"}},
         "sleep": {"date": "2026-01-02"},
         "reminders": [{"id": 3, "title": "Seen one", "level": "info", "status": "seen"},
                       {"id": 4, "title": "Weigh in on 2026-01-05", "level": "alert", "status": "new", "date": "2026-01-02"}]}
        """),
      activity: json(#"{"date": "2026-01-02", "daily": {"steps": 12345, "active_kcal": 512.4, "exercise_min": 31}}"#),
      coach: json(#"{"target_low": 10, "target_high": 14, "current_strain": 6.5, "status": null, "remaining_to_low": 3.5}"#),
      monitor: json("""
        {"metrics": [{"key": "rhr", "value": 51, "unit": "bpm", "low": 53, "high": 62, "status": "low"},
                     {"key": "hrv", "value": null, "missing_reason": "none"}]}
        """),
      memory: json("""
        {"status": [{"key": "weight", "label": "Weight", "value": "80.5", "unit": "kg", "tone": "neutral", "trend": "down",
                     "good_direction": "down", "group": "Body", "spark": [81, null, 80.5]},
                    {"key": "bedtime", "label": "Bedtime", "value": "0:03", "tone": "warn", "group": "Sleep"}]}
        """),
      sync: json(#"{"state": "ok", "label": "Syncing normally", "minutes_ago": 125}"#),
      circles: json(#"{"members": [{"id": 2}, {"id": 3}], "has_circles": true}"#),
      userToday: "2026-01-02", circlesOn: true)
  }

  func testEveryBlockOfThePage() {
    let m = TodayModel(inputs)
    XCTAssertEqual(m.rings.map(\.value), ["0", "1", "10"])   // 0 and 1 are numbers, not booleans
    XCTAssertEqual(m.rings[1].tone, .bad)
    XCTAssertEqual(m.word, "Rest")
    XCTAssertEqual(m.loadChip, "Tired")
    XCTAssertEqual(m.sections.map(\.id), ["recovery", "sleep", "activity"])
    XCTAssertEqual(m.sections[0].metrics.map(\.value), ["49", "52"])
    XCTAssertEqual(m.sections[1].metrics.map(\.value), ["6:40", "7:50", "1:10"])
    XCTAssertEqual(m.sections[2].metrics.map(\.value), ["12,345", "512", "31"])
    XCTAssertEqual(m.coach?.current, 6.5)
    XCTAssertEqual(m.coach?.stateLabel, tr("差 3.5", "3.5 to go"))
    XCTAssertEqual(m.vitals.count, 2)
    XCTAssertEqual(m.vitalsOff, 1)
    XCTAssertEqual(m.vitals[0].explainKey, "resting_hr")
    XCTAssertEqual(m.status.map(\.name), ["Body", "Sleep"])
    XCTAssertEqual(m.status[0].items[0].trendTone, .good)
    XCTAssertEqual(m.status[1].items[0].tone, .mid)
    XCTAssertEqual(m.reminders.map(\.id), [4, 3])             // new first
    XCTAssertEqual(m.reminders[0].title, "Weigh in on 1/5")
    XCTAssertEqual(m.reminders[0].tone, .bad)
    XCTAssertEqual(m.sync?.tone, .good)
    XCTAssertEqual(m.sync?.ago, tr("2 小时前", "2 h ago"))
    XCTAssertEqual(m.circles?.members, 2)
    XCTAssertEqual(m.detail.tsb, -12)
    XCTAssertEqual(m.detail.flags, [tr("无 HRV", "No HRV")])
    XCTAssertEqual(m.timezone, "Europe/Example")
    XCTAssertFalse(m.isPast)
    XCTAssertFalse(m.local)
  }

  func testLookBackDayHidesTodayOnlyBlocks() {
    var inp = inputs
    inp.userToday = "2026-01-09"
    let m = TodayModel(inp)
    XCTAssertTrue(m.isPast)
    XCTAssertNil(m.sync)
    XCTAssertNil(m.circles)
    XCTAssertEqual(m.rings[2].label, tr("前一天负荷", "Day-before strain"))
  }

  func testPhoneScoresFillOnlyWhatTheServerHasNot() {
    let inp = TodayInputs(today: json(#"{"date": "2026-01-02", "scores": {"today": {"sleep_score": 77}, "yesterday": {}}}"#),
                          localDay: DailyScore(date: "2026-01-02", recovery: 70, sleepScore: 60, strain: nil, hrvMs: 44, rhr: 50,
                                               origin: "app", computedAt: 0),
                          localPrev: DailyScore(date: "2026-01-01", recovery: nil, sleepScore: nil, strain: 8.04, origin: "app", computedAt: 0),
                          userToday: "2026-01-02")
    let m = TodayModel(inp)
    XCTAssertEqual(m.rings.map(\.value), ["77", "70", "8"])   // the server's sleep score stays
    XCTAssertEqual(m.rings[1].tone, .good)
    XCTAssertTrue(m.local)
    XCTAssertEqual(m.sections[0].metrics[0].value, "44")
  }

  func testExplainModel() throws {
    let m = ExplainModel(json: json("""
      {"key": "hrv", "title": "HRV", "what": "What it is.", "current": {"value": 48, "unit": "ms", "basis": "7d", "tone": "good"},
       "facts": [{"label": "Baseline", "value": "39 ± 7 ms"}], "advice": [{"text": "Fine.", "tone": "good"}],
       "read": [{"range": "above", "label": "Good", "tone": "good"}], "recovery": "Weight 60%.", "tips": ["Sleep"],
       "exercises": {"banned": [{"name": "Dips", "reason": "shoulder"}], "caution": [{"name": "Row", "cues": "light"}]},
       "ai_prompt": "Explain my HRV"}
      """))
    XCTAssertEqual(m.currentValue, "48")
    XCTAssertEqual(m.currentTone, .good)
    XCTAssertEqual(m.facts.first?.1, "39 ± 7 ms")
    XCTAssertEqual(m.read.first?.tone, .good)
    XCTAssertEqual(m.avoid, ["Dips · shoulder"])
    XCTAssertEqual(m.caution, ["Row · light"])
    XCTAssertEqual(m.aiPrompt, "Explain my HRV")
    XCTAssertEqual(m.recoveryHead, tr("和恢复", "And recovery"))
  }

  func testSleepNightModel() {
    let m = SleepNightModel(json: json("""
      {"date": "2026-01-02", "prev": "2026-01-01", "next": null, "ai_prompt": "Why?",
       "main": {"start": "23:10", "end": "06:40", "in_bed_min": 450, "asleep_min": 420, "efficiency_pct": 93.3,
                "latency_min": 12, "awakenings": 0, "staged": true, "deep_min": 80, "rem_min": 90,
                "stages": [{"stage": "core", "offset_min": 0, "minutes": 30, "start": "23:10", "end": "23:40"},
                           {"stage": "unspecified", "offset_min": 30, "minutes": 5}],
                "composition": [{"stage": "deep", "min": 80, "pct": 19}]},
       "score": {"value": 84, "parts": [{"key": "duration", "score": 90, "weight": 0.4}], "need_min": 480},
       "baseline": {"avg7": {"asleep_min": 400, "bedtime": "23:30"}, "avg30": {}, "regularity_pct_7d": 88},
       "vitals": [{"key": "rhr", "value": 50, "unit": "bpm", "delta": 3.2}, {"key": "hrv", "value": 45, "unit": "ms", "delta": 0.2},
                  {"key": "resp_rate", "value": 14.2, "unit": "rpm", "delta": -0.4}]}
      """))
    XCTAssertTrue(m.hasMain)
    XCTAssertEqual(m.stages.count, 2)
    XCTAssertEqual(m.awakenings, 0)
    XCTAssertEqual(m.compare.first?.night, "7:00")
    XCTAssertEqual(m.compare.first?.avg7, "6:40")
    XCTAssertEqual(m.compare.map(\.label).last, tr("睡眠分", "Sleep score"))
    XCTAssertEqual(m.vitals.map(\.delta), ["+3", "±0", "-0.4"])
    XCTAssertEqual(m.vitals.map(\.worse), [true, false, false])
    XCTAssertEqual(m.regularity7, 88)
  }

  func testMonthHelpers() {
    XCTAssertEqual(MonthDays.addMonths("2026-01", -1), "2025-12")
    XCTAssertEqual(MonthDays.addMonths("2026-12", 1), "2027-01")
    XCTAssertEqual(MonthDays.leadingBlanks("2026-06"), 0)   // 1 June 2026 is a Monday
    XCTAssertEqual(MonthDays.leadingBlanks("2026-02"), 6)   // a Sunday
    XCTAssertEqual(LocalDay.add("2026-03-01", -1), "2026-02-28")
  }
}

final class TodayStorageTests: XCTestCase {
  func testSnapshotReadsTheDayOfTheCurrentDocument() throws {
    let db = try AppDatabase.inMemory()
    try db.saveDoc(DocKey.todayCurrent, json: Data(#"{"date": "2026-01-02", "scores": {}}"#.utf8))
    try db.saveDoc(TodayDocs.activity("2026-01-02"), json: Data(#"{"daily": {"steps": 10}}"#.utf8))
    try db.saveDoc(TodayDocs.activity("2026-01-01"), json: Data(#"{"daily": {"steps": 99}}"#.utf8))
    let s = try db.writer.read { try TodaySnapshot.read($0, date: nil) }
    XCTAssertEqual(s.day, "2026-01-02")
    XCTAssertNotNil(s.docs[TodayDocs.activity("2026-01-02")])
    XCTAssertNil(s.docs[TodayDocs.activity("2026-01-01")])
    let past = try db.writer.read { try TodaySnapshot.read($0, date: "2026-01-01") }
    XCTAssertNotNil(past.docs[TodayDocs.activity("2026-01-01")])
  }

  func testDocPullerSavesAndDropsForbidden() async throws {
    let db = try AppDatabase.inMemory()
    try db.saveDoc("a", json: Data("{}".utf8))
    try db.saveDoc("b", json: Data(#"{"old": true}"#.utf8))
    PathStub.replies = ["/x/a": (200, #"{"n": 1}"#), "/x/b": (403, #"{"detail": "no share"}"#)]
    let out = await DocPuller(api: PathStub.client(), db: db).pull([
      DocFetch("a", .get("/x/a")), DocFetch("b", .get("/x/b")), DocFetch("c", .get("/x/offline")),
    ])
    XCTAssertEqual(out.saved, ["a"])
    XCTAssertEqual(out.removed, ["b"])
    XCTAssertTrue(out.offline)
    XCTAssertEqual(try db.doc("a")?.json?["n"] as? Int, 1)
    XCTAssertNil(try db.doc("b"))
  }

  func testWeighInIsLocalFirstAndQueued() throws {
    let db = try AppDatabase.inMemory()
    let queue = OfflineQueue(db: db)
    var tags = WeightLog.toggle([], "clothed")
    tags = WeightLog.toggle(tags, "fasting")
    tags = WeightLog.toggle(tags, "unclothed")
    XCTAssertEqual(tags, ["fasting", "unclothed"])
    XCTAssertEqual(WeightLog.kg("80,46"), 80.5)
    XCTAssertNil(WeightLog.kg("12"))
    let body = WeightLog.body(day: "2026-01-02", kg: 80.5, tags: tags, note: "  ", clientId: "c-1", isToday: false)
    XCTAssertEqual(body["context_tags"] as? [String], ["fasting", "unclothed"])
    XCTAssertNil(body["measured_at"])
    let cid = try WeightLog.save(db, queue: queue, body: body)
    let item = try XCTUnwrap(queue.all().first)
    XCTAssertEqual(item.clientId, cid)
    XCTAssertEqual(item.path, "/v1/users/me/weight")
    let sent = try JSONSerialization.jsonObject(with: item.body!) as? [String: Any]
    XCTAssertEqual(sent?["client_id"] as? String, "c-1")
    let pending = try db.writer.read { try Bool.fetchOne($0, sql: "SELECT pending FROM records WHERE kind = 'weight' AND id = 'c:c-1'") }
    XCTAssertEqual(pending, true)
    WeightLog.markSent(db, clientId: cid)
    XCTAssertEqual(try db.writer.read { try Bool.fetchOne($0, sql: "SELECT pending FROM records WHERE id = 'c:c-1'") }, false)
  }
}

final class LocalScoringTests: XCTestCase {
  func testBuiltInBundleLoadsInJavaScriptCore() throws {
    let rt = try OpenBodyScoreRuntime.builtin()
    XCTAssertEqual(rt.version, "0.1.0")
    XCTAssertEqual(rt.algoVersions["scores"], "v1.2-2026-10-27")
    let hrv = try rt.invoke("analyzeHrv", [[0, 0.8, 1.62, 2.41, 3.25, 4.05], []]) as? [String: Any]
    XCTAssertNotNil(hrv?["algo_version"])
  }

  /// 40 made-up days on the phone (stages, heart rate every 10 minutes, nightly HRV, resting HR) → today's scores.
  func testScoresFromLocalRawSamples() throws {
    let db = try AppDatabase.inMemory()
    let tz = "Australia/Sydney", zone = TimeZone(identifier: tz)!
    var cal = Calendar(identifier: .gregorian)
    cal.timeZone = zone
    let today = "2026-01-30"
    let todayNoon = LocalDay.parse(today, zone)!
    try db.writer.write { db in
      var n = 0
      func sample(_ type: String, _ a: Date, _ b: Date, _ v: Double) throws {
        n += 1
        try db.execute(sql: "INSERT INTO hk_samples (uuid, type, start, end, value) VALUES (?, ?, ?, ?, ?)",
                       arguments: ["s\(n)", type, a.timeIntervalSince1970, b.timeIntervalSince1970, v])
      }
      for back in (0..<40).reversed() {
        let noon = todayNoon.addingTimeInterval(-Double(back) * 86_400)
        let wake = cal.date(bySettingHour: 7, minute: 0, second: 0, of: noon)!
        var t = wake.addingTimeInterval(-8 * 3600)
        let cycle: [(Int, Double)] = [(3, 30), (4, 35), (3, 20), (5, 25), (2, 5)]
        var k = 0
        while t < wake {
          let (stage, mins) = cycle[k % cycle.count]
          let e = min(wake, t.addingTimeInterval(mins * 60))
          try sample(LocalScoring.sleepAnalysis, t, e, Double(stage))
          t = e
          k += 1
        }
        let wiggle = Double((back * 7) % 5)
        for h in [1.0, 3.0, 5.0] { try sample("HKQuantityTypeIdentifierHeartRateVariabilitySDNN", wake.addingTimeInterval(-h * 3600), wake.addingTimeInterval(-h * 3600), 42 + wiggle) }
        try sample(LocalScoring.restingHR, wake.addingTimeInterval(3 * 3600), wake.addingTimeInterval(3 * 3600), 54 + wiggle / 2)
        var m = wake.addingTimeInterval(-9 * 3600)
        let end = back == 0 ? wake.addingTimeInterval(4 * 3600) : wake.addingTimeInterval(15 * 3600)
        while m < end {
          let hour = cal.component(.hour, from: m)
          let workout = back % 2 == 0 && hour == 18
          let hr = workout ? 150.0 : (hour < 7 ? 52.0 + wiggle : 74.0)
          try sample("HKQuantityTypeIdentifierHeartRate", m, m, hr)
          m = m.addingTimeInterval(600)
        }
      }
      // a server row two days back must stay as it is
      try DailyScore(date: "2026-01-28", recovery: 11, sleepScore: 22, strain: 3, origin: "server", computedAt: 0).save(db)
    }
    XCTAssertTrue(LocalScoring.hasRawData(db))
    let rt = try OpenBodyScoreRuntime.builtin()
    let out = try LocalScoring.run(db: db, runtime: rt, tz: tz, hrvKind: "sdnn", today: today, days: 3)
    XCTAssertNil(out.skipped)
    XCTAssertEqual(out.written, ["2026-01-29", "2026-01-30"])
    let row = try XCTUnwrap(try db.writer.read { try DailyScore.fetchOne($0, key: today) })
    XCTAssertEqual(row.origin, "app")
    XCTAssertEqual(row.algoVersion, rt.algoVersions["scores"])
    XCTAssertNotNil(row.sleepScore)
    XCTAssertNotNil(row.recovery, String(decoding: row.payload ?? Data(), as: UTF8.self))
    XCTAssertEqual(try db.writer.read { try DailyScore.fetchOne($0, key: "2026-01-28") }?.recovery, 11)
    // rmssd users need beat-to-beat series the phone does not keep yet
    XCTAssertNotNil(try LocalScoring.run(db: db, runtime: rt, tz: tz, hrvKind: "rmssd", today: today).skipped)
  }
}
