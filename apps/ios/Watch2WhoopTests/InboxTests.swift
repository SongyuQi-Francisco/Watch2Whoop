import XCTest
@testable import Watch2Whoop

// 我 → 消息 (#359). All names are made up (示例 / example).

private enum Fixture {
  static func note(_ id: String, at: String = "2026-11-02T08:00:00+00:00", read: Bool = false, kind: String = "circle.like") -> String {
    """
    {"id": "\(id)", "type": "circle", "kind": "\(kind)", "at": "\(at)", "read": \(read), "title": "Example", "body": "",
     "url": "/app/?tab=friends&feed=12", "circle_id": 3}
    """
  }

  static let card = """
  {"id": "invite:7", "type": "circle_invite", "at": "2026-11-02T08:00:00+00:00", "read": false,
   "title": "Kim invited you", "body": "Members will see", "url": null,
   "invite": {"invite_id": 7, "circle_name": "Example club", "avatar": "/v1/users/me/inbox/invites/7/avatar?v=2",
              "owner_name": "Kim", "members": 4, "expires_at": "2026-11-16T08:00:00+00:00",
              "default_scopes": ["scores", "training"],
              "shares": [{"scope": "scores", "label": "Scores"}, {"scope": "training", "label": "Workouts"}],
              "consent_ok": false, "rules_ok": true, "can_mute": true}}
  """

  static let week = """
  {"id": "whatsnew:2026-10-25", "type": "whatsnew", "at": "2026-10-26T08:00:00+00:00", "read": true, "title": "This week",
   "body": "One new thing", "url": "/app/?whatsnew=1", "entries": [{"date": "2026-10-20", "text": "Example", "url": null}]}
  """

  static let notice = """
  {"id": "notice:3", "type": "notice", "at": "2026-10-20T08:00:00+00:00", "read": false, "title": "Example notice", "body": "Body",
   "url": "https://example.com/x", "link_label": "Open it"}
  """

  static func page(_ items: [String], unread: Int, more: Bool = false, next: String? = nil) -> InboxPage {
    let json = """
    {"items": [\(items.joined(separator: ","))], "unread": \(unread), "more": \(more),
     "next": \(next.map { "\"\($0)\"" } ?? "null"), "now": "2026-11-02T09:00:00Z", "keep_days": 90}
    """
    return try! JSONDecoder.api.decode(InboxPage.self, from: Data(json.utf8))
  }
}

final class InboxModelTests: XCTestCase {
  func testEveryKindOfItemDecodes() throws {
    let p = Fixture.page([Fixture.note("notification:1"), Fixture.card, Fixture.week, Fixture.notice], unread: 3)
    XCTAssertEqual(p.items.map(\.type), ["circle", "circle_invite", "whatsnew", "notice"])
    XCTAssertEqual(p.items[0].circleId, 3)
    let card = try XCTUnwrap(p.items[1].invite)
    XCTAssertEqual(card.inviteId, 7)
    XCTAssertEqual(card.shares.map(\.label), ["Scores", "Workouts"])
    XCTAssertFalse(card.consentOk)
    XCTAssertTrue(card.canMute)
    XCTAssertEqual(p.items[2].entries.map(\.text), ["Example"])
    XCTAssertEqual(p.items[3].linkLabel, "Open it")
  }

  func testAnUnknownTypeStillDecodes() {
    let p = Fixture.page(["""
    {"id": "later:1", "type": "something_new", "at": null, "read": false, "title": "Later"}
    """], unread: 1)
    XCTAssertEqual(p.items.first?.title, "Later")
  }

  func testTheCacheSurvivesBeingSaved() throws {
    var c = InboxLogic.mergeHead(InboxCache(), Fixture.page([Fixture.card, Fixture.week], unread: 1, more: true, next: "c"))
    c.pendingRead = ["x"]
    let again = try JSONDecoder.api.decode(InboxCache.self, from: try JSONEncoder.api.encode(c))
    XCTAssertEqual(again, c)
    XCTAssertEqual(again.items.first?.invite?.circleName, "Example club")
  }
}

final class InboxLogicTests: XCTestCase {
  func testTheFirstPageIsTheTruthForItsRange() {
    var c = InboxCache()
    c.items = Fixture.page([Fixture.note("notification:1"), Fixture.card, Fixture.notice], unread: 3).items
    // the invite was withdrawn on the server
    let out = InboxLogic.mergeHead(c, Fixture.page([Fixture.note("notification:2"), Fixture.note("notification:1")], unread: 2))
    XCTAssertEqual(out.items.map(\.id), ["notification:2", "notification:1"])
    XCTAssertEqual(out.unread, 2)
    XCTAssertNil(out.next)
  }

  func testWhatWasLoadedFurtherDownStaysWhenThereIsMore() {
    var c = InboxCache()
    c.items = Fixture.page([Fixture.note("notification:1"), Fixture.notice], unread: 2).items
    c.next = "older"
    let out = InboxLogic.mergeHead(c, Fixture.page([Fixture.note("notification:2"), Fixture.note("notification:1")], unread: 2, more: true, next: "page1"))
    XCTAssertEqual(out.items.map(\.id), ["notification:2", "notification:1", "notice:3"])
    XCTAssertEqual(out.next, "older")
  }

  func testAFurtherPageGoesBelowWithoutDuplicates() {
    var c = InboxCache()
    c.items = Fixture.page([Fixture.note("notification:1")], unread: 1).items
    let out = InboxLogic.append(c, Fixture.page([Fixture.note("notification:1"), Fixture.notice], unread: 2))
    XCTAssertEqual(out.items.map(\.id), ["notification:1", "notice:3"])
    XCTAssertNil(out.next)
  }

  func testWhatIsPendingStaysAppliedOverTheServersAnswer() {
    var c = InboxCache()
    c.pendingRead = ["notification:1"]
    c.pendingHidden = ["notice:3"]
    let out = InboxLogic.mergeHead(c, Fixture.page([Fixture.note("notification:1"), Fixture.note("notification:2"), Fixture.notice], unread: 3))
    XCTAssertEqual(out.items.map(\.id), ["notification:1", "notification:2"])
    XCTAssertEqual(out.items.map(\.read), [true, false])
    XCTAssertEqual(out.unread, 1)
  }

  func testPendingMarkAllRead() {
    var c = InboxCache()
    c.pendingAllRead = true
    let out = InboxLogic.mergeHead(c, Fixture.page([Fixture.note("notification:1"), Fixture.notice], unread: 9))
    XCTAssertTrue(out.items.allSatisfy(\.read))
    XCTAssertEqual(out.unread, 0)
  }

  func testMarkingAndRemovingKeepTheCount() {
    var c = InboxCache()
    c.items = Fixture.page([Fixture.note("notification:1"), Fixture.note("notification:2", read: true), Fixture.notice], unread: 2).items
    c.unread = 2
    XCTAssertTrue(InboxLogic.markRead(&c, "notification:1"))
    XCTAssertFalse(InboxLogic.markRead(&c, "notification:1"))
    XCTAssertFalse(InboxLogic.markRead(&c, "missing"))
    XCTAssertEqual(c.unread, 1)
    InboxLogic.remove(&c, "notification:2")   // read already: the count stays
    XCTAssertEqual(c.unread, 1)
    InboxLogic.remove(&c, "notice:3")
    XCTAssertEqual(c.unread, 0)
    XCTAssertEqual(c.items.map(\.id), ["notification:1"])
    var d = InboxCache()
    d.items = Fixture.page([Fixture.notice], unread: 1).items
    d.unread = 5
    InboxLogic.markAllRead(&d)
    XCTAssertEqual(d.unread, 0)
    XCTAssertTrue(d.items[0].read)
  }

  func testPeople() {
    XCTAssertEqual(InboxLogic.people(1), tr("1 人", "1 person"))
    XCTAssertEqual(InboxLogic.people(4), tr("4 人", "4 people"))
  }

  func testWhen() throws {
    let now = try XCTUnwrap(FeedFormat.parseDate("2026-11-02T12:00:00+00:00"))
    XCTAssertEqual(InboxLogic.when("2026-11-02T11:59:30+00:00", now: now), tr("刚刚", "Just now"))
    XCTAssertEqual(InboxLogic.when("2026-11-02T11:30:00+00:00", now: now), tr("30 分钟前", "30 min ago"))
    XCTAssertEqual(InboxLogic.when("2026-11-02T09:00:00+00:00", now: now), tr("3 小时前", "3 h ago"))
    XCTAssertEqual(InboxLogic.when(nil, now: now), "")
  }
}

final class InboxLinkTests: XCTestCase {
  func testLinksIntoTheInbox() {
    XCTAssertEqual(InboxLink.parse("/app/?tab=me&inbox=invite:3"), InboxLink(item: "invite:3"))
    XCTAssertEqual(InboxLink.parse("/app/?tab=me&inbox=notification:9"), InboxLink(item: "notification:9"))
    XCTAssertEqual(InboxLink.parse("/app/#notifications"), InboxLink(item: nil))
    XCTAssertEqual(InboxLink.parse("/app/?whatsnew=1"), InboxLink(item: nil))
  }

  func testOtherLinksAreNotForTheInbox() {
    XCTAssertNil(InboxLink.parse("/app/?tab=friends&feed=12"))
    XCTAssertNil(InboxLink.parse("/app/?tab=friends&circle_id=3"))
    XCTAssertNil(InboxLink.parse("/app/"))
    XCTAssertNil(InboxLink.parse("watch2whoop://sync"))
    XCTAssertNil(InboxLink.parse("https://example.com/"))
  }

  func testATappedPushOpensItsRowExceptAPost() {
    XCTAssertEqual(InboxLink.pushTarget(url: "/app/?tab=friends&circle_id=3", kind: "circle.joined", nid: 9), "/app/?tab=me&inbox=notification:9")
    XCTAssertEqual(InboxLink.pushTarget(url: "/app/?tab=friends&circle_id=3", kind: "circle.invite_accepted", nid: 9), "/app/?tab=me&inbox=notification:9")
    XCTAssertEqual(InboxLink.pushTarget(url: nil, kind: "system.maintenance", nid: 4), "/app/?tab=me&inbox=notification:4")
    XCTAssertEqual(InboxLink.pushTarget(url: "/app/?tab=friends&feed=12", kind: "circle.like", nid: 9), "/app/?tab=friends&feed=12")
    XCTAssertEqual(InboxLink.pushTarget(url: "/app/?tab=me&inbox=invite:3", kind: "circle.invite", nid: 9), "/app/?tab=me&inbox=invite:3")
    XCTAssertNil(InboxLink.pushTarget(url: nil, kind: "circle.like", nid: nil))
  }

  @MainActor
  func testAppModelRoutesTheLinks() {
    let app = AppModel()
    app.handle(link: "/app/?tab=me&inbox=invite:3")
    XCTAssertEqual(app.route, .inbox(item: "invite:3"))
    app.handle(link: "/app/#notifications")
    XCTAssertEqual(app.route, .inbox(item: nil))
    app.handle(link: "/app/?tab=friends&circle_id=3")
    XCTAssertEqual(app.route, .circles(post: nil))
    app.handle(link: "/app/?tab=friends&feed=12")
    XCTAssertEqual(app.route, .circles(post: 12))
  }
}

final class DirectInviteTests: XCTestCase {
  func testOptionsDecodeAndStatusesHaveLabels() throws {
    let json = """
    {"circles": [{"id": 3, "name": "Example gym", "avatar": null, "members": 2, "status": "open"},
                 {"id": 4, "name": "Example family", "members": 5, "status": "pending"}],
     "per_day": 10, "left_today": 9}
    """
    let o = try JSONDecoder.api.decode(InviteOptions.self, from: Data(json.utf8))
    XCTAssertEqual(o.circles.map(\.status), ["open", "pending"])
    XCTAssertEqual(o.leftToday, 9)
    for s in ["pending", "accepted", "member", "full"] {
      XCTAssertNotEqual(DirectInviteSheet.label(s), s, s)
    }
  }

  func testTheMuteListDecodes() throws {
    let m = try JSONDecoder.api.decode(InviteMute.self, from: Data(#"{"id": 2, "display_name": "Sam", "created_at": "x"}"#.utf8))
    XCTAssertEqual(m.displayName, "Sam")
  }
}
