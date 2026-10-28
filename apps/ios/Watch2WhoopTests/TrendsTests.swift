import XCTest
@testable import Watch2Whoop

/// N4: the Trends tab's parsing, the status cards computed on the phone, charts and reports. Every number is made up.
final class TrendsTests: XCTestCase {
  override func setUp() {
    super.setUp()
    Lang.override = "zh"
  }

  override func tearDown() {
    Lang.override = nil
    super.tearDown()
  }

  // MARK: - /trends document

  static let trendsJSON: [String: Any] = [
    "start": "2026-03-25", "end": "2026-03-31", "days": 7, "prev_start": "2026-03-18",
    "dates": ["2026-03-25", "2026-03-26", "2026-03-27", "2026-03-28", "2026-03-29", "2026-03-30", "2026-03-31"],
    "series": [
      "recovery": ["label": "Recovery", "unit": "", "group": "recovery", "values": [70, 40, NSNull(), 20, 66, 80, 55],
                   "avg7": [NSNull(), NSNull(), 55, 43.3, 49, 55.2, 55.2], "n": 6, "mean": 55.2, "prev_mean": 50, "prev_n": 7,
                   "zones": ["green": 2, "yellow": 3, "red": 1],
                   "contributors": [["hrv": 0.5, "rhr": -0.2], NSNull(), NSNull(), NSNull(), NSNull(), NSNull(), ["hrv": 1.0]]],
      "hrv": ["label": "HRV", "unit": "ms", "group": "recovery", "values": [48, 50, 52, 47, 45, 51, 49], "avg7": [NSNull(), NSNull(), 50, 49.3, 48.6, 48.8, 48.9],
              "n": 7, "mean": 48.9, "prev_mean": 47.2, "prev_n": 7,
              "baseline": ["lo": [44, 44, 44, 44, 44, 44, 44], "hi": [54, 54, 54, 54, 54, 54, 54], "mean": [49, 49, 49, 49, 49, 49, 49]]],
      "strain": ["label": "Strain", "unit": "", "group": "strain", "values": [12.5, 8, 15, NSNull(), 10, 16.2, 9], "avg7": [NSNull(), NSNull(), 11.8, 11.8, 11.4, 12.3, 11.7],
                 "n": 6, "mean": 11.8, "prev_mean": 12, "prev_n": 7,
                 "target": ["bands": [[14, 18], [10, 14], NSNull(), [0, 10], [10, 14], [14, 18], [10, 14]], "in_range_days": 2, "days_with_strain": 6]],
      "vo2max": ["label": "VO2max", "unit": "", "group": "long", "values": [NSNull(), NSNull(), 44.1, NSNull(), NSNull(), 44.6, NSNull()],
                 "avg7": [NSNull(), NSNull(), NSNull(), NSNull(), NSNull(), NSNull(), NSNull()], "n": 2, "mean": 44.35, "prev_mean": 44.0, "prev_n": 1],
      "daylight": ["label": "Daylight", "unit": "min", "group": "sleep", "values": [NSNull(), NSNull(), NSNull(), NSNull(), NSNull(), NSNull(), NSNull()],
                   "avg7": [NSNull(), NSNull(), NSNull(), NSNull(), NSNull(), NSNull(), NSNull()], "n": 0, "prev_n": 0],
    ],
    "unavailable": ["weight": "no data"],
    "hr_zones": ["labels": ["50–60%", "60–70%", "70–80%", "80–90%", "90–100%"], "total_min": [60, 120, 40, 10, 2],
                 "weeks": [["week": "2026-03-23", "minutes": [30, 60, 20, 5, 1]]]],
    "aerobic_weeks": [["week": "2026-03-23", "value": 160, "target": 150]],
    "status": ["hrv_status": ["status": "balanced", "label": "平衡", "avg7_ms": 48.9, "band_ms": [44, 54], "nights": 7],
               "resilience": ["level": "solid", "label": "稳固", "capacity": 0.52, "stress_load": 0.2]],
  ]

  func testParsesTheTrendsDocument() throws {
    let d = try XCTUnwrap(TrendsData(json: Self.trendsJSON))
    XCTAssertEqual(d.dates.count, 7)
    let r = try XCTUnwrap(d.series["recovery"])
    XCTAssertEqual(r.values[2], nil)
    XCTAssertEqual(r.values[0], 70)
    XCTAssertEqual(r.zones, TrendSeries.Zones(green: 2, yellow: 3, red: 1))
    XCTAssertEqual(r.contributors[0]?["hrv"], 0.5)
    XCTAssertNil(r.contributors[1])
    XCTAssertEqual(d.series["strain"]?.target?.bands[0], 14...18)
    XCTAssertNil(d.series["strain"]?.target?.bands[2])
    XCTAssertEqual(d.series["hrv"]?.baseline?.lo.first, 44)
    XCTAssertEqual(d.series["vo2max"]?.last, 44.6)
    XCTAssertEqual(d.hrZones?.totalMin, [60, 120, 40, 10, 2])
    XCTAssertEqual(d.aerobicWeeks.first?.value, 160)
    XCTAssertFalse(d.withYear)
  }

  func testCardsFollowThePage() throws {
    let page = TrendsPage(data: try XCTUnwrap(TrendsData(json: Self.trendsJSON)), local: nil, vo2: ["level": "good"], sleep: nil)
    let groups = TrendCards.groups(page)
    XCTAssertEqual(groups.map(\.id), ["recovery", "sleep", "strain", "stress", "long"])
    let all = groups.flatMap(\.cards)
    // an optional metric without any reading is hidden; a missing core metric shows a dash card
    XCTAssertNil(all.first { $0.id == "daylight" })
    XCTAssertEqual(all.first { $0.id == "hrv" }?.value, "49")
    XCTAssertNotNil(all.first { $0.id == "resting_hr" }?.empty)
    // "vs 上期": HRV is better higher, so up is good
    XCTAssertEqual(all.first { $0.id == "hrv" }?.base?.0, "↑ 2")
    XCTAssertEqual(all.first { $0.id == "hrv" }?.base?.1, .good)
    // VO₂max: the latest reading, and the fitness level from the VO₂max endpoint instead of the change
    let vo2 = try XCTUnwrap(all.first { $0.id == "vo2max" })
    XCTAssertEqual(vo2.value, "44.6")
    XCTAssertEqual(vo2.base?.0, "体能 良好")
    // recovery bars in recovery colours; strain bars green inside the day's band
    let rec = try XCTUnwrap(all.first { $0.id == "recovery" }?.spec)
    XCTAssertEqual(rec.layers[0].tones, [.good, .mid, .text, .bad, .mid, .good, .mid])
    let strain = try XCTUnwrap(all.first { $0.id == "strain" })
    XCTAssertEqual(strain.spec?.layers[1].tones?[0], .text)
    XCTAssertEqual(strain.spec?.layers[1].tones?[5], .good)
    XCTAssertEqual(strain.tip(5), "3/30 · 16.2 / 14–18")
    XCTAssertEqual(all.first { $0.id == "hrv" }?.tip(2), "3/27 · 52 ms")
  }

  func testChartRangeKeepsTheReferenceBandVisible() {
    // band above every reading: its lower edge joins the range, so a strip of it shows
    let a = ChartSpec.range([5, 8, 10], min: 0, max: nil, zone: 20...60)
    XCTAssertEqual(a.lowerBound, 0)
    XCTAssertGreaterThan(a.upperBound, 20)
    XCTAssertLessThan(a.upperBound, 30)
    // band overlapping the readings adds nothing (clipped at the plot edge)
    let b = ChartSpec.range([40, 50], min: nil, max: nil, zone: 0...100)
    XCTAssertEqual(b.lowerBound, 40 - 1.2, accuracy: 1e-9)
    XCTAssertEqual(b.upperBound, 50 + 1.2, accuracy: 1e-9)
    // nothing at all
    XCTAssertEqual(ChartSpec.range([], min: 0, max: 100, zone: nil), 0...100)
  }

  func testLongLinesAreDownsampledKeepingPeaksAndGaps() {
    // a year of days with one spike and a 10-day gap
    var pts: [TrendChartBody.Pt] = []
    var seg = 0
    for i in 0..<365 {
      if (200..<210).contains(i) { if i == 200 { seg += 1 }; continue }
      pts.append(.init(id: i, y: i == 100 ? 99 : 50 + sin(Double(i) / 5), seg: seg))
    }
    let out = TrendChartBody.downsample(pts, limit: 140)
    XCTAssertLessThanOrEqual(out.count, 142)
    XCTAssertEqual(out.first, pts.first)
    XCTAssertEqual(out.last, pts.last)
    XCTAssertTrue(out.contains { $0.id == 100 }, "the spike survives")
    XCTAssertEqual(Set(out.map(\.seg)), [0, 1], "the gap stays a gap")
    XCTAssertEqual(out.map(\.id), out.map(\.id).sorted())
    // short ranges are drawn as they are
    XCTAssertEqual(TrendChartBody.downsample(Array(pts.prefix(90)), limit: 140), Array(pts.prefix(90)))
  }

  func testYearLabelsOnLongRanges() {
    XCTAssertTrue(TrendsFormat.spansYear("2025-11-01", "2026-01-15"))
    XCTAssertTrue(TrendsFormat.spansYear("2026-01-01", "2026-12-31"))
    XCTAssertFalse(TrendsFormat.spansYear("2026-03-01", "2026-03-31"))
    XCTAssertEqual(TrendsFormat.day("2025-11-03", year: true), "2025/11/3")
    XCTAssertEqual(TrendsFormat.day("2025-11-03", year: false), "11/3")
    XCTAssertEqual(TrendsFormat.mondayOf("2026-04-02"), "2026-03-30")
  }

  // MARK: - status cards on the phone

  /// 80 made-up days; the expected values are the server's (`trends/calc.py` run on the same numbers).
  static func sampleInputs() -> TrendsLocal.Inputs {
    var inp = TrendsLocal.Inputs()
    for i in 0..<80 {
      let d = LocalDay.add("2026-03-31", -(79 - i))
      inp.hrv[d] = i < 73 ? (10 * (50 + 5 * sin(Double(i) / 3))).rounded() / 10 : 40
      inp.trimp[d] = Double(60 + (i % 7) * 10)
      inp.ctl[d] = (10 * (40 + Double(i) * 0.2)).rounded() / 10
      inp.zoneMinutes[d] = [10, 20, 15, 5, 1 + Double(i % 3)]
      inp.strain[d] = 8 + Double(i % 5) * 2.5
      inp.tsb[d] = Double(-5 + (i % 9))
      if i >= 60 && i < 79 { inp.wristTemp[d] = 35.0 }
    }
    inp.recovery["2026-03-31"] = 50
    inp.z["2026-03-31"] = ["rhr": 1.2, "hrv": -1.5, "resp_rate": 0.4]
    inp.wristTemp["2026-03-31"] = 35.6
    inp.spo2["2026-03-31"] = 95
    inp.lowOffset["2026-03-31"] = 120
    inp.asleep["2026-03-31"] = 420
    return inp
  }

  func testLocalStatusMatchesTheServer() throws {
    let rt = try OpenBodyScoreRuntime.builtin()
    let out = try TrendsLocal.compute(Self.sampleInputs(), end: "2026-03-31", tz: "Australia/Sydney", runtime: rt)
    XCTAssertEqual(out.keys, ["hrv_status", "training_status", "load_focus", "illness", "recovery_index", "recovery_time"])
    let h = try XCTUnwrap(out.status["hrv_status"] as? [String: Any])
    XCTAssertEqual(h["status"] as? String, "poor")
    XCTAssertEqual(h["label"] as? String, "很低")
    XCTAssertEqual(TodayModel.num(h["avg7_ms"]), 40)
    XCTAssertEqual((h["band_ms"] as? [Any])?.compactMap(TodayModel.num), [46.1, 53.3])
    let t = try XCTUnwrap(out.status["training_status"] as? [String: Any])
    XCTAssertEqual(t["status"] as? String, "strained")
    XCTAssertEqual(TodayModel.num(t["acwr"]), 1)
    let lf = try XCTUnwrap(out.status["load_focus"] as? [String: Any])
    XCTAssertEqual((lf["groups"] as? [[String: Any]])?.compactMap { TodayModel.num($0["pct"]) }, [40, 52, 8])
    let rt2 = try XCTUnwrap(out.status["recovery_time"] as? [String: Any])
    XCTAssertEqual(TodayModel.num(rt2["hours"]), 24)
    XCTAssertEqual(rt2["from_date"] as? String, "2026-03-30")
    let ri = try XCTUnwrap(out.status["recovery_index"] as? [String: Any])
    XCTAssertEqual(TodayModel.num(ri["pct"]), 29)
    XCTAssertEqual(ri["level"] as? String, "good")
    let il = try XCTUnwrap(out.status["illness"] as? [String: Any])
    XCTAssertEqual(TodayModel.num(il["score"]), 63)
    XCTAssertEqual(il["level"] as? String, "high")
  }

  func testShortLocalHistoryLeavesTheServerValues() throws {
    var inp = TrendsLocal.Inputs()
    for i in 0..<10 { inp.hrv[LocalDay.add("2026-03-31", -i)] = 50; inp.trimp[LocalDay.add("2026-03-31", -i)] = 80 }
    inp.ctl["2026-03-31"] = 40
    let out = try TrendsLocal.compute(inp, end: "2026-03-31", tz: "Australia/Sydney", runtime: try OpenBodyScoreRuntime.builtin())
    XCTAssertTrue(out.keys.isEmpty, "\(out.keys)")
    // merged: the server's cards stay, the phone's replace only what it computed
    let server = Self.trendsJSON["status"] as! [String: Any]
    let local = TrendsLocal.Output(status: ["hrv_status": ["status": "low", "label": "偏低", "avg7_ms": 41, "band_ms": [44, 54], "nights": 7]],
                                   keys: ["hrv_status"], bundleVersion: "0.1.0")
    let st = TrendStatus(status: TrendsLocal.merge(server: server, local: local).status, local: local.keys)
    XCTAssertEqual(st.tiles.map(\.key), ["hrv_status", "resilience"])
    XCTAssertEqual(st.tiles[0].value, "偏低")
    XCTAssertEqual(st.tiles[0].tone, .mid)
    XCTAssertTrue(st.tiles[0].local)
    XCTAssertFalse(st.tiles[1].local)
  }

  func testLocalStatusReadsTheLocalTablesAndIsCached() throws {
    let db = try AppDatabase.inMemory()
    let inp = Self.sampleInputs()
    try db.writer.write { db in
      for (d, h) in inp.hrv {
        try db.execute(sql: "INSERT INTO daily_metrics (date, metric, value, updated_at) VALUES (?, 'heart_rate_variability_sdnn', ?, 0)", arguments: [d, h])
      }
      for d in inp.trimp.keys {
        var p: [String: Any] = ["trimp": inp.trimp[d]!, "ctl": inp.ctl[d]!, "tsb": inp.tsb[d]!, "strain": inp.strain[d]!,
                                "details": ["load": ["zone_minutes": inp.zoneMinutes[d]!]]]
        if d == "2026-03-31" {
          p["details"] = ["load": ["zone_minutes": inp.zoneMinutes[d]!], "recovery": ["z": inp.z[d]!, "spo2_night": 95, "wrist_temp": 35.6,
                                                                                     "rhr_low_offset_min": 120], "sleep": ["asleep_min": 420]]
          p["recovery"] = 50
        } else if let t = inp.wristTemp[d] {
          p["details"] = ["load": ["zone_minutes": inp.zoneMinutes[d]!], "recovery": ["wrist_temp": t]]
        }
        try DailyScore(date: d, recovery: d == "2026-03-31" ? 50 : nil, sleepScore: nil, strain: inp.strain[d], hrvMs: nil, rhr: nil,
                       payload: try JSONSerialization.data(withJSONObject: p), algoVersion: nil, origin: "server", computedAt: 0).save(db)
      }
    }
    let read = try TrendsLocal.read(db, end: "2026-03-31")
    XCTAssertEqual(read.trimp["2026-03-31"], inp.trimp["2026-03-31"])
    XCTAssertEqual(read.z["2026-03-31"]?["hrv"], -1.5)
    XCTAssertEqual(read.lowOffset["2026-03-31"], 120)
    let out = try XCTUnwrap(try TrendsLocal.run(db: db, runtime: try OpenBodyScoreRuntime.builtin(), end: "2026-03-31", tz: "Australia/Sydney"))
    XCTAssertTrue(out.keys.contains("hrv_status"))
    XCTAssertTrue(out.keys.contains("illness"))
    let cached = TrendsLocal.Output(json: try db.doc(TrendsLocal.docKey("2026-03-31"))?.json)
    XCTAssertEqual(cached, out)
  }

  // MARK: - reports, sleep nights

  func testReportTiles() throws {
    let r = try XCTUnwrap(ReportModel(json: [
      "start": "2026-03-25", "end": "2026-03-31", "previous": ["start": "2026-03-18", "end": "2026-03-24"],
      "current": ["recovery": ["avg": 61.5], "sleep": ["avg_actual_min": 432, "avg_bedtime": "23:40", "consistency_pct": 78],
                  "load": ["avg_strain": 11.2], "stress": ["avg": 0.8], "training": ["sessions": 4], "weight": ["last_kg": 70.4, "change_kg": -0.6]],
      "previous_values": ["sleep": ["avg_bedtime": "00:10"]],
      "change": ["recovery": ["avg": 3.5], "sleep": ["avg_actual_min": -12, "consistency_pct": 0], "load": ["avg_strain": 1.1],
                 "stress": ["avg": 0.2], "training": ["sessions": 1]],
      "journal_findings": [["key": "sauna", "label": "Sauna", "diff_recovery": 6.2]],
    ]))
    XCTAssertEqual(r.range, "3/25 – 3/31")
    XCTAssertEqual(r.tiles.map(\.id), ["recovery", "sleep", "bedtime", "consistency", "load", "stress", "training", "weight"])
    XCTAssertEqual(r.tiles[0].delta, "↑ 4")
    XCTAssertEqual(r.tiles[0].tone, .good)
    XCTAssertEqual(r.tiles[1].value, "7:12")
    XCTAssertEqual(r.tiles[1].tone, .bad)
    XCTAssertEqual(r.tiles[2].delta, "上期 00:10")
    XCTAssertEqual(r.tiles[3].tone, .text)
    XCTAssertEqual(r.tiles[5].tone, .bad)   // more stress is worse
    XCTAssertEqual(r.tiles[7].delta, "↓ 0.6kg")
    XCTAssertEqual(r.tiles[7].tone, .good)
    XCTAssertEqual(r.findings.first?.diff, 6.2)
  }

  func testBehaviorRows() {
    let list: [[String: Any]] = [
      ["item": ["key": "a", "label": "Alcohol (any)"], "outcomes": ["next_recovery": ["status": "ok", "direction": "worse", "diff_yes_minus_no": -8.4, "reliable": true]]],
      ["item": ["key": "b", "label": "Sauna / steam"], "outcomes": ["next_recovery": ["status": "ok", "direction": "better", "diff_yes_minus_no": 3.1, "reliable": false]]],
      ["item": ["key": "c", "label": "Late meal"], "outcomes": ["next_recovery": ["status": "too_few"]]],
    ]
    let b = BehaviorRow.parse(list)
    XCTAssertEqual(b.rows.map(\.label), ["Alcohol", "Sauna"])
    XCTAssertEqual(b.rows[0].tone, .bad)
    XCTAssertEqual(b.lacking, 1)
  }

  func testSleepNightsAverages() {
    let s = SleepNights(json: ["nights": [
      ["date": "2026-03-30", "main": ["start": "23:30", "end": "07:00", "asleep_min": 420, "deep_min": 80, "core_min": 250, "rem_min": 90, "awake_min": 20], "naps": []],
      ["date": "2026-03-31", "main": ["start": "00:30", "end": "08:00", "asleep_min": 400], "naps": [["start": "14:00", "end": "14:30"]]],
      ["date": "2026-04-01", "main": NSNull(), "naps": []],
    ]])
    XCTAssertEqual(s.mains.count, 2)
    XCTAssertEqual(s.avgSleep, 410)
    XCTAssertEqual(SleepNights.clock(s.avgBed), "00:00")   // the mean of 23:30 and 00:30 on the clock circle
    XCTAssertEqual(SleepNights.clock(s.avgWake), "07:30")
    XCTAssertEqual(s.nights[1].naps.first, (18 * 60)...(18 * 60 + 30))
  }
}
