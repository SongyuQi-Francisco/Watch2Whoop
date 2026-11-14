import XCTest
@testable import Watch2Whoop

/// 2.0.1 phase 3 (docs/design/2.0.1 `05-body` … `08-sync-center`): the 身体 hero and faces, the sync dial and lanes, the
/// sync center's one button and one self-help step, 我's AI face, the circle dial. Every number is made up.
final class Phase3GlanceTests: XCTestCase {
  override func setUp() {
    super.setUp()
    Lang.override = "zh"
  }

  override func tearDown() {
    Lang.override = nil
    super.tearDown()
  }

  // MARK: - fixtures

  private func dates(_ n: Int, end: String = "2026-11-10") -> [String] {
    var out: [String] = []
    for i in stride(from: n - 1, through: 0, by: -1) { out.append(LocalDay.add(end, -i)) }
    return out
  }

  private func trends(_ series: [String: [String: Any]], days: Int) -> TrendsData {
    let json: [String: Any] = ["dates": dates(days), "series": series, "days": days]
    return TrendsData(json: json)!
  }

  private func values(_ xs: [Double?]) -> [Any] {
    var out: [Any] = []
    for x in xs { out.append(x.map { $0 as Any } ?? NSNull()) }
    return out
  }

  // MARK: - 身体 hero

  func testHeroMeanDeltaAndBars() {
    var xs: [Double?] = Array(repeating: 60, count: 29)
    xs.append(90)
    let d = trends(["recovery": ["values": values(xs), "prev_mean": 58.0]], days: 30)
    let h = BodyGlanceRules.hero(d, score: .recovery, range: .month)
    XCTAssertEqual(h.mean ?? 0, 61, accuracy: 0.001)
    XCTAssertEqual(h.delta, 3)
    XCTAssertEqual(h.deltaTone, .good)
    XCTAssertEqual(h.bars.count, 30)
    XCTAssertEqual(h.lastTone, .good)   // 90 is above the 67 zone edge
    XCTAssertEqual(h.dates.count, 3)
    XCTAssertEqual(h.dates.first, "10/12")
    XCTAssertEqual(h.dates.last, "11/10")
  }

  func testHeroOnTodayRangeHasNoDelta() {
    let xs: [Double?] = Array(repeating: 70, count: 61)
    let d = trends(["sleep_score": ["values": values(xs), "prev_mean": 50.0]], days: 61)
    let h = BodyGlanceRules.hero(d, score: .sleep, range: .today)
    XCTAssertEqual(h.bars.count, 30)
    XCTAssertNil(h.delta)
    XCTAssertEqual(h.lastTone, .sleep)
  }

  func testHeroWithoutReadings() {
    let xs: [Double?] = Array(repeating: nil, count: 7)
    let d = trends(["strain": ["values": values(xs)]], days: 7)
    let h = BodyGlanceRules.hero(d, score: .strain, range: .week)
    XCTAssertNil(h.mean)
    XCTAssertNil(h.delta)
    XCTAssertEqual(h.deltaTone, .text)
  }

  func testLongRangesBecomeWeeklyBars() {
    var xs: [Double?] = []
    for i in 0..<90 { xs.append(Double(i)) }
    let b = BodyGlanceRules.bucketed(xs)
    XCTAssertEqual(b.count, 13)
    // the newest week ends on the last day: 83 … 89
    XCTAssertEqual(b.last ?? nil, 86)
    XCTAssertEqual(BodyGlanceRules.bucketed(Array(xs.prefix(30))).count, 30)
  }

  func testStrainChangeTakesNoColour() {
    XCTAssertEqual(BodyGlanceRules.deltaTone(2, better: 0), .text)
    XCTAssertEqual(BodyGlanceRules.deltaTone(-2, better: 1), .bad)
    XCTAssertEqual(BodyGlanceRules.signed(-0.4, digits: 1), "−0.4")
    XCTAssertEqual(BodyGlanceRules.signed(3, digits: 0), "+3")
  }

  func testTrainingDaysOfTheWeek() {
    let aerobic: [Double?] = [0, 30, nil, 0, 0, 45, 0]
    let muscle: [Double?] = [1200, 0, nil, 0, 800, 0, 0]
    let d = trends(["aerobic_min": ["values": values(aerobic)], "muscle_load": ["values": values(muscle)]], days: 7)
    let days = BodyGlanceRules.trainingDays(d)
    XCTAssertEqual(days, [true, true, nil, false, true, true, false])
  }

  func testHRVFaceAndAgeGauge() {
    var xs: [Double?] = Array(repeating: 55, count: 29)
    xs.append(68)
    let d = trends(["hrv": ["values": values(xs)]], days: 30)
    let h = BodyGlanceRules.hrv(d)
    XCTAssertEqual(h?.last, 68)
    XCTAssertEqual(h?.values.count, 30)
    let p = BodyGlanceRules.agePositions(body: 29.4, chrono: 31.4)
    XCTAssertEqual(p.chrono, 0.5)
    XCTAssertLessThan(p.body, 0.5)
    XCTAssertEqual(BodyGlanceRules.agePositions(body: 60, chrono: 30).body, 1)
  }

  // MARK: - sync dial and lanes

  private func missing(_ days: [(String, [String])], since: String? = "2026-01-01") -> MissingDays {
    var dayList: [[String: Any]] = []
    for (d, k) in days { dayList.append(["date": d, "kinds": k]) }
    var j: [String: Any] = ["kinds": [["key": "heart_rate", "label": "心率"], ["key": "sleep", "label": "睡眠"]],
                            "days": dayList, "window_days": 30, "end": "2026-11-09"]
    if let since { j["since"] = since }
    return MissingDays(j)
  }

  func testSyncDaysCountsMissingAndLanes() {
    let m = missing([("2026-11-09", ["sleep"]), ("2026-11-08", ["heart_rate", "sleep"])])
    let d = SyncDays.of(m, fallbackEnd: "2026-11-01")
    XCTAssertEqual(d.days.count, 30)
    XCTAssertEqual(d.days.last, "2026-11-09")   // the server's end wins over the phone's
    XCTAssertEqual(d.missing, 2)
    XCTAssertEqual(d.synced, 28)
    XCTAssertEqual(d.lanes.count, 2)
    XCTAssertEqual(d.lanes[0].marks.count, 14)
    XCTAssertEqual(d.lanes[0].marks.last, .ok)          // heart rate is there on the 9th
    XCTAssertEqual(d.lanes[1].marks.last, .missing)     // sleep is not
    XCTAssertEqual(d.laneDays.first, "2026-10-27")
    XCTAssertEqual(SyncGlanceRules.verdict(d), "可补传")
  }

  func testSyncDaysBeforeTheFirstData() {
    let m = missing([], since: "2026-11-05")
    let d = SyncDays.of(m, fallbackEnd: "2026-11-09")
    XCTAssertEqual(d.counted, 5)
    XCTAssertEqual(d.missing, 0)
    XCTAssertEqual(SyncGlanceRules.verdict(d), "已同步")
    let none = SyncDays.of(missing([], since: nil), fallbackEnd: "2026-11-09")
    XCTAssertEqual(none.counted, 0)
    XCTAssertEqual(SyncGlanceRules.verdict(none), "没有数据")
  }

  func testPrimaryButtonAndHelpStep() {
    let offline = SyncDiagnosis.Issue(id: "offline", text: "x", action: .retry)
    let bg = SyncDiagnosis.Issue(id: "background", text: "y", action: .openSettings)
    XCTAssertEqual(SyncGlanceRules.primary(permission: .notAsked, issues: [offline]), .turnOn)
    XCTAssertEqual(SyncGlanceRules.primary(permission: .asked, issues: [offline]), .retry)
    XCTAssertEqual(SyncGlanceRules.primary(permission: .asked, issues: []), .syncNow)
    // the retry is the primary button; the hint is the one self-help step
    XCTAssertEqual(SyncGlanceRules.helpStep(issues: [offline], hints: [bg]), bg)
    XCTAssertEqual(SyncGlanceRules.shortLabel(bg), "后台刷新已关")
    XCTAssertNil(SyncGlanceRules.helpStep(issues: [offline], hints: []))
  }

  // MARK: - 我

  func testAIFace() throws {
    let choice = try JSONDecoder().decode(AIChoice.self, from: Data("""
      {"options": [{"key": "fallback", "label": "Jarvis", "current": true}, {"key": "p1", "label": "Claude"}]}
      """.utf8))
    let list = try JSONDecoder().decode(AIProviderList.self, from: Data("""
      {"providers": [], "fallback": {"display_name": "Jarvis", "remaining_pct": 40}}
      """.utf8))
    let f = MeAIFace.of(choice: choice, list: list)
    XCTAssertEqual(f?.fallback, true)
    XCTAssertEqual(f?.remainingPct, 40)
    let own = try JSONDecoder().decode(AIChoice.self, from: Data("""
      {"options": [{"key": "p1", "label": "Claude", "current": true}]}
      """.utf8))
    XCTAssertEqual(MeAIFace.of(choice: own, list: list), MeAIFace(name: "Claude", fallback: false, remainingPct: nil))
    XCTAssertNil(MeAIFace.of(choice: nil, list: nil))
  }

  // MARK: - 圈子

  func testCircleDialPlacesAndSpreadsPeople() throws {
    let o = try JSONDecoder().decode(MemberOverview.self, from: Data("""
      {"has_circles": true,
       "me": {"id": 1, "display_name": "小林", "trained": true, "scores": {"recovery": 70, "sleep_score": 80}},
       "members": [
         {"id": 2, "display_name": "A", "shared": ["scores"], "trained": true, "scores": {"recovery": 71, "sleep_score": 60}},
         {"id": 3, "display_name": "B", "shared": ["scores"], "trained": false, "scores": {"recovery": 20}},
         {"id": 4, "display_name": "C", "shared": [], "trained": false}
       ]}
      """.utf8))
    let people = CircleGlanceRules.people(o)
    XCTAssertEqual(people.count, 4)
    let t = CircleGlanceRules.trained(people)
    XCTAssertEqual(t.trained, 2)
    XCTAssertEqual(t.total, 4)
    let dial = CircleGlanceRules.dial(people)
    XCTAssertEqual(dial.map(\.id), [3, 1, 2])   // C shares no recovery: not on the dial
    XCTAssertEqual(dial[1].at, 0.70, accuracy: 0.0001)
    XCTAssertEqual(dial[2].at, 0.70 + CircleGlanceParams.minGap, accuracy: 0.0001)   // pushed apart
    XCTAssertTrue(dial[1].isSelf)
    XCTAssertEqual(CircleGlanceRules.mean(people.map { $0.scores?.sleepScore }) ?? 0, 70, accuracy: 0.001)
  }

  func testMyRank() throws {
    let b = try JSONDecoder().decode(Leaderboard.self, from: Data("""
      {"board": "body", "rows": [
        {"id": 2, "display_name": "A", "status": "ok", "score": 90, "rank": 1},
        {"id": 1, "display_name": "小林", "self": true, "status": "ok", "score": 80, "rank": 2},
        {"id": 3, "display_name": "B", "status": "insufficient"}
      ]}
      """.utf8))
    let r = CircleGlanceRules.myRank(b)
    XCTAssertEqual(r?.rank, 2)
    XCTAssertEqual(r?.of, 2)
    let bars = CircleGlanceRules.rankBars(b)
    XCTAssertEqual(bars.count, 2)
    XCTAssertEqual(bars[1].mine, true)
  }
}
