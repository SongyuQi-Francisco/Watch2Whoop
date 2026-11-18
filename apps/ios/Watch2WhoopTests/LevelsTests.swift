import XCTest
@testable import Watch2Whoop

/// 2.1.0 levels of 今天 and 身体 (docs/design/2.0.1/drilldown option C′): where back and the crumb lead, which metric a
/// page is about, and the numbers of 今日详情's rows and of a metric page. Every number is made up.
final class LevelsTests: XCTestCase {
  override func setUp() {
    super.setUp()
    Lang.override = "zh"
  }

  override func tearDown() {
    Lang.override = nil
    super.tearDown()
  }

  // MARK: - navigation

  func testLevelsOfAPath() {
    XCTAssertEqual(LevelNav.level([]), 1)
    XCTAssertEqual(LevelNav.level([.day(anchor: nil)]), 2)
    XCTAssertEqual(LevelNav.level([.all(.metrics)]), 2)
    XCTAssertEqual(LevelNav.level([.day(anchor: nil), .metric("hrv")]), 3)
    XCTAssertEqual(LevelNav.level([.metric("hrv")]), 3)
    XCTAssertEqual(LevelNav.level([.metric("sleep_score"), .night(nil)]), 3)
  }

  /// Back is the system's (one step), so a metric opened straight from the first glance returns to it; the crumb goes up
  /// one level: from that metric to 今日详情, not back to the first glance (owner's answer 2, 2026-11-19).
  func testTheCrumbGoesUpOneLevel() {
    let day = LevelRoute.day(anchor: nil)
    XCTAssertEqual(LevelNav.up([], second: day), [])
    XCTAssertEqual(LevelNav.up([day], second: day), [])
    XCTAssertEqual(LevelNav.up([.metric("hrv")], second: day), [day])
    XCTAssertEqual(LevelNav.up([day, .metric("hrv")], second: day), [day])
    XCTAssertEqual(LevelNav.up([day, .metric("hrv"), .metric("recovery")], second: day), [day])   // through 相关
    XCTAssertEqual(LevelNav.up([.metric("hrv"), .metric("recovery")], second: day), [day])
    XCTAssertEqual(LevelNav.up([.day(anchor: "attention"), .night(nil)], second: day), [.day(anchor: "attention")])
    // 身体: its own level 2 in the mode last used
    XCTAssertEqual(LevelNav.up([.metric("hrv")], second: .all(.sleep)), [.all(.sleep)])
    XCTAssertEqual(LevelNav.up([.all(.report), .metric("hrv")], second: .all(.metrics)), [.all(.report)])
  }

  func testTheLadderOpensLevelTwoOnlyFromTheFirstGlance() {
    XCTAssertEqual(LevelNav.down([], second: .day(anchor: nil)), [.day(anchor: nil)])
    XCTAssertEqual(LevelNav.down([.metric("hrv")], second: .day(anchor: nil)), [.metric("hrv")])
  }

  func testASwipeUpIsFarAndMostlyVertical() {
    XCTAssertTrue(LevelNav.isSwipeUp(0, -120))
    XCTAssertTrue(LevelNav.isSwipeUp(30, -100))
    XCTAssertFalse(LevelNav.isSwipeUp(0, -60))     // too short (a scroll bounce)
    XCTAssertFalse(LevelNav.isSwipeUp(90, -100))   // a diagonal: the day swipe's
    XCTAssertFalse(LevelNav.isSwipeUp(0, 140))     // down = pull to refresh
  }

  // MARK: - which metric a page is about

  func testCatalog() {
    XCTAssertEqual(MetricCatalog.head("recovery"), .recovery)
    XCTAssertEqual(MetricCatalog.head("sleep_score"), .sleep)
    XCTAssertEqual(MetricCatalog.head("sleep_hours"), .sleep)
    XCTAssertEqual(MetricCatalog.head("strain"), .strain)
    XCTAssertEqual(MetricCatalog.head("stress"), .stress)
    XCTAssertEqual(MetricCatalog.head("body_age"), .bodyAge)
    XCTAssertEqual(MetricCatalog.head("hrv"), .plain)
    // a day row and its range card, a range card and its series
    XCTAssertEqual(MetricCatalog.card("tsb"), "training_load")
    XCTAssertEqual(MetricCatalog.card("muscle_load"), "vol")
    XCTAssertEqual(MetricCatalog.series("training_load"), "tsb")
    XCTAssertEqual(MetricCatalog.series("azm"), "aerobic_min")
    XCTAssertNil(MetricCatalog.series("hr_zones"))
    XCTAssertEqual(MetricCatalog.title("hrv"), "HRV")
    XCTAssertEqual(MetricCatalog.title("hr_zones"), "心率区间分布")
    XCTAssertEqual(MetricCatalog.explain("sleep_hours"), "sleep_need")
    XCTAssertTrue(MetricCatalog.drawsLine("hrv"))
    XCTAssertFalse(MetricCatalog.drawsLine("steps"))      // bars: the range card's own chart
    XCTAssertFalse(MetricCatalog.drawsLine("recovery"))   // bars in recovery colours
    XCTAssertEqual(MetricCatalog.group("tsb"), "strain")
    XCTAssertEqual(MetricCatalog.group("weight"), "long")
  }

  /// 相关: the nearest metrics of the same category (the mockup's HRV page: 恢复, 静息心率, 呼吸频率).
  func testRelatedAreTheNearestOfTheSameCategory() {
    XCTAssertEqual(MetricCatalog.related("hrv"), ["recovery", "resting_hr", "resp_rate"])
    XCTAssertEqual(MetricCatalog.related("recovery"), ["hrv", "resting_hr", "resp_rate"])
    XCTAssertEqual(MetricCatalog.related("body_age"), ["weight", "vo2max"])
    XCTAssertEqual(MetricCatalog.related("stress"), ["resilience"])
    XCTAssertEqual(MetricCatalog.related("training_load"), ["strain", "acwr", "strain_combined"])
    XCTAssertEqual(MetricCatalog.related("hr_zones"), [])
  }

  // MARK: - 今日详情's rows

  private func day() -> TrendsData {
    TodayCoreTests.page(TodayCoreTests.doc(series: [
      "recovery": TodayCoreTests.series(TodayCoreTests.steady(60, spread: 5, day: 70), unit: "%"),
      "hrv": TodayCoreTests.series(TodayCoreTests.steady(60, spread: 4, day: 68)),
      "resting_hr": TodayCoreTests.series(TodayCoreTests.steady(52, spread: 1, day: 52)),
      "sleep_hours": TodayCoreTests.series(TodayCoreTests.steady(7, spread: 0.2, day: 7.6)),
      "weight": TodayCoreTests.series((0..<61).map { $0 == 40 ? 71.4 as Any : NSNull() }),
      // optional, nothing in the last week: no row
      "daylight": TodayCoreTests.series((0..<61).map { $0 < 20 ? 30.0 as Any : NSNull() }),
    ]))
  }

  /// 2.0.0's groups and order; each row against the usual.
  func testDayRows() throws {
    let groups = LevelRows.day(day(), overrides: ["recovery": 82])
    XCTAssertEqual(groups.map(\.id), ["recovery", "sleep", "strain", "stress", "long"])
    let rec = try XCTUnwrap(groups.first?.rows.first { $0.id == "recovery" })
    XCTAssertEqual(rec.value, "82")              // the dial's own number
    XCTAssertEqual(rec.unit, "%")
    XCTAssertEqual(rec.spark.count, LevelParams.rowSparkDays)
    XCTAssertEqual(rec.spark.last, 82)
    let hrv = try XCTUnwrap(groups[0].rows.first { $0.id == "hrv" })
    XCTAssertEqual(hrv.value, "68")
    XCTAssertEqual(hrv.delta, "+8")              // against the median of the 30 days before
    XCTAssertEqual(hrv.deltaTone, .good)         // above the usual, higher is better
    XCTAssertEqual(hrv.usual, 56...64)
    let rhr = try XCTUnwrap(groups[0].rows.first { $0.id == "resting_hr" })
    XCTAssertEqual(rhr.deltaTone, .text)         // within the usual
    let sleep = try XCTUnwrap(groups[1].rows.first { $0.id == "sleep_hours" })
    XCTAssertEqual(sleep.value, "7:36")
    XCTAssertEqual(sleep.delta, "+0:36")         // hours as h:mm
    XCTAssertNil(sleep.unit)
    // a latest-only metric: its latest reading, no delta (it is not the day's)
    let weight = try XCTUnwrap(groups.last?.rows.first { $0.id == "weight" })
    XCTAssertEqual(weight.value, "71.4")
    XCTAssertNil(weight.delta)
    XCTAssertFalse(groups.flatMap(\.rows).contains { $0.id == "daylight" })
    // a metric with no series is still a row (its page says why), an optional one is not
    XCTAssertEqual(groups.flatMap(\.rows).first { $0.id == "spo2" }?.value, nil)
    XCTAssertNotNil(groups.flatMap(\.rows).first { $0.id == "spo2" })
  }

  func testTheMetricHeadAndTheRangeFigures() throws {
    let h = try XCTUnwrap(MetricHeadModel.of("hrv", day: day()))
    XCTAssertEqual(h.value, "68")
    XCTAssertEqual(h.unit, "ms")
    XCTAssertEqual(h.delta, "+8")
    XCTAssertEqual(h.usualText, "56–64")
    XCTAssertNil(MetricHeadModel.of("hr_zones", day: day()))   // a card drawn from several series: the card's own head
    XCTAssertEqual(MetricHeadModel.of("recovery", day: day(), override: 82)?.value, "82")

    let range = TodayCoreTests.page([
      "start": "2026-03-02", "end": "2026-03-31", "days": 30, "dates": (0..<30).map { LocalDay.add("2026-03-31", $0 - 29) },
      "series": ["hrv": ["values": (0..<30).map { 50.0 + Double($0 % 7) }, "avg7": [], "n": 30, "mean": 53.0, "prev_mean": 50.0]],
    ] as [String: Any])
    let s = try XCTUnwrap(RangeStatsModel.of("hrv", range: range))
    XCTAssertEqual(s.mean, "53")
    XCTAssertEqual(s.high, "56")
    XCTAssertEqual(s.low, "50")
    XCTAssertEqual(s.vsPrev, "+3")
    XCTAssertEqual(s.vsTone, .good)
    XCTAssertNil(RangeStatsModel.of("steps", range: range))   // no reading in the range: no figures
  }
}
