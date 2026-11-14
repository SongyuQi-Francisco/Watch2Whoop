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
    let today = "2026-01-02"
    let ctx = WatchContext(signedIn: true, plan: plan([ex("bench")]), lang: "zh", sentAt: t0)
    func state(_ c: WatchContext?, _ q: WatchEventQueue = WatchEventQueue(), reachable: Bool = true, running: Bool = false,
               day: String = today) -> WatchSyncState {
      WatchSyncState.derive(context: c, queue: q, reachable: reachable, today: day, sessionRunning: running, now: t0.addingTimeInterval(150))
    }
    XCTAssertEqual(state(nil), .signedOut)                                                                  // S8
    XCTAssertEqual(state(WatchContext(signedIn: false, plan: nil, lang: "zh", sentAt: t0)), .signedOut)
    XCTAssertEqual(state(WatchContext(signedIn: true, plan: nil, lang: "zh", sentAt: t0)), .noPlan)       // S9
    XCTAssertEqual(state(ctx, day: "2026-01-03"), .noPlan)                                                 // yesterday's plan
    XCTAssertEqual(state(ctx, running: true, day: "2026-01-03"), .linked(minutesAgo: nil))                // past midnight mid-session
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
