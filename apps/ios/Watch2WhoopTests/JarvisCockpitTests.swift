import XCTest
@testable import Watch2Whoop

/// 2.0.1 Jarvis cockpit: the state machine (transitions, interruptible springs, the auto-send clock), the pure helpers of the
/// reply card. No UI, no network; made-up words and numbers only.
@MainActor
final class JarvisCockpitTests: XCTestCase {
  private let c = CGPoint(x: 201, y: 751)

  /// Steps the machine `seconds` at 120 Hz and returns every effect on the way.
  @discardableResult
  private func run(_ m: inout CockpitMachine, _ seconds: Double) -> [CockpitEffect] {
    var out: [CockpitEffect] = []
    var t = 0.0
    while t < seconds - 1e-9 {
      out += m.step(1.0 / 120)
      t += 1.0 / 120
    }
    return out
  }

  private func panel(_ m: CockpitMachine, _ key: CockpitKey) -> CGPoint {
    let it = m.items.first { $0.key == key }!
    return CockpitGeometry.point(CockpitGeometry.panelMid, it.angle, around: c)
  }

  // MARK: - open / close

  func testCoreOpensTheFirstRingAndClosesAgain() {
    var m = CockpitMachine()
    XCTAssertEqual(m.mode, .idle)
    XCTAssertEqual(m.tap(at: c, center: c), [.opened])
    XCTAssertEqual(m.mode, .menu)
    XCTAssertEqual(m.items.map(\.key), [.meal, .train, .talk, .photo, .ask])
    run(&m, 1.2)
    XCTAssertTrue(m.panels.allSatisfy { abs($0.x - 1) < 0.02 }, "every panel out")
    XCTAssertGreaterThan(m.on.x, 0.98, "the wash is on")
    // the core again closes
    XCTAssertEqual(m.tap(at: c, center: c), [.closed])
    XCTAssertEqual(m.mode, .idle)
    run(&m, 1.2)
    XCTAssertTrue(m.panels.allSatisfy { abs($0.x) < 0.02 })
    XCTAssertFalse(m.isActive, "settles: the view can slow down")
  }

  func testEmptySpaceClosesTheMenu() {
    var m = CockpitMachine()
    _ = m.tapCore()
    run(&m, 0.6)
    XCTAssertEqual(m.tap(at: CGPoint(x: 40, y: 120), center: c), [.closed])
    XCTAssertEqual(m.mode, .idle)
  }

  func testPanelsFanOutStaggeredFromTheMiddle() {
    var m = CockpitMachine()
    _ = m.tapCore()
    run(&m, 0.05)
    // 说话 (0°) starts at once, the outer ones (±72°) 0.12 s later
    XCTAssertGreaterThan(m.panels[2].x, 0.05)
    XCTAssertEqual(m.panels[0].x, 0, accuracy: 1e-9)
    XCTAssertEqual(m.panels[4].x, 0, accuracy: 1e-9)
  }

  /// Tapping the core while the panels are still fanning out folds them back from where they are: no jump, and they keep
  /// going towards zero.
  func testOpeningCanBeInterrupted() {
    var m = CockpitMachine()
    _ = m.tapCore()
    run(&m, 0.09)
    let mid = m.panels.map(\.x)
    XCTAssertTrue(mid[2] > 0.1 && mid[2] < 1, "halfway out")
    XCTAssertEqual(m.tapCore(), [.closed])
    XCTAssertEqual(m.panels.map(\.x), mid, "the springs keep their place when the target flips")
    run(&m, 1.0 / 120)
    XCTAssertLessThan(abs(m.panels[2].x - mid[2]), 0.05, "continuous: one frame later still near")
    run(&m, 1.2)
    XCTAssertTrue(m.panels.allSatisfy { abs($0.x) < 0.02 }, "folded back")
    // and it can open again at once
    XCTAssertEqual(m.tapCore(), [.opened])
    XCTAssertEqual(m.mode, .menu)
  }

  // MARK: - second rings

  func testMealOpensTheSecondRingAndTheCrumbGoesBack() {
    var m = CockpitMachine()
    m.meals = [(name: "鸡胸饭", kcal: 570), (name: "燕麦碗", kcal: 420)]
    _ = m.tapCore()
    run(&m, 0.8)
    XCTAssertEqual(m.tap(at: panel(m, .meal), center: c), [])
    XCTAssertEqual(m.selected, 0, "lit while it waits")
    run(&m, CockpitTiming.select + CockpitTiming.subRing + 0.02)
    XCTAssertEqual(m.level, 2)
    XCTAssertEqual(m.items.map(\.key), [.mealPhoto, .mealTalk, .template(0), .template(1), .mealMore])
    XCTAssertEqual(m.items[2].title, "鸡胸饭")
    XCTAssertEqual(m.items[2].sub, "570 kcal")
    XCTAssertEqual(m.crumbIcon, .soup)
    run(&m, 1.5)
    XCTAssertEqual(m.rot.x, 36, accuracy: 0.5, "the tick ring turned one notch")
    XCTAssertGreaterThan(m.crumb.x, 0.95, "the chosen key docks above the core")
    // the crumb: back to the first ring, the tick ring turns back
    XCTAssertEqual(m.tap(at: CGPoint(x: c.x, y: c.y - CockpitGeometry.crumbDistance), center: c), [])
    run(&m, CockpitTiming.back + 1.5)
    XCTAssertEqual(m.level, 1)
    XCTAssertEqual(m.items.map(\.key), [.meal, .train, .talk, .photo, .ask])
    XCTAssertEqual(m.rot.x, 0, accuracy: 0.5)
  }

  func testTrainRingHasFourKeys() {
    var m = CockpitMachine()
    _ = m.tapCore()
    run(&m, 0.8)
    m.activate(.train)
    run(&m, 0.7)
    XCTAssertEqual(m.items.map(\.key), [.trainStart, .trainTalk, .trainBackfill, .trainPlan])
    XCTAssertEqual(m.items.map(\.angle), [-54, -18, 18, 54])
    XCTAssertEqual(m.crumbIcon, .dumbbell)
  }

  func testMealRingWithOneQuickMealLeavesItsPlaceEmpty() {
    let ring = CockpitItem.mealRing([(name: "燕麦碗", kcal: nil)])
    XCTAssertEqual(ring.map(\.key), [.mealPhoto, .mealTalk, .template(0), .mealMore])
    XCTAssertNil(ring[2].sub)
  }

  func testATappedPanelIgnoresFurtherTapsUntilItActs() {
    var m = CockpitMachine()
    _ = m.tapCore()
    run(&m, 0.8)
    _ = m.tap(at: panel(m, .photo), center: c)
    XCTAssertEqual(m.tap(at: panel(m, .ask), center: c), [])
    XCTAssertEqual(m.selected, 3)
    let fx = run(&m, CockpitTiming.select + 0.02)
    XCTAssertEqual(fx, [.closed, .run(.photo)], "closes, then the app opens the camera")
    XCTAssertEqual(m.mode, .idle)
  }

  func testQuickMealThinksUntilTheCardIsReady() {
    var m = CockpitMachine()
    m.meals = [(name: "鸡胸饭", kcal: 570)]
    _ = m.tapCore()
    run(&m, 0.8)
    m.activate(.meal)
    run(&m, 0.8)
    m.activate(.template(0))
    let fx = run(&m, CockpitTiming.select + 0.02)
    XCTAssertEqual(fx, [.run(.template(0))])
    XCTAssertEqual(m.mode, .think)
    XCTAssertEqual(m.flow, .meal)
    m.showCard()
    XCTAssertEqual(m.mode, .card)
    run(&m, 1.5)
    XCTAssertGreaterThan(m.cardWidth.x, 0.98)
    XCTAssertGreaterThan(m.cardHeight.x, 0.95, "unfolded")
    // anywhere outside the card (the card's own buttons are views above) closes it
    XCTAssertEqual(m.tap(at: CGPoint(x: 30, y: 60), center: c), [.closed])
    run(&m, 1.5)
    XCTAssertLessThan(m.cardWidth.x, 0.02)
  }

  // MARK: - voice

  private func listening(_ key: CockpitKey = .talk) -> CockpitMachine {
    var m = CockpitMachine()
    _ = m.tapCore()
    run(&m, 0.8)
    m.activate(key)
    let fx = run(&m, CockpitTiming.select + 0.02)
    XCTAssertEqual(fx, [.listen(.qa)])
    XCTAssertEqual(m.mode, .listen)
    return m
  }

  func testPauseThenOneSecondRingThenSend() {
    var m = listening()
    m.heard("今天适合")
    run(&m, 0.3)
    m.heard("今天适合练腿吗")
    run(&m, CockpitTiming.pause - 0.05)
    XCTAssertEqual(m.mode, .listen, "still talking (words changed recently)")
    run(&m, 0.1)
    XCTAssertEqual(m.mode, .confirm, "the pause is heard")
    XCTAssertEqual(run(&m, CockpitTiming.autoSend - 0.15), [])
    XCTAssertEqual(m.mode, .confirm)
    XCTAssertEqual(run(&m, 0.2), [.send(.qa)], "sent after one second")
    XCTAssertEqual(m.mode, .think)
    XCTAssertEqual(m.words, "今天适合练腿吗")
  }

  func testCoreSendsAtOnceDuringTheRing() {
    var m = listening()
    m.heard("今天适合练腿吗")
    run(&m, CockpitTiming.pause + 0.05)
    XCTAssertEqual(m.mode, .confirm)
    XCTAssertEqual(m.tapCore(), [.send(.qa)])
    XCTAssertEqual(m.mode, .think)
  }

  func testCoreDuringListeningStopsAndWaitsForTheRing() {
    var m = listening()
    m.heard("中午吃了")
    XCTAssertEqual(m.tapCore(), [])
    XCTAssertEqual(m.mode, .confirm)
  }

  func testMoreWordsDuringTheRingListenAgain() {
    var m = listening()
    m.heard("中午吃了")
    run(&m, CockpitTiming.pause + 0.05)
    XCTAssertEqual(m.mode, .confirm)
    m.heard("中午吃了鸡胸饭")
    XCTAssertEqual(m.mode, .listen)
    run(&m, 0.5)
    XCTAssertEqual(m.mode, .listen, "the clock started again")
  }

  func testCancelLeftOfTheCore() {
    var m = listening()
    m.heard("卧推")
    let fx = m.tap(at: CGPoint(x: c.x + CockpitGeometry.cancelOffset, y: c.y), center: c)
    XCTAssertEqual(fx, [.stopListening, .closed])
    XCTAssertEqual(m.mode, .idle)
    XCTAssertEqual(run(&m, 3), [], "nothing is sent later")
  }

  func testNoWordsGivesUp() {
    var m = listening()
    let fx = run(&m, CockpitTiming.noWords + 0.1)
    XCTAssertEqual(fx, [.stopListening, .closed, .gaveUp])
    XCTAssertEqual(m.mode, .idle)
  }

  func testCoreWithNoWordsYetCancels() {
    var m = listening()
    XCTAssertEqual(m.tapCore(), [.stopListening, .closed])
  }

  func testSecondRingVoiceKeysPickTheirFlow() {
    var m = CockpitMachine()
    _ = m.tapCore()
    run(&m, 0.8)
    m.activate(.train)
    run(&m, 1.2)
    m.activate(.trainTalk)
    XCTAssertEqual(run(&m, CockpitTiming.select + 0.02), [.listen(.set)])
    m.heard("卧推六十公斤八个")
    XCTAssertEqual(run(&m, CockpitTiming.pause + CockpitTiming.autoSend + 0.2), [.send(.set)])

    var n = CockpitMachine()
    _ = n.tapCore()
    run(&n, 0.8)
    n.activate(.meal)
    run(&n, 1.2)
    n.activate(.mealTalk)
    XCTAssertEqual(run(&n, CockpitTiming.select + 0.02), [.listen(.meal)])
  }

  func testHoldOnTheClosedCoreTalksAtOnce() {
    var m = CockpitMachine()
    XCTAssertEqual(m.startListening(.qa), [.listen(.qa)])
    XCTAssertEqual(m.mode, .listen)
    XCTAssertEqual(m.startListening(.qa), [], "only from idle")
  }

  func testWordsAreIgnoredOutsideListening() {
    var m = CockpitMachine()
    m.heard("hello")
    XCTAssertEqual(m.words, "")
  }

  // MARK: - reduced motion

  func testReducedMotionFadesWithoutStaggerOrTurning() {
    var m = CockpitMachine()
    m.reduceMotion = true
    let a0 = m.tickAngle
    _ = m.tapCore()
    run(&m, 0.02)
    XCTAssertGreaterThan(m.panels[0].x, 0.01, "no stagger: the outer panel starts at once")
    run(&m, 0.5)
    XCTAssertTrue(m.panels.allSatisfy { $0.x <= 1.0001 && $0.x > 0.97 }, "no overshoot")
    m.activate(.meal)
    run(&m, 1)
    XCTAssertEqual(m.rot.x, 0, "the tick ring does not turn")
    XCTAssertEqual(m.tickAngle, a0, "the core does not spin")
  }

  // MARK: - helpers of the card

  func testVerdictFromTheAnswer() {
    var v = CockpitText.verdict(from: "**可以练**，恢复在正常范围。\n\n- HRV 上升")
    XCTAssertEqual(v.verdict, "可以练")
    XCTAssertEqual(v.sub, "恢复在正常范围")
    v = CockpitText.verdict(from: "今天建议休息，昨晚只睡了五个小时。")
    XCTAssertEqual(v.verdict, "今天建议休息")
    XCTAssertEqual(v.sub, "昨晚只睡了五个小时")
    v = CockpitText.verdict(from: "Good to train. Recovery is in range.")
    XCTAssertEqual(v.verdict, "Good to tra…", "cut to fit the card")
    XCTAssertNil(v.sub)
    XCTAssertEqual(CockpitText.verdict(from: "  ").verdict, "…")
  }

  func testSetText() {
    XCTAssertEqual(CockpitText.set(weightKg: 60, reps: 8, seconds: nil), "60 kg × 8")
    XCTAssertEqual(CockpitText.set(weightKg: 62.5, reps: 5, seconds: nil), "62.5 kg × 5")
    XCTAssertEqual(CockpitText.set(weightKg: nil, reps: 12, seconds: nil), "× 12")
    XCTAssertEqual(CockpitText.set(weightKg: nil, reps: nil, seconds: 45), "45 s")
  }

  func testPrimaryActionFromCards() {
    let card = JSONValue.object(["type": .string("training"),
                                 "actions": .array([.object(["kind": .string("send"), "key": .string("adjust_training")]),
                                                    .object(["kind": .string("open"), "page": .string("record.training")])])])
    XCTAssertEqual(CockpitController.primary([card])?.page, "record.training")
    XCTAssertNil(CockpitController.primary([.object(["type": .string("metric")])]))
  }

  func testSetCardFindsTheExerciseThatGainedASet() throws {
    let doc: [String: Any] = [
      "sessions": [["name": "上肢 A", "exercises": [
        ["slug": "bench", "name_zh": "卧推", "target": ["sets": 3, "reps": 8], "load": ["kg": 60]],
        ["slug": "row", "name_zh": "划船", "target": ["sets": 3, "reps": 10]]]]],
      "can_log": true,
      "log": ["exercises": [["slug": "bench", "sets": [["reps": 8, "weight_kg": 60, "done": true], ["reps": 8, "weight_kg": 60, "done": true],
                                                         ["reps": 8, "weight_kg": 60, "done": false]]]]],
    ]
    let s = try XCTUnwrap(TrainingSession.build(doc, day: "2026-11-12", pending: []))
    let card = try XCTUnwrap(CockpitController.setCard(s, before: ["bench": 1]))
    XCTAssertEqual(card.slug, "bench")
    XCTAssertEqual(card.log.exercise, "卧推")
    XCTAssertEqual(card.log.set, "60 kg × 8")
    XCTAssertEqual(card.log.index, 2)
    XCTAssertEqual(card.log.planned, 3)
    XCTAssertNil(CockpitController.setCard(s, before: ["bench": 2]), "nothing new")
  }

  func testMealCardShares() {
    var day = NutritionDay(date: "2026-11-12", goal: Macros(kcal: 2300, protein: 60, carbs: 100, fat: 35), goalName: nil,
                           intake: Macros(kcal: 990), entries: [])
    day.intake = Macros(kcal: 990)
    let m = CockpitController.mealCard(slot: "午餐", name: "鸡胸饭", added: Macros(kcal: 570, protein: 45, carbs: 66, fat: 14),
                                       before: 420, day: day)
    XCTAssertEqual(m.dayAfter, 990)
    XCTAssertEqual(m.goal, 2300)
    XCTAssertEqual(m.macros[0].fraction ?? 0, 0.75, accuracy: 0.001)
    XCTAssertEqual(m.macros[1].fraction ?? 0, 0.66, accuracy: 0.001)
    XCTAssertEqual(m.macros[2].fraction ?? 0, 0.4, accuracy: 0.001)
  }
}
