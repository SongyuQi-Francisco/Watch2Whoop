import GRDB
import XCTest
@testable import Watch2Whoop

/// The Today tab's details against the page (stress, sleep coach, body age, the empty ring's next step, a night's notes,
/// the day buttons, Trends' folds). Every value here is made up.
final class TodayDetailsTests: XCTestCase {
  override func setUp() {
    super.setUp()
    Lang.override = "zh"
  }

  override func tearDown() {
    Lang.override = nil
    super.tearDown()
  }

  private func state(_ package: String, _ mode: String) -> Data {
    Data(#"{"compute": {"packages": {"\#(package)": {"state": "\#(mode)", "kinds": {}}}}}"#.utf8)
  }

  // MARK: - stress

  static let stressRow: [String: Any] = [
    "date": "2026-03-31", "score": 1.4, "level": "medium", "low_h": 5, "medium_h": 3, "high_h": 1, "peak_hour": 14,
    "hours": [["hour": 9, "score": 0.5] as [String: Any], ["hour": 14, "score": 2.4] as [String: Any],
              ["hour": 15, "score": NSNull()] as [String: Any]],
    "baseline": ["calm_hr": 61.0], "flags": [String: Any](),
  ]

  func testStressRowParsesHoursLevelsPeakAndCalmHeartRate() throws {
    let m = try XCTUnwrap(StressDayModel(json: Self.stressRow))
    XCTAssertEqual(m.score, 1.4)
    XCTAssertEqual(m.levelLabel, "中")
    XCTAssertEqual(m.tone, .mid)
    XCTAssertEqual(m.hours.map(\.hour), [9, 14], "an hour without a score is left out")
    XCTAssertEqual(m.curve.count, 24)
    XCTAssertEqual(m.curve[14], 2.4)
    XCTAssertNil(m.curve[15] ?? nil)
    XCTAssertEqual(m.totalHours, 9)
    XCTAssertEqual(m.peakHour, 14)
    XCTAssertEqual(m.calmHr, 61)
    XCTAssertNil(m.reason)
  }

  func testStressWithoutScoreSaysWhy() throws {
    let m = try XCTUnwrap(StressDayModel(json: ["date": "2026-03-31", "score": NSNull(), "flags": ["no_raw_hr": "x"]]))
    XCTAssertNil(m.score)
    XCTAssertEqual(m.reason, "无心率")
  }

  /// Primary with the phone's row: the row, and no reason to ask the server. Shadow: the server's document, as section 3.1 says.
  func testStressReadsThePhonesRowOnlyInPrimary() throws {
    let db = try AppDatabase.inMemory()
    let day = "2026-03-31"
    try db.writer.write { db in
      try db.execute(sql: """
        INSERT INTO stress_daily (date, score, level, low_h, medium_h, high_h, peak_hour, hours, baseline, flags, n_hr, algo_version,
                                  bundle_version, computed_at)
        VALUES (?, 2.2, 'high', 1, 2, 3, 20, ?, ?, ?, 100, 'a', 'b', 0)
        """, arguments: [day, Data(#"[{"hour": 20, "score": 2.2}]"#.utf8), Data(#"{"calm_hr": 58}"#.utf8), Data("{}".utf8)])
    }
    let server = ["days": [Self.stressRow]]
    try db.saveDoc(StressDetail.docKey(day), json: try JSONSerialization.data(withJSONObject: server))

    try db.saveDoc(LocalCompute.stateDoc, json: state("stress", "shadow"))
    var r = try db.writer.read { try StressDetail.read($0, day: day) }
    XCTAssertFalse(r.local)
    XCTAssertEqual(r.json.flatMap(StressDayModel.init(json:))?.score, 1.4)

    try db.saveDoc(LocalCompute.stateDoc, json: state("stress", "primary"))
    r = try db.writer.read { try StressDetail.read($0, day: day) }
    XCTAssertTrue(r.local)
    let local = try XCTUnwrap(r.json.flatMap(StressDayModel.init(json:)))
    XCTAssertEqual(local.score, 2.2)
    XCTAssertEqual(local.levelLabel, "高")
    XCTAssertEqual(local.calmHr, 58)
    XCTAssertEqual(local.peakHour, 20)

    // primary without a row for the day: the server's copy
    r = try db.writer.read { try StressDetail.read($0, day: "2026-03-30") }
    XCTAssertFalse(r.local)
    XCTAssertNil(r.body)
  }

  // MARK: - sleep coach

  func testSleepCoachLineFromTheCoachDocument() throws {
    let m = try XCTUnwrap(SleepCoachModel(json: ["need_min": 490, "wake_time": "08:30", "recommended_bedtime": "00:15",
                                                 "shortfall_min": 20, "sleep_debt_min": 45, "source": "goal"]))
    XCTAssertEqual(m.bedtime, "00:15")
    XCTAssertTrue(m.fromGoal)
    XCTAssertEqual(m.line, "需要 8:10 · 08:30 起 · 差 0:20")
    Lang.override = "en"
    XCTAssertEqual(m.line, "Need 8:10 · up at 08:30 · short 0:20")
  }

  func testSleepCoachWithoutShortfallOrAnything() throws {
    let m = try XCTUnwrap(SleepCoachModel(json: ["need_min": 480, "wake_time": NSNull(), "recommended_bedtime": NSNull(), "shortfall_min": NSNull()]))
    XCTAssertEqual(m.line, "需要 8:00")
    XCTAssertNil(SleepCoachModel(json: ["date": "2026-03-31"]))
    XCTAssertNil(SleepCoachModel(json: nil))
  }

  func testTodayModelCarriesTheCoachAndTheEmptyRingsNextStep() {
    let scores: [String: Any] = [
      "today": [String: Any](), "yesterday": [String: Any](),
      "recovery_empty": ["code": "no_sleep", "label": "没开睡眠追踪", "cta": "开启手表睡眠追踪", "action": "sleep_help"],
    ]
    let doc: [String: Any] = ["date": "2026-03-31", "scores": scores]
    var inp = TodayInputs(today: doc)
    inp.sleepCoach = ["need_min": 480, "recommended_bedtime": "23:40"]
    let m = TodayModel(inp)
    XCTAssertEqual(m.recoveryNext?.action, .sleepHelp)
    XCTAssertEqual(m.recoveryNext?.label, "开启手表睡眠追踪")
    XCTAssertEqual(m.tonight?.bedtime, "23:40")
  }

  func testNextStepOnlyForAnEmptyRingWithSomethingToDo() {
    XCTAssertNil(RecoveryNext(["code": "pending", "label": "还没算", "cta": NSNull(), "action": NSNull()]))
    XCTAssertEqual(RecoveryNext(["action": "consent", "cta": "去确认"])?.action, .consent)
    XCTAssertEqual(RecoveryNext(["action": "sync", "cta": "去同步"])?.action, .sync)
    let scored: [String: Any] = ["today": ["recovery": 70], "recovery_empty": ["action": "sync", "cta": "去同步"]]
    let withScore: [String: Any] = ["date": "2026-03-31", "scores": scored]
    XCTAssertNil(TodayModel(TodayInputs(today: withScore)).recoveryNext)
  }

  // MARK: - body age

  static let contributors: [[String: Any]] = [
    ["key": "sleep_duration", "label": "平均睡眠时长", "category": "sleep", "value": 7.25, "unit": "h", "years": 0.6],
    ["key": "steps", "label": "日均步数", "category": "activity", "value": 8421, "unit": "步", "years": -1.2],
    ["key": "strength", "label": "每周力量训练", "category": "activity", "value": 120, "unit": "分钟", "years": -0.4,
     "coverage": "possibly_incomplete"],
    ["key": "body_comp", "label": "身体成分", "category": "fitness", "value": 0.456, "measure": "waist_to_height", "years": 0],
  ]

  func testBodyAgeGroupsContributorsByDomain() {
    let m = BodyAgeModel(json: ["body_age": 30.5, "chrono_age": 32.0, "delta_years": -1.5, "pace_of_aging": 0.8,
                                "history": [["as_of": "2026-03-22", "body_age": 31.0] as [String: Any],
                                            ["as_of": "2026-03-29", "body_age": 30.5] as [String: Any],
                                            ["as_of": "2026-03-15", "body_age": NSNull()] as [String: Any]],
                                "contributors": Self.contributors,
                                "insufficient": [["key": "vo2max", "label": "VO₂max", "category": "fitness", "reason": "x"]]])
    XCTAssertEqual(m.groups.map(\.id), ["sleep", "activity", "fitness"])
    XCTAssertEqual(m.groups.map(\.title), ["睡眠", "活动", "体能"])
    XCTAssertEqual(m.groups[1].items.map(\.key), ["steps", "strength"])
    XCTAssertEqual(m.groups[1].items[0].value, "8,421 步")
    XCTAssertEqual(m.groups[1].items[0].tone, .good)
    XCTAssertEqual(m.groups[0].items[0].value, "7.3 h")
    XCTAssertEqual(m.groups[0].items[0].tone, .bad)
    XCTAssertTrue(m.groups[1].items[1].partial)
    XCTAssertEqual(m.groups[2].items[0].value, "腰围身高比 0.46")
    XCTAssertEqual(m.history.map(\.date), ["2026-03-22", "2026-03-29"], "a week without a body age is left out")
    XCTAssertEqual(m.insufficient, ["VO₂max"])
    XCTAssertEqual(m.scale, 1.2)
    XCTAssertEqual(BodyAgeModel.paceTone(m.pace), .good)
    XCTAssertEqual(BodyAgeModel.paceTone(1.2), .bad)
    XCTAssertEqual(BodyAgeModel.paceTone(1.0), .text)
    XCTAssertEqual(BodyAgeModel.signed(-1.5), "−1.5")
    XCTAssertEqual(BodyAgeModel.signed(4), "+4.0")
  }

  /// The phone's rows keep the bundle's Chinese labels: English by key.
  func testBodyAgeLabelsFollowTheLanguage() {
    Lang.override = "en"
    let m = BodyAgeModel(json: ["contributors": Self.contributors])
    XCTAssertEqual(m.groups.map(\.title), ["Sleep", "Activity", "Fitness"])
    XCTAssertEqual(m.groups[1].items[0].label, "Daily steps")
    XCTAssertEqual(m.groups[1].items[0].value, "8,421 steps")
    XCTAssertEqual(m.groups[2].items[0].value, "Waist-to-height 0.46")
  }

  func testBodyAgeReadsThePhonesWeeksOnlyInPrimary() throws {
    let db = try AppDatabase.inMemory()
    let list = try JSONSerialization.data(withJSONObject: Self.contributors)
    try db.writer.write { db in
      for (w, a) in [("2026-03-16", 31.2), ("2026-03-23", 30.9)] {
        try db.execute(sql: """
          INSERT INTO healthspan_weekly (week_start, as_of, chrono_age, body_age, delta_years, pace_of_aging, contributors, insufficient,
                                         algo_version, bundle_version, computed_at)
          VALUES (?, ?, 32.0, ?, -1.1, 0.9, ?, '[]', 'a', 'b', 0)
          """, arguments: [w, LocalDay.add(w, 6), a, list])
      }
    }
    let server = #"{"body_age": 40.0, "contributors": [], "history": []}"#
    try db.saveDoc(BodyAgeDetail.docKey, json: Data(server.utf8))

    try db.saveDoc(LocalCompute.stateDoc, json: state("coach", "shadow"))
    var r = try db.writer.read { try BodyAgeDetail.read($0) }
    XCTAssertFalse(r.local)
    XCTAssertEqual(r.json.map(BodyAgeModel.init(json:))?.bodyAge, 40)

    try db.saveDoc(LocalCompute.stateDoc, json: state("coach", "primary"))
    r = try db.writer.read { try BodyAgeDetail.read($0) }
    XCTAssertTrue(r.local)
    let m = try XCTUnwrap(r.json.map(BodyAgeModel.init(json:)))
    XCTAssertEqual(m.bodyAge, 30.9)
    XCTAssertEqual(m.history.map(\.age), [31.2, 30.9], "oldest to newest")
    XCTAssertEqual(m.groups.count, 3)
  }

  /// Jarvis' 「打开身体年龄」 opens the detail, the others keep their tab.
  @MainActor func testJarvisOpensTheBodyAgeDetail() {
    let router = JarvisRouter()
    var tab: AppTab?
    router.selectTab = { tab = $0 }
    router.open(page: "healthspan")
    XCTAssertEqual(router.sheet, .bodyAge)
    XCTAssertEqual(tab, .today)
    router.open(page: "trends")
    XCTAssertNil(router.sheet)
    XCTAssertEqual(tab, .trends)
  }

  // MARK: - a night's notes

  func testSleepNightParsesItsNotes() {
    let m = SleepNightModel(json: ["date": "2026-03-31", "notes": [
      ["key": "short_of_need", "tone": "bad", "text": "比睡眠需求少 1h"], ["key": "need_met", "tone": "good", "text": "睡够了需求"],
      ["tone": "x"],
    ]])
    XCTAssertEqual(m.notes.map(\.key), ["short_of_need", "need_met"])
    XCTAssertEqual(m.notes.map(\.tone), [.bad, .good])
    XCTAssertNotEqual(m, SleepNightModel(json: ["date": "2026-03-31"]), "a change in the notes redraws")
  }

  // MARK: - Trends: 180 days and folds

  func testTrendRangeHasSixMonths() {
    XCTAssertEqual(TrendRange(rawValue: 180), .halfYear)
    XCTAssertEqual(TrendRange.halfYear.docDays, 180)
    XCTAssertEqual(TrendRange.allCases.map(\.rawValue), [1, 7, 30, 90, 180, 365])
  }

  func testFoldedCategoriesRoundTrip() {
    var raw = ""
    XCTAssertFalse(TrendsFold.contains(raw, "sleep"))
    raw = TrendsFold.toggled(raw, "sleep")
    raw = TrendsFold.toggled(raw, "long")
    XCTAssertTrue(TrendsFold.contains(raw, "sleep"))
    XCTAssertTrue(TrendsFold.contains(raw, "long"))
    XCTAssertFalse(TrendsFold.contains(raw, "stress"))
    raw = TrendsFold.toggled(raw, "sleep")
    XCTAssertFalse(TrendsFold.contains(raw, "sleep"))
    XCTAssertEqual(raw, "long")
  }

  func testFoldedHeaderShowsItsCategorysStatus() throws {
    let hrv: [String: Any] = ["status": "balanced", "label": "平衡", "avg7_ms": 50.0, "band_ms": [40.0, 60.0], "nights": 7]
    let status = TrendStatus(status: ["hrv_status": hrv], local: [])
    let s = try XCTUnwrap(TrendsFold.summary("recovery", status))
    XCTAssertEqual(s.text, "平衡")
    XCTAssertEqual(s.tone, .good)
    XCTAssertNil(TrendsFold.summary("long", status))
    XCTAssertNil(TrendsFold.summary("strain", status))
  }

  func testBodyAgeCardOpensItsOwnSheet() {
    let d = TodayCoreTests.page(TodayCoreTests.doc(series: ["body_age": TodayCoreTests.series(TodayCoreTests.steady(30, spread: 0.1, day: 30))]))
    let page = TrendsPage(data: d, local: nil, vo2: nil, sleep: nil)
    let card = TrendCards.groups(page).first { $0.id == "long" }?.cards.first { $0.id == "body_age" }
    XCTAssertEqual(card?.sheet?.id, "bodyAge")
  }

  // MARK: - day buttons

  func testStepStopsAtTheUsersToday() {
    XCTAssertEqual(TodayStore.stepTarget(from: nil, today: "2026-03-31", delta: -1), "2026-03-30", "from today")
    XCTAssertEqual(TodayStore.stepTarget(from: "2026-03-30", today: "2026-03-31", delta: 1), "2026-03-31")
    XCTAssertNil(TodayStore.stepTarget(from: nil, today: "2026-03-31", delta: 1), "never past today")
    XCTAssertEqual(TodayStore.stepTarget(from: "2026-03-01", today: "2026-03-31", delta: -1), "2026-02-28", "across a month")
    XCTAssertNil(TodayStore.stepTarget(from: nil, today: nil, delta: -1), "today not known yet")
  }
}
