import Foundation
import GRDB

/// The raw samples the phone scores from (docs/ios-native-app.md section 9.1, #340 N2b), kept small:
/// - `hk_samples`: the scoring types only (heart rate, HRV SDNN, respiration, SpO2, sleeping wrist temperature, resting
///   heart rate, sleep stages), for the last `keepDays` days. Written from HealthKit by the host (`HealthKitScoringReader`)
///   and, after a reinstall, from the server's raw stream (`DeltaRemote`).
/// - `heartbeat_series`: beat-to-beat readings (RMSSD users), stored when the sync uploads them (`RawUploadPass`) or pulled
///   with the server's RMSSD; the beats themselves are dropped once the bundle has analysed them.
/// - `hr_day_hist`: per-day heart-rate histograms {bpm: count} for the scores' 365-day HRmax window, kept after the
///   samples of that day are pruned (from the phone's samples, or once from `GET /v1/sync/scoring?hist_from=`).
/// - `score_dirty`: the local days whose inputs changed and have to be scored again.
/// Size: about 1,000–2,000 heart-rate samples a day dominate (~200 bytes a row with its indexes): 60 days ≈ 12–25 MB; the
/// histograms and the rest are well under 1 MB (measured figures in the doc).
final class LocalRawStore {
  static let keepDays = 60
  static let heartRate = "HKQuantityTypeIdentifierHeartRate"
  static let sleepAnalysis = "HKCategoryTypeIdentifierSleepAnalysis"
  static let restingHR = "HKQuantityTypeIdentifierRestingHeartRate"
  /// HealthKit identifier → HealthKit unit string the value is stored in (the server's registry units; SpO2 as a fraction,
  /// which the bundle turns into percent).
  static let types: [String: String] = [
    heartRate: "count/min",
    "HKQuantityTypeIdentifierHeartRateVariabilitySDNN": "ms",
    "HKQuantityTypeIdentifierRespiratoryRate": "count/min",
    "HKQuantityTypeIdentifierOxygenSaturation": "%",
    "HKQuantityTypeIdentifierAppleSleepingWristTemperature": "degC",
    restingHR: "count/min",
    sleepAnalysis: "",
  ]
  /// A sleep belongs to the day of its end minus 4 hours (the server's rule, importers/common.py).
  static let wakeShift: TimeInterval = 4 * 3600

  struct Sample: Equatable {
    var uuid: String
    var type: String
    var start: Double
    var end: Double
    var value: Double
  }

  let db: AppDatabase
  var zone: TimeZone

  init(db: AppDatabase, zone: TimeZone = .current) {
    self.db = db
    self.zone = zone
  }

  /// A JSON number (not a boolean) as a finite Double.
  static func num(_ v: Any?) -> Double? {
    guard let n = v as? NSNumber, CFGetTypeID(n) != CFBooleanGetTypeID() else { return nil }
    let d = n.doubleValue
    return d.isFinite ? d : nil
  }

  func day(_ t: Double) -> String { LocalDay.string(Date(timeIntervalSince1970: t), zone) }

  /// The local day a sample changes the scores of.
  func scoringDay(_ s: Sample) -> String {
    s.type == Self.sleepAnalysis ? day(s.end - Self.wakeShift) : day(s.start)
  }

  // MARK: - writing

  /// New / changed samples and HealthKit deletions → `hk_samples`, histograms of the touched days, `score_dirty`.
  /// Returns the dirty days.
  @discardableResult
  func ingest(_ samples: [Sample], deleted: [String] = []) throws -> Set<String> {
    guard !samples.isEmpty || !deleted.isEmpty else { return [] }
    let tz = zone.identifier
    return try db.writer.write { db -> Set<String> in
      var days = Set<String>(), hrDays = Set<String>()
      for s in samples where Self.types[s.type] != nil {
        try db.execute(sql: """
          INSERT INTO hk_samples (uuid, type, start, end, value, tz, uploaded) VALUES (?, ?, ?, ?, ?, ?, 1)
          ON CONFLICT(uuid) DO UPDATE SET type = excluded.type, start = excluded.start, end = excluded.end, value = excluded.value
          """, arguments: [s.uuid, s.type, s.start, s.end, s.value, tz])
        days.insert(scoringDay(s))
        if s.type == Self.heartRate { hrDays.insert(day(s.start)) }
      }
      for uuid in deleted {
        guard let r = try Row.fetchOne(db, sql: "SELECT type, start, end FROM hk_samples WHERE uuid = ?", arguments: [uuid]) else {
          continue
        }
        let s = Sample(uuid: uuid, type: r["type"], start: r["start"], end: r["end"], value: 0)
        days.insert(scoringDay(s))
        if s.type == Self.heartRate { hrDays.insert(day(s.start)) }
        try db.execute(sql: "DELETE FROM hk_samples WHERE uuid = ?", arguments: [uuid])
      }
      try refreshHistograms(db, days: hrDays)
      try Self.markDirty(db, days)
      return days
    }
  }

  /// Beat-to-beat readings as the sync uploads them (RawSync: `{uuid, start, end, beats: [s], gaps: [bool]}`, ISO times).
  @discardableResult
  func ingestBeats(_ items: [[String: Any]], deleted: [String] = []) throws -> Set<String> {
    guard !items.isEmpty || !deleted.isEmpty else { return [] }
    return try db.writer.write { db -> Set<String> in
      var days = Set<String>()
      for it in items {
        guard let uuid = it["uuid"] as? String, let a = (it["start"] as? String).flatMap(ISO8601.parse),
              let b = (it["end"] as? String).flatMap(ISO8601.parse), let beats = it["beats"] as? [Double], beats.count >= 2 else {
          continue
        }
        let start = floor(a.timeIntervalSince1970)   // whole seconds, like the server's start_at
        let blob = try JSONSerialization.data(withJSONObject: ["t": beats, "gaps": it["gaps"] as? [Bool] ?? []])
        try db.execute(sql: """
          INSERT INTO heartbeat_series (uuid, start, end, beats, origin) VALUES (?, ?, ?, ?, 'phone')
          ON CONFLICT(uuid) DO UPDATE SET start = excluded.start, end = excluded.end, beats = excluded.beats,
            rmssd = NULL, sdnn = NULL, origin = 'phone'
          """, arguments: [uuid.lowercased(), start, b.timeIntervalSince1970, blob])
        days.insert(day(start))
      }
      for uuid in deleted {
        if let t = try Double.fetchOne(db, sql: "SELECT start FROM heartbeat_series WHERE uuid = ?", arguments: [uuid.lowercased()]) {
          days.insert(day(t))
          try db.execute(sql: "DELETE FROM heartbeat_series WHERE uuid = ?", arguments: [uuid.lowercased()])
        }
      }
      try Self.markDirty(db, days)
      return days
    }
  }

  /// RMSSD / SDNN of the readings the phone has not analysed yet (the bundle's `analyzeHrv`, the server's rr-v1); the
  /// beats are dropped afterwards. Returns how many were analysed.
  @discardableResult
  func analyzeBeats(_ runtime: OpenBodyScoreRuntime) throws -> Int {
    let rows = try db.writer.read { db in
      try Row.fetchAll(db, sql: "SELECT uuid, beats FROM heartbeat_series WHERE beats IS NOT NULL")
    }
    var n = 0
    for r in rows {
      let uuid: String = r["uuid"]
      guard let data = r["beats"] as Data?, let j = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
            let t = j["t"] as? [Double] else { continue }
      let gaps = j["gaps"] as? [Bool] ?? []
      let out = (try? runtime.invoke("analyzeHrv", [t, gaps])) as? [String: Any]
      try db.writer.write { db in
        try db.execute(sql: "UPDATE heartbeat_series SET rmssd = ?, sdnn = ?, beats = NULL WHERE uuid = ?",
                       arguments: [Self.num(out?["rmssd"]), Self.num(out?["sdnn"]), uuid])
      }
      n += 1
    }
    return n
  }

  /// The phone's own histograms of these days, from `hk_samples` (one sample per start time; Python's round()).
  func refreshHistograms(_ db: Database, days: Set<String>) throws {
    for d in days {
      guard let noon = LocalDay.parse(d, zone) else { continue }
      var cal = Calendar(identifier: .gregorian)
      cal.timeZone = zone
      let lo = cal.startOfDay(for: noon), hi = cal.date(byAdding: .day, value: 1, to: lo)!
      var seen = Set<Double>(), hist: [String: Int] = [:]
      for r in try Row.fetchAll(db, sql: "SELECT start, value FROM hk_samples WHERE type = ? AND start >= ? AND start < ? ORDER BY start, uuid",
                                arguments: [Self.heartRate, lo.timeIntervalSince1970, hi.timeIntervalSince1970]) {
        let t: Double = r["start"]
        guard seen.insert(t).inserted else { continue }
        let v: Double = r["value"]
        hist[String(Int(v.rounded(.toNearestOrEven))), default: 0] += 1
      }
      if hist.isEmpty {
        try db.execute(sql: "DELETE FROM hr_day_hist WHERE date = ? AND source = 'phone'", arguments: [d])
        continue
      }
      try db.execute(sql: """
        INSERT INTO hr_day_hist (date, hist, source, updated_at) VALUES (?, ?, 'phone', ?)
        ON CONFLICT(date) DO UPDATE SET hist = excluded.hist, source = 'phone', updated_at = excluded.updated_at
        """, arguments: [d, try JSONSerialization.data(withJSONObject: hist, options: [.sortedKeys]), Date().timeIntervalSince1970])
    }
  }

  /// The server's histograms (`GET /v1/sync/scoring` → `hr_hist.days`): only for days the phone has none of its own.
  func storeServerHistograms(_ days: [String: Any]) throws {
    try db.writer.write { db in
      for (d, h) in days {
        guard let h = h as? [String: Any], !h.isEmpty else { continue }
        try db.execute(sql: """
          INSERT INTO hr_day_hist (date, hist, source, updated_at) VALUES (?, ?, 'server', ?) ON CONFLICT(date) DO NOTHING
          """, arguments: [d, try JSONSerialization.data(withJSONObject: h, options: [.sortedKeys]), Date().timeIntervalSince1970])
      }
    }
  }

  static func markDirty(_ db: Database, _ days: Set<String>) throws {
    let t = Date().timeIntervalSince1970
    for d in days {
      try db.execute(sql: "INSERT INTO score_dirty (date, at) VALUES (?, ?) ON CONFLICT(date) DO UPDATE SET at = excluded.at",
                     arguments: [d, t])
    }
  }

  // MARK: - housekeeping

  /// Drops raw samples and readings older than `keepDays` before `today` (their histograms stay) and dirty marks older
  /// than that (those days are the server's to score).
  func prune(today: String) throws {
    guard let noon = LocalDay.parse(today, zone) else { return }
    let cut = noon.addingTimeInterval(-Double(Self.keepDays + 2) * 86_400).timeIntervalSince1970
    let cutDay = LocalDay.string(Date(timeIntervalSince1970: cut), zone)
    let histCut = LocalDay.add(today, -430)
    try db.writer.write { db in
      try db.execute(sql: "DELETE FROM hk_samples WHERE start < ?", arguments: [cut])
      try db.execute(sql: "DELETE FROM heartbeat_series WHERE start < ?", arguments: [cut])
      try db.execute(sql: "DELETE FROM score_dirty WHERE date < ?", arguments: [cutDay])
      try db.execute(sql: "DELETE FROM hr_day_hist WHERE date < ?", arguments: [histCut])
    }
  }

  /// The first day the phone keeps raw samples of in full.
  static func rawStart(today: String) -> String { LocalDay.add(today, -(keepDays - 1)) }

  /// What the tables take now (the sync center / the doc's size figures).
  func footprint() throws -> [String: Int] {
    try db.writer.read { db in
      var out: [String: Int] = [:]
      for t in ["hk_samples", "heartbeat_series", "hr_day_hist", "daily_scores", "daily_metrics", "sleep_sessions"] {
        out[t] = try Int.fetchOne(db, sql: "SELECT count(*) FROM \(t)") ?? 0
      }
      return out
    }
  }
}
