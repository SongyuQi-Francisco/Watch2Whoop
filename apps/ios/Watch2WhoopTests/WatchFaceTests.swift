import XCTest
@testable import Watch2Whoop

// The watch-face complications' rules (WatchShared/WatchFace.swift, docs/watch-app-plan.md 2.5): which entry at which
// time, the stale / missing states, the rest countdown, the rest day, the reload decision, the taps' routing and the
// one-time move of the store into the App Group. All data is synthetic.

private let utc: Calendar = {
  var c = Calendar(identifier: .gregorian)
  c.timeZone = TimeZone(identifier: "UTC")!
  return c
}()

/// 2026-03-10 09:00 UTC.
private let now = Date(timeIntervalSince1970: 1_773_133_200)
private let todayKey = "2026-03-10"
private let midnight = Date(timeIntervalSince1970: 1_773_187_200)   // 2026-03-11 00:00 UTC

private func exercise(_ slug: String, sets: Int = 4, rest: Int = 90) -> WatchExercise {
  WatchExercise(slug: slug, name: slug, sets: sets, done: 0, reps: 8, weightKg: 60, durationS: nil, weighted: true,
                restSec: rest, weightStepKg: 2.5, last: [])
}

private func context(recovery: Double? = 82, tone: WatchTone? = .good, dataAgo: TimeInterval = 120, plan: Bool = true,
                     rest: Bool = false, phone: WatchPhoneSync = WatchPhoneSync(.ok), lang: String = "zh",
                     day: String = todayKey) -> WatchContext {
  let t = WatchToday(day: day, recovery: recovery, tone: tone, usualLo: 58, usualHi: 74, verdict: "x", reason: recovery == nil ? "y" : nil,
                     sleep: 88, strain: 2.8, computedAt: now.addingTimeInterval(-60), dataAt: now.addingTimeInterval(-dataAgo),
                     phone: phone)
  let p = plan && !rest ? WatchPlan(day: todayKey, title: "Upper A", exercises: [exercise("bench"), exercise("row")]) : nil
  return WatchContext(signedIn: true, plan: p, lang: lang, sentAt: now.addingTimeInterval(-60),
                      planFor: plan || rest ? todayKey : "2026-03-09", today: t)
}

private func heard() -> WatchEventQueue {
  var q = WatchEventQueue()
  q.heard(at: now.addingTimeInterval(-60))
  return q
}

private func inputs(_ c: WatchContext?, session: SavedSession? = nil, queue: WatchEventQueue = heard()) -> FaceTimeline.Inputs {
  FaceTimeline.Inputs(context: c, queue: queue, session: session)
}

private func ids() -> () -> String {
  var n = 0
  return { n += 1; return "e\(n)" }
}

/// A session of `context()`'s plan with `logged` sets of bench done; the last one logged `ago` seconds before now
/// (receipt still up when `ago` < 1.2 s).
private func session(logged: Int, ago: TimeInterval) -> SavedSession {
  var s = SetSession(plan: context().plan!, newId: ids())
  _ = s.primary(at: now.addingTimeInterval(-1000))
  for i in 0..<logged {
    let at = i == logged - 1 ? now.addingTimeInterval(-ago) : now.addingTimeInterval(-900 + Double(i) * 150)
    _ = s.primary(at: at)
    if i < logged - 1 {
      _ = s.tick(at: at.addingTimeInterval(2))
      _ = s.primary(at: at.addingTimeInterval(100))
    }
  }
  return SavedSession(session: s, savedAt: now.addingTimeInterval(-ago))
}

final class WatchFaceTimelineTests: XCTestCase {
  func testFreshDayGreysAtTheStaleLimitThenEmptiesAtMidnight() {
    let e = FaceTimeline.entries(inputs(context()), now: now, calendar: utc)
    XCTAssertEqual(e.map(\.date), [now, now.addingTimeInterval(-120 + Double(WatchParams.staleAfterMin) * 60), midnight])
    XCTAssertEqual(e[0].recovery, 82)
    XCTAssertEqual(e[0].tone, .good)
    XCTAssertFalse(e[0].grey)
    XCTAssertEqual(e[0].key, .train)
    XCTAssertEqual(e[0].sleep, 88)
    XCTAssertEqual(e[0].strain, 2.8)
    // six hours after the newest data: the same numbers, grey (S3: never passed off as current)
    XCTAssertEqual(e[1].recovery, 82)
    XCTAssertTrue(e[1].grey)
    // a new day: yesterday's numbers are not today's, and yesterday's plan is not today's
    XCTAssertNil(e[2].recovery)
    XCTAssertNil(e[2].tone)
    XCTAssertNil(e[2].sleep)
    XCTAssertEqual(e[2].key, .unavailable)
  }

  func testOldDataIsGreyFromTheStart() {
    let e = FaceTimeline.entries(inputs(context(dataAgo: 7 * 3600)), now: now, calendar: utc)
    XCTAssertEqual(e.first?.grey, true)
    XCTAssertEqual(e.first?.recovery, 82)
    // the phone's own 「旧了」 greys as well
    XCTAssertEqual(FaceTimeline.entry(inputs(context(phone: WatchPhoneSync(.stale, "6h"))), at: now, calendar: utc).grey, true)
  }

  func testMissingRecoveryIsMissingNotZero() {
    let e = FaceTimeline.entry(inputs(context(recovery: nil)), at: now, calendar: utc)
    XCTAssertNil(e.recovery)
    XCTAssertNil(e.tone)
    XCTAssertFalse(e.grey)
    XCTAssertEqual(e.sleep, 88)   // the other numbers stay
    // yesterday's recovery is missing today
    XCTAssertNil(FaceTimeline.entry(inputs(context(day: "2026-03-09")), at: now, calendar: utc).recovery)
  }

  func testSignedOutAndNoPhoneShowNothing() {
    let out = FaceTimeline.entry(inputs(WatchContext(signedIn: false, plan: nil, lang: "en", sentAt: now)), at: now, calendar: utc)
    XCTAssertFalse(out.signedIn)
    XCTAssertNil(out.recovery)
    XCTAssertEqual(out.key, .unavailable)
    let none = FaceTimeline.entries(inputs(nil, queue: WatchEventQueue()), now: now, calendar: utc)
    XCTAssertEqual(none.count, 1)   // nothing changes by itself: one entry until the phone speaks
    XCTAssertNil(none[0].recovery)
    XCTAssertEqual(none[0].key, .unavailable)
  }

  func testRestDayKeyIsQuickLogAndPlanMissingIsUnavailable() {
    XCTAssertEqual(FaceTimeline.entry(inputs(context(rest: true)), at: now, calendar: utc).key, .meal)
    XCTAssertEqual(FaceTimeline.entry(inputs(context(plan: false)), at: now, calendar: utc).key, .unavailable)
  }

  func testLanguageIsThePhones() {
    XCTAssertEqual(FaceTimeline.entry(inputs(context(lang: "en")), at: now, calendar: utc).lang, "en")
    XCTAssertEqual(FaceTimeline.entry(inputs(context(lang: "zh")), at: now, calendar: utc).lang, "zh")
  }

  func testHorizonIsJustAfterMidnight() {
    XCTAssertEqual(FaceTimeline.horizon(now: now, calendar: utc), midnight.addingTimeInterval(1))
    // nothing after the horizon is in the timeline
    XCTAssertTrue(FaceTimeline.entries(inputs(context()), now: now, calendar: utc).allSatisfy { $0.date <= midnight.addingTimeInterval(1) })
  }
}

final class WatchFaceSessionTests: XCTestCase {
  func testRestCountsDownThenTheSetComesBack() {
    // the second set of bench logged 18 s ago: resting 90 s, 72 s left
    let saved = session(logged: 2, ago: 18)
    let e = FaceTimeline.entries(inputs(context(), session: saved), now: now, calendar: utc)
    let restStart = now.addingTimeInterval(-18)
    XCTAssertEqual(e[0].key, .session)
    XCTAssertEqual(e[0].live?.exercise, "bench")
    XCTAssertEqual(e[0].live?.done, 2)
    XCTAssertEqual(e[0].live?.sets, 4)
    XCTAssertEqual(e[0].live?.weightKg, 60)
    XCTAssertEqual(e[0].live?.reps, 8)
    XCTAssertEqual(e[0].live?.rest, restStart...restStart.addingTimeInterval(90))
    // the countdown runs on the face by itself; the next entry is the rest's end: the set, no countdown
    XCTAssertEqual(e[1].date, restStart.addingTimeInterval(90))
    XCTAssertNotNil(e[1].live)
    XCTAssertNil(e[1].live?.rest)
    // the numbers stay underneath (circular recovery is unchanged during a session)
    XCTAssertEqual(e[0].recovery, 82)
  }

  func testReceiptIsProjectedIntoTheRest() {
    // stored while the 1.2 s receipt was up (the app saves at every step; the face must not wait for the next save)
    let saved = session(logged: 1, ago: 0.5)
    guard case .logged = saved.session.phase else { return XCTFail("expected the receipt") }
    let e = FaceTimeline.entry(inputs(context(), session: saved), at: now, calendar: utc)
    XCTAssertNil(e.live)   // still the receipt: no countdown yet
    let later = FaceTimeline.entry(inputs(context(), session: saved), at: now.addingTimeInterval(1), calendar: utc)
    let start = now.addingTimeInterval(-0.5)
    XCTAssertEqual(later.live?.rest, start...start.addingTimeInterval(90))
    // and the timeline has an entry at the receipt's end
    let dates = FaceTimeline.entries(inputs(context(), session: saved), now: now, calendar: utc).map(\.date)
    XCTAssertTrue(dates.contains(start.addingTimeInterval(WatchParams.receiptSec)))
    XCTAssertTrue(dates.contains(start.addingTimeInterval(90)))
  }

  func testInASetThereIsNoCountdown() {
    var s = SetSession(plan: context().plan!, newId: ids())
    _ = s.primary(at: now.addingTimeInterval(-30))
    let e = FaceTimeline.entry(inputs(context(), session: SavedSession(session: s, savedAt: now.addingTimeInterval(-30))), at: now,
                               calendar: utc)
    XCTAssertEqual(e.live?.done, 0)
    XCTAssertNil(e.live?.rest)
    XCTAssertEqual(e.key, .session)
  }

  func testLastSetEndsTheLiveFaceAndAForgottenSessionExpires() {
    // all sets of a one-exercise plan: the receipt gives way to the summary, not to a rest
    var one = SetSession(plan: WatchPlan(day: todayKey, title: "A", exercises: [exercise("bench", sets: 1)]), newId: ids())
    _ = one.primary(at: now.addingTimeInterval(-60))
    _ = one.primary(at: now.addingTimeInterval(-0.5))
    let done = SavedSession(session: one, savedAt: now)
    XCTAssertNil(FaceTimeline.entry(inputs(context(), session: done), at: now.addingTimeInterval(2), calendar: utc).live)
    // a session not touched for `resumeWithinHours` is not the app's any more (it ends it on launch)
    var s = SetSession(plan: context().plan!, newId: ids())
    _ = s.primary(at: now.addingTimeInterval(-30))
    let saved = SavedSession(session: s, savedAt: now.addingTimeInterval(-30))
    let end = now.addingTimeInterval(-30 + WatchParams.resumeWithinHours * 3600 + 1)
    let e = FaceTimeline.entries(inputs(context(), session: saved), now: now, calendar: utc)
    XCTAssertNotNil(e.first?.live)
    XCTAssertEqual(e.first { $0.date >= end }?.live, nil)
    XCTAssertTrue(e.contains { $0.date == end })
  }
}

final class WatchFaceReloadTests: XCTestCase {
  func testOnlyAChangeOnTheFaceSpendsAReload() {
    let a = inputs(context())
    // a new push with the same numbers (only its time differs): no reload
    var same = context()
    same.sentAt = now
    XCTAssertFalse(FaceTimeline.needsReload(before: a, after: inputs(same), now: now, calendar: utc))
    XCTAssertTrue(FaceTimeline.needsReload(before: a, after: inputs(context(recovery: 54, tone: .mid)), now: now, calendar: utc))
    XCTAssertTrue(FaceTimeline.needsReload(before: a, after: inputs(context(lang: "en")), now: now, calendar: utc))
    // newer data moves the moment it turns grey
    XCTAssertTrue(FaceTimeline.needsReload(before: a, after: inputs(context(dataAgo: 10)), now: now, calendar: utc))
    // a session step
    XCTAssertTrue(FaceTimeline.needsReload(before: a, after: inputs(context(), session: session(logged: 2, ago: 18)), now: now,
                                           calendar: utc))
    // the receipt becoming the rest (the app's tick) is what the face already drew
    let receipt = session(logged: 1, ago: 0.5)
    var rest = receipt
    _ = rest.session.tick(at: now.addingTimeInterval(1))
    XCTAssertFalse(FaceTimeline.needsReload(before: inputs(context(), session: receipt), after: inputs(context(), session: rest),
                                            now: now.addingTimeInterval(1), calendar: utc))
  }
}

final class WatchDeepLinkTests: XCTestCase {
  private func home(_ c: WatchContext) -> WatchHome {
    WatchHome.derive(context: c, today: todayKey, sync: .linked(minutesAgo: 1))
  }

  func testUrlsRoundTrip() {
    for l in [WatchDeepLink.home, .start] { XCTAssertEqual(WatchDeepLink(url: l.url), l) }
    XCTAssertEqual(WatchDeepLink.start.url.absoluteString, "watch2whoop://face/start")
    XCTAssertNil(WatchDeepLink(url: URL(string: "watch2whoop://face/nope")!))
    XCTAssertNil(WatchDeepLink(url: URL(string: "watch2whoop://sync")!))   // the phone's own link is not the face's
    XCTAssertNil(WatchDeepLink(url: URL(string: "https://face/start")!))
  }

  func testStartKeyFollowsHomesLeftCapsule() {
    XCTAssertEqual(WatchDeepLink.start.route(home: home(context()), running: false, signedIn: true), [.start])
    XCTAssertEqual(WatchDeepLink.start.route(home: home(context(rest: true)), running: false, signedIn: true), [.quick])
    XCTAssertEqual(WatchDeepLink.start.route(home: home(context(plan: false)), running: false, signedIn: true), [])
  }

  func testRecoveryOpensHomeAndASessionAlwaysWins() {
    XCTAssertEqual(WatchDeepLink.home.route(home: home(context()), running: false, signedIn: true), [])
    XCTAssertEqual(WatchDeepLink.start.route(home: home(context()), running: true, signedIn: true), [])
    XCTAssertEqual(WatchDeepLink.start.route(home: home(context()), running: false, signedIn: false), [])
  }
}

final class WatchStoreGroupTests: XCTestCase {
  private func suite() -> UserDefaults {
    let name = "w2w.test.\(UUID().uuidString)"
    let d = UserDefaults(suiteName: name)!
    d.removePersistentDomain(forName: name)
    return d
  }

  func testOldKeysMoveOnceAndTheSuiteWins() {
    let old = suite(), group = suite()
    let oldStore = WatchStore(defaults: old)
    oldStore.save(context())
    var q = WatchEventQueue()
    q.enqueue(.quick(.water, id: "w1", day: todayKey, at: now, ml: 250))
    oldStore.save(q)
    WatchStore.moveKeys(from: old, to: group)
    let moved = WatchStore(defaults: group)
    XCTAssertEqual(moved.context(), context())
    XCTAssertEqual(moved.queue().waiting, 1)   // an undelivered event is not lost
    XCTAssertNil(old.data(forKey: WatchStore.Key.context.rawValue))   // one source afterwards
    // only once: a later value in the old place is not copied over the suite's
    WatchStore(defaults: old).save(context(recovery: 10))
    WatchStore.moveKeys(from: old, to: group)
    XCTAssertEqual(moved.context()?.today?.recovery, 82)
  }

  func testSuiteValueIsNotOverwritten() {
    let old = suite(), group = suite()
    WatchStore(defaults: old).save(context(recovery: 10))
    WatchStore(defaults: group).save(context(recovery: 82))
    WatchStore.moveKeys(from: old, to: group)
    XCTAssertEqual(WatchStore(defaults: group).context()?.today?.recovery, 82)
  }
}
