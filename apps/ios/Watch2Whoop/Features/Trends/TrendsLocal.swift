import Foundation
import GRDB

/// The Trends status cards the phone computes itself with the algorithm bundle (`OpenBodyScore.trend(name, …)`, the
/// port of trends/calc.py), from the local tables `daily_scores` (its `payload` is the server's score row with `details`),
/// `daily_metrics` and `sleep_sessions`. The rest comes from `/trends` (docs/ios-native-app.md section 9.3):
///
/// | Card | Where | Why |
/// |---|---|---|
/// | HRV status, training status, load focus, illness risk, lowest-HR timing, recovery time, chronotype | phone | inputs are in the local score rows, metrics and sleep sessions |
/// | resilience, body battery, heart-rate recovery | phone when package `stress` is primary (M2), else server | the bundle's `stressCards` (insights/stress.cards) on `stress_daily` (StressStage), the score rows and the heart-rate-recovery values the phone has (Apple's; M1c's workouts once on the phone) |
///
/// Package `stress` in shadow: the three cards are computed and compared with the server's status of the same day (field
/// names in `meta` `stress.compare`), the page keeps the server's; primary: the phone's replace them (null hides a card).
///
/// A card is only computed when the local history covers its window (else the bundle would answer from a few days and
/// disagree with the server); otherwise the server's value stays. The result is kept as the document
/// `trends.local:<end>`, so the page draws it from the cache next time.
enum TrendsLocal {
  /// Keys the phone can compute; everything else in `status` is the server's.
  static let keys: Set<String> = ["hrv_status", "training_status", "load_focus", "illness", "recovery_index", "recovery_time", "chronotype"]
  /// Days read before `end` (HRV status: 7 + 60 days; wrist temperature baseline 60 days).
  static let lookback = 70

  /// The local inputs, keyed by local date (`YYYY-MM-DD`), as the bundle takes them.
  struct Inputs: Equatable {
    var recovery: [String: Double] = [:]
    var strain: [String: Double] = [:]
    var trimp: [String: Double] = [:]
    var ctl: [String: Double] = [:]
    var tsb: [String: Double] = [:]
    var zoneMinutes: [String: [Double]] = [:]
    var z: [String: [String: Double]] = [:]
    var spo2: [String: Double] = [:]
    var wristTemp: [String: Double] = [:]
    var lowOffset: [String: Double] = [:]
    var asleep: [String: Double] = [:]
    var hrv: [String: Double] = [:]
    var vo2: [String: Double] = [:]
    /// Main sleep per wake day: [start, end] in UTC seconds.
    var nights: [String: [Double]] = [:]

    var isEmpty: Bool { recovery.isEmpty && hrv.isEmpty && nights.isEmpty && trimp.isEmpty }
  }

  struct Output: Equatable {
    var status: [String: Any]
    var keys: Set<String>
    var bundleVersion: String

    static func == (a: Output, b: Output) -> Bool {
      a.keys == b.keys && a.bundleVersion == b.bundleVersion && NSDictionary(dictionary: a.status).isEqual(to: b.status)
    }

    var json: [String: Any] { ["status": status, "keys": keys.sorted(), "bundle_version": bundleVersion] }

    init(status: [String: Any], keys: Set<String>, bundleVersion: String) {
      self.status = status
      self.keys = keys
      self.bundleVersion = bundleVersion
    }

    init?(json: [String: Any]?) {
      guard let json, let s = json["status"] as? [String: Any] else { return nil }
      status = s
      keys = Set(json["keys"] as? [String] ?? [])
      bundleVersion = json["bundle_version"] as? String ?? ""
    }
  }

  static func docKey(_ end: String) -> String { "trends.local:\(end)" }

  // MARK: - reading the local tables

  static func read(_ db: AppDatabase, end: String) throws -> Inputs {
    let lo = LocalDay.add(end, -lookback)
    return try db.writer.read { db in
      var inp = Inputs()
      for r in try Row.fetchAll(db, sql: "SELECT date, recovery, strain, payload FROM daily_scores WHERE date BETWEEN ? AND ?",
                                arguments: [lo, end]) {
        let d: String = r["date"]
        if let v = r["recovery"] as Double? { inp.recovery[d] = v }
        if let v = r["strain"] as Double? { inp.strain[d] = v }
        guard let data = r["payload"] as Data?, let p = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any] else { continue }
        add(p, day: d, to: &inp)
      }
      for r in try Row.fetchAll(db, sql: """
        SELECT date, metric, value FROM daily_metrics WHERE metric IN ('heart_rate_variability_sdnn', 'vo2_max')
        AND value IS NOT NULL AND date BETWEEN ? AND ?
        """, arguments: [lo, end]) {
        let d: String = r["date"], v: Double = r["value"]
        if (r["metric"] as String) == "vo2_max" { inp.vo2[d] = v } else { inp.hrv[d] = v }
      }
      for r in try Row.fetchAll(db, sql: "SELECT date, start, end FROM sleep_sessions WHERE is_main AND date > ? AND date <= ?",
                                arguments: [LocalDay.add(end, -60), end]) {
        let d: String = r["date"], a: Double = r["start"], b: Double = r["end"]
        if let cur = inp.nights[d], cur[1] - cur[0] >= b - a { continue }   // the longest main sleep of the night
        inp.nights[d] = [a, b]
      }
      return inp
    }
  }

  /// One stored score row (the server's `daily_scores` columns + `details`) into the inputs.
  static func add(_ p: [String: Any], day d: String, to inp: inout Inputs) {
    if inp.recovery[d] == nil, let v = TodayModel.num(p["recovery"]) { inp.recovery[d] = v }
    if inp.strain[d] == nil, let v = TodayModel.num(p["strain"]) { inp.strain[d] = v }
    if let v = TodayModel.num(p["trimp"]) { inp.trimp[d] = v }
    if let v = TodayModel.num(p["ctl"]) { inp.ctl[d] = v }
    if let v = TodayModel.num(p["tsb"]) { inp.tsb[d] = v }
    let details = p["details"] as? [String: Any] ?? [:]
    if let zm = (details["load"] as? [String: Any])?["zone_minutes"] as? [Any] { inp.zoneMinutes[d] = zm.map { TodayModel.num($0) ?? 0 } }
    if let rc = details["recovery"] as? [String: Any] {
      if let z = rc["z"] as? [String: Any] { inp.z[d] = z.compactMapValues(TodayModel.num) }
      if let v = TodayModel.num(rc["spo2_night"]) { inp.spo2[d] = v }
      if let v = TodayModel.num(rc["wrist_temp"]) { inp.wristTemp[d] = v }
      if let v = TodayModel.num(rc["rhr_low_offset_min"]) { inp.lowOffset[d] = v }
    }
    if let v = TodayModel.num((details["sleep"] as? [String: Any])?["asleep_min"]) { inp.asleep[d] = v }
  }

  // MARK: - computing

  /// The cards the local history is long enough for, as of `end`.
  static func compute(_ inp: Inputs, end: String, tz: String, runtime: OpenBodyScoreRuntime) throws -> Output {
    _ = try? runtime.invoke("setLanguage", [Lang.code])
    func trend(_ name: String, _ args: [Any]) throws -> Any? {
      let out = try runtime.invoke("trend", [name] + args) as? [String: Any]
      let v = out?["value"]
      return v is NSNull ? nil : v
    }
    func count<T>(_ m: [String: T], days: Int) -> Int { m.keys.filter { $0 > LocalDay.add(end, -days) && $0 <= end }.count }
    var status: [String: Any] = [:]

    // HRV status: 7-day mean against the 60 days before it (3 nights in the week, 14 in the baseline)
    var hrvStatus: Any?
    if count(inp.hrv, days: 7) >= 3, count(inp.hrv, days: 67) - count(inp.hrv, days: 7) >= 14 {
      hrvStatus = try trend("hrv_status", [inp.hrv, end])
      if let h = hrvStatus { status["hrv_status"] = h }
    }
    // training status and load focus: 28 days of training load (21 with TRIMP, as for the ACWR series)
    if count(inp.trimp, days: 28) >= 21, inp.ctl[end] != nil {
      if let t = try trend("training_status", [inp.trimp, inp.ctl, inp.vo2, hrvStatus ?? NSNull(), end]) { status["training_status"] = t }
    }
    if count(inp.zoneMinutes, days: 28) >= 21, let f = try trend("load_focus", [inp.zoneMinutes, end]) { status["load_focus"] = f }
    // illness risk: the day's z-scores, wrist temperature against its own 60-day mean (≥ 3 nights), SpO₂
    if let z = inp.z[end] {
      let dev: Any = wristTempDeviation(inp.wristTemp, end: end) ?? NSNull()
      let spo2: Any = inp.spo2[end] ?? NSNull()
      if let il = try trend("illness_risk", [z, dev, spo2]) { status["illness"] = il }
    }
    if let low = inp.lowOffset[end], let asleep = inp.asleep[end], let ri = try trend("recovery_index", [low, asleep]) {
      status["recovery_index"] = ri
    }
    if inp.recovery[end] != nil || inp.strain[LocalDay.add(end, -1)] != nil, count(inp.strain, days: 4) > 0 {
      var zone: Any = NSNull()
      if let r = inp.recovery[end], let zr = try trend("recovery_zone", [r]) { zone = zr }
      if let rt = try trend("recovery_time", [inp.strain, inp.tsb, zone, end]) { status["recovery_time"] = rt }
    }
    let nights = inp.nights.sorted { $0.key < $1.key }.map { [$0.value[0] * 1000, $0.value[1] * 1000] }
    if nights.count >= 10, let c = try trend("chronotype", [nights, tz]) { status["chronotype"] = c }

    return Output(status: status, keys: Set(status.keys), bundleVersion: runtime.version)
  }

  /// The night's wrist temperature against the mean of the 60 days before it (≥ 3 nights), two decimals (series.py).
  static func wristTempDeviation(_ temps: [String: Double], end: String) -> Double? {
    guard let t = temps[end] else { return nil }
    let from = LocalDay.add(end, -60)
    let past: [Double] = temps.filter { $0.key >= from && $0.key < end }.map { $0.value }
    guard past.count >= 3 else { return nil }
    let mean: Double = past.reduce(0, +) / Double(past.count)
    let dev: Double = t - mean
    return (dev * 100).rounded() / 100
  }

  /// Reads, computes and stores `trends.local:<end>` (nothing is stored when the phone has no history at all).
  @discardableResult
  static func run(db: AppDatabase, runtime: OpenBodyScoreRuntime, end: String, tz: String, now: Date = Date()) throws -> Output? {
    let inp = try read(db, end: end)
    guard !inp.isEmpty else { return nil }
    var out = try compute(inp, end: end, tz: tz, runtime: runtime)
    let mode = try db.writer.read { StressStage.mode($0) }
    if mode != .off {
      let cards = try stressCards(db, runtime: runtime, end: end, tz: tz, now: now)
      if mode == .primary {
        for k in stressKeys { out.status[k] = cards[k] ?? NSNull() }
        out.keys.formUnion(stressKeys)
      } else if let server = try serverStatus(db, end: end) {
        recordStressCompare(compareStress(local: cards, server: server, today: end == today(tz, now)), end: end, db: db)
      }
    }
    try db.saveDoc(docKey(end), json: try JSONSerialization.data(withJSONObject: out.json))
    return out
  }

  // MARK: - the stress cards (package M2)

  /// The cards `stressCards` answers (insights/stress.cards).
  static let stressKeys: Set<String> = ["resilience", "body_battery", "hr_recovery"]
  /// Days of heart-rate-recovery values read before `end`: the server's window of the default 30-day Trends range
  /// (trends/series.window: 2 × 30 days + 90 days of baselines), so the last value is the same one.
  static let hrrLookback = 150
  static let stressCompareMeta = "stress.compare"

  static func today(_ tz: String, _ now: Date) -> String { LocalDay.string(now, TimeZone(identifier: tz) ?? .current) }

  /// The bundle's `stressCards` on the local tables: the score rows and `stress_daily` of the 14 days up to `end`, the
  /// heart-rate-recovery values (Apple's one-minute value, else the workouts' own when M1c's table is there).
  static func stressCards(_ db: AppDatabase, runtime: OpenBodyScoreRuntime, end: String, tz: String, now: Date) throws -> [String: Any] {
    let from = LocalDay.add(end, -13)
    let data: [String: Any] = try db.writer.read { db in
      var apple: [String: Double] = [:], own: [String: Double] = [:]
      for r in try Row.fetchAll(db, sql: """
        SELECT date, value FROM daily_metrics WHERE metric = 'heart_rate_recovery_one_minute' AND value IS NOT NULL
        AND date BETWEEN ? AND ?
        """, arguments: [LocalDay.add(end, -hrrLookback), end]) {
        apple[r["date"] as String] = r["value"] as Double
      }
      if let rows = try? Row.fetchAll(db, sql: """
        SELECT local_date, max(hr_recovery_bpm) AS v FROM workouts WHERE dup_of IS NULL AND excluded IS NULL
        AND hr_recovery_bpm IS NOT NULL AND local_date BETWEEN ? AND ? GROUP BY local_date
        """, arguments: [LocalDay.add(end, -hrrLookback), end]) {
        for r in rows { own[r["local_date"] as String] = r["v"] as Double }
      }
      let scores = try SeriesLocal.scoreRows(db, from: from, to: end), stress = try StressStage.rows(db, from: from, to: end)
      return ["scores": scores, "stress": stress, "hrr_apple": apple, "hrr_workouts": own]
    }
    var cal = Calendar(identifier: .gregorian)
    cal.timeZone = TimeZone(identifier: tz) ?? .current
    let opts: [String: Any] = ["end": end, "today": today(tz, now), "tz": tz, "now_hour": cal.component(.hour, from: now)]
    _ = try? runtime.invoke("setLanguage", [Lang.code])
    return (try runtime.invoke("stressCards", [data, opts]) as? [String: Any]) ?? [:]
  }

  /// The server's status cards as of `end`: any cached Trends document of that end date.
  static func serverStatus(_ db: AppDatabase, end: String) throws -> [String: Any]? {
    try db.writer.read { db in
      for d in try DocRecord.fetchAll(db, sql: "SELECT * FROM docs WHERE key LIKE 'trends:%' ORDER BY fetched_at DESC") {
        if let j = d.json, j["end"] as? String == end, let s = j["status"] as? [String: Any] { return s }
      }
      return nil
    }
  }

  /// Field paths of the three cards that differ (numbers within 1e-9 relative, texts exact). Today's body battery
  /// depends on the hour each side computed it at: only its value on waking is compared.
  static func compareStress(local: [String: Any], server: [String: Any], today: Bool) -> SeriesLocal.Comparison {
    var l: [String: Any] = [:], s: [String: Any] = [:]
    for k in stressKeys {
      l[k] = local[k] ?? NSNull()
      s[k] = server[k] ?? NSNull()
    }
    if today {
      l["body_battery"] = ((l["body_battery"] as? [String: Any])?["wake"]) ?? NSNull()
      s["body_battery"] = ((s["body_battery"] as? [String: Any])?["wake"]) ?? NSNull()
    }
    return SeriesLocal.compare(local: l, server: s)
  }

  static func recordStressCompare(_ c: SeriesLocal.Comparison, end: String, db: AppDatabase) {
    let o: [String: Any] = ["ok": c.ok, "fields_over": c.fieldsOver, "compared": c.compared, "end": end,
                            "at": Date().timeIntervalSince1970]
    if let data = try? JSONSerialization.data(withJSONObject: o, options: [.sortedKeys]) {
      try? db.setMeta(stressCompareMeta, String(decoding: data, as: UTF8.self))
    }
  }

  /// The server's status with the phone's results laid over it.
  static func merge(server: [String: Any], local: Output?) -> (status: [String: Any], keys: Set<String>) {
    guard let local else { return (server, []) }
    var s = server
    for (k, v) in local.status { s[k] = v }
    return (s, local.keys)
  }
}
