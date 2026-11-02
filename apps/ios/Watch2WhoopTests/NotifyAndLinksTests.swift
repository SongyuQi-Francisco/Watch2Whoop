import XCTest
@testable import Watch2Whoop

/// The way to the sync center and the history reminder, what a tapped notification opens, the consent's circle notice, the
/// invite link and the user guide (pure parts; the screens are in docs/ios-native-1.0.9-regression.md's run).
final class NotifyAndLinksTests: XCTestCase {
  // MARK: - links

  func testNotificationLinks() {
    XCTAssertEqual(AppLink.parse("/app/?tab=friends&feed=12"), .post(12))
    XCTAssertEqual(AppLink.parse("/app/#tab=friends&post=9"), .post(9))
    XCTAssertEqual(AppLink.parse("/app/#tab=friends"), .friends)
    XCTAssertEqual(AppLink.parse("/app/?tab=friends"), .friends)
    XCTAssertEqual(AppLink.parse("/app/?whatsnew=1"), .whatsNew)
    XCTAssertEqual(AppLink.parse("/app/?tab=me"), .tab(.me))
    XCTAssertEqual(AppLink.parse("/app/?tab=today"), .tab(.today))
    XCTAssertEqual(AppLink.parse("/app/?tab=trends"), .tab(.trends))
    XCTAssertEqual(AppLink.parse("/app/?tab=food"), .tab(.food))
    XCTAssertEqual(AppLink.parse("/app/?tab=record"), .tab(.record))
    XCTAssertEqual(AppLink.parse("/app/?tab=me&ai_cards=1"), .tab(.me))     // quota cards: the tab at least
    XCTAssertEqual(AppLink.parse("watch2whoop://circle?code=ABCDEFGHJK"), .join("ABCDE-FGHJK"))
    XCTAssertNil(AppLink.parse("/app/"))                                    // opening the app is the whole answer
    XCTAssertNil(AppLink.parse("/app/#notifications"))
    XCTAssertNil(AppLink.parse("/app/?tab=nonsense"))
    XCTAssertNil(AppLink.parse(""))
  }

  @MainActor func testTapOnNotificationRoutes() {
    let app = AppModel(store: CredentialStore())
    app.handle(link: "/app/?whatsnew=1")
    XCTAssertEqual(app.route, .whatsNew)
    app.handle(link: "/app/?tab=me")
    XCTAssertEqual(app.route, .tab(.me))
    app.handle(link: "/app/?tab=friends&feed=3")
    XCTAssertEqual(app.route, .circles(post: 3))
    app.route = nil
    app.handle(link: "/app/")
    XCTAssertNil(app.route)
  }

  func testTabALinkLandsOn() {
    XCTAssertEqual(AppTab.food.landing(circles: true), .record)     // 饮食 lives inside 记录 while 朋友 has its place
    XCTAssertEqual(AppTab.food.landing(circles: false), .food)
    XCTAssertEqual(AppTab.circles.landing(circles: false), .today)
    XCTAssertEqual(AppTab.circles.landing(circles: true), .circles)
    XCTAssertEqual(AppTab.me.landing(circles: true), .me)
  }

  func testInviteLinkIsTheLandingPage() {
    let link = InviteShare.webLink(server: "https://body.example.com", code: "QWERT-YUP23")
    XCTAssertEqual(link?.absoluteString, "https://body.example.com/app/join.html#QWERT-YUP23")
    XCTAssertEqual(CircleLink.parse(link!.absoluteString), .join("QWERT-YUP23"))
    XCTAssertEqual(AppLink.parse(link!.absoluteString), .join("QWERT-YUP23"))
    XCTAssertEqual(InviteCode.extract("加入我的圈子：邀请码 QWERT-YUP23 \(link!.absoluteString)"), "QWERT-YUP23")
    XCTAssertEqual(InviteCode.extract(link!.absoluteString), "QWERT-YUP23")
    XCTAssertEqual(CircleLink.parse("https://body.example.com/app/?circle=qwert-yup23"), .join("QWERT-YUP23"))   // older links
    XCTAssertNil(CircleLink.parse("https://body.example.com/app/join.html"))
  }

  func testHandbookFollowsTheServerAndTheLanguage() {
    XCTAssertEqual(HandbookLink.url(server: "https://body.example.com", zh: true)?.absoluteString, "https://body.example.com/app/handbook/")
    XCTAssertEqual(HandbookLink.url(server: "https://body.example.com", zh: false)?.absoluteString, "https://body.example.com/app/handbook/en/")
    XCTAssertEqual(HandbookLink.url(server: "http://127.0.0.1:8214/", zh: true)?.absoluteString, "http://127.0.0.1:8214/app/handbook/")
    XCTAssertNil(HandbookLink.url(server: "", zh: true))
    XCTAssertNil(HandbookLink.url(server: "watch2whoop://x", zh: true))   // SFSafariViewController takes http(s) only
  }

  // MARK: - history reminder

  private func coverage(state: String = "due", show: Bool = true, active: Bool = false, percent: Int? = nil,
                        days: Int = 14, label: String? = "恢复分基线还在建立", muted: Bool = false) -> BackfillCoverage? {
    var j: [String: Any] = ["days": days, "target_days": 90, "status": "building",
                            "progress": ["active": active, "percent": percent as Any],
                            "remind": ["state": state, "show": show, "muted": muted], "action": ["kind": "backfill", "days": 90]]
    if let label { j["impact_label"] = label }
    return BackfillCoverage(j)
  }

  func testCoverageParsing() throws {
    XCTAssertNil(BackfillCoverage(nil))
    XCTAssertNil(BackfillCoverage(["days": 3]))
    let c = try XCTUnwrap(coverage())
    XCTAssertEqual(c.days, 14)
    XCTAssertEqual(c.targetDays, 90)
    XCTAssertEqual(c.askDays, 90)
    XCTAssertTrue(c.due)
    XCTAssertFalse(c.complete)
    XCTAssertEqual(c.fraction, 14.0 / 90, accuracy: 0.001)
    XCTAssertTrue(c.subtitle.hasPrefix(tr("14 / 90 天", "14 / 90 days")))
    XCTAssertTrue(c.subtitle.contains("恢复分基线还在建立"))
  }

  func testCardShowsOnReminderDaysAndWhileRunning() throws {
    let due = try XCTUnwrap(coverage())
    XCTAssertTrue(due.showsCard(overviewState: "ok"))
    XCTAssertTrue(due.showsCard(overviewState: "syncing"))
    XCTAssertFalse(due.showsCard(overviewState: "issue"), "a stuck sync comes first")
    XCTAssertFalse(due.showsCard(overviewState: nil))
    let later = try XCTUnwrap(coverage(state: "later", show: false))
    XCTAssertFalse(later.showsCard(overviewState: "ok"))
    let running = try XCTUnwrap(coverage(state: "progress", show: true, active: true, percent: 40))
    XCTAssertTrue(running.showsCard(overviewState: "issue"))
    XCTAssertEqual(running.fraction, 0.4, accuracy: 0.001)
    XCTAssertEqual(running.subtitle, tr("补全中 40%", "Filling in 40%"))
    let failed = try XCTUnwrap(coverage(state: "error", show: true))
    XCTAssertEqual(failed.subtitle, tr("没能补全", "Could not fill in"))
    XCTAssertFalse(failed.due)
  }

  func testPhoneProgressCounts() throws {
    let later = try XCTUnwrap(coverage(state: "later", show: false))
    XCTAssertEqual(later.withPhone(BackfillState()), later)
    let phone = later.withPhone(BackfillState(["active": true, "percent": 25]))
    XCTAssertTrue(phone.active)
    XCTAssertEqual(phone.percent, 25)
    XCTAssertTrue(phone.showsCard(overviewState: "ok"))
  }

  // MARK: - stuck

  private let plain = SyncDiagnosis(overall: .ok, headline: "ok", issues: [])
  private func issueCard(_ code: String) -> TodayModel.SyncCard {
    TodayModel.SyncCard(state: "issue", label: "x", tone: .mid, ago: nil, issueCode: code, issueLabel: "label \(code)")
  }

  func testStuckBanner() {
    XCTAssertNil(SyncStuck.of(plain, card: nil))
    XCTAssertNil(SyncStuck.of(SyncDiagnosis(overall: .syncing, headline: "s", issues: []), card: issueCard("lagging")))
    let local = SyncDiagnosis(overall: .attention, headline: "h", issues: [
      .init(id: "rate", text: "wait", action: nil), .init(id: "failed", text: "failed", action: .syncNow)])
    XCTAssertEqual(SyncStuck.of(local, card: nil), SyncStuck(text: "failed", retry: true))
    let perm = SyncDiagnosis(overall: .attention, headline: "h", issues: [.init(id: "permission", text: "allow", action: .requestPermission)])
    XCTAssertEqual(SyncStuck.of(perm, card: nil), SyncStuck(text: "allow", retry: false), "the sync center has that button")
    let notSynced = SyncDiagnosis(overall: .attention, headline: "2 not synced", issues: [])
    XCTAssertEqual(SyncStuck.of(notSynced, card: nil), SyncStuck(text: "2 not synced", retry: false))
    XCTAssertEqual(SyncStuck.of(plain, card: issueCard("lagging")), SyncStuck(text: "label lagging", retry: true))
    XCTAssertEqual(SyncStuck.of(plain, card: issueCard("background_off")), SyncStuck(text: "label background_off", retry: false))
    XCTAssertNil(SyncStuck.of(plain, card: issueCard("type_missing")), "not a stuck sync (sync-status.js BANNER_CODES)")
    XCTAssertNil(SyncStuck.of(plain, card: TodayModel.SyncCard(state: "ok", label: "x", tone: .good, ago: nil)))
  }

  func testTodayModelReadsTheOverview() {
    var inp = TodayInputs(today: [:])
    inp.sync = ["state": "issue", "label": "Behind", "issue": ["code": "lagging", "label": "Behind", "tone": "mid"],
                "coverage": ["days": 3, "target_days": 90, "remind": ["state": "later", "show": false]]]
    let m = TodayModel(inp)
    XCTAssertEqual(m.sync?.issueCode, "lagging")
    XCTAssertEqual(m.sync?.issueLabel, "Behind")
    XCTAssertEqual(m.coverage?.days, 3)
  }

  // MARK: - consent and what's new

  func testConsentNoticeNamesTheCircleAndWhatItSees() throws {
    let p = try JSONDecoder().decode(Profile.self, from: Data(#"{"circle_pending": [{"circle_id": 3, "name": "Example Club", "scopes": ["scores", "sleep"]}]}"#.utf8))
    let c = try XCTUnwrap(p.circlePending.first)
    XCTAssertEqual(c.name, "Example Club")
    let text = ConsentView.joinNotice(c)
    XCTAssertTrue(text.contains("Example Club"))
    XCTAssertTrue(text.contains(CircleScope.label("scores")) && text.contains(CircleScope.label("sleep")))
    XCTAssertTrue(ConsentView.joinNotice(PendingCircle(name: "X")).contains(tr("什么都不共享", "nothing")))
    XCTAssertTrue(try JSONDecoder().decode(Profile.self, from: Data("{}".utf8)).circlePending.isEmpty)
    XCTAssertTrue(ConsentView.thanks(joined: ["Example Club"]).contains("Example Club"))
    XCTAssertFalse(ConsentView.thanks(joined: []).isEmpty)
    let a = try JSONDecoder().decode(ConsentAccepted.self, from: Data(#"{"circles_joined": [{"circle_id": 3, "name": "Example Club", "scopes": []}]}"#.utf8))
    XCTAssertEqual(a.circlesJoined.map(\.name), ["Example Club"])
  }

  func testWhatsNewKeepsTheEntryLink() throws {
    let j = #"{"weeks": [{"week_start": "2026-10-26", "items": [{"date": "2026-10-28", "text": "A", "url": "/app/?tab=me"}, {"date": "2026-10-27", "text": "B", "url": null}]}]}"#
    let items = try XCTUnwrap(JSONDecoder().decode(WhatsNew.self, from: Data(j.utf8)).weeks.first?.items)
    XCTAssertEqual(items.map(\.url), ["/app/?tab=me", nil])
  }
}
