import Foundation
import GRDB

/// Today's scores computed on the phone (#340: "算法在手机上算"): the local raw rows (`hk_samples`, `sleep_sessions`,
/// `daily_metrics`) → `OpenBodyScore.buildInputs` → `computeDays` in JavaScriptCore → `daily_scores` (origin `app`).
///
/// What it can and cannot do yet (docs/ios-native-app.md section 9.1):
/// - It needs the raw samples on the phone. The sync engine starts writing `hk_samples` in N8 and N2's full restore
///   brings history back; until then the tables are empty and this does nothing (the Today page shows the server's
///   numbers, as before).
/// - Only `hrv_kind = sdnn` can be computed here: `rmssd` needs the beat-to-beat series, which has no local table yet.
/// - Baselines need history: recovery a 60-day baseline (≥ 14 nights), CTL / ATL from the scoring origin, HRmax 365 days.
///   With a short local history the numbers differ from the server's, so a local row never replaces a server row; the
///   page uses it only for a day the server has no score for (offline, not synced yet).
enum LocalScoring {
  /// HealthKit identifiers → the bundle's sample kinds.
  static let sampleKinds: [String: String] = [
    "HKQuantityTypeIdentifierHeartRate": "hr",
    "HKQuantityTypeIdentifierHeartRateVariabilitySDNN": "hrv",
    "HKQuantityTypeIdentifierRespiratoryRate": "resp",
    "HKQuantityTypeIdentifierOxygenSaturation": "spo2",
    "HKQuantityTypeIdentifierAppleSleepingWristTemperature": "temp",
  ]
  static let restingHR = "HKQuantityTypeIdentifierRestingHeartRate"
  static let sleepAnalysis = "HKCategoryTypeIdentifierSleepAnalysis"
  /// HKCategoryValueSleepAnalysis → stage (0 = in bed is not sleep).
  static let sleepStages: [Int: String] = [1: "unspecified", 2: "awake", 3: "core", 4: "deep", 5: "rem"]
  /// Samples older than this many days before the first scored day are not read (HRmax window of the engine).
  static let historyDays = 365
  /// Gap that starts a new sleep session (importers/common.py).
  static let sessionGap: TimeInterval = 60 * 60

  struct Result: Equatable {
    var written: [String] = []
    var skipped: String?
  }

  /// Is there anything to compute from? (cheap; the page checks this before loading the bundle)
  static func hasRawData(_ db: AppDatabase) -> Bool {
    (try? db.writer.read { db in
      try Bool.fetchOne(db, sql: "SELECT EXISTS (SELECT 1 FROM hk_samples WHERE type = ? LIMIT 1)", arguments: [restingHR])
    }) ?? false
  }

  /// Computes `days` days up to `today` and writes them as origin `app`, never over a server row.
  @discardableResult
  static func run(db: AppDatabase, runtime: OpenBodyScoreRuntime, tz: String, hrvKind: String, today: String,
                  days: Int = 3, birthDate: String? = nil) throws -> Result {
    guard hrvKind == "sdnn" else { return Result(skipped: "hrv_kind \(hrvKind) needs beat-to-beat series") }
    guard let zone = TimeZone(identifier: tz), let todayDate = LocalDay.parse(today, zone) else { return Result(skipped: "bad day") }
    let since = todayDate.addingTimeInterval(-Double(historyDays + days) * 86_400).timeIntervalSince1970
    guard let source = try source(db: db, tz: tz, zone: zone, since: since, hrvKind: hrvKind, birthDate: birthDate),
          !(source["daily_rhr"] as? [String: Double] ?? [:]).isEmpty else {
      return Result(skipped: "no local resting heart rate")
    }
    let first = LocalDay.string(todayDate.addingTimeInterval(-Double(days - 1) * 86_400), zone)
    // The engine computes from the scoring origin so the baselines are the full local history; we keep the last days.
    let rows = try runtime.scoreDays(source: source, first: nil, last: today, today: today).filter {
      ($0["date"] as? String ?? "") >= first
    }
    var out = Result()
    try db.writer.write { db in
      for row in rows {
        guard let day = row["date"] as? String else { continue }
        if let existing = try DailyScore.fetchOne(db, key: day), existing.origin != "app" { continue }
        let payload = try JSONSerialization.data(withJSONObject: row, options: [.sortedKeys])
        try DailyScore(date: day, recovery: num(row["recovery"]), sleepScore: num(row["sleep_score"]),
                       strain: num(row["strain"]), hrvMs: num(row["hrv_night_ms"]), rhr: num(row["rhr_night"]),
                       payload: payload, algoVersion: row["algo_version"] as? String, origin: "app",
                       computedAt: Date().timeIntervalSince1970).save(db)
        out.written.append(day)
      }
    }
    return out
  }

  /// The bundle's `buildInputs` source from the local tables (nil when there is nothing).
  static func source(db: AppDatabase, tz: String, zone: TimeZone, since: Double, hrvKind: String,
                     birthDate: String?) throws -> [String: Any]? {
    try db.writer.read { db -> [String: Any]? in
      var samples: [[String: Any]] = []
      let kinds = sampleKinds.filter { hrvKind == "sdnn" || $0.value != "hrv" }
      var args: [(any DatabaseValueConvertible)?] = kinds.keys.map { $0 }
      args.append(since)
      let rows = try Row.fetchAll(db, sql: """
        SELECT type, start, value FROM hk_samples WHERE type IN (\(kinds.keys.map { _ in "?" }.joined(separator: ",")))
        AND start >= ? ORDER BY start
        """, arguments: StatementArguments(args))
      for r in rows {
        guard let kind = kinds[r["type"] as String] else { continue }
        samples.append(["kind": kind, "t": (r["start"] as Double) * 1000, "value": r["value"] as Double])
      }
      // resting heart rate per local day: the server's daily_metrics first, then the watch's own samples (last of the day)
      var rhr: [String: Double] = [:]
      for r in try Row.fetchAll(db, sql: "SELECT start, value FROM hk_samples WHERE type = ? AND start >= ? ORDER BY start",
                                arguments: [restingHR, since]) {
        rhr[LocalDay.string(Date(timeIntervalSince1970: r["start"]), zone)] = r["value"]
      }
      for r in try Row.fetchAll(db, sql: "SELECT date, value FROM daily_metrics WHERE metric = 'resting_heart_rate' AND value IS NOT NULL") {
        rhr[r["date"]] = r["value"]
      }
      // sleep: the synced sessions when there are any, else sessions built from the watch's stage samples
      var segments: [[String: Any]] = []
      var sessions: [[String: Any]] = []
      let stored = try Row.fetchAll(db, sql: "SELECT date, start, end, stages, source FROM sleep_sessions WHERE end >= ? ORDER BY start",
                                    arguments: [since])
      if !stored.isEmpty {
        for r in stored {
          let stages = (r["stages"] as Data?).flatMap { try? JSONSerialization.jsonObject(with: $0) as? [[Any]] } ?? []
          var segs: [Segment] = []
          for s in stages where s.count >= 3 {
            guard let st = s[0] as? String, let a = TodayModel.num(s[1]), let b = TodayModel.num(s[2]) else { continue }
            segs.append(Segment(start: a, end: b, stage: st))
          }
          sessions.append(session(segs, start: r["start"], end: r["end"], wake: r["date"], source: r["source"]))
          segments += segs.map(\.json)
        }
      } else {
        var segs: [Segment] = []
        for r in try Row.fetchAll(db, sql: "SELECT start, end, value FROM hk_samples WHERE type = ? AND start >= ? ORDER BY start",
                                  arguments: [sleepAnalysis, since]) {
          guard let st = sleepStages[Int(r["value"] as Double)] else { continue }
          segs.append(Segment(start: r["start"], end: r["end"], stage: st))
        }
        for group in groupSessions(segs) {
          let a = group.map(\.start).min()!, b = group.map(\.end).max()!
          sessions.append(session(group, start: a, end: b, wake: LocalDay.string(Date(timeIntervalSince1970: b - 4 * 3600), zone),
                                  source: "app"))
        }
        segments = segs.map(\.json)
      }
      if samples.isEmpty && rhr.isEmpty && sessions.isEmpty { return nil }
      var src: [String: Any] = ["tz": tz, "hrv_kind": hrvKind, "sleep_sessions": sessions, "samples": samples,
                                "segments": segments, "daily_rhr": rhr]
      if let birthDate { src["birth_date"] = birthDate }
      return src
    }
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

  /// One `sleep_sessions` row of the bundle: minutes per stage from the segments (awake is not asleep).
  static func session(_ segs: [Segment], start: Double, end: Double, wake: String, source: String?) -> [String: Any] {
    var mins: [String: Double] = [:]
    for s in segs { mins[s.stage, default: 0] += (s.end - s.start) / 60 }
    let asleep = (mins["core"] ?? 0) + (mins["deep"] ?? 0) + (mins["rem"] ?? 0) + (mins["unspecified"] ?? 0)
    return ["wake_date": wake, "start": start * 1000, "end": end * 1000, "asleep_min": asleep.rounded(),
            "core_min": (mins["core"] ?? 0).rounded(), "deep_min": (mins["deep"] ?? 0).rounded(),
            "rem_min": (mins["rem"] ?? 0).rounded(), "awake_min": (mins["awake"] ?? 0).rounded(),
            "source": source ?? "app", "device": "Apple Watch"]
  }

  private static func num(_ v: Any?) -> Double? { TodayModel.num(v) }
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
