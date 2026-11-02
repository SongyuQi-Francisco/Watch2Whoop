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
    XCTAssertEqual(s.tabs, [.today, .trends, .sleep, .record])

    // everything shared
    PathStub.replies[today] = (200, #"{"date": "2026-01-02", "scores": {}, "nutrition": {"intake": {}}}"#)
    for p in ["memory", "water", "journal/items", "supplements", "labs/summary", "events"] { PathStub.replies["/v1/users/42/\(p)"] = (200, "[]") }
    s = store()
    await s.start()
    XCTAssertEqual(s.tabs, MemberTab.allCases)

    // an ordinary answer with nothing shared
    PathStub.replies = [today: (200, #"{"date": "2026-01-02", "scores": {"error": "x", "code": "forbidden"}}"#)]
    for p in ["trends", "sleep/nights", "memory", "water", "training/calendar", "journal/items", "supplements", "labs/summary", "events"] {
      PathStub.replies["/v1/users/42/\(p)"] = (403, "{}")
    }
    s = store()
    await s.start()
    XCTAssertEqual(s.phase, .notShared)
    XCTAssertEqual(s.tabs, [])

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
    XCTAssertEqual(s.tabs, [])
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
