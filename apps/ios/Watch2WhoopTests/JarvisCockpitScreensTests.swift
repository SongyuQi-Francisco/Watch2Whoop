import XCTest
@testable import Watch2Whoop

/// 2.0.1 screens behind the cockpit's keys (16 拍照, 17 提问, 19 计划, 20 快记): the pure parts. Made-up meals and plans only.
@MainActor
final class JarvisCockpitScreensTests: XCTestCase {
  private func template(_ id: Int, _ name: String, _ meal: MealSlot, uses: Int, kcal: Double = 400) -> MealTemplate {
    MealTemplate(id: id, name: name, meal: meal, totals: Macros(kcal: kcal, protein: 30, carbs: 40, fat: 10), items: name, todayLog: nil,
                 pending: nil, uses30d: uses)
  }

  // MARK: - 20 快记

  func testTemplateRowsSortFilterAndSearch() {
    let all = [template(1, "燕麦碗", .breakfast, uses: 3), template(2, "鸡胸饭", .lunch, uses: 9), template(3, "三文鱼饭", .dinner, uses: 9)]
    XCTAssertEqual(CockpitTemplatesSheet.rows(all, query: "", filter: .common).map(\.id), [2, 3, 1], "most used first, ties keep order")
    XCTAssertEqual(CockpitTemplatesSheet.rows(all, query: "", filter: .meal(.lunch)).map(\.id), [2])
    XCTAssertEqual(CockpitTemplatesSheet.rows(all, query: "饭", filter: .common).map(\.id), [2, 3])
    XCTAssertEqual(CockpitTemplatesSheet.rows(all, query: "饭", filter: .meal(.breakfast)).map(\.id), [])
  }

  func testMacroBarSharesAreEnergyShares() {
    let s = MacroBar.shares(Macros(kcal: 400, protein: 25, carbs: 50, fat: 10))   // 100 / 200 / 90 kcal
    XCTAssertEqual(s.count, 3)
    XCTAssertEqual(s[0], 100.0 / 390, accuracy: 1e-9)
    XCTAssertEqual(s[2], 90.0 / 390, accuracy: 1e-9)
    XCTAssertEqual(MacroBar.shares(Macros(kcal: 100)), [], "no macros: no bar")
  }

  // MARK: - 19 计划

  func testPlanSetsAndSplits() {
    XCTAssertEqual(CockpitPlanSheet.sets("4 × 8"), 4)
    XCTAssertEqual(CockpitPlanSheet.sets("12 × 10"), 12)
    XCTAssertEqual(CockpitPlanSheet.sets(nil), 0)
    func split(_ type: String, _ n: Int) -> SplitOption {
      SplitOption(type: type, name: type, summary: "", days: (1...n).map { SplitOption.Day(dayOfWeek: $0, name: "D\($0)", exerciseCount: 5) })
    }
    let splits = [split("3_day_full", 3), split("4_day_upper_lower", 4), split("4_day_push_pull", 4), split("5_day_ppl", 5)]
    XCTAssertEqual(CockpitPlanSheet.dayCounts(splits), [3, 4, 5])
    XCTAssertEqual(CockpitPlanSheet.split(days: 4, in: splits, current: "4_day_push_pull")?.type, "4_day_push_pull", "keeps the plan's own")
    XCTAssertEqual(CockpitPlanSheet.split(days: 4, in: splits, current: "3_day_full")?.type, "4_day_upper_lower", "the picker's default")
    XCTAssertEqual(CockpitPlanSheet.split(days: 5, in: splits, current: nil)?.type, "5_day_ppl")
    XCTAssertNil(CockpitPlanSheet.split(days: 6, in: splits, current: nil))
  }

  // MARK: - 17 提问

  func testContextChipsOnlyForReadingsWithData() {
    var m = TodayModel(json: [:])
    m.rings = [TodayModel.Ring(key: "sleep", label: "", value: "88", fraction: 0.88, tone: .sleep, action: .sleepDetail(nil)),
               TodayModel.Ring(key: "recovery", label: "", value: "82", fraction: 0.82, tone: .good, action: .recovery),
               TodayModel.Ring(key: "strain", label: "", value: nil, fraction: 0, tone: .train, action: .strain)]
    let chips = JarvisChatView.contextChips(m)
    XCTAssertEqual(chips.map(\.icon), [.circleGauge, .moon], "recovery first; strain has no value today: no chip")
    XCTAssertTrue(chips[0].label.contains("82"))
    let once = JarvisChatView.adding(chips[0], to: "Q")
    XCTAssertTrue(once.hasPrefix("Q") && once.contains("82"))
    XCTAssertEqual(JarvisChatView.adding(chips[0], to: once), once, "added once")
  }

  func testQuickQuestionIcons() {
    XCTAssertEqual(JarvisChatView.icon(for: JarvisRouter.Quick(text: "x", photo: true)), .camera)
    XCTAssertEqual(JarvisChatView.icon(for: JarvisRouter.Quick(text: "zzz")), .messageCircle)
    for q in JarvisRouter.quick(for: "today") { _ = JarvisChatView.icon(for: q) }   // every list entry gets a mark
  }

  // MARK: - 16b 拍照结果

  func testAmountKeepsTheLogsOwnUnit() {
    let grams = FoodEntry(id: "1", date: "2026-11-12", time: nil, meal: .lunch, food: "鸡胸", quantity: 150, unit: "g", macros: Macros(kcal: 248))
    XCTAssertEqual(CockpitMealPhoto.amount(grams).value, "150")
    XCTAssertEqual(CockpitMealPhoto.amount(grams).unit, "g")
    let servings = FoodEntry(id: "2", date: "2026-11-12", time: nil, meal: .lunch, food: "米饭", quantity: 1.5, unit: nil, macros: Macros(kcal: 300))
    XCTAssertEqual(CockpitMealPhoto.amount(servings).value, "1.5")
    XCTAssertFalse(CockpitMealPhoto.amount(servings).unit.isEmpty, "no unit: servings, never made-up grams")
  }
}
