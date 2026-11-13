import XCTest
@testable import Watch2Whoop

/// 2.0.1 Today (docs/design/2.0.1): the tab bar, the dial's numbers (usual range, verdict, empty state), the readings,
/// the L1 rows and the sync capsule's states. Every number is made up.
final class TodayGlanceTests: XCTestCase {
  override func setUp() {
    super.setUp()
    Lang.override = "zh"
  }

  override func tearDown() {
    Lang.override = nil
    super.tearDown()
  }

  // MARK: - tab bar

  func testFiveTabsWithCirclesFourWithout() {
    XCTAssertEqual(AppTab.visible(circles: true), [.today, .trends, .record, .circles, .me])
    XCTAssertEqual(AppTab.visible(circles: false), [.today, .trends, .record, .me])
    XCTAssertFalse(AppTab.visible(circles: true).contains(.food))   // 饮食 is a segment of 记录
    XCTAssertEqual(AppTab.trends.title, "身体")
    XCTAssertEqual(AppTab.circles.title, "圈子")
    XCTAssertEqual(AppTab.trends.icon, .activity)
    XCTAssertEqual(AppTab.food.landing(circles: true), .record)
    XCTAssertEqual(AppTab.circles.landing(circles: false), .today)
  }

  // MARK: - rules

  func testUsualNeedsTwoWeeksAndLeavesTodayOut() throws {
    let thirteen: [Double?] = (0..<13).map { Double(60 + $0) } + [90]
    XCTAssertNil(TodayGlanceRules.usual(thirteen))   // first two weeks: no usual line
    let days: [Double?] = (0..<30).map { Double(50 + $0) } + [99]   // 50…79, then today
    let u = try XCTUnwrap(TodayGlanceRules.usual(days))
    XCTAssertEqual(u.lowerBound, 57.25, accuracy: 1e-9)
    XCTAssertEqual(u.upperBound, 71.75, accuracy: 1e-9)
    XCTAssertEqual(TodayGlanceRules.usualMid(days)!, 64.5, accuracy: 1e-9)
    // gaps do not count as days
    let gaps: [Double?] = (0..<30).map { $0 % 3 == 0 ? Double($0) : nil } + [1]
    XCTAssertNil(TodayGlanceRules.usual(gaps))
  }

  func testPercentile() {
    XCTAssertEqual(TodayGlanceRules.percentile([1, 2, 3, 4, 5], 0.5), 3)
    XCTAssertEqual(TodayGlanceRules.percentile([10, 20], 0.25), 12.5)
    XCTAssertEqual(TodayGlanceRules.percentile([7], 0.75), 7)
    XCTAssertEqual(TodayGlanceRules.percentile([], 0.5), 0)
  }

  func testDialAndGaugeFractionsClamp() {
    XCTAssertEqual(TodayGlanceRules.dialFraction(82), 0.82, accuracy: 1e-9)
    XCTAssertEqual(TodayGlanceRules.dialFraction(nil), 0)
    XCTAssertEqual(TodayGlanceRules.dialFraction(130), 1)
    XCTAssertEqual(TodayGlanceRules.dialFraction(-5), 0)
    XCTAssertEqual(TodayGlanceRules.fraction(10.5, max: 21), 0.5, accuracy: 1e-9)
    XCTAssertEqual(TodayGlanceRules.fraction(30, max: 21), 1)
    XCTAssertEqual(TodayGlanceRules.fraction(3, max: 0), 0)
  }

  func testVerdictPrefersTheServersWord() {
    XCTAssertEqual(TodayGlanceRules.verdict(server: "可上强度", value: 20), "可上强度")
    XCTAssertEqual(TodayGlanceRules.verdict(server: nil, value: 82), "可上强度")
    XCTAssertEqual(TodayGlanceRules.verdict(server: "", value: 50), "按计划练")
    XCTAssertEqual(TodayGlanceRules.verdict(server: nil, value: 20), "以恢复为主")
    XCTAssertNil(TodayGlanceRules.verdict(server: nil, value: nil))
  }

  func testEmptyReasonIsShort() {
    XCTAssertEqual(TodayGlanceRules.reason(flags: ["no_sleep": true], emptyLabel: "x"), "昨晚没戴表")
    XCTAssertEqual(TodayGlanceRules.reason(flags: ["hrv_missing": true], emptyLabel: nil), "缺 HRV")
    XCTAssertEqual(TodayGlanceRules.reason(flags: nil, emptyLabel: "待同步"), "待同步")
    XCTAssertEqual(TodayGlanceRules.reason(flags: nil, emptyLabel: "这是一段很长的说明句子"), "暂无评分")
    XCTAssertNil(TodayGlanceRules.reason(flags: nil, emptyLabel: nil))
  }

  func testDeltaIsSignedAgainstYourUsual() {
    XCTAssertEqual(TodayGlanceRules.delta(68, mid: 62, digits: 0), "+6")
    XCTAssertEqual(TodayGlanceRules.delta(51, mid: 53, digits: 0), "−2")
    XCTAssertEqual(TodayGlanceRules.delta(14.2, mid: 14.1, digits: 1), "+0.1")
    XCTAssertEqual(TodayGlanceRules.delta(60.2, mid: 60, digits: 0), "±0")
    XCTAssertNil(TodayGlanceRules.delta(60, mid: nil, digits: 0))
    XCTAssertEqual(TodayGlanceRules.signed(4.4), "+4")
    XCTAssertEqual(TodayGlanceRules.signed(-12), "−12")
    XCTAssertEqual(TodayGlanceRules.signed(0.2), "0")
  }

  func testDeltaToneOnlyOutsideYourUsual() {
    let u = 54.0...64.0
    XCTAssertEqual(TodayGlanceRules.deltaTone(68, usual: u, better: 1), .good)    // HRV above
    XCTAssertEqual(TodayGlanceRules.deltaTone(50, usual: u, better: 1), .mid)
    XCTAssertEqual(TodayGlanceRules.deltaTone(60, usual: u, better: 1), .text)
    XCTAssertEqual(TodayGlanceRules.deltaTone(51, usual: 52...55, better: -1), .good)   // resting HR below
    XCTAssertEqual(TodayGlanceRules.deltaTone(14.9, usual: 13.9...14.4, better: 0), .text)   // breathing: ink
    XCTAssertEqual(TodayGlanceRules.deltaTone(70, usual: nil, better: 1), .text)
  }

  func testTailPadsAtTheFront() {
    XCTAssertEqual(TodayGlanceRules.tail([1, 2], 4), [nil, nil, 1, 2])
    XCTAssertEqual(TodayGlanceRules.tail([1, 2, 3, 4, 5], 3), [3, 4, 5])
  }

  func testVitals() {
    func v(_ id: String, _ x: Double?, off: Bool) -> TodayModel.Vital {
      TodayModel.Vital(id: id, name: id, short: id, value: x, unit: nil, low: 50, high: 60, off: off, missing: nil, explainKey: nil)
    }
    let list = [v("a", 55, off: false), v("b", 65, off: true), v("c", nil, off: false)]
    XCTAssertEqual(TodayGlanceRules.vitalsCount(list).ok, 1)
    XCTAssertEqual(TodayGlanceRules.vitalsCount(list).total, 2)
    XCTAssertEqual(TodayGlanceRules.vitalPosition(list[0])!, 0.5, accuracy: 1e-9)
    XCTAssertEqual(TodayGlanceRules.vitalPosition(list[1])!, 1.1, accuracy: 1e-9)   // clamped a little outside
    XCTAssertNil(TodayGlanceRules.vitalPosition(list[2]))
  }

  // MARK: - sync capsule

  func testSyncCapsuleStates() {
    let ok = TodayModel.SyncCard(state: "ok", label: "", tone: .good, ago: "2 分钟前")
    XCTAssertEqual(SyncCapsuleState.of(overall: .ok, card: ok, missingDays: nil), .ok(ago: "2 分钟前"))
    XCTAssertEqual(SyncCapsuleState.of(overall: .syncing, card: ok, missingDays: nil), .busy)
    XCTAssertEqual(SyncCapsuleState.of(overall: .attention, card: ok, missingDays: 2), .behind("缺2天"))
    let bad = TodayModel.SyncCard(state: "issue", label: "", tone: .bad, ago: "6 小时前", issueCode: "x")
    XCTAssertEqual(SyncCapsuleState.of(overall: .ok, card: bad, missingDays: nil), .failed)
    XCTAssertEqual(SyncCapsuleState.of(overall: .attention, card: bad, missingDays: nil), .failed)
    let mid = TodayModel.SyncCard(state: "issue", label: "", tone: .mid, ago: "6 小时前")
    XCTAssertEqual(SyncCapsuleState.of(overall: .ok, card: mid, missingDays: nil), .behind("6 小时前"))
    XCTAssertEqual(SyncCapsuleState.of(overall: .ok, card: nil, missingDays: nil), .ok(ago: nil))
  }

  // MARK: - the glance built from the documents

  static func doc(_ series: [String: [Double?]]) -> [String: Any] {
    var out: [String: Any] = [:]
    for (k, v) in series { out[k] = TodayCoreTests.series(v.map { $0 as Any? ?? NSNull() }) }
    return TodayCoreTests.doc(series: out)
  }

  /// 61 days: 60 history days cycling 58…74, then today.
  static func history(_ today: Double?, from lo: Double = 58, step: Double = 4) -> [Double?] {
    (0..<60).map { lo + Double($0 % 5) * step } + [today]
  }

  func testGlanceFromTheDocuments() throws {
    let today: [String: Any] = [
      "date": TodayCoreTests.end,
      "scores": ["today": ["recovery": 82, "sleep_score": 88, "hrv_night_ms": 68, "rhr_night": 51],
                 "yesterday": ["strain": 9.1, "tsb": 4.4], "recovery_zone": "green", "recovery_word": "可上强度"] as [String: Any],
    ]
    let trends = Self.doc([
      "recovery": Self.history(nil), "sleep_score": Self.history(nil, from: 72, step: 3),
      "hrv": Self.history(nil, from: 54, step: 2.5), "resting_hr": Self.history(nil, from: 52, step: 0.5),
      "resp_rate": Self.history(14.2, from: 13.9, step: 0.1), "steps": Self.history(3860, from: 3000, step: 200),
    ])
    let m = TodayModel(TodayInputs(today: today, activity: ["daily": ["steps": 3860, "active_kcal": 186, "exercise_min": 12]],
                                   coach: ["target_low": 12, "target_high": 16, "current_strain": 2.8],
                                   userToday: TodayCoreTests.end, trends: trends))
    let g = m.glance
    XCTAssertEqual(g.recovery.value, 82)
    XCTAssertEqual(g.recovery.tone, .good)
    XCTAssertEqual(g.recovery.verdict, "可上强度")
    XCTAssertNil(g.recovery.reason)
    let u = try XCTUnwrap(g.recovery.usual)
    XCTAssertEqual(u.lowerBound, 62, accuracy: 1e-9)
    XCTAssertEqual(u.upperBound, 70, accuracy: 1e-9)
    XCTAssertEqual(g.recovery.week.count, 7)
    XCTAssertEqual(g.recovery.week.last ?? nil, 82)   // today's number fills the last bar
    XCTAssertEqual(g.month.count, 30)
    XCTAssertEqual(g.monthDates.last, TodayCoreTests.end)

    XCTAssertEqual(g.sleep.text, "88")
    XCTAssertNotNil(g.sleep.usual)
    XCTAssertEqual(g.strain.text, "2.8")   // today so far, never yesterday's 9.1
    XCTAssertEqual(g.strain.target, 12...16)

    XCTAssertEqual(g.recoveryRows.map(\.id), ["hrv", "resting_hr", "resp_rate"])
    XCTAssertEqual(g.recoveryRows[0].value, "68")
    XCTAssertEqual(g.recoveryRows[0].deltaTone, .good)
    XCTAssertEqual(g.recoveryRows[0].spark.count, 14)
    XCTAssertEqual(g.recoveryRows[1].delta, "−2")   // 51 against a median of 53
    XCTAssertEqual(g.recoveryRows[2].deltaTone, .text)

    XCTAssertEqual(g.strainRows.map(\.id), ["steps", "active_kcal", "exercise_min", "tsb"])
    XCTAssertEqual(g.strainRows[0].value, "3,860")
    XCTAssertEqual(g.strainRows[3].value, "+4")
  }

  func testNotWornAndFirstDays() {
    let today: [String: Any] = [
      "date": TodayCoreTests.end,
      "scores": ["today": ["flags": ["no_sleep": true]], "yesterday": [String: Any](),
                 "recovery_empty": ["label": "昨晚没有睡眠数据"]] as [String: Any],
    ]
    // day 5: four days of history only
    let trends = Self.doc(["recovery": Array(repeating: nil, count: 56) + [60, 70, 65, 71, nil]])
    let m = TodayModel(TodayInputs(today: today, userToday: TodayCoreTests.end, trends: trends))
    XCTAssertNil(m.glance.recovery.value)
    XCTAssertEqual(m.glance.recovery.reason, "昨晚没戴表")
    XCTAssertNil(m.glance.recovery.verdict)
    XCTAssertNil(m.glance.recovery.usual)   // no usual line in the first two weeks
    XCTAssertEqual(m.glance.recovery.week.compactMap { $0 }, [60, 70, 65, 71])
    XCTAssertNil(m.glance.strain.text)
    XCTAssertTrue(m.glance.recoveryRows.isEmpty)
  }
}
