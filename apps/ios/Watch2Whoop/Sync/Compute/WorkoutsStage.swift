import Foundation
import GRDB
import HealthKit

/// M1c (docs/ios-native-2.0.md section 4): the phone's own workouts, with TRIMP, five-zone minutes, heart-rate recovery and
/// duplicates, computed with the bundle's `workoutDays` (packages/openbodyscore-js src/31_workouts.js, the twin of the
/// server's training/importer.workout_days). Runs before the scores.
///
/// One run:
/// 1. reads the HKWorkouts of the window the phone reads HealthKit for (`readHealth`, an anchored query; stored in
///    `hk_workouts` in the sync engine's shape, EngineWorkout.map) and marks their days dirty (`score_dirty` kind `workouts`);
/// 2. pulls the server's workouts (`pullServer`: the `records` stream's kind `workout` on its own cursor; Open Wearables,
///    Shortcut, export.zip and Hevy copies, the user's corrections on the app's rows) into `workouts` (computed_by server);
/// 3. computes the dirty days and the days whose server rows changed (every day with a workout after a new algorithm):
///    the app's rows → `derived_rows` kind `workouts` and `workouts` / `workout_hrr` (computed_by phone; the server's row of
///    the same workout stays in `server_row`), the duplicates of the server's rows around them; days whose kept workout
///    energy changed are marked dirty for the derivation (M1's `incomplete` rule reads it, as the server does);
/// 4. uploads the rows not uploaded yet once the server has everything this phone read (as DeriveStage).
/// The training calendar / day read these tables through `TrainingLocal` (Features/Record): the server's documents in
/// shadow, the phone's in primary.
struct WorkoutsStage: ComputeStage {
  static let name = "workouts"
  static let package = "workouts"
  static let implemented = true
  static let kind = "workouts"
  /// Days per bundle call.
  static let chunkDays = 14
  /// Heart rate read around a day's workouts (cardio/hrr.hrr60 looks 2 minutes before the end, enrich 3 minutes after).
  static let hrMargin: Double = 300
  /// The server's records pages per run (500 changes each); the rest comes next time.
  static let maxPullPages = 10
  /// meta: the derive algorithm last used, the records cursor of the server's workouts.
  static let algoKey = "workouts.algo"
  /// The cursor of the filtered pull (`kinds: {records: [workout]}`). The server stamps a filtered cursor with its kind set
  /// and refuses it for any other request, so this key is this stage's own and never feeds an unfiltered pull. (The key of
  /// 2.0.0 betas, `workouts.records_cursor`, held an unfiltered cursor and is not read any more.)
  static let cursorKey = "workouts.records_cursor.workout"
  /// The pull filter: only the workouts of the records stream come down, not the water, food, journal … of the user.
  static let pullKinds: [String: [String]] = ["records": ["workout"]]

  // MARK: - host hooks (tests replace them)

  /// Reads the HKWorkouts into `hk_workouts`; returns the local days that changed (nil: nothing is read, e.g. in tests).
  static var readHealth: ((AppDatabase, TimeZone) async -> Set<String>)? = { db, zone in
    let since = await MainActor.run { HealthSyncService.shared.uploadedSince }
    return await WorkoutsReader(db: db, zone: zone).read(uploadedSince: since)
  }
  /// Pulls the server's workouts into `workouts`; returns the local days that changed (nil: nothing is pulled).
  static var pullServer: ((AppDatabase, APIClient) async -> Set<String>)? = { db, api in await WorkoutsStage.pullServerWorkouts(db: db, api: api) }
  static var uploadGate: () async -> Bool = { await DeriveStage.uploadGate() }
  static var runtime: (AppDatabase) throws -> OpenBodyScoreRuntime = { try DeriveStage.runtime($0) }

  // MARK: - one run

  func run(_ ctx: ComputeContext) async -> ComputeStageReport {
    var rep = ComputeStageReport(stage: Self.name, state: "done")
    let db = ctx.db, zone = ScoringSync.zone(db)
    var changed = Set<String>()
    if let read = Self.readHealth { changed.formUnion(await read(db, zone)) }
    if let pull = Self.pullServer { changed.formUnion(await pull(db, ctx.api)) }
    if !changed.isEmpty { try? await db.writer.write { db in try LocalRawStore.markDirty(db, changed, kind: Self.kind) } }
    let rt: OpenBodyScoreRuntime
    do { rt = try Self.runtime(db) } catch {
      rep.state = "failed"
      rep.detail = "bundle: \(error)"
      return rep
    }
    guard let algo = rt.algoVersions["workouts"] else {
      rep.state = "skipped"
      rep.detail = "bundle without workouts"
      return rep
    }
    let today = LocalDay.string(ctx.now, zone)
    let started = Date().timeIntervalSince1970
    do {
      let days = try Self.daysToCompute(db, zone: zone, today: today, algo: algo)
      let birth = Self.birthDate(db)
      for chunk in stride(from: 0, to: days.count, by: Self.chunkDays).map({ Array(days[$0..<min(days.count, $0 + Self.chunkDays)]) }) {
        rep.written += try Self.computeChunk(db: db, zone: zone, runtime: rt, days: chunk, birthDate: birth)
      }
      try await db.writer.write { db in
        try db.execute(sql: "DELETE FROM score_dirty WHERE kind = ? AND date <= ? AND at <= ?", arguments: [Self.kind, today, started])
        try db.execute(sql: "INSERT INTO meta (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                       arguments: [Self.algoKey, algo])
      }
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

  /// The dirty days (up to today); every day with a workout after a new algorithm (or the first run).
  static func daysToCompute(_ db: AppDatabase, zone: TimeZone, today: String, algo: String) throws -> [String] {
    try db.writer.read { db -> [String] in
      let last = try String.fetchOne(db, sql: "SELECT value FROM meta WHERE key = ?", arguments: [Self.algoKey])
      var days = Set(try String.fetchAll(db, sql: "SELECT date FROM score_dirty WHERE kind = ? AND date <= ?",
                                         arguments: [Self.kind, today]))
      if last != algo {
        days.formUnion(try String.fetchAll(db, sql: "SELECT DISTINCT local_date FROM workouts WHERE source = 'app'"))
        for t in try Double.fetchAll(db, sql: "SELECT start FROM hk_workouts") {
          days.insert(LocalDay.string(Date(timeIntervalSince1970: t), zone))
        }
      }
      return days.filter { $0 <= today }.sorted()
    }
  }

  /// The profile's birth date (HRmax without the day's scores), from the cached profile document.
  static func birthDate(_ db: AppDatabase) -> String? {
    guard let p = (try? db.doc(SeriesLocal.profileDoc))?.json, let b = p["birth_date"] as? String, b.count >= 10 else { return nil }
    return String(b.prefix(10))
  }

  // MARK: - the bundle's input

  /// `workoutDays`' src for these days: the phone's HKWorkouts from the day before to the day after (duplicates), the
  /// server's rows of the other sources around them, the server's app rows as the corrections, heart rate over the days,
  /// HRmax of the days' scores, the birth date (HRmax without scores).
  static func source(_ db: Database, zone: TimeZone, days: [String], birthDate: String?) throws -> [String: Any] {
    guard let a = days.first, let b = days.last else { return [:] }
    let lo = dayStart(LocalDay.add(a, -1), zone), hi = dayStart(LocalDay.add(b, 2), zone)
    var hk: [[String: Any]] = []
    for r in try Row.fetchAll(db, sql: """
      SELECT w.uuid, w.activity, w.activity_name, w.start, w.end, w.duration_s, w.stats, w.meta, s.name, s.device_type, s.device_name
      FROM hk_workouts w LEFT JOIN hk_sources s ON s.id = w.source_id WHERE w.start >= ? AND w.start < ? ORDER BY w.start, w.uuid
      """, arguments: [lo, hi]) {
      let stats = (r["stats"] as Data?).flatMap { try? JSONSerialization.jsonObject(with: $0) } ?? [String: Any]()
      let meta = (r["meta"] as Data?).flatMap { (try? JSONSerialization.jsonObject(with: $0)) as? [String: Any] } ?? [:]
      let name = (r["name"] as String?) ?? ""
      hk.append(["uuid": r["uuid"] as String, "start": r["start"] as Double, "end": r["end"] as Double,
                 "value": ((r["duration_s"] as Double?) ?? 0) / 60, "value_text": nz(r["activity_name"] as String?),
                 "meta": ["activity": nz((r["activity"] as Int64?).map { NSNumber(value: $0) }), "stats": stats],
                 "tz": nz(meta["tz"] as? String), "source": name, "source_name": name,
                 "device_type": nz(r["device_type"] as String?), "device_name": nz(r["device_name"] as String?)])
    }
    var others: [[String: Any]] = [], overrides: [[String: Any]] = []
    for r in try Row.fetchAll(db, sql: """
      SELECT key, source, external_id, server_row, computed_by, row FROM workouts WHERE local_date >= ? AND local_date <= ?
      """, arguments: [LocalDay.add(a, -1), LocalDay.add(b, 1)]) {
      let source: String = r["source"]
      guard let s = (r["server_row"] as Data?).flatMap({ (try? JSONSerialization.jsonObject(with: $0)) as? [String: Any] }) else { continue }
      if source == "app" {
        // the server's row of an app workout carries the user's corrections (type, name, hidden; type rules applied)
        overrides.append(["source": "app", "external_id": r["external_id"] as String, "type": nz(s["type"]),
                          "name_zh": nz(s["name_zh"]), "hidden": (s["excluded"] as? String) == "user_hidden"])
        continue
      }
      let flags = s["flags"] as? [String: Any] ?? [:]
      let manual = (flags["manual_dup"] as? Bool) == true && !(s["dup_of"] is NSNull) && s["dup_of"] != nil
      var o: [String: Any] = ["source": source, "external_id": r["external_id"] as String, "type": nz(s["type"]),
                              "start": nz(s["start_at"]), "end": nz(s["end_at"]), "is_xml": (s["is_xml"] as? Bool) ?? false,
                              "device": nz(s["device"]), "excluded": nz(s["excluded"]), "manual_dup": manual]
      if manual, let id = (s["dup_of"] as? NSNumber)?.int64Value,
         let k = try String.fetchOne(db, sql: "SELECT key FROM workouts WHERE server_id = ?", arguments: [id]) {
        o["dup_of"] = k
      }
      others.append(o)
    }
    // heart rate over the days (watch first: the first value of an instant is kept), then the history's
    var hr: [[Double]] = []
    let hlo = dayStart(a, zone) - hrMargin, hhi = dayStart(LocalDay.add(b, 2), zone) + hrMargin
    for r in try Row.fetchAll(db, sql: """
      SELECT h.start, h.value FROM hk_samples h LEFT JOIN hk_sources s ON s.id = h.source_id
      WHERE h.type = 'HKQuantityTypeIdentifierHeartRate' AND h.start >= ? AND h.start <= ?
      ORDER BY CASE s.device_type WHEN 'watch' THEN 0 WHEN 'band' THEN 0 WHEN 'ring' THEN 0 WHEN 'phone' THEN 1 ELSE 2 END,
               h.start, h.uuid
      """, arguments: [hlo, hhi]) {
      hr.append([r["start"] as Double, r["value"] as Double])
    }
    if (try? db.tableExists("hist_samples")) == true {
      for r in try Row.fetchAll(db, sql: "SELECT t, value FROM hist_samples WHERE kind = ? AND t >= ? AND t <= ? ORDER BY t",
                                arguments: [LocalHistoryParams.Kind.hr.rawValue, hlo, hhi]) {
        hr.append([r["t"] as Double, r["value"] as Double])
      }
    }
    var hrMax: [String: Any] = [:]
    for r in try Row.fetchAll(db, sql: "SELECT date, payload FROM daily_scores WHERE date >= ? AND date <= ?", arguments: [a, b]) {
      let p = (r["payload"] as Data?).flatMap { (try? JSONSerialization.jsonObject(with: $0)) as? [String: Any] } ?? [:]
      if let v = LocalRawStore.num(p["hr_max"]), v > 0 { hrMax[r["date"] as String] = v }
    }
    return ["tz": zone.identifier, "days": days, "hk_workouts": hk, "others": others, "overrides": overrides, "type_rules": [Any](),
            "hr": ["cols": ["t", "value"], "rows": hr], "hr_max": hrMax, "birth_date": nz(birthDate)]
  }

  static func dayStart(_ d: String, _ zone: TimeZone) -> Double {
    var cal = Calendar(identifier: .gregorian)
    cal.timeZone = zone
    let p = d.split(separator: "-").compactMap { Int($0) }
    guard p.count == 3, let t = cal.date(from: DateComponents(year: p[0], month: p[1], day: p[2])) else { return 0 }
    return t.timeIntervalSince1970
  }

  // MARK: - one chunk

  /// The local rows → `workoutDays` → the local tables. Returns the "workouts:<day>" whose upload rows changed.
  static func computeChunk(db: AppDatabase, zone: TimeZone, runtime rt: OpenBodyScoreRuntime, days: [String],
                           birthDate: String?) throws -> [String] {
    let src = try db.writer.read { db in try source(db, zone: zone, days: days, birthDate: birthDate) }
    guard let out = try rt.invoke("workoutDays", [src]) as? [String: Any] else { throw AlgorithmError.load("workoutDays") }
    return try db.writer.write { db in try save(db, out: out, days: days, bundle: rt.version) }
  }

  /// Writes `workoutDays`' answer (inside the caller's transaction); returns the days whose upload rows changed.
  @discardableResult
  static func save(_ db: Database, out: [String: Any], days: [String], bundle: String) throws -> [String] {
    let algo = out["algo_version"] as? String ?? ""
    let now = Date().timeIntervalSince1970
    let byDay = out["days"] as? [String: Any] ?? [:], dups = out["dups"] as? [String: String] ?? [:]
    guard let a = days.first, let b = days.last else { return [] }
    let before = try keptKcal(db, from: a, to: b)
    var changed: [String] = []
    for d in days {
      let rows = byDay[d] as? [[String: Any]] ?? []
      if try keep(db, day: d, rows: rows, algo: algo, bundle: bundle, now: now) { changed.append("\(kind):\(d)") }
      let keys = rows.compactMap { $0["external_id"] as? String }.map { "app:\($0)" }
      // the phone's rows of the day that are gone (deleted in Health) go back to the server's copy, or away
      try db.execute(sql: """
        DELETE FROM workouts WHERE computed_by = 'phone' AND local_date = ? AND server_row IS NULL AND NOT (key IN (SELECT value FROM json_each(?)))
        """, arguments: [d, String(data: try JSONSerialization.data(withJSONObject: keys), encoding: .utf8)])
      try db.execute(sql: """
        UPDATE workouts SET computed_by = 'server', dup_of = NULL, row = server_row, updated_at = ?
        WHERE computed_by = 'phone' AND local_date = ? AND server_row IS NOT NULL AND NOT (key IN (SELECT value FROM json_each(?)))
        """, arguments: [now, d, String(data: try JSONSerialization.data(withJSONObject: keys), encoding: .utf8)])
      try db.execute(sql: "DELETE FROM workout_hrr WHERE local_date = ? AND computed_by = 'phone'", arguments: [d])
      for r in rows { try upsert(db, r, algo: algo, bundle: bundle, now: now) }
    }
    // the duplicates of the server's rows around these days, as the phone sees them (the app's copies rank first); a
    // server row that duplicates an app workout this phone did not read (older than its window) keeps the server's mark
    let lo = LocalDay.add(a, -1), hi = LocalDay.add(b, 1)
    let read = Set(try String.fetchAll(db, sql: "SELECT 'app:' || uuid FROM hk_workouts"))
    for r in try Row.fetchAll(db, sql: """
      SELECT key, dup_of FROM workouts WHERE computed_by = 'server' AND source <> 'app' AND local_date >= ? AND local_date <= ?
      """, arguments: [lo, hi]) {
      let k: String = r["key"], cur: String? = r["dup_of"]
      if dups[k] == nil, let cur, cur.hasPrefix("app:") || cur.hasPrefix("id:"), !read.contains(cur) { continue }
      try db.execute(sql: "UPDATE workouts SET dup_of = ? WHERE key = ?", arguments: [dups[k], k])
    }
    let after = try keptKcal(db, from: a, to: b)
    let energy = Set(Set(before.keys).union(after.keys).filter { abs((before[$0] ?? 0) - (after[$0] ?? 0)) > 1e-6 })
    if !energy.isEmpty { try LocalRawStore.markDirty(db, energy, kind: "derive") }
    return changed
  }

  /// Σ active energy of the kept workouts per day (the server's daily.day_src for the `incomplete` rule).
  static func keptKcal(_ db: Database, from: String, to: String) throws -> [String: Double] {
    var out: [String: Double] = [:]
    for r in try Row.fetchAll(db, sql: """
      SELECT local_date, sum(active_kcal) AS k FROM workouts WHERE dup_of IS NULL AND excluded IS NULL AND active_kcal IS NOT NULL
      AND local_date >= ? AND local_date <= ? GROUP BY local_date
      """, arguments: [from, to]) {
      out[r["local_date"] as String] = r["k"] as Double
    }
    return out
  }

  /// keptKcal for DeriveStage (an empty answer when the table cannot be read).
  static func keptKcalRows(_ db: Database, from: String, to: String) -> [String: Double] {
    (try? keptKcal(db, from: from, to: to)) ?? [:]
  }

  /// One upload row → the local `workouts` / `workout_hrr` rows of that workout (computed_by phone).
  static func upsert(_ db: Database, _ r: [String: Any], algo: String, bundle: String, now: Double) throws {
    guard let ext = r["external_id"] as? String, let d = r["local_date"] as? String else { return }
    let key = "app:\(ext)"
    let serverId = try Int64.fetchOne(db, sql: "SELECT server_id FROM workouts WHERE key = ?", arguments: [key])
    var flags = r["flags"] as? [String: Any] ?? [:]
    if let h = LocalRawStore.num(r["hr_recovery_bpm"]) { flags["hr_recovery_bpm"] = h }
    let zoneValues: [Any] = (1...5).compactMap { i -> Any? in
      guard let z = r["zone\(i)_min"], !(z is NSNull) else { return nil }
      return z
    }
    var zones: Any = NSNull()
    if zoneValues.count == 5 { zones = zoneValues }
    var idValue: Any = key
    if let serverId { idValue = NSNumber(value: serverId) }
    let row: [String: Any] = [
      "id": idValue, "source": "app", "external_id": ext, "device": nz(r["device"]),
      "origin": nz(r["origin"]), "type": nz(r["type"]), "name_zh": nz(r["name_zh"]), "start_at": nz(r["start"]),
      "end_at": nz(r["end"]), "local_date": d, "duration_min": nz(r["duration_min"]), "active_kcal": nz(r["active_kcal"]),
      "total_kcal": nz(r["total_kcal"]), "distance_km": nz(r["distance_km"]), "avg_hr": nz(r["avg_hr"]),
      "max_hr": nz(r["max_hr"]), "hr_zone_min": zones, "trimp": nz(r["trimp"]), "hr_max_used": nz(r["hr_max_used"]),
      "zone_offset": nz(r["zone_offset"]), "is_xml": false, "excluded": nz(r["excluded"]), "flags": flags]
    try db.execute(sql: """
      INSERT INTO workouts (key, server_id, source, external_id, local_date, type, start, end, duration_min, active_kcal, trimp,
                            hr_recovery_bpm, intensity, dup_of, excluded, computed_by, algo_version, bundle_version, row, updated_at)
      VALUES (?, ?, 'app', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'phone', ?, ?, ?, ?)
      ON CONFLICT(key) DO UPDATE SET local_date = excluded.local_date, type = excluded.type, start = excluded.start,
        end = excluded.end, duration_min = excluded.duration_min, active_kcal = excluded.active_kcal, trimp = excluded.trimp,
        hr_recovery_bpm = excluded.hr_recovery_bpm, intensity = excluded.intensity, dup_of = excluded.dup_of,
        excluded = excluded.excluded, computed_by = 'phone', algo_version = excluded.algo_version,
        bundle_version = excluded.bundle_version, row = excluded.row, updated_at = excluded.updated_at
      """, arguments: [key, serverId, ext, d, r["type"] as? String ?? "other", LocalRawStore.num(r["start"]) ?? 0,
                       LocalRawStore.num(r["end"]) ?? 0, LocalRawStore.num(r["duration_min"]) ?? 0,
                       LocalRawStore.num(r["active_kcal"]), LocalRawStore.num(r["trimp"]), LocalRawStore.num(r["hr_recovery_bpm"]),
                       r["intensity"] as? String, r["dup_of"] as? String, r["excluded"] as? String, algo, bundle,
                       try JSONSerialization.data(withJSONObject: row, options: [.sortedKeys]), now])
    if let valid = r["hrr_valid"] as? Bool {
      try db.execute(sql: """
        INSERT OR REPLACE INTO workout_hrr (key, local_date, end, peak_hr, hr_60s, hrr60, hr_max_used, valid, reason, computed_by, algo_version)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'phone', ?)
        """, arguments: [key, d, LocalRawStore.num(r["end"]) ?? 0, LocalRawStore.num(r["hrr_peak"]), LocalRawStore.num(r["hrr_60s"]),
                         LocalRawStore.num(r["hrr60"]), LocalRawStore.num(r["hr_max_used"]), valid, r["hrr_reason"] as? String, algo])
    }
  }

  /// `derived_rows` of one day: unchanged rows keep their upload state; returns true when they changed.
  static func keep(_ db: Database, day: String, rows: [[String: Any]], algo: String, bundle: String, now: Double) throws -> Bool {
    let data = try JSONSerialization.data(withJSONObject: rows, options: [.sortedKeys])
    let old = try Row.fetchOne(db, sql: "SELECT rows, algo_version FROM derived_rows WHERE kind = ? AND date = ?", arguments: [kind, day])
    if let old, (old["rows"] as Data) == data, (old["algo_version"] as String) == algo { return false }
    if old == nil, rows.isEmpty { return false }   // a day without the app's workouts is not sent until it had some
    try db.execute(sql: """
      INSERT INTO derived_rows (kind, date, rows, algo_version, bundle_version, computed_at, uploaded) VALUES (?, ?, ?, ?, ?, ?, 0)
      ON CONFLICT(kind, date) DO UPDATE SET rows = excluded.rows, algo_version = excluded.algo_version,
        bundle_version = excluded.bundle_version, computed_at = excluded.computed_at, uploaded = 0
      """, arguments: [kind, day, data, algo, bundle, now])
    return true
  }

  // MARK: - the server's workouts (records stream, kind workout)

  /// Pulls the `records` stream filtered to the kind `workout` (`pullKinds`) on this stage's own cursor (whole-day replace of
  /// the server's rows; a phone row of the same workout keeps the server's copy in `server_row`). Returns the days that changed.
  static func pullServerWorkouts(db: AppDatabase, api: APIClient) async -> Set<String> {
    var days = Set<String>()
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
      let got = (try? await db.writer.write { db -> Set<String> in
        var touched = Set<String>()
        for ch in changes where ch["stream"] as? String == "records" && ch["kind"] as? String == "workout" {
          guard let d = ch["date"] as? String else { continue }
          let items = ((ch["body"] as? [String: Any])?["items"] as? [[String: Any]]) ?? []
          try applyServerDay(db, day: d, items: items)
          touched.insert(d)
        }
        if let next {
          try db.execute(sql: "INSERT INTO meta (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                         arguments: [cursorKey, next])
        }
        return touched
      }) ?? []
      days.formUnion(got)
      if page["more"] as? Bool != true { break }
    }
    return days
  }

  /// One pulled day of the server's workouts.
  static func applyServerDay(_ db: Database, day: String, items: [[String: Any]]) throws {
    let now = Date().timeIntervalSince1970
    try db.execute(sql: "DELETE FROM workouts WHERE computed_by = 'server' AND local_date = ?", arguments: [day])
    try db.execute(sql: "DELETE FROM workout_hrr WHERE computed_by = 'server' AND local_date = ?", arguments: [day])
    try db.execute(sql: "UPDATE workouts SET server_row = NULL, server_id = NULL WHERE computed_by = 'phone' AND local_date = ?",
                   arguments: [day])
    for it in items {
      guard let source = it["source"] as? String, let ext = it["external_id"] as? String else { continue }
      let key = "\(source):\(ext)", data = try JSONSerialization.data(withJSONObject: it, options: [.sortedKeys])
      let id = (it["id"] as? NSNumber)?.int64Value
      if try Bool.fetchOne(db, sql: "SELECT EXISTS (SELECT 1 FROM workouts WHERE key = ? AND computed_by = 'phone')", arguments: [key]) == true {
        try db.execute(sql: "UPDATE workouts SET server_id = ?, server_row = ? WHERE key = ?", arguments: [id, data, key])
        continue
      }
      let flags = it["flags"] as? [String: Any] ?? [:]
      let dup = (it["dup_of"] as? NSNumber).map { "id:\($0.int64Value)" }
      try db.execute(sql: """
        INSERT OR REPLACE INTO workouts (key, server_id, source, external_id, local_date, type, start, end, duration_min, active_kcal,
                                         trimp, hr_recovery_bpm, intensity, dup_of, excluded, computed_by, row, server_row, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, 'server', ?, ?, ?)
        """, arguments: [key, id, source, ext, it["local_date"] as? String ?? day, it["type"] as? String ?? "other",
                         LocalRawStore.num(it["start_at"]) ?? 0, LocalRawStore.num(it["end_at"]) ?? 0,
                         LocalRawStore.num(it["duration_min"]) ?? 0, LocalRawStore.num(it["active_kcal"]), LocalRawStore.num(it["trimp"]),
                         LocalRawStore.num(flags["hr_recovery_bpm"]), dup, it["excluded"] as? String, data, data, now])
      if let h = it["hrr"] as? [String: Any], let valid = h["valid"] as? Bool {
        try db.execute(sql: """
          INSERT OR REPLACE INTO workout_hrr (key, local_date, end, peak_hr, hr_60s, hrr60, hr_max_used, valid, reason, computed_by)
          VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'server')
          """, arguments: [key, it["local_date"] as? String ?? day, LocalRawStore.num(it["end_at"]) ?? 0, LocalRawStore.num(h["peak_hr"]),
                           LocalRawStore.num(h["hr_60s"]), LocalRawStore.num(h["hrr60"]), LocalRawStore.num(h["hr_max_used"]), valid,
                           h["reason"] as? String])
      }
    }
    // the server's dup_of is its row id: the key of that row (once it is here)
    try db.execute(sql: """
      UPDATE workouts SET dup_of = COALESCE((SELECT w2.key FROM workouts w2 WHERE w2.server_id = CAST(substr(workouts.dup_of, 4) AS INTEGER)),
                                            dup_of)
      WHERE dup_of LIKE 'id:%'
      """)
  }
}

private func nz(_ v: Any?) -> Any { v.flatMap { $0 is NSNull ? nil : $0 } ?? NSNull() }

/// The HKWorkouts of the read window into `hk_workouts` (the sync engine's shape: EngineWorkout.map), with an anchored
/// query (anchor in the account database, `hk.workouts.anchor`).
final class WorkoutsReader {
  static let anchorKey = "hk.workouts.anchor"
  static let sinceKey = "hk.workouts.since"
  static let pageSize = 500
  private let db: AppDatabase
  private let zone: TimeZone
  private let store = HKHealthStore()

  init(db: AppDatabase, zone: TimeZone) {
    self.db = db
    self.zone = zone
  }

  func read(now: Date = Date(), uploadedSince: Date? = nil) async -> Set<String> {
    guard HKHealthStore.isHealthDataAvailable() else { return [] }
    let since = HealthKitScoringReader.since(now: now, uploadedSince: uploadedSince)
    if let previous = (try? db.meta(Self.sinceKey)).flatMap({ $0 }).flatMap(Double.init),
       HealthKitScoringReader.needsReread(previous: previous, next: since.timeIntervalSince1970) {
      try? await db.writer.write { db in try db.execute(sql: "DELETE FROM meta WHERE key = ?", arguments: [Self.anchorKey]) }
    }
    try? db.setMeta(Self.sinceKey, String(since.timeIntervalSince1970))
    var days = Set<String>()
    for _ in 0..<20 {
      let anchor = (try? db.meta(Self.anchorKey)).flatMap { $0 }.flatMap { Data(base64Encoded: $0) }
        .flatMap { try? NSKeyedUnarchiver.unarchivedObject(ofClass: HKQueryAnchor.self, from: $0) }
      guard let page = await query(anchor: anchor, since: since) else { break }
      if let d = try? save(page.workouts, deleted: page.deleted) { days.formUnion(d) }
      if let a = page.anchor, let data = try? NSKeyedArchiver.archivedData(withRootObject: a, requiringSecureCoding: true) {
        try? db.setMeta(Self.anchorKey, data.base64EncodedString())
      }
      if page.workouts.count + page.deleted.count < Self.pageSize { break }
    }
    return days
  }

  /// Stores the workouts (the sync engine's mapping) and drops the deleted ones; returns their local days.
  func save(_ workouts: [HKWorkout], deleted: [String]) throws -> Set<String> {
    let zone = zone
    return try db.writer.write { db -> Set<String> in
      var days = Set<String>()
      for w in workouts {
        guard let m = EngineWorkout.map(w, src: 0), let uuid = m["uuid"] as? String else { continue }
        let sid = try LocalRawStore.sourceId(db, HealthKitScoringReader.source(w))
        let start = w.startDate.timeIntervalSince1970
        try db.execute(sql: """
          INSERT OR REPLACE INTO hk_workouts (uuid, activity, activity_name, start, end, duration_s, stats, events, meta, source_id, uploaded)
          VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
          """, arguments: [uuid, m["activity"] as? Int, m["activity_name"] as? String, start, w.endDate.timeIntervalSince1970,
                           w.duration, try JSONSerialization.data(withJSONObject: m["stats"] ?? [String: Any]()),
                           try JSONSerialization.data(withJSONObject: m["events"] ?? [Any]()),
                           try JSONSerialization.data(withJSONObject: m["meta"] ?? [String: Any]()), sid])
        days.insert(LocalDay.string(w.startDate, zone))
      }
      for u in deleted {
        if let start = try Double.fetchOne(db, sql: "SELECT start FROM hk_workouts WHERE uuid = ?", arguments: [u]) {
          days.insert(LocalDay.string(Date(timeIntervalSince1970: start), zone))
          try db.execute(sql: "DELETE FROM hk_workouts WHERE uuid = ?", arguments: [u])
        }
      }
      return days
    }
  }

  private struct Page {
    var workouts: [HKWorkout]
    var deleted: [String]
    var anchor: HKQueryAnchor?
  }

  private func query(anchor: HKQueryAnchor?, since: Date) async -> Page? {
    await withCheckedContinuation { cont in
      let predicate = HKQuery.predicateForSamples(withStart: since, end: nil, options: [])
      let q = HKAnchoredObjectQuery(type: HKObjectType.workoutType(), predicate: predicate, anchor: anchor,
                                    limit: Self.pageSize) { _, added, deleted, next, error in
        if error != nil { cont.resume(returning: nil); return }
        cont.resume(returning: Page(workouts: (added ?? []).compactMap { $0 as? HKWorkout },
                                    deleted: (deleted ?? []).map { $0.uuid.uuidString.lowercased() }, anchor: next))
      }
      store.execute(q)
    }
  }
}
