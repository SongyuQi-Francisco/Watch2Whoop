import XCTest
@testable import Watch2Whoop

/// 2.1.0: the fields PR #452 added to the server, as the app reads and draws them (timer, 4-week muscle load, library
/// count and stars, history cards, weekly training days, the AI allowance used, who can see me, unread faces). Every name and
/// number is made up; a field an older server lacks must leave the old empty state.
final class WireFieldsTests: XCTestCase {
  override func setUp() {
    super.setUp()
    Lang.override = "zh"
  }

  override func tearDown() {
    Lang.override = nil
    super.tearDown()
  }

  private func obj(_ s: String) -> [String: Any] {
    (try? JSONSerialization.jsonObject(with: Data(s.utf8))) as? [String: Any] ?? [:]
  }

  private func date(_ s: String) -> Date { FeedFormat.parseDate(s)! }

  private func pending(_ kind: String, _ payload: [String: Any], at: Double = 0, state: OutboxItem.State? = .pending) -> PendingRecord {
    let data = try! JSONSerialization.data(withJSONObject: payload)
    return PendingRecord(kind: kind, clientId: UUID().uuidString, date: "2026-11-15", payload: data, createdAt: at, state: state, lastError: nil)
  }

  // MARK: - 1. training timer

  private let sessionDoc = """
    {"sessions": [{"name": "上肢 A", "exercises": [{"slug": "bench", "name": "卧推", "target": {"sets": 2, "reps": 8, "weight_kg": 60}}]}],
     "can_log": true,
     "log": {"status": "draft", "exercises": [{"slug": "bench", "sets": [{"reps": 8, "weight_kg": 60, "done": true}]}],
             "started_at": "2026-11-15T18:26:45+11:00", "started_from": "first_set", "first_set_at": "2026-11-15T18:26:45+11:00"}}
    """

  func testSessionReadsTheStart() throws {
    let s = try XCTUnwrap(TrainingSession.build(obj(sessionDoc), day: "2026-11-15", pending: []))
    XCTAssertEqual(s.startedAt, "2026-11-15T18:26:45+11:00")
    XCTAssertEqual(s.startedFrom, "first_set")
    XCTAssertTrue(s.acceptsStart)
  }

  func testOlderServerHasNoStartAndTakesNone() throws {
    let old = obj("""
      {"sessions": [], "can_log": true, "log": {"status": "draft", "exercises": []}}
      """)
    let s = try XCTUnwrap(TrainingSession.build(old, day: "2026-11-15", pending: []))
    XCTAssertNil(s.startedAt)
    XCTAssertFalse(s.acceptsStart)
    let sent = TrainingTimer.startToSend(accepts: false, queued: nil, server: nil, serverFrom: nil, hasDone: true,
                                         firstTick: Date(), now: Date())
    XCTAssertNil(sent)
    XCTAssertNil(s.payload(startedAt: nil)["started_at"])
  }

  func testQueuedSaveKeepsItsStart() throws {
    let save = pending(RecordKind.session, ["exercises": [], "started_at": "2026-11-15T07:00:00+11:00"])
    let s = try XCTUnwrap(TrainingSession.build(obj(sessionDoc), day: "2026-11-15", pending: [save]))
    XCTAssertEqual(s.startedAt, "2026-11-15T07:00:00+11:00")
    let sent = TrainingTimer.startToSend(accepts: true, queued: "2026-11-15T07:00:00+11:00", server: s.startedAt,
                                         serverFrom: "first_set", hasDone: true, firstTick: nil, now: Date())
    XCTAssertEqual(sent, "2026-11-15T07:00:00+11:00")
  }

  func testStartToSend() {
    let tick = date("2026-11-15T18:20:00+11:00")
    let now = date("2026-11-15T18:20:01+11:00")
    // nothing ticked yet: no start
    XCTAssertNil(TrainingTimer.startToSend(accepts: true, queued: nil, server: nil, serverFrom: nil, hasDone: false, firstTick: nil, now: now))
    // a draft's first save is not a start; the tick is
    let sent = TrainingTimer.startToSend(accepts: true, queued: nil, server: "2026-11-15T18:00:00+11:00", serverFrom: "draft",
                                         hasDone: true, firstTick: tick, now: now)
    XCTAssertEqual(sent.flatMap(TrainingTimer.parse), tick)
    // the server's ticked start wins over the phone's
    let kept = TrainingTimer.startToSend(accepts: true, queued: nil, server: "2026-11-15T18:10:00+11:00", serverFrom: "first_set",
                                         hasDone: true, firstTick: tick, now: now)
    XCTAssertEqual(kept, "2026-11-15T18:10:00+11:00")
  }

  func testElapsed() {
    let now = date("2026-11-15T18:49:00+11:00")
    let start = "2026-11-15T18:26:46+11:00"
    let running = TrainingTimer.elapsed(start: start, startedFrom: "first_set", submittedAt: nil, submitted: false, doneSets: 3, now: now)
    XCTAssertEqual(running, 22 * 60 + 14)
    XCTAssertEqual(TrainingTimer.text(running ?? 0), "22:14")
    XCTAssertEqual(TrainingTimer.text(3729), "1:02:09")
    // no set ticked: no timer, even with a draft start
    XCTAssertNil(TrainingTimer.elapsed(start: start, startedFrom: "draft", submittedAt: nil, submitted: false, doneSets: 0, now: now))
    // submitted: the length stays
    let done = TrainingTimer.elapsed(start: start, startedFrom: "first_set", submittedAt: "2026-11-15T19:26:46+11:00",
                                     submitted: true, doneSets: 0, now: date("2026-11-15T23:00:00+11:00"))
    XCTAssertEqual(done, 3600)
    // a start in the future or too long ago is not shown
    XCTAssertNil(TrainingTimer.elapsed(start: "2026-11-15T19:00:00+11:00", startedFrom: "first_set", submittedAt: nil,
                                       submitted: false, doneSets: 1, now: now))
    XCTAssertNil(TrainingTimer.elapsed(start: "2026-11-15T08:00:00+11:00", startedFrom: "first_set", submittedAt: nil,
                                       submitted: false, doneSets: 1, now: now))
    XCTAssertNil(TrainingTimer.elapsed(start: nil, startedFrom: nil, submittedAt: nil, submitted: false, doneSets: 1, now: now))
  }

  // MARK: - 2. four weeks of muscle load

  private let muscleDoc = """
    {"muscles": [
      {"key": "chest", "name": "胸", "ranges": {"week": {"sets": 12}, "last7": {"sets": 12}, "last28": {"sets": 46.5}},
       "target": {"low": 10, "high": 20}, "target_4w": {"low": 40, "high": 80}, "status": {"week": "ok", "last7": "ok", "last28": "ok"}},
      {"key": "lats", "name": "背阔", "ranges": {"week": {"sets": 4}, "last28": {"sets": 20}},
       "target": {"low": 10, "high": 20}, "target_4w": {"low": 40, "high": 80}, "status": {"week": "low", "last28": "low"}}
    ]}
    """

  func testFourWeeksUseTheirOwnRange() throws {
    let m = try XCTUnwrap(MuscleLoad.parse(obj(muscleDoc)))
    XCTAssertTrue(m.hasLast28)
    XCTAssertEqual(MuscleWeekPage.ranges(m), [.week, .last28])
    let chest = try XCTUnwrap(RecordGlance.groups(m, range: .last28).first { $0.key == "chest" })
    XCTAssertEqual(chest.sets, 46.5)
    XCTAssertEqual(chest.low, 40)
    XCTAssertEqual(chest.high, 80)
    XCTAssertEqual(chest.status, "ok")
    let week = try XCTUnwrap(RecordGlance.groups(m, range: .week).first { $0.key == "chest" })
    XCTAssertEqual(week.high, 20)
    let t = RecordGlance.onTarget(m, range: .last28)
    XCTAssertEqual(t.ok, 1)
    XCTAssertEqual(t.total, 2)
    XCTAssertEqual(m.regions(.last28)["chest"]?.status, "ok")
  }

  func testOlderServerKeepsSevenDays() throws {
    let m = try XCTUnwrap(MuscleLoad.parse(obj("""
      {"muscles": [{"key": "chest", "name": "胸", "ranges": {"week": {"sets": 12}, "last7": {"sets": 12}},
        "target": {"low": 10, "high": 20}, "status": {"week": "ok", "last7": "ok"}}]}
      """)))
    XCTAssertFalse(m.hasLast28)
    XCTAssertEqual(MuscleWeekPage.ranges(m), [.week, .last7])
    XCTAssertEqual(RecordGlance.onTarget(m, range: .last28).total, 0)
    XCTAssertEqual(MuscleLoad.Range.card, [.today, .week, .last7])
  }

  // MARK: - 3. library: stars

  func testStarsFromTheServerAndThePhone() {
    let list = LibraryExercise.list(obj("""
      {"exercises": [{"slug": "bench", "name_zh": "卧推", "favorite": true}, {"slug": "row", "name_zh": "划船", "favorite": false},
                     {"slug": "old"}], "library_total": 1268}
      """))
    XCTAssertEqual(list.map(\.favorite), [true, false, nil])
    XCTAssertNil(ExerciseFavorites.shown(server: nil, pending: nil, tapped: nil))
    XCTAssertEqual(ExerciseFavorites.shown(server: false, pending: true, tapped: nil), true)
    XCTAssertEqual(ExerciseFavorites.shown(server: true, pending: true, tapped: false), false)
    let rows = [pending(ExerciseFavorites.kind, ["slug": "row", "favorite": true], at: 1),
                pending(ExerciseFavorites.kind, ["slug": "row", "favorite": false], at: 2),
                pending(ExerciseFavorites.kind, ["slug": "bench", "favorite": false], at: 3, state: .failed)]
    let p = ExerciseFavorites.pending(rows)
    XCTAssertEqual(p["row"], false)
    XCTAssertNil(p["bench"])   // refused: the server's star stays
    let starred = ExerciseFavorites.filtered(list) { x in ExerciseFavorites.shown(server: x.favorite, pending: nil, tapped: x.slug == "row" ? true : nil) }
    XCTAssertEqual(starred.map(\.slug), ["bench", "row"])
    XCTAssertEqual(ExerciseFavorites.path("bench"), "/v1/users/me/training/exercises/bench/favorite")
  }

  // MARK: - 4. history cards

  func testZoneSharesAndSegments() throws {
    XCTAssertNil(HistoryCardWire.zoneShares(nil))
    XCTAssertNil(HistoryCardWire.zoneShares([0, 0, 0, 0, 0]))
    XCTAssertNil(HistoryCardWire.zoneShares([1, 2]))
    let s = try XCTUnwrap(HistoryCardWire.zoneShares([5, 15.5, 40, 19.5, 10]))
    XCTAssertEqual(s.reduce(0, +), 1, accuracy: 1e-9)
    XCTAssertEqual(s[2], 40.0 / 90.0, accuracy: 1e-9)
    let w = HistoryCardWire.segments([0.995, 0, 0, 0, 0.005], minShare: 0.03)
    XCTAssertEqual(w.reduce(0, +), 1, accuracy: 1e-9)
    XCTAssertEqual(w[1], 0)
    XCTAssertGreaterThan(w[4], 0.02)
  }

  func testCardMusclesFromTheServer() throws {
    XCTAssertEqual(HistoryCardWire.regions(["quadriceps", "middle back", "unknown"]), ["quadriceps", "upper-back"])
    XCTAssertTrue(HistoryCardWire.regions(nil).isEmpty)
    let server = obj("""
      {"days": [{"date": "2026-11-14", "sessions_count": 1, "hr_zone_min": [5, 15.5, 40, 19.5, 10],
                 "muscles": ["quadriceps", "glutes"], "muscle_groups": ["legs"]}]}
      """)
    let phone = obj("""
      {"days": [{"date": "2026-11-14", "sessions_count": 1, "hr_zone_min": [1, 2, 3, 4, 5]}]}
      """)
    let m = try XCTUnwrap(TrainingMonth.build(phone, month: "2026-11", server: server))
    let d = try XCTUnwrap(m.days.first)
    XCTAssertEqual(d.muscles, ["quadriceps", "glutes"])
    XCTAssertEqual(d.muscleGroups, ["legs"])
    XCTAssertEqual(d.hrZoneMin, [1, 2, 3, 4, 5])   // the shown document's own minutes
    let old = try XCTUnwrap(TrainingMonth.build(obj(#"{"days": [{"date": "2026-11-14"}]}"#), month: "2026-11"))
    XCTAssertNil(old.days.first?.hrZoneMin)
    XCTAssertNil(old.days.first?.muscles)
  }

  // MARK: - 5. 身体 weekly face

  func testWeeklyFaceCountsLoggedSessions() throws {
    let dates = (0..<7).map { LocalDay.add("2026-11-15", $0 - 6) }
    let base: [String: Any] = ["dates": dates, "series": [String: Any](), "days": 7]
    var withSessions = base
    withSessions["training_sessions"] = ["values": [1, 0, 1, 1, 0, 2, NSNull()], "total": 5]
    let d = try XCTUnwrap(TrendsData(json: withSessions))
    XCTAssertEqual(BodyGlanceRules.trainingDays(d), [true, false, true, true, false, true, nil])
    let old = try XCTUnwrap(TrendsData(json: base))
    XCTAssertNil(old.trainingSessions)
    var broken = base
    broken["training_sessions"] = ["values": NSNull(), "error": "unreadable"]
    XCTAssertNil(TrendsData.sessions(broken))
  }

  // MARK: - 6. 我 → AI face

  func testAIMoneyOnTheFallback() throws {
    let choice = try JSONDecoder().decode(AIChoice.self, from: Data("""
      {"options": [{"key": "fallback", "label": "Jarvis", "current": true, "spent_cny": 1.2, "quota_cny": 3.0}]}
      """.utf8))
    let f = try XCTUnwrap(MeAIFace.of(choice: choice, list: nil))
    XCTAssertEqual(f.usedPct, 40)
    XCTAssertFalse(f.low)
    let list = try JSONDecoder().decode(AIProviderList.self, from: Data("""
      {"providers": [], "fallback": {"remaining_pct": 10, "spent_cny": 2.7, "quota_cny": 3}}
      """.utf8))
    let old = try JSONDecoder().decode(AIChoice.self, from: Data(#"{"options": [{"key": "fallback", "label": "Jarvis", "current": true}]}"#.utf8))
    let g = try XCTUnwrap(MeAIFace.of(choice: old, list: list))
    XCTAssertEqual(g.usedPct, 90)
    XCTAssertTrue(g.low)
    let none = try XCTUnwrap(MeAIFace.of(choice: old, list: nil))
    XCTAssertNil(none.usedPct)
  }

  func testAIUsedPercentRoundsAndCaps() {
    XCTAssertEqual(MeAIFace.usedPct(spent: 1.2, quota: 3), 40)
    XCTAssertEqual(MeAIFace.usedPct(spent: 0.01, quota: 3), 0)
    XCTAssertEqual(MeAIFace.usedPct(spent: 2.99, quota: 3), 100)
    XCTAssertEqual(MeAIFace.usedPct(spent: 4.5, quota: 3), 100)
    XCTAssertEqual(MeAIFace.usedPct(spent: -1, quota: 3), 0)
    XCTAssertNil(MeAIFace.usedPct(spent: 1, quota: 0))
    XCTAssertNil(MeAIFace.usedPct(spent: nil, quota: 3))
    XCTAssertNil(MeAIFace.usedPct(spent: 1, quota: nil))
  }

  // MARK: - 7 / 8. faces

  func testAudienceAndUnreadFaces() throws {
    let who = obj("""
      {"count": 6, "more": 1, "people": [{"id": 7, "name": "小林", "initial": "林", "scopes": ["scores", "sleep"], "avatar": "/v1/avatars/users/7?v=1"},
                                          {"id": 9, "name": "Bea", "initial": "B", "scopes": ["training"]}]}
      """)
    let a = try XCTUnwrap(Audience.parse(who))
    XCTAssertEqual(a.count, 6)
    XCTAssertEqual(a.people.map(\.initial), ["林", "B"])
    XCTAssertEqual(a.people.first?.scopes, ["scores", "sleep"])
    XCTAssertNil(Audience.parse(nil))

    let page = try JSONDecoder().decode(InboxPage.self, from: Data("""
      {"items": [], "unread": 4, "unread_from": [{"type": "person", "id": 7, "name": "Bea", "initial": "B"},
        {"type": "circle_invite", "name": "Night runners", "initial": "N"}, {"type": "system"}]}
      """.utf8))
    let cache = InboxLogic.mergeHead(InboxCache(), page)
    XCTAssertEqual(cache.unreadFaces.map(\.initial), ["B", "N", ""])
    XCTAssertEqual(cache.unreadFaces.last?.system, true)
    var read = cache
    InboxLogic.markAllRead(&read)
    XCTAssertTrue(read.unreadFaces.isEmpty)
    let oldPage = try JSONDecoder().decode(InboxPage.self, from: Data(#"{"items": [], "unread": 2}"#.utf8))
    XCTAssertTrue(InboxLogic.mergeHead(InboxCache(), oldPage).unreadFaces.isEmpty)

    let feed = FacePerson.list(obj(#"{"from": [{"id": 7, "name": "Bea", "initial": "B"}, {"name": "", "initial": ""}]}"#)["from"])
    XCTAssertEqual(feed?.map(\.initial), ["B"])
  }
}
