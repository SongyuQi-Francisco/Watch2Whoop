import SwiftUI
import XCTest
@testable import Watch2Whoop

/// Native Jarvis cards against `Fixtures/jarvis_cards.json`: one card of every server type (ai/cards.py), built by the real
/// server code from the Quick Start's synthetic data and trimmed, with hand-made diet cards in the builders' shapes.
///
/// `testEveryCardRenders` also writes contact sheets when `W2W_CARD_SHOTS` is set (run with
/// `TEST_RUNNER_W2W_CARD_SHOTS=<dir>`); `W2W_CARD_JSON` swaps in another dump of the same shape.
@MainActor
final class JarvisCardsTests: XCTestCase {
  /// cards.TYPES + TYPES_V2 + product.
  private static let serverTypes: Set<String> = [
    "training", "meal", "metric", "sleep", "actions", "muscle", "goals", "suggest", "recipe", "recipes", "templates", "shopping",
    "recovery", "strain", "stress", "healthspan", "trend", "weight", "lab", "supplements", "water", "journal", "report", "hr_zones",
    "vo2max", "monitor", "data_status", "product",
  ]
  private static let openPages = ["today", "trends", "record.training", "food", "sleep", "profile", "food.recipes", "food.shopping", "recipe",
                                  "healthspan", "lab", "trends.report", "supplements", "data_status"]
  private static let sendKeys = ["adjust_training", "next_meal", "explain_trend", "analyze_muscles", "more_meals", "log_recipe", "recipe_steps",
                                 "shopping_plan", "undo_meal", "explain_recovery", "explain_stress", "improve_body_age", "improve_vo2max",
                                 "explain_monitor", "explain_lab", "log_supplements", "explain_journal", "explain_report", "explain_sleep",
                                 "log_product"]

  private var savedLang: String?

  override func setUp() async throws {
    savedLang = Lang.override
    Lang.override = "zh"
  }

  override func tearDown() async throws {
    Lang.override = savedLang
  }

  private func fixture() throws -> [JSONValue] {
    let env = ProcessInfo.processInfo.environment["W2W_CARD_JSON"]
    let url = try env.map { URL(fileURLWithPath: $0) }
      ?? XCTUnwrap(Bundle(for: Self.self).url(forResource: "jarvis_cards", withExtension: "json"))
    return try XCTUnwrap(JSONValue.parse(Data(contentsOf: url))?.array)
  }

  private func card(_ type: String, _ all: [JSONValue]) throws -> JSONValue {
    try XCTUnwrap(all.first { $0["type"]?.string == type }, type)
  }

  private func json(_ s: String) -> JSONValue { JSONValue.parse(Data(s.utf8))! }

  // MARK: - coverage

  func testEveryServerTypeIsNativeAndInTheFixture() throws {
    XCTAssertEqual(JarvisCards.nativeTypes, Self.serverTypes)
    XCTAssertEqual(Set(try fixture().compactMap { $0["type"]?.string }), Self.serverTypes)
  }

  func testEveryTypeHasATitleAndEveryButtonALabel() throws {
    for t in Self.serverTypes where t != "actions" && t != "metric" {
      XCTAssertNotEqual(JarvisCards.title(t), tr("卡片", "Card"), t)
    }
    for p in Self.openPages { XCTAssertNotNil(JarvisCards.openLabel(p), p) }
    let c = json(#"{"name":"示例","id":1,"metric":"hrv","test_name":"示例","date":"2026-01-02"}"#)
    for k in Self.sendKeys { XCTAssertNotNil(JarvisCards.send(k, c), k) }
  }

  // MARK: - GenericCard

  func testGenericCardShowsOnlyTranslatedLabels() {
    let c = json(#"{"type":"future_card","date":"2026-01-02","score":41.5,"status":"in_range","level":"Low","raw_field_name":7,"other":"text","nested":{"a":1},"actions":[]}"#)
    let rows = GenericCard.rows(c)
    XCTAssertEqual(rows.map(\.0), [tr("分数", "Score"), tr("等级", "Level")])
    XCTAssertEqual(rows.map(\.1), ["41.5", "Low"])
    XCTAssertFalse(rows.contains { $0.0.contains("_") || $0.1.contains("_") })
    XCTAssertEqual(JarvisCards.title("future_card"), tr("卡片", "Card"))
  }

  func testMetricNameNeverABareKey() {
    XCTAssertEqual(JarvisCards.metricName("sleep_efficiency"), tr("睡眠效率", "Sleep efficiency"))
    let c = json(#"{"trend":{"series":{"ctl":{"label":"体能 CTL"}}}}"#)
    XCTAssertEqual(JarvisCards.metricName("ctl", card: c), "体能 CTL")
    XCTAssertEqual(JarvisCards.metricName("no_such_metric"), tr("这个指标", "this metric"))
    XCTAssertTrue(JarvisCards.send("explain_trend", json(#"{"metric":"no_such_metric"}"#))?.1.contains("no_such_metric") == false)
  }

  // MARK: - body cards

  func testRecoveryAndSleep() throws {
    let all = try fixture()
    XCTAssertEqual(try XCTUnwrap(JarvisCards.percent(0.51)), 51, accuracy: 0.001)
    XCTAssertNil(JarvisCards.percent(nil))
    let r = try XCTUnwrap(JarvisCards.trendsData(card("recovery", all)))
    XCTAssertNotNil(TrendCards.recovery(r)?.spec)
    let s = try card("sleep", all)
    XCTAssertEqual(JarvisCards.stages(try XCTUnwrap(s["main"])).count, 3)
    XCTAssertEqual(JarvisCards.stages(json("{}")).count, 0)
    XCTAssertEqual(JarvisCards.sleepPart("duration"), tr("时长", "Duration"))
    XCTAssertEqual(JarvisCards.sleepPart("nope"), tr("其他", "Other"))
  }

  func testStrain() throws {
    let c = try card("strain", fixture()), coach = try XCTUnwrap(c["coach"])
    XCTAssertEqual(StrainJarvisCard.state(json(#"{"status":"in_range"}"#)).1, .good)
    XCTAssertEqual(StrainJarvisCard.state(json(#"{"status":"above"}"#)).1, .mid)
    XCTAssertEqual(StrainJarvisCard.state(json(#"{"status":"below","current_strain":4,"remaining_to_low":6.5}"#)).0, tr("差 6.5", "6.5 to go"))
    XCTAssertEqual(StrainJarvisCard.state(json(#"{"status":null,"current_strain":null}"#)).0, "—")
    XCTAssertNotNil(coach["target_low"]?.double)
    let d = try XCTUnwrap(JarvisCards.trendsData(c))
    XCTAssertEqual(d.dates.count, 14)
    let m = try XCTUnwrap(JarvisCards.trendModel("strain", d))
    XCTAssertNotNil(m.spec)
    XCTAssertEqual(m.title, tr("负荷", "Strain"))
    XCTAssertFalse(m.legend.isEmpty)
  }

  func testStress() throws {
    let c = try card("stress", fixture()), d = try XCTUnwrap(c["days"]?[0])
    XCTAssertEqual(d["score"]?.double, 1.4)
    XCTAssertEqual(StressJarvisCard.level("medium")?.1, .mid)
    XCTAssertNil(StressJarvisCard.level(nil))
    XCTAssertEqual(StressJarvisCard.reason(json(#"{"no_motion_data":"x","no_raw_hr":"y"}"#)), tr("无心率", "No heart rate"))
    XCTAssertNil(StressJarvisCard.reason(json("{}")))
  }

  func testHealthspan() throws {
    let c = try card("healthspan", fixture())
    XCTAssertNotNil(c["body_age"]?.double)
    XCTAssertFalse((c["contributors"]?.array ?? []).isEmpty)
    XCTAssertEqual(HealthspanJarvisCard.signed(-2.96), "−3.0")
    XCTAssertEqual(HealthspanJarvisCard.signed(4), "+4.0")
    XCTAssertEqual(HealthspanJarvisCard.value(json(#"{"value":43.7,"unit":"mL/kg/min"}"#)), "43.7 mL/kg/min")
    XCTAssertEqual(HealthspanJarvisCard.value(json(#"{"key":"body_comp","measure":"bmi","value":22.46}"#)), "BMI 22.5")
    XCTAssertEqual(HealthspanJarvisCard.value(json(#"{"key":"activity","value":10675,"unit":"步"}"#)), "10,675 步")
    XCTAssertNil(HealthspanJarvisCard.value(json("{}")))
    XCTAssertNotEqual(HealthspanJarvisCard.category("nope"), "nope")
  }

  func testTrendWeightVO2() throws {
    let all = try fixture()
    let trend = try XCTUnwrap(all.first { $0["type"]?.string == "trend" && $0["metric"]?.string == "hrv" })
    let hrv = try XCTUnwrap(JarvisCards.trendModel("hrv", XCTUnwrap(JarvisCards.trendsData(trend))))
    XCTAssertEqual(hrv.title, "HRV")
    XCTAssertEqual(hrv.unit, "ms")
    XCTAssertNotNil(hrv.base)
    // a series the Trends tab has no card for: its own label, still a chart
    let ctl = try XCTUnwrap(all.first { $0["type"]?.string == "trend" && $0["metric"]?.string == "ctl" })
    let m = try XCTUnwrap(JarvisCards.trendModel("ctl", XCTUnwrap(JarvisCards.trendsData(ctl))))
    XCTAssertNotEqual(m.title, "ctl")
    XCTAssertNotNil(m.spec)
    let weight = try card("weight", all)
    XCTAssertEqual(JarvisCards.trendModel("weight", try XCTUnwrap(JarvisCards.trendsData(weight)))?.unit, "kg")
    let vo2 = try card("vo2max", all)
    XCTAssertEqual(VO2JarvisCard.level(vo2), vo2["level_zh"]?.string)
    Lang.override = "en"
    XCTAssertEqual(VO2JarvisCard.level(json(#"{"level":"good","level_zh":"良好"}"#)), "good")
  }

  func testHRZones() throws {
    let z = try XCTUnwrap(HRZonesJarvisCard.zones(card("hr_zones", fixture())))
    XCTAssertEqual(z.labels.count, 5)
    XCTAssertEqual(z.totalMin.count, 5)
    XCTAssertFalse(z.weeks.isEmpty)
    XCTAssertNil(HRZonesJarvisCard.zones(json(#"{"zones":null}"#)))
  }

  func testLab() throws {
    let c = try card("lab", fixture())
    let h = try XCTUnwrap(LabHistory.parse(c.any as? [String: Any]))
    XCTAssertEqual(h.key, "uric_acid")
    XCTAssertEqual(h.rows.count, 4)
    XCTAssertEqual(h.rows.last?.homeDevice, true)
    XCTAssertEqual(h.rows.last?.delta, -43)
    XCTAssertEqual(h.rows.last?.fasting, true)
    XCTAssertEqual(h.deltaTone(-43), .good)   // good_direction: down
  }

  func testReport() throws {
    let c = try card("report", fixture())
    let r = try XCTUnwrap((c.any as? [String: Any]).flatMap(ReportModel.init(json:)))
    XCTAssertFalse(r.tiles.isEmpty)
    XCTAssertEqual(r.findings.first?.diff, -6.4)
    XCTAssertNotNil(r.previous)
    XCTAssertEqual(r.tiles.first { $0.id == "stress" }?.tone, .good)   // lower is better, and it went down
  }

  func testMonitor() throws {
    let v = MonitorJarvisCard.vitals(try card("monitor", fixture()))
    XCTAssertEqual(v.count, 5)
    XCTAssertEqual(v.filter(\.off).count, 1)
    XCTAssertEqual(v.first?.name, tr("静息心率", "Resting HR"))   // the app's name, not the server's long label
    XCTAssertEqual(v.first?.explainKey, "resting_hr")
    // an item without a value is not off, whatever its status says
    XCTAssertFalse(MonitorJarvisCard.vitals(json(#"{"metrics":[{"key":"spo2","value":null,"status":"low"}]}"#))[0].off)
  }

  func testJournal() throws {
    let c = try card("journal", fixture())
    let b = BehaviorRow.parse((c["results"]?.any as? [[String: Any]]) ?? [])
    XCTAssertEqual(b.rows.map(\.diff), [-6.4, 3.1])   // largest effect first
    XCTAssertEqual(b.rows[0].tone, .bad)
    XCTAssertFalse(b.rows[1].reliable)
    XCTAssertEqual(b.lacking, 1)
  }

  func testDataStatus() throws {
    let c = try card("data_status", fixture())
    XCTAssertEqual(DataStatusJarvisCard.sorted(c).compactMap { $0["status"]?.string }, ["never", "stale", "ok", "ok"])
    XCTAssertEqual(DataStatusJarvisCard.state("stale").1, .mid)
    XCTAssertEqual(DataStatusJarvisCard.state("nope").0, "—")
  }

  // MARK: - muscle and diet cards

  func testMuscle() throws {
    let c = try card("muscle", fixture())
    let (m, range) = MuscleJarvisCard.load(c)
    XCTAssertEqual(range, .week)
    XCTAssertEqual(m.muscles.count, c["muscles"]?.array?.count)
    XCTAssertFalse(m.regions(range).isEmpty)
    XCTAssertTrue(MuscleJarvisCard.low(c).count < m.muscles.count)
    XCTAssertNotNil(MuscleBodies.shared.side(sex: m.sex, front: true))
    XCTAssertEqual(MuscleJarvisCard.load(json(#"{"range":"nope","muscles":[]}"#)).1, .week)
  }

  func testSuggestAndRows() throws {
    let all = try fixture(), c = try card("suggest", all)
    XCTAssertEqual(SuggestJarvisCard.slots(c).count, 2)
    XCTAssertTrue(SuggestJarvisCard.slots(json(#"{"slots":[{"slot":"lunch","logged":null,"items":[]}]}"#)).isEmpty)
    let r = try XCTUnwrap(c["slots"]?[1]?["items"]?[0])
    XCTAssertEqual(JarvisRows.recipe(r, slot: "dinner"), "按食谱「示例鸡胸饭」记一份晚餐（食谱 id 1）")
    XCTAssertEqual(JarvisRows.recipe(r, slot: nil), "按食谱「示例鸡胸饭」记一份（食谱 id 1）")
    XCTAssertEqual(JarvisRows.meta(r), "620 kcal 含饭 · 蛋白 45 g · 25′")
    XCTAssertEqual(JarvisRows.badges(r).map(\.1), [.good, .mid])
    let t = try XCTUnwrap(card("templates", all)["items"]?[1])
    XCTAssertEqual(JarvisRows.template(t), "按快捷模板「示例酸奶」记一次（模板 id 6）")
    XCTAssertEqual(JarvisRows.templateLine(t), "加餐 · 150 kcal · 蛋白 17 g")
    Lang.override = "en"
    XCTAssertEqual(JarvisRows.recipe(r, slot: "dinner"), "Log one serving of “示例鸡胸饭” as Dinner (recipe id 1)")
  }

  func testRecipeAndShopping() throws {
    let all = try fixture()
    XCTAssertEqual(RecipeJarvisCard.meta(try card("recipe", all)), "25′ · 4 步 · 每份 1 碗")
    let s = try card("shopping", all)
    XCTAssertEqual(ShoppingJarvisCard.ordered(s).map { $0["bought"]?.bool }, [false, false, true])
    XCTAssertEqual(ShoppingJarvisCard.cost(s), "≈ AUD 42.50")
  }

  // MARK: - where the buttons go

  func testRoutes() {
    let lab = json(#"{"test_key":"uric_acid","test_name":"尿酸"}"#)
    typealias R = JarvisRouter
    XCTAssertEqual(R.route(page: "lab", card: lab, circles: true), .detail(.lab(key: "uric_acid", name: "尿酸")))
    XCTAssertEqual(R.route(page: "supplements", card: nil, circles: true), .detail(.supplements))
    XCTAssertEqual(R.route(page: "data_status", card: nil, circles: true), .detail(.dataStatus))
    XCTAssertEqual(R.route(page: "profile", card: nil, circles: true), .detail(.profile))
    XCTAssertEqual(R.route(page: "sleep", card: json(#"{"date":"2026-01-02"}"#), circles: true), .detail(.sleep(date: "2026-01-02")))
    XCTAssertEqual(R.route(page: "explain.hrv", card: json(#"{"title":"HRV"}"#), circles: true), .detail(.explain(key: "hrv", title: "HRV")))
    XCTAssertEqual(R.route(page: "today", card: nil, circles: true), .tab(.today, nil))
    XCTAssertEqual(R.route(page: "trends", card: nil, circles: true), .tab(.trends, .trends(.metrics)))
    XCTAssertEqual(R.route(page: "healthspan", card: nil, circles: true), .detail(.bodyAge))
    XCTAssertEqual(R.route(page: "healthspan", card: nil, circles: false), .detail(.bodyAge))
    XCTAssertEqual(R.route(page: "trends.report", card: nil, circles: true), .tab(.trends, .trends(.report)))
    XCTAssertEqual(R.route(page: "record.training", card: nil, circles: true), .tab(.record, .training))
    // food is a segment of 记录 (2.0.1), with or without the friends flag
    XCTAssertEqual(R.route(page: "food", card: nil, circles: true), .tab(.record, .food(.diet)))
    XCTAssertEqual(R.route(page: "food", card: nil, circles: false), .tab(.record, .food(.diet)))
    XCTAssertEqual(R.route(page: "food.shopping", card: nil, circles: false), .tab(.record, .food(.shopping)))
    XCTAssertEqual(R.route(page: "food.recipes", card: nil, circles: true), .tab(.record, .food(.recipes)))
    XCTAssertEqual(R.route(page: "recipe", card: json(#"{"id":12}"#), circles: true), .tab(.record, .food(.recipe(id: "12"))))
  }

  func testPageLink() {
    let link = JarvisPageLink.shared
    link.ask(.food(.shopping), circles: true)
    XCTAssertEqual(link.segment, .food)
    XCTAssertEqual(link.food, .shopping)
    link.segment = nil
    link.food = nil
    link.ask(.food(.diet), circles: false)
    XCTAssertEqual(link.segment, .food)
    XCTAssertEqual(link.food, .diet)
    link.segment = nil
    link.food = nil
    link.ask(.training, circles: true)
    XCTAssertEqual(link.segment, .training)
    link.segment = nil
    link.ask(.trends(.report), circles: true)
    XCTAssertEqual(TrendsLink.shared.take(), TrendsLink.Request(end: nil, range: nil, mode: .report))
  }

  func testAnyKeepsTheShapeOfJSONSerialization() {
    let v = json(#"{"n":1.5,"b":true,"s":"x","null":null,"a":[1,2],"o":{"k":3}}"#)
    let o = v.any as? [String: Any]
    XCTAssertEqual(TodayModel.num(o?["n"]), 1.5)
    XCTAssertNil(TodayModel.num(o?["b"]))   // a boolean is not a number
    XCTAssertNil(TodayModel.num(o?["null"]))
    XCTAssertEqual((o?["a"] as? [Any])?.compactMap(TodayModel.num), [1, 2])
    XCTAssertEqual(TodayModel.num((o?["o"] as? [String: Any])?["k"]), 3)
  }

  // MARK: - drawing

  private func page(_ cards: [JSONValue], dark: Bool) -> some View {
    HStack(alignment: .top, spacing: 16) {
      ForEach(Array(stride(from: 0, to: cards.count, by: 2)), id: \.self) { i in
        VStack(alignment: .leading, spacing: 16) {
          ForEach(i..<min(i + 2, cards.count), id: \.self) { k in
            JarvisCardView(card: cards[k], send: { _ in }, open: { _, _ in }).frame(width: 358)
          }
        }
        .frame(width: 358, alignment: .topLeading)
      }
    }
    .padding(16)
    .background(Palette.sheet)
    .environment(\.colorScheme, dark ? .dark : .light)
  }

  /// Every card draws in both languages; with `W2W_CARD_SHOTS` the contact sheets are written there.
  func testEveryCardRenders() throws {
    let all = try fixture()
    let dir = ProcessInfo.processInfo.environment["W2W_CARD_SHOTS"]
    for lang in ["zh", "en"] {
      Lang.override = lang
      for c in all {
        let r = ImageRenderer(content: JarvisCardView(card: c, send: { _ in }, open: { _, _ in }).frame(width: 358))
        XCTAssertNotNil(r.uiImage, "\(lang) \(c["type"]?.string ?? "")")
      }
      guard let dir else { continue }
      try FileManager.default.createDirectory(atPath: dir, withIntermediateDirectories: true)
      let chunks = stride(from: 0, to: all.count, by: 6).map { Array(all[$0..<min($0 + 6, all.count)]) }
      for (n, chunk) in chunks.enumerated() {
        for dark in lang == "zh" ? [false, true] : [false] {
          let r = ImageRenderer(content: page(chunk, dark: dark))
          r.scale = 1.4
          let data = try XCTUnwrap(r.uiImage?.pngData())
          try data.write(to: URL(fileURLWithPath: "\(dir)/cards-\(lang)\(dark ? "-dark" : "")-\(n).png"))
        }
      }
    }
  }
}
