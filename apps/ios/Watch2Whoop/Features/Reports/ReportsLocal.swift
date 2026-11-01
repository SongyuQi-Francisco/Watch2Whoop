import Foundation
import GRDB

/// 2.0.0 package M5 (docs/ios-native-2.0.md section 4): the weekly / monthly report and the journal analysis (行为影响)
/// computed on the phone with the algorithm bundle (`src/35_reports.js`, the port of insights/reports.compute and
/// records/analysis + records/regression) from the local tables. Nothing is uploaded and no table is written: the results
/// are documents (section 3.1), like M3's views.
///
/// | Page | Server document | Phone's document |
/// |---|---|---|
/// | Report (Trends → 周报 / 月报) | `report:<period>:<end>` | `report.local:<period>:<end>` |
/// | 行为影响 card | `journal.analysis[.r]:<end>` | `journal.analysis.local:[r:]<end>` (`.r` / `r:` = flag `journal_regression`) |
///
/// Package `reports` (flag `native_reports`, `GET /v1/sync/compute`): off = nothing is computed; shadow = computed and
/// compared with the server's document (field paths of the differences in `meta` `reports.compare`), the page still draws
/// the server's; primary = the page draws the phone's (the server's while the phone's is not there yet).
///
/// Inputs: `daily_scores`, `sleep_sessions`, `daily_metrics`, the journal (the `records` stream filtered to the kinds
/// `journal` and `journal_items` on this view's own cursor, kept in `records` like the server's other copies),
/// `ext_records` (training log, weigh-ins), `workouts` (M1c) and `stress_daily` (M2). A block whose input the phone does not
/// have is taken from the server's document of the same page (`server_only` in the phone's document), never computed from
/// missing inputs: nutrition until the goal versions are on the phone (M6: RulesStage's inputs document covering both
/// periods; then only its added-sugar, total-sugar and sugary-drink fields are the server's, the food extras stay there),
/// stress until package `stress` is primary, training until M1c's workouts are on the phone, training and weight while
/// the log cannot be read, the journal findings before the first journal pull.
enum ReportsLocal {
  static let package = "reports"
  static let compareMeta = "reports.compare"
  /// The filtered pull's cursor (the server stamps it with the kind set, docs/ios-native-2.0.md "Pull kind filter").
  static let cursorKey = "reports.records_cursor.journal,journal_items"
  static let pullKinds: [String: [String]] = ["records": ["journal", "journal_items"]]
  static let maxPullPages = 20
  /// "1" after a successful `ext_records` pull, "0" when the server could not read the log (training / weight from the
  /// server's document then); unchanged offline.
  static let extOkMeta = "reports.ext_ok"
  /// insights/reports_params.JOURNAL_DAYS: the journal window of the findings and of the 行为影响 card.
  static let journalDays = 90
  static let periodDays = ["week": 7, "month": 30]
  /// The owner sees every block (the report of someone else is never computed on this phone).
  static let ownScopes = ["journal", "nutrition", "nutrition_summary", "scores", "sleep", "training", "weight"]
  static let scoreFields = ["recovery", "sleep_score", "sleep_actual_min", "sleep_performance", "sleep_debt_min", "strain",
                            "trimp", "muscle_load", "ctl", "atl", "tsb", "rhr_night", "hrv_night_ln"]

  static func reportKey(_ period: String, end: String?) -> String { "report.local:\(period):\(end ?? "")" }
  static func analysisKey(end: String?, regression: Bool) -> String { "journal.analysis.local:\(regression ? "r:" : "")\(end ?? "")" }

  // MARK: - which document a page draws

  static func mode(_ db: Database) -> ComputeState.Mode {
    guard let d = try? DocRecord.fetchOne(db, key: LocalCompute.stateDoc) else { return .off }
    return ComputeState.decode(d.body).mode(package)
  }

  /// The phone's document when the package is primary and it has been computed, else the server's.
  static func shown(_ db: Database, server: String, local: String) throws -> DocRecord? {
    if mode(db) == .primary, let l = try DocRecord.fetchOne(db, key: local) { return l }
    return try DocRecord.fetchOne(db, key: server)
  }

  // MARK: - jobs

  enum Job: Hashable {
    case report(period: String, end: String?)
    case analysis(end: String?, regression: Bool)

    var localKey: String {
      switch self {
      case let .report(p, end): return ReportsLocal.reportKey(p, end: end)
      case let .analysis(end, r): return ReportsLocal.analysisKey(end: end, regression: r)
      }
    }

    var serverKey: String {
      switch self {
      case let .report(p, end): return ReportDocs.report(ReportModel.Period(rawValue: p) ?? .week, end: end)
      case let .analysis(end, r): return ReportDocs.behavior(end: end, regression: r)
      }
    }

    var name: String {
      switch self {
      case let .report(p, _): return "report_\(p)"
      case let .analysis(_, r): return r ? "journal_analysis_regression" : "journal_analysis"
      }
    }
  }

  struct Context {
    var tz: String
    /// The user's today (`YYYY-MM-DD`): the end of a page without one.
    var today: String
  }

  @MainActor private static var running: Set<Job> = []

  /// After the report page pulled its server documents: the compute state, the journal and the log's rows, then the
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
      let ctx = Context(tz: tz, today: userToday ?? LocalDay.string(Date(), TimeZone(identifier: tz) ?? .current))
      await pullInputs(todo, db: db, api: api, ctx: ctx)
      guard let rt = try? OpenBodyScoreRuntime.builtin() else { return }
      for job in todo { _ = try? run(job, db: db, runtime: rt, ctx: ctx) }
    }
  }

  static func currentMode(db: AppDatabase, api: APIClient) async -> ComputeState.Mode {
    if let d = try? db.doc(LocalCompute.stateDoc), Date().timeIntervalSince1970 - d.fetchedAt < SeriesLocal.stateMaxAge {
      return ComputeState.decode(d.body).mode(package)
    }
    return await LocalCompute(db: db, api: api).fetchState().mode(package)
  }

  /// The journal (filtered records pull) and, for a report, the log's rows of its windows (`ext_records`).
  static func pullInputs(_ jobs: [Job], db: AppDatabase, api: APIClient, ctx: Context) async {
    await pullJournal(db: db, api: api)
    let puller = DocPuller(api: api, db: db)
    for case let .report(p, end) in jobs {
      let e = end ?? ctx.today, n = periodDays[p] ?? 7
      let out = await puller.pullExtRecords(from: LocalDay.add(e, -(n - 1) - 30), to: e)
      if out.unavailable { try? db.setMeta(extOkMeta, "0") } else if !out.offline { try? db.setMeta(extOkMeta, "1") }
    }
  }

  /// Pulls the `records` stream filtered to `journal` and `journal_items` on this view's own cursor: whole-day replace of
  /// the server's journal answers (`records` kind `journal`, id `<date>:<key>`), the whole list of items (kind
  /// `journal_items`, id = key). Pending local answers (the offline queue's rows) are left alone.
  static func pullJournal(db: AppDatabase, api: APIClient) async {
    for _ in 0..<maxPullPages {
      let cursor = ((try? db.meta(cursorKey)) ?? nil) ?? ""
      let body: [String: Any] = ["cursors": ["records": cursor], "limit": 500, "kinds": pullKinds]
      let page: [String: Any]
      do {
        page = try await api.json(APIRequest(method: .post, path: Endpoint.syncPull,
                                             body: try JSONSerialization.data(withJSONObject: body), idempotent: true, timeout: 60))
      } catch APIError.http(422, _) {
        try? await db.writer.write { db in try db.execute(sql: "DELETE FROM meta WHERE key = ?", arguments: [cursorKey]) }
        break
      } catch {
        break
      }
      let changes = (page["changes"] as? [[String: Any]]) ?? []
      let next = (page["cursors"] as? [String: String])?["records"]
      try? await db.writer.write { db in
        for ch in changes where ch["stream"] as? String == "records" {
          let items = ((ch["body"] as? [String: Any])?["items"] as? [[String: Any]]) ?? []
          switch ch["kind"] as? String {
          case "journal":
            guard let d = ch["date"] as? String else { continue }
            try applyJournalDay(db, day: d, items: items)
          case "journal_items":
            try applyJournalItems(db, items: items)
          default:
            continue
          }
        }
        if let next {
          try db.execute(sql: "INSERT INTO meta (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                         arguments: [cursorKey, next])
        }
      }
      if page["more"] as? Bool != true { break }
    }
  }

  static func applyJournalDay(_ db: Database, day: String, items: [[String: Any]]) throws {
    let now = Date().timeIntervalSince1970
    try db.execute(sql: "DELETE FROM records WHERE kind = 'journal' AND pending = 0 AND date = ?", arguments: [day])
    for it in items {
      guard let key = it["key"] as? String else { continue }
      try db.execute(sql: """
        INSERT OR REPLACE INTO records (kind, id, date, server_id, payload, updated_at, pending) VALUES ('journal', ?, ?, ?, ?, ?, 0)
        """, arguments: ["\(day):\(key)", day, "\(day):\(key)", try JSONSerialization.data(withJSONObject: it, options: [.sortedKeys]), now])
    }
  }

  static func applyJournalItems(_ db: Database, items: [[String: Any]]) throws {
    let now = Date().timeIntervalSince1970
    try db.execute(sql: "DELETE FROM records WHERE kind = 'journal_items' AND pending = 0")
    for it in items {
      guard let key = it["key"] as? String else { continue }
      try db.execute(sql: """
        INSERT OR REPLACE INTO records (kind, id, server_id, payload, updated_at, pending) VALUES ('journal_items', ?, ?, ?, ?, 0)
        """, arguments: [key, key, try JSONSerialization.data(withJSONObject: it, options: [.sortedKeys]), now])
    }
  }

  /// Computes one job, stores the phone's document, compares it with the server's (when cached). nil when the phone has
  /// nothing to compute from.
  @discardableResult
  static func run(_ job: Job, db: AppDatabase, runtime: OpenBodyScoreRuntime, ctx: Context) throws -> [String: Any]? {
    let serverData = try db.doc(job.serverKey)?.body
    let server = serverData.flatMap { try? JSONSerialization.jsonObject(with: $0) }
    guard var out = try compute(job, db: db, runtime: runtime, ctx: ctx, server: server) else { return nil }
    out["computed_at"] = Date().timeIntervalSince1970
    try db.saveDoc(job.localKey, json: try JSONSerialization.data(withJSONObject: out))
    if let server {
      let ignore = Set(out["server_only_paths"] as? [String] ?? [])
      let c: SeriesLocal.Comparison
      if let list = server as? [Any] {
        c = SeriesLocal.compare(local: ["results": out["results"] ?? NSNull()], server: ["results": list], ignore: ignore)
      } else if let doc = server as? [String: Any] {
        c = SeriesLocal.compare(local: out, server: doc, ignore: ignore)
      } else {
        return out
      }
      record(job, c, db: db)
    }
    return out
  }

  /// The phone's answer of one job (the server's JSON + `algo_version`, `bundle_version`, `server_only`).
  static func compute(_ job: Job, db: AppDatabase, runtime: OpenBodyScoreRuntime, ctx: Context, server: Any?) throws -> [String: Any]? {
    let extOk = ((try? db.meta(extOkMeta)) ?? nil) == "1"
    let journalOk = ((try? db.meta(cursorKey)) ?? nil) != nil
    switch job {
    case let .report(p, end):
      let e = end ?? ctx.today
      var (data, only) = try db.writer.read { try reportData($0, period: p, end: e, tz: ctx.tz, extOk: extOk, journalOk: journalOk) }
      guard (data["scores"] as? [Any])?.isEmpty == false else { return nil }
      // M6: the nutrition block from the log's day sums and the goal versions once the phone resolves the goals
      var nutritionLocal = false
      if extOk, let days = try nutritionDays(db: db, runtime: runtime, period: p, end: e) {
        data["nutrition"] = ["days": days]
        only.removeAll { $0 == "nutrition" }
        nutritionLocal = true
      }
      guard let out = try runtime.invoke("report", [data, ["period": p, "end": e]]) as? [String: Any] else { return nil }
      let res = overlay(out, server: server as? [String: Any], serverOnly: only)
      return nutritionLocal ? overlayFields(res, server: server as? [String: Any], block: "nutrition", fields: nutritionServerFields) : res
    case let .analysis(end, regression):
      guard journalOk else { return nil }
      let e = end ?? ctx.today
      let data = try db.writer.read { try journalData($0, end: e, days: journalDays) }
      return try runtime.invoke("journalAnalysis", [data, ["end": e, "days": journalDays, "regression": regression]]) as? [String: Any]
    }
  }

  // MARK: - reading the local tables (what insights/reports.load and records/analysis.load_data read on the server)

  /// `OBS.report`'s data and the blocks that come from the server's document.
  static func reportData(_ db: Database, period: String, end: String, tz: String, extOk: Bool,
                         journalOk: Bool) throws -> ([String: Any], [String]) {
    let n = periodDays[period] ?? 7
    let start = LocalDay.add(end, -(n - 1)), pstart = LocalDay.add(end, -(2 * n - 1))
    var data: [String: Any] = ["tz": tz, "can": ownScopes, "nutrition_mode": "full"]
    var only = ["nutrition"]
    data["scores"] = try scoreRows(db, from: pstart, to: end)
    if StressStage.mode(db) == .primary, let rows = try? Row.fetchAll(db, sql: """
      SELECT date, score, high_h FROM stress_daily WHERE date BETWEEN ? AND ? ORDER BY date
      """, arguments: [pstart, end]) {
      data["stress"] = rows.map { r -> [String: Any] in
        ["date": r["date"] as String, "score": SeriesLocal.nz(r["score"] as Double?), "high_h": (r["high_h"] as Int?) ?? 0]
      }
    } else {
      only.append("stress")
    }
    data["sleep_sessions"] = try SeriesLocal.sessions(db, from: LocalDay.add(pstart, -1), to: end)
    if extOk && SeriesLocal.workoutsOnPhone(db) {
      let log = try extRows(db, field: "strength_sets", from: pstart, to: end), workouts = try workoutRows(db, from: pstart, to: end)
      data["training"] = ["log": log, "error": NSNull(), "workouts": workouts] as [String: Any]
    } else {
      only.append("training")
    }
    if extOk {
      let weights = try extRows(db, field: "weights", from: LocalDay.add(start, -30), to: end)
      data["weights"] = ["rows": weights]
    } else {
      only.append("weight")
    }
    if journalOk {
      data["journal"] = try journalData(db, end: end, days: journalDays)
    } else {
      only.append("journal_findings")
    }
    return (data, only)
  }

  /// daily_scores rows of [from, to] in the shape of reports.load's query (`s` = details.sleep, `rhr_daily`).
  static func scoreRows(_ db: Database, from: String, to: String) throws -> [[String: Any]] {
    try SeriesLocal.scoreRows(db, from: from, to: to).map { row in
      var out: [String: Any] = ["date": row["date"] ?? NSNull()]
      for k in scoreFields { out[k] = SeriesLocal.nz(row[k] is NSNull ? nil : row[k]) }
      if let s = (row["details"] as? [String: Any])?["sleep"] as? [String: Any] { out["s"] = s } else { out["s"] = NSNull() }
      out["rhr_daily"] = (row["flags"] as? [String: Any])?["rhr_from_daily"] != nil
      return out
    }
  }

  /// One field of the cached `ext_records` days in [from, to], concatenated in date order.
  static func extRows(_ db: Database, field: String, from: String, to: String) throws -> [[String: Any]] {
    try DocRecord.fetchAll(db, sql: "SELECT * FROM docs WHERE key >= ? AND key <= ? ORDER BY key",
                           arguments: [DocPuller.extRecordsKey(from), DocPuller.extRecordsKey(to)])
      .flatMap { ($0.json?[field] as? [[String: Any]]) ?? [] }
  }

  /// The kept workouts of [from, to] (`dup_of IS NULL AND excluded IS NULL`, as the server) with their name.
  static func workoutRows(_ db: Database, from: String, to: String) throws -> [[String: Any]] {
    try Row.fetchAll(db, sql: """
      SELECT local_date, duration_min, row FROM workouts WHERE dup_of IS NULL AND excluded IS NULL AND local_date BETWEEN ? AND ?
      ORDER BY local_date, start
      """, arguments: [from, to]).map { r in
      let row = (r["row"] as Data?).flatMap { (try? JSONSerialization.jsonObject(with: $0)) as? [String: Any] } ?? [:]
      return ["local_date": r["local_date"] as String, "duration_min": r["duration_min"] as Double,
              "name_zh": row["name_zh"] as? String ?? ""]
    }
  }

  /// records/analysis.load_data on the local tables: the active items in the server's order (sort, key), the journal
  /// answers of [end − days + 1, end], the scores of those days + 1 and the next days' resting heart rate and HRV.
  static func journalData(_ db: Database, end: String, days: Int) throws -> [String: Any] {
    let start = LocalDay.add(end, -(days - 1)), after = LocalDay.add(end, 1)
    func payload(_ r: Row) -> [String: Any] {
      (r["payload"] as Data?).flatMap { (try? JSONSerialization.jsonObject(with: $0)) as? [String: Any] } ?? [:]
    }
    var labels: [String: String] = [:]
    if let d = try DocRecord.fetchOne(db, key: RecordDocs.journalItems),
       let list = (try? JSONSerialization.jsonObject(with: d.body)) as? [[String: Any]] {
      for it in list { if let k = it["key"] as? String, let l = it["label"] as? String { labels[k] = l } }
    }
    let items = try Row.fetchAll(db, sql: "SELECT payload FROM records WHERE kind = 'journal_items' AND pending = 0").map(payload)
      .filter { $0["active"] as? Bool != false && $0["key"] is String }
      .sorted { a, b in
        let sa = TodayModel.num(a["sort"]) ?? .infinity, sb = TodayModel.num(b["sort"]) ?? .infinity   // NULLS LAST
        return sa != sb ? sa < sb : (a["key"] as! String) < (b["key"] as! String)
      }
      .map { it -> [String: Any] in
        var out: [String: Any] = [:]
        for k in ["key", "label_zh", "kind", "unit"] { out[k] = SeriesLocal.nz(it[k] is NSNull ? nil : it[k]) }
        if let k = it["key"] as? String, let l = labels[k] { out["label"] = l }
        return out
      }
    let entries = try Row.fetchAll(db, sql: """
      SELECT payload FROM records WHERE kind = 'journal' AND pending = 0 AND date BETWEEN ? AND ? ORDER BY date
      """, arguments: [start, end]).map(payload).compactMap { p -> [String: Any]? in
      guard let d = p["date"] as? String, let k = p["key"] as? String, let v = TodayModel.num(p["value"]) else { return nil }
      return ["date": String(d.prefix(10)), "key": k, "value": v]
    }
    var scores: [String: Any] = [:]
    for row in try SeriesLocal.scoreRows(db, from: start, to: after) {
      guard let d = row["date"] as? String else { continue }
      scores[d] = ["recovery": SeriesLocal.nz(TodayModel.num(row["recovery"])),
                   "sleep_actual_min": SeriesLocal.nz(TodayModel.num(row["sleep_actual_min"])),
                   "strain": SeriesLocal.nz(TodayModel.num(row["strain"]))]
    }
    var metrics: [String: [String: Any]] = [:]
    for r in try Row.fetchAll(db, sql: """
      SELECT date, metric, value FROM daily_metrics WHERE metric IN ('resting_heart_rate', 'heart_rate_variability_sdnn')
      AND value IS NOT NULL AND date BETWEEN ? AND ?
      """, arguments: [LocalDay.add(start, 1), after]) {
      let d: String = r["date"], m: String = r["metric"], v: Double = r["value"]
      metrics[d, default: [:]][m] = v
    }
    return ["items": items, "entries": entries, "scores": scores, "metrics": metrics]
  }

  // MARK: - M6: the nutrition block's goals on the phone

  /// The nutrition block's fields the phone cannot compute (added sugar, sugary drinks, total sugar: food_entry_extras
  /// and the entries' own values live on the server; the log's day sums carry no added sugar): taken from the server's
  /// document of the same period, never computed from missing inputs.
  static let nutritionServerFields = ["added_sugar_avg_g", "added_sugar_known_days", "added_sugar_over_cap_days", "sugar_avg_g",
                                      "sugary_drinks"]

  /// nutrition/daily day rows of both periods (OBS.nutritionDays = goals/nutrition.goal_for + day_from_totals) from the log's
  /// day sums (`ext_records` nutrition_totals) and the goal versions of RulesStage's newest inputs document; nil when the
  /// versions are not on the phone or a day of the window predates the first version (its goal would be SparkyFitness's).
  static func nutritionDays(db: AppDatabase, runtime rt: OpenBodyScoreRuntime, period: String, end: String) throws -> [[String: Any]]? {
    let n = periodDays[period] ?? 7
    let pstart = LocalDay.add(end, -(2 * n - 1))
    let read = try db.writer.read { db -> ([[String: Any]], [String: Any])? in
      guard let doc = try DocRecord.fetchOne(db, sql: "SELECT * FROM docs WHERE key LIKE 'rules:inputs:%' ORDER BY key DESC LIMIT 1"),
            let versions = doc.json?["nutrition_versions"] as? [[String: Any]], !versions.isEmpty,
            let first = versions.compactMap({ $0["effective_from"] as? String }).min(), first <= pstart else { return nil }
      var totals: [String: Any] = [:]
      for d in try DocRecord.fetchAll(db, sql: "SELECT * FROM docs WHERE key >= ? AND key <= ? ORDER BY key",
                                      arguments: [DocPuller.extRecordsKey(pstart), DocPuller.extRecordsKey(end)]) {
        guard let j = d.json, let day = j["date"] as? String, let t = j["nutrition_totals"], !(t is NSNull) else { continue }
        totals[day] = t
      }
      return (versions, totals)
    }
    guard let read else { return nil }
    _ = try? rt.invoke("setLanguage", [Lang.code])
    return try rt.invoke("nutritionDays", [read.1, read.0, pstart, end, [String: Any]()]) as? [[String: Any]]
  }

  /// Some fields of one block taken from the server's document (current, previous_values, change); their paths join
  /// `server_only_paths`, so the comparison skips them.
  static func overlayFields(_ local: [String: Any], server: [String: Any]?, block: String, fields: [String]) -> [String: Any] {
    var out = local
    var paths = out["server_only_paths"] as? [String] ?? []
    for part in ["current", "previous_values", "change"] {
      guard var p = out[part] as? [String: Any], var b = p[block] as? [String: Any] else { continue }
      let s = ((server?[part] as? [String: Any])?[block] as? [String: Any])
      for f in fields where b[f] != nil || s?[f] != nil {
        b[f] = s?[f] ?? NSNull()
        paths.append("\(part).\(block).\(f)")
      }
      p[block] = b
      out[part] = p
    }
    out["server_only_paths"] = paths
    return out
  }

  // MARK: - server-only blocks, comparison

  /// The phone's report with the server-only blocks taken from the server's document of the same period (left out when
  /// there is none: never a number computed from missing inputs).
  static func overlay(_ local: [String: Any], server: [String: Any]?, serverOnly: [String]) -> [String: Any] {
    guard !serverOnly.isEmpty else { return local }
    var out = local
    var paths: [String] = []
    for b in serverOnly {
      if b == "journal_findings" {
        out[b] = server?[b]
        paths.append(b)
        continue
      }
      for part in ["current", "previous_values", "change"] {
        var p = out[part] as? [String: Any] ?? [:]
        p[b] = (server?[part] as? [String: Any])?[b]
        out[part] = p
        paths.append("\(part).\(b)")
      }
    }
    out["server_only"] = serverOnly
    out["server_only_paths"] = paths
    return out
  }

  /// Keeps the last comparison of each job (field paths only) in `meta` `reports.compare`.
  static func record(_ job: Job, _ c: SeriesLocal.Comparison, db: AppDatabase) {
    var all = (try? db.meta(compareMeta)).flatMap { $0.data(using: .utf8) }
      .flatMap { (try? JSONSerialization.jsonObject(with: $0)) as? [String: Any] } ?? [:]
    all[job.name] = ["ok": c.ok, "fields_over": c.fieldsOver, "compared": c.compared, "key": job.localKey,
                     "at": Date().timeIntervalSince1970]
    if let data = try? JSONSerialization.data(withJSONObject: all, options: [.sortedKeys]) {
      try? db.setMeta(compareMeta, String(decoding: data, as: UTF8.self))
    }
  }
}
