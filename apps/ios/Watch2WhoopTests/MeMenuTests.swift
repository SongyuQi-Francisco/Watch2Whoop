import XCTest
@testable import Watch2Whoop

// 我's structure (2.0.1, MeMenu.swift): a short first level, everything else one page further, nothing more than two taps
// away, 删除账号 under 账号 (App Review), 共享给我的 as one row that opens a page.
final class MeMenuTests: XCTestCase {
  /// Every row that existed on 我 in 2.0.0 (the pages are new; the people of 共享给我的 are on their page).
  private let everyEntry: [MeItem] = [
    .inbox, .syncCenter, .unsent, .ai, .notifications, .invite,
    .supplements, .labs, .events, .bodyAge, .memory,
    .dataStatus, .hevy,
    .bedtimeGoal, .trainingSettings, .language, .appearance,
    .handbook, .feedback, .whatsNew, .privacy, .terms, .licences, .version,
    .profile, .signIn, .lateInvite, .signOut, .deleteAccount,
  ]

  private func everythingOn() -> MeMenu {
    MeMenu(flags: ["hevy_import", "muscle_load", AppearanceChoice.flag, "inbox"], inbox: true, unread: 2, unsent: 3, shared: 7,
           referrals: true, inviteOptional: true, handbook: true, privacy: true, terms: true)
  }

  private func nothingOn() -> MeMenu { MeMenu() }

  /// Rows one tap away (the first level) and two taps away (a page opened from it), with the page each sits on.
  private func reachable(_ m: MeMenu) -> [MeItem: MePage?] {
    var out: [MeItem: MePage?] = [:]
    for item in m.firstLevel.flatMap(\.items) {
      out[item] = .some(nil)
      if case .page(let p) = item {
        for sub in m.groups(p).flatMap(\.items) { out[sub] = p }
      }
    }
    return out
  }

  func testTheFirstLevelIsShortEvenWithEverythingOn() {
    let m = everythingOn()
    XCTAssertLessThanOrEqual(m.firstLevel.count, 4)
    XCTAssertLessThanOrEqual(m.firstLevel.flatMap(\.items).count, 12)
    XCTAssertEqual(m.firstLevel.flatMap(\.items), [
      .inbox, .syncCenter, .unsent,
      .page(.shared), .page(.healthRecord), .page(.data),
      .ai, .notifications, .page(.preferences),
      .invite, .page(.help), .page(.account),
    ])
  }

  func testEveryEntryIsAtMostTwoTapsAway() {
    let r = reachable(everythingOn())
    for item in everyEntry { XCTAssertTrue(r.keys.contains(item), "\(item) is not within two taps") }
    XCTAssertEqual(Set(r.keys).subtracting(everyEntry).filter { if case .page = $0 { return false }; return true }, [])
    // and each row exactly once: a page never repeats a first-level row
    let m = everythingOn()
    let all = m.firstLevel.flatMap(\.items) + MePage.allCases.flatMap { m.groups($0).flatMap(\.items) }
    XCTAssertEqual(all.count, Set(all).count)
  }

  func testDeleteAccountIsTwoTapsAwayUnderAccount() {
    for m in [everythingOn(), nothingOn()] {
      XCTAssertTrue(m.firstLevel.flatMap(\.items).contains(.page(.account)))
      XCTAssertEqual(reachable(m)[.deleteAccount], .some(.account))
      XCTAssertEqual(reachable(m)[.signOut], .some(.account))
      XCTAssertEqual(m.groups(.account).last?.items, [.signOut, .deleteAccount])
    }
  }

  func testSharedWithMeIsOneRowAndOnlyWhenSomeoneShares() {
    var m = everythingOn()
    XCTAssertEqual(m.firstLevel.flatMap(\.items).filter { $0 == .page(.shared) }.count, 1)
    XCTAssertEqual(m.firstLevel[1].items.first, .page(.shared))
    m.shared = 0
    XCTAssertFalse(m.firstLevel.flatMap(\.items).contains(.page(.shared)))
    XCTAssertEqual(m.groups(.shared), [])   // the people are listed by the page itself
  }

  func testRowsBehindAFlagFollowIt() {
    let off = reachable(nothingOn())
    for item in [MeItem.inbox, .hevy, .trainingSettings, .appearance, .invite, .lateInvite, .unsent, .handbook, .privacy, .terms] {
      XCTAssertFalse(off.keys.contains(item), "\(item) shows without its flag / the server's answer")
    }
    let on = reachable(everythingOn())
    XCTAssertEqual(on[.hevy], .some(.data))
    XCTAssertEqual(on[.trainingSettings], .some(.preferences))
    XCTAssertEqual(on[.appearance], .some(.preferences))
    XCTAssertEqual(on[.lateInvite], .some(.account))
    XCTAssertEqual(on[.invite], .some(nil))   // circles' growth hook stays on the first level
    XCTAssertEqual(on[.inbox], .some(nil))
  }

  func testAPageWithOneRowIsThatRow() {
    // without Hevy, 数据与导入 would hold 数据状态 alone: the first level shows 数据状态 itself
    let m = nothingOn()
    XCTAssertTrue(m.firstLevel.flatMap(\.items).contains(.dataStatus))
    XCTAssertFalse(m.firstLevel.flatMap(\.items).contains(.page(.data)))
    XCTAssertEqual(m.entry(.data), [.dataStatus])
    XCTAssertEqual(everythingOn().entry(.data), [.page(.data)])
  }

  func testUnsentSitsNextToSyncOnlyWhileThereIsSomething() {
    var m = nothingOn()
    XCTAssertEqual(m.firstLevel.first?.items, [.syncCenter])
    m.unsent = 2
    XCTAssertEqual(m.firstLevel.first?.items, [.syncCenter, .unsent])
  }

  func testEachRowOpensItsOwnSheetOrDoesSomethingElse() {
    let others: Set<MeItem> = [.inbox, .syncCenter, .language, .handbook, .privacy, .terms, .version, .signOut, .deleteAccount]
    let sheets = everyEntry.filter { !others.contains($0) }.map { ($0, $0.sheet) }
    for (item, sheet) in sheets { XCTAssertNotNil(sheet, "\(item) opens nothing") }
    XCTAssertEqual(Set(sheets.compactMap { $0.1?.id }).count, sheets.count)   // no two rows open the same sheet
    for item in others { XCTAssertNil(item.sheet) }
    for p in MePage.allCases { XCTAssertNil(MeItem.page(p).sheet) }
  }

  func testEveryRowAndPageHasATitle() {
    for item in everyEntry + MePage.allCases.map(MeItem.page) {
      XCTAssertFalse(item.title.isEmpty, "\(item)")
    }
  }
}
