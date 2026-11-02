import Foundation
import GRDB

/// M4 (docs/ios-native-2.0.md section 4): body age computed on the phone with the bundle's `healthspanLocal`
/// (packages/openbodyscore-js src/34_coach.js, the twin of the server's insights/healthspan_store.weekly_local): the
/// weekly rows from the scoring origin to today, uploaded as kind `healthspan`. Runs after the scores and stress, since it
/// reads the score rows (sleep, heart-rate zones), the daily values, the nights and M1c's workouts.
///
/// One run:
/// 1. when: after a new coach algorithm, on a new day, or when the last run is older than `recomputeEvery` (a sync round
///    brings new nights / steps / weigh-ins; the weeks are cheap: one bundle call for the whole history);
/// 2. inputs (`parts`, the server's healthspan_store.load_parts on the local tables): the profile document (birth date,
///    sex, height, goal weight), `daily_scores` (actual sleep, strain, zone minutes), `daily_metrics` (steps, resting heart
///    rate, VO₂max, body mass), `sleep_sessions`, M1c's `workouts` (kept copies), the SparkyFitness strength sessions and
///    weigh-ins as raw rows (`ext_records`, pulled for the window first), the journal's strength days (records `journal`),
///    the scoring origin (first day with a resting heart rate). The phone has no Hevy import and no training plan (the plan
///    only labels the strength item; docs section 4 M4 "as built");
/// 3. the rows go to `healthspan_weekly` (shadow and primary: the phone's own table; pages read it only in primary) and to
///    `derived_rows` (kind `healthspan`, one per week by its Monday), uploaded like DeriveStage's once everything read from
///    HealthKit is on the server.
struct HealthspanStage: ComputeStage {
  static let name = "healthspan"
  static let package = "coach"
  static let implemented = true
  static let kind = "healthspan"
  /// seconds between two recomputes of the same day (a sync round may bring new inputs)
  static let recomputeEvery: Double = 3600
  /// the ext_records range of one request (the server answers at most 400 days)
  static let extChunkDays = 365
  /// healthspan_params.STRENGTH_JOURNAL_KEYS: the journal items that say "trained strength today"
  static let strengthJournalKeys = ["training_upper", "training_legs"]
  static let metrics = ["step_count", "resting_heart_rate", "vo2_max", "body_mass"]
  /// meta: the coach algorithm, day and time of the last computation
  static let algoKey = "healthspan.algo"
  static let dayKey = "healthspan.day"
  static let atKey = "healthspan.at"

  // MARK: - host hooks (tests replace them)

  static var uploadGate: () async -> Bool = { await MainActor.run { HealthSyncService.shared.uploadedEverything } }
  static var runtime: (AppDatabase) throws -> OpenBodyScoreRuntime = { try AlgoBundles(db: $0).runtime() }
  /// Pulls the SparkyFitness rows of [from, to] (tests: nothing).
  static var pullExt: (AppDatabase, APIClient, String, String) async -> Void = { db, api, from, to in
    var lo = from
    while lo <= to {
      let hi = min(LocalDay.add(lo, extChunkDays - 1), to)
      _ = await DocPuller(api: api, db: db).pullExtRecords(from: lo, to: hi)
      lo = LocalDay.add(hi, 1)
    }
  }

  /// The journal's strength days come from the server's `records` stream filtered to the kind `journal` (the pull's kind
  /// filter, #368) on this stage's own cursor; each pulled day replaces that day in the document `journalDoc`
  /// ({"days": {date: true}}). The filtered cursor is stamped with its kind set, so it never feeds another pull.
  static let journalCursorKey = "healthspan.records_cursor.journal"
  static let journalKinds: [String: [String]] = ["records": ["journal"]]
  static let journalDoc = "healthspan.journal_strength"
  static let maxPullPages = 10
  static var pullJournal: (AppDatabase, APIClient) async -> Void = { db, api in await pullJournalDays(db: db, api: api) }

  // MARK: - one run

  func run(_ ctx: ComputeContext) async -> ComputeStageReport {
    var rep = ComputeStageReport(stage: Self.name, state: "done")
    let db = ctx.db, zone = ScoringSync.zone(db)
    let today = LocalDay.string(ctx.now, zone)
    let rt: OpenBodyScoreRuntime
    do { rt = try Self.runtime(db) } catch {
      rep.state = "failed"
      rep.detail = "bundle: \(error)"
      return rep
    }
    guard let algo = rt.algoVersions["coach"] else {
      rep.state = "skipped"
      rep.detail = "bundle without coach"
      return rep
    }
    if Self.due(db, algo: algo, today: today, now: ctx.now.timeIntervalSince1970) {
      do {
        if let origin = try await db.writer.read({ try Self.origin($0) }) {
          await Self.pullExt(db, ctx.api, LocalDay.add(origin, -1), today)
          await Self.pullJournal(db, ctx.api)
        }
        rep.written = try Self.compute(db: db, runtime: rt, today: today, zone: zone)
        try db.setMeta(Self.algoKey, algo)
        try db.setMeta(Self.dayKey, today)
        try db.setMeta(Self.atKey, String(ctx.now.timeIntervalSince1970))
      } catch {
        rep.state = "failed"
        rep.detail = String(describing: error)
        return rep
      }
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

  static func due(_ db: AppDatabase, algo: String, today: String, now: Double) -> Bool {
    let last: String? = (try? db.meta(algoKey)) ?? nil, day: String? = (try? db.meta(dayKey)) ?? nil
    let atText: String? = (try? db.meta(atKey)) ?? nil
    guard last == algo, day == today, let at = atText.flatMap({ Double($0) }) else { return true }
    return now - at >= recomputeEvery
  }

  /// The local rows → `healthspanLocal` → `healthspan_weekly` + `derived_rows`. Returns "healthspan:<week>" of the weeks
  /// whose rows changed.
  static func compute(db: AppDatabase, runtime rt: OpenBodyScoreRuntime, today: String, zone: TimeZone) throws -> [String] {
    guard let parts = try db.writer.read({ try Self.parts($0, today: today, zone: zone) }) else { return [] }
    guard let rows = try rt.invoke("healthspanLocal", [parts, today]) as? [[String: Any]] else { return [] }
    return try db.writer.write { db in try save(db, rows: rows, bundle: rt.version) }
  }

  // MARK: - the inputs (the server's healthspan_store.load_parts on the local tables)

  /// The scoring origin: the first day with a resting heart rate (scores/engine.scoring_origin).
  static func origin(_ db: Database) throws -> String? {
    try String.fetchOne(db, sql: "SELECT min(date) FROM daily_metrics WHERE metric = 'resting_heart_rate' AND value IS NOT NULL")
  }

  /// `healthspanLocal`'s parts over [origin − 1, today]; nil without an origin (body age needs one, as the server's load).
  static func parts(_ db: Database, today: String, zone: TimeZone) throws -> [String: Any]? {
    guard let origin = try origin(db), origin <= today else { return nil }
    let start = LocalDay.add(origin, -1)
    let nz = SeriesLocal.nz
    let profile = (try DocRecord.fetchOne(db, key: SeriesLocal.profileDoc))?.json ?? [:]
    let birth: Any = (profile["birth_date"] as? String).map { String($0.prefix(10)) as Any } ?? NSNull()
    let scores: [[String: Any]] = try Row.fetchAll(db, sql: "SELECT date, strain, payload FROM daily_scores WHERE date BETWEEN ? AND ? ORDER BY date",
                                                   arguments: [start, today]).map { r in
      let p = (r["payload"] as Data?).flatMap { (try? JSONSerialization.jsonObject(with: $0)) as? [String: Any] } ?? [:]
      let load = (p["details"] as? [String: Any])?["load"] as? [String: Any]
      return ["date": r["date"] as String, "sleep_actual_min": nz(p["sleep_actual_min"]),
              "strain": TrainingLocal.pick(p["strain"], r["strain"] as Double?), "z": nz(load?["zone_minutes"])]
    }
    var metrics: [String: [String: Double]] = [:]
    for m in Self.metrics { metrics[m] = [:] }
    for r in try Row.fetchAll(db, sql: """
      SELECT date, metric, value FROM daily_metrics WHERE metric IN ('step_count', 'resting_heart_rate', 'vo2_max', 'body_mass')
      AND value IS NOT NULL AND date BETWEEN ? AND ? ORDER BY date
      """, arguments: [start, today]) {
      metrics[r["metric"] as String, default: [:]][r["date"] as String] = r["value"] as Double
    }
    let sleep = try SeriesLocal.sessions(db, from: LocalDay.add(start, -1), to: today)
    var workouts: [[String: Any]] = []
    if let rows = try? Row.fetchAll(db, sql: """
      SELECT local_date, type, duration_min, row FROM workouts WHERE dup_of IS NULL AND excluded IS NULL
      AND local_date BETWEEN ? AND ? ORDER BY start, key
      """, arguments: [start, today]) {
      workouts = rows.map { r in
        let row = (r["row"] as Data?).flatMap { (try? JSONSerialization.jsonObject(with: $0)) as? [String: Any] } ?? [:]
        return ["local_date": r["local_date"] as String, "type": r["type"] as String, "duration_min": r["duration_min"] as Double,
                "hr_zone_min": zones(row)]
      }
    }
    var sessions: [Any] = [], weights: [Any] = []
    for doc in try DocRecord.fetchAll(db, sql: "SELECT * FROM docs WHERE key >= ? AND key <= ? ORDER BY key",
                                      arguments: [DocPuller.extRecordsKey(start), DocPuller.extRecordsKey(today)]) {
      guard let j = doc.json else { continue }
      sessions += (j["strength_sets"] as? [Any]) ?? []
      weights += (j["weights"] as? [Any]) ?? []
    }
    // the journal's strength days: the server's (pulled, journalDoc) and this phone's unsent entries
    var journalDays = Set(try String.fetchAll(db, sql: """
      SELECT DISTINCT date FROM records WHERE kind = 'journal' AND NOT deleted AND date BETWEEN ? AND ?
      AND json_extract(payload, '$.key') IN (\(strengthJournalKeys.map { "'\($0)'" }.joined(separator: ",")))
      AND json_extract(payload, '$.value') > 0
      """, arguments: [start, today]))
    for (d, on) in ((try DocRecord.fetchOne(db, key: journalDoc))?.json?["days"] as? [String: Bool]) ?? [:]
    where on && start <= d && d <= today {
      journalDays.insert(d)
    }
    let journal = journalDays.sorted()
    return ["tz": zone.identifier, "birth": birth, "sex": nz(profile["sex"]), "height": nz(profile["height_cm"]),
            "goal_kg": nz(profile["goal_weight_kg"]), "start": start, "end": today, "scores": scores, "metrics": metrics,
            "sleep": sleep, "workouts": workouts, "sparky_sessions": sessions, "sparky_weights": weights,
            "sparky_error": NSNull(), "imported": [String: Any](), "origin": origin, "journal": journal, "plan": NSNull()]
  }

  /// Pulls the `records` stream filtered to the kind `journal` on this stage's own cursor and keeps, per pulled day,
  /// whether a strength item was logged (whole-day replace; a deleted day drops out). A refused cursor (422) is dropped,
  /// so the next run restores from the start.
  static func pullJournalDays(db: AppDatabase, api: APIClient) async {
    for _ in 0..<maxPullPages {
      let cursor = ((try? db.meta(journalCursorKey)) ?? nil) ?? ""
      let body: [String: Any] = ["cursors": ["records": cursor], "limit": 500, "kinds": journalKinds]
      let page: [String: Any]
      do {
        page = try await api.json(APIRequest(method: .post, path: Endpoint.syncPull,
                                             body: try JSONSerialization.data(withJSONObject: body), idempotent: true, timeout: 60))
      } catch APIError.http(422, _) {
        try? await db.writer.write { db in try db.execute(sql: "DELETE FROM meta WHERE key = ?", arguments: [journalCursorKey]) }
        break
      } catch {
        break
      }
      let changes = (page["changes"] as? [[String: Any]]) ?? []
      let next = (page["cursors"] as? [String: String])?["records"]
      try? await db.writer.write { db in
        var days = ((try DocRecord.fetchOne(db, key: journalDoc))?.json?["days"] as? [String: Bool]) ?? [:]
        for ch in changes where ch["stream"] as? String == "records" && ch["kind"] as? String == "journal" {
          guard let d = ch["date"] as? String else { continue }
          let items = ((ch["body"] as? [String: Any])?["items"] as? [[String: Any]]) ?? []
          let strength = items.contains { it in
            strengthJournalKeys.contains(it["key"] as? String ?? "") && (LocalRawStore.num(it["value"]) ?? 0) > 0
          }
          days[d] = strength ? true : nil
        }
        let doc = DocRecord(key: journalDoc, version: 0, body: try JSONSerialization.data(withJSONObject: ["days": days]),
                            fetchedAt: Date().timeIntervalSince1970)
        try doc.save(db)
        if let next {
          try db.execute(sql: "INSERT INTO meta (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                         arguments: [journalCursorKey, next])
        }
      }
      if page["more"] as? Bool != true { break }
    }
  }

  /// A workout row's five heart-rate zone minutes (the server's hr_zone_min), or null without them.
  static func zones(_ row: [String: Any]) -> Any {
    if let z = row["hr_zone_min"] as? [Any], z.count == 5 { return z }
    let z = (1...5).map { row["zone\($0)_min"] }
    guard z.allSatisfy({ $0 != nil && !($0 is NSNull) }) else { return NSNull() }
    return z.map { $0! }
  }

  // MARK: - storing what the bundle answered

  /// Inside the caller's transaction: every week replaced (weeks no longer computed removed); returns the weeks whose
  /// rows changed.
  @discardableResult
  static func save(_ db: Database, rows: [[String: Any]], bundle: String) throws -> [String] {
    let now = Date().timeIntervalSince1970
    let json = { (v: Any?) throws -> Data in try JSONSerialization.data(withJSONObject: v ?? [Any](), options: [.sortedKeys]) }
    var changed: [String] = [], weeks: [String] = []
    for r in rows {
      guard let w = r["week_start"] as? String, let algo = r["algo_version"] as? String, let asOf = r["as_of"] as? String else { continue }
      weeks.append(w)
      try db.execute(sql: """
        INSERT INTO healthspan_weekly (week_start, as_of, chrono_age, body_age, delta_years, pace_of_aging, pace_raw,
                                       pace_window_weeks, contributors, insufficient, algo_version, bundle_version, computed_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(week_start) DO UPDATE SET as_of = excluded.as_of, chrono_age = excluded.chrono_age,
          body_age = excluded.body_age, delta_years = excluded.delta_years, pace_of_aging = excluded.pace_of_aging,
          pace_raw = excluded.pace_raw, pace_window_weeks = excluded.pace_window_weeks, contributors = excluded.contributors,
          insufficient = excluded.insufficient, algo_version = excluded.algo_version, bundle_version = excluded.bundle_version,
          computed_at = excluded.computed_at
        """, arguments: [w, asOf, LocalRawStore.num(r["chrono_age"]), LocalRawStore.num(r["body_age"]),
                         LocalRawStore.num(r["delta_years"]), LocalRawStore.num(r["pace_of_aging"]), LocalRawStore.num(r["pace_raw"]),
                         (r["pace_window_weeks"] as? NSNumber)?.intValue, try json(r["contributors"]), try json(r["insufficient"]),
                         algo, bundle, now])
      // the upload row: the server's row shape (no week_start, no notes, no stamps)
      var up = r
      for k in ["week_start", "notes", "algo_version", "bundle_version"] { up.removeValue(forKey: k) }
      if try keep(db, week: w, rows: [up], algo: algo, bundle: bundle, now: now) { changed.append("\(kind):\(w)") }
    }
    if !weeks.isEmpty {
      let marks = weeks.map { _ in "?" }.joined(separator: ",")
      try db.execute(sql: "DELETE FROM healthspan_weekly WHERE week_start NOT IN (\(marks))", arguments: StatementArguments(weeks))
    }
    return changed
  }

  /// `derived_rows` of one week: unchanged rows keep their upload state; returns true when they changed.
  private static func keep(_ db: Database, week: String, rows: [Any], algo: String, bundle: String, now: Double) throws -> Bool {
    let data = try JSONSerialization.data(withJSONObject: rows, options: [.sortedKeys])
    let old = try Row.fetchOne(db, sql: "SELECT rows, algo_version FROM derived_rows WHERE kind = ? AND date = ?", arguments: [kind, week])
    if let old, (old["rows"] as Data) == data, (old["algo_version"] as String) == algo { return false }
    try db.execute(sql: """
      INSERT INTO derived_rows (kind, date, rows, algo_version, bundle_version, computed_at, uploaded) VALUES (?, ?, ?, ?, ?, ?, 0)
      ON CONFLICT(kind, date) DO UPDATE SET rows = excluded.rows, algo_version = excluded.algo_version,
        bundle_version = excluded.bundle_version, computed_at = excluded.computed_at, uploaded = 0
      """, arguments: [kind, week, data, algo, bundle, now])
    return true
  }

  // MARK: - reading back (the pages)

  /// The package's mode from the cached `GET /v1/sync/compute` answer (off without one).
  static func mode(_ db: Database) -> ComputeState.Mode {
    guard let d = try? DocRecord.fetchOne(db, key: LocalCompute.stateDoc) else { return .off }
    return ComputeState.decode(d.body).mode(package)
  }

  /// `{as_of, body_age}` of the weeks with a body age in [from, to] (trends/series.load's body_age block).
  static func bodyAges(_ db: Database, from: String, to: String) throws -> [[String: Any]] {
    try Row.fetchAll(db, sql: """
      SELECT as_of, body_age FROM healthspan_weekly WHERE as_of BETWEEN ? AND ? AND body_age IS NOT NULL ORDER BY as_of
      """, arguments: [from, to]).map { r -> [String: Any] in ["as_of": r["as_of"] as String, "body_age": r["body_age"] as Double] }
  }
}
