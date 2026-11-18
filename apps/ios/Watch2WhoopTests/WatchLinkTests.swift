import XCTest
@testable import Watch2Whoop

// The Apple Watch app's rules (docs/watch-app-plan.md phase 1), tested here because they are plain Swift shared by the
// iPhone and the watch (apps/ios/WatchShared). All data is synthetic.

private func json(_ s: String) -> [String: Any] { (try? JSONSerialization.jsonObject(with: Data(s.utf8))) as? [String: Any] ?? [:] }

private let t0 = Date(timeIntervalSince1970: 1_800_000_000)

private func ex(_ slug: String, sets: Int = 2, done: Int = 0, reps: Int? = 8, kg: Double? = 60, rest: Int = 90,
                last: [String] = [], weighted: Bool = true, secs: Double? = nil) -> WatchExercise {
  WatchExercise(slug: slug, name: slug, sets: sets, done: done, reps: reps, weightKg: kg, durationS: secs, weighted: weighted,
                restSec: rest, weightStepKg: 2.5, last: last)
}

private func plan(_ exercises: [WatchExercise]) -> WatchPlan { WatchPlan(day: "2026-01-02", title: "Upper A", exercises: exercises) }

/// Ids e1, e2, … so events can be compared.
private func counter() -> () -> String {
  var n = 0
  return { n += 1; return "e\(n)" }
}

private func emitted(_ fx: [SetSession.Effect]) -> [WatchSetEvent] {
  fx.compactMap { if case .emit(let e) = $0 { return e } else { return nil } }
}

final class WatchSetSessionTests: XCTestCase {
  func testStartLogRestAndBack() {
    var s = SetSession(plan: plan([ex("bench", last: ["57.5×8", "57.5×8"]), ex("row")]), newId: counter())
    XCTAssertEqual(s.phase, .ready)
    XCTAssertEqual(s.primary(at: t0), [.startWorkout, .haptic(.start)])
    XCTAssertEqual(s.phase, .set)
    s.recordHeartRate(132)
    s.crown(1)   // reps by default (plan 2.1)
    let fx = s.primary(at: t0.addingTimeInterval(40))
    XCTAssertEqual(fx.first, .haptic(.success))
    let e = emitted(fx)
    XCTAssertEqual(e.count, 1)
    XCTAssertEqual(e[0].id, "e1")
    XCTAssertEqual(e[0].kind, .set)
    XCTAssertEqual(e[0].day, "2026-01-02")
    XCTAssertEqual(e[0].slug, "bench")
    XCTAssertEqual(e[0].setIndex, 0)
    XCTAssertEqual(e[0].reps, 9)
    XCTAssertEqual(e[0].weightKg, 60)
    XCTAssertEqual(e[0].hrEnd, 132)
    XCTAssertNil(e[0].restBeforeS)
    XCTAssertEqual(s.delta(of: s.lastLogged!), .weight(2.5))
    // the receipt stays 1.2 s, a tap meanwhile does nothing
    XCTAssertEqual(s.primary(at: t0.addingTimeInterval(40.5)), [])
    XCTAssertEqual(s.tick(at: t0.addingTimeInterval(41)), [])
    XCTAssertEqual(s.tick(at: t0.addingTimeInterval(41.3)), [])
    guard case .rest(let timer) = s.phase else { return XCTFail("rest expected") }
    XCTAssertEqual(timer.total, 90)
    XCTAssertEqual(timer.start, t0.addingTimeInterval(40))   // the rest counts from the set, not from the receipt
    s.recordHeartRate(118)
    XCTAssertEqual(s.restDrop, 14)
    // zero: one haptic, back to the set screen, next set of the same exercise with the numbers just done
    XCTAssertEqual(s.tick(at: t0.addingTimeInterval(130)), [.haptic(.restOver)])
    XCTAssertEqual(s.phase, .set)
    XCTAssertEqual(s.current?.slug, "bench")
    XCTAssertEqual(s.setIndex, 1)
    XCTAssertEqual(s.reps, 9)
    let second = emitted(s.primary(at: t0.addingTimeInterval(170)))
    XCTAssertEqual(second.first?.restBeforeS, 90)
    XCTAssertEqual(second.first?.setIndex, 1)
    // the exercise is done: the set screen moves on to the next one
    XCTAssertEqual(s.current?.slug, "row")
    XCTAssertEqual(s.setIndex, 0)
  }

  func testSkipRestCrownOnRestAndWeightFocus() {
    var s = SetSession(plan: plan([ex("bench", sets: 3)]), newId: counter())
    _ = s.primary(at: t0)
    s.select(.weight)
    s.crown(-2)
    XCTAssertEqual(s.weightKg, 55)
    s.crown(-100)
    XCTAssertEqual(s.weightKg, 0)   // never below zero
    s.select(.reps)
    s.crown(-50)
    XCTAssertEqual(s.reps, 0)
    s.crown(3)
    _ = s.primary(at: t0)
    _ = s.tick(at: t0.addingTimeInterval(2))
    s.crown(2)   // rest +30 s
    guard case .rest(let t) = s.phase else { return XCTFail("rest expected") }
    XCTAssertEqual(t.total, 120)
    XCTAssertEqual(s.primary(at: t0.addingTimeInterval(20)), [])   // 跳过
    XCTAssertEqual(s.phase, .set)
    XCTAssertEqual(emitted(s.primary(at: t0.addingTimeInterval(60))).first?.restBeforeS, 20)
  }

  func testBodyweightAndTimedExercises() {
    var s = SetSession(plan: plan([ex("pullup", sets: 1, kg: nil, weighted: false), ex("plank", sets: 1, reps: nil, kg: nil, weighted: false, secs: 45)]),
                       newId: counter())
    _ = s.primary(at: t0)
    s.select(.weight)   // no weight to select
    XCTAssertEqual(s.focus, .reps)
    let a = emitted(s.primary(at: t0))
    XCTAssertNil(a.first?.weightKg)
    XCTAssertEqual(a.first?.reps, 8)
    _ = s.tick(at: t0.addingTimeInterval(2))
    _ = s.primary(at: t0.addingTimeInterval(3))
    XCTAssertTrue(s.isTimed)
    s.crown(1)
    let b = emitted(s.primary(at: t0.addingTimeInterval(10)))
    XCTAssertEqual(b.first?.durationS, 50)
    XCTAssertNil(b.first?.reps)
  }

  func testLastSetEndsInTheSummaryWithTheEndEvent() {
    var s = SetSession(plan: plan([ex("bench", sets: 1, last: ["60×8"]), ex("row", sets: 1, kg: 50, last: ["52.5×8"])]), newId: counter())
    _ = s.primary(at: t0)
    s.recordHeartRate(120)
    _ = s.primary(at: t0.addingTimeInterval(60))
    _ = s.tick(at: t0.addingTimeInterval(62))
    s.recordHeartRate(100)
    _ = s.primary(at: t0.addingTimeInterval(120))   // skip the rest
    _ = s.primary(at: t0.addingTimeInterval(180))
    XCTAssertTrue(s.allDone)
    let fx = s.tick(at: t0.addingTimeInterval(182))
    XCTAssertEqual(s.phase, .summary)
    XCTAssertEqual(emitted(fx).map(\.kind), [.end])
    XCTAssertTrue(fx.contains(.endWorkout))
    XCTAssertTrue(fx.contains(.haptic(.end)))
    let sum = s.summary(at: t0.addingTimeInterval(600))
    XCTAssertEqual(sum.doneSets, 2)
    XCTAssertEqual(sum.plannedSets, 2)
    XCTAssertEqual(sum.minutes, 3)   // ended at the summary, not when it was read
    XCTAssertEqual(sum.volumeKg, 60 * 8 + 50 * 8)
    XCTAssertEqual(sum.avgHr, 110)
    XCTAssertEqual(sum.same, 1)      // bench 60×8 = 60×8
    XCTAssertEqual(sum.worse, 1)     // row 50×8 < 52.5×8
    XCTAssertEqual(sum.avgDropBpm, 20)
    XCTAssertEqual(sum.avgRestS, 60)
    XCTAssertEqual(sum.plannedRestS, 90)
    XCTAssertEqual(s.primary(at: t0.addingTimeInterval(700)), [])
    XCTAssertEqual(s.phase, .finished)
  }

  func testEndingEarlyAndEndingWithNothingLogged() {
    var empty = SetSession(plan: plan([ex("bench")]), newId: counter())
    _ = empty.primary(at: t0)
    XCTAssertEqual(empty.end(at: t0), [.endWorkout])   // nothing for the phone to submit
    XCTAssertEqual(empty.phase, .finished)

    var s = SetSession(plan: plan([ex("bench", sets: 4)]), newId: counter())
    _ = s.primary(at: t0)
    _ = s.primary(at: t0)
    XCTAssertEqual(emitted(s.end(at: t0.addingTimeInterval(5))).map(\.kind), [.end])
    XCTAssertEqual(s.summary().doneSets, 1)
    XCTAssertEqual(s.summary().plannedSets, 4)
  }

  func testSetsAlreadyDoneOnThePhoneAreSkipped() {
    let s = SetSession(plan: plan([ex("bench", sets: 2, done: 2), ex("row", sets: 3, done: 1)]), newId: counter())
    XCTAssertEqual(s.current?.slug, "row")
    XCTAssertEqual(s.setIndex, 1)
    XCTAssertEqual(s.doneSets, 3)
  }

  func testCorrectingTheLastSetReplacesIt() {
    var s = SetSession(plan: plan([ex("bench", sets: 2)]), newId: counter())
    _ = s.primary(at: t0)
    _ = s.primary(at: t0)
    XCTAssertEqual(s.amendLast(weightKg: 62.5, reps: 6, at: t0), [])   // only during the rest
    _ = s.tick(at: t0.addingTimeInterval(2))
    let e = emitted(s.amendLast(weightKg: 62.5, reps: 6, at: t0.addingTimeInterval(3)))
    XCTAssertEqual(e.map(\.id), ["e2"])
    XCTAssertEqual(e.first?.setIndex, 0)
    XCTAssertEqual(e.first?.weightKg, 62.5)
    XCTAssertEqual(s.summary().volumeKg, 62.5 * 6)
    XCTAssertEqual(s.summary().doneSets, 1)
  }

  func testSwipeChangesExerciseOnlyOnTheSetScreen() {
    var s = SetSession(plan: plan([ex("bench"), ex("press", kg: 25, last: ["22.5×10"])]), newId: counter())
    s.swipe(1)
    XCTAssertEqual(s.exercise, 0)   // not before the start
    _ = s.primary(at: t0)
    s.swipe(1)
    XCTAssertEqual(s.current?.slug, "press")
    XCTAssertEqual(s.weightKg, 25)
    XCTAssertEqual(s.lastSet(), LastSet(weightKg: 22.5, reps: 10))
    s.swipe(1)
    XCTAssertEqual(s.exercise, 1)   // no wrap
    s.swipe(-1)
    XCTAssertEqual(s.current?.slug, "bench")
  }

  func testDeltaAgainstTheLastSession() {
    var s = SetSession(plan: plan([ex("bench", sets: 3, last: ["60×8", "60×7"])]), newId: counter())
    _ = s.primary(at: t0)
    _ = s.primary(at: t0)
    XCTAssertEqual(s.delta(of: s.lastLogged!), .same)
    _ = s.tick(at: t0.addingTimeInterval(2))
    _ = s.primary(at: t0.addingTimeInterval(3))
    _ = s.primary(at: t0.addingTimeInterval(4))
    XCTAssertEqual(s.delta(of: s.lastLogged!), .reps(1))   // 8 against 7
    _ = s.tick(at: t0.addingTimeInterval(6))
    _ = s.primary(at: t0.addingTimeInterval(7))
    s.crown(-2)
    _ = s.primary(at: t0.addingTimeInterval(8))
    XCTAssertEqual(s.delta(of: s.lastLogged!), .reps(-1))  // set 3 compares with the last session's last set
    let fresh = SetSession(plan: plan([ex("new")]))
    XCTAssertNil(fresh.lastSet())
  }

  func testLastSetParsing() {
    XCTAssertEqual(LastSet("57.5×8"), LastSet(weightKg: 57.5, reps: 8))
    XCTAssertEqual(LastSet("12"), LastSet(weightKg: nil, reps: 12))
    XCTAssertEqual(LastSet("40x10"), LastSet(weightKg: 40, reps: 10))
    XCTAssertNil(LastSet("abc"))
    XCTAssertEqual(WatchNum.kg(60), "60")
    XCTAssertEqual(WatchNum.kg(62.5), "62.5")
    XCTAssertEqual(WatchNum.kg(nil), "—")
  }
}

final class WatchRestTimerTests: XCTestCase {
  func testCountdownLimitsAndClock() {
    var t = RestTimer(start: t0, total: 90)
    XCTAssertEqual(t.remaining(at: t0.addingTimeInterval(18)), 72)
    XCTAssertEqual(RestTimer.clock(t.remaining(at: t0.addingTimeInterval(18))), "1:12")
    XCTAssertEqual(RestTimer.clock(t.remaining(at: t0.addingTimeInterval(89.5))), "0:01")
    XCTAssertEqual(t.fraction(at: t0.addingTimeInterval(45)), 0.5, accuracy: 0.001)
    XCTAssertFalse(t.isOver(at: t0.addingTimeInterval(89)))
    XCTAssertTrue(t.isOver(at: t0.addingTimeInterval(91)))
    XCTAssertEqual(t.remaining(at: t0.addingTimeInterval(200)), 0)
    t.adjust(steps: -10)
    XCTAssertEqual(t.total, WatchParams.minRestSec)
    t.adjust(steps: 100)
    XCTAssertEqual(t.total, WatchParams.maxRestSec)
    XCTAssertEqual(RestTimer(start: t0, total: 0).total, WatchParams.minRestSec)
    XCTAssertEqual(t.elapsed(at: t0.addingTimeInterval(35.4)), 35)
  }
}

final class WatchEventQueueTests: XCTestCase {
  private func ev(_ id: String, _ kind: WatchSetEvent.Kind = .set, at: TimeInterval = 0) -> WatchSetEvent {
    WatchSetEvent(id: id, kind: kind, day: "2026-01-02", slug: kind == .set ? "bench" : nil, setIndex: kind == .set ? 0 : nil,
                  reps: 8, weightKg: 60, durationS: nil, at: t0.addingTimeInterval(at), hrEnd: nil, restBeforeS: nil)
  }

  func testQueueLifecycleAndStorage() {
    var q = WatchEventQueue()
    q.enqueue(ev("a"))
    q.enqueue(ev("a"))   // same id once
    q.enqueue(ev("b"))
    XCTAssertEqual(q.waiting, 2)
    XCTAssertEqual(q.takePending().map(\.id), ["a", "b"])
    XCTAssertEqual(q.takePending(), [])   // already handed over
    XCTAssertEqual(q.sending, 2)
    q.markDelivered("a", at: t0)
    q.markFailed("b")
    XCTAssertEqual(q.deliveredInBatch, 1)
    XCTAssertEqual(q.failed, 1)
    XCTAssertEqual(q.lastContact, t0)
    q.retryFailed()
    XCTAssertEqual(q.takePending().map(\.id), ["b"])
    XCTAssertEqual(q.items.first?.attempts, 2)
    // after a relaunch: the transfer the system still holds is not sent again, the lost one is
    q.enqueue(ev("c"))
    _ = q.takePending()
    q.restoreAfterLaunch(inFlight: ["b"])
    XCTAssertEqual(q.pending, 1)
    XCTAssertEqual(q.items.first { $0.state == .pending }?.event.id, "c")
    let back = WatchEventQueue.load(q.data())
    XCTAssertEqual(back, q)
    XCTAssertEqual(WatchEventQueue.load(nil), WatchEventQueue())
    // emptied: a new batch counts from zero
    q.markDelivered("b", at: t0)
    q.markDelivered("c", at: t0)
    XCTAssertTrue(q.isEmpty)
    q.enqueue(ev("d"))
    XCTAssertEqual(q.deliveredInBatch, 0)
  }

  func testSyncStates() {
    let now = t0.addingTimeInterval(150)
    let ctx = WatchContext(signedIn: true, plan: plan([ex("bench")]), lang: "zh", sentAt: t0)
    func state(_ c: WatchContext?, _ q: WatchEventQueue = WatchEventQueue(), reachable: Bool = true) -> WatchSyncState {
      WatchSyncState.derive(context: c, queue: q, reachable: reachable, now: now)
    }
    XCTAssertEqual(state(nil), .signedOut)                                                                  // S8
    XCTAssertEqual(state(WatchContext(signedIn: false, plan: nil, lang: "zh", sentAt: t0)), .signedOut)
    // phase 1's S9 wall is gone: no plan is a state of home, not of the sync mark
    XCTAssertEqual(state(WatchContext(signedIn: true, plan: nil, lang: "zh", sentAt: t0)), .linked(minutesAgo: nil))
    var q = WatchEventQueue()
    q.heard(at: t0)
    XCTAssertEqual(state(ctx, q), .linked(minutesAgo: 2))                                                  // S1
    q.enqueue(ev("a"))
    q.enqueue(ev("b"))
    XCTAssertEqual(state(ctx, q, reachable: false), .away(2))                                              // S4
    XCTAssertEqual(state(ctx, q), .catchingUp(done: 0, total: 2))                                          // S2 / S5
    _ = q.takePending()
    q.markDelivered("a", at: t0)
    XCTAssertEqual(state(ctx, q), .catchingUp(done: 1, total: 2))
    q.markFailed("b")
    XCTAssertEqual(state(ctx, q, reachable: false), .failed(1))                                            // S6
  }

  func testCodecRoundTrips() throws {
    let e = ev("x", at: 12.5)
    XCTAssertEqual(WatchCodec.event(WatchCodec.message(e)), e)
    let c = WatchContext(signedIn: true, plan: plan([ex("bench", last: ["60×8"])]), lang: "en", sentAt: t0)
    let msg = WatchCodec.message(c)
    XCTAssertTrue(msg[WatchCodec.contextKey] is Data)   // a property-list value WatchConnectivity accepts
    XCTAssertEqual(WatchCodec.context(msg), c)
    XCTAssertNil(WatchCodec.event([:]))
    XCTAssertNil(WatchCodec.context(["other": 1]))
  }
}

final class PhoneWatchLinkTests: XCTestCase {
  private let day = json("""
  {"date": "2026-01-02", "is_today": true, "rest_day": false, "can_log": true,
   "sessions": [{"name": "Upper A", "exercises": [
     {"slug": "bench", "name_zh": "卧推", "name_en": "Bench press", "status": "active", "modality": "weight_reps",
      "target": {"sets": 3, "reps": 8, "weight_kg": 60, "rest_sec": 120},
      "last": [{"date": "2025-12-30", "sets": ["57.5×8", "57.5×8", "57.5×7"]}]},
     {"slug": "upright_row", "name_zh": "直立划船", "name_en": "Upright row", "status": "banned", "target": {"sets": 3, "reps": 10}},
     {"slug": "pullup", "name_zh": "引体", "name_en": "Pull-up", "status": "active", "modality": "reps", "target": {"sets": 2, "reps": 6}},
     {"slug": "plank", "name_zh": "平板", "name_en": "Plank", "status": "active", "modality": "duration", "target": {"sets": 2, "duration_s": 45}}
   ]}],
   "log": {"status": "draft", "exercises": [{"slug": "bench", "sets": [{"reps": 8, "weight_kg": 60, "done": true},
     {"reps": 8, "weight_kg": 60, "done": false}, {"reps": 8, "weight_kg": 60, "done": false}]}]}}
  """)

  func testPlanForTheWatch() throws {
    let s = try XCTUnwrap(TrainingSession.build(day, day: "2026-01-02", pending: []))
    let p = try XCTUnwrap(WatchPlanBuilder.plan(from: s))
    XCTAssertEqual(p.day, "2026-01-02")
    XCTAssertEqual(p.title, "Upper A")
    XCTAssertEqual(p.exercises.map(\.slug), ["bench", "pullup", "plank"])   // the banned one is not offered
    let bench = p.exercises[0]
    XCTAssertEqual(bench.sets, 3)
    XCTAssertEqual(bench.done, 1)
    XCTAssertEqual(bench.restSec, 120)
    XCTAssertEqual(bench.weightKg, 60)
    XCTAssertEqual(bench.last, ["57.5×8", "57.5×8", "57.5×7"])
    XCTAssertTrue(bench.weighted)
    XCTAssertEqual(p.exercises[1].restSec, WatchParams.defaultRestSec)
    XCTAssertFalse(p.exercises[1].weighted)
    XCTAssertNil(p.exercises[1].weightKg)
    XCTAssertEqual(p.exercises[2].durationS, 45)
    XCTAssertNil(p.exercises[2].reps)
    XCTAssertEqual(p.totalSets, 7)
    XCTAssertNil(WatchPlanBuilder.plan(from: nil))
    var rest = s
    rest.restDay = true
    XCTAssertNil(WatchPlanBuilder.plan(from: rest))
  }

  private func set(_ id: String, _ slug: String, _ i: Int, reps: Int, kg: Double?, at: TimeInterval, day: String = "2026-01-02") -> WatchSetEvent {
    WatchSetEvent(id: id, kind: .set, day: day, slug: slug, setIndex: i, reps: reps, weightKg: kg, durationS: nil,
                  at: t0.addingTimeInterval(at), hrEnd: 130, restBeforeS: nil)
  }

  func testMergingTheWatchsSetsIntoTheDraft() throws {
    let s = try XCTUnwrap(TrainingSession.build(day, day: "2026-01-02", pending: []))
    let end = WatchSetEvent(id: "z", kind: .end, day: "2026-01-02", slug: nil, setIndex: nil, reps: nil, weightKg: nil,
                            durationS: nil, at: t0.addingTimeInterval(900), hrEnd: nil, restBeforeS: nil)
    let events = [
      end,
      set("b", "bench", 1, reps: 7, kg: 62.5, at: 100),
      set("c", "bench", 1, reps: 8, kg: 62.5, at: 200),   // a correction of the same set: the later one wins
      set("d", "bench", 3, reps: 6, kg: 62.5, at: 300),   // an extra set beyond the plan: rows are added
      set("e", "pullup", 0, reps: 6, kg: nil, at: 400),
      set("f", "upright_row", 0, reps: 10, kg: 20, at: 500),   // banned: not written, kept
      set("g", "bench", 0, reps: 8, kg: 60, at: 600, day: "2026-01-03"),   // another day: kept for that day
      set("a", "bench", 0, reps: 1, kg: 1, at: 50),       // already merged before: dropped
    ]
    let r = WatchEventMerge.apply(events, to: s.rows, day: s.day, exercises: s.exercises, seen: ["a"])
    XCTAssertEqual(r.applied, ["b", "c", "d", "e", "z"])
    XCTAssertTrue(r.ended)
    XCTAssertEqual(r.held.map(\.id), ["f", "g"])
    let bench = try XCTUnwrap(r.rows["bench"])
    XCTAssertEqual(bench.count, 4)
    XCTAssertEqual(bench[0], SetRow(reps: 8, weightKg: 60, durationS: nil, done: true))   // the phone's own tick stays
    XCTAssertEqual(bench[1], SetRow(reps: 8, weightKg: 62.5, durationS: nil, done: true))
    XCTAssertEqual(bench[2], SetRow(reps: 8, weightKg: 60, durationS: nil, done: false))
    XCTAssertEqual(bench[3], SetRow(reps: 6, weightKg: 62.5, durationS: nil, done: true))
    XCTAssertEqual(r.rows["pullup"]?[0], SetRow(reps: 6, weightKg: nil, durationS: nil, done: true))
    XCTAssertNil(r.rows["upright_row"])
    // the same transfer arriving again changes nothing
    let again = WatchEventMerge.apply(events, to: r.rows, day: s.day, exercises: s.exercises, seen: Set(["a"] + r.applied))
    XCTAssertEqual(again.applied, [])
    XCTAssertEqual(again.rows, r.rows)
    XCTAssertFalse(again.ended)
  }

  /// No paired watch, or the app not on it: the phone does nothing for the watch (no store, no pull, no push).
  func testThePhoneStaysSilentWithoutAWatch() {
    XCTAssertTrue(WatchLinkGate.linked(supported: true, activated: true, paired: true, appInstalled: true))
    XCTAssertFalse(WatchLinkGate.linked(supported: false, activated: true, paired: true, appInstalled: true))   // iPad
    XCTAssertFalse(WatchLinkGate.linked(supported: true, activated: false, paired: true, appInstalled: true))   // before activation
    XCTAssertFalse(WatchLinkGate.linked(supported: true, activated: true, paired: false, appInstalled: false))  // no watch
    XCTAssertFalse(WatchLinkGate.linked(supported: true, activated: true, paired: true, appInstalled: false))   // app not on the watch
  }

  @MainActor
  func testDetachWithoutAWatchDoesNothing() {
    // in the simulator there is never a paired watch: attach / detach must not start anything or throw
    PhoneWatchBridge.shared.detach()
    PhoneWatchBridge.shared.detach()
  }

  func testInboxKeepsTheNewestIds() {
    var inbox = PhoneWatchInbox()
    inbox.remember((0..<(WatchParams.phoneSeenIdsKept + 5)).map { "id\($0)" })
    XCTAssertEqual(inbox.seen.count, WatchParams.phoneSeenIdsKept)
    XCTAssertEqual(inbox.seen.first, "id5")
    let d = UserDefaults(suiteName: "watch-inbox-test")!
    d.removePersistentDomain(forName: "watch-inbox-test")
    inbox.save(d)
    XCTAssertEqual(PhoneWatchInbox.load(d), inbox)
  }
}

// MARK: - phase 2a: home, quick log, resume

private let day = "2026-01-02"

private func today(recovery: Double? = 82, day d: String = day, dataAgo: TimeInterval = 120,
                   phone: WatchPhoneSync = WatchPhoneSync(.ok)) -> WatchToday {
  WatchToday(day: d, recovery: recovery, tone: recovery == nil ? nil : .good, usualLo: 58, usualHi: 74, verdict: "可上强度",
             reason: recovery == nil ? "昨晚没戴表" : nil, sleep: 88, strain: 2.8, computedAt: t0,
             dataAt: t0.addingTimeInterval(-dataAgo), phone: phone)
}

private func meal(_ id: Int, kcal: Double? = 500) -> WatchQuickMeal {
  WatchQuickMeal(id: id, name: "m\(id)", slot: "Lunch", kcal: kcal, proteinG: 30)
}

final class WatchHomeTests: XCTestCase {
  private func ctx(plan p: WatchPlan? = plan([ex("bench")]), planFor: String? = nil, today t: WatchToday? = today(),
                   quick: WatchQuick? = nil) -> WatchContext {
    WatchContext(signedIn: true, plan: p, lang: "zh", sentAt: t0, planFor: planFor, today: t, quick: quick)
  }

  /// A context saved by the phase-1 watch (or sent by a phase-1 phone) still decodes; the new fields are nil.
  func testOldContextShapeStillDecodes() throws {
    let old = """
    {"signedIn": true, "lang": "zh", "sentAt": 1800000000000,
     "plan": {"day": "2026-01-02", "title": "Upper A", "exercises": [{"slug": "bench", "name": "Bench", "sets": 3, "done": 0,
       "reps": 8, "weightKg": 60, "weighted": true, "restSec": 90, "weightStepKg": 2.5, "last": ["60×8"]}]}}
    """
    let c = try XCTUnwrap(WatchCodec.context([WatchCodec.contextKey: Data(old.utf8)]))
    XCTAssertTrue(c.signedIn)
    XCTAssertEqual(c.plan?.exercises.first?.slug, "bench")
    XCTAssertNil(c.planFor)
    XCTAssertNil(c.today)
    XCTAssertNil(c.quick)
    XCTAssertEqual(WatchHome.derive(context: c, today: day, sync: .linked(minutesAgo: 1)).key, .train("Upper A"))
    // an old event (no quick-log fields) too
    let e = """
    {"id": "x", "kind": "set", "day": "2026-01-02", "slug": "bench", "setIndex": 0, "reps": 8, "weightKg": 60, "at": 1800000000000}
    """
    let ev = try XCTUnwrap(WatchCodec.event([WatchCodec.eventKey: Data(e.utf8)]))
    XCTAssertTrue(ev.isTraining)
    XCTAssertNil(ev.templateId)
  }

  func testNewContextRoundTrips() throws {
    var q = WatchQuick(day: day, meals: [meal(1), meal(2, kcal: nil)], waterMl: 250, kcal: 600, kcalGoal: 2300)
    q.results = ["a": true, "b": false]
    let c = ctx(planFor: day, quick: q)
    XCTAssertEqual(c.planFor, day)
    XCTAssertEqual(WatchCodec.context(WatchCodec.message(c)), c)
    let m = WatchSetEvent.quick(.meal, id: "m", day: day, at: t0, templateId: 7)
    XCTAssertEqual(WatchCodec.event(WatchCodec.message(m)), m)
    XCTAssertFalse(m.isTraining)
    let u = WatchSetEvent.quick(.undo, id: "u", day: day, at: t0, target: "m")
    XCTAssertEqual(WatchCodec.event(WatchCodec.message(u))?.target, "m")
  }

  /// Phase 2a has no voice: the core is dimmed and a hold is refused; 2b changes only `WatchVoice.derive`.
  func testVoiceIsUnavailableUntilItShips() {
    XCTAssertEqual(WatchVoice.derive(context: ctx(), sync: .linked(minutesAgo: 1)), .unavailable(.notYet))
    XCTAssertEqual(WatchVoice.derive(context: nil, sync: .signedOut), .unavailable(.signedOut))
    XCTAssertFalse(WatchHome.derive(context: ctx(), today: day, sync: .linked(minutesAgo: 1)).voice.isAvailable)
    XCTAssertTrue(WatchVoice.available.isAvailable)
  }

  /// A newer phone may send blocks this watch does not know (voice, widget data) or a state it cannot read: the
  /// context still decodes, the unknown is ignored and an unreadable block is nil.
  func testContextToleratesFutureFields() throws {
    let newer = """
    {"signedIn": true, "lang": "zh", "sentAt": 1800000000000, "planFor": "2026-01-02",
     "voice": {"mode": "api", "direct": true}, "complications": {"families": ["circular"]},
     "today": {"day": "2026-01-02", "recovery": 82, "computedAt": 1800000000000, "phone": {"kind": "teleporting"}},
     "quick": {"day": "2026-01-02", "meals": [{"id": 1, "name": "Oats", "slot": "Breakfast", "kcal": 420}], "results": {}}}
    """
    let c = try XCTUnwrap(WatchCodec.context([WatchCodec.contextKey: Data(newer.utf8)]))
    XCTAssertEqual(c.planFor, "2026-01-02")
    XCTAssertNil(c.today)                          // its phone state is unknown here: the block is dropped, not the context
    XCTAssertEqual(c.quick?.meals.map(\.id), [1])
    XCTAssertNil(WatchCodec.context([WatchCodec.contextKey: Data(#"{"lang": "zh"}"#.utf8)]))   // phase-1 fields stay required
  }

  /// Everything the watch keeps lives in one store (the widgets' step moves it to an App Group suite).
  func testStoreKeepsEverythingInOnePlace() throws {
    let suite = "watch-store-test"
    let d = try XCTUnwrap(UserDefaults(suiteName: suite))
    d.removePersistentDomain(forName: suite)
    defer { d.removePersistentDomain(forName: suite) }
    let store = WatchStore(defaults: d)
    XCTAssertNil(store.context())
    XCTAssertTrue(store.queue().isEmpty)
    let c = ctx(planFor: day, quick: WatchQuick(day: day, meals: [meal(1)], waterMl: 250, kcal: 600, kcalGoal: 2300))
    store.save(c)
    var q = WatchEventQueue()
    q.enqueue(.quick(.water, id: "w", day: day, at: t0, ml: 250))
    store.save(q)
    var logs = WatchQuickLogs()
    logs.add(WatchQuickLog.water(250, id: "w", day: day, at: t0))
    store.save(logs)
    var s = SetSession(plan: plan([ex("bench")]), newId: counter())
    _ = s.primary(at: t0)
    store.save(SavedSession(session: s, savedAt: t0))
    let again = WatchStore(defaults: d)
    XCTAssertEqual(again.context(), c)
    XCTAssertEqual(again.queue(), q)
    XCTAssertEqual(again.quickLogs(), logs)
    XCTAssertEqual(again.session()?.session, s)
    again.save(nil as SavedSession?)
    XCTAssertNil(store.session())
    store.clear()
    XCTAssertNil(again.context())
    XCTAssertTrue(again.quickLogs().items.isEmpty)
  }

  func testHomeKeysAndNumbers() {
    let linked = WatchSyncState.linked(minutesAgo: 2)
    // D1: today's plan → its name
    var h = WatchHome.derive(context: ctx(), today: day, sync: linked)
    XCTAssertEqual(h.key, .train("Upper A"))
    XCTAssertEqual(h.recovery, 82)
    XCTAssertEqual(h.tone, .good)
    XCTAssertEqual(h.usual, 58...74)
    XCTAssertEqual(h.verdict, "可上强度")
    XCTAssertEqual(h.sleep, 88)
    XCTAssertEqual(h.strain, 2.8)
    XCTAssertFalse(h.grey)
    // D2: the phone says there is no training today → 「记一餐」
    XCTAssertEqual(WatchHome.derive(context: ctx(plan: nil, planFor: day), today: day, sync: linked).key, .meal)
    // a plan with nothing to log is a day without training too
    XCTAssertEqual(WatchHome.derive(context: ctx(plan: plan([]), planFor: day), today: day, sync: linked).key, .meal)
    // S9 inside home: yesterday's plan, or the phone has not answered about today
    XCTAssertEqual(WatchHome.derive(context: ctx(), today: "2026-01-03", sync: linked).key, .planMissing)
    XCTAssertEqual(WatchHome.derive(context: ctx(plan: nil), today: day, sync: linked).key, .planMissing)
    XCTAssertEqual(WatchHome.derive(context: nil, today: day, sync: .signedOut).key, .planMissing)
    // yesterday's numbers are not today's: missing, never carried over
    h = WatchHome.derive(context: ctx(), today: "2026-01-03", sync: linked)
    XCTAssertNil(h.recovery)
    XCTAssertNil(h.sleep)
    XCTAssertNil(h.strain)
    XCTAssertNil(h.verdict)
    // no recovery today: no tone, no verdict, the phone's reason instead
    h = WatchHome.derive(context: ctx(today: today(recovery: nil)), today: day, sync: linked)
    XCTAssertNil(h.recovery)
    XCTAssertNil(h.tone)
    XCTAssertNil(h.verdict)
    XCTAssertEqual(h.reason, "昨晚没戴表")
    // S3: grey
    XCTAssertTrue(WatchHome.derive(context: ctx(), today: day, sync: .stale(minutesAgo: 400)).grey)
    XCTAssertFalse(WatchHome.derive(context: ctx(), today: day, sync: .offline).grey)
    // quick log offered only with something to log
    XCTAssertFalse(h.canQuickLog)
    let q = WatchQuick(day: day, meals: [], waterMl: 250, kcal: nil, kcalGoal: nil)
    XCTAssertTrue(WatchHome.derive(context: ctx(quick: q), today: day, sync: linked).canQuickLog)
  }

  /// The mark joins the watch's queue with the phone's capsule, aged on the watch (S1, S2, S3, S7 and the phone's own).
  func testSyncMarkFollowsThePhonesCapsule() {
    var q = WatchEventQueue()
    q.heard(at: t0)
    func state(_ t: WatchToday?, at now: Date = t0.addingTimeInterval(60), queue: WatchEventQueue? = nil) -> WatchSyncState {
      WatchSyncState.derive(context: WatchContext(signedIn: true, plan: nil, lang: "zh", sentAt: t0, today: t), queue: queue ?? q,
                            reachable: true, now: now)
    }
    // the age is the data's, not the last message's
    XCTAssertEqual(state(today(dataAgo: 120)), .linked(minutesAgo: 3))
    // S3: older than the limit, by the watch's own clock (a phone that went quiet cannot say so)
    XCTAssertEqual(state(today(dataAgo: 120), at: t0.addingTimeInterval(Double(WatchParams.staleAfterMin) * 60)),
                   .stale(minutesAgo: WatchParams.staleAfterMin + 2))
    XCTAssertEqual(state(today(phone: WatchPhoneSync(.stale, "6时前"))), .stale(minutesAgo: 3))
    // S2: the phone is syncing, only while that message is fresh
    XCTAssertEqual(state(today(phone: WatchPhoneSync(.busy))), .phoneSyncing)
    XCTAssertEqual(state(today(phone: WatchPhoneSync(.busy)), at: t0.addingTimeInterval(WatchParams.phoneBusyFreshSec + 1)),
                   .linked(minutesAgo: 4))
    // S7 and the phone's own problems
    XCTAssertEqual(state(today(phone: WatchPhoneSync(.offline, "离线"))), .offline)
    XCTAssertEqual(state(today(phone: WatchPhoneSync(.behind, "缺2天"))), .behind("缺2天"))
    XCTAssertEqual(state(today(phone: WatchPhoneSync(.failed))), .phoneFailed)
    // the watch's own link comes first: an undelivered quick log says 「待传」 / 「补交」 whatever the phone said
    var waiting = q
    waiting.enqueue(.quick(.meal, id: "m", day: day, at: t0, templateId: 1))
    XCTAssertEqual(state(today(phone: WatchPhoneSync(.offline)), queue: waiting), .catchingUp(done: 0, total: 1))
    XCTAssertTrue(WatchSyncState.stale(minutesAgo: 400).greysNumbers)
    XCTAssertFalse(WatchSyncState.linked(minutesAgo: 1).greysNumbers)
  }

  func testAgeLabelsInBothLanguages() {
    defer { Lang.override = nil }
    Lang.override = "zh"
    XCTAssertEqual(WatchSyncState.age(2), "2分前")
    XCTAssertEqual(WatchSyncState.age(361), "6时前")
    XCTAssertEqual(WatchSyncState.age(3 * 24 * 60), "3天前")
    XCTAssertEqual(WatchSyncState.age(0), "刚刚")
    XCTAssertEqual(WatchSyncState.age(nil), "—")
    Lang.override = "en"
    XCTAssertEqual(WatchSyncState.age(2), "2m ago")
    XCTAssertEqual(WatchSyncState.age(361), "6h ago")
    XCTAssertEqual(WatchSyncState.age(3 * 24 * 60), "3d ago")
  }

  /// Undo right after a tap: a pending event is simply taken back; once handed over, the phone gets an `undo`.
  func testQuickLogEventsAndCancel() {
    let l = WatchQuickLog.meal(meal(3, kcal: 570), id: "m1", day: day, at: t0)
    XCTAssertEqual(l.event.kind, .meal)
    XCTAssertEqual(l.event.templateId, 3)
    XCTAssertEqual(l.event.id, "m1")
    let w = WatchQuickLog.water(250, id: "w1", day: day, at: t0)
    XCTAssertEqual(w.event.kind, .water)
    XCTAssertEqual(w.event.ml, 250)
    var q = WatchEventQueue()
    q.enqueue(l.event)
    q.enqueue(w.event)
    XCTAssertTrue(q.cancel("m1"))          // not handed over yet
    XCTAssertEqual(q.waiting, 1)
    _ = q.takePending()
    XCTAssertFalse(q.cancel("w1"))         // with WatchConnectivity: the phone will get it, so an undo follows
    q.markFailed("w1")
    XCTAssertTrue(q.cancel("w1"))          // a failed transfer never reached the phone
    XCTAssertTrue(q.isEmpty)
    XCTAssertFalse(q.cancel("nope"))
    // the watch remembers its logs for a day and a half, no longer
    var logs = WatchQuickLogs()
    logs.add(l)
    logs.add(l)
    logs.add(WatchQuickLog.water(250, id: "old", day: "2026-01-01", at: t0.addingTimeInterval(-40 * 3600)))
    XCTAssertEqual(logs.items.count, 2)
    logs.markUndone("m1")
    XCTAssertTrue(logs.log("m1")?.undone == true)
    logs.prune(now: t0)
    XCTAssertEqual(logs.items.map(\.id), ["m1"])
    XCTAssertEqual(WatchQuickLogs.load(logs.data()), logs)
  }

  /// M3's bar: the phone's intake, corrected only by the watch's own logs it had not counted (or counted before an undo).
  func testReceiptBar() throws {
    let a = WatchQuickLog.meal(meal(1, kcal: 400), id: "a", day: day, at: t0)
    var b = WatchQuickLog.meal(meal(2, kcal: 300), id: "b", day: day, at: t0.addingTimeInterval(60))
    let c = WatchQuickLog.meal(meal(3, kcal: 570), id: "c", day: day, at: t0.addingTimeInterval(120))
    let water = WatchQuickLog.water(250, id: "w", day: day, at: t0)
    var q = WatchQuick(day: day, meals: [], waterMl: 250, kcal: 1000, kcalGoal: 2300)
    // nothing of the watch's counted yet: a and b are added to the phone's number
    var bar = try XCTUnwrap(c.bar(logs: [a, b, c, water], quick: q))
    XCTAssertEqual(bar.before, 1700)
    XCTAssertEqual(bar.added, 570)
    XCTAssertEqual(bar.goal, 2300)
    // the phone has counted a and c itself: c is taken out of "before", a is not added twice
    q.results = ["a": true, "c": true]
    bar = try XCTUnwrap(c.bar(logs: [a, b, c], quick: q))
    XCTAssertEqual(bar.before, 1000 + 300 - 570)
    // b undone on the watch after the phone counted it: taken out
    b.undone = true
    q.results["b"] = true
    bar = try XCTUnwrap(c.bar(logs: [a, b, c], quick: q))
    XCTAssertEqual(bar.before, 1000 - 300 - 570)
    // unknown → no bar, never a guess
    XCTAssertNil(water.bar(logs: [water], quick: q))                                           // a drink has no kcal bar
    XCTAssertNil(c.bar(logs: [c], quick: nil))
    XCTAssertNil(c.bar(logs: [c], quick: WatchQuick(day: "2026-01-01", meals: [], kcal: 1000, kcalGoal: 2300)))
    XCTAssertNil(c.bar(logs: [c], quick: WatchQuick(day: day, meals: [], kcal: nil, kcalGoal: 2300)))
    let unknown = WatchQuickLog.meal(meal(9, kcal: nil), id: "u", day: day, at: t0)
    XCTAssertNil(c.bar(logs: [unknown, c], quick: WatchQuick(day: day, meals: [], kcal: 1000, kcalGoal: 2300)))
  }

  /// A running session is saved and comes back on launch; one left for hours is not resumed.
  func testSessionResumesAfterRelaunch() throws {
    var s = SetSession(plan: plan([ex("bench", sets: 3), ex("row")]), newId: counter())
    _ = s.primary(at: t0)
    s.recordHeartRate(140)
    s.crown(2)
    _ = s.primary(at: t0.addingTimeInterval(40))
    _ = s.tick(at: t0.addingTimeInterval(41.5))
    guard case .rest = s.phase else { return XCTFail("rest expected") }
    let saved = SavedSession(session: s, savedAt: t0.addingTimeInterval(42))
    let back = try XCTUnwrap(WatchCodec.decode(SavedSession.self, WatchCodec.encode(saved)))
    XCTAssertEqual(back, saved)
    XCTAssertEqual(back.session.logged.count, 1)
    XCTAssertEqual(back.session.setIndex, 1)
    XCTAssertEqual(back.session.reps, 10)
    XCTAssertEqual(back.session.startedAt, t0)
    // the countdown goes on from the same start
    var r = back.session
    XCTAssertEqual(r.tick(at: t0.addingTimeInterval(40 + 90)), [.haptic(.restOver)])
    XCTAssertEqual(r.phase, .set)
    XCTAssertTrue(back.resumable(now: t0.addingTimeInterval(3600)))
    XCTAssertFalse(back.resumable(now: t0.addingTimeInterval(WatchParams.resumeWithinHours * 3600 + 100)))
    XCTAssertFalse(SavedSession(session: SetSession(plan: plan([ex("bench")])), savedAt: t0).resumable(now: t0))   // not started
    // a saved session that does not fit its plan is not loaded
    XCTAssertNil(WatchCodec.decode(SavedSession.self, Data(#"{"savedAt": 0, "session": {"plan": {"day": "d", "title": "t", "exercises": []}, "phase": {"set": {}}, "exercise": 0, "nextSet": [1], "focus": "reps", "logged": [], "hrSum": 0, "hrCount": 0, "restDrops": [], "restsTaken": [], "restsPlanned": []}}"#.utf8)))
  }
}

final class PhoneWatchTodayTests: XCTestCase {
  override func tearDown() {
    Lang.override = nil
    super.tearDown()
  }

  /// The word under the watch's number fits the watch (≤ 4 characters / 2 English words), in both languages.
  func testVerdictsAreShortInBothLanguages() {
    for lang in ["zh", "en"] {
      Lang.override = lang
      for v in [10.0, 50, 90] {
        let w = WatchTodayBuilder.verdict(v)
        if lang == "zh" { XCTAssertLessThanOrEqual(w.count, 4, w) } else { XCTAssertLessThanOrEqual(w.split(separator: " ").count, 2, w) }
      }
    }
    Lang.override = "zh"
    XCTAssertEqual(WatchTodayBuilder.verdict(82), "可上强度")
    XCTAssertEqual(WatchTodayBuilder.verdict(54), "正常练")
    XCTAssertEqual(WatchTodayBuilder.tone(20), .bad)
    XCTAssertEqual(WatchTodayBuilder.tone(54), .mid)
    XCTAssertEqual(WatchTodayBuilder.tone(67), .good)
  }

  func testThePhonesCapsuleForTheWatch() {
    XCTAssertEqual(WatchTodayBuilder.phone(.ok(ago: "2分前"), offline: false), WatchPhoneSync(.ok))
    XCTAssertEqual(WatchTodayBuilder.phone(.busy, offline: false), WatchPhoneSync(.busy))
    XCTAssertEqual(WatchTodayBuilder.phone(.stale("离线"), offline: true), WatchPhoneSync(.offline, "离线"))
    XCTAssertEqual(WatchTodayBuilder.phone(.stale("6时前"), offline: false), WatchPhoneSync(.stale, "6时前"))
    XCTAssertEqual(WatchTodayBuilder.phone(.behind("缺2天"), offline: false), WatchPhoneSync(.behind, "缺2天"))
    XCTAssertEqual(WatchTodayBuilder.phone(.failed, offline: false), WatchPhoneSync(.failed))
    XCTAssertEqual(WatchTodayBuilder.phone(.off, offline: false).kind, .off)
  }

  func testQuickTilesFromTheQuickMeals() {
    let list = json("""
    {"t": [
      {"id": 1, "name": "Oats", "meal": "breakfast", "totals": {"kcal": 420, "protein_g": 18}, "uses_30d": 3},
      {"id": 2, "name": "Guess", "meal": "lunch", "totals": {"kcal": 700}, "uses_30d": 9, "needs_confirmation": true},
      {"id": 3, "name": "Rice", "meal": "lunch", "totals": {"kcal": 570, "protein_g": 42}, "uses_30d": 8},
      {"id": 4, "name": "Yogurt", "meal": "snacks", "totals": {"kcal": 150}, "uses_30d": 5},
      {"id": 5, "name": "Soup", "meal": "dinner", "totals": {"kcal": 300}, "uses_30d": 1}
    ]}
    """)["t"] as? [[String: Any]]
    let templates = MealTemplate.parseList(list, pending: [], today: "2026-01-02")
    let nutrition = NutritionDay(date: "2026-01-02", goal: Macros(kcal: 2300), goalName: nil, intake: Macros(kcal: 610), entries: [])
    let q = WatchQuickBuilder.quick(templates: templates, nutrition: nutrition, day: "2026-01-02", water: true, results: ["x": true])
    XCTAssertEqual(q.meals.map(\.id), [3, 4, 1])            // most used first; the one that needs its grams confirmed is not offered
    XCTAssertEqual(q.meals.first?.kcal, 570)
    XCTAssertEqual(q.meals.first?.proteinG, 42)
    XCTAssertEqual(q.waterMl, WatchParams.waterQuickMl)
    XCTAssertEqual(q.kcal, 610)
    XCTAssertEqual(q.kcalGoal, 2300)
    XCTAssertEqual(q.results, ["x": true])
    // water off; another day's intake is not today's
    let off = WatchQuickBuilder.quick(templates: templates, nutrition: nutrition, day: "2026-01-03", water: false, results: [:])
    XCTAssertNil(off.waterMl)
    XCTAssertNil(off.kcal)
  }

  /// The phone's inbox written by phase 1 (no quick logs) still loads with its seen ids.
  func testInboxFromPhaseOneStillLoads() throws {
    let old = #"{"seen": ["a", "b"], "held": []}"#
    let inbox = try XCTUnwrap(WatchCodec.decode(PhoneWatchInbox.self, Data(old.utf8)))
    XCTAssertEqual(inbox.seen, ["a", "b"])
    XCTAssertTrue(inbox.quick.isEmpty)
    var i = inbox
    let m = WatchSetEvent.quick(.meal, id: "m", day: "2026-01-02", at: t0, templateId: 3)
    i.logged(m, counted: true)
    i.logged(m, counted: true)
    XCTAssertEqual(i.quick.count, 1)
    XCTAssertEqual(i.results, ["m": true])
    i.uncount("m")
    XCTAssertEqual(i.results, ["m": false])
    XCTAssertEqual(i.original("m")?.event, m)
    XCTAssertEqual(WatchCodec.decode(PhoneWatchInbox.self, WatchCodec.encode(i)), i)
  }

  @MainActor
  func testPushesOnlyWhenSomethingBesidesTheTimeChanged() {
    let a = WatchContext(signedIn: true, plan: nil, lang: "zh", sentAt: t0, planFor: "2026-01-02", today: today())
    var b = a
    b.sentAt = t0.addingTimeInterval(30)
    b.today?.computedAt = t0.addingTimeInterval(30)
    b.today?.dataAt = a.today?.dataAt?.addingTimeInterval(20)
    XCTAssertTrue(PhoneWatchBridge.sameExceptTimes(b, a))
    b.today?.recovery = 80
    XCTAssertFalse(PhoneWatchBridge.sameExceptTimes(b, a))
    XCTAssertFalse(PhoneWatchBridge.sameExceptTimes(a, nil))
  }
}
