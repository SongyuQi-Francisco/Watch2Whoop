import XCTest
@testable import Watch2Whoop

// Circles (#340 N7). All names and numbers are made up (示例 / example).

private enum Fixture {
  static let list = """
  {"circles": [{"id": 3, "name": "Example gym", "owner_id": 1, "is_owner": true, "personal": false,
                "avatar": "/v1/friends/circles/3/avatar?v=9", "my_scopes": ["scores", "training"],
                "members": [{"id": 1, "display_name": "Alex", "owner": true, "me": true, "joined_at": "2026-01-02T08:00:00+00:00"},
                            {"id": 2, "display_name": "Sam", "remark": "Coach", "avatar": "/v1/avatars/users/2?v=4",
                             "join": {"via": "invite_code", "by": {"display_name": "Alex", "me": true}}}],
                "prefs": {"pinned": false, "muted": true, "archived": false}, "recent_at": "2026-01-03T09:00:00+00:00",
                "invites": {"live": 1, "codes": [{"id": 8, "created_at": "2026-01-02T08:00:00+00:00", "expires_at": null, "used_count": 2}], "until": null}},
               {"id": 4, "name": "Example family", "is_owner": false, "my_scopes": [], "members": [],
                "prefs": {"pinned": true, "muted": false, "archived": false}}],
   "invites": [{"id": 11, "circle_id": 5, "name": "Example club", "from_name": "Kim", "members": 4,
                "default_scopes": ["scores"], "expires_at": "2026-01-09T00:00:00+00:00"}],
   "prefs_on": true, "scopes": ["scores", "sleep", "training"], "default_scopes": ["scores"], "consent_ok": true,
   "limits": {"circles": 10, "members": 30, "invite_days": [1, 7], "invite_default_days": 7}, "search_from": 6}
  """

  static let overview = """
  {"me": {"id": 1, "display_name": "Alex", "self": true, "date": "2026-01-03", "shared": null, "hidden": [],
          "scores": {"recovery": 66, "zone": "yellow", "label": "Fair", "sleep_min": 412, "strain": 9.4, "state": "scored",
                     "last_sync_min": 12, "last_sync_date": "2026-01-03",
                     "week": [{"date": "2026-01-03", "today": true, "recovery": 66, "zone": "yellow"}]},
          "trained": true, "received": {"clap": ["Sam"], "sleep": [], "train": []},
          "seen": {"count": 1, "scopes": ["scores"], "varies": false, "hidden": ["training"]}},
   "members": [{"id": 2, "display_name": "Sam", "self": false, "shared": ["training"], "hidden": ["scores"],
                "scores": null, "trained": false, "sent": ["clap"], "remark": "Coach"},
               {"id": 7, "display_name": "Old", "scores": {"recovery": 50, "synced_today": false, "week": []}}],
   "nudge_kinds": ["clap", "sleep", "train"], "has_circles": true,
   "circles": [{"id": 3, "name": "Example gym", "members": 2, "archived": false}, {"id": 4, "name": "Archived", "members": 1, "archived": true}]}
  """

  static func post(_ id: Int, _ date: String, likes: Int = 0, mine: Bool = false, comments: Int = 0) -> String {
    """
    {"id": \(id), "date": "\(date)", "user": {"id": 2, "name": "Sam", "self": false, "avatar": null},
     "circles": ["Example gym"], "circle_refs": [{"id": 3, "name": "Example gym", "avatar": null}],
     "title": "Strength training", "duration_min": 55, "started_at": "\(date)T07:00:00+00:00",
     "exercise_count": 1, "set_count": 2, "volume_kg": 1200.5, "pr_count": 1,
     "exercises": [{"slug": "squat", "name": "Squat", "thumb": null, "muscles": ["quadriceps"],
                    "sets": [{"reps": 5, "weight_kg": 100}, {"reps": 5, "weight_kg": 110.5}], "volume_kg": 1052.5, "pr": true}],
     "active_kcal": 320, "avg_hr": 121, "max_hr": 160,
     "activities": [{"type": "strength", "kind": "strength", "name": "Strength", "duration_min": 55, "hr_zones": [10, 20, 15, 8, 2], "load": 80}],
     "no_exercises": false, "likes": {"count": \(likes), "mine": \(mine)}, "comment_count": \(comments),
     "preview_comments": [{"id": 1, "user": {"id": 1, "name": "Alex"}, "body": "Nice"}]}
    """
  }

  static func page(_ posts: [String], next: String?) -> String {
    #"{"posts": [\#(posts.joined(separator: ","))], "next": \#(next.map { "\"\($0)\"" } ?? "null"), "has_circles": true, "preview_exercises": 3, "comment_preview": 2, "comment_max": 300}"#
  }
}

private func decode<T: Decodable>(_ s: String, as: T.Type = T.self) throws -> T {
  try JSONDecoder.api.decode(T.self, from: Data(s.utf8))
}

final class CircleModelTests: XCTestCase {
  func testCircleListDecodesAndOrders() throws {
    let l = try decode(Fixture.list, as: CirclesList.self)
    XCTAssertEqual(l.circles.count, 2)
    let gym = l.circles[0]
    XCTAssertTrue(gym.isOwner)
    XCTAssertEqual(gym.members[1].shownName, "Coach")
    XCTAssertEqual(gym.members[1].join?.via, "invite_code")
    XCTAssertEqual(gym.invites?.codes.first?.usedCount, 2)
    XCTAssertNil(gym.invites?.codes.first?.expiresAt)
    XCTAssertEqual(l.invites.first?.fromName, "Kim")
    XCTAssertEqual(l.limits.inviteDays, [1, 7])
    // pinned first (prefs_on)
    XCTAssertEqual(l.ordered.shown.map(\.id), [4, 3])
    // a cached copy round-trips
    let again = try decode(String(data: try JSONEncoder.api.encode(l), encoding: .utf8)!, as: CirclesList.self)
    XCTAssertEqual(again, l)
    // an older server: no moderation fields → nothing asked, nothing blocked
    XCTAssertFalse(l.rulesNeeded)
    XCTAssertFalse(l.banned)
    XCTAssertFalse(gym.members[1].blocked)
  }

  func testModerationFieldsAndRulesText() throws {
    let l = try decode("""
    {"circles": [{"id": 3, "name": "Example gym", "members": [{"id": 2, "display_name": "Sam", "blocked": true}]}],
     "rules": {"required": true, "accepted": false, "version": "2026-10-31"}, "banned": false, "blocked_count": 1}
    """, as: CirclesList.self)
    XCTAssertTrue(l.rulesNeeded)
    XCTAssertEqual(l.rules.version, "2026-10-31")
    XCTAssertEqual(l.blockedCount, 1)
    XCTAssertTrue(l.circles[0].members[0].blocked)
    let again = try decode(String(data: try JSONEncoder.api.encode(l), encoding: .utf8)!, as: CirclesList.self)
    XCTAssertEqual(again, l)
    let b = try decode(#"{"blocks": [{"id": 2, "display_name": "Sam", "created_at": "2026-01-02T08:00:00+00:00"}]}"#,
                       as: [String: [BlockedPerson]].self)
    XCTAssertEqual(b["blocks"]?.first?.displayName, "Sam")
    // the rules Markdown: headings, bullets (continuation lines joined), paragraphs
    let blocks = MarkdownBlocks.blocks("# Rules\n\nVersion: 1\n\n## 1. Not allowed\n\n- spam;\n  more of it\n- hate\n\nText\nnext line")
    XCTAssertEqual(blocks.map(\.kind), [.h1, .para, .h2, .bullet, .bullet, .para])
    XCTAssertEqual(blocks[3].text, "spam; more of it")
    XCTAssertEqual(blocks[5].text, "Text next line")
    XCTAssertEqual(ReportReason.all.count, 7)
    XCTAssertEqual(ReportReason.label("nope"), ReportReason.label("other"))
  }

  func testOverviewKeepsOnlyWhatIsShared() throws {
    let o = try decode(Fixture.overview, as: MemberOverview.self)
    XCTAssertEqual(o.everyone.map(\.id), [1, 2, 7])
    let sam = o.members[0]
    XCTAssertNil(sam.scores)
    XCTAssertTrue(sam.isHidden("scores"))
    XCTAssertEqual(sam.shownName, "Coach")
    XCTAssertEqual(sam.sent, ["clap"])
    XCTAssertTrue(o.me!.isHidden("training"))          // my card: what friends do not see of me
    XCTAssertEqual(o.me?.scores?.week.first?.recovery, 66)
    XCTAssertEqual(o.members[1].scores?.state, "stale")    // older server: synced_today only
    XCTAssertEqual(o.circles?.count, 2)
    let again = try decode(String(data: try JSONEncoder.api.encode(o), encoding: .utf8)!, as: MemberOverview.self)
    XCTAssertEqual(again, o)
  }

  func testFeedAndLeaderboard() throws {
    let page = try decode(Fixture.page([Fixture.post(9, "2026-01-03", likes: 2)], next: "2026-01-03:9"), as: FeedPage.self)
    let p = try XCTUnwrap(page.posts.first)
    XCTAssertEqual(p.circleRefs.first?.name, "Example gym")
    XCTAssertEqual(p.exercises.first?.sets.last?.weightKg, 110.5)
    XCTAssertEqual(p.activities.first?.hrZones?.count, 5)
    XCTAssertEqual(p.likes, Post.Likes(count: 2, mine: false))
    XCTAssertEqual(page.next, "2026-01-03:9")
    XCTAssertEqual(FeedFormat.set(p.exercises[0].sets[1]), "110.5 kg × 5")
    let b = try decode("""
    {"board": "body", "window_days": 7, "boards": ["body", "activity"], "rows": [
      {"id": 1, "display_name": "Alex", "self": true, "status": "ok", "score": 71, "rank": 1, "sub": {"green_days": 4, "sleep_avg_min": 430, "note": "x"}},
      {"id": 2, "display_name": "Sam", "status": "not_shared", "score": null, "sub": {}, "rank": null}]}
    """, as: Leaderboard.self)
    XCTAssertEqual(b.rows.first?.sub["green_days"], 4)
    XCTAssertNil(b.rows.first?.sub["note"])
    XCTAssertNil(b.rows.last?.rank)
  }
}

final class FeedLogicTests: XCTestCase {
  private func page(_ ids: [(Int, String)], next: String?) throws -> FeedPage {
    try decode(Fixture.page(ids.map { Fixture.post($0.0, $0.1) }, next: next), as: FeedPage.self)
  }

  func testHeadRefreshMergesAndDropsWhatIsGone() throws {
    var cache = FeedLogic.mergeHead(FeedCache(), try page([(9, "2026-01-03"), (8, "2026-01-02")], next: "2026-01-02:8"))
    cache = FeedLogic.append(cache, try page([(7, "2026-01-01"), (6, "2025-12-31")], next: nil))
    XCTAssertEqual(cache.posts.map(\.id), [9, 8, 7, 6])
    XCTAssertNil(cache.next)
    // a new post on top; 9 is gone on the server (inside the fresh page's range); 7 and 6 are older and stay
    cache = FeedLogic.mergeHead(cache, try page([(10, "2026-01-04"), (8, "2026-01-02")], next: "2026-01-02:8"))
    XCTAssertEqual(cache.posts.map(\.id), [10, 8, 7, 6])
    XCTAssertNil(cache.next)   // the cache already reached the end
    // the server has nothing more than one post: everything else is gone
    cache = FeedLogic.mergeHead(cache, try page([(10, "2026-01-04")], next: nil))
    XCTAssertEqual(cache.posts.map(\.id), [10])
    // nothing at all (left every circle)
    cache = FeedLogic.mergeHead(cache, try page([], next: nil))
    XCTAssertTrue(cache.posts.isEmpty)
  }

  func testFirstHeadTakesTheServerCursor() throws {
    let cache = FeedLogic.mergeHead(FeedCache(), try page([(9, "2026-01-03")], next: "2026-01-03:9"))
    XCTAssertEqual(cache.next, "2026-01-03:9")
  }

  func testQueuedLikesAndCommentsAreLaidOver() throws {
    let posts = try page([(9, "2026-01-03")], next: nil).posts
    let me = PostUser(id: 1, name: "Alex", isSelf: true)
    func item(_ id: Int64, _ kind: String, _ method: String, _ path: String, _ body: String? = nil,
              _ state: OutboxItem.State = .pending) -> OutboxItem {
      var o = OutboxItem(id: id, clientId: "c\(id)", kind: kind, groupKey: "g", method: method, path: path,
                         body: body.map { Data($0.utf8) }, createdAt: 0)
      o.state = state
      return o
    }
    let pending = [
      item(1, FeedLogic.likeKind, "PUT", Endpoint.Circles.like(9)),
      item(2, FeedLogic.commentKind, "POST", Endpoint.Circles.comments(9), #"{"body":"Great set"}"#),
      item(3, FeedLogic.commentKind, "POST", Endpoint.Circles.comments(9), #"{"body":"Refused"}"#, .failed),
      item(4, FeedLogic.commentKind, "POST", Endpoint.Circles.comments(5), #"{"body":"Other post"}"#),
    ]
    let shown = FeedLogic.overlay(posts, pending: pending, me: me, commentPreview: 2)[0]
    XCTAssertEqual(shown.likes, Post.Likes(count: 1, mine: true))
    XCTAssertEqual(shown.commentCount, 2)
    XCTAssertEqual(shown.previewComments.map(\.body), ["Great set", "Refused"])
    let queued = FeedLogic.queuedComments(pending, post: 9, me: me)
    XCTAssertEqual(queued.map(\.id), [-2, -3])
    XCTAssertEqual(queued.map(\.local), [.pending, .failed])
    // an unlike that matches the server already changes nothing
    let same = FeedLogic.overlay(posts, pending: [item(5, FeedLogic.likeKind, "DELETE", Endpoint.Circles.like(9))], me: me, commentPreview: 2)
    XCTAssertEqual(same[0].likes, posts[0].likes)
  }
}

final class CircleLinkTests: XCTestCase {
  func testNotificationAndInviteLinks() {
    XCTAssertEqual(CircleLink.parse("/app/?tab=friends&feed=42"), .post(42))
    XCTAssertEqual(CircleLink.parse("watch2whoop://feed?post=7"), .post(7))
    XCTAssertEqual(CircleLink.parse("https://body.example.com/app/?circle=abcde-fghjk"), .join("ABCDE-FGHJK"))
    XCTAssertEqual(CircleLink.parse("watch2whoop://circle?code=ABCDEFGHJK"), .join("ABCDE-FGHJK"))
    XCTAssertNil(CircleLink.parse("/app/#notifications"))
    XCTAssertNil(CircleLink.parse("/app/?tab=today&feed=3"))
  }

  func testCodeFromPastedText() {
    XCTAssertEqual(InviteCode.extract("Join my circle \u{201C}Example\u{201D} on Watch2Whoop: invite code QWERT-YUP23 https://body.example.com/app/?circle=QWERT-YUP23"), "QWERT-YUP23")
    XCTAssertEqual(InviteCode.extract("  qwert yup23 "), "QWERT-YUP23")
    XCTAssertEqual(InviteCode.extract("EXAMPLECLUB"), "EXAMPLECLUB")   // an activation code of another shape
    XCTAssertNil(InviteCode.extract("  "))
    XCTAssertEqual(InviteShare.webLink(server: "https://body.example.com", code: "QWERT-YUP23")?.absoluteString,
                   "https://body.example.com/app/?circle=QWERT-YUP23")
    XCTAssertEqual(apiRequest(relative: "/v1/avatars/users/2?v=4").query.first?.value, "4")
  }
}

final class QueueClaimTests: XCTestCase {
  private var clock = 1_000.0

  func testSupersedeKeepsOnlyTheNewestLike() throws {
    let q = OfflineQueue(db: try AppDatabase.inMemory(), now: { [unowned self] in self.clock })
    try q.enqueue(kind: FeedLogic.likeKind, group: "circle.like:9", method: .put, path: "/like/9", body: nil)
    try q.supersede(group: "circle.like:9")
    try q.enqueue(kind: FeedLogic.likeKind, group: "circle.like:9", method: .delete, path: "/like/9", body: nil)
    XCTAssertEqual(try q.all().map(\.method), ["DELETE"])
  }

  func testTwoDrainsNeverSendTheSameEntryTwice() async throws {
    let q = OfflineQueue(db: try AppDatabase.inMemory(), now: { [unowned self] in self.clock })
    for i in 0..<5 { try q.enqueue(kind: FeedLogic.commentKind, group: "c:\(i)", method: .post, path: "/c/\(i)", body: nil) }
    let sent = Sent()
    async let a = q.drain { item in await sent.add(item.path); try? await Task.sleep(nanoseconds: 5_000_000); return .done }
    async let b = q.drain { item in await sent.add(item.path); try? await Task.sleep(nanoseconds: 5_000_000); return .done }
    let (na, nb) = try await (a, b)
    XCTAssertEqual(na + nb, 5)
    let paths = await sent.paths
    XCTAssertEqual(paths.count, 5)
    XCTAssertEqual(Set(paths).count, 5)
  }

  func testAnEntryCutOffMidSendGoesAgainAfterItsLease() async throws {
    let db = try AppDatabase.inMemory()
    let q = OfflineQueue(db: db, now: { [unowned self] in self.clock })
    try q.enqueue(kind: FeedLogic.commentKind, group: "c", method: .post, path: "/c", body: nil)
    // the app was killed while it was being sent
    try await db.writer.write { db in try db.execute(sql: "UPDATE outbox SET state = 'sending', next_attempt_at = ?", arguments: [self.clock + OfflineQueue.lease]) }
    XCTAssertTrue(try q.due().isEmpty)
    clock += OfflineQueue.lease + 1
    var sent = 0
    _ = try await q.drain { _ in sent += 1; return .done }
    XCTAssertEqual(sent, 1)
    XCTAssertTrue(try q.all().isEmpty)
  }
}

private actor Sent {
  var paths: [String] = []
  func add(_ p: String) { paths.append(p) }
}

@MainActor
final class CirclesStoreTests: XCTestCase {
  override func setUp() {
    super.setUp()
    for k in ["w2w.circles.circle.v1", "w2w.circles.board.v1", "w2w.circles.mode.v1"] { UserDefaults.standard.removeObject(forKey: k) }
  }

  private func session() throws -> AccountSession {
    let c = URLSessionConfiguration.ephemeral
    c.protocolClasses = [StubRouter.self]
    let api = APIClient(baseURL: URL(string: "https://body.example.com")!, token: { "example-token" },
                        session: URLSession(configuration: c), sleep: { _ in })
    var cfg = AccountConfig(server: "https://body.example.com")
    cfg.userId = 1
    return AccountSession(config: cfg, api: api, db: try AppDatabase.inMemory(), flags: ["friends", "circle_feed"])
  }

  func testRefreshCachesAndALikeIsQueuedAndShownAtOnce() async throws {
    let s = try session()
    let store = CirclesStore(session: s)
    // the requests run in parallel: answer by path, not by order
    StubRouter.install([
      "/v1/friends": Fixture.list, "/v1/friends/overview": Fixture.overview,
      "/v1/friends/feed": Fixture.page([Fixture.post(9, "2026-01-03", likes: 1)], next: nil),
      "/v1/friends/feed/unread": #"{"count": 3}"#,
    ])
    defer { StubRouter.uninstall() }
    await store.refresh(force: true)
    XCTAssertEqual(store.list?.circles.count, 2)
    XCTAssertEqual(store.overview?.members.count, 2)
    XCTAssertEqual(store.posts.map(\.id), [9])
    XCTAssertEqual(store.unread, 3)
    // a second store (the Today card, the next launch) opens on the cache without the network
    let cached = CirclesStore(session: s)
    XCTAssertEqual(cached.posts.map(\.id), [9])
    XCTAssertEqual(cached.list?.circles.first?.name, "Example gym")

    // offline: the like shows at once and waits in the queue
    StubRouter.offline = true
    store.setLike(store.posts[0], true)
    XCTAssertEqual(store.posts[0].likes, Post.Likes(count: 2, mine: true))
    try await Task.sleep(nanoseconds: 300_000_000)
    let queued = try s.sync.queue.all()
    XCTAssertEqual(queued.map(\.kind), [FeedLogic.likeKind])
    XCTAssertEqual(queued.first?.method, "PUT")
    // un-like before it went out: only the newest state stays queued
    store.setLike(store.posts[0], false)
    try await Task.sleep(nanoseconds: 300_000_000)
    XCTAssertEqual(try s.sync.queue.all().map(\.method), ["DELETE"])
    XCTAssertEqual(store.posts[0].likes, Post.Likes(count: 1, mine: false))

    // a comment offline: listed as sending under the post
    store.addComment(9, "  Example comment ")
    try await Task.sleep(nanoseconds: 300_000_000)
    XCTAssertEqual(store.commentList(9)?.last?.body, "Example comment")
    XCTAssertEqual(store.commentList(9)?.last?.local, .pending)
    XCTAssertEqual(store.posts[0].commentCount, 1)
    // the comment carries its queue entry's client_id: a resend after a timeout is not a second comment (N2b)
    let comment = try XCTUnwrap(try s.sync.queue.all().first { $0.kind == FeedLogic.commentKind })
    let sent = try JSONSerialization.jsonObject(with: try XCTUnwrap(comment.body)) as? [String: Any]
    XCTAssertEqual(sent?["client_id"] as? String, comment.clientId)
    XCTAssertEqual(sent?["body"] as? String, "Example comment")
  }
}

/// Answers by request path (the store fetches several things at once); `offline` = no connection.
final class StubRouter: URLProtocol {
  static var routes: [String: String] = [:]
  static var offline = false

  static func install(_ r: [String: String]) {
    routes = r
    offline = false
  }

  static func uninstall() { routes = [:]; offline = false }

  override class func canInit(with request: URLRequest) -> Bool { true }
  override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }

  override func startLoading() {
    if Self.offline {
      client?.urlProtocol(self, didFailWithError: URLError(.notConnectedToInternet))
      return
    }
    let body = Self.routes[request.url?.path ?? ""]
    let resp = HTTPURLResponse(url: request.url!, statusCode: body == nil ? 404 : 200, httpVersion: "HTTP/1.1", headerFields: nil)!
    client?.urlProtocol(self, didReceive: resp, cacheStoragePolicy: .notAllowed)
    client?.urlProtocol(self, didLoad: Data((body ?? "{}").utf8))
    client?.urlProtocolDidFinishLoading(self)
  }

  override func stopLoading() {}
}
