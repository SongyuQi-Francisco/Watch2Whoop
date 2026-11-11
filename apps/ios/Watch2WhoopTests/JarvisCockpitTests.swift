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
    m.meals = [CockpitMeal(id: 31, name: "鸡胸饭", kcal: 570), CockpitMeal(id: 32, name: "燕麦碗", kcal: 420)]
    _ = m.tapCore()
    run(&m, 0.8)
    XCTAssertEqual(m.tap(at: panel(m, .meal), center: c), [])
    XCTAssertEqual(m.selected, 0, "lit while it waits")
    run(&m, CockpitTiming.select + CockpitTiming.subRing + 0.02)
    XCTAssertEqual(m.level, 2)
    XCTAssertEqual(m.items.map(\.key), [.mealPhoto, .mealTalk, .template(31), .template(32), .mealMore], "keys carry the template id")
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

  /// 记训练 has no 说一组: no tool of the in-app AI writes one set into today's session.
  func testTrainRingHasThreeKeys() {
    var m = CockpitMachine()
    _ = m.tapCore()
    run(&m, 0.8)
    m.activate(.train)
    run(&m, 0.7)
    XCTAssertEqual(m.items.map(\.key), [.trainStart, .trainBackfill, .trainPlan])
    XCTAssertEqual(m.items.map(\.angle), [-36, 0, 36])
    XCTAssertFalse(m.items.contains { $0.key == .trainTalk })
    XCTAssertEqual(m.crumbIcon, .dumbbell)
  }

  func testMealRingWithOneQuickMealLeavesItsPlaceEmpty() {
    let ring = CockpitItem.mealRing([CockpitMeal(id: 7, name: "燕麦碗", kcal: nil)])
    XCTAssertEqual(ring.map(\.key), [.mealPhoto, .mealTalk, .template(7), .mealMore])
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
    m.meals = [CockpitMeal(id: 31, name: "鸡胸饭", kcal: 570)]
    _ = m.tapCore()
    run(&m, 0.8)
    m.activate(.meal)
    run(&m, 0.8)
    m.activate(.template(31))
    m.meals = [CockpitMeal(id: 40, name: "别的", kcal: 100)]   // the list changed before the tap acts
    let fx = run(&m, CockpitTiming.select + 0.02)
    XCTAssertEqual(fx, [.run(.template(31))], "the tapped meal, by id")
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
    m.autoSend = true   // flag on; the flag-off tests turn it off
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

  func testMealVoiceKeyPicksItsFlow() {
    var n = CockpitMachine()
    n.autoSend = true
    _ = n.tapCore()
    run(&n, 0.8)
    n.activate(.meal)
    run(&n, 1.2)
    n.activate(.mealTalk)
    XCTAssertEqual(run(&n, CockpitTiming.select + 0.02), [.listen(.meal)])
    n.heard("中午吃了鸡胸饭")
    XCTAssertEqual(run(&n, CockpitTiming.pause + CockpitTiming.autoSend + 0.2), [.send(.meal)])
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

  func testPrimaryActionFromCards() {
    let card = JSONValue.object(["type": .string("training"),
                                 "actions": .array([.object(["kind": .string("send"), "key": .string("adjust_training")]),
                                                    .object(["kind": .string("open"), "page": .string("record.training")])])])
    XCTAssertEqual(TurnReading.primary([card])?.page, "record.training")
    XCTAssertNil(TurnReading.primary([.object(["type": .string("metric")])]))
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

  // MARK: - review fixes (2026-11-12)

  /// Flag jarvis_voice_direct off: the pause does not send; the words wait (corrected) until 发送.
  func testFlagOffWaitsForSend() {
    var m = listening()
    m.autoSend = false
    m.heard("今天适合练腿吗")
    run(&m, CockpitTiming.pause + 0.05)
    XCTAssertEqual(m.mode, .confirm)
    XCTAssertEqual(run(&m, 5), [], "nothing goes out by itself")
    XCTAssertEqual(m.mode, .confirm)
    m.correct("今天适合练腿吗？")
    XCTAssertEqual(m.words, "今天适合练腿吗？", "the server's words replace the phone's while they wait")
    XCTAssertEqual(m.mode, .confirm)
    XCTAssertEqual(m.sendNow(), [.send(.qa)])
    XCTAssertEqual(m.mode, .think)
  }

  func testCorrectionOnlyWhileWaiting() {
    var m = listening()
    m.heard("中午")
    m.correct("别的")
    XCTAssertEqual(m.words, "中午", "while listening the recogniser's words stand")
  }

  func testSixtySecondsStopsListening() {
    var m = listening()
    XCTAssertEqual(m.timeUp(), [.stopListening, .closed, .gaveUp], "no words: give up")
    var n = listening()
    n.heard("卧推")
    XCTAssertEqual(n.timeUp(), [])
    XCTAssertEqual(n.mode, .confirm, "words: as if the core was tapped")
  }

  /// A write that went out while the cockpit was closed still shows its result (with 撤销).
  func testCardComesBackAfterClosingDuringThink() {
    var m = CockpitMachine()
    m.meals = [CockpitMeal(id: 31, name: "鸡胸饭", kcal: 570)]
    _ = m.tapCore()
    run(&m, 0.8)
    m.activate(.meal)
    run(&m, 0.8)
    m.activate(.template(31))
    run(&m, CockpitTiming.select + 0.02)
    XCTAssertEqual(m.mode, .think)
    XCTAssertEqual(m.tap(at: CGPoint(x: 30, y: 60), center: c), [.closed])
    m.showCard()
    XCTAssertEqual(m.mode, .card)
    run(&m, 1.5)
    XCTAssertGreaterThan(m.cardHeight.x, 0.95)
  }

  func testLanguageChip() {
    var m = listening()
    m.canSwitchLanguage = true
    m.heard("hello")
    let fx = m.tap(at: CGPoint(x: c.x + CockpitGeometry.langOffset, y: c.y), center: c)
    XCTAssertEqual(fx, [.switchLanguage])
    XCTAssertEqual(m.mode, .listen)
    XCTAssertEqual(m.words, "", "starts again in the other language")
    var n = listening()
    XCTAssertEqual(n.tap(at: CGPoint(x: c.x + CockpitGeometry.langOffset, y: c.y), center: c), [.stopListening, .closed],
                   "no chip when one language only: a tap outside")
  }

  func testIdleSettlesSoNothingRedraws() {
    var m = CockpitMachine()
    _ = m.tapCore()
    run(&m, 0.4)
    m.heard("x")
    _ = m.close()
    run(&m, 2)
    XCTAssertFalse(m.isActive)
  }

  // MARK: - the controller

  private func item(_ id: Int, _ k: JarvisChat.Item.Kind) -> JarvisChat.Item { JarvisChat.Item(id: id, kind: k) }

  func testTurnReadingQuestion() {
    var today = TodayModel(json: [:])
    today.rings = [TodayModel.Ring(key: "recovery", label: "", value: "82", fraction: 0.82, tone: .good, action: .recovery),
                   TodayModel.Ring(key: "sleep", label: "", value: "88", fraction: 0.88, tone: .sleep, action: .sleepDetail(nil))]
    let r = TurnReading.read([item(1, .assistant("**可以练**，恢复在正常范围。"))], question: "今天适合练腿吗", flow: .qa, today: today)
    guard case .answer(let a) = r else { return XCTFail("\(r)") }
    XCTAssertEqual(a.verdict, "可以练")
    XCTAssertEqual(a.gauge?.value, "82")
    XCTAssertEqual(a.readings.map(\.label).count, 1)
    XCTAssertNil(a.undoText, "nothing was logged")
  }

  /// What the AI logged: no ids, so no guessing; 撤销 asks the AI, and the day's total is the server's.
  func testTurnReadingMealUndoGoesToTheAI() {
    let card = JSONValue.object(["type": .string("meal"),
                                 "totals": .object(["calories": .number(990)]), "goal": .object(["calories": .number(2300)]),
                                 "actions": .array([.object(["kind": .string("send"), "key": .string("undo_meal")])])])
    let r = TurnReading.read([item(1, .card(card)), item(2, .assistant("已记到午餐：鸡胸饭 570 kcal。"))], question: "中午吃了鸡胸饭",
                             flow: .meal, today: nil)
    guard case .answer(let a) = r else { return XCTFail("\(r)") }
    XCTAssertEqual(a.verdict, "已记到午餐")
    XCTAssertEqual(a.sub, "鸡胸饭 570 kcal")
    XCTAssertEqual(a.readings.first?.value, "990 / 2,300 kcal")
    XCTAssertEqual(a.undoText, JarvisCards.send("undo_meal", .object([:]))?.1)
  }

  /// 说一组: there is no tool that writes one set into today's session, so the card is the AI's words, nothing 「已记到」.
  func testTurnReadingSetIsWordsOnly() {
    let r = TurnReading.read([item(1, .assistant("卧推 60 kg × 8，第 5 组。"))], question: "卧推六十公斤八个", flow: .set, today: nil)
    guard case .answer(let a) = r else { return XCTFail("\(r)") }
    XCTAssertNil(a.undoText)
    XCTAssertNil(a.gauge)
    XCTAssertTrue(a.readings.isEmpty)
  }

  func testTurnReadingConfirmAndFailure() {
    let call = PendingCall(.object(["call_id": .number(41), "tool": .string("undo_meal_log"), "summary": .string("x")]))!
    XCTAssertEqual(TurnReading.read([item(1, .confirm(call, approved: nil))], question: "", flow: .meal, today: nil), .needsConfirm)
    XCTAssertEqual(TurnReading.read([item(1, .note("额度用完了", warn: true, retry: false))], question: "", flow: .qa, today: nil),
                   .failed("额度用完了"))
  }

  func testMealsKeepTheirIds() {
    let t = [MealTemplate(id: 5, name: "燕麦碗", meal: .breakfast, totals: Macros(kcal: 420), items: "", todayLog: nil, pending: nil, uses30d: 2),
             MealTemplate(id: 9, name: "鸡胸饭", meal: .lunch, totals: Macros(kcal: 570), items: "", todayLog: nil, pending: nil, uses30d: 7)]
    XCTAssertEqual(CockpitController.meals(t), [CockpitMeal(id: 9, name: "鸡胸饭", kcal: 570), CockpitMeal(id: 5, name: "燕麦碗", kcal: 420)])
  }

  /// A frame's published state is read when it is applied: a change in between is not overwritten by the older frame.
  func testFrameNeverPublishesAnOlderMode() async {
    let ctl = CockpitController(voice: JarvisVoice())
    _ = ctl.machine.tapCore()           // menu
    ctl.frame(1)                        // schedules publishing
    _ = ctl.machine.close()             // the user closed it before that ran
    await Task.yield()
    for _ in 0..<5 { await withCheckedContinuation { c in DispatchQueue.main.async { c.resume() } } }
    XCTAssertEqual(ctl.mode, .idle)
  }

  // MARK: - second review (2026-11-12)

  /// An effect from an earlier frame is dropped when the user has overtaken it (× before the microphone started, a close
  /// before the words went out).
  func testOvertakenEffectsAreDropped() {
    var m = listening()
    XCTAssertTrue(CockpitController.stillValid(.listen(.qa), m))
    _ = m.close()
    XCTAssertFalse(CockpitController.stillValid(.listen(.qa), m), "closed before the frame's effect ran: no microphone")
    XCTAssertFalse(CockpitController.stillValid(.switchLanguage, m))
    XCTAssertFalse(CockpitController.stillValid(.send(.qa), m), "nothing goes out after a close")
    XCTAssertTrue(CockpitController.stillValid(.stopListening, m))
    XCTAssertTrue(CockpitController.stillValid(.closed, m))
    var n = listening()
    n.heard("今天适合练腿吗")
    _ = n.sendNow()
    XCTAssertTrue(CockpitController.stillValid(.send(.qa), n))
    XCTAssertFalse(CockpitController.stillValid(.send(.meal), n), "another flow's send")
    XCTAssertFalse(CockpitController.stillValid(.run(.template(3)), CockpitMachine()), "a quick meal only while it thinks")
  }

  /// The quick meal's 撤销 is bound to this write: the log id the server gave it, never an older one.
  func testUndoIsBoundToThisWrite() {
    XCTAssertEqual(CockpitController.thisWrite(before: nil, after: 51), 51)
    XCTAssertNil(CockpitController.thisWrite(before: 51, after: 51), "an older logging of the template: not ours")
    XCTAssertNil(CockpitController.thisWrite(before: nil, after: nil), "still queued: undone by its client id")
    XCTAssertEqual(CockpitController.thisWrite(before: 50, after: 51), 51)
  }

  /// The flag gate: without the account's flags (unknown, not loaded) voice never sends by itself.
  func testVoiceDirectUnknownMeansConfirmFirst() {
    XCTAssertFalse(JarvisRouter().voiceDirect)
    XCTAssertFalse(CockpitMachine().autoSend, "the machine waits for 发送 until the flag says otherwise")
  }
}
