import GRDB
import SwiftUI
import XCTest
@testable import Watch2Whoop

// Flag `ui_ai_first` (#279): the AI-first Today. Every value here is made up.

private func json(_ s: String) -> [String: Any] {
  (try? JSONSerialization.jsonObject(with: Data(s.utf8))) as? [String: Any] ?? [:]
}

private let homeDoc = """
  {"focus": ["sleep"], "hidden": ["journal"], "focus_options": ["fat", "muscle", "sleep", "recover", "markers"], "max_modules": 6,
   "modules": [{"key": "muscle_load", "reason": "pinned"}, {"key": "sleep_goal", "reason": "focus"}, {"key": "sleep", "reason": "focus"},
               {"key": "health_monitor", "reason": "finding"}, {"key": "training", "reason": "used"}, {"key": "strain_coach", "reason": null}],
   "pace": {"day": 0, "limit": 3, "veteran": false},
   "overview": {"findings": [{"key": "hrv_low", "module": "health_monitor", "area": "A", "value": "−20%", "label": "L", "tone": "mid", "weight": 2},
                             {"key": "recovery_up", "module": null, "area": "B", "value": "66", "label": "M", "tone": "good", "weight": 1}],
                "stats": {"days": 70, "since": "2026-01-01", "nights": 60, "workouts": 12}}}
  """

/// A today document with data in every block the page reads.
private func todayInputs(past: Bool = false, recovery: Double? = 79, sleep: Double? = 400, need: Double? = 470) -> TodayInputs {
  var t: [String: Any] = ["hrv_night_ms": 48, "rhr_night": 52]
  if let recovery { t["recovery"] = recovery }
  if let sleep { t["sleep_actual_min"] = sleep }
  if let need { t["sleep_need_min"] = need }
  return TodayInputs(
    today: ["date": "2026-01-02", "scores": ["today": t, "yesterday": ["date": "2026-01-01", "strain": 9.5], "recovery_zone": "green"],
            "reminders": [["id": 1, "title": "First", "level": "alert", "status": "new"],
                          ["id": 2, "title": "Second", "level": "info", "status": "new"],
                          ["id": 3, "title": "Third", "level": "info", "status": "seen"],
                          ["id": 4, "title": "Fourth", "level": "info", "status": "seen"]]],
    activity: json(#"{"date": "2026-01-02", "daily": {"steps": 12345, "active_kcal": 512, "exercise_min": 31}}"#),
    coach: json(#"{"target_low": 10, "target_high": 14, "current_strain": 6.5, "status": null, "remaining_to_low": 3.5}"#),
    monitor: json(#"{"metrics": [{"key": "rhr", "value": 52, "status": "normal"}]}"#),
    userToday: past ? "2026-01-09" : "2026-01-02", circlesOn: true)
}

final class TodayHomeModelTests: XCTestCase {
  func testTheDocumentIsRead() throws {
    let h = try XCTUnwrap(TodayHome(json: json(homeDoc)))
    XCTAssertEqual(h.focus, ["sleep"])
    XCTAssertEqual(h.hidden, ["journal"])
    XCTAssertEqual(h.modules.first, TodayHome.Module(key: "muscle_load", reason: .pinned))
    XCTAssertNil(h.modules.last?.reason)
    XCTAssertEqual(h.pace, TodayHome.Pace(day: 0, limit: 3, veteran: false))
    XCTAssertEqual(h.overview?.days, 70)
    XCTAssertEqual(h.overview?.findings.map(\.tone), [.mid, .good])
    XCTAssertNil(TodayHome(json: ["no": "modules"]))
    XCTAssertNil(TodayHome(json: nil))
  }

  func testAnOlderServerWithoutThePaceHasNoPace() throws {
    var d = json(homeDoc)
    d["pace"] = nil
    let h = try XCTUnwrap(TodayHome(json: d))
    XCTAssertNil(h.pace)
    XCTAssertEqual(h.shown { _ in true }.count, 6)
  }

  // The new-user stages of the design (4.2, 4.6, D5, D6, D8): the pace only limits what nobody asked for

  func testDayOneThreeCardsPlusWhatTheUserOrAnAIAskedFor() throws {
    let h = try XCTUnwrap(TodayHome(json: json(homeDoc)))
    // pinned + two focus = the three of day 1; the finding waits; what an AI used is on the page at once (D5)
    XCTAssertEqual(h.shown { _ in true }.map(\.key), ["muscle_load", "sleep_goal", "sleep", "training"])
  }

  func testThePaceGrowsAndNeverPassesTheMaximum() throws {
    var h = try XCTUnwrap(TodayHome(json: json(homeDoc)))
    h.pace = TodayHome.Pace(day: 1, limit: 5, veteran: false)
    XCTAssertEqual(h.shown { _ in true }.map(\.key), ["muscle_load", "sleep_goal", "sleep", "health_monitor", "training"])
    h.pace = TodayHome.Pace(day: 14, limit: 6, veteran: false)
    XCTAssertEqual(h.shown { _ in true }.count, 6)
    h.modules.append(TodayHome.Module(key: "journal", reason: .used))
    XCTAssertEqual(h.shown { _ in true }.count, 6)
  }

  func testAnExistingUserHasAllFromTheFirstDay() throws {
    var h = try XCTUnwrap(TodayHome(json: json(homeDoc)))
    h.pace = TodayHome.Pace(day: 0, limit: 6, veteran: true)
    XCTAssertEqual(h.shown { _ in true }.count, 6)
  }

  func testNoEmptyCardsAndNothingLocked() throws {
    let h = try XCTUnwrap(TodayHome(json: json(homeDoc)))
    // a card with nothing to show does not take a place: the next one moves up
    let shown = h.shown { $0 != "sleep_goal" }
    XCTAssertEqual(shown.map(\.key), ["muscle_load", "sleep", "health_monitor", "training"])
    // what is not on the page is still there, to put on it: nothing is lost, hidden ones last
    XCTAssertEqual(h.notShown(shown), ["sleep_goal", "strain_coach", "journal"])
  }

  func testNoDocumentMeansTheClassicOrder() {
    XCTAssertEqual(TodayHome.fallback.modules.map(\.key), TodayHome.defaultOrder)
    XCTAssertEqual(TodayHome.fallback.shown { _ in true }.count, TodayHome.defaultMax)
    XCTAssertTrue(TodayHome.fallback.modules.allSatisfy { $0.reason == nil })
  }

  func testWhichCardsHaveSomethingToShow() {
    let m = TodayModel(todayInputs())
    for k in ["health_monitor", "strain_coach", "sleep", "sleep_goal", "training", "journal"] {
      XCTAssertTrue(TodayHome.has(k, model: m, flags: []), k)
    }
    XCTAssertFalse(TodayHome.has("muscle_load", model: m, flags: []))
    XCTAssertTrue(TodayHome.has("muscle_load", model: m, flags: ["muscle_load"]))
    XCTAssertFalse(TodayHome.has("something_new", model: m, flags: []))
    // a day looked back at: only cards about that day
    let past = TodayModel(todayInputs(past: true))
    XCTAssertTrue(past.isPast)
    for k in ["sleep_goal", "journal", "muscle_load"] { XCTAssertFalse(TodayHome.has(k, model: past, flags: ["muscle_load"]), k) }
    XCTAssertTrue(TodayHome.has("sleep", model: past, flags: []))
    XCTAssertFalse(TodayHome.has("training", model: past, flags: []), "no training document: its link opens today")
    var pastDoc = todayInputs(past: true)
    pastDoc.today?["training"] = ["planned": [], "logged": [], "rest_day": true] as [String: Any]
    let pastTraining = TodayModel(pastDoc)
    XCTAssertTrue(TodayHome.has("training", model: pastTraining, flags: []))
    XCTAssertTrue(TodayHome.cardHasHeading("training", model: pastTraining))
    XCTAssertFalse(TodayHome.cardHasHeading("training", model: past))
    XCTAssertFalse(TodayHome.cardHasHeading("sleep", model: pastTraining))
    // no data at all: no monitor, no strain target, no sleep
    let empty = TodayModel(TodayInputs())
    for k in ["health_monitor", "strain_coach", "sleep"] { XCTAssertFalse(TodayHome.has(k, model: empty, flags: []), k) }
  }

  func testRingsSleepIsTheNightAgainstItsNeedTheOthersAreTheClassicOnes() {
    let m = TodayModel(todayInputs(sleep: 400, need: 500))
    let rings = TodayHome.rings(m)
    XCTAssertEqual(rings.map(\.key), m.rings.map(\.key))
    XCTAssertEqual(rings[1].key, "recovery")   // in the middle, as the classic page
    XCTAssertEqual(rings[0].value, "6:40")
    XCTAssertEqual(rings[0].fraction, 0.8, accuracy: 0.001)
    XCTAssertEqual(rings[1], m.rings[1])
    XCTAssertEqual(rings[2], m.rings[2])
    // no night: an empty ring, not a number
    let none = TodayHome.rings(TodayModel(todayInputs(sleep: nil)))
    XCTAssertNil(none[0].value)
    XCTAssertEqual(none[0].fraction, 0)
    // the model's own numbers are not touched: the classic ring stays the sleep score
    XCTAssertEqual(m.rings[0].label, tr("睡眠分", "Sleep score"))
  }

  func testFocusHasAnEndAndTheRestIsOneTapAway() {
    let m = TodayModel(todayInputs())
    let f = TodayHome.focusItems(m)
    XCTAssertEqual(f.reminders.count, TodayHomeParams.maxFocusItems)
    XCTAssertEqual(f.reminders.map(\.id), [1, 2, 3])
    var withAttention = m
    withAttention.attention = [TodayAttention(id: "x", label: "X", value: "1", tone: .bad, severity: 1, open: .coach),
                               TodayAttention(id: "y", label: "Y", value: "2", tone: .mid, severity: 1, open: .coach)]
    let g = TodayHome.focusItems(withAttention)
    XCTAssertEqual(g.attention.count, 2)     // what is off on the day comes first
    XCTAssertEqual(g.reminders.map(\.id), [1])
  }

  // MARK: - questions

  func testQuestionsComeFromTheUsersOwnNumbers() {
    let qs = TodayHomeAsk.today(TodayModel(todayInputs(recovery: 79, sleep: 400, need: 470)), photo: true)
    XCTAssertEqual(qs.count, TodayHomeParams.maxQuestions)
    XCTAssertTrue(qs[0].label.contains("79"))
    XCTAssertEqual(qs[1].label, tr("为什么睡得比需要的少？", "Why did I sleep less than I need?"))
    XCTAssertEqual(qs[2].text, tr("帮我记一顿饭", "Log a meal for me"))
    XCTAssertFalse(qs[2].isExample)
    // slept enough: no question about it, and room for the photo
    let enough = TodayHomeAsk.today(TodayModel(todayInputs(sleep: 460, need: 470)), photo: true)
    XCTAssertEqual(enough.map(\.photo), [false, false, true])
    // no recovery and no photo: never an empty row
    let bare = TodayHomeAsk.today(TodayModel(todayInputs(recovery: nil, sleep: nil, need: nil)), photo: false)
    XCTAssertEqual(bare.count, 1)
    XCTAssertFalse(bare[0].isExample)
  }

  func testACardsQuestionIsAboutThatCard() {
    XCTAssertTrue(TodayHomeAsk.question(card: "recovery", label: "", value: "79").contains("79"))
    XCTAssertTrue(TodayHomeAsk.question(card: "strain", label: "", value: "9.5").contains("9.5"))
    XCTAssertTrue(TodayHomeAsk.question(card: "journal", label: "Journal", value: nil).contains("Journal"))
    XCTAssertTrue(TodayHomeAsk.question(card: "x", label: "Steps", value: "12").contains("12"))
  }

  // MARK: - the search field

  func testSearchFindsFeaturesAndEverythingElseIsAQuestion() {
    let water = tr("喝水", "Water")
    XCTAssertEqual(TodayHomeFeatures.matches(water, circles: false).first?.name, water)
    XCTAssertEqual(TodayHomeFeatures.direct(water, circles: false)?.dest, .food)
    XCTAssertTrue(TodayHomeFeatures.matches("  ", circles: false).isEmpty)
    XCTAssertLessThanOrEqual(TodayHomeFeatures.matches("e", circles: true).count, 5)
    XCTAssertNil(TodayHomeFeatures.direct("why is the sky blue", circles: false))
    XCTAssertEqual(TodayHomeFeatures.normalized("周报 / 月报 · A"), "周报月报a")
  }

  func testCirclesAreThereOnlyWithTheirTab() {
    let name = tr("圈子动态", "Circle feed")
    XCTAssertTrue(TodayHomeFeatures.all(circles: true).flatMap(\.items).contains { $0.name == name })
    XCTAssertFalse(TodayHomeFeatures.all(circles: false).flatMap(\.items).contains { $0.name == name })
  }

  // MARK: - the local copy

  func testPatches() throws {
    let d = json(homeDoc)
    var h = try XCTUnwrap(TodayHome(json: TodayHome.patched(d, module: "sleep", state: "hidden")))
    XCTAssertFalse(h.modules.contains { $0.key == "sleep" })
    XCTAssertEqual(h.hidden, ["journal", "sleep"])
    // pinned: after what is already pinned, off the hidden list
    h = try XCTUnwrap(TodayHome(json: TodayHome.patched(d, module: "journal", state: "pinned")))
    XCTAssertEqual(h.modules.prefix(2).map(\.key), ["muscle_load", "journal"])
    XCTAssertEqual(h.modules[1].reason, .pinned)
    XCTAssertEqual(h.hidden, [])
    // back to the rules: a candidate again, no reason until the server says
    h = try XCTUnwrap(TodayHome(json: TodayHome.patched(d, module: "journal", state: "auto")))
    XCTAssertEqual(h.modules.last, TodayHome.Module(key: "journal", reason: nil))
    XCTAssertEqual(h.hidden, [])
  }
}

/// With the flag off the page must be exactly what it was: nothing of the AI-first page runs, reads or asks the server.
@MainActor
final class TodayHomeFlagOffTests: XCTestCase {
  private func session(flags: Set<String>) throws -> AccountSession {
    let cfg = AccountConfig(server: "https://body.example.com", userId: 7, displayName: "Test")
    return AccountSession(config: cfg, api: PathStub.client(), db: try AppDatabase.inMemory(), flags: flags)
  }

  override func setUp() {
    super.setUp()
    PathStub.replies = [:]
    PathStub.paths = []
  }

  func testTheFlagIsTheOnlySwitch() {
    XCTAssertFalse(TodayHome.isOn([]))
    XCTAssertFalse(TodayHome.isOn(["friends", "muscle_load", "native_sync"]))
    XCTAssertTrue(TodayHome.isOn(["ui_ai_first"]))
  }

  func testTheClassicStoreNeverAsksForTheHomeDocument() async throws {
    let s = try session(flags: [])
    let store = TodayStore(session: s)
    await store.refresh()
    XCTAssertFalse(PathStub.paths.contains { $0.contains("/ui/") }, "\(PathStub.paths)")
    for day in ["2026-01-02"] {
      for today in [true, false] {
        XCTAssertFalse(TodayDocs.fetches(day: day, isToday: today, circles: true).contains { $0.request.path.contains("/ui/") })
      }
    }
  }

  func testACachedHomeDocumentChangesNothingForTheClassicPage() throws {
    let s = try session(flags: [])
    try s.db.saveDoc(DocKey.todayCurrent, json: try JSONSerialization.data(withJSONObject: todayInputs().today ?? [:]))
    let before = TodayStore(session: s).model
    let beforeRead = try s.db.writer.read { db in try TodaySnapshot.read(db, date: nil) }
    try s.db.saveDoc(TodayHome.docKey, json: Data(homeDoc.utf8))
    let after = TodayStore(session: s).model
    let afterRead = try s.db.writer.read { db in try TodaySnapshot.read(db, date: nil) }
    XCTAssertEqual(before, after)
    XCTAssertEqual(beforeRead, afterRead)
    XCTAssertNil(afterRead.docs[TodayHome.docKey])
  }

  func testTheClassicModelKeepsItsRings() {
    let m = TodayModel(todayInputs())
    XCTAssertEqual(m.rings.map(\.key), ["sleep", "recovery", "strain"])
    XCTAssertEqual(m.rings[0].label, tr("睡眠分", "Sleep score"))
  }

  func testAStoreThatWasNeverStartedDoesNothing() async throws {
    let store = TodayHomeStore()
    XCTAssertFalse(store.isStarted)
    await store.refresh()
    store.setModule("sleep", state: "hidden")
    store.setFocus(["sleep"])
    store.overviewSeen()
    XCTAssertNil(store.home)
    XCTAssertTrue(PathStub.paths.isEmpty)
  }

  /// The kill switch: flag off takes the page back at once, and the document that made it stays unread.
  func testStopForgetsThePage() throws {
    let s = try session(flags: ["ui_ai_first"])
    try s.db.saveDoc(TodayHome.docKey, json: Data(homeDoc.utf8))
    let store = TodayHomeStore()
    store.start(s)
    XCTAssertEqual(store.home?.focus, ["sleep"])
    store.overviewOpened = true
    store.stop()
    XCTAssertFalse(store.isStarted)
    XCTAssertNil(store.home)
    XCTAssertFalse(store.overviewOpened)
  }

  /// The kill switch on a live page: pixel by pixel the same as before the flag came on. Only the top of the page: the line
  /// under it shows the state of the refresh, which has nothing to do with the flag.
  func testTheFlagGoingOffDrawsTheSamePageAgain() throws {
    let scene = UIApplication.shared.connectedScenes.compactMap { $0 as? UIWindowScene }.first
    guard let scene else { throw XCTSkip("no window scene in this test host") }
    let s = try session(flags: [])
    try s.db.saveDoc(DocKey.todayCurrent, json: try JSONSerialization.data(withJSONObject: todayInputs().today ?? [:]))
    try s.db.saveDoc(TodayHome.docKey, json: Data(homeDoc.utf8))
    let host = UIHostingController(rootView: TodayView(session: s).environmentObject(s).environmentObject(AppModel()))
    let window = UIWindow(windowScene: scene)
    window.frame = CGRect(x: 0, y: 0, width: 390, height: 844)
    window.rootViewController = host
    window.isHidden = false
    defer {
      window.isHidden = true
      window.rootViewController = nil
    }

    let off = settled(host.view)
    XCTAssertNotNil(off)
    s.flags = [TodayHome.flag]
    XCTAssertNotEqual(settled(host.view), off, "the flag must change the page")
    s.flags = []
    XCTAssertEqual(settled(host.view), off, "with the flag off again the page must be what it was")
  }

  /// The picture once it has stopped changing (the rings animate in).
  private func settled(_ view: UIView) -> Data? {
    func shot() -> Data? {
      UIGraphicsImageRenderer(size: CGSize(width: 390, height: 520)).image { _ in
        view.drawHierarchy(in: view.bounds, afterScreenUpdates: true)
      }.pngData()
    }
    var last = shot(), same = 0
    for _ in 0..<60 where same < 4 {
      RunLoop.main.run(until: Date().addingTimeInterval(0.25))
      let now = shot()
      same = now == last ? same + 1 : 0
      last = now
    }
    return last
  }
}

/// The account's choices: the document lives in the account's own database, edits go local first and through the offline queue.
@MainActor
final class TodayHomeStoreTests: XCTestCase {
  private func session(flags: Set<String> = ["ui_ai_first"], db: AppDatabase? = nil) throws -> AccountSession {
    let cfg = AccountConfig(server: "https://body.example.com", userId: 7, displayName: "Test")
    return AccountSession(config: cfg, api: PathStub.client(), db: try db ?? AppDatabase.inMemory(), flags: flags)
  }

  override func setUp() {
    super.setUp()
    PathStub.replies = [:]
    PathStub.paths = []
  }

  private func wait(_ what: String = "condition", _ ok: @escaping () -> Bool) async {
    for _ in 0..<100 where !ok() { try? await Task.sleep(nanoseconds: 20_000_000) }
    XCTAssertTrue(ok(), what)
  }

  func testRefreshPutsTheServersDocumentOnThePage() async throws {
    PathStub.replies["/v1/users/me/ui/home"] = (200, homeDoc)
    let s = try session()
    let store = TodayHomeStore()
    store.start(s)
    XCTAssertNil(store.home)
    await store.refresh()
    await wait { store.home != nil }
    XCTAssertEqual(store.home?.pace?.limit, 3)
  }

  func testAFlagTurnedOffOnTheServerRemovesTheDocument() async throws {
    PathStub.replies["/v1/users/me/ui/home"] = (404, "{}")
    let s = try session()
    try s.db.saveDoc(TodayHome.docKey, json: Data(homeDoc.utf8))
    let store = TodayHomeStore()
    store.start(s)
    XCTAssertNotNil(store.home)
    await store.refresh()
    await wait { store.home == nil }
    XCTAssertNil(try s.db.doc(TodayHome.docKey))
  }

  func testTheStateBelongsToTheAccount() throws {
    let a = try session(), b = try session()
    try a.db.saveDoc(TodayHome.docKey, json: Data(homeDoc.utf8))
    let storeA = TodayHomeStore(), storeB = TodayHomeStore()
    storeA.start(a)
    storeB.start(b)
    XCTAssertEqual(storeA.home?.focus, ["sleep"])
    XCTAssertNil(storeB.home)
  }

  func testAnEditIsOnThePageAtOnceAndGoesThroughTheQueue() async throws {
    // no reply for the PUT: offline. The change stays on the page and in the queue, and a refresh does not undo it
    let s = try session()
    try s.db.saveDoc(TodayHome.docKey, json: Data(homeDoc.utf8))
    let store = TodayHomeStore()
    store.start(s)
    store.setModule("sleep", state: "hidden")
    await wait { store.home?.hidden.contains("sleep") == true }
    let items = try s.sync.queue.all()
    XCTAssertEqual(items.map(\.path), ["/v1/users/me/ui/modules/sleep"])
    XCTAssertEqual(items.first?.kind, TodayHome.queueKind)
    XCTAssertEqual(items.first?.method, "PUT")
    PathStub.replies["/v1/users/me/ui/home"] = (200, homeDoc)
    await store.refresh()
    XCTAssertEqual(store.home?.hidden.contains("sleep"), true)
    XCTAssertFalse(PathStub.paths.contains("/v1/users/me/ui/home"))
  }

  func testOverviewSeenClearsItLocallyAtOnce() async throws {
    let s = try session()
    try s.db.saveDoc(TodayHome.docKey, json: Data(homeDoc.utf8))
    let store = TodayHomeStore()
    store.start(s)
    XCTAssertNotNil(store.home?.overview)
    store.overviewSeen()
    await wait { store.home?.overview == nil }
    XCTAssertEqual(try s.sync.queue.all().map(\.path), ["/v1/users/me/ui/overview-seen"])
  }

  func testOnceSentTheServersOrderReplacesTheLocalOne() async throws {
    let answer = homeDoc.replacingOccurrences(of: #""focus": ["sleep"]"#, with: #""focus": ["fat"]"#)
    PathStub.replies["/v1/users/me/ui/focus"] = (200, answer)
    PathStub.replies["/v1/users/me/ui/home"] = (200, answer)
    let s = try session()
    try s.db.saveDoc(TodayHome.docKey, json: Data(homeDoc.utf8))
    let store = TodayHomeStore()
    store.start(s)
    store.setFocus(["fat"])
    await wait { PathStub.paths.contains("/v1/users/me/ui/home") }
    await wait { store.home?.focus == ["fat"] }
    XCTAssertEqual(try s.sync.queue.all().count, 0)
  }
}
