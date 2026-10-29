import Foundation
import GRDB

/// 2.0.0 package M1c (docs/ios-native-2.0.md section 4): the training calendar and the single-day detail computed on the
/// phone with the algorithm bundle (`src/31_workouts.js` `trainingCalendar` / `trainingDay`, the port of
/// training/history.compute + calendar_view / day_view) from the local tables: `workouts` (WorkoutsStage: the phone's own
/// workouts and the server's of the other sources), `ext_records` (the SparkyFitness strength log, merged by day),
/// `daily_metrics`, `daily_scores` and the exercise restrictions document (the exercises' status).
///
/// | Page | Server document | Phone's document |
/// |---|---|---|
/// | Training calendar (month) | `training:calendar:<month>` | `training.local:calendar:<month>` |
/// | Training day | `training:day:<day>` | `training.local:day:<day>` |
///
/// Package `workouts` (flag `native_workouts`): off = nothing is computed; shadow = computed and compared with the
/// server's document (field paths of the differences in `meta` `workouts.compare`), the page draws the server's;
/// primary = the page draws the phone's (the server's while the phone's is not there yet). The muscle-load map stays the
/// server's document (the phone has no exercise catalog yet: the static bundle reaches the app later).
enum TrainingLocal {
  static let package = "workouts"
  static let compareMeta = "workouts.compare"

  static func calendarKey(_ month: String) -> String { "training.local:calendar:\(month)" }
  static func dayKey(_ day: String) -> String { "training.local:day:\(day)" }

  /// The package's state from the cached `GET /v1/sync/compute` answer.
  static func mode(_ state: [String: Any]?) -> ComputeState.Mode {
    guard let state, let data = try? JSONSerialization.data(withJSONObject: state) else { return .off }
    return ComputeState.decode(data).mode(package)
  }

  /// The document a page draws: the phone's in primary once computed, else the server's.
  static func shown(state: [String: Any]?, server: [String: Any]?, local: [String: Any]?) -> [String: Any]? {
    mode(state) == .primary && local != nil ? local : server
  }

  // MARK: - computing

  /// After a page pulled its server documents: the phone's months / days (nothing when the package is off).
  static func refresh(db: AppDatabase, api: APIClient, months: [String], days: [String] = [], today: String) async {
    guard await currentMode(db: db, api: api) != .off else { return }
    let zone = ScoringSync.zone(db)
    var lo = days.min() ?? today, hi = days.max() ?? today
    for m in months {
      lo = min(lo, "\(m)-01")
      hi = max(hi, "\(m)-31")
    }
    _ = await DocPuller(api: api, db: db).pullExtRecords(from: lo, to: min(hi, today))
    guard let rt = try? AlgoBundles(db: db).runtime() else { return }
    for m in months { _ = try? month(m, db: db, runtime: rt, zone: zone, today: today) }
    for d in days { _ = try? day(d, db: db, runtime: rt, zone: zone, today: today) }
  }

  /// The cached state, asked again when older than SeriesLocal.stateMaxAge (offline: the cached one).
  static func currentMode(db: AppDatabase, api: APIClient) async -> ComputeState.Mode {
    if let d = try? db.doc(LocalCompute.stateDoc), Date().timeIntervalSince1970 - d.fetchedAt < SeriesLocal.stateMaxAge {
      return ComputeState.decode(d.body).mode(package)
    }
    return await LocalCompute(db: db, api: api).fetchState().mode(package)
  }

  /// One month's calendar; stored as the phone's document and compared with the server's (when cached).
  @discardableResult
  static func month(_ month: String, db: AppDatabase, runtime rt: OpenBodyScoreRuntime, zone: TimeZone,
                    today: String) throws -> [String: Any]? {
    let start = "\(month)-01", end = lastDay(month)
    let data = try db.writer.read { db in try self.data(db, from: start, to: end, zone: zone, exercises: false) }
    _ = try? rt.invoke("setLanguage", [Lang.code])
    let opts: [String: Any] = ["start": start, "end": end, "label": month, "today": today]
    guard var out = try rt.invoke("trainingCalendar", [data, opts]) as? [String: Any] else { return nil }
    out["computed_at"] = Date().timeIntervalSince1970
    try db.saveDoc(calendarKey(month), json: try JSONSerialization.data(withJSONObject: out))
    if let server = try db.doc(RecordDocs.calendar(month))?.json { record("calendar", calendarKey(month), out, server, db: db) }
    return out
  }

  /// One day's detail; stored as the phone's document and compared with the server's (when cached).
  @discardableResult
  static func day(_ day: String, db: AppDatabase, runtime rt: OpenBodyScoreRuntime, zone: TimeZone,
                  today: String) throws -> [String: Any]? {
    let data = try db.writer.read { db in try self.data(db, from: day, to: day, zone: zone, exercises: true) }
    _ = try? rt.invoke("setLanguage", [Lang.code])
    guard var out = try rt.invoke("trainingDay", [data, ["date": day, "today": today]]) as? [String: Any] else { return nil }
    out["computed_at"] = Date().timeIntervalSince1970
    try db.saveDoc(dayKey(day), json: try JSONSerialization.data(withJSONObject: out))
    if let server = try db.doc(RecordDocs.trainingDay(day))?.json { record("day", dayKey(day), out, server, db: db) }
    return out
  }

  static func lastDay(_ month: String) -> String {
    var d = "\(month)-28"
    while LocalDay.add(d, 1).hasPrefix(month) { d = LocalDay.add(d, 1) }
    return d
  }

  // MARK: - reading the local tables (what training/history.load reads on the server)

  static let dailyMetrics = ["active_energy_burned": "active_kcal", "basal_energy_burned": "basal_kcal",
                             "apple_exercise_time": "exercise_min", "step_count": "steps"]

  static func data(_ db: Database, from: String, to: String, zone: TimeZone, exercises: Bool) throws -> [String: Any] {
    let workouts = try Row.fetchAll(db, sql: """
      SELECT row FROM workouts WHERE dup_of IS NULL AND excluded IS NULL AND local_date >= ? AND local_date <= ? ORDER BY start, key
      """, arguments: [from, to]).compactMap { r in (try? JSONSerialization.jsonObject(with: r["row"] as Data)) as? [String: Any] }
    var sparky: [Any] = []
    for r in try Row.fetchAll(db, sql: "SELECT key, body FROM docs WHERE key >= ? AND key <= ? ORDER BY key",
                              arguments: [DocPuller.extRecordsKey(from), DocPuller.extRecordsKey(to)]) {
      guard let o = (try? JSONSerialization.jsonObject(with: r["body"] as Data)) as? [String: Any] else { continue }
      sparky += (o["strength_sets"] as? [Any]) ?? []
    }
    var metrics: [String: [String: Any]] = [:]
    for r in try Row.fetchAll(db, sql: """
      SELECT date, metric, value FROM daily_metrics WHERE date >= ? AND date <= ? AND value IS NOT NULL
      AND metric IN ('active_energy_burned', 'basal_energy_burned', 'apple_exercise_time', 'step_count')
      """, arguments: [from, to]) {
      metrics[r["date"] as String, default: [:]][dailyMetrics[r["metric"] as String] ?? ""] = r["value"] as Double
    }
    var scores: [String: [String: Any]] = [:], hrMax: [String: Any] = [:]
    for r in try Row.fetchAll(db, sql: "SELECT date, strain, recovery, payload FROM daily_scores WHERE date >= ? AND date <= ?",
                              arguments: [from, LocalDay.add(to, 1)]) {
      let p = (r["payload"] as Data?).flatMap { (try? JSONSerialization.jsonObject(with: $0)) as? [String: Any] } ?? [:]
      let d: String = r["date"]
      scores[d] = ["strain": pick(p["strain"], r["strain"] as Double?), "muscle_load": pick(p["muscle_load"], nil),
                   "recovery": pick(p["recovery"], r["recovery"] as Double?), "trimp": pick(p["trimp"], nil),
                   "hr_max": pick(p["hr_max"], nil)]
      if d <= to, let h = LocalRawStore.num(p["hr_max"]), h > 0 { hrMax[d] = h }
    }
    var status: Any = NSNull()
    if exercises {
      var byId: [String: String] = [:]
      let rules = (try DocRecord.fetchOne(db, key: RecordDocs.rules)).flatMap {
        (try? JSONSerialization.jsonObject(with: $0.body)) as? [[String: Any]]
      } ?? []
      for m in rules {
        if let id = m["app_exercise_id"] as? String, let st = m["status"] as? String { byId[id] = st }
      }
      status = byId
    }
    let profile = (try DocRecord.fetchOne(db, key: SeriesLocal.profileDoc))?.json
    let birth: Any = (profile?["birth_date"] as? String).map { String($0.prefix(10)) as Any } ?? NSNull()
    return ["timezone": zone.identifier, "workouts": workouts, "sparky": sparky, "sparky_error": NSNull(),
            "imported": [Any](), "targets": [String: Any](), "status": status, "metrics": metrics, "scores": scores,
            "hr_max": hrMax, "birth_date": birth, "can_scores": true, "can_metrics": true]
  }

  /// A payload value, else the column's (null when neither has one).
  static func pick(_ a: Any?, _ b: Double?) -> Any {
    if let a, !(a is NSNull) { return a }
    if let b { return b }
    return NSNull()
  }

  // MARK: - shadow comparison (field paths only, section 3.4)

  static func record(_ page: String, _ key: String, _ local: [String: Any], _ server: [String: Any], db: AppDatabase) {
    // the phone has no Hevy import and no SparkyFitness error texts of its own: those fields are not compared
    let c = SeriesLocal.compare(local: local, server: server, ignore: ["sparky_error"])
    var all = (try? db.meta(compareMeta)).flatMap { $0.data(using: .utf8) }
      .flatMap { (try? JSONSerialization.jsonObject(with: $0)) as? [String: Any] } ?? [:]
    all[page] = ["ok": c.ok, "fields_over": c.fieldsOver, "compared": c.compared, "key": key, "at": Date().timeIntervalSince1970]
    if let data = try? JSONSerialization.data(withJSONObject: all, options: [.sortedKeys]) {
      try? db.setMeta(compareMeta, String(decoding: data, as: UTF8.self))
    }
  }
}
