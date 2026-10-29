import Foundation
import GRDB

/// M6 (docs/ios-native-2.0.md section 4): the rule reminders evaluated on the phone with the bundle's `rulesLocal`
/// (packages/openbodyscore-js src/36_rules.js, the twin of the server's rules/context.assemble + rules/engine.evaluate_data,
/// i.e. `evaluate-rules`), uploaded as kind `reminders`. Runs last: it reads the scores and sleep the stages before wrote.
///
/// One run:
/// 1. when: after a new rules algorithm, on a new day, or when the last run is older than `recomputeEvery` (the water
///    reminder depends on the hour; a logged meal or workout changes the inputs);
/// 2. inputs (`raw`, rules/context.load_raw): the server's raw inputs document `GET /v1/users/me/rules/inputs?date=` (the
///    exercise rules, the SparkyFitness library / templates / weekly plan / training log / food log / weigh-ins as read,
///    the nutrition goal versions, the bedtime goal, plan day changes, diet constraints, water — rows, nothing computed;
///    cached as `rules:inputs:<day>`, so a run offline uses the last copy) and the phone's own tables: `daily_scores`
///    (7 days), the journal (`records` kind `journal`, pulled with ReportsLocal's filtered pull, unsent entries included),
///    body mass (`daily_metrics`), `sleep_sessions` (the bedtime goal's nights) and the newest sleep need;
/// 3. today at the run's time and yesterday at 23:59 (the server checks a settled day: section 3.4) → `reminders` (the
///    phone's table, statuses kept, sticky dismissals inherited like the server's `store`, rows of a rule that errored
///    kept) and `derived_rows` kind `reminders` ([run row, reminder rows + the status changed on this phone]); the Today
///    document `reminders.local:<day>` (the server's list shape) is rebuilt from the table;
/// 4. the server's own rows come back through the `records` stream filtered to kind `reminders` (own cursor): their ids
///    (PATCH /reminders/{id}) and statuses changed elsewhere (the AI, another device) — a status changed here and not yet
///    confirmed only moves forward;
/// 5. uploads once everything read from HealthKit is on the server; in shadow the Today page keeps the server's reminders
///    and meta `rules.compare` keeps the comparison with the server's list (field names only).
struct RulesStage: ComputeStage {
  static let name = "rules"
  static let package = "rules"
  static let implemented = true
  static let kind = "reminders"
  /// seconds between two evaluations of the same day
  static let recomputeEvery: Double = 900
  static let algoKey = "rules.algo"
  static let dayKey = "rules.day"
  static let atKey = "rules.at"
  static let compareMeta = "rules.compare"
  /// rules/context.py windows
  static let scoreDays = 7
  static let journalDays = 21
  static let weightDays = 21
  static let historyDays = 21
  /// rules/context.INPUT_KEYS: what the inputs document carries
  static let inputKeys = ["sessions", "exercises", "presets", "plans", "meta", "food", "nutrition_versions", "sparky_goals",
                          "sparky_weights", "sleep_goal", "overrides", "diet", "water", "water_goal", "errors", "missing"]
  static let scoreFields = ["recovery", "sleep_score", "sleep_debt_min", "sleep_actual_min", "sleep_need_min", "strain", "flags"]
  static let levelOrder = ["alert": 0, "warning": 1, "info": 2]
  static let statusRank = ["new": 0, "seen": 1, "dismissed": 2]

  static func inputsKey(_ day: String) -> String { "rules:inputs:\(day)" }
  static func localKey(_ day: String) -> String { "reminders.local:\(day)" }

  /// The filtered records pull of the server's reminders (its own cursor: the server stamps it with the kind set).
  static let cursorKey = "rules.records_cursor.reminders"
  static let pullKinds: [String: [String]] = ["records": ["reminders"]]
  static let maxPullPages = 10

  // MARK: - host hooks (tests replace them)

  static var uploadGate: () async -> Bool = { await MainActor.run { HealthSyncService.shared.uploadedEverything } }
  static var runtime: (AppDatabase) throws -> OpenBodyScoreRuntime = { try AlgoBundles(db: $0).runtime() }
  /// Pulls the raw inputs documents of the days (tests: nothing).
  static var pullInputs: (AppDatabase, APIClient, [String]) async -> Void = { db, api, days in
    _ = await DocPuller(api: api, db: db).pull(days.map { d in
      DocFetch(inputsKey(d), APIRequest.get("/v1/users/me/rules/inputs", ["date": d]))
    })
  }
  static var pullJournal: (AppDatabase, APIClient) async -> Void = { db, api in await ReportsLocal.pullJournal(db: db, api: api) }
  static var pullReminders: (AppDatabase, APIClient) async -> Void = { db, api in await pullServerReminders(db: db, api: api) }

  // MARK: - one run

  func run(_ ctx: ComputeContext) async -> ComputeStageReport {
    var rep = ComputeStageReport(stage: Self.name, state: "done")
    let db = ctx.db, zone = ScoringSync.zone(db)
    let today = LocalDay.string(ctx.now, zone), yday = LocalDay.add(today, -1)
    let rt: OpenBodyScoreRuntime
    do { rt = try Self.runtime(db) } catch {
      rep.state = "failed"
      rep.detail = "bundle: \(error)"
      return rep
    }
    guard let algo = rt.algoVersions["rules"] else {
      rep.state = "skipped"
      rep.detail = "bundle without rules"
      return rep
    }
    if Self.due(db, algo: algo, today: today, now: ctx.now.timeIntervalSince1970) {
      await Self.pullInputs(db, ctx.api, [today, yday])
      await Self.pullJournal(db, ctx.api)
      await Self.pullReminders(db, ctx.api)
      do {
        for (day, now) in [(yday, Self.endOfDay(yday, zone)), (today, ctx.now)] {
          rep.written += try Self.compute(db: db, runtime: rt, day: day, now: now, zone: zone)
        }
        try db.setMeta(Self.algoKey, algo)
        try db.setMeta(Self.dayKey, today)
        try db.setMeta(Self.atKey, String(ctx.now.timeIntervalSince1970))
      } catch {
        rep.state = "failed"
        rep.detail = String(describing: error)
        return rep
      }
      Self.compareToday(db: db, day: today)
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

  /// 23:59 of `day` in the zone (yesterday's final evaluation).
  static func endOfDay(_ day: String, _ zone: TimeZone) -> Date {
    var cal = Calendar(identifier: .gregorian)
    cal.timeZone = zone
    let p = day.split(separator: "-").compactMap { Int($0) }
    return cal.date(from: DateComponents(year: p[0], month: p[1], day: p[2], hour: 23, minute: 59)) ?? Date()
  }

  /// ISO 8601 with the zone's offset (Python's datetime.isoformat of the local time).
  static func iso(_ d: Date, _ zone: TimeZone) -> String {
    let f = ISO8601DateFormatter()
    f.timeZone = zone
    f.formatOptions = [.withInternetDateTime]
    return f.string(from: d)
  }

  /// One day: raw → `rulesLocal` → the table, `derived_rows`, the Today document. Returns "reminders:<day>" when the
  /// uploaded rows changed; nothing without an inputs document (never evaluated from missing inputs).
  static func compute(db: AppDatabase, runtime rt: OpenBodyScoreRuntime, day: String, now: Date, zone: TimeZone) throws -> [String] {
    guard let raw = try db.writer.read({ try Self.raw($0, day: day, now: now, zone: zone) }) else { return [] }
    guard let out = try rt.invoke("rulesLocal", [raw]) as? [String: Any] else { return [] }
    let algo = out["algo_version"] as? String ?? rt.algoVersions["rules"] ?? "", nowText = raw["now"] as? String
    return try db.writer.write { db in try save(db, day: day, result: out, now: nowText, algo: algo, bundle: rt.version) }
  }

  // MARK: - the inputs (rules/context.load_raw on the local tables + the inputs document)

  static func payload(_ data: Data?) -> [String: Any] {
    data.flatMap { (try? JSONSerialization.jsonObject(with: $0)) as? [String: Any] } ?? [:]
  }

  /// `rulesLocal`'s raw input of `day`; nil without the server's inputs document of that day.
  static func raw(_ db: Database, day: String, now: Date, zone: TimeZone) throws -> [String: Any]? {
    guard let inputs = try DocRecord.fetchOne(db, key: inputsKey(day))?.json else { return nil }
    let nz = SeriesLocal.nz
    var raw: [String: Any] = ["day": day, "tz": zone.identifier, "now": iso(now, zone)]
    for k in inputKeys { raw[k] = nz(inputs[k]) }
    if raw["errors"] is NSNull { raw["errors"] = [String: Any]() }
    if raw["missing"] is NSNull { raw["missing"] = [String: Any]() }
    if raw["meta"] is NSNull { raw["meta"] = [Any]() }
    raw["scores"] = try SeriesLocal.scoreRows(db, from: LocalDay.add(day, -scoreDays), to: day).map { r -> [String: Any] in
      var row: [String: Any] = ["date": r["date"] ?? NSNull()]
      for k in scoreFields { row[k] = nz(r[k] is NSNull ? nil : r[k]) }
      if row["flags"] is NSNull { row["flags"] = [String: Any]() }
      return row
    }
    raw["journal"] = try journal(db, from: LocalDay.add(day, -journalDays), to: day)
    raw["body_mass"] = try Row.fetchAll(db, sql: """
      SELECT date, value FROM daily_metrics WHERE metric = 'body_mass' AND unit = 'kg' AND value IS NOT NULL AND date BETWEEN ? AND ?
      ORDER BY date
      """, arguments: [LocalDay.add(day, -(weightDays - 1)), day]).map { r -> [String: Any] in
      ["date": r["date"] as String, "value": r["value"] as Double]
    }
    raw["sleep_sessions"] = try SeriesLocal.sessions(db, from: LocalDay.add(day, -historyDays), to: LocalDay.add(day, 1))
      .map { s -> [String: Any] in
        ["wake_date": s["wake_date"] ?? NSNull(), "start_at": s["start_at"] ?? NSNull(), "end_at": s["end_at"] ?? NSNull(),
         "asleep_min": s["asleep_min"] ?? NSNull(), "is_main": s["is_main"] ?? false]
      }
    var need: Any = NSNull()
    for r in try Row.fetchAll(db, sql: "SELECT payload FROM daily_scores WHERE date <= ? ORDER BY date DESC LIMIT 60",
                              arguments: [LocalDay.add(day, 1)]) {
      if let v = TodayModel.num(payload(r["payload"] as Data?)["sleep_need_min"]) { need = v; break }
    }
    raw["sleep_need"] = need
    return raw
  }

  /// The journal answers of [from, to] as journal_entries rows ({date, key, value}): the server's copies and this phone's
  /// unsent ones, the newest per (date, key).
  static func journal(_ db: Database, from: String, to: String) throws -> [[String: Any]] {
    var by: [String: [String: Any]] = [:]
    for r in try Row.fetchAll(db, sql: """
      SELECT date, payload FROM records WHERE kind = 'journal' AND NOT deleted AND date BETWEEN ? AND ? ORDER BY updated_at
      """, arguments: [from, to]) {
      let p = payload(r["payload"] as Data?)
      guard let d = r["date"] as String?, let k = p["key"] as? String, let v = TodayModel.num(p["value"]) else { continue }
      by["\(d)|\(k)"] = ["date": String(d.prefix(10)), "key": k, "value": v]
    }
    return by.keys.sorted().compactMap { by[$0] }
  }

  // MARK: - storing what the bundle answered

  /// Inside the caller's transaction. Returns "reminders:<day>" when the upload rows changed.
  @discardableResult
  static func save(_ db: Database, day: String, result: [String: Any], now: String?, algo: String, bundle: String) throws -> [String] {
    let reminders = result["reminders"] as? [[String: Any]] ?? []
    let rules = result["rules"] as? [[String: Any]] ?? []
    let errored = Set(rules.filter { $0["status"] as? String == "error" }.compactMap { $0["rule_key"] as? String })
    let at = Date().timeIntervalSince1970
    var keep: Set<String> = []
    for r in reminders {
      guard let rule = r["rule_key"] as? String, let level = r["level"] as? String else { continue }
      let subject = r["subject"] as? String ?? ""
      keep.insert("\(rule)|\(subject)")
      var status = "new"
      if (r["sticky"] as? Bool) == true, let prev = try String.fetchOne(db, sql: """
        SELECT status FROM reminders WHERE rule_key = ? AND subject = ? AND date < ? ORDER BY date DESC LIMIT 1
        """, arguments: [rule, subject, day]), prev == "dismissed" {
        status = "dismissed"
      }
      try db.execute(sql: """
        INSERT INTO reminders (date, rule_key, subject, level, payload, status, algo_version, bundle_version, computed_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(date, rule_key, subject) DO UPDATE SET level = excluded.level, payload = excluded.payload,
          algo_version = excluded.algo_version, bundle_version = excluded.bundle_version, computed_at = excluded.computed_at
        """, arguments: [day, rule, subject, level, try JSONSerialization.data(withJSONObject: r, options: [.sortedKeys]),
                         status, algo, bundle, at])
    }
    // reminders that no longer hold are dropped, except those of a rule that errored (the server's store keeps them too)
    for r in try Row.fetchAll(db, sql: "SELECT rule_key, subject FROM reminders WHERE date = ?", arguments: [day]) {
      let rule: String = r["rule_key"], subject: String = r["subject"]
      if !keep.contains("\(rule)|\(subject)") && !errored.contains(rule) {
        try db.execute(sql: "DELETE FROM reminders WHERE date = ? AND rule_key = ? AND subject = ?", arguments: [day, rule, subject])
      }
    }
    let run: [String: Any] = ["role": "run", "now": now ?? NSNull(), "rules": rules]
    let changed = try keepDerived(db, day: day, run: run, algo: algo, bundle: bundle, at: at)
    try rebuildLocalDoc(db, day: day)
    return changed ? ["\(kind):\(day)"] : []
  }

  /// The upload rows of `day` from the table (+ the run row); unchanged rows (the run's time aside) keep their upload state.
  @discardableResult
  static func keepDerived(_ db: Database, day: String, run: [String: Any]?, algo: String, bundle: String, at: Double) throws -> Bool {
    let old = try Row.fetchOne(db, sql: "SELECT rows, algo_version FROM derived_rows WHERE kind = ? AND date = ?", arguments: [kind, day])
    let oldRows = (old.flatMap { (try? JSONSerialization.jsonObject(with: $0["rows"] as Data)) as? [[String: Any]] }) ?? []
    guard let runRow = run ?? oldRows.first(where: { $0["role"] as? String == "run" }) else { return false }
    var rows: [[String: Any]] = [runRow]
    for r in try Row.fetchAll(db, sql: "SELECT payload, status, status_dirty FROM reminders WHERE date = ? ORDER BY rowid",
                              arguments: [day]) {
      var row = payload(r["payload"] as Data?)
      row["role"] = "reminder"
      row["status"] = (r["status_dirty"] as Bool) ? (r["status"] as String) : NSNull()
      for k in ["algo_version", "bundle_version"] { row.removeValue(forKey: k) }
      rows.append(row)
    }
    func strip(_ xs: [[String: Any]]) -> Data? {
      try? JSONSerialization.data(withJSONObject: xs.map { r -> [String: Any] in
        var x = r
        if x["role"] as? String == "run" { x.removeValue(forKey: "now") }
        return x
      }, options: [.sortedKeys])
    }
    if let old, (old["algo_version"] as String) == algo, strip(oldRows) == strip(rows) { return false }
    try db.execute(sql: """
      INSERT INTO derived_rows (kind, date, rows, algo_version, bundle_version, computed_at, uploaded) VALUES (?, ?, ?, ?, ?, ?, 0)
      ON CONFLICT(kind, date) DO UPDATE SET rows = excluded.rows, algo_version = excluded.algo_version,
        bundle_version = excluded.bundle_version, computed_at = excluded.computed_at, uploaded = 0
      """, arguments: [kind, day, try JSONSerialization.data(withJSONObject: rows, options: [.sortedKeys]), algo, bundle, at])
    return true
  }

  /// `reminders.local:<day>`: the day's reminders in the server's list shape (GET /today `reminders`: new and seen, by
  /// level then rule); `id` = the server's id, or minus the local row id before the server has the row.
  static func rebuildLocalDoc(_ db: Database, day: String) throws {
    var items: [[String: Any]] = []
    for r in try Row.fetchAll(db, sql: """
      SELECT rowid, rule_key, subject, level, payload, status, server_id FROM reminders WHERE date = ? AND status != 'dismissed'
      """, arguments: [day]) {
      let p = payload(r["payload"] as Data?)
      let id: Int64 = (r["server_id"] as Int64?) ?? -(r["rowid"] as Int64)
      items.append(["id": id, "date": day, "rule_key": r["rule_key"] as String, "subject": r["subject"] as String,
                    "level": r["level"] as String, "title": p["title"] ?? "", "message": p["message"] ?? "",
                    "title_en": p["title_en"] ?? "", "message_en": p["message_en"] ?? "", "status": r["status"] as String])
    }
    items.sort { a, b in
      let la = levelOrder[a["level"] as? String ?? ""] ?? 3, lb = levelOrder[b["level"] as? String ?? ""] ?? 3
      if la != lb { return la < lb }
      let ra = a["rule_key"] as? String ?? "", rb = b["rule_key"] as? String ?? ""
      return ra != rb ? ra < rb : (a["subject"] as? String ?? "") < (b["subject"] as? String ?? "")
    }
    let doc = DocRecord(key: localKey(day), version: 0, body: try JSONSerialization.data(withJSONObject: ["date": day, "reminders": items]),
                        fetchedAt: Date().timeIntervalSince1970)
    try doc.save(db)
  }

  // MARK: - statuses

  /// The user changed a reminder's status on this phone (Today's 已看 / 不再提醒 / 标为未读): `id` = the server's id, or
  /// minus the local row id. Returns true when a local row was changed (the change rides along with the next upload).
  @discardableResult
  static func setStatus(_ db: AppDatabase, id: Int, status: String) -> Bool {
    (try? db.writer.write { db -> Bool in
      let row = id < 0
        ? try Row.fetchOne(db, sql: "SELECT rowid, date FROM reminders WHERE rowid = ?", arguments: [-id])
        : try Row.fetchOne(db, sql: "SELECT rowid, date FROM reminders WHERE server_id = ?", arguments: [id])
      guard let row else { return false }
      let day: String = row["date"]
      try db.execute(sql: "UPDATE reminders SET status = ?, status_dirty = 1 WHERE rowid = ?", arguments: [status, row["rowid"] as Int64])
      if let algo = try String.fetchOne(db, sql: "SELECT algo_version FROM derived_rows WHERE kind = ? AND date = ?", arguments: [kind, day]),
         let bundle = try String.fetchOne(db, sql: "SELECT bundle_version FROM derived_rows WHERE kind = ? AND date = ?", arguments: [kind, day]) {
        try keepDerived(db, day: day, run: nil, algo: algo, bundle: bundle, at: Date().timeIntervalSince1970)
      }
      try rebuildLocalDoc(db, day: day)
      return true
    }) ?? false
  }

  /// The server's rows of the pulled days: their ids, and their statuses unless this phone's own change is further along.
  static func applyServer(_ db: Database, day: String, items: [[String: Any]]) throws {
    for it in items {
      guard let rule = it["rule_key"] as? String, let id = (it["id"] as? NSNumber)?.int64Value,
            let status = it["status"] as? String else { continue }
      let subject = it["subject"] as? String ?? ""
      guard let local = try Row.fetchOne(db, sql: "SELECT status, status_dirty FROM reminders WHERE date = ? AND rule_key = ? AND subject = ?",
                                         arguments: [day, rule, subject]) else { continue }
      let mine: String = local["status"], dirty: Bool = local["status_dirty"]
      let take = !dirty || (statusRank[status] ?? 0) >= (statusRank[mine] ?? 0)
      try db.execute(sql: """
        UPDATE reminders SET server_id = ?, status = ?, status_dirty = ? WHERE date = ? AND rule_key = ? AND subject = ?
        """, arguments: [id, take ? status : mine, take ? false : dirty, day, rule, subject])
    }
    try rebuildLocalDoc(db, day: day)
  }

  /// Pulls the `records` stream filtered to `reminders` on this stage's own cursor (a refused cursor is dropped, so the
  /// next run restores from the start).
  static func pullServerReminders(db: AppDatabase, api: APIClient) async {
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
        for ch in changes where ch["stream"] as? String == "records" && ch["kind"] as? String == kind {
          guard let d = ch["date"] as? String else { continue }
          try applyServer(db, day: d, items: ((ch["body"] as? [String: Any])?["items"] as? [[String: Any]]) ?? [])
        }
        if let next {
          try db.execute(sql: "INSERT INTO meta (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                         arguments: [cursorKey, next])
        }
      }
      if page["more"] as? Bool != true { break }
    }
  }

  // MARK: - reading back (Today)

  static func mode(_ db: Database) -> ComputeState.Mode {
    guard let d = try? DocRecord.fetchOne(db, key: LocalCompute.stateDoc) else { return .off }
    return ComputeState.decode(d.body).mode(package)
  }

  /// The day's reminders in the app's language (title / message from the English twin when the app is in English).
  static func localized(_ items: [[String: Any]]) -> [[String: Any]] {
    items.map { it in
      var x = it
      if !Lang.isZh {
        if let t = it["title_en"] as? String, !t.isEmpty { x["title"] = t }
        if let m = it["message_en"] as? String, !m.isEmpty { x["message"] = m }
      }
      return x
    }
  }

  // MARK: - shadow comparison (field names only, section 3.4)

  /// The server's list of today (GET /today `reminders`, new and seen) against the phone's rows of the day: the same
  /// (rule, subject) pairs (`rows`), and for each the level and texts in the server's language.
  static func compareToday(db: AppDatabase, day: String) {
    let dated: [String: Any]? = (try? db.doc(DocKey.today(day)))?.json
    let current: [String: Any]? = (try? db.doc(DocKey.todayCurrent))?.json
    guard let t = dated ?? current, t["date"] as? String == day, let server = t["reminders"] as? [[String: Any]],
          let local = try? db.writer.read({ db in
            try Row.fetchAll(db, sql: "SELECT rule_key, subject, level, payload, status FROM reminders WHERE date = ?", arguments: [day])
          }) else { return }
    var over = Set<String>(), n = 0
    var mine: [String: (level: String, p: [String: Any], status: String)] = [:]
    for r in local {
      let rule: String = r["rule_key"], subject: String = r["subject"], level: String = r["level"], status: String = r["status"]
      mine["\(rule)|\(subject)"] = (level, payload(r["payload"] as Data?), status)
    }
    var seen = Set<String>()
    for s in server {
      let key = "\(s["rule_key"] as? String ?? "")|\(s["subject"] as? String ?? "")"
      seen.insert(key)
      n += 1
      guard let m = mine[key] else { over.insert("rows"); continue }
      if m.level != s["level"] as? String { over.insert("level") }
      let en = !Lang.isZh
      let title = (en ? (m.p["title_en"] as? String).flatMap { $0.isEmpty ? nil : $0 } : nil) ?? m.p["title"] as? String
      let message = (en ? (m.p["message_en"] as? String).flatMap { $0.isEmpty ? nil : $0 } : nil) ?? m.p["message"] as? String
      if title != s["title"] as? String { over.insert("title") }
      if message != s["message"] as? String { over.insert("message") }
    }
    for (k, m) in mine where m.status != "dismissed" && !seen.contains(k) { over.insert("rows") }
    let c: [String: Any] = ["ok": over.isEmpty, "fields_over": over.sorted(), "compared": n, "key": localKey(day),
                            "at": Date().timeIntervalSince1970]
    if let data = try? JSONSerialization.data(withJSONObject: ["reminders": c], options: [.sortedKeys]) {
      try? db.setMeta(compareMeta, String(decoding: data, as: UTF8.self))
    }
  }
}
