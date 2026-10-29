import Foundation
import GRDB

/// M1 (docs/ios-native-2.0.md section 4): the day's values and the nights, derived on the phone from what it reads from
/// HealthKit, with the bundle's `deriveDays` (packages/openbodyscore-js src/30_derive.js, the twin of the server's
/// rawdata/derive.derive_days). Runs before the scores, which read `daily_metrics` and `sleep_sessions`.
///
/// One run:
/// 1. reads HealthKit (`readHealth`: the scoring types and, for this package, every other raw type of the registry and the
///    15-minute statistics buckets; `HealthKitScoringReader.readEngine`), which marks the changed days (`score_dirty`,
///    kind `derive`);
/// 2. derives the dirty days, the days whose pulled `daily.sources` changed, and every day again after a new derive
///    algorithm, only from the first day the phone read HealthKit in full (`HealthKitScoringReader.since`): the day's
///    'app' rows → `daily_metric_sources` (origin phone), the rows to upload → `derived_rows` (kinds `daily`, `sleep`);
/// 3. primary only (the server trusts the phone's rows): the chosen values replace `daily_metrics`, the nights replace the
///    day's `sleep_sessions` (source app), and the changed days are marked dirty for the scores. In shadow the pages keep
///    the server's values (section 5);
/// 4. uploads the rows not uploaded yet (`PUT /v1/users/me/derived/app`, newest batch first) once everything this phone
///    read from HealthKit is on the server (`uploadGate`), so the server's spot check sees the same samples.
struct DeriveStage: ComputeStage {
  static let name = "derive"
  static let package = "derive"
  static let implemented = true
  static let kinds = ["daily", "sleep"]
  /// Days per bundle call (each loads its days' raw samples: ~2,000–4,000 rows a day).
  static let chunkDays = 7
  /// Days per upload (the server takes ≤ 400, nativesync/params.DERIVED_MAX_DAYS).
  static let uploadBatchDays = 120
  /// meta: the newest pulled source row (local `updated_at`) already derived with; the derive algorithm last used.
  static let sourcesSeenKey = "derive.sources_seen"
  static let algoKey = "derive.algo"

  // MARK: - host hooks (tests replace them)

  /// Reads HealthKit into the local tables (nil: nothing is read, e.g. in tests).
  static var readHealth: ((AppDatabase, TimeZone) async -> Void)? = { db, zone in
    let since = await MainActor.run { HealthSyncService.shared.uploadedSince }
    let reader = HealthKitScoringReader(db: db)
    let store = LocalRawStore(db: db, zone: zone)
    if !(await reader.read(into: store, uploadedSince: since)).isEmpty { try? db.setMeta(ScoringSync.readKey, "1") }
    _ = await reader.readEngine(into: store, uploadedSince: since)
  }
  /// true when the server has everything this phone read from HealthKit (as ScoringSync's upload gate).
  static var uploadGate: () async -> Bool = { await MainActor.run { HealthSyncService.shared.uploadedEverything } }
  /// The algorithm bundle in use (the signed download when there is one, else the built-in copy).
  static var runtime: (AppDatabase) throws -> OpenBodyScoreRuntime = { try AlgoBundles(db: $0).runtime() }

  // MARK: - one run

  func run(_ ctx: ComputeContext) async -> ComputeStageReport {
    var rep = ComputeStageReport(stage: Self.name, state: "done")
    let db = ctx.db, zone = ScoringSync.zone(db)
    let store = LocalRawStore(db: db, zone: zone)
    if let read = Self.readHealth { await read(db, zone) }
    let today = LocalDay.string(ctx.now, zone)
    guard let first = Self.firstFullDay(db, store) else {
      rep.state = "waiting_sync"
      rep.detail = "no_read"
      return rep
    }
    let rt: OpenBodyScoreRuntime
    do { rt = try Self.runtime(db) } catch {
      rep.state = "failed"
      rep.detail = "bundle: \(error)"
      return rep
    }
    guard let algo = rt.algoVersions["derive"] else {
      rep.state = "skipped"
      rep.detail = "bundle without derive"
      return rep
    }
    let primary = ctx.state.mode(Self.package) == .primary
    let started = Date().timeIntervalSince1970
    do {
      let days = try Self.daysToDerive(db, first: first, today: today, algo: algo)
      for chunk in stride(from: 0, to: days.count, by: Self.chunkDays).map({ Array(days[$0..<min(days.count, $0 + Self.chunkDays)]) }) {
        rep.written += try Self.deriveChunk(db: db, store: store, runtime: rt, days: chunk, today: today, primary: primary)
      }
      try await db.writer.write { db in
        try db.execute(sql: "DELETE FROM score_dirty WHERE kind = 'derive' AND (date < ? OR (date <= ? AND at <= ?))",
                       arguments: [first, today, started])
        if let seen = try Double.fetchOne(db, sql: "SELECT max(updated_at) FROM daily_metric_sources WHERE origin = 'server'") {
          try db.execute(sql: "INSERT INTO meta (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                         arguments: [Self.sourcesSeenKey, String(seen)])
        }
        try db.execute(sql: "INSERT INTO meta (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                       arguments: [Self.algoKey, algo])
      }
    } catch {
      rep.state = "failed"
      rep.detail = String(describing: error)
      return rep
    }
    // the upload: settled days only (everything read from HealthKit is on the server)
    let pending = (try? await db.writer.read { db in
      try Bool.fetchOne(db, sql: "SELECT EXISTS (SELECT 1 FROM derived_rows WHERE uploaded = 0)")
    }) ?? false
    guard pending else { return rep }
    guard await Self.uploadGate() else {
      rep.state = "waiting_sync"
      return rep
    }
    for kind in Self.kinds {
      let (sent, status) = await Self.upload(kind: kind, db: db, api: ctx.api, algo: algo, bundle: rt.version)
      rep.uploaded += sent.map { "\(kind):\($0)" }
      if let status, status != "ok" { rep.detail = [rep.detail, "\(kind): \(status)"].compactMap { $0 }.joined(separator: "; ") }
    }
    return rep
  }

  // MARK: - which days

  /// The first local day the phone read HealthKit for in full (the reader's window start; a start inside a day leaves
  /// that day out). nil before the first read.
  static func firstFullDay(_ db: AppDatabase, _ store: LocalRawStore) -> String? {
    guard let since = (try? db.meta("hk.scoring.since")).flatMap({ $0 }).flatMap(Double.init) else { return nil }
    let d = store.day(since)
    return store.dayStart(d) == since ? d : LocalDay.add(d, 1)
  }

  /// The dirty days, the days whose pulled sources changed, every day after a new derive algorithm (or the first run):
  /// within [first, today], sorted.
  static func daysToDerive(_ db: AppDatabase, first: String, today: String, algo: String) throws -> [String] {
    try db.writer.read { db -> [String] in
      let last = try String.fetchOne(db, sql: "SELECT value FROM meta WHERE key = ?", arguments: [Self.algoKey])
      if last != algo {
        var all: [String] = [], d = first
        while d <= today { all.append(d); d = LocalDay.add(d, 1) }
        return all
      }
      var days = Set(try String.fetchAll(db, sql: "SELECT date FROM score_dirty WHERE kind = 'derive' AND date >= ? AND date <= ?",
                                         arguments: [first, today]))
      let seen = try String.fetchOne(db, sql: "SELECT value FROM meta WHERE key = ?", arguments: [Self.sourcesSeenKey])
        .flatMap(Double.init) ?? 0
      days.formUnion(try String.fetchAll(db, sql: """
        SELECT DISTINCT date FROM daily_metric_sources WHERE origin = 'server' AND updated_at > ? AND date >= ? AND date <= ?
        """, arguments: [seen, first, today]))
      return days.sorted()
    }
  }

  /// One chunk: the local rows → `deriveDays` → the local tables. Returns the "<kind>:<day>" whose rows changed.
  static func deriveChunk(db: AppDatabase, store: LocalRawStore, runtime rt: OpenBodyScoreRuntime, days: [String], today: String,
                          primary: Bool) throws -> [String] {
    let src = try db.writer.read { db in try source(db, store: store, days: days, today: today) }
    guard let out = try rt.invoke("deriveDays", [src]) as? [String: Any] else { throw AlgorithmError.load("deriveDays") }
    return try db.writer.write { db in try save(db, out: out, days: days, primary: primary, bundle: rt.version) }
  }

  // MARK: - the bundle's input

  /// `deriveDays`' src for these days: the raw samples and buckets from the day before the first to 3 days after the last
  /// (the nights' window, rawdata/params SLEEP_DAYS_BEFORE / AFTER), the other sources' rows and the phone's own of the
  /// 60 days before (the basal reference), the day's workout energy.
  static func source(_ db: Database, store: LocalRawStore, days: [String], today: String) throws -> [String: Any] {
    guard let a = days.first, let b = days.last, let lo = store.dayStart(LocalDay.add(a, -1)),
          let hi = store.dayStart(LocalDay.add(b, 4)) else { return [:] }
    var samples: [[Any]] = []
    let rows = try Row.fetchCursor(db, sql: """
      SELECT h.uuid, h.type, h.start, h.end, h.value, s.name, h.source_id, s.device_type, s.device_name
      FROM hk_samples h LEFT JOIN hk_sources s ON s.id = h.source_id WHERE h.start >= ? AND h.start < ? ORDER BY h.start, h.uuid
      """, arguments: [lo, hi])
    while let r = try rows.next() {
      samples.append([r[0] as String, r[1] as String, r[2] as Double, r[3] as Double, r[4] as Double,
                      (r[5] as String?) ?? "", (r[6] as Int64?).map { NSNumber(value: $0) } ?? NSNull(),
                      (r[7] as String?) ?? NSNull(), (r[8] as String?) ?? NSNull()])
    }
    var buckets: [[Any]] = []
    for r in try Row.fetchAll(db, sql: "SELECT type, start, value FROM hk_stat_buckets WHERE start >= ? AND start < ? ORDER BY type, start",
                              arguments: [Int(lo), Int(hi)]) {
      buckets.append([r[0] as String, Double(r[1] as Int), r[2] as Double])
    }
    let histLo = LocalDay.add(a, -60)
    func row(_ r: Row) -> [String: Any] {
      var o: [String: Any] = ["date": r["date"] as String, "metric": r["metric"] as String, "source": r["source"] as String,
                              "value": r["value"] as Double, "agg": r["agg"] as String,
                              "unit": (r["unit"] as String?) ?? NSNull(), "n": (r["n"] as Int?) ?? NSNull(),
                              "min_value": (r["min"] as Double?) ?? NSNull(), "max_value": (r["max"] as Double?) ?? NSNull()]
      o["flags"] = (r["flags"] as Data?).flatMap { try? JSONSerialization.jsonObject(with: $0) } ?? [String: Any]()
      return o
    }
    let other = try Row.fetchAll(db, sql: """
      SELECT * FROM daily_metric_sources WHERE origin = 'server' AND source <> 'app' AND date >= ? AND date <= ?
      """, arguments: [histLo, b]).map(row)
    // the phone's own 'app' rows of the days before, else the server's (the basal reference)
    let mine = try Row.fetchAll(db, sql: """
      SELECT * FROM daily_metric_sources WHERE source = 'app' AND date >= ? AND date < ?
      ORDER BY date, metric, CASE origin WHEN 'phone' THEN 0 ELSE 1 END
      """, arguments: [histLo, a])
    var seen = Set<String>(), history: [[String: Any]] = []
    for r in mine where seen.insert("\(r["date"] as String)|\(r["metric"] as String)").inserted { history.append(row(r)) }
    var kcal: [String: Double] = [:]
    for r in try Row.fetchAll(db, sql: "SELECT start, stats FROM hk_workouts WHERE start >= ? AND start < ?", arguments: [lo, hi]) {
      guard let data = r["stats"] as Data?, let st = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any],
            let k = LocalRawStore.num(st["active_kcal"]) else { continue }
      kcal[store.day(r["start"] as Double), default: 0] += k
    }
    return ["tz": store.zone.identifier, "today": today, "days": days, "wake_dates": days,
            "samples": ["cols": ["uuid", "type", "start", "end", "value", "source", "source_id", "device_type", "device_name"],
                        "rows": samples],
            "buckets": ["cols": ["type", "start", "value"], "rows": buckets], "other": other, "app_history": history,
            "workout_kcal": kcal.filter { $0.key >= a && $0.key <= b }]
  }

  // MARK: - storing what the bundle answered

  /// Writes `deriveDays`' answer for these days (inside the caller's transaction); returns the days whose rows changed.
  @discardableResult
  static func save(_ db: Database, out: [String: Any], days: [String], primary: Bool, bundle: String) throws -> [String] {
    let algo = out["algo_version"] as? String ?? ""
    let now = Date().timeIntervalSince1970
    let daily = out["daily"] as? [String: Any] ?? [:], sleep = out["sleep"] as? [String: Any] ?? [:]
    var changed: [String] = [], scoreDays = Set<String>()
    for d in days {
      let v = daily[d] as? [String: Any] ?? [:]
      let sources = v["sources"] as? [[String: Any]] ?? [], chosen = v["chosen"] as? [[String: Any]] ?? []
      let rows = sources.map { $0.merging(["role": "source"]) { a, _ in a } } + chosen.map { $0.merging(["role": "chosen"]) { a, _ in a } }
      if try keep(db, kind: "daily", day: d, rows: rows, algo: algo, bundle: bundle, now: now) { changed.append("daily:\(d)") }
      try db.execute(sql: "DELETE FROM daily_metric_sources WHERE date = ? AND origin = 'phone'", arguments: [d])
      for r in sources {
        try db.execute(sql: """
          INSERT INTO daily_metric_sources (date, metric, source, origin, value, unit, agg, n, min, max, flags, updated_at)
          VALUES (?, ?, 'app', 'phone', ?, ?, ?, ?, ?, ?, ?, ?)
          """, arguments: [d, r["metric"] as? String, LocalRawStore.num(r["value"]), r["unit"] as? String, r["agg"] as? String,
                           (r["n"] as? NSNumber)?.intValue, LocalRawStore.num(r["min_value"]), LocalRawStore.num(r["max_value"]),
                           try json(r["flags"] ?? [String: Any]()), now])
      }
      if primary {
        let before = try Row.fetchAll(db, sql: "SELECT metric, value, source FROM daily_metrics WHERE date = ? ORDER BY metric",
                                      arguments: [d]).map { "\($0["metric"] as String)=\(($0["value"] as Double?) ?? .nan)@\(($0["source"] as String?) ?? "")" }
        try db.execute(sql: "DELETE FROM daily_metrics WHERE date = ?", arguments: [d])
        for r in chosen {
          try db.execute(sql: """
            INSERT INTO daily_metrics (date, metric, value, unit, source, flags, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)
            """, arguments: [d, r["metric"] as? String, LocalRawStore.num(r["value"]), r["unit"] as? String, r["source"] as? String,
                             try json(r["flags"] ?? [String: Any]()), now])
        }
        let after = chosen.map { "\($0["metric"] as? String ?? "")=\(LocalRawStore.num($0["value"]) ?? .nan)@\($0["source"] as? String ?? "")" }
          .sorted()
        if before != after { scoreDays.insert(d) }
      }
    }
    for w in days {
      let sessions = sleep[w] as? [[String: Any]] ?? []
      if try keep(db, kind: "sleep", day: w, rows: sessions, algo: algo, bundle: bundle, now: now) {
        changed.append("sleep:\(w)")
        if primary { scoreDays.insert(w) }
      }
      guard primary else { continue }
      // the app's nights replace the day's (the server's replace_sleep: app is the highest source, lower ones go)
      if sessions.isEmpty {
        try db.execute(sql: "DELETE FROM sleep_sessions WHERE date = ? AND source = 'app'", arguments: [w])
      } else {
        try db.execute(sql: "DELETE FROM sleep_sessions WHERE date = ?", arguments: [w])
      }
      for (i, s) in sessions.enumerated() {
        guard let a = LocalRawStore.num(s["start"]), let b = LocalRawStore.num(s["end"]) else { continue }
        let stages: [[Any]] = ((s["segments"] as? [[Any]]) ?? []).compactMap { g in
          guard g.count >= 3, let x = LocalRawStore.num(g[0]), let y = LocalRawStore.num(g[1]), let st = g[2] as? String else { return nil }
          return [st, x, y]
        }
        var payload = s
        payload.removeValue(forKey: "segments")
        payload["source"] = "app"
        payload["wake_date"] = w
        try db.execute(sql: """
          INSERT INTO sleep_sessions (id, date, start, end, is_main, stages, source, updated_at, payload)
          VALUES (?, ?, ?, ?, ?, ?, 'app', ?, ?)
          """, arguments: ["app:\(w):\(i)", w, a, b, s["is_main"] as? Bool ?? (i == 0), try JSONSerialization.data(withJSONObject: stages),
                           now, try JSONSerialization.data(withJSONObject: payload, options: [.sortedKeys])])
      }
    }
    if !scoreDays.isEmpty { try LocalRawStore.markDirty(db, scoreDays) }
    return changed
  }

  /// `derived_rows` of one (kind, day): unchanged rows keep their upload state; returns true when they changed.
  private static func keep(_ db: Database, kind: String, day: String, rows: [Any], algo: String, bundle: String,
                           now: Double) throws -> Bool {
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

  private static func json(_ o: Any) throws -> Data {
    try JSONSerialization.data(withJSONObject: o, options: [.sortedKeys])
  }

  // MARK: - the upload

  /// Sends one kind's rows not uploaded yet (current algorithm only), newest batch first. Returns the days the server
  /// took (ok / mismatch: it noted the check) and the last status (nil when there was nothing to send).
  static func pending(db: AppDatabase, kind: String, algo: String) throws -> [(date: String, rows: Data)] {
    try db.writer.read { db in
      try Row.fetchAll(db, sql: """
        SELECT date, rows FROM derived_rows WHERE kind = ? AND uploaded = 0 AND algo_version = ? ORDER BY date DESC
        """, arguments: [kind, algo]).map { (date: $0["date"] as String, rows: $0["rows"] as Data) }
    }
  }

  static func upload(kind: String, db: AppDatabase, api: APIClient, algo: String, bundle: String) async -> ([String], String?) {
    let rows = (try? pending(db: db, kind: kind, algo: algo)) ?? []
    var sent: [String] = [], status: String?
    for start in stride(from: 0, to: rows.count, by: Self.uploadBatchDays) {
      let batch = Array(rows[start..<min(rows.count, start + Self.uploadBatchDays)])
      let items: [[String: Any]] = batch.compactMap { r in
        guard let list = try? JSONSerialization.jsonObject(with: r.rows) else { return nil }
        return ["date": r.date, "rows": list]
      }
      let body: [String: Any] = ["kind": kind, "algo_version": algo, "bundle_version": bundle, "days": items]
      do {
        let j = try await api.json(APIRequest(method: .put, path: Endpoint.derivedUpload,
                                              body: try JSONSerialization.data(withJSONObject: body), timeout: 60))
        status = j["status"] as? String
      } catch {
        return (sent, "error: \(error)")
      }
      guard status == "ok" || status == "mismatch" else { return (sent, status) }   // stale_algo: the next bundle
      let days = batch.map(\.date)
      try? await db.writer.write { db in
        for d in days {
          try db.execute(sql: "UPDATE derived_rows SET uploaded = 1 WHERE kind = ? AND date = ? AND algo_version = ?",
                         arguments: [kind, d, algo])
        }
      }
      sent += days
    }
    return (sent, status)
  }
}
