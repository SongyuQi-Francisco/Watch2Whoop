import XCTest
@testable import Watch2Whoop

/// #357: Trends' 「今天」 rows, the range cards' 「N 天均」 basis, the Today tab's 需要注意 / 今日核心 and the 「全部指标 →」
/// link. Every number is made up.
final class TodayCoreTests: XCTestCase {
  override func setUp() {
    super.setUp()
    Lang.override = "zh"
  }

  override func tearDown() {
    Lang.override = nil
    super.tearDown()
  }

  // MARK: - a synthetic 61-day /trends document ending on 2026-03-31

  static let end = "2026-03-31"
  static var dates: [String] { (0..<61).map { LocalDay.add(end, $0 - 60) } }

  /// 60 baseline days alternating `mean ± spread`, then the day's value.
  static func steady(_ mean: Double, spread: Double, day: Double?) -> [Any] {
    (0..<60).map { mean + ($0 % 2 == 0 ? spread : -spread) as Any } + [day as Any? ?? NSNull()]
  }

  static func series(_ values: [Any], band: (lo: Double, hi: Double, mean: Double)? = nil, unit: String = "") -> [String: Any] {
    let present = values.compactMap { $0 as? Double }
    var s: [String: Any] = ["label": "x", "unit": unit, "group": "g", "values": values,
                            "avg7": Array(repeating: NSNull(), count: values.count) as [Any],
                            "n": present.count, "mean": present.isEmpty ? NSNull() as Any : present.reduce(0, +) / Double(present.count) as Any, "prev_n": 0]
    if let b = band {
      s["baseline"] = ["lo": Array(repeating: b.lo, count: values.count), "hi": Array(repeating: b.hi, count: values.count),
                       "mean": Array(repeating: b.mean, count: values.count)]
    }
    return s
  }

  static func doc(series: [String: Any], status: [String: Any] = [:], end: String = TodayCoreTests.end) -> [String: Any] {
    let dates = (0..<61).map { LocalDay.add(end, $0 - 60) }
    return ["start": dates[0], "end": end, "days": 61, "prev_start": LocalDay.add(dates[0], -61), "dates": dates,
            "series": series, "status": status]
  }

  static func page(_ json: [String: Any]) -> TrendsData { TrendsData(json: json)! }

  // MARK: - DayBaseline and the 「今天」 rows

  func testBaselinePrefersTheServersBandElseThe60DayWindow() throws {
    let d = Self.page(Self.doc(series: [
      "hrv": Self.series(Self.steady(50, spread: 4, day: 41), band: (lo: 44, hi: 56, mean: 50)),
      "steps": Self.series(Self.steady(8000, spread: 1000, day: 8100)),
    ]))
    let hrv = try XCTUnwrap(DayBaseline.of(d.series["hrv"]!, at: 60))
    XCTAssertEqual(hrv, DayBaseline(mean: 50, sd: 6, fromBand: true))
    XCTAssertEqual(hrv.z(41)!, -1.5, accuracy: 1e-9)
    let steps = try XCTUnwrap(DayBaseline.of(d.series["steps"]!, at: 60))
    XCTAssertFalse(steps.fromBand)
    XCTAssertEqual(steps.mean, 8000, accuracy: 1e-9)
    XCTAssertEqual(steps.direction(8100), 0)   // 0.1 SD: flat
    XCTAssertEqual(steps.direction(9000), 1)
    // fewer than 14 readings in the window: no baseline
    var few: [Any] = Array(repeating: NSNull() as Any, count: 50) + (0..<10).map { Double($0) as Any }
    few.append(5.0)
    XCTAssertNil(DayBaseline.of(Self.page(Self.doc(series: ["steps": Self.series(few)])).series["steps"]!, at: 60))
  }

  func testTodayRowsShowTheDayAgainstTheBaseline() throws {
    var weight: [Any] = Array(repeating: NSNull(), count: 61)
    weight[58] = 80.4
    let d = Self.page(Self.doc(series: [
      "hrv": Self.series(Self.steady(50, spread: 4, day: 41), band: (lo: 44, hi: 56, mean: 50), unit: "ms"),
      "resting_hr": Self.series(Self.steady(55, spread: 2, day: 55.4), band: (lo: 53, hi: 57, mean: 55)),
      "sleep_hours": Self.series(Self.steady(7, spread: 0.5, day: 7.5)),
      "weight": Self.series(weight),
      "daylight": Self.series(Array(repeating: NSNull(), count: 61)),
    ]))
    let groups = TrendDay.groups(d)
    XCTAssertEqual(groups.map(\.id), ["recovery", "sleep", "strain", "stress", "long"])
    let rows = groups.flatMap(\.rows)
    // HRV below its band: down arrow, bad (higher is better), the baseline mean next to it
    let hrv = try XCTUnwrap(rows.first { $0.id == "hrv" })
    XCTAssertEqual(hrv.value, "41")
    XCTAssertEqual(hrv.unit, "ms")
    XCTAssertEqual(hrv.arrow, "↓")
    XCTAssertEqual(hrv.tone, .bad)
    XCTAssertEqual(hrv.base, "基线 50")
    XCTAssertEqual(hrv.spark.count, TodayParams.sparkDays)
    // resting HR within half an SD: flat, text colour
    let rhr = try XCTUnwrap(rows.first { $0.id == "resting_hr" })
    XCTAssertEqual(rhr.arrow, "→")
    XCTAssertEqual(rhr.tone, .text)
    // sleep in h:mm, above the phone-side 60-day mean: good
    let sleep = try XCTUnwrap(rows.first { $0.id == "sleep_hours" })
    XCTAssertEqual(sleep.value, "7:30")
    XCTAssertNil(sleep.unit)
    XCTAssertEqual(sleep.arrow, "↑")
    XCTAssertEqual(sleep.tone, .good)
    // weight: the latest reading with its own date, no direction
    let w = try XCTUnwrap(rows.first { $0.id == "weight" })
    XCTAssertEqual(w.value, "80.4")
    XCTAssertEqual(w.date, Self.dates[58])
    XCTAssertNil(w.arrow)
    // a core metric without a series: a dash row; an optional one without a reading this week: hidden
    XCTAssertEqual(rows.first { $0.id == "spo2" }?.value, nil)
    XCTAssertNotNil(rows.first { $0.id == "spo2" })
    XCTAssertNil(rows.first { $0.id == "daylight" })
    XCTAssertNil(rows.first { $0.id == "recovery_index" })
  }

  // MARK: - range cards: 「N 天均」 and both figures in the sheet

  func testRangeCardsSayWhatTheHeadlineIs() throws {
    let page = TrendsPage(data: try XCTUnwrap(TrendsData(json: TrendsTests.trendsJSON)), local: nil, vo2: nil, sleep: nil)
    let cards = TrendCards.groups(page).flatMap(\.cards)
    let hrv = try XCTUnwrap(cards.first { $0.id == "hrv" })
    XCTAssertEqual(hrv.value, "49")
    XCTAssertEqual(hrv.basis, "7 天均")
    XCTAssertEqual(hrv.figures, RangeFigures(meanLabel: "7 天均", mean: "49", dayLabel: "3/31 当天", day: "49", unit: "ms"))
    XCTAssertEqual(cards.first { $0.id == "recovery" }?.basis, "7 天均")
    XCTAssertEqual(cards.first { $0.id == "strain" }?.figures?.day, "9")
    // latest-only metrics keep 「最新」, and the sheet names the latest reading's day
    let vo2 = try XCTUnwrap(cards.first { $0.id == "vo2max" })
    XCTAssertEqual(vo2.basis, "最新")
    XCTAssertEqual(vo2.figures?.dayLabel, "最新 3/30")
    XCTAssertEqual(vo2.figures?.day, "44.6")
    // a card without data says nothing about a basis
    XCTAssertNil(cards.first { $0.id == "resting_hr" }?.basis)

    Lang.override = "en"
    XCTAssertEqual(TrendCards.meanBasis(page.data), "7-day mean")
    XCTAssertEqual(TrendRange.allCases.map(\.label), ["Today", "7 d", "30 d", "90 d", "365 d"])
    XCTAssertEqual(TrendRange.today.docDays, TodayParams.trendsDays)
    XCTAssertEqual(TrendRange.week.docDays, 7)
  }

  // MARK: - 需要注意

  static func vital(_ key: String, _ v: Double, low: Double, high: Double) -> TodayModel.Vital {
    TodayModel.Vital(id: key, name: key, short: key, value: v, unit: nil, low: low, high: high, off: v < low || v > high, missing: nil,
                     explainKey: ["rhr": "resting_hr", "hrv": "hrv"][key])
  }

  func testAttentionPicksTheWorstThreeAndHidesWhenAllIsWell() throws {
    let status: [String: Any] = [
      "illness": ["level": "watch", "label": "留意", "score": 31],
      "hrv_status": ["status": "poor", "label": "差", "avg7_ms": 38, "band_ms": [44, 56], "nights": 7],
      "training_status": ["status": "productive", "label": "有效", "acwr": 1.1],
      "body_battery": ["current": 45, "wake": 70, "low": 40],
    ]
    let doc = Self.doc(series: [
      "hrv": Self.series(Self.steady(50, spread: 4, day: 38), band: (lo: 44, hi: 56, mean: 50), unit: "ms"),   // z −2
      "resting_hr": Self.series(Self.steady(55, spread: 2, day: 58.2), band: (lo: 53, hi: 57, mean: 55)),      // z +1.6
      "spo2": Self.series(Self.steady(97, spread: 0.2, day: 96.5), band: (lo: 96.8, hi: 97.2, mean: 97)),     // z −2.5 but < 1.5 points
      "wrist_temp": Self.series(Self.steady(0, spread: 0.1, day: 0.6)),                                      // ≥ +0.5 °C
    ], status: status)
    let t = try XCTUnwrap(TodayTrends(json: doc, local: nil, day: Self.end))
    let all = TodayAttentionRules.select(trends: t, vitals: [Self.vital("resp_rate", 19.5, low: 13, high: 18)])
    XCTAssertEqual(all.count, TodayParams.maxItems)
    // hrv_status poor (red, 3.0) > hrv z −2 > monitor 2.0 = wrist +0.6 °C (1.8)… ; illness watch (1.6) and RHR (1.6) drop out
    XCTAssertEqual(all.map(\.id), ["hrv_status", "hrv", "resp_rate"])
    let hrv = all[1]
    XCTAssertEqual(hrv.value, "38")
    XCTAssertEqual(hrv.arrow, "↓")
    XCTAssertEqual(hrv.base, "基线 50")
    XCTAssertEqual(hrv.open, .metric(key: "hrv", title: "HRV"))
    XCTAssertEqual(all[2].base, "13–18")
    XCTAssertEqual(all[2].arrow, "↑")
    if case .status(let tile) = all[0].open { XCTAssertEqual(tile.key, "hrv_status") } else { XCTFail("status sheet") }

    // every rule on its own
    let metrics = TodayParams.watch.compactMap { TodayAttentionRules.metric($0, t.data) }
    XCTAssertEqual(metrics.map(\.id), ["hrv", "resting_hr", "wrist_temp"])   // SpO₂: within its 1.5-point floor
    XCTAssertEqual(metrics.last?.value, "+0.6")
    XCTAssertEqual(metrics.last!.severity, 1.8, accuracy: 1e-9)
    // training status productive and body battery 45 are ordinary days
    XCTAssertEqual(t.status.tiles.compactMap(TodayAttentionRules.status).map(\.id), ["hrv_status", "illness"])

    // a vital that is off its range and off its baseline is one item
    let dup = TodayAttentionRules.select(trends: t, vitals: [Self.vital("hrv", 38, low: 42, high: 70)])
    XCTAssertEqual(dup.filter { $0.id == "hrv" }.count, 1)

    // nothing off: no items (the block is hidden)
    let calm = Self.doc(series: ["hrv": Self.series(Self.steady(50, spread: 4, day: 51), band: (lo: 44, hi: 56, mean: 50))],
                        status: ["illness": ["level": "low", "label": "低", "score": 5]])
    XCTAssertEqual(TodayAttentionRules.select(trends: TodayTrends(json: calm, local: nil, day: Self.end),
                                              vitals: [Self.vital("rhr", 55, low: 50, high: 60)]), [])
    XCTAssertEqual(TodayAttentionRules.select(trends: nil, vitals: []), [])
  }

  func testThePhonesStatusCardsReplaceTheServers() throws {
    let doc = Self.doc(series: [:], status: ["illness": ["level": "low", "label": "低", "score": 5]])
    let local: [String: Any] = ["status": ["illness": ["level": "high", "label": "高", "score": 72]], "keys": ["illness"], "bundle_version": "t"]
    let t = try XCTUnwrap(TodayTrends(json: doc, local: local, day: Self.end))
    let items = TodayAttentionRules.select(trends: t, vitals: [])
    XCTAssertEqual(items.map(\.id), ["illness"])
    XCTAssertEqual(items.first?.tone, .bad)
    XCTAssertEqual(items.first?.base, "72")
    // a cached document of another day describes nothing about this one
    XCTAssertNil(TodayTrends(json: doc, local: nil, day: "2026-04-01"))
  }

  // MARK: - 今日核心

  func testCoreCardsInOrderAndHiddenWithoutData() throws {
    var rec = Self.series(Self.steady(60, spread: 5, day: 70)), str = Self.series(Self.steady(10, spread: 2, day: 12))
    rec["avg7"] = Array(repeating: NSNull(), count: 60) + [64.4]
    str["avg7"] = Array(repeating: NSNull(), count: 60) + [11.26]
    let doc = Self.doc(series: ["recovery": rec, "strain": str, "stress": Self.series(Self.steady(1, spread: 0.2, day: 2.3))],
                       status: ["illness": ["level": "watch", "label": "留意", "score": 31],
                                "training_status": ["status": "productive", "label": "有效", "acwr": 1.1],
                                "body_battery": ["current": 45, "wake": 70, "low": 40]])
    let coach = TodayModel.Coach(low: 10, high: 14, current: 6.5, stateLabel: "差 3.5", tone: .text)
    let cards = TodayCoreCards.build(coach: coach, trends: TodayTrends(json: doc, local: nil, day: Self.end))
    XCTAssertEqual(cards.map(\.id), ["coach", "recovery_vs_strain", "stress", "illness", "body_battery", "training_status"])
    XCTAssertEqual(cards[0].value, "6.5")
    XCTAssertEqual(cards[0].unit, "/ 10–14")
    XCTAssertEqual(cards[1].value, "64")
    XCTAssertEqual(cards[1].unit, "/ 11.3")
    XCTAssertEqual(cards[2].value, "2.3")
    XCTAssertEqual(cards[2].tone, .bad)
    XCTAssertEqual(cards[3].tone, .mid)   // same colour as the Trends status card
    XCTAssertEqual(cards[4].tone, .mid)
    // no trends document: only the strain target; nothing at all: no cards
    XCTAssertEqual(TodayCoreCards.build(coach: coach, trends: nil).map(\.id), ["coach"])
    XCTAssertEqual(TodayCoreCards.build(coach: nil, trends: nil), [])
  }

  func testTodayModelReadsTheDaysTrendsDocument() throws {
    let today: [String: Any] = ["date": Self.end, "scores": ["today": ["recovery": 40], "yesterday": [:]]]
    let doc = Self.doc(series: ["hrv": Self.series(Self.steady(50, spread: 4, day: 38), band: (lo: 44, hi: 56, mean: 50))],
                       status: ["illness": ["level": "watch", "label": "留意", "score": 31]])
    let m = TodayModel(TodayInputs(today: today, coach: ["target_low": 10, "target_high": 14, "current_strain": 4],
                                   userToday: Self.end, trends: doc))
    XCTAssertEqual(m.attention.map(\.id), ["hrv", "illness"])
    XCTAssertEqual(m.core.map(\.id), ["coach", "illness"])
    // a look-back day whose document is not cached yet: no stale blocks
    let past = TodayModel(TodayInputs(today: ["date": "2026-03-20", "scores": [String: Any]()], userToday: Self.end, trends: doc))
    XCTAssertEqual(past.attention, [])
    XCTAssertEqual(past.core, [])
  }

  // MARK: - storage and the 「全部指标 →」 link

  func testTodayReadsAndPullsTheSharedTrendsDocument() throws {
    let db = try AppDatabase.inMemory()
    try db.saveDoc(DocKey.todayCurrent, json: Data(#"{"date": "2026-03-31", "scores": {}}"#.utf8))
    try db.saveDoc("trends:61", json: Data(#"{"end": "2026-03-31", "dates": [], "series": {}}"#.utf8))
    try db.saveDoc(TrendsLocal.docKey("2026-03-31"), json: Data(#"{"status": {}, "keys": []}"#.utf8))
    let s = try db.writer.read { try TodaySnapshot.read($0, date: nil) }
    XCTAssertNotNil(s.docs["trends:61"])
    XCTAssertNotNil(s.docs["trends.local:2026-03-31"])
    let today = TodayDocs.fetches(day: "2026-03-31", isToday: true, circles: false).map(\.key)
    XCTAssertTrue(today.contains("trends:61"))
    let past = TodayDocs.fetches(day: "2026-03-20", isToday: false, circles: false).map(\.key)
    XCTAssertTrue(past.contains("trends:61:2026-03-20"))
  }

  @MainActor
  func testAllMetricsLinkOpensTrendsTodayOnTheSameDay() {
    let link = TrendsLink()
    link.open(day: "2026-03-20")
    XCTAssertEqual(link.pending, TrendsLink.Request(end: "2026-03-20", range: .today, mode: .metrics))
    let r = link.take()
    XCTAssertNil(link.pending)
    XCTAssertNil(link.take())

    let saved = TrendsStore.sessionRange
    defer { TrendsStore.sessionRange = saved }
    TrendsStore.sessionRange = .quarter
    let store = TrendsStore()
    XCTAssertEqual(store.range, .quarter)   // kept while the app runs
    store.apply(r!)
    XCTAssertEqual(store.range, .today)
    XCTAssertEqual(store.end, "2026-03-20")
    XCTAssertEqual(TrendsStore.sessionRange, .today)
    // from today's Today: no end date (the user's today)
    link.open(day: nil)
    store.apply(link.take()!)
    XCTAssertNil(store.end)
  }
}
