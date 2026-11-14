import SwiftUI
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
    let thirteen: [Double?] = (0..<13).map { i -> Double? in Double(60 + i) } + [90]
    XCTAssertNil(TodayGlanceRules.usual(thirteen))   // first two weeks: no usual line
    let days: [Double?] = (0..<30).map { i -> Double? in Double(50 + i) } + [99]   // 50…79, then today
    let u = try XCTUnwrap(TodayGlanceRules.usual(days))
    XCTAssertEqual(u.lowerBound, 57.25, accuracy: 1e-9)
    XCTAssertEqual(u.upperBound, 71.75, accuracy: 1e-9)
    XCTAssertEqual(TodayGlanceRules.usualMid(days)!, 64.5, accuracy: 1e-9)
    // gaps do not count as days
    var gaps: [Double?] = []
    for i in 0..<30 { gaps.append(i % 3 == 0 ? Double(i) : nil) }
    gaps.append(1)
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

  // MARK: - sync capsule (sync visibility is P0)

  func testSyncCapsuleStates() {
    let ok = TodayModel.SyncCard(state: "ok", label: "", tone: .good, ago: "2 分钟前", minutes: 2)
    XCTAssertEqual(SyncCapsuleState.of(overall: .ok, page: .idle, card: ok), .ok(ago: "2 分钟前"))
    XCTAssertEqual(SyncCapsuleState.of(overall: .syncing, page: .idle, card: ok), .busy)
    XCTAssertEqual(SyncCapsuleState.of(overall: .ok, page: .syncing, card: ok), .busy)
    XCTAssertEqual(SyncCapsuleState.of(overall: .attention, page: .idle, card: ok), .behind("2 分钟前"))
    let bad = TodayModel.SyncCard(state: "issue", label: "", tone: .bad, ago: "6 小时前", issueCode: "x")
    XCTAssertEqual(SyncCapsuleState.of(overall: .ok, page: .idle, card: bad), .failed)
    XCTAssertEqual(SyncCapsuleState.of(overall: .attention, page: .idle, card: bad), .failed)
    XCTAssertEqual(SyncCapsuleState.of(overall: .ok, page: .failed("x"), card: ok), .failed)
    let mid = TodayModel.SyncCard(state: "issue", label: "", tone: .mid, ago: "6 小时前")
    XCTAssertEqual(SyncCapsuleState.of(overall: .ok, page: .idle, card: mid), .behind("6 小时前"))
    XCTAssertEqual(SyncCapsuleState.of(overall: .ok, page: .idle, card: nil), .ok(ago: nil))
  }

  /// Sync turned off is never 「已同步」, and a tap opens the sync center instead of syncing.
  func testSyncOffIsItsOwnStateAndOpensTheCenter() {
    let ok = TodayModel.SyncCard(state: "ok", label: "", tone: .good, ago: "2 分钟前", minutes: 2)
    let off = SyncCapsuleState.of(overall: .off, page: .idle, card: ok)
    XCTAssertEqual(off, .off)
    XCTAssertTrue(off.opensCenter)
    XCTAssertEqual(SyncCapsuleState.of(overall: .off, page: .idle, card: nil), .off)
    XCTAssertTrue(SyncCapsuleState.busy.opensCenter)
    XCTAssertTrue(SyncCapsuleState.behind("x").opensCenter)
    XCTAssertFalse(SyncCapsuleState.ok(ago: nil).opensCenter)
    XCTAssertFalse(SyncCapsuleState.stale("x").opensCenter)
    XCTAssertFalse(SyncCapsuleState.failed.opensCenter)
  }

  /// Sync off and offline at once: 「未开同步」 (nothing comes in until sync is on; the center is where to turn it on).
  func testSyncOffWinsOverOffline() {
    XCTAssertEqual(SyncCapsuleState.of(overall: .off, page: .offline, card: nil), .off)
  }

  /// 「缺N天」: days just before today with nothing on the server; older gaps stay in the sync center's list.
  func testMissingDaysAreTheRecentOnes() {
    let dates = ["2026-01-09", "2026-01-08", "2026-01-08", "2026-01-02", "2025-12-20"]
    XCTAssertEqual(TodayGlanceRules.recentMissing(dates, today: "2026-01-10"), 2)
    XCTAssertEqual(TodayGlanceRules.recentMissing(["2026-01-10"], today: "2026-01-10"), 0)   // today is still coming in
    XCTAssertEqual(TodayGlanceRules.recentMissing(["2026-01-03"], today: "2026-01-10"), 1)   // 7 days back is in
    XCTAssertEqual(TodayGlanceRules.recentMissing(["2026-01-02"], today: "2026-01-10"), 0)
    var card = TodayModel.SyncCard(state: "ok", label: "", tone: .good, ago: "2 分钟前", minutes: 2)
    card.missingDays = 2
    let st = SyncCapsuleState.of(overall: .ok, page: .idle, card: card)
    XCTAssertEqual(st, .behind("缺2天"))
    XCTAssertTrue(st.opensCenter)   // the sync center has the list and the re-sync
    card.missingDays = 0
    XCTAssertEqual(SyncCapsuleState.of(overall: .ok, page: .idle, card: card), .ok(ago: "2 分钟前"))
    // read from the documents
    let m = TodayModel(TodayInputs(today: ["date": "2026-01-10"], sync: ["state": "ok", "minutes_ago": 5],
                                   missing: ["days": [["date": "2026-01-09", "kinds": ["sleep"]], ["date": "2025-12-01", "kinds": ["steps"]]]],
                                   userToday: "2026-01-10"))
    XCTAssertEqual(m.sync?.missingDays, 1)
  }

  /// Offline or old data never looks like the latest: the capsule and the faces say how old it is.
  func testStaleDataSaysSo() {
    let fresh = TodayModel.SyncCard(state: "ok", label: "", tone: .good, ago: "2 分钟前", minutes: 2)
    let old = TodayModel.SyncCard(state: "ok", label: "", tone: .good, ago: "7 小时前", minutes: 420)
    XCTAssertEqual(SyncCapsuleState.of(overall: .ok, page: .offline, card: fresh), .stale("离线"))
    XCTAssertEqual(SyncCapsuleState.of(overall: .attention, page: .offline, card: fresh), .stale("离线"))
    XCTAssertEqual(SyncCapsuleState.of(overall: .ok, page: .idle, card: old), .stale("7 小时前"))
    XCTAssertEqual(SyncCapsuleState.of(overall: .ok, page: .idle, card: fresh), .ok(ago: "2 分钟前"))
    XCTAssertEqual(TodayGlanceRules.staleAge(page: .offline, card: nil), "离线")
    XCTAssertEqual(TodayGlanceRules.staleAge(page: .idle, card: old), "7 小时前")
    XCTAssertNil(TodayGlanceRules.staleAge(page: .idle, card: fresh))
    XCTAssertNil(TodayGlanceRules.staleAge(page: .idle, card: nil))
    let edge = TodayModel.SyncCard(state: "ok", label: "", tone: .good, ago: nil, minutes: TodayParams.syncStaleMinutes)
    XCTAssertEqual(TodayGlanceRules.staleAge(page: .idle, card: edge), TodayModel.ago(TodayParams.syncStaleMinutes))
    // the server's card is read with its minutes
    let m = TodayModel(TodayInputs(today: ["date": "2026-01-02"], sync: ["state": "ok", "minutes_ago": 420], userToday: "2026-01-02"))
    XCTAssertEqual(m.sync?.minutes, 420)
  }

  // MARK: - drawing, links, faces

  func testARangeOfOneValueHasOneEdge() {
    XCTAssertEqual(TodayGlanceRules.edges(5...5), [5])
    XCTAssertEqual(TodayGlanceRules.edges(2...7), [2, 7])
  }

  func testAFoodLinkOpensTheFoodSegment() {
    XCTAssertEqual(AppTab.food.destination(circles: true).tab, .record)
    XCTAssertEqual(AppTab.food.destination(circles: true).segment, .food)
    XCTAssertEqual(AppTab.food.destination(circles: false).segment, .food)
    XCTAssertEqual(AppTab.trends.destination(circles: true).tab, .trends)
    XCTAssertNil(AppTab.trends.destination(circles: true).segment)
    XCTAssertEqual(AppTab.circles.destination(circles: false).tab, .today)
  }

  /// No circles data yet: no face (never 「0 人练了」 before the members are known).
  func testTheCirclesFaceWaitsForItsData() {
    XCTAssertEqual(CirclesFaceMode.of(listLoaded: false, hasCircles: false, overviewLoaded: false), .hidden)
    XCTAssertEqual(CirclesFaceMode.of(listLoaded: true, hasCircles: true, overviewLoaded: false), .hidden)
    XCTAssertEqual(CirclesFaceMode.of(listLoaded: true, hasCircles: true, overviewLoaded: true), .count)
    XCTAssertEqual(CirclesFaceMode.of(listLoaded: false, hasCircles: true, overviewLoaded: true), .count)
    XCTAssertEqual(CirclesFaceMode.of(listLoaded: true, hasCircles: false, overviewLoaded: false), .invite)
  }

  // MARK: - the page in a window

  @MainActor private func session(flags: Set<String>) throws -> AccountSession {
    let cfg = AccountConfig(server: "https://body.example.com", userId: 7, displayName: "小林")
    return AccountSession(config: cfg, api: PathStub.client(), db: try AppDatabase.inMemory(), flags: flags)
  }

  private static let noop = TodayGlanceActions(
    l1: { _ in }, sheet: { _ in }, open: { _ in }, calendar: {}, backToToday: {}, step: { _ in }, syncTap: {}, syncCenter: {},
    recoveryNext: { _ in }, food: {}, circles: {}, training: {})

  /// A day without a recovery score and with `flag` as the reason.
  private static func noScore(_ flag: String) -> TodayModel {
    TodayModel(TodayInputs(today: ["date": "2026-01-02", "scores": ["today": ["flags": [flag: true]], "yesterday": [String: Any]()]],
                           userToday: "2026-01-02"))
  }

  @MainActor private func window(_ view: AnyView) throws -> (UIWindow, UIHostingController<AnyView>) {
    guard let scene = UIApplication.shared.connectedScenes.compactMap({ $0 as? UIWindowScene }).first else {
      throw XCTSkip("no window scene in this test host")
    }
    let host = UIHostingController(rootView: view)
    let window = UIWindow(windowScene: scene)
    window.frame = CGRect(x: 0, y: 0, width: 390, height: 600)
    window.rootViewController = host
    window.isHidden = false
    return (window, host)
  }

  /// The picture once it has stopped changing.
  @MainActor private func settled(_ view: UIView) -> Data? {
    func shot() -> Data? {
      UIGraphicsImageRenderer(size: CGSize(width: 390, height: 600)).image { _ in
        view.drawHierarchy(in: view.bounds, afterScreenUpdates: true)
      }.pngData()
    }
    var last = shot(), same = 0
    for _ in 0..<40 where same < 3 {
      RunLoop.main.run(until: Date().addingTimeInterval(0.2))
      let now = shot()
      same = now == last ? same + 1 : 0
      last = now
    }
    return last
  }

  /// The dial's middle follows the day: another reason with the same (empty) arc is drawn on the same page. A dial that
  /// compared only its arc (the first version) kept the old words here.
  @MainActor func testTheDialRedrawsItsMiddle() throws {
    PathStub.replies = [:]
    let s = try session(flags: [])
    func page(_ m: TodayModel) -> AnyView {
      AnyView(TodayGlancePage(model: m, isPast: false, circlesOn: false, day: nil, session: s, run: Self.noop)
        .environmentObject(s).environmentObject(AppModel()))
    }
    let notWorn = Self.noScore("no_sleep"), noHRV = Self.noScore("hrv_missing")
    XCTAssertEqual(notWorn.glance.recovery.reason, "昨晚没戴表")
    XCTAssertEqual(noHRV.glance.recovery.reason, "缺 HRV")
    let (window, host) = try window(page(notWorn))
    defer { window.isHidden = true; window.rootViewController = nil }
    let a = settled(host.view)
    host.rootView = page(noHRV)
    let b = settled(host.view)
    host.rootView = page(notWorn)
    let c = settled(host.view)
    XCTAssertNotEqual(a, b, "a new reason must be drawn in the dial")
    XCTAssertEqual(a, c)
  }

  /// Circles are a core feature: with nothing cached the face is not drawn yet, and the page still asks for the circles (the
  /// first version asked from inside the face, so it never asked and the face never came).
  @MainActor func testThePageAsksForTheCirclesWithNothingCached() throws {
    PathStub.replies = [:]
    PathStub.paths = []
    let s = try session(flags: ["friends"])
    XCTAssertNil(CirclesStore.shared(for: s).list)
    let view = AnyView(TodayGlancePage(model: Self.noScore("no_sleep"), isPast: false, circlesOn: true, day: nil, session: s,
                                       run: Self.noop).environmentObject(s).environmentObject(AppModel()))
    let (window, _) = try window(view)
    defer { window.isHidden = true; window.rootViewController = nil }
    for _ in 0..<50 where !PathStub.paths.contains(Endpoint.Circles.list) { RunLoop.main.run(until: Date().addingTimeInterval(0.1)) }
    XCTAssertTrue(PathStub.paths.contains(Endpoint.Circles.list), "\(PathStub.paths)")
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

/// The L1 sheets' server data from #448: the strain coach's `day_curve` / `forecast` and the sleep night's `series`.
/// Older servers send neither, so every field is optional. Every number is made up.
final class TodayL1DataTests: XCTestCase {
  override func setUp() {
    super.setUp()
    Lang.override = "zh"
  }

  override func tearDown() {
    Lang.override = nil
    super.tearDown()
  }

  private func coach(curve: [String: Any]?, forecast: [String: Any]? = nil) -> [String: Any] {
    var c: [String: Any] = ["target_low": 10, "target_high": 14, "current_strain": 6.5]
    if let curve { c["day_curve"] = curve }
    if let forecast { c["forecast"] = forecast }
    return c
  }

  func testTheCurveAndTheForecast() throws {
    let c = coach(curve: ["step_min": 60, "values": [0, 0, 0.4, 1.3, 6.5], "reason": NSNull(), "computed_at": "x"],
                  forecast: ["sessions": ["下肢 B"], "strain": 12.8, "n": 3, "of": 4, "dates": ["2026-01-01"]])
    let d = try XCTUnwrap(TodayGlanceRules.strainDay(c, current: 6.5, isPast: false))
    XCTAssertEqual(d.values, [0, 0, 0.4, 1.3, 6.5])
    XCTAssertNil(d.reason)
    XCTAssertEqual(d.forecast, 12.8)
    XCTAssertEqual(TodayGlanceRules.forecastCount(d), "3 次")
    // the expected strain never sits below what is already done
    XCTAssertEqual(TodayGlanceRules.strainDay(c, current: 15.2, isPast: false)?.forecast, 15.2)
    // a full set of sessions needs no count
    let full = coach(curve: ["values": [1.0]], forecast: ["strain": 12.8, "n": 4, "of": 4])
    XCTAssertNil(TodayGlanceRules.forecastCount(try XCTUnwrap(TodayGlanceRules.strainDay(full, current: 1, isPast: false))))
  }

  func testNoForecastWithoutAPlanASessionOrOnAPastDay() {
    XCTAssertNil(TodayGlanceRules.strainDay(coach(curve: ["values": [1.0]]), current: 1, isPast: false)?.forecast)
    let none = coach(curve: ["values": [1.0]], forecast: ["strain": NSNull(), "n": 0, "of": 4])
    XCTAssertNil(TodayGlanceRules.strainDay(none, current: 1, isPast: false)?.forecast)
    let some = coach(curve: ["values": [1.0]], forecast: ["strain": 12.0, "n": 2, "of": 4])
    XCTAssertNil(TodayGlanceRules.strainDay(some, current: 1, isPast: true)?.forecast)
  }

  func testAnOlderServerKeepsTheGauge() {
    XCTAssertNil(TodayGlanceRules.strainDay(coach(curve: nil), current: 6.5, isPast: false))
    XCTAssertNil(TodayGlanceRules.strainDay(nil, current: nil, isPast: false))
    XCTAssertNil(TodayGlanceRules.strainDay(["day_curve": "garbage"], current: nil, isPast: false))
  }

  func testNoCurveSaysWhyAndDrawsNothing() throws {
    let raw = try XCTUnwrap(TodayGlanceRules.strainDay(coach(curve: ["values": [Any](), "reason": "no_raw_hr"]), current: nil, isPast: false))
    XCTAssertEqual(raw.values, [])
    XCTAssertEqual(TodayGlanceRules.curveReason(raw.reason), "没有心率")
    XCTAssertEqual(TodayGlanceRules.curveReason("no_scores"), "没有评分")
    XCTAssertEqual(TodayGlanceRules.curveReason("not_computed"), "曲线未算")
    let unknown = try XCTUnwrap(TodayGlanceRules.strainDay(coach(curve: ["values": [Any]()]), current: nil, isPast: false))
    XCTAssertEqual(TodayGlanceRules.curveReason(unknown.reason), "曲线未算")
  }

  func testTheTodayModelCarriesTheCurve() {
    let m = TodayModel(TodayInputs(today: ["date": "2026-01-02"], coach: coach(curve: ["values": [0.5, 6.5]]),
                                   userToday: "2026-01-02"))
    XCTAssertEqual(m.glance.strainDay?.values, [0.5, 6.5])
  }

  /// The phone's own strain coach (M4 primary) has no curve: the server's curve and forecast are laid over it.
  func testThePhonesCoachTakesTheServersCurve() {
    let local: [String: Any] = ["target_low": 9, "target_high": 13, "current_strain": 7.1]
    let server = coach(curve: ["values": [Any](), "reason": "not_computed"], forecast: ["strain": 12.0, "n": 4, "of": 4])
    let c = TodayDocs.coach(local: local, server: server)
    XCTAssertEqual(c?["target_low"] as? Int, 9)
    XCTAssertEqual((c?["day_curve"] as? [String: Any])?["reason"] as? String, "not_computed")
    XCTAssertNotNil(c?["forecast"])
    XCTAssertEqual(TodayDocs.coach(local: nil, server: server)?["target_low"] as? Int, 10)
    XCTAssertNil(TodayDocs.coach(local: local, server: nil)?["day_curve"])
  }

  func testTheSleepSeriesKeepsItsGaps() throws {
    let json: [String: Any] = [
      "date": "2026-01-02",
      "main": ["start": "23:40", "end": "07:30", "staged": true, "deep_min": 72, "asleep_min": 440],
      "baseline": ["regularity_pct_7d": 76, "regularity_pct_30d": 80, "avg30": ["deep_min": 84]],
      "series": ["nights": 4, "dates": ["2025-12-30", "2025-12-31", "2026-01-01", "2026-01-02"],
                 "deep_min": ["values": [88, NSNull(), 95, 72], "n": 3, "mean": 85],
                 "regularity_pct_7d": ["values": [81, 83, NSNull(), 76], "n": 3]],
    ]
    let m = SleepNightModel(json: json)
    XCTAssertEqual(m.deepSeries, [88, nil, 95, 72])
    XCTAssertEqual(m.regularitySeries, [81, 83, nil, 76])
    let rows = TodaySleepL1.rows(m)
    XCTAssertEqual(rows.first { $0.id == "deep" }?.spark, [88, nil, 95, 72])
    XCTAssertNotNil(rows.first { $0.id == "deep" }?.usual)
    XCTAssertEqual(rows.first { $0.id == "regularity" }?.spark, [81, 83, nil, 76])
    // an older server: no series, no line, the numbers stay
    var old = json
    old.removeValue(forKey: "series")
    let o = SleepNightModel(json: old)
    XCTAssertEqual(o.deepSeries, [])
    let orow = try XCTUnwrap(TodaySleepL1.rows(o).first { $0.id == "deep" })
    XCTAssertEqual(orow.spark, [])
    XCTAssertEqual(orow.value, TodayModel.hm(72))
    XCTAssertNotEqual(m, o)
  }

  func testTheNightIsAskedWithItsSeries() {
    let r = Endpoint.sleepNight("2026-01-02")
    XCTAssertTrue(r.query.contains(URLQueryItem(name: "series_nights", value: "30")))
    XCTAssertTrue(r.query.contains(URLQueryItem(name: "date", value: "2026-01-02")))
    XCTAssertFalse(Endpoint.sleepNight(nil).query.contains { $0.name == "date" })
  }
}
