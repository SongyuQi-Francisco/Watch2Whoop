import Foundation
import GRDB

/// M2 (docs/ios-native-2.0.md section 4): the day's stress computed on the phone with the bundle's `stressLocal`
/// (packages/openbodyscore-js src/32_stress.js, the twin of the server's insights/stress.days_local): while awake and
/// still, how far heart rate / HRV is from the person's calm baseline of the 14 days before. Runs after the scores, since
/// it reads their night HRV (`hrv_night_ln`), and before the pages' Trends cards (TrendsLocal), which read `stress_daily`.
///
/// One run:
/// 1. which days: every day after a new stress algorithm (or the first run); otherwise the last `recentDays` and every day
///    whose heart-rate samples changed since it was computed (history downloaded later, a deleted sample), each with the
///    14 days after it (their calm baseline includes it);
/// 2. per chunk the bundle gets the phone's own HealthKit samples (heart rate, HRV, raw steps per source; `hk_samples`)
///    and, on days without any of a type, the history area's (`hist_samples`: the server's merged inputs, steps as
///    15-minute buckets): the server's sync-mode-`app` rule (insights/stress.merge_app); the sleep sessions and workouts
///    as windows to leave out; the night HRV of the 60 days before; the Shortcut-only days (a flag text);
/// 3. the rows go to `stress_daily` (shadow and primary: the phone's own table; pages read it only in primary) and to
///    `derived_rows` (kind `stress`), uploaded like DeriveStage's once everything read from HealthKit is on the server;
/// 4. the cardio view (`cardio.local:<today>`, the server's /cardio for the last 90 days) from the local score rows and,
///    once M1c has them on the phone, its `workout_hrr` rows.
struct StressStage: ComputeStage {
  static let name = "stress"
  static let package = "stress"
  static let implemented = true
  static let kind = "stress"
  /// Today and the 15 days before: a new sample reaches the calm baseline of the 14 days after its own.
  static let recentDays = 16
  /// insights/stress BASELINE_DAYS (calm baseline) and the 60 nights of HRV (score_range): what a chunk reads before it.
  static let baselineDays = 14
  static let hrvNightDays = 60
  /// Days per bundle call (heart rate every few minutes: ~200–300 samples a day plus 14 days before the chunk).
  static let chunkDays = 30
  /// The cardio view's range (cardio/views.window: 90 days).
  static let cardioDays = 90
  /// meta: the stress algorithm last used.
  static let algoKey = "stress.algo"
  static func cardioDoc(_ day: String) -> String { "cardio.local:\(day)" }

  static let heartRate = LocalRawStore.heartRate
  static let hrvType = "HKQuantityTypeIdentifierHeartRateVariabilitySDNN"
  static let stepType = "HKQuantityTypeIdentifierStepCount"

  // MARK: - host hooks (tests replace them)

  /// true when the server has everything this phone read from HealthKit (as ScoringSync's upload gate).
  static var uploadGate: () async -> Bool = { await MainActor.run { HealthSyncService.shared.uploadedEverything } }
  /// The algorithm bundle in use (the signed download when there is one, else the built-in copy).
  static var runtime: (AppDatabase) throws -> OpenBodyScoreRuntime = { try AlgoBundles(db: $0).runtime() }

  // MARK: - one run

  func run(_ ctx: ComputeContext) async -> ComputeStageReport {
    var rep = ComputeStageReport(stage: Self.name, state: "done")
    let db = ctx.db, zone = ScoringSync.zone(db)
    let store = LocalRawStore(db: db, zone: zone)
    let today = LocalDay.string(ctx.now, zone)
    let rt: OpenBodyScoreRuntime
    do { rt = try Self.runtime(db) } catch {
      rep.state = "failed"
      rep.detail = "bundle: \(error)"
      return rep
    }
    guard let algo = rt.algoVersions["stress_cards"] else {
      rep.state = "skipped"
      rep.detail = "bundle without stress_cards"
      return rep
    }
    do {
      let days = try Self.days(db: db, store: store, today: today, algo: algo)
      for chunk in Self.chunks(days) {
        rep.written += try Self.computeChunk(db: db, store: store, runtime: rt, days: chunk)
      }
      try db.setMeta(Self.algoKey, algo)
      try Self.cardio(db: db, runtime: rt, today: today, tz: zone.identifier)
    } catch {
      rep.state = "failed"
      rep.detail = String(describing: error)
      return rep
    }
    let pending = (try? await db.writer.read { db in
      try Bool.fetchOne(db, sql: "SELECT EXISTS (SELECT 1 FROM derived_rows WHERE kind = ? AND uploaded = 0)", arguments: [Self.kind])
    }) ?? false
    guard pending else { return rep }
    guard await Self.uploadGate() else {
      rep.state = "waiting_sync"
      return rep
    }
    let (sent, status) = await DeriveStage.upload(kind: Self.kind, db: db, api: ctx.api, algo: algo, bundle: rt.version)
    rep.uploaded += sent.map { "\(Self.kind):\($0)" }
    if let status, status != "ok" { rep.detail = "\(Self.kind): \(status)" }
    return rep
  }

  // MARK: - which days

  /// The first local day with a heart-rate sample (the phone's own or the history's); nil without any.
  static func firstDay(_ db: Database, store: LocalRawStore) throws -> String? {
    let a = try Double.fetchOne(db, sql: "SELECT min(start) FROM hk_samples WHERE type = ?", arguments: [heartRate])
    let b = try Double.fetchOne(db, sql: "SELECT min(t) FROM hist_samples WHERE kind = ?", arguments: [LocalHistoryParams.Kind.hr.rawValue])
    return [a, b].compactMap { $0 }.min().map(store.day)
  }

  /// Heart-rate samples of one local day (the phone's own + the history's): a computed row remembers it (`n_hr`).
  static func heartRateCount(_ db: Database, store: LocalRawStore, day: String) throws -> Int {
    guard let lo = store.dayStart(day), let hi = store.dayStart(LocalDay.add(day, 1)) else { return 0 }
    return try Int.fetchOne(db, sql: """
      SELECT (SELECT count(*) FROM hk_samples WHERE type = ? AND start >= ? AND start < ?)
           + (SELECT count(*) FROM hist_samples WHERE kind = ? AND t >= ? AND t < ?)
      """, arguments: [heartRate, lo, hi, LocalHistoryParams.Kind.hr.rawValue, lo, hi]) ?? 0
  }

  /// Every day from the first sample after a new algorithm; else the recent days and the days whose heart rate changed
  /// (or that have no row yet), each with the 14 days after it. Sorted, within [first, today].
  static func daysToCompute(_ db: Database, store: LocalRawStore, today: String, algo: String) throws -> [String] {
    guard let first = try firstDay(db, store: store), first <= today else { return [] }
    var all: [String] = [], d = first
    while d <= today { all.append(d); d = LocalDay.add(d, 1) }
    let last = try String.fetchOne(db, sql: "SELECT value FROM meta WHERE key = ?", arguments: [algoKey])
    if last != algo { return all }
    var stored: [String: Int] = [:]
    for r in try Row.fetchAll(db, sql: "SELECT date, n_hr, algo_version FROM stress_daily WHERE date >= ?", arguments: [first])
    where (r["algo_version"] as String) == algo {
      stored[r["date"] as String] = r["n_hr"] as Int
    }
    let recentFrom = LocalDay.add(today, -(recentDays - 1))
    var days = Set(all.filter { $0 >= recentFrom })
    for day in all where day < recentFrom {
      guard try stored[day] != heartRateCount(db, store: store, day: day) else { continue }
      var x = day
      for _ in 0...baselineDays where x <= today { days.insert(x); x = LocalDay.add(x, 1) }
    }
    return days.sorted()
  }

  static func days(db: AppDatabase, store: LocalRawStore, today: String, algo: String) throws -> [String] {
    try db.writer.read { db in try daysToCompute(db, store: store, today: today, algo: algo) }
  }

  /// Sorted days → runs of consecutive days of at most `chunkDays` (a chunk reads its range plus the 14 days before).
  static func chunks(_ days: [String]) -> [[String]] {
    var out: [[String]] = []
    for d in days {
      if let last = out.last?.last, LocalDay.add(last, 1) == d, out[out.count - 1].count < chunkDays {
        out[out.count - 1].append(d)
      } else {
        out.append([d])
      }
    }
    return out
  }

  // MARK: - one chunk

  /// The local rows → `stressLocal` → `stress_daily` + `derived_rows`. Returns the "stress:<day>" whose rows changed.
  static func computeChunk(db: AppDatabase, store: LocalRawStore, runtime rt: OpenBodyScoreRuntime, days: [String]) throws -> [String] {
    let (src, counts) = try db.writer.read { db in try source(db, store: store, days: days) }
    guard !src.isEmpty, let rows = try rt.invoke("stressLocal", [src]) as? [[String: Any]] else { return [] }
    return try db.writer.write { db in try save(db, rows: rows, counts: counts, bundle: rt.version) }
  }

  /// `stressLocal`'s src for the range from the first to the last of these days, and the heart-rate count of each day.
  /// UTC seconds throughout.
  static func source(_ db: Database, store: LocalRawStore, days: [String]) throws -> ([String: Any], [String: Int]) {
    guard let a = days.first, let b = days.last, let lo = store.dayStart(LocalDay.add(a, -baselineDays)),
          let hi = store.dayStart(LocalDay.add(b, 1)) else { return ([:], [:]) }
    var app: [String: [[Any]]] = ["hr": [], "hrv": [], "steps": []]
    var counts: [String: Int] = [:]
    let groups = [heartRate: "hr", hrvType: "hrv", stepType: "steps"]
    let rows = try Row.fetchCursor(db, sql: """
      SELECT h.type, h.start, h.value, s.device_type, s.device_model, s.name FROM hk_samples h
      LEFT JOIN hk_sources s ON s.id = h.source_id
      WHERE h.type IN (?, ?, ?) AND h.start >= ? AND h.start < ? ORDER BY h.start, h.uuid
      """, arguments: [heartRate, hrvType, stepType, lo, hi])
    while let r = try rows.next() {
      guard let g = groups[r[0] as String] else { continue }
      let t: Double = r[1], v: Double = r[2]
      // the server's source key (rawdata/samples._source_key): steps are kept per source
      let key = "\((r[3] as String?) ?? "unknown"):\((r[4] as String?) ?? ""):\((r[5] as String?) ?? "")"
      app[g, default: []].append([t, v, key])
      if g == "hr" { counts[store.day(t), default: 0] += 1 }
    }
    var other: [String: [[Any]]] = ["hr": [], "hrv": [], "steps": []]
    let kinds: [LocalHistoryParams.Kind: String] = [.hr: "hr", .hrv: "hrv", .steps: "steps"]
    for (kind, g) in kinds {
      for r in try Row.fetchAll(db, sql: "SELECT t, value FROM hist_samples WHERE kind = ? AND t >= ? AND t < ? ORDER BY t",
                                arguments: [kind.rawValue, lo, hi]) {
        let t: Double = r[0], v: Double = r[1]
        other[g, default: []].append([t, v])
        if kind == .hr { counts[store.day(t), default: 0] += 1 }
      }
    }
    let sleep: [[Double]] = try Row.fetchAll(db, sql: """
      SELECT start, end FROM sleep_sessions WHERE end >= ? AND start < ? ORDER BY start
      """, arguments: [lo, hi]).map { [$0[0] as Double, $0[1] as Double] }
    var workouts: [[Double]] = try Row.fetchAll(db, sql: "SELECT start, end FROM hk_workouts WHERE start >= ? AND start < ?",
                                                arguments: [lo, hi]).map { [$0[0] as Double, $0[1] as Double] }
    // M1c's workouts table (Open Wearables' and older workouts too) when the phone has it
    if let more = try? Row.fetchAll(db, sql: "SELECT start, \"end\" FROM workouts WHERE start >= ? AND start < ?", arguments: [lo, hi]) {
      workouts += more.compactMap { r in
        guard let s = r[0] as Double?, let e = r[1] as Double? else { return nil }
        return [s, e]
      }
    }
    var hrvNight: [String: Double] = [:]
    for r in try Row.fetchAll(db, sql: "SELECT date, payload FROM daily_scores WHERE date >= ? AND date <= ?",
                              arguments: [LocalDay.add(a, -hrvNightDays), b]) {
      guard let data = r["payload"] as Data?, let p = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any] else { continue }
      let rec = (p["details"] as? [String: Any])?["recovery"] as? [String: Any]
      if let v = LocalRawStore.num(p["hrv_night_ln"]) ?? LocalRawStore.num(rec?["hrv_night_ln"]) { hrvNight[r["date"] as String] = v }
    }
    let shortcut = try String.fetchAll(db, sql: """
      SELECT DISTINCT date FROM daily_metrics WHERE source = 'shortcut' AND date >= ? AND date <= ?
      """, arguments: [a, b])
    let src: [String: Any] = ["tz": store.zone.identifier, "start": a, "end": b, "app": app, "other": other, "sleep": sleep,
                              "workouts": workouts, "hrv_night": hrvNight, "shortcut": shortcut]
    return (src, counts)
  }

  // MARK: - storing what the bundle answered

  /// Inside the caller's transaction; returns the days whose rows changed.
  @discardableResult
  static func save(_ db: Database, rows: [[String: Any]], counts: [String: Int], bundle: String) throws -> [String] {
    let now = Date().timeIntervalSince1970
    var changed: [String] = []
    for r in rows {
      guard let d = r["date"] as? String, let algo = r["algo_version"] as? String else { continue }
      var row = r
      for k in ["date", "algo_version", "bundle_version"] { row.removeValue(forKey: k) }
      let json = { (v: Any?) throws -> Data in try JSONSerialization.data(withJSONObject: v ?? NSNull(), options: [.sortedKeys, .fragmentsAllowed]) }
      var baseline: Data?
      if let b = r["baseline"], !(b is NSNull) { baseline = try json(b) }
      try db.execute(sql: """
        INSERT INTO stress_daily (date, score, level, low_h, medium_h, high_h, peak_hour, hours, baseline, flags, n_hr,
                                  algo_version, bundle_version, computed_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(date) DO UPDATE SET score = excluded.score, level = excluded.level, low_h = excluded.low_h,
          medium_h = excluded.medium_h, high_h = excluded.high_h, peak_hour = excluded.peak_hour, hours = excluded.hours,
          baseline = excluded.baseline, flags = excluded.flags, n_hr = excluded.n_hr, algo_version = excluded.algo_version,
          bundle_version = excluded.bundle_version, computed_at = excluded.computed_at
        """, arguments: [d, LocalRawStore.num(r["score"]), r["level"] as? String, (r["low_h"] as? NSNumber)?.intValue ?? 0,
                         (r["medium_h"] as? NSNumber)?.intValue ?? 0, (r["high_h"] as? NSNumber)?.intValue ?? 0,
                         (r["peak_hour"] as? NSNumber)?.intValue, try json(r["hours"] ?? [Any]()), baseline,
                         try json(r["flags"] ?? [String: Any]()), counts[d] ?? 0, algo, bundle, now])
      if try keep(db, day: d, rows: [row], algo: algo, bundle: bundle, now: now) { changed.append("\(kind):\(d)") }
    }
    return changed
  }

  /// `derived_rows` of one day: unchanged rows keep their upload state; returns true when they changed.
  private static func keep(_ db: Database, day: String, rows: [Any], algo: String, bundle: String, now: Double) throws -> Bool {
    let data = try JSONSerialization.data(withJSONObject: rows, options: [.sortedKeys])
    let old = try Row.fetchOne(db, sql: "SELECT rows, algo_version FROM derived_rows WHERE kind = ? AND date = ?", arguments: [kind, day])
    if let old, (old["rows"] as Data) == data, (old["algo_version"] as String) == algo { return false }
    try db.execute(sql: """
      INSERT INTO derived_rows (kind, date, rows, algo_version, bundle_version, computed_at, uploaded) VALUES (?, ?, ?, ?, ?, ?, 0)
      ON CONFLICT(kind, date) DO UPDATE SET rows = excluded.rows, algo_version = excluded.algo_version,
        bundle_version = excluded.bundle_version, computed_at = excluded.computed_at, uploaded = 0
      """, arguments: [kind, day, data, algo, bundle, now])
    return true
  }

  // MARK: - reading back (the pages)

  /// `stress_daily` rows of [from, to] in the server's row shape ({date, score, level, hours}, as trends/series.load and
  /// insights/stress.cards read them).
  static func rows(_ db: Database, from: String, to: String) throws -> [[String: Any]] {
    try Row.fetchAll(db, sql: "SELECT date, score, level, hours FROM stress_daily WHERE date BETWEEN ? AND ? ORDER BY date",
                     arguments: [from, to]).map { r -> [String: Any] in
      ["date": r["date"] as String, "score": SeriesLocal.nz(r["score"] as Double?), "level": SeriesLocal.nz(r["level"] as String?),
       "hours": (r["hours"] as Data?).flatMap { try? JSONSerialization.jsonObject(with: $0) } ?? [Any]()]
    }
  }

  /// The package's mode from the cached `GET /v1/sync/compute` answer (off without one).
  static func mode(_ db: Database) -> ComputeState.Mode {
    guard let d = try? DocRecord.fetchOne(db, key: LocalCompute.stateDoc) else { return .off }
    return ComputeState.decode(d.body).mode(package)
  }

  // MARK: - the cardio view

  /// `cardio.local:<today>`: the bundle's cardioView (cardio/views.compute) on the local score rows and, when the phone
  /// has M1c's tables, its heart-rate-recovery rows.
  static func cardio(db: AppDatabase, runtime rt: OpenBodyScoreRuntime, today: String, tz: String) throws {
    let start = LocalDay.add(today, -(cardioDays - 1)), lo = LocalDay.add(today, -59)
    let data: [String: Any] = try db.writer.read { db in
      let scores: [[String: Any]] = try Row.fetchAll(db, sql: "SELECT date, payload FROM daily_scores WHERE date BETWEEN ? AND ? ORDER BY date",
                                                     arguments: [start, today]).map { r -> [String: Any] in
        let p = (r["payload"] as Data?).flatMap { (try? JSONSerialization.jsonObject(with: $0)) as? [String: Any] } ?? [:]
        return ["date": r["date"] as String, "details": p["details"] ?? NSNull()]
      }
      let hrr: [[String: Any]] = (try? Row.fetchAll(db, sql: """
        SELECT h.workout_uuid, h.local_date, h.end_at, h.peak_hr, h.hr_60s, h.hrr60, h.apple_hrr60, h.valid, h.reason,
               w.type, w.name_zh, w.duration_min
        FROM workout_hrr h JOIN workouts w ON w.uuid = h.workout_uuid
        WHERE h.local_date BETWEEN ? AND ? ORDER BY h.end_at
        """, arguments: [Swift.min(start, lo), today]).map { r -> [String: Any] in
        let nz = SeriesLocal.nz
        return ["workout_id": nz(r[0] as String?), "local_date": r[1] as String, "end_at": (r[2] as Double) * 1000,
                "peak_hr": nz(r[3] as Double?), "hr_60s": nz(r[4] as Double?), "hrr60": nz(r[5] as Double?),
                "apple_hrr60": nz(r[6] as Double?), "valid": (r[7] as Bool?) ?? false, "reason": nz(r[8] as String?),
                "type": nz(r[9] as String?), "name_zh": nz(r[10] as String?), "duration_min": nz(r[11] as Double?)]
      }) ?? []
      return ["tz": tz, "hrr": hrr, "scores": scores]
    }
    guard let out = try rt.invoke("cardioView", [data, ["start": start, "end": today]]) as? [String: Any] else { return }
    try db.saveDoc(cardioDoc(today), json: try JSONSerialization.data(withJSONObject: out))
  }
}
