import XCTest
@testable import Watch2Whoop

// Someone else's read-only view: it stays in memory, the server decides what is shown. Every value here is made up.
@MainActor
final class MemberViewTests: XCTestCase {
  private let person = ViewablePerson(id: 42, displayName: "Friend", remark: "Nickname", avatar: nil)

  override func setUp() {
    super.setUp()
    PathStub.replies = [:]
    PathStub.paths = []
  }

  private func store(_ client: APIClient = PathStub.client()) -> MemberViewStore { MemberViewStore(person: person, client: client) }

  // MARK: - what may be asked

  func testOnlyGetsOfTheViewedPersonsReadablePaths() {
    let ok = { (r: APIRequest) in MemberViewStore.allows(r, user: "42") }
    for r in [Endpoint.today("42"), Endpoint.today("42", date: "2026-01-02"), Endpoint.trends(days: 61, end: nil, user: "42"),
              Endpoint.sleepNight("2026-01-02", user: "42"), Endpoint.sleepNights(end: nil, user: "42"), Endpoint.monthDays("2026-01", user: "42"),
              Endpoint.explain("metric", "hrv", day: nil, user: "42"), Endpoint.report("week", end: nil, user: "42"),
              Endpoint.journalAnalysis(end: nil, user: "42"), Endpoint.dayActivity("2026-01-02", user: "42"), Endpoint.memory(user: "42"),
              APIRequest.get("/v1/users/42/labs/uric_acid/history"), APIRequest.get("/v1/users/42/recipes/7")] {
      XCTAssertTrue(ok(r), r.path)
    }
    // never my own data under their name
    for r in [Endpoint.today(), Endpoint.trends(days: 7, end: nil), Endpoint.today("43"), Endpoint.memory()] { XCTAssertFalse(ok(r), r.path) }
    // never the ECG, AI settings, or anything that is not on the list
    for p in ["ecg", "ecg/1", "heartbeats", "cardio", "ai-providers", "ai-tokens", "ai-choice", "ai-fallback", "sync/overview", "profile",
              "weight/recent", "todayish", "meal-templates"] {
      XCTAssertFalse(ok(.get("/v1/users/42/\(p)")), p)
    }
    // never a write, whatever the path
    for m in [APIRequest.Method.post, .put, .patch, .delete] {
      XCTAssertFalse(ok(APIRequest(method: m, path: "/v1/users/42/water")))
    }
    XCTAssertFalse(ok(APIRequest(method: .get, path: "/v1/users/42/water", body: Data())))
  }

  func testNoRequestLeavesForAPathThatIsNotAllowed() async {
    PathStub.replies = ["/v1/users/me/today": (200, #"{"date": "2026-01-02"}"#), "/v1/users/42/ecg": (200, "{}")]
    let s = store()
    let mine = Endpoint.today(), ecg = APIRequest.get("/v1/users/42/ecg")
    await s.load([mine, ecg])
    XCTAssertEqual(PathStub.paths, [])
    XCTAssertEqual(s.state(mine), .denied)
    XCTAssertNil(s.json(mine))
    XCTAssertNil(s.json(ecg))
  }

  // MARK: - the server decides

  func testRefusalsAndFailures() async {
    let s = store()
    let ok = s.request("supplements"), refused = s.request("labs/summary"), off = s.request("water")
    let block = s.request("training/week"), down = s.request("events")
    PathStub.replies = [ok.path: (200, #"[{"id": 1, "name": "A"}]"#), refused.path: (403, #"{"detail": "no"}"#), off.path: (404, "{}"),
                        block.path: (200, #"{"error": "no", "code": "forbidden"}"#)]
    await s.load([ok, refused, off, block, down])
    XCTAssertEqual(s.state(ok), .loaded)
    XCTAssertEqual(s.list(ok)?.count, 1)
    XCTAssertEqual(s.state(refused), .denied)
    XCTAssertEqual(s.state(off), .denied)
    XCTAssertEqual(s.state(block), .denied)
    XCTAssertNil(s.json(block))
    XCTAssertEqual(s.state(down), .offline)

    // the owner takes the share back: the next refresh drops what was on screen
    PathStub.replies[ok.path] = (403, #"{"detail": "no"}"#)
    await s.load([ok], force: true)
    XCTAssertEqual(s.state(ok), .denied)
    XCTAssertNil(s.list(ok))

    // no connection on a refresh: what is on screen stays
    let t = s.request("today")
    PathStub.replies[t.path] = (200, #"{"date": "2026-01-02"}"#)
    await s.load([t])
    PathStub.replies[t.path] = nil
    await s.load([t], force: true)
    XCTAssertEqual(s.state(t), .loaded)
    XCTAssertNotNil(s.json(t))
  }

  func testPagesAreTheOnesTheServerAnswered() async {
    let today = "/v1/users/42/today"
    // a circle-style share: scores, sleep, training
    PathStub.replies = [
      today: (200, #"{"date": "2026-01-02", "scores": {"today": {"recovery": 70}}, "sleep": {"date": "2026-01-02"}, "training": {},"# +
        #""reminders": {"error": "x", "code": "forbidden"}, "nutrition": {"error": "x", "code": "forbidden"}}"#),
      "/v1/users/42/trends": (200, #"{"dates": ["2026-01-02"], "series": {"recovery": {"values": [70]}}}"#),
      "/v1/users/42/sleep/nights": (200, #"{"nights": []}"#),
      "/v1/users/42/memory": (403, "{}"), "/v1/users/42/water": (403, "{}"),
      "/v1/users/42/training/calendar": (200, #"{"days": []}"#), "/v1/users/42/journal/items": (403, "{}"),
      "/v1/users/42/supplements": (403, "{}"), "/v1/users/42/labs/summary": (403, "{}"), "/v1/users/42/events": (403, "{}"),
    ]
    var s = store()
    await s.start()
    XCTAssertEqual(s.phase, .ready)
    XCTAssertEqual(s.today, "2026-01-02")
    XCTAssertTrue(s.scores, "the dial and the readings")
    XCTAssertTrue(s.hasDay, "今日详情")
    XCTAssertEqual(s.pages, [.training], "no food, health or report door: not shared")

    // everything shared
    PathStub.replies[today] = (200, #"{"date": "2026-01-02", "scores": {}, "nutrition": {"intake": {}}}"#)
    for p in ["memory", "water", "journal/items", "supplements", "labs/summary", "events"] { PathStub.replies["/v1/users/42/\(p)"] = (200, "[]") }
    PathStub.replies["/v1/users/42/reports"] = (200, "{}")
    s = store()
    await s.start()
    XCTAssertEqual(s.pages, MemberPage.allCases)

    // only their food: no dial, no 今日详情, just the 饮食 door
    PathStub.replies = [today: (200, #"{"date": "2026-01-02", "scores": {"error": "x", "code": "forbidden"}, "nutrition": {"intake": {}}}"#)]
    for p in ["trends", "memory", "water", "training/calendar", "journal/items", "supplements", "labs/summary", "events", "reports"] {
      PathStub.replies["/v1/users/42/\(p)"] = (403, "{}")
    }
    s = store()
    await s.start()
    XCTAssertEqual(s.phase, .ready)
    XCTAssertFalse(s.scores)
    XCTAssertFalse(s.hasDay)
    XCTAssertEqual(s.pages, [.food])

    // an ordinary answer with nothing shared
    PathStub.replies[today] = (200, #"{"date": "2026-01-02", "scores": {"error": "x", "code": "forbidden"}}"#)
    s = store()
    await s.start()
    XCTAssertEqual(s.phase, .notShared)
    XCTAssertEqual(s.pages, [])
    XCTAssertFalse(s.hasDay)

    // the share is gone
    PathStub.replies = [today: (403, "{}")]
    s = store()
    await s.start()
    XCTAssertEqual(s.phase, .notShared)

    // offline: try again, not "not shared"
    PathStub.replies = [:]
    s = store()
    await s.start()
    XCTAssertEqual(s.phase, .offline)
  }

  func testTodayModelHidesWhatIsNotShared() {
    let refused: [String: Any] = ["error": "x", "code": "forbidden"]
    var m = TodayModel.member(today: ["date": "2026-01-02", "scores": refused, "sleep": refused, "reminders": refused],
                              activity: ["date": "2026-01-02", "daily": ["steps": 1234]], coach: nil, monitor: nil, memory: nil, trends: nil,
                              theirToday: "2026-01-02")
    XCTAssertEqual(m.rings, [])
    XCTAssertNil(m.error, "a refused block is not an error")
    XCTAssertEqual(m.sections.map(\.id), ["activity"])
    XCTAssertEqual(m.reminders, [])
    XCTAssertFalse(m.isPast)

    m = TodayModel.member(today: ["date": "2026-01-01", "scores": ["today": ["recovery": 70, "hrv_night_ms": 50]], "reminders": refused],
                          activity: nil, coach: nil, monitor: nil, memory: nil, trends: nil, theirToday: "2026-01-02")
    XCTAssertEqual(m.rings.map(\.key), ["sleep", "recovery", "strain"])
    XCTAssertEqual(m.rings[1].value, "70")
    XCTAssertTrue(m.isPast)
    XCTAssertEqual(m.sections.map(\.id), ["recovery"])
    XCTAssertFalse(m.local, "someone else's numbers are never the phone's")
  }

  func testViewableListLeavesMeOut() {
    let list: [[String: Any]] = [["id": 1, "display_name": "Me", "self": true, "scopes": NSNull()],
                                 ["id": 42, "display_name": "Friend", "self": false, "scopes": ["scores"], "remark": "Nick", "avatar": "/a.png"]]
    let p = ViewablePerson.parse(list)
    XCTAssertEqual(p.map(\.id), [42])
    XCTAssertEqual(p[0].shownName, "Nick")
    XCTAssertEqual(ViewablePerson(id: 2, displayName: "Name", remark: "").shownName, "Name")
    XCTAssertEqual(ViewablePerson.parse(nil), [])
  }

  // MARK: - it stays in memory

  /// The shared loaders take the view, not a database: the view has none to give them. (That the phone's database file holds
  /// nothing of theirs after a real session was checked end to end, see the PR.)
  func testSharedLoadersAskOnlyForThemAndClosingDropsEverything() async throws {
    PathStub.replies = ["/v1/users/42/today": (200, #"{"date": "2026-01-02", "scores": {"today": {"recovery": 70}}}"#),
                        "/v1/users/42/explain/metric/hrv": (200, #"{"title": "HRV"}"#),
                        "/v1/users/42/sleep/night": (200, #"{"date": "2026-01-02"}"#)]
    let s = store()
    await s.start()
    XCTAssertEqual(s.today, "2026-01-02")

    // the shared loaders, given the view
    let explain = ExplainLoader()
    explain.start(s, kind: "metric", key: "hrv", day: nil)
    let night = SleepNightStore()
    night.show(s, date: nil)
    let report = ReportStore()
    await report.load(s, end: nil, regression: false)
    for _ in 0..<200 where explain.model == nil || night.model == nil { try await Task.sleep(nanoseconds: 10_000_000) }
    XCTAssertEqual(explain.model?.title, "HRV")
    XCTAssertEqual(night.model?.date, "2026-01-02")

    // every request was theirs
    XCTAssertFalse(PathStub.paths.isEmpty)
    XCTAssertTrue(PathStub.paths.allSatisfy { $0.hasPrefix("/v1/users/42/") }, "\(PathStub.paths)")

    s.close()
    XCTAssertNil(s.json(s.todayRequest(nil)))
    XCTAssertEqual(s.state(s.todayRequest(nil)), .loading)
    XCTAssertNil(s.today)
    XCTAssertEqual(s.pages, [])
    XCTAssertFalse(s.scores)
    // an answer on its way after closing goes nowhere
    let before = PathStub.paths.count
    await s.load([s.todayRequest(nil)])
    XCTAssertEqual(PathStub.paths.count, before)
    XCTAssertNil(s.json(s.todayRequest(nil)))
  }

  func testTheClientKeepsNoCopyAnywhere() {
    let c = APIClient.uncachedSession.configuration
    XCTAssertNil(c.urlCache)
    XCTAssertNil(c.httpCookieStorage)
    XCTAssertNil(c.urlCredentialStorage)
    XCTAssertEqual(c.requestCachePolicy, .reloadIgnoringLocalCacheData)
    let api = APIClient(baseURL: URL(string: "https://body.example.com")!, token: { "example-token" })
    XCTAssertEqual(try api.uncached().urlRequest(.get("/v1/users/42/today")).value(forHTTPHeaderField: "Authorization"), "Bearer example-token")
  }

  // MARK: - 2.1.0: the same levels as my 今天, read-only, nothing beyond the share

  /// The allow-list is the contract with the server's share scopes: moving the view onto the levels did not widen it.
  func testReadableListIsUnchanged() {
    XCTAssertEqual(MemberViewStore.readable, [
      "today", "trends", "vo2max", "sleep/details", "sleep/nights", "sleep/night", "days", "strain-coach", "health-monitor", "memory",
      "reports", "journal/analysis", "journal/items", "journal/frequent", "journal", "training/day", "training/calendar", "training/week",
      "training/muscle-load", "exercises", "water", "recipes", "supplements", "labs/summary", "labs", "events", "explain",
    ])
  }

  /// Their day for the first glance and 今日详情: a refused block is not there (no food face, no reminders), the self-help step
  /// under an empty dial (consent, sync, sleep help: about my own data) is never offered, and every request is theirs.
  func testTheirDayDrawsOnlyWhatIsShared() async {
    let refused = #"{"error": "x", "code": "forbidden"}"#
    PathStub.replies = [
      "/v1/users/42/today": (200, #"{"date": "2026-01-02", "scores": "# + refused + #", "nutrition": "# + refused + #", "reminders": "# + refused + "}"),
      "/v1/users/42/training/calendar": (403, "{}"),
    ]
    let s = store()
    await s.start()
    let d = MemberDayStore(member: s)
    await d.load()
    XCTAssertTrue(d.hasData)
    XCTAssertNil(d.model.recoveryNext, "no self-help step on someone else's dial")
    XCTAssertEqual(d.model.reminders, [])
    XCTAssertNil(d.nutrition, "no 饮食 face")
    XCTAssertNil(d.month, "no 训练 face")
    XCTAssertFalse(s.scores, "no dial, no readings")
    XCTAssertFalse(PathStub.paths.isEmpty)
    XCTAssertTrue(PathStub.paths.allSatisfy { $0.hasPrefix("/v1/users/42/") }, "\(PathStub.paths)")

    // the day moves, never past their today, and stays theirs
    d.step(1)
    XCTAssertNil(d.date)
    d.step(-1)
    XCTAssertEqual(d.date, "2026-01-01")
  }

  /// A metric page of someone else reads their range through the view (no database, nothing to observe), switches range the
  /// same way, and asks only for their paths.
  func testTheirMetricPageReadsOnlyThem() async throws {
    let doc = TodayCoreTests.doc(series: ["hrv": TodayCoreTests.series(TodayCoreTests.steady(50, spread: 4, day: 55))], end: "2026-01-02")
    let json = String(data: try JSONSerialization.data(withJSONObject: doc), encoding: .utf8)!
    PathStub.replies = ["/v1/users/42/trends": (200, json)]
    let s = store()
    let t = TrendsStore(member: s, range: .week, end: nil)
    XCTAssertTrue(t.isObserving, "nothing of mine to observe")
    await t.refresh()
    XCTAssertEqual(t.page?.data.series["hrv"]?.values.last ?? nil, 55)
    t.setRange(.month)
    for _ in 0..<100 where t.loading || PathStub.paths.count < 4 { try await Task.sleep(nanoseconds: 10_000_000) }
    XCTAssertEqual(t.range, .month)
    XCTAssertTrue(PathStub.paths.allSatisfy { $0.hasPrefix("/v1/users/42/") }, "\(PathStub.paths)")
  }

  /// No write entry in the view's own code: it sends nothing but GETs through `MemberViewStore.load`, never syncs, never asks
  /// an AI, never opens my forms. (The shared pages it reuses check `memberView` themselves: see the hosted test below.)
  func testTheViewItselfHasNoWriteEntries() throws {
    let dir = URL(fileURLWithPath: #filePath).deletingLastPathComponent().deletingLastPathComponent()
      .appendingPathComponent("Watch2Whoop/Features/MemberView")
    let files = try FileManager.default.contentsOfDirectory(at: dir, includingPropertiesForKeys: nil).filter { $0.pathExtension == "swift" }
    XCTAssertGreaterThanOrEqual(files.count, 3)
    for f in files {
      let code = ReachabilityTests.release(try String(contentsOf: f, encoding: .utf8))
      for token in [".post", ".put", ".patch", ".delete", "enqueue(", "saveDoc", "askAI", "AskAI", "JarvisRouter", "HealthSyncService", "syncNow",
                    "SyncNudges", "TodaySyncCapsule", "TodayFoodFace", "TodayCirclesFace", "WeightSheet", ".weight", "setReminder", "api.data(",
                    "session.api", "session.db"] {
        XCTAssertFalse(code.contains(token), "\(f.lastPathComponent) has \(token)")
      }
    }
  }

  /// 今日详情 is the same page for me and for them; on theirs nothing writes or leads to my own data: no ＋ on 体重, the
  /// weight card opens its explanation instead of 记体重, no sync nudges. (The page reads `memberView` for this.)
  func testTheirDayDetailHasNoWriteControls() {
    XCTAssertEqual(DayWrites.of(isPast: false, member: true), DayWrites(logWeight: false, weightCard: false, syncNudges: false))
    XCTAssertEqual(DayWrites.of(isPast: true, member: true), DayWrites(logWeight: false, weightCard: false, syncNudges: false))
    // my own day keeps them (the rule is not "never")
    XCTAssertEqual(DayWrites.of(isPast: false, member: false), DayWrites(logWeight: true, weightCard: true, syncNudges: true))
    XCTAssertEqual(DayWrites.of(isPast: true, member: false), DayWrites(logWeight: false, weightCard: true, syncNudges: false))
  }

  /// A metric their day's document does not carry is not shared with me: no row on their 今日详情 (on mine it is a 「—」 row,
  /// so the page still says the metric exists).
  func testTheirDayDetailLeavesOutMetricsNotShared() {
    let d = TodayCoreTests.page(TodayCoreTests.doc(series: ["recovery": TodayCoreTests.series(TodayCoreTests.steady(60, spread: 5, day: 70)),
                                                            "resp_rate": TodayCoreTests.series(TodayCoreTests.steady(14, spread: 0.3, day: 14.2))]))
    let theirs = LevelRows.day(d, onlyPresent: true).flatMap(\.rows).map(\.id)
    XCTAssertEqual(Set(theirs), ["recovery", "resp_rate"])
    let mine = LevelRows.day(d).flatMap(\.rows)
    XCTAssertTrue(mine.contains { $0.id == "hrv" && $0.value == nil }, "my own day keeps the row, as 「—」")
    // the dial's own number still makes a row (it is the first glance's)
    XCTAssertTrue(LevelRows.day(d, overrides: ["strain": 4.2], onlyPresent: true).flatMap(\.rows).contains { $0.id == "strain" })
  }

  // MARK: - the sheets' stores do nothing without a session

  func testInertStoresReadAndWriteNothing() async {
    let today = TodayStore()
    today.setReminder(1, status: "seen")
    await today.refresh()
    today.select("2026-01-02")
    XCTAssertFalse(today.isObserving)
    XCTAssertNil(today.date)
    let trends = TrendsStore()
    await trends.refresh()
    XCTAssertNil(trends.page)
    XCTAssertEqual(PathStub.paths, [])
  }
}
