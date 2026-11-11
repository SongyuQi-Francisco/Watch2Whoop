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

  // MARK: - 18 补记

  private func client() -> ManualWorkoutAPI {
    ManualWorkoutAPI(api: APIClient(baseURL: URL(string: "https://body.example.com")!, token: { "example-token" },
                                    session: StubProtocol.session(), sleep: { _ in }))
  }

  private func sent(_ r: URLRequest) throws -> [String: Any] {
    let data = try XCTUnwrap(r.httpBodyStream.map(DeltaRemoteTests.read) ?? r.httpBody)
    return try XCTUnwrap(JSONSerialization.jsonObject(with: data) as? [String: Any])
  }

  func testBackfillOptions() async throws {
    StubProtocol.reset([StubProtocol.Reply(status: 200, body: """
      {"types":[{"type":"basketball","name":"篮球","name_en":"Basketball"},{"type":"swim","name":"游泳","name_en":"Swimming"}],
       "intensities":[{"key":"light","label":"轻"},{"key":"moderate","label":"中"},{"key":"hard","label":"强"}],
       "max_duration_min":600,"max_past_days":7}
      """), StubProtocol.Reply(status: 404, body: "{\"detail\":\"Not Found\"}")])
    let o = try await XCTUnwrap(client().options())
    XCTAssertEqual(o.types.map(\.type), ["basketball", "swim"])
    XCTAssertEqual(o.intensities.map(\.key), ["light", "moderate", "hard"])
    XCTAssertEqual(o.maxPastDays, 7)
    XCTAssertTrue(StubProtocol.requests[0].url!.path.hasSuffix("/training/manual-workouts/options"))
    let missing = try await client().options()
    XCTAssertNil(missing, "no endpoint yet: the key keeps its old way")
  }

  func testBackfillRequestBody() throws {
    let untimed = ManualWorkoutAPI.Entry(type: "basketball", minutes: 60, intensity: "moderate", date: "2026-11-11", time: nil, clientId: "k1")
    XCTAssertEqual(untimed.body["date"] as? String, "2026-11-11")
    XCTAssertNil(untimed.body["start"])
    XCTAssertEqual(untimed.body["duration_min"] as? Double, 60)
    XCTAssertEqual(untimed.body["client_id"] as? String, "k1")
    let timed = ManualWorkoutAPI.Entry(type: "swim", minutes: 30, intensity: "hard", date: "2026-11-10", time: "19:10", clientId: "k2")
    XCTAssertEqual(timed.body["start"] as? String, "2026-11-10T19:10:00")
    XCTAssertNil(timed.body["date"])
  }

  func testBackfillRetrySameKey() async throws {
    let ok = StubProtocol.Reply(status: 200, body: """
      {"created":true,"workout":{"id":77,"name":"篮球","date":"2026-11-11"},"duplicate_of":null}
      """)
    StubProtocol.reset([StubProtocol.Reply(status: 503), ok])
    let e = ManualWorkoutAPI.Entry(type: "basketball", minutes: 60, intensity: "moderate", date: "2026-11-11", time: nil, clientId: "same-key")
    let out = try await client().add(e)
    XCTAssertEqual(out, ManualWorkoutAPI.Outcome(workoutId: 77, name: "篮球", date: "2026-11-11", duplicate: nil))
    XCTAssertEqual(StubProtocol.requests.count, 2, "retried after the 503")
    XCTAssertEqual(try sent(StubProtocol.requests[0])["client_id"] as? String, "same-key")
    XCTAssertEqual(try sent(StubProtocol.requests[1])["client_id"] as? String, "same-key", "the retry is the same record")
    XCTAssertEqual(StubProtocol.requests[1].httpMethod, "POST")
  }

  func testBackfillDuplicateOfTheWatch() async throws {
    StubProtocol.reset([StubProtocol.Reply(status: 200, body: """
      {"created":true,"workout":{"id":78,"name":"篮球","date":"2026-11-11"},
       "duplicate_of":{"id":12,"source":"apple","name":"篮球","date":"2026-11-11","duration_min":62}}
      """)])
    let e = ManualWorkoutAPI.Entry(type: "basketball", minutes: 60, intensity: "moderate", date: "2026-11-11", time: nil, clientId: "k3")
    let out = try await client().add(e)
    XCTAssertEqual(out.workoutId, 78)
    XCTAssertEqual(out.duplicate, "篮球", "the card says the watch has it")
  }

  func testBackfillUndoDeletesThatRecord() async throws {
    StubProtocol.reset([StubProtocol.Reply(status: 200, body: "{\"deleted\":true}")])
    try await client().undo(78)
    XCTAssertEqual(StubProtocol.requests[0].httpMethod, "DELETE")
    XCTAssertTrue(StubProtocol.requests[0].url!.path.hasSuffix("/training/manual-workouts/78"))
  }

  func testBackfillDays() {
    let d = CockpitBackfillSheet.days(today: "2026-11-12", back: 3)
    XCTAssertEqual(d, ["2026-11-09", "2026-11-10", "2026-11-11", "2026-11-12"])
  }
}
