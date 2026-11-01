import Foundation
import GRDB

/// The compact history of the scores' raw inputs (#340 local history; docs/ios-native-app.md section 9.1 「本机完整历史」):
/// every day before the hot window, one row per (kind, instant) in `hist_samples`, the nights' stage segments in
/// `hist_segments`, which days it holds in `hist_days`. Two writers:
/// - the server's history (stream raw_history, `HistoryDownload`): a day is replaced as a whole with what the server's
///   scoring merges from every source (Open Wearables, the Shortcut, an export.zip, the app's own uploads);
/// - the hot window (`prune`): a day leaving `hk_samples` moves here unless the server's history holds it already.
/// Readers: `LocalScoring.source` (before `hotFrom`), the histograms of the HRmax window, the data status page.
extension LocalRawStore {
  typealias Kind = LocalHistoryParams.Kind

  /// HealthKit identifier → history kind (resting heart rate and sleep stages are not samples of the history: the daily
  /// resting heart rate comes with the daily stream, the stages become segments).
  static let historyKinds: [String: Kind] = [
    heartRate: .hr,
    "HKQuantityTypeIdentifierHeartRateVariabilitySDNN": .hrv,
    "HKQuantityTypeIdentifierRespiratoryRate": .resp,
    "HKQuantityTypeIdentifierOxygenSaturation": .spo2,
    "HKQuantityTypeIdentifierAppleSleepingWristTemperature": .temp,
  ]

  // MARK: - the server's history

  /// One day of the raw_history stream (body_engine nativesync/history.py) replaces that day of the history; nil = the
  /// day has no inputs any more. Inside the caller's transaction. Returns the rows stored.
  @discardableResult
  func storeHistoryDay(_ db: Database, day: String, body: [String: Any]?) throws -> Int {
    guard let lo = dayStart(day), let hi = dayStart(LocalDay.add(day, 1)) else { return 0 }
    let kinds = Kind.allCases.map { String($0.rawValue) }.joined(separator: ",")
    try db.execute(sql: "DELETE FROM hist_samples WHERE kind IN (\(kinds)) AND t >= ? AND t < ?", arguments: [lo, hi])
    try db.execute(sql: "DELETE FROM hist_segments WHERE date = ?", arguments: [day])
    guard let body else {
      try db.execute(sql: "DELETE FROM hist_days WHERE date = ?", arguments: [day])
      try db.execute(sql: "DELETE FROM heartbeat_series WHERE origin = 'server' AND start >= ? AND start < ?", arguments: [lo, hi])
      try storeHistogram(db, day: day, [:], source: "history")
      return 0
    }
    let t0 = Self.num(body["t0"]) ?? lo
    var rows = 0
    var hr: [(Double, Double)] = []
    let insert = try db.makeStatement(sql: "INSERT OR IGNORE INTO hist_samples (kind, t, value) VALUES (?, ?, ?)")
    for (name, list) in (body["inputs"] as? [String: Any]) ?? [:] {
      guard let kind = Kind(name: name), let pairs = list as? [[Any]] else { continue }
      for p in pairs where p.count >= 2 {
        guard let dt = Self.num(p[0]), let v = Self.num(p[1]) else { continue }
        let t = t0 + dt
        try insert.execute(arguments: [kind.rawValue, t, v])
        guard db.changesCount > 0 else { continue }   // the same instant twice: the first one counts
        if kind == .hr { hr.append((t, v)) }
        rows += 1
      }
    }
    for s in (body["segments"] as? [[Any]]) ?? [] where s.count >= 3 {
      guard let a = Self.num(s[0]), let b = Self.num(s[1]), let st = s[2] as? String else { continue }
      try db.execute(sql: "INSERT OR REPLACE INTO hist_segments (start, end, stage, date) VALUES (?, ?, ?, ?)",
                     arguments: [a, b, st, day])
      rows += 1
    }
    if let beats = body["beats"] as? [String: Any], let cols = beats["cols"] as? [String], let list = beats["rows"] as? [[Any]] {
      let ix = Dictionary(uniqueKeysWithValues: cols.enumerated().map { ($1, $0) })
      func col(_ r: [Any], _ k: String) -> Any? { ix[k].flatMap { $0 < r.count ? r[$0] : nil } }
      for r in list {
        guard let a = Self.num(col(r, "start")), let e = Self.num(col(r, "end")) else { continue }
        // readings from an export.xml have no HealthKit uuid on the server
        let uuid = (col(r, "uuid") as? String)?.lowercased() ?? "x:\(Int(a))"
        let ms = (col(r, "beats_ms") as? [Any])?.compactMap(Self.num) ?? []
        let blob = ms.count >= 2
          ? try JSONSerialization.data(withJSONObject: ["ms": ms, "gaps": col(r, "gaps") as? [Bool] ?? []]) : nil
        try db.execute(sql: """
          INSERT INTO heartbeat_series (uuid, start, end, rmssd, sdnn, beats, origin, analyzed) VALUES (?, ?, ?, ?, ?, ?, 'server', 1)
          ON CONFLICT(uuid) DO UPDATE SET beats = coalesce(heartbeat_series.beats, excluded.beats)
          """, arguments: [uuid, a, e, Self.num(col(r, "rmssd")), Self.num(col(r, "sdnn")), blob])
        rows += 1
      }
    }
    hr.sort { $0.0 < $1.0 }
    try storeHistogram(db, day: day, Self.histogram(hr), source: "history")
    try db.execute(sql: """
      INSERT INTO hist_days (date, source, rows, complete, fetched_at) VALUES (?, 'server', ?, ?, ?)
      ON CONFLICT(date) DO UPDATE SET source = 'server', rows = excluded.rows, complete = excluded.complete,
        fetched_at = excluded.fetched_at
      """, arguments: [day, rows, body["complete"] as? Bool ?? true, Date().timeIntervalSince1970])
    return rows
  }

  // MARK: - the hot window rolling over

  /// The hot samples before `cut` (UTC seconds) whose days the server's history does not hold yet: their scoring kinds
  /// into `hist_samples` (SpO2 in percent like the server's), their stage samples into `hist_segments`. Returns the days.
  func rollOver(_ db: Database, before cut: Double) throws -> [String] {
    let server = Set(try String.fetchAll(db, sql: "SELECT date FROM hist_days WHERE source = 'server'"))
    var byDay: [String: Int] = [:]
    let insert = try db.makeStatement(sql: "INSERT OR IGNORE INTO hist_samples (kind, t, value) VALUES (?, ?, ?)")
    let rows = try Row.fetchCursor(db, sql: "SELECT type, start, end, value FROM hk_samples WHERE start < ? ORDER BY start, uuid",
                                   arguments: [cut])
    var stages: [LocalScoring.Segment] = []
    while let r = try rows.next() {
      let type: String = r["type"], start: Double = r["start"], end: Double = r["end"], value: Double = r["value"]
      if type == Self.sleepAnalysis {
        if let stage = LocalScoring.sleepStages[Int(value)] { stages.append(.init(start: start, end: end, stage: stage)) }
        continue
      }
      guard let kind = Self.historyKinds[type] else { continue }
      let d = day(start)
      guard !server.contains(d) else { continue }
      try insert.execute(arguments: [kind.rawValue, start, kind == .spo2 && value <= 1 ? value * 100 : value])
      byDay[d, default: 0] += 1
    }
    // 2.0.0 M1: the 15-minute step buckets of those days (kind steps, t = the bucket's start)
    let steps = try Row.fetchCursor(db, sql: "SELECT start, value FROM hk_stat_buckets WHERE type = ? AND start < ? ORDER BY start",
                                    arguments: [Self.stepCount, Int(cut)])
    while let r = try steps.next() {
      let start = Double(r["start"] as Int), value: Double = r["value"]
      let d = day(start)
      guard !server.contains(d), value > 0 else { continue }
      try insert.execute(arguments: [Kind.steps.rawValue, start, value])
      byDay[d, default: 0] += 1
    }
    // a night's segments all go to its wake day (end of the night − 4 h), the day of its sleep_sessions row
    for night in LocalScoring.groupSessions(stages) {
      let d = day(night.map(\.end).max()! - Self.wakeShift)
      guard !server.contains(d) else { continue }
      for s in night {
        try db.execute(sql: "INSERT OR IGNORE INTO hist_segments (start, end, stage, date) VALUES (?, ?, ?, ?)",
                       arguments: [s.start, s.end, s.stage, d])
      }
      byDay[d, default: 0] += night.count
    }
    let now = Date().timeIntervalSince1970
    for (d, n) in byDay {
      try db.execute(sql: """
        INSERT INTO hist_days (date, source, rows, complete, fetched_at) VALUES (?, 'phone', ?, 1, ?)
        ON CONFLICT(date) DO UPDATE SET rows = hist_days.rows + excluded.rows WHERE hist_days.source = 'phone'
        """, arguments: [d, n, now])
    }
    return byDay.keys.sorted()
  }

  /// LocalHistoryParams' per-kind limits (all nil today: nothing is dropped by age).
  func applyKeepLimits(_ db: Database, today: String) throws {
    for (kind, keep) in LocalHistoryParams.keepDays {
      guard let keep, let cut = dayStart(LocalDay.add(today, -keep)) else { continue }
      try db.execute(sql: "DELETE FROM hist_samples WHERE kind = ? AND t < ?", arguments: [kind.rawValue, cut])
    }
    if let keep = LocalHistoryParams.segmentKeepDays {
      try db.execute(sql: "DELETE FROM hist_segments WHERE date < ?", arguments: [LocalDay.add(today, -keep)])
    }
    if let keep = LocalHistoryParams.beatsKeepDays, let cut = dayStart(LocalDay.add(today, -keep)) {
      try db.execute(sql: "UPDATE heartbeat_series SET beats = NULL WHERE start < ? AND analyzed = 1", arguments: [cut])
    }
    if let keep = LocalHistoryParams.histogramKeepDays {
      try db.execute(sql: "DELETE FROM hr_day_hist WHERE date < ?", arguments: [LocalDay.add(today, -keep)])
    }
  }

  // MARK: - reading

  /// The oldest day the history (or, without it, the hot window) holds, and whether the server's download is complete.
  func coverage(today: String) -> (oldest: String, complete: Bool) {
    let hot = hotFrom(today: today)
    let oldest = (try? db.writer.read { db in try String.fetchOne(db, sql: "SELECT min(date) FROM hist_days") }) ?? nil
    return (min(oldest ?? hot, hot), HistoryDownload.isComplete(db))
  }
}
