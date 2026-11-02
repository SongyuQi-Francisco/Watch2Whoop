import Foundation
import GRDB

/// 2.0.0 package M3 (docs/ios-native-2.0.md section 4): the Trends series, the month calendar, the last nights, the sleep
/// regularity card and the VO₂max level computed on the phone with the algorithm bundle (`src/33_series.js`, the port of
/// trends/series.compute, web/views month_days / sleep_nights, insights/sleep_detail and insights/fitness) from the local
/// tables. Nothing is uploaded and nothing persistent is written: the results are documents (section 3.1).
///
/// | Page | Server document | Phone's document |
/// |---|---|---|
/// | Trends, Today's 今日核心 / 需要注意 | `trends:<days>[:<end>]` | `trends.local.series:<days>[:<end>]` |
/// | Month calendar | `days:<month>` | `days.local:<month>` |
/// | Last nights | `trends.nights:<end>` | `sleep.local:nights:<end>` |
/// | Sleep regularity card | `trends.sleepdetails:<end>` | `sleep.local:details:<end>` |
/// | VO₂max level | `trends.vo2max:<end>` | `trends.local.vo2max:<end>` |
///
/// Package `series` (flag `native_series`, `GET /v1/sync/compute`): off = nothing is computed; shadow = computed and
/// compared with the server's document (field names of the differences in `meta` `series.compare`), the page still
/// draws the server's; primary = the page draws the phone's (the server's while the phone's is not there yet).
/// Inputs the phone does not compute yet (body age while package `coach` is not primary, workouts before M1c) and the stress series / cards
/// while package `stress` (M2, StressStage) is not primary come from the server's document of the same page
/// (`server_only` in the phone's document); with `stress` primary, heart-rate recovery is Apple's value until M1c.
enum SeriesLocal {
  static let package = "series"
  /// The compute state is asked again when the cached answer is older than this (seconds).
  static let stateMaxAge: Double = 3600
  static let profileDoc = "profile:me"
  static let profileMaxAge: Double = 86_400
  static let compareMeta = "series.compare"
  /// trends/series.py's data text for a series without data (AI-facing, not shown as is): 没有数据.
  static let noData = "\u{6CA1}\u{6709}\u{6570}\u{636E}"
  /// Relative tolerance of the shadow comparison (section 3.4: series values and prev_mean 1e-9 relative).
  static let tolerance = 1e-9

  static func trendsKey(_ days: Int, end: String?) -> String { "trends.local.series:\(days)" + (end.map { ":\($0)" } ?? "") }
  static func daysKey(_ month: String) -> String { "days.local:\(month)" }
  static func nightsKey(_ end: String?) -> String { "sleep.local:nights:\(end ?? "")" }
  static func detailsKey(_ end: String?) -> String { "sleep.local:details:\(end ?? "")" }
  static func vo2Key(_ end: String?) -> String { "trends.local.vo2max:\(end ?? "")" }

  // MARK: - which document a page draws

  static func mode(_ db: Database) -> ComputeState.Mode {
    guard let d = try? DocRecord.fetchOne(db, key: LocalCompute.stateDoc) else { return .off }
    return ComputeState.decode(d.body).mode(package)
  }

  /// The phone's document when the package is primary and it has been computed, else the server's. Read inside the
  /// page's observation, so the page follows a state change by itself.
  static func shown(_ db: Database, server: String, local: String) throws -> DocRecord? {
    if mode(db) == .primary, let l = try DocRecord.fetchOne(db, key: local) { return l }
    return try DocRecord.fetchOne(db, key: server)
  }

  // MARK: - jobs

  enum Job: Hashable {
    case trends(days: Int, end: String?)
    case month(String)
    case nights(end: String?)
    case details(end: String?)
    case vo2(end: String?)

    var localKey: String {
      switch self {
      case let .trends(days, end): return SeriesLocal.trendsKey(days, end: end)
      case let .month(m): return SeriesLocal.daysKey(m)
      case let .nights(end): return SeriesLocal.nightsKey(end)
      case let .details(end): return SeriesLocal.detailsKey(end)
      case let .vo2(end): return SeriesLocal.vo2Key(end)
      }
    }

    var serverKey: String {
      switch self {
      case let .trends(days, end): return TrendsDocs.trends(days, end: end)
      case let .month(m): return TodayDocs.days(m)
      case let .nights(end): return TrendsDocs.sleepNights(end)
      case let .details(end): return TrendsDocs.sleepDetails(end)
      case let .vo2(end): return TrendsDocs.vo2max(end)
      }
    }

    /// The comparison's name (meta `series.compare`).
    var name: String {
      switch self {
      case .trends: return "trends"
      case .month: return "days"
      case .nights: return "nights"
      case .details: return "sleep_details"
      case .vo2: return "vo2max"
      }
    }
  }

  /// What one computation needs besides the local tables.
  struct Context {
    var tz: String
    /// The user's today (`YYYY-MM-DD`).
    var today: String
    /// The local hour now (body battery of today).
    var nowHour: Int
  }

  @MainActor private static var running: Set<Job> = []

  /// After a page pulled its server documents: the compute state (cached, asked again after `stateMaxAge`), then the
  /// phone's documents of these jobs in the background (nothing when the package is off for this user).
  @MainActor
  static func refresh(session: AccountSession, _ jobs: [Job], userToday: String?) {
    let todo = jobs.filter { !running.contains($0) }
    guard !todo.isEmpty else { return }
    running.formUnion(todo)
    let db = session.db, api = session.api, tz = TrendsStore.timezone(session)
    Task.detached(priority: .utility) {
      defer { Task { @MainActor in running.subtract(todo) } }
      guard await currentMode(db: db, api: api) != .off else { return }
      let zone = TimeZone(identifier: tz) ?? .current
      var cal = Calendar(identifier: .gregorian)
      cal.timeZone = zone
      let ctx = Context(tz: tz, today: userToday ?? LocalDay.string(Date(), zone), nowHour: cal.component(.hour, from: Date()))
      await pullInputs(todo, db: db, api: api, ctx: ctx)
      guard let rt = try? OpenBodyScoreRuntime.builtin() else { return }
      for job in todo { _ = try? run(job, db: db, runtime: rt, ctx: ctx) }
    }
  }

  /// The cached state, asked again when older than `stateMaxAge` (offline: the cached one).
  static func currentMode(db: AppDatabase, api: APIClient) async -> ComputeState.Mode {
    if let d = try? db.doc(LocalCompute.stateDoc), Date().timeIntervalSince1970 - d.fetchedAt < stateMaxAge {
      return ComputeState.decode(d.body).mode(package)
    }
    return await LocalCompute(db: db, api: api).fetchState().mode(package)
  }

  /// Raw inputs only the server can give: the weigh-ins of the Trends windows (`ext_records`) and the profile (birth date,
  /// sex) of the VO₂max level.
  static func pullInputs(_ jobs: [Job], db: AppDatabase, api: APIClient, ctx: Context) async {
    let puller = DocPuller(api: api, db: db)
    for case let .trends(days, end) in jobs {
      let e = end ?? ctx.today
      _ = await puller.pullExtRecords(from: LocalDay.add(window(end: e, days: days).prevStart, -6), to: e)
    }
    if jobs.contains(where: { if case .vo2 = $0 { return true }; return false }) {
      let cached = try? db.doc(profileDoc)
      if cached == nil || Date().timeIntervalSince1970 - (cached?.fetchedAt ?? 0) > profileMaxAge {
        _ = await puller.pull([DocFetch(profileDoc, .get(Endpoint.profile))])
      }
    }
  }

  /// Computes one job, stores the phone's document, compares it with the server's (when cached). nil when the phone has
  /// nothing to compute from.
  @discardableResult
  static func run(_ job: Job, db: AppDatabase, runtime: OpenBodyScoreRuntime, ctx: Context) throws -> [String: Any]? {
    let server = try db.doc(job.serverKey)?.json
    guard var out = try compute(job, db: db, runtime: runtime, ctx: ctx, server: server) else { return nil }
    out["computed_at"] = Date().timeIntervalSince1970
    try db.saveDoc(job.localKey, json: try JSONSerialization.data(withJSONObject: out))
    if let server { record(job, compare(local: out, server: server, ignore: Set(out["server_only_paths"] as? [String] ?? [])), db: db) }
    return out
  }

  /// The phone's answer of one job (the server's JSON + `algo_version`, `bundle_version`, `server_only`).
  static func compute(_ job: Job, db: AppDatabase, runtime: OpenBodyScoreRuntime, ctx: Context,
                      server: [String: Any]?) throws -> [String: Any]? {
    _ = try? runtime.invoke("setLanguage", [Lang.code])
    switch job {
    case let .trends(days, end):
      let e = end ?? ctx.today
      let (data, serverOnly) = try db.writer.read { try trendsData($0, end: e, days: days) }
      guard (data["scores"] as? [Any])?.isEmpty == false || (data["metrics"] as? [Any])?.isEmpty == false else { return nil }
      let opts: [String: Any] = ["end": e, "days": days, "today": ctx.today, "tz": ctx.tz, "now_hour": ctx.nowHour]
      guard let out = try runtime.invoke("trendsSeries", [data, opts]) as? [String: Any] else { return nil }
      return overlay(out, server: server, serverOnly: serverOnly)
    case let .month(month):
      let args = try db.writer.read { try monthArgs($0, month: month, today: ctx.today, server: server) }
      return try runtime.invoke("monthDays", args) as? [String: Any]
    case let .nights(end):
      let e = end ?? ctx.today
      let (sessions, scores) = try db.writer.read { try nightsArgs($0, end: e, days: 14) }
      guard !sessions.isEmpty || !scores.isEmpty else { return nil }
      return try runtime.invoke("sleepNights", [sessions, scores, ["end": e, "days": 14, "tz": ctx.tz]]) as? [String: Any]
    case let .details(end):
      let e = end ?? ctx.today
      let (rows, nights) = try db.writer.read { try detailsArgs($0, end: e) }
      guard !rows.isEmpty || !nights.isEmpty else { return nil }
      return try runtime.invoke("sleepDetails", [rows, nights, ["end": e, "tz": ctx.tz]]) as? [String: Any]
    case let .vo2(end):
      let e = end ?? ctx.today
      let rows = try db.writer.read { db in
        try Row.fetchAll(db, sql: """
          SELECT date, value FROM daily_metrics WHERE metric = 'vo2_max' AND value IS NOT NULL AND date BETWEEN ? AND ?
          ORDER BY date
          """, arguments: [LocalDay.add(e, -364), e]).map { r -> [String: Any] in ["date": r["date"] as String, "value": r["value"] as Double] }
      }
      let p = try db.doc(profileDoc)?.json ?? [:]
      return try runtime.invoke("vo2max", [rows, nz(p["birth_date"]), nz(p["sex"]), e]) as? [String: Any]
    }
  }

  // MARK: - reading the local tables (what trends/series.load and the views read on the server)

  struct Window: Equatable {
    var start: String
    var prevStart: String
    var lo: String
  }

  /// trends/series.window: the range, the previous period of the same length, the first day read (90 days of baselines).
  static func window(end: String, days: Int) -> Window {
    let start = LocalDay.add(end, -(days - 1)), prev = LocalDay.add(start, -days)
    return Window(start: start, prevStart: prev, lo: LocalDay.add(prev, -90))
  }

  static let metrics = ["resting_heart_rate", "heart_rate_variability_sdnn", "vo2_max", "step_count", "active_energy_burned",
                        "basal_energy_burned", "time_in_daylight", "oxygen_saturation", "respiratory_rate",
                        "apple_sleeping_wrist_temperature"]

  /// Server-only keys while the phone lacks a table: series and status cards taken from the server's document.
  struct ServerOnly: Equatable {
    var series: [String] = []
    var status: [String] = []
    var isEmpty: Bool { series.isEmpty && status.isEmpty }
  }

  /// The `data` of `OBS.series.compute` from the local tables, and what has to come from the server's document.
  static func trendsData(_ db: Database, end: String, days: Int) throws -> ([String: Any], ServerOnly) {
    let w = window(end: end, days: days)
    var data: [String: Any] = ["can": ["scores": true, "metrics": true, "sleep": true, "weight": true, "training": true]]
    var only = ServerOnly()
    data["scores"] = try scoreRows(db, from: w.lo, to: end)
    // stress (M2), body age (M4), workouts' own heart-rate recovery (M1c): only from their tables when the phone has them.
    // Stress (and the cards that read it) only when package `stress` is primary: in shadow the page keeps the server's.
    let stressPrimary = StressStage.mode(db) == .primary
    if stressPrimary, let rows = try? StressStage.rows(db, from: w.lo, to: end) {
      data["stress"] = rows
    } else {
      only.series += ["stress", "resilience"]
      only.status += ["resilience", "body_battery"]
    }
    // body age (M4): the phone's weeks only when package `coach` is primary; in shadow the page keeps the server's
    if HealthspanStage.mode(db) == .primary, let rows = try? HealthspanStage.bodyAges(db, from: w.prevStart, to: end) {
      data["body_age"] = rows
    } else {
      only.series.append("body_age")
    }
    data["metrics"] = try Row.fetchAll(db, sql: """
      SELECT date, metric, value FROM daily_metrics WHERE metric IN (\(metrics.map { "'\($0)'" }.joined(separator: ",")))
      AND value IS NOT NULL AND date BETWEEN ? AND ? ORDER BY date, metric
      """, arguments: [w.lo, end]).map { r -> [String: Any] in
      ["date": r["date"] as String, "metric": r["metric"] as String, "value": r["value"] as Double]
    }
    data["sleep"] = try mainSleeps(db, from: w.lo, to: end)
    var apple: [String: Double] = [:]
    for r in try Row.fetchAll(db, sql: """
      SELECT date, value FROM daily_metrics WHERE metric = 'heart_rate_recovery_one_minute' AND value IS NOT NULL
      AND date BETWEEN ? AND ? ORDER BY date
      """, arguments: [w.lo, end]) {
      let d: String = r["date"], v: Double = r["value"]
      apple[d] = v
    }
    data["hrr_apple"] = apple
    if workoutsOnPhone(db), let rows = try? Row.fetchAll(db, sql: """
      SELECT local_date, max(hr_recovery_bpm) AS v FROM workouts WHERE dup_of IS NULL AND excluded IS NULL
      AND hr_recovery_bpm IS NOT NULL AND local_date BETWEEN ? AND ? GROUP BY local_date ORDER BY local_date
      """, arguments: [w.lo, end]) {
      var own: [String: Double] = [:]
      for r in rows {
        let d: String = r["local_date"], v: Double = r["v"]
        own[d] = v
      }
      data["hrr_workouts"] = own
    } else if stressPrimary {
      data["hrr_workouts"] = [String: Double]()   // M2 primary: the heart-rate-recovery values the phone has (Apple's)
    } else {
      only.series.append("hr_recovery")
      only.status.append("hr_recovery")
    }
    data["weights"] = try weights(db, from: LocalDay.add(w.prevStart, -6), to: end)
    return (data, only)
  }

  /// M1c's `workouts` table holds the phone's workouts only while the package computes (WorkoutsStage); empty (package
  /// off) or missing, the training days and the workouts' heart-rate recovery come from the server's documents.
  static func workoutsOnPhone(_ db: Database) -> Bool {
    (try? Bool.fetchOne(db, sql: "SELECT EXISTS (SELECT 1 FROM workouts)")) == true
  }

  /// The local score rows as the server's daily_scores rows (columns + flags + details), by date.
  static func scoreRows(_ db: Database, from: String, to: String) throws -> [[String: Any]] {
    try Row.fetchAll(db, sql: "SELECT date, recovery, sleep_score, strain, payload FROM daily_scores WHERE date BETWEEN ? AND ? ORDER BY date",
                     arguments: [from, to]).map { r in
      var row = (r["payload"] as Data?).flatMap { (try? JSONSerialization.jsonObject(with: $0)) as? [String: Any] } ?? [:]
      if row.isEmpty {
        row = ["recovery": nz(r["recovery"] as Double?), "sleep_score": nz(r["sleep_score"] as Double?),
               "strain": nz(r["strain"] as Double?)]
      }
      row["date"] = r["date"] as String
      return row
    }
  }

  /// The longest main sleep of each wake date (the server's `DISTINCT ON (wake_date) … ORDER BY asleep_min DESC NULLS LAST`).
  static func mainSleeps(_ db: Database, from: String, to: String) throws -> [[String: Any]] {
    var best: [String: (asleep: Double?, row: [String: Any])] = [:]
    var order: [String] = []
    for s in try sessions(db, from: from, to: to) where s["is_main"] as? Bool == true {
      let d = s["wake_date"] as! String, a = TodayModel.num(s["asleep_min"])
      if let cur = best[d] {
        if let a, a > (cur.asleep ?? -Double.infinity) { best[d] = (a, s) }
      } else {
        best[d] = (a, s)
        order.append(d)
      }
    }
    return order.sorted().compactMap { d in
      guard let s = best[d]?.row else { return nil }
      return ["wake_date": d, "start_at": nz(s["start_at"]), "end_at": nz(s["end_at"]), "asleep_min": nz(s["asleep_min"])]
    }
  }

  /// sleep_sessions rows of [from, to] by start, in the server's row shape (instants in epoch milliseconds).
  static func sessions(_ db: Database, from: String, to: String) throws -> [[String: Any]] {
    try Row.fetchAll(db, sql: "SELECT date, start, end, is_main, source, payload FROM sleep_sessions WHERE date BETWEEN ? AND ? ORDER BY start",
                     arguments: [from, to]).map { r in
      let p = (r["payload"] as Data?).flatMap { (try? JSONSerialization.jsonObject(with: $0)) as? [String: Any] } ?? [:]
      var row: [String: Any] = ["wake_date": r["date"] as String, "start_at": (r["start"] as Double) * 1000,
                                "end_at": (r["end"] as Double) * 1000, "is_main": r["is_main"] as Bool,
                                "source": nz((r["source"] as String?) ?? (p["source"] as? String))]
      for k in ["asleep_min", "deep_min", "core_min", "rem_min", "awake_min", "unspecified_min", "device"] {
        row[k] = nz(p[k])
      }
      return row
    }
  }

  /// Weigh-ins (kg) by day, the server's `trends/series.weights`: Apple's body mass, overridden by the weight log
  /// (records `weight`, then the log's raw rows `ext_records`, which is what the server reads), then this phone's
  /// unsent entries.
  static func weights(_ db: Database, from: String, to: String) throws -> [String: Double] {
    var out: [String: Double] = [:]
    for r in try Row.fetchAll(db, sql: """
      SELECT date, value FROM daily_metrics WHERE metric = 'body_mass' AND unit = 'kg' AND value IS NOT NULL AND date BETWEEN ? AND ?
      """, arguments: [from, to]) {
      let d: String = r["date"], v: Double = r["value"]
      out[d] = v
    }
    var pending: [String: Double] = [:]
    for r in try Row.fetchAll(db, sql: """
      SELECT date, payload, pending FROM records WHERE kind = 'weight' AND NOT deleted AND date BETWEEN ? AND ? ORDER BY updated_at
      """, arguments: [from, to]) {
      guard let d = r["date"] as String?, let data = r["payload"] as Data?,
            let p = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any], let kg = TodayModel.num(p["weight_kg"]) else { continue }
      out[d] = kg
      if r["pending"] as Bool { pending[d] = kg }
    }
    for doc in try DocRecord.fetchAll(db, sql: "SELECT * FROM docs WHERE key >= ? AND key <= ? ORDER BY key",
                                      arguments: [DocPuller.extRecordsKey(from), DocPuller.extRecordsKey(to)]) {
      guard let j = doc.json, let d = j["date"] as? String else { continue }
      for w in (j["weights"] as? [[String: Any]]) ?? [] { if let kg = TodayModel.num(w["weight_kg"]) { out[d] = kg } }
    }
    for (d, kg) in pending { out[d] = kg }
    return out
  }

  /// `monthDays` arguments: [month, today, scores, present, firsts, hidden] (web/views.month_days).
  static func monthArgs(_ db: Database, month: String, today: String, server: [String: Any]?) throws -> [Any] {
    let start = "\(month)-01", end = LocalDay.add(MonthDays.addMonths(month, 1) + "-01", -1)
    var scores: [String: Any] = [:]
    for r in try Row.fetchAll(db, sql: "SELECT date, recovery, sleep_score, strain FROM daily_scores WHERE date BETWEEN ? AND ?",
                              arguments: [start, end]) {
      let d: String = r["date"]
      scores[d] = ["recovery": nz(r["recovery"] as Double?), "sleep_score": nz(r["sleep_score"] as Double?),
                   "strain": nz(r["strain"] as Double?)]
    }
    var present: [String: [String]] = [
      "sleep": try String.fetchAll(db, sql: "SELECT DISTINCT date FROM sleep_sessions WHERE is_main AND date BETWEEN ? AND ?", arguments: [start, end]),
      "metrics": try String.fetchAll(db, sql: "SELECT DISTINCT date FROM daily_metrics WHERE date BETWEEN ? AND ?", arguments: [start, end]),
    ]
    var firsts: [Any] = [
      nz(try String.fetchOne(db, sql: "SELECT min(date) FROM daily_scores")),
      nz(try String.fetchOne(db, sql: "SELECT min(date) FROM sleep_sessions WHERE is_main")),
      nz(try String.fetchOne(db, sql: "SELECT min(date) FROM daily_metrics")),
    ]
    if workoutsOnPhone(db), let days = try? String.fetchAll(db, sql: """
      SELECT DISTINCT local_date FROM workouts WHERE dup_of IS NULL AND excluded IS NULL AND local_date BETWEEN ? AND ?
      """, arguments: [start, end]) {
      present["training"] = days
      let first = (try? String.fetchOne(db, sql: "SELECT min(local_date) FROM workouts WHERE dup_of IS NULL AND excluded IS NULL")) ?? nil
      firsts.append(nz(first))
    } else if let s = server, s["month"] as? String == month,
              let sd = s["days"] as? [[String: Any]], sd.contains(where: { $0["training"] is Bool }) {
      // training days: the server's until M1c (workouts) is on the phone; null there (no source) stays null here
      present["training"] = sd.filter { $0["training"] as? Bool == true }.compactMap { $0["date"] as? String }
    }
    if let f = server?["first"] as? String { firsts.append(f) }   // history the phone may not hold (old imports)
    return [month, today, scores, present, firsts, [String]()]
  }

  /// `sleepNights` arguments: the sessions of the 14 nights and the score rows of those days.
  static func nightsArgs(_ db: Database, end: String, days: Int) throws -> ([[String: Any]], [String: Any]) {
    let start = LocalDay.add(end, -(days - 1))
    var scores: [String: Any] = [:]
    for row in try scoreRows(db, from: start, to: end) {
      var s: [String: Any] = [:]
      for k in ["sleep_score", "sleep_actual_min", "sleep_need_min", "sleep_debt_min", "flags"] { s[k] = nz(row[k]) }
      scores[row["date"] as! String] = s
    }
    return (try sessions(db, from: start, to: end), scores)
  }

  /// `sleepDetails` arguments: the sessions of the 31 days before `end` (SRI 30 days + the day before) and the nights of
  /// the last 14 days (`details.sleep` of the score rows).
  static func detailsArgs(_ db: Database, end: String) throws -> ([[String: Any]], [[String: Any]]) {
    let nights: [[String: Any]] = try scoreRows(db, from: LocalDay.add(end, -13), to: end).compactMap { row in
      guard let s = (row["details"] as? [String: Any])?["sleep"] else { return nil }
      return ["date": nz(row["date"]), "sleep_score": nz(row["sleep_score"]), "s": s]
    }
    return (try sessions(db, from: LocalDay.add(end, -30), to: end), nights)
  }

  /// nil as JSON null.
  static func nz(_ v: Any?) -> Any { v ?? NSNull() }

  static func jsonValue(_ v: DatabaseValue) -> Any? {
    switch v.storage {
    case .blob(let d): return try? JSONSerialization.jsonObject(with: d, options: [.fragmentsAllowed])
    case .string(let s): return try? JSONSerialization.jsonObject(with: Data(s.utf8), options: [.fragmentsAllowed])
    default: return nil
    }
  }

  // MARK: - server-only parts, comparison

  /// The phone's Trends document with the server-only series and cards taken from the server's document of the same
  /// range (left out, "没有数据", when there is none: never a number computed from missing inputs).
  static func overlay(_ local: [String: Any], server: [String: Any]?, serverOnly: ServerOnly) -> [String: Any] {
    guard !serverOnly.isEmpty else { return local }
    var out = local
    var series = out["series"] as? [String: Any] ?? [:]
    var unavailable = out["unavailable"] as? [String: Any] ?? [:]
    var status = out["status"] as? [String: Any] ?? [:]
    let ss = server?["series"] as? [String: Any] ?? [:], su = server?["unavailable"] as? [String: Any] ?? [:]
    for k in serverOnly.series {
      if let v = ss[k] {
        series[k] = v
        unavailable[k] = nil
      } else {
        series[k] = nil
        unavailable[k] = nz(su[k] ?? noData)
      }
    }
    let st = server?["status"] as? [String: Any] ?? [:]
    for k in serverOnly.status { status[k] = nz(st[k]) }
    out["series"] = series
    out["unavailable"] = unavailable
    out["status"] = status
    out["server_only"] = ["series": serverOnly.series, "status": serverOnly.status]
    out["server_only_paths"] = serverOnly.series.map { "series.\($0)" } + serverOnly.status.map { "status.\($0)" }
      + serverOnly.series.map { "unavailable.\($0)" }
    return out
  }

  struct Comparison: Equatable {
    var ok: Bool
    /// Field paths only (`series.hrv.values`), never values (section 3.4).
    var fieldsOver: [String]
    var compared: Int
  }

  /// Every number within `tolerance` (relative), everything else equal; the phone's extra keys (versions, server-only
  /// parts, `computed_at`) and the server's `ignore` paths are left out.
  static func compare(local: [String: Any], server: [String: Any], ignore: Set<String> = []) -> Comparison {
    var over = Set<String>(), n = 0
    let skip: Set<String> = ["algo_version", "bundle_version", "server_only", "server_only_paths", "computed_at"]
    func walk(_ a: Any?, _ b: Any?, _ path: String) {
      if ignore.contains(path) { return }
      let a = a is NSNull ? nil : a, b = b is NSNull ? nil : b
      if let x = TodayModel.num(a), let y = TodayModel.num(b) {
        n += 1
        if x != y && abs(x - y) > Swift.max(tolerance * Swift.max(abs(x), abs(y)), tolerance) { over.insert(path) }
        return
      }
      if let x = a as? [String: Any], let y = b as? [String: Any] {
        for k in Set(x.keys).union(y.keys) where path.isEmpty ? !skip.contains(k) : true {
          walk(x[k], y[k], path.isEmpty ? k : "\(path).\(k)")
        }
        return
      }
      if let x = a as? [Any], let y = b as? [Any] {
        if x.count != y.count { over.insert(path); return }
        for (u, v) in zip(x, y) { walk(u, v, path) }
        return
      }
      n += 1
      if a == nil && b == nil { return }
      if let x = a as? String, let y = b as? String, x == y { return }
      if let x = a as? Bool, let y = b as? Bool, x == y { return }
      over.insert(path)
    }
    walk(local, server, "")
    return Comparison(ok: over.isEmpty, fieldsOver: over.sorted(), compared: n)
  }

  /// Keeps the last comparison of each job (names only) in `meta` `series.compare` (sync center / debugging).
  static func record(_ job: Job, _ c: Comparison, db: AppDatabase) {
    var all = (try? db.meta(compareMeta)).flatMap { $0.data(using: .utf8) }
      .flatMap { (try? JSONSerialization.jsonObject(with: $0)) as? [String: Any] } ?? [:]
    all[job.name] = ["ok": c.ok, "fields_over": c.fieldsOver, "compared": c.compared, "key": job.localKey,
                     "at": Date().timeIntervalSince1970]
    if let data = try? JSONSerialization.data(withJSONObject: all, options: [.sortedKeys]) {
      try? db.setMeta(compareMeta, String(decoding: data, as: UTF8.self))
    }
  }
}
