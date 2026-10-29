import Foundation
import GRDB

/// Scores computed on the phone (#340: "算法在手机上算"; docs/ios-native-app.md section 9.1): the local tables →
/// `OpenBodyScore.buildInputs` → `computeDays` in JavaScriptCore → `daily_scores` (origin `app`).
///
/// A *seeded* run (packages/openbodyscore-js engine.js): the phone keeps raw samples for `LocalRawStore.keepDays` only,
/// so it recomputes from a later `first` and brings the history in as
/// - `seed_rows`: the stored rows of the 62 days before `first` (baselines, sleep debt, CTL / ATL — pulled from the
///   server or written here before);
/// - `hr_hist`: per-day heart-rate histograms of the 365 days before the raw window (HRmax);
/// and, for the days it computes: raw samples from two days before `first`, the server's sleep sessions and stage
/// segments (sleep stream), the daily resting heart rate (daily stream), the muscle load and `hrv_kind` from
/// `GET /v1/sync/scoring`. RMSSD users read `heartbeat_series`.
/// A night the server has not derived yet (no `sleep_sessions` row for that wake day) is built from the phone's stage
/// samples; such a day is "unsettled": shown, not uploaded (`ScoringSync` uploads only the days before it).
enum LocalScoring {
  /// HealthKit identifiers → the bundle's sample kinds.
  static let sampleKinds: [String: String] = [
    "HKQuantityTypeIdentifierHeartRate": "hr",
    "HKQuantityTypeIdentifierHeartRateVariabilitySDNN": "hrv",
    "HKQuantityTypeIdentifierRespiratoryRate": "resp",
    "HKQuantityTypeIdentifierOxygenSaturation": "spo2",
    "HKQuantityTypeIdentifierAppleSleepingWristTemperature": "temp",
  ]
  static let restingHR = LocalRawStore.restingHR
  static let sleepAnalysis = LocalRawStore.sleepAnalysis
  /// HKCategoryValueSleepAnalysis → stage (0 = in bed is not sleep).
  static let sleepStages: [Int: String] = [1: "unspecified", 2: "awake", 3: "core", 4: "deep", 5: "rem"]
  /// Days of stored rows before `first` (60-day baselines, 14 nights of sleep debt) and of sleep before it (consistency).
  static let seedDays = 62
  static let sleepBackDays = 20
  /// Gap that starts a new sleep session (importers/common.py).
  static let sessionGap: TimeInterval = 60 * 60

  /// What the server tells the phone (`GET /v1/sync/scoring`).
  struct Context: Equatable {
    var tz: String
    var hrvKind: String
    var birthDate: String?
    var origin: String?
    var algoVersion: String?
    var syncMode: String = "app"
    var muscle: [String: Any] = [:]
    var muscleError: String?

    static func == (a: Context, b: Context) -> Bool {
      a.tz == b.tz && a.hrvKind == b.hrvKind && a.birthDate == b.birthDate && a.origin == b.origin
        && a.algoVersion == b.algoVersion && a.syncMode == b.syncMode && a.muscleError == b.muscleError
    }

    init(tz: String, hrvKind: String, birthDate: String? = nil, origin: String? = nil, algoVersion: String? = nil,
         syncMode: String = "app") {
      self.tz = tz
      self.hrvKind = hrvKind
      self.birthDate = birthDate
      self.origin = origin
      self.algoVersion = algoVersion
      self.syncMode = syncMode
    }

    init?(_ j: [String: Any]) {
      guard let tz = j["tz"] as? String else { return nil }
      self.tz = tz
      hrvKind = j["hrv_kind"] as? String ?? "sdnn"
      birthDate = j["birth_date"] as? String
      origin = j["scoring_origin"] as? String
      algoVersion = j["algo_version"] as? String
      syncMode = j["sync_mode"] as? String ?? "ow"
      let m = j["muscle"] as? [String: Any]
      muscle = m?["days"] as? [String: Any] ?? [:]
      muscleError = m?["error"] as? String
    }
  }

  struct Result: Equatable {
    var written: [String] = []
    /// Days computed with a night the server has not sent yet (not to be uploaded).
    var unsettled: [String] = []
    var skipped: String?
  }

  /// Is there anything to compute from? (cheap)
  static func hasRawData(_ db: AppDatabase) -> Bool {
    (try? db.writer.read { db in
      try Bool.fetchOne(db, sql: "SELECT EXISTS (SELECT 1 FROM hk_samples LIMIT 1)")
    }) ?? false
  }

  /// Computes `[first, last]` (`last` = `today` unless a chunk of a longer recompute) and writes the rows as origin `app`
  /// (`replace`: over the server's rows too — the app scores this user; otherwise only days without a row). The rows are
  /// marked not uploaded. Memory follows the range (the whole source goes through JavaScriptCore in one call): longer
  /// ranges go chunk by chunk (`runChunked`).
  @discardableResult
  static func run(db: AppDatabase, runtime: OpenBodyScoreRuntime, context ctx: Context, first: String, last: String? = nil,
                  today: String, replace: Bool = true) throws -> Result {
    // everything this call allocates goes when it returns (a chunked recompute calls it hundreds of times in a row)
    try autoreleasepool {
      try compute(db: db, runtime: runtime, context: ctx, first: first, last: last, today: today, replace: replace)
    }
  }

  private static func compute(db: AppDatabase, runtime: OpenBodyScoreRuntime, context ctx: Context, first: String,
                              last: String?, today: String, replace: Bool) throws -> Result {
    guard let zone = TimeZone(identifier: ctx.tz), LocalDay.parse(today, zone) != nil else { return Result(skipped: "bad day") }
    var first = first
    let last = min(last ?? today, today)
    if let origin = ctx.origin, origin > first { first = origin }
    if first > last { return Result(skipped: "nothing to score") }
    guard let source = try Self.source(db: db, ctx: ctx, zone: zone, first: first, last: last, today: today) else {
      return Result(skipped: "no local data")
    }
    let rows = try runtime.scoreDays(source: source.src, first: first, last: last, today: today, origin: ctx.origin)
    var out = Result(unsettled: source.unsettled.sorted())
    try db.writer.write { db in
      for row in rows {
        guard let day = row["date"] as? String, day >= first, day <= last else { continue }
        if !replace, let existing = try DailyScore.fetchOne(db, key: day), existing.origin != "app" { continue }
        let payload = try JSONSerialization.data(withJSONObject: row, options: [.sortedKeys])
        try DailyScore(date: day, recovery: num(row["recovery"]), sleepScore: num(row["sleep_score"]),
                       strain: num(row["strain"]), hrvMs: nil, rhr: num(row["rhr_night"]),
                       payload: payload, algoVersion: row["algo_version"] as? String, origin: "app",
                       computedAt: Date().timeIntervalSince1970, uploaded: false).save(db)
        out.written.append(day)
      }
    }
    return out
  }

  /// The bundle's `buildInputs` source for `[first, last]` (nil when there is nothing at all). Raw samples from two days
  /// before `first` to the end of `last`: `hk_samples` from `LocalRawStore.hotFrom` on, the compact history before it
  /// (LocalHistoryStore.swift). A night's stage segments: the sleep stream's (`sleep_sessions.stages`), else the history's.
  static func source(db: AppDatabase, ctx: Context, zone: TimeZone, first: String, last: String? = nil,
                     today: String) throws -> (src: [String: Any], unsettled: Set<String>)? {
    let last = last ?? today
    let store = LocalRawStore(db: db, zone: zone)
    let keepDay = LocalDay.add(first, -2)
    guard let keep = store.dayStart(keepDay), let stop = store.dayStart(LocalDay.add(last, 1)),
          let sleepLo = store.dayStart(LocalDay.add(first, -(sleepBackDays + 1))) else { return nil }
    let hot = store.hotFromIfSet.flatMap(store.dayStart) ?? keep   // no boundary yet: all from hk_samples
    let hotLo = max(keep, hot)
    return try db.writer.read { db -> (src: [String: Any], unsettled: Set<String>)? in
      // raw samples of the computed days (and the two before: the first night, wrist temperature lead)
      var samples: [[String: Any]] = []
      let kinds = sampleKinds.filter { ctx.hrvKind == "sdnn" || $0.value != "hrv" }
      if keep < hot {   // the history
        for name in Set(kinds.values).sorted() {
          guard let kind = LocalHistoryParams.Kind(name: name) else { continue }
          let rows = try Row.fetchCursor(db, sql: "SELECT t, value FROM hist_samples WHERE kind = ? AND t >= ? AND t < ? ORDER BY t",
                                         arguments: [kind.rawValue, keep, min(hot, stop)])
          while let r = try rows.next() {
            samples.append(["kind": name, "t": (r[0] as Double) * 1000, "value": r[1] as Double])
          }
        }
      }
      if stop > hotLo {   // the hot window
        var args: [(any DatabaseValueConvertible)?] = kinds.keys.map { $0 }
        args += [hotLo, stop]
        let rows = try Row.fetchCursor(db, sql: """
          SELECT type, start, value FROM hk_samples WHERE type IN (\(kinds.keys.map { _ in "?" }.joined(separator: ",")))
          AND start >= ? AND start < ? ORDER BY start, uuid
          """, arguments: StatementArguments(args))
        while let r = try rows.next() {
          guard let kind = kinds[r[0] as String] else { continue }
          samples.append(["kind": kind, "t": (r[1] as Double) * 1000, "value": r[2] as Double])
        }
      }
      if ctx.hrvKind == "rmssd" {
        for r in try Row.fetchAll(db, sql: """
          SELECT start, rmssd FROM heartbeat_series WHERE rmssd IS NOT NULL AND start >= ? AND start < ? ORDER BY start
          """, arguments: [keep, stop]) {
          samples.append(["kind": "hrv", "t": (r["start"] as Double) * 1000, "value": r["rmssd"] as Double])
        }
      }
      // resting heart rate per day: the server's daily value, else the watch's own (last of the day)
      var rhr: [String: Double] = [:]
      for r in try Row.fetchAll(db, sql: "SELECT start, value FROM hk_samples WHERE type = ? AND start >= ? AND start < ? ORDER BY start",
                                arguments: [restingHR, keep, stop]) {
        rhr[LocalDay.string(Date(timeIntervalSince1970: r["start"]), zone)] = r["value"]
      }
      for r in try Row.fetchAll(db, sql: """
        SELECT date, value FROM daily_metrics WHERE metric = 'resting_heart_rate' AND value IS NOT NULL AND date >= ? AND date <= ?
        """, arguments: [LocalDay.add(first, -seedDays), last]) {
        rhr[r["date"]] = r["value"]
      }
      // sleep: the server's sessions and stage segments; a wake day it has not sent yet from the phone's stage samples
      var sessions: [[String: Any]] = []
      var segments: [[String: Any]] = []
      var nightDays = Set<String>(), stagedDays = Set<String>()
      for r in try Row.fetchAll(db, sql: """
        SELECT date, start, end, stages, source, payload FROM sleep_sessions WHERE date >= ? AND date <= ? ORDER BY start
        """, arguments: [LocalDay.add(first, -sleepBackDays), last]) {
        let wake: String = r["date"]
        nightDays.insert(wake)
        let stages = (r["stages"] as Data?).flatMap { try? JSONSerialization.jsonObject(with: $0) as? [[Any]] } ?? []
        var segs: [Segment] = []
        for s in stages where s.count >= 3 {
          guard let st = s[0] as? String, let a = num(s[1]), let b = num(s[2]) else { continue }
          segs.append(Segment(start: a, end: b, stage: st))
        }
        if !segs.isEmpty { stagedDays.insert(wake) }
        segments += segs.map(\.json)
        if let p = (r["payload"] as Data?).flatMap({ try? JSONSerialization.jsonObject(with: $0) as? [String: Any] }) {
          sessions.append(serverSession(p, wake: wake))
        } else {
          sessions.append(session(segs, start: r["start"], end: r["end"], wake: wake, source: r["source"]))
        }
      }
      // nights older than the sleep stream's segments: the history's (the same segments the server's scores use)
      for r in try Row.fetchAll(db, sql: "SELECT start, end, stage, date FROM hist_segments WHERE start >= ? AND start < ? ORDER BY start",
                                arguments: [sleepLo, stop]) where !stagedDays.contains(r["date"] as String) {
        segments.append(Segment(start: r["start"], end: r["end"], stage: r["stage"]).json)
      }
      var unsettled = Set<String>()
      var phone: [Segment] = []
      for r in try Row.fetchAll(db, sql: "SELECT start, end, value FROM hk_samples WHERE type = ? AND start >= ? AND start < ? ORDER BY start",
                                arguments: [sleepAnalysis, hotLo, stop]) {
        guard let st = sleepStages[Int(r["value"] as Double)] else { continue }
        phone.append(Segment(start: r["start"], end: r["end"], stage: st))
      }
      for group in groupSessions(phone) {
        let a = group.map(\.start).min()!, b = group.map(\.end).max()!
        let wake = LocalDay.string(Date(timeIntervalSince1970: b - LocalRawStore.wakeShift), zone)
        guard wake >= first, wake <= last, !nightDays.contains(wake) else { continue }
        sessions.append(session(group, start: a, end: b, wake: wake, source: "phone"))
        segments += group.map(\.json)
        unsettled.insert(wake)
      }
      // history: per-day heart-rate histograms before the raw samples given, stored rows before `first`
      var hist: [String: Any] = [:]
      for r in try Row.fetchAll(db, sql: "SELECT date, hist FROM hr_day_hist WHERE date >= ? AND date < ?",
                                arguments: [LocalDay.add(first, -366), keepDay]) {
        if let h = (r["hist"] as Data?).flatMap({ try? JSONSerialization.jsonObject(with: $0) }) { hist[r["date"]] = h }
      }
      var seeds: [[String: Any]] = []
      for r in try Row.fetchAll(db, sql: "SELECT date, payload FROM daily_scores WHERE date >= ? AND date < ? ORDER BY date",
                                arguments: [LocalDay.add(first, -seedDays), first]) {
        guard var p = (r["payload"] as Data?).flatMap({ try? JSONSerialization.jsonObject(with: $0) as? [String: Any] }) else { continue }
        p["date"] = r["date"] as String
        seeds.append(p)
      }
      if samples.isEmpty && rhr.isEmpty && sessions.isEmpty { return nil }
      var src: [String: Any] = ["tz": ctx.tz, "hrv_kind": ctx.hrvKind, "sleep_sessions": sessions, "samples": samples,
                                "segments": segments, "daily_rhr": rhr, "hr_hist": hist, "seed_rows": seeds]
      if let b = ctx.birthDate { src["birth_date"] = b }
      if !ctx.muscle.isEmpty { src["muscle"] = ctx.muscle }
      if let e = ctx.muscleError { src["muscle_error"] = e }
      return (src, unsettled)
    }
  }

  /// A session of the sleep stream (UTC seconds) as the bundle's sleep_sessions row (milliseconds).
  static func serverSession(_ p: [String: Any], wake: String) -> [String: Any] {
    var s: [String: Any] = ["wake_date": wake, "source": p["source"] ?? NSNull(), "device": p["device"] ?? NSNull()]
    for k in ["asleep_min", "core_min", "deep_min", "rem_min", "awake_min"] { s[k] = p[k] ?? NSNull() }
    s["start"] = (num(p["start"]) ?? 0) * 1000
    s["end"] = (num(p["end"]) ?? 0) * 1000
    return s
  }

  struct Segment: Equatable {
    var start: Double
    var end: Double
    var stage: String
    var json: [String: Any] { ["start": start * 1000, "end": end * 1000, "stage": stage] }
  }

  /// Watch stage samples → sessions: a gap of more than an hour starts a new one.
  static func groupSessions(_ segs: [Segment]) -> [[Segment]] {
    var out: [[Segment]] = []
    for s in segs.sorted(by: { $0.start < $1.start }) {
      if let last = out.last?.map(\.end).max(), s.start - last <= sessionGap { out[out.count - 1].append(s) }
      else { out.append([s]) }
    }
    return out
  }

  /// One `sleep_sessions` row of the bundle from stage segments (awake is not asleep).
  static func session(_ segs: [Segment], start: Double, end: Double, wake: String, source: String?) -> [String: Any] {
    var mins: [String: Double] = [:]
    for s in segs { mins[s.stage, default: 0] += (s.end - s.start) / 60 }
    let asleep = (mins["core"] ?? 0) + (mins["deep"] ?? 0) + (mins["rem"] ?? 0) + (mins["unspecified"] ?? 0)
    return ["wake_date": wake, "start": start * 1000, "end": end * 1000, "asleep_min": asleep,
            "core_min": mins["core"] ?? 0, "deep_min": mins["deep"] ?? 0,
            "rem_min": mins["rem"] ?? 0, "awake_min": mins["awake"] ?? 0,
            "source": source ?? "phone", "device": "Apple Watch"]
  }

  static func num(_ v: Any?) -> Double? { LocalRawStore.num(v) }
}

/// Local calendar days (`YYYY-MM-DD` in the user's zone).
enum LocalDay {
  static func string(_ d: Date, _ zone: TimeZone) -> String {
    var cal = Calendar(identifier: .gregorian)
    cal.timeZone = zone
    let c = cal.dateComponents([.year, .month, .day], from: d)
    return String(format: "%04d-%02d-%02d", c.year ?? 0, c.month ?? 0, c.day ?? 0)
  }

  /// Noon of that day (safe against DST edges when adding whole days).
  static func parse(_ s: String, _ zone: TimeZone) -> Date? {
    let p = s.prefix(10).split(separator: "-").compactMap { Int($0) }
    guard p.count == 3 else { return nil }
    var cal = Calendar(identifier: .gregorian)
    cal.timeZone = zone
    return cal.date(from: DateComponents(year: p[0], month: p[1], day: p[2], hour: 12))
  }

  static func add(_ s: String, _ days: Int) -> String {
    let utc = TimeZone(identifier: "UTC")!
    guard let d = parse(s, utc) else { return s }
    return string(d.addingTimeInterval(Double(days) * 86_400), utc)
  }
}
