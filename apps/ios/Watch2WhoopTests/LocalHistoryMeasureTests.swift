import GRDB
import XCTest
@testable import Watch2Whoop

/// Sizes and memory of the local history with dense made-up Apple Watch days (#340 local history; the figures in
/// LocalHistoryParams and docs/ios-native-app.md come from here). CI runs a short history; the full measurement:
///   TEST_RUNNER_W2W_MEASURE_DAYS=1095 xcodebuild test … -only-testing:Watch2WhoopTests/LocalHistoryMeasureTests
/// Output lines start with `W2W_MEASURE`.
final class LocalHistoryMeasureTests: XCTestCase {
  /// A dense day (made up): heart rate every ~43 s (≈2,000, the upper end of a watch worn all day with a workout),
  /// 8 HRV readings, ~110 respiration and ~30 SpO2 readings, a wrist temperature, a night of ~32 stage segments and
  /// 8 beat-to-beat readings of ~70 beats.
  static func denseBody(_ day: String, t0: Double, seed: Int) -> [String: Any] {
    var hr: [[Double]] = [], resp: [[Double]] = [], spo2: [[Double]] = [], hrv: [[Double]] = []
    var t = 17.0 + Double(seed % 13)
    var i = 0
    while t < 86_300 {
      let hour = Int(t / 3600)
      let base = hour < 7 ? 52.0 : (hour == 18 ? 145.0 : 72.0)
      hr.append([(t * 1000).rounded() / 1000, base + Double((i * 7 + seed) % 11)])
      t += 41 + Double((i * 13 + seed) % 5) + 0.237
      i += 1
    }
    for k in 0..<110 { resp.append([Double(k) * 230 + 60.5, 13.5 + Double((k + seed) % 5) * 0.5]) }
    for k in 0..<30 { spo2.append([Double(k) * 2800 + 30, 95.0 + Double((k + seed) % 4)]) }
    for k in 0..<8 { hrv.append([Double(k) * 9000 + 1200.25, 38.5 + Double((k * 3 + seed) % 9)]) }
    var segs: [[Any]] = []
    var s = t0 - 3600   // 23:00 the evening before
    let stages = ["core", "deep", "core", "rem", "awake"]
    for k in 0..<32 {
      let e = s + 900
      segs.append([s, e, stages[k % stages.count]])
      s = e
    }
    var beats: [[Any]] = []
    for k in 0..<8 {
      var ms: [Int] = [0]
      for b in 1..<70 { ms.append(ms[b - 1] + 780 + (b * 37 + k) % 90) }
      let a = t0 + Double(k) * 9000 + 1200
      beats.append(["e\(seed)-\(k)", a, a + 60, 41.5, 39.0, "app", ms, NSNull()])
    }
    return ["t0": t0, "complete": true, "inputs": ["hr": hr, "resp": resp, "spo2": spo2, "hrv": hrv, "temp": [[3 * 3600, 35.9]]],
            "segments": segs, "beats": ["cols": ["uuid", "start", "end", "rmssd", "sdnn", "origin", "beats_ms", "gaps"], "rows": beats]]
  }

  func testSizesAndMemory() throws {
    let env = ProcessInfo.processInfo.environment
    let days = Int(env["W2W_MEASURE_DAYS"] ?? "") ?? 45
    let server = "https://measure.example", uid = 990_002
    try? AppDatabase.deleteAccount(server: server, userId: uid)
    defer { try? AppDatabase.deleteAccount(server: server, userId: uid) }
    let db = try AppDatabase.open(server: server, userId: uid)
    let zone = Synthetic.zone
    let store = LocalRawStore(db: db, zone: zone)
    let today = "2026-06-30"
    let day0 = LocalDay.add(today, -(days - 1))
    func mb(_ b: Int64) -> String { String(format: "%.1f", Double(b) / 1_048_576) }

    // the history, as the download stores it (one transaction a page of 14 days, like HistoryDownload)
    let base = db.footprint().fileBytes
    var rows = 0
    let t1 = Date()
    for page in stride(from: 0, to: days, by: 14) {
      try db.writer.write { dbw in
        for k in page..<min(days, page + 14) {
          let d = LocalDay.add(day0, k)
          try autoreleasepool {
            rows += try store.storeHistoryDay(dbw, day: d, body: Self.denseBody(d, t0: store.dayStart(d)!, seed: k))
          }
        }
      }
    }
    // the nights and daily resting heart rate the sleep / daily streams bring (all history)
    try db.writer.write { dbw in
      for k in 0..<days {
        let d = LocalDay.add(day0, k), t0 = store.dayStart(d)!
        let payload: [String: Any] = ["start": t0 - 3600, "end": t0 + 7 * 3600, "asleep_min": 440, "core_min": 250, "deep_min": 90,
                                      "rem_min": 100, "awake_min": 40, "source": "app", "device": "Apple Watch"]
        try dbw.execute(sql: """
          INSERT INTO sleep_sessions (id, date, start, end, is_main, source, updated_at, payload) VALUES (?, ?, ?, ?, 1, 'app', 0, ?)
          """, arguments: ["s:\(d)", d, t0 - 3600, t0 + 7 * 3600, try JSONSerialization.data(withJSONObject: payload)])
        try dbw.execute(sql: "INSERT INTO daily_metrics (date, metric, value, source, updated_at) VALUES (?, 'resting_heart_rate', ?, 'app', 0)",
                        arguments: [d, 52 + Double(k % 5)])
      }
    }
    try db.maintain(onPower: false)
    let f = db.footprint()
    let histBytes = f.history ?? (f.fileBytes - base)
    print("W2W_MEASURE history days=\(days) rows=\(rows) store=\(String(format: "%.1f", Date().timeIntervalSince(t1)))s "
          + "history=\(mb(histBytes))MB perDay=\(histBytes / Int64(days))B file=\(mb(f.fileBytes))MB "
          + "scores+sleep=\(mb(f.scores ?? 0))MB")
    for y in [1, 2, 3] {
      print("W2W_MEASURE estimate years=\(y) history=\(mb(histBytes / Int64(days) * 365 * Int64(y)))MB")
    }
    XCTAssertGreaterThan(rows, days * 2000)

    // the hot window (uuid-keyed, from HealthKit): 3 dense days, per-day bytes
    let hotDays = 3
    let before = db.footprint().raw ?? 0
    for k in 0..<hotDays {
      let d = LocalDay.add(today, -k), t0 = store.dayStart(d)!
      let b = Self.denseBody(d, t0: t0, seed: k)
      let hr = (b["inputs"] as! [String: Any])["hr"] as! [[Double]]
      try store.ingest(hr.map { LocalRawStore.Sample(uuid: UUID().uuidString.lowercased(), type: LocalRawStore.heartRate,
                                                     start: t0 + $0[0], end: t0 + $0[0], value: $0[1]) })
    }
    if let after = db.footprint().raw {
      print("W2W_MEASURE hot perDay=\((after - before) / Int64(hotDays))B estimate60=\(mb((after - before) / Int64(hotDays) * 60))MB")
    }
    try db.writer.write { dbw in try dbw.execute(sql: "DELETE FROM hk_samples") }

    // a full recompute chunk by chunk (what ScoringSync does), memory sampled
    let rt = try OpenBodyScoreRuntime.builtin()
    let ctx = LocalScoring.Context(tz: Synthetic.tz, hrvKind: "sdnn", birthDate: "1990-05-05", origin: day0)
    try db.setMeta(LocalRawStore.hotFromKey, LocalDay.add(today, 1))   // all of it from the history
    let chunk = Int(env["W2W_MEASURE_CHUNK"] ?? "") ?? LocalHistoryParams.scoreChunkDays
    let probe = MemoryProbe()
    let t2 = Date()
    var a = day0, written = 0
    while a <= today {
      let b = min(today, LocalDay.add(a, chunk - 1))
      written += try LocalScoring.run(db: db, runtime: rt, context: ctx, first: a, last: b, today: today).written.count
      rt.collectGarbage()
      if env["W2W_MEASURE_TRACE"] != nil { print("W2W_MEASURE trace \(b) now=\(mb(Int64(MemoryProbe.footprint())))MB") }
      a = LocalDay.add(b, 1)
    }
    let (peak, start) = probe.stop()
    print("W2W_MEASURE chunked days=\(written) chunk=\(chunk) time=\(String(format: "%.1f", Date().timeIntervalSince(t2)))s "
          + "peak=\(mb(Int64(peak)))MB start=\(mb(Int64(start)))MB delta=\(mb(Int64(peak) - Int64(start)))MB")
    XCTAssertEqual(written, days)

    // for comparison: one call over (at most) 180 days
    let one = min(days, 180)
    rt.collectGarbage()
    let probe2 = MemoryProbe()
    try LocalScoring.run(db: db, runtime: rt, context: ctx, first: LocalDay.add(today, -(one - 1)), today: today)
    let (peak2, start2) = probe2.stop()
    print("W2W_MEASURE oneCall days=\(one) peak=\(mb(Int64(peak2)))MB delta=\(mb(Int64(peak2) - Int64(start2)))MB")
  }
}
