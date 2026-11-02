import XCTest
@testable import Watch2Whoop

/// R2 forced upgrade: `/v1/web/me` → `upgrade` and 426 answers turn into the notice banner / the full-screen page.
@MainActor
final class UpgradeGateTests: XCTestCase {
  private func defaults() -> UserDefaults {
    let name = "upgrade-tests-\(UUID().uuidString)"
    let d = UserDefaults(suiteName: name)!
    d.removePersistentDomain(forName: name)
    return d
  }

  func testParseStates() {
    XCTAssertEqual(UpgradeInfo.parse(["state": "none", "must_upgrade": false])?.state, UpgradeInfo.State.none)
    let n = UpgradeInfo.parse(["state": "notice", "must_upgrade": false, "min": "2.0.0", "block_from": "2026-12-01",
                               "url": "https://example.com/app", "title": "T", "message": "M"])
    XCTAssertEqual(n?.state, .notice)
    XCTAssertEqual(n?.url?.absoluteString, "https://example.com/app")
    XCTAssertEqual(n?.minVersion, "2.0.0")
    XCTAssertEqual(n?.blockFrom, "2026-12-01")
    XCTAssertEqual(UpgradeInfo.parse(["state": "notice", "must_upgrade": true])?.state, .block, "must_upgrade wins")
    XCTAssertNil(UpgradeInfo.parse(["url": "http://example.com"])?.url, "https only")
    XCTAssertNil(UpgradeInfo.parse(nil))
  }

  func test426BodyIsAlwaysABlock() {
    let body = Data(#"{"detail":{"code":"upgrade_required","state":"block","message":"M","url":"https://example.com/x"}}"#.utf8)
    let info = UpgradeInfo.blocked(by: body)
    XCTAssertEqual(info.state, .block)
    XCTAssertEqual(info.message, "M")
    XCTAssertEqual(UpgradeInfo.blocked(by: Data("not json".utf8)).state, .block)
  }

  func testNoticeOncePerDayAndServerLiftsTheBlock() {
    var clock = Date(timeIntervalSince1970: 1_800_000_000)
    let gate = UpgradeGate(defaults: defaults(), now: { clock })
    gate.apply(UpgradeInfo(state: .notice))
    XCTAssertTrue(gate.showsNotice)
    gate.closeNotice()
    XCTAssertFalse(gate.showsNotice)
    gate.apply(UpgradeInfo(state: .notice))
    XCTAssertFalse(gate.showsNotice, "closed for the rest of the day")
    clock = clock.addingTimeInterval(86_400)
    gate.apply(UpgradeInfo(state: .notice))
    XCTAssertTrue(gate.showsNotice, "back the next day")

    gate.receivedUpgradeRequired(Data(#"{"detail":{"code":"upgrade_required"}}"#.utf8))
    XCTAssertTrue(gate.showsBlock)
    XCTAssertFalse(gate.showsNotice)
    gate.apply(.clear)   // kill switch: /v1/web/me says none again
    XCTAssertFalse(gate.showsBlock)
  }

  func testNoticeDue() {
    XCTAssertTrue(UpgradeGate.noticeDue(today: "2026-11-01", lastClosed: nil))
    XCTAssertTrue(UpgradeGate.noticeDue(today: "2026-11-02", lastClosed: "2026-11-01"))
    XCTAssertFalse(UpgradeGate.noticeDue(today: "2026-11-01", lastClosed: "2026-11-01"))
  }

  func testClientReads426DetailMessage() async {
    StubProtocol.reset([.init(status: 426, body: #"{"detail":{"code":"upgrade_required","message":"please update"}}"#)])
    let api = APIClient(baseURL: URL(string: "https://body.example.com")!, token: { "example-token" },
                        session: StubProtocol.session(), sleep: { _ in })
    do {
      _ = try await api.data(.get("/v1/users/me/today"))
      XCTFail("426 must throw")
    } catch let e as APIError {
      XCTAssertEqual(e, .http(426, "please update"))
      XCTAssertFalse(e.isRetryable)
    } catch {
      XCTFail("\(error)")
    }
    let r = StubProtocol.requests.first
    XCTAssertEqual(r?.value(forHTTPHeaderField: "X-App-Client"), "ios-native")
  }
}
