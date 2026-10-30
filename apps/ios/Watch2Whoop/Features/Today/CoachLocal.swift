import Foundation
import GRDB

/// 2.0.0 package M4 (docs/ios-native-2.0.md section 4): the Today tab's coach blocks and the sleep detail computed on the
/// phone with the algorithm bundle (`src/34_coach.js`, the port of insights/coach.py, web/views.py's Today composition,
/// web/recovery_empty.py and sleep_night/view.py) from the local tables. Views only: nothing is uploaded (section 3.2);
/// body age is HealthspanStage's (uploaded as kind `healthspan`).
///
/// | Page | Server document | Phone's document |
/// |---|---|---|
/// | Today: strain target, health monitor, the rings' `scores` block, the night's `sleep` block, the sleep coach | `today.coach:<day>`, `today.monitor:<day>`, `today:<day>` (`scores`, `sleep`) | `coach.local:<day>` (`strain_coach`, `health_monitor`, `scores`, `sleep`, `sleep_coach`) |
/// | Sleep detail | `sleep:night:<day>` | `sleepnight.local:<day>` |
/// | Bedtime goal | `goal.sleep` | `goal.sleep.local` (BedtimeGoal; its settings feed the sleep coach) |
///
/// Package `coach` (flag `native_coach`): off = nothing is computed; shadow = computed and compared with the server's
/// documents (field paths of the differences in `meta` `coach.compare`), the pages draw the server's; primary = the pages
/// draw the phone's (the server's while the phone's is not there yet). The Today document's reminders, training and
/// nutrition blocks stay the server's (M6).
enum CoachLocal {
  static let package = "coach"
  static let compareMeta = "coach.compare"
  /// Days of score rows the blocks read: the sleep coach's 14 nights (scores params DEBT_NIGHTS), the health monitor's 60
  /// (insights/coach.MONITOR_DAYS), the Today scores' 7, the sleep detail's 30 (sleep_night/params.BASELINE_LONG).
  static let coachDays = 14
  static let monitorDays = 60
  static let nightDays = 30
  static let lookbackDays = 7

  static func coachKey(_ day: String) -> String { "coach.local:\(day)" }
  static func nightKey(_ day: String) -> String { "sleepnight.local:\(day)" }

  // MARK: - which document a page draws

  static func mode(_ db: Database) -> ComputeState.Mode { HealthspanStage.mode(db) }

  /// The phone's coach document of `day` when the package is primary and it has been computed (nil otherwise).
  static func shownCoach(_ db: Database, day: String) throws -> DocRecord? {
    guard mode(db) == .primary else { return nil }
    return try DocRecord.fetchOne(db, key: coachKey(day))
  }

  /// The sleep detail a page draws: the phone's in primary once computed, else the server's.
  static func shownNight(_ db: Database, serverKey: String, day: String?) throws -> DocRecord? {
    if mode(db) == .primary, let d = try day ?? latestNight(db), let l = try DocRecord.fetchOne(db, key: nightKey(d)) { return l }
    return try DocRecord.fetchOne(db, key: serverKey)
  }

  static func currentMode(db: AppDatabase, api: APIClient) async -> ComputeState.Mode {
    if let d = try? db.doc(LocalCompute.stateDoc), Date().timeIntervalSince1970 - d.fetchedAt < SeriesLocal.stateMaxAge {
      return ComputeState.decode(d.body).mode(package)
    }
    return await LocalCompute(db: db, api: api).fetchState().mode(package)
  }

  // MARK: - computing

  @MainActor private static var running: Set<String> = []

  /// After Today pulled its server documents: the phone's coach document of `day` (and the night's detail), in the
  /// background; nothing when the package is off for this user.
  @MainActor
  static func refresh(session: AccountSession, day: String, userToday: String?) {
    guard !running.contains(day) else { return }
    running.insert(day)
    let db = session.db, api = session.api, tz = TrendsStore.timezone(session)
    Task.detached(priority: .utility) {
      defer { Task { @MainActor in running.remove(day) } }
      guard await currentMode(db: db, api: api) != .off else { return }
      let zone = TimeZone(identifier: tz) ?? .current
      let today = userToday ?? LocalDay.string(Date(), zone)
      guard let rt = try? AlgoBundles(db: db).runtime() else { return }
      _ = try? coach(day, db: db, runtime: rt, tz: tz, today: today)
      _ = try? night(day, db: db, runtime: rt, tz: tz, today: today)
    }
  }

  /// The sleep detail page after its server pull (nil date = the latest night).
  @MainActor
  static func refreshNight(session: AccountSession, day: String?) {
    let db = session.db, api = session.api, tz = TrendsStore.timezone(session)
    Task.detached(priority: .utility) {
      guard await currentMode(db: db, api: api) != .off else { return }
      let zone = TimeZone(identifier: tz) ?? .current
      let today = LocalDay.string(Date(), zone)
      let latest: String? = (try? await db.writer.read { try latestNight($0) }) ?? nil
      guard let d = day ?? latest, let rt = try? AlgoBundles(db: db).runtime() else { return }
      _ = try? night(d, db: db, runtime: rt, tz: tz, today: today)
    }
  }

  /// `coach.local:<day>`: strain coach, health monitor, sleep coach and the Today `scores` / `sleep` blocks; compared with
  /// the server's documents of the day when cached.
  @discardableResult
  static func coach(_ day: String, db: AppDatabase, runtime rt: OpenBodyScoreRuntime, tz: String, today: String,
                    now: Date = Date()) throws -> [String: Any]? {
    let input = try db.writer.read { db in try coachInputs(db, day: day, today: today, tz: tz) }
    guard let input else { return nil }
    _ = try? rt.invoke("setLanguage", [Lang.code])
    var out: [String: Any] = ["date": day]
    out["strain_coach"] = try rt.invoke("strainCoach", [input.today, input.yday, day])
    out["health_monitor"] = try rt.invoke("healthMonitor", [input.monitor, day])
    // the bedtime goal's view of tonight from the cached settings (insights/coach._goal_view: the current goal, whatever
    // the day), also kept as the goal page's `goal.sleep.local`
    let night = BedtimeGoal.tonight(now, tz: TimeZone(identifier: tz) ?? .current)
    let goal = BedtimeGoal.coachGoal(try? BedtimeGoal.compute(db, runtime: rt, tz: tz, night: night))
    out["sleep_coach"] = try rt.invoke("sleepCoach", [input.coachRows, ["date": day, "tz": tz, "goal": goal]])
    var opts = input.scoresOpts
    opts["empty"] = try rt.invoke("recoveryEmpty", [input.empty])
    out["scores"] = try rt.invoke("todayScores", [input.week, opts])
    var timeline: Any = NSNull()
    if let main = input.main {
      timeline = try Self.timeline(rt, input.segments, main.start, main.end, tz)
    }
    out["sleep"] = try rt.invoke("todaySleep", [input.mains, ["date": day, "tz": tz, "timeline": timeline]])
    out["algo_version"] = rt.algoVersions["coach"] ?? NSNull()
    out["bundle_version"] = rt.version
    out["computed_at"] = Date().timeIntervalSince1970
    try db.saveDoc(coachKey(day), json: try JSONSerialization.data(withJSONObject: out))
    compareCoach(day, out, db: db)
    return out
  }

  /// `sleepnight.local:<day>` (sleep_night/view.compute on the local tables); compared with the server's when cached.
  @discardableResult
  static func night(_ day: String, db: AppDatabase, runtime rt: OpenBodyScoreRuntime, tz: String, today: String) throws -> [String: Any]? {
    let data = try db.writer.read { db in try nightData(db, day: day, today: today, tz: tz) }
    guard var data else { return nil }
    _ = try? rt.invoke("setLanguage", [Lang.code])
    if let segs = data.removeValue(forKey: "segments") as? [[String: Any]], let main = data.removeValue(forKey: "main") as? [Double] {
      data["timeline"] = try timeline(rt, segs, main[0], main[1], tz)
    }
    guard var out = try rt.invoke("sleepNight", [data]) as? [String: Any] else { return nil }
    out["computed_at"] = Date().timeIntervalSince1970
    try db.saveDoc(nightKey(day), json: try JSONSerialization.data(withJSONObject: out))
    if let server = try db.doc(TodayDocs.sleepNight(day))?.json {
      // the stage timeline's source may be another device on the server (Open Wearables): compared on its own
      record("sleep_night", nightKey(day), SeriesLocal.compare(local: out, server: server, ignore: ["main.stages_source", "today"]), db: db)
    }
    return out
  }

  /// web/views.night_stages' answer from the phone's own segments: [stages, "app", null], or none with the server's reason
  /// text (这晚没有阶段明细).
  static func timeline(_ rt: OpenBodyScoreRuntime, _ segments: [[String: Any]], _ start: Double, _ end: Double,
                       _ tz: String) throws -> [Any] {
    let stages = try rt.invoke("hypnogram", [segments, start, end, tz]) as? [Any] ?? []
    if stages.isEmpty { return [[Any](), NSNull(), "\u{8FD9}\u{665A}\u{6CA1}\u{6709}\u{9636}\u{6BB5}\u{660E}\u{7EC6}"] as [Any] }
    return [stages, "app", NSNull()] as [Any]
  }

  // MARK: - reading the local tables (what the server's loaders read)

  struct CoachInputs {
    var today: Any
    var yday: Any
    var monitor: [[String: Any]]
    var coachRows: [[String: Any]]
    var week: [String: Any]
    var scoresOpts: [String: Any]
    var empty: [String: Any]
    var mains: [[String: Any]]
    var main: (start: Double, end: Double)?
    var segments: [[String: Any]]
  }

  static func payload(_ r: Row) -> [String: Any] {
    (r["payload"] as Data?).flatMap { (try? JSONSerialization.jsonObject(with: $0)) as? [String: Any] } ?? [:]
  }

  /// A local score row in the server's scores/store.FIELDS shape (the payload; hrv_night_ms from the night's ln HRV).
  static func fieldsRow(_ date: String, _ p: [String: Any]) -> [String: Any] {
    var row = p
    row["date"] = date
    if row["hrv_night_ms"] == nil || row["hrv_night_ms"] is NSNull, let ln = LocalRawStore.num(p["hrv_night_ln"]) {
      row["hrv_night_ms"] = (exp(ln) * 10).rounded(.toNearestOrAwayFromZero) / 10   // round(exp(ln)::numeric, 1)
    }
    row.removeValue(forKey: "details")
    return row
  }

  static func coachInputs(_ db: Database, day: String, today: String, tz: String) throws -> CoachInputs? {
    let nz = SeriesLocal.nz
    var rows: [String: [String: Any]] = [:]
    for r in try Row.fetchAll(db, sql: "SELECT date, payload FROM daily_scores WHERE date BETWEEN ? AND ? ORDER BY date",
                              arguments: [LocalDay.add(day, -monitorDays), day]) {
      rows[r["date"] as String] = payload(r)
    }
    guard !rows.isEmpty else { return nil }
    let pick = { (d: String) -> Any in
      guard let p = rows[d] else { return NSNull() }
      let row: [String: Any] = ["recovery": nz(p["recovery"]), "strain": nz(p["strain"]), "tsb": nz(p["tsb"]),
                                "flags": p["flags"] ?? [String: Any]()]
      return row
    }
    var apple: [String: Double] = [:]
    for r in try Row.fetchAll(db, sql: """
      SELECT date, value FROM daily_metrics WHERE metric = 'resting_heart_rate' AND value IS NOT NULL AND date BETWEEN ? AND ?
      """, arguments: [LocalDay.add(day, -monitorDays), day]) {
      apple[r["date"] as String] = r["value"] as Double
    }
    let monitor: [[String: Any]] = rows.keys.sorted().map { d -> [String: Any] in
      let p = rows[d]!, det = p["details"] as? [String: Any]
      return ["date": d, "rhr_night": nz(p["rhr_night"]), "hrv_night_ln": nz(p["hrv_night_ln"]),
              "resp_rate_night": nz(p["resp_rate_night"]), "rec": nz(det?["recovery"]),
              "rhr_daily": (p["flags"] as? [String: Any])?["rhr_from_daily"] != nil, "apple_rhr": nz(apple[d])]
    }
    let coachRows: [[String: Any]] = rows.keys.filter { $0 > LocalDay.add(day, -coachDays) }.sorted(by: >).map { d -> [String: Any] in
      let p = rows[d]!
      return ["date": d, "sleep_actual_min": nz(p["sleep_actual_min"]), "strain": nz(p["strain"]),
              "s": nz((p["details"] as? [String: Any])?["sleep"])]
    }
    var week: [String: Any] = [:]
    for d in rows.keys where d >= LocalDay.add(day, -6) { week[d] = fieldsRow(d, rows[d]!) }
    let lastRec = try Row.fetchOne(db, sql: """
      SELECT date, payload FROM daily_scores WHERE date < ? AND recovery IS NOT NULL ORDER BY date DESC LIMIT 1
      """, arguments: [day]).map { r -> [String: Any] in
      let p = payload(r)
      return ["date": r["date"] as String, "recovery": nz(p["recovery"]), "sleep_score": nz(p["sleep_score"])]
    }
    let lastSleep = try Row.fetchOne(db, sql: """
      SELECT date, payload FROM daily_scores WHERE date < ? AND sleep_score IS NOT NULL ORDER BY date DESC LIMIT 1
      """, arguments: [day]).map { r -> [String: Any] in
      let p = payload(r)
      return ["date": r["date"] as String, "sleep_score": nz(p["sleep_score"]), "sleep_actual_min": nz(p["sleep_actual_min"]),
              "sleep_need_min": nz(p["sleep_need_min"]), "sleep_debt_min": nz(p["sleep_debt_min"])]
    }
    let computed = try Double.fetchOne(db, sql: "SELECT max(computed_at) FROM daily_scores")
    let opts: [String: Any] = ["date": day, "last": nz(lastRec), "last_sleep": nz(lastSleep),
                               "computed_at": nz(computed.map { $0 * 1000 })]
    // recovery_empty's facts on the phone: consent is the server's (an app user has given it), the origin is the first day
    // with a resting heart rate, a watch night within the 7 days before the day
    let sessions = try SeriesLocal.sessions(db, from: LocalDay.add(day, -lookbackDays), to: day)
    let watchNight = sessions.contains { s in
      ((s["device"] as? String)?.lowercased().contains("watch") ?? false)
        || ((TodayModel.num(s["core_min"]) ?? 0) + (TodayModel.num(s["deep_min"]) ?? 0) + (TodayModel.num(s["rem_min"]) ?? 0)) > 0
    }
    let origin = try HealthspanStage.origin(db)
    let anyData = try Bool.fetchOne(db, sql: "SELECT EXISTS (SELECT 1 FROM daily_metrics) OR EXISTS (SELECT 1 FROM sleep_sessions)") ?? false
    let empty: [String: Any] = ["date": day, "today": nz(rows[day]), "local_today": today, "needs_consent": false,
                                "origin": nz(origin), "any_data": anyData, "recent_watch_sleep": watchNight]
    let mains = sessions.filter { $0["is_main"] as? Bool == true }
    let latest = mainOf(mains, from: LocalDay.add(day, -lookbackDays), to: day)
    var segments: [[String: Any]] = []
    var main: (Double, Double)?
    if let l = latest, let s = TodayModel.num(l["start_at"]), let e = TodayModel.num(l["end_at"]) {
      main = (s, e)
      segments = try stageSegments(db, start: s / 1000, end: e / 1000)
    }
    return CoachInputs(today: pick(day), yday: pick(LocalDay.add(day, -1)), monitor: monitor, coachRows: coachRows, week: week,
                       scoresOpts: opts, empty: empty, mains: mains, main: main, segments: segments)
  }

  /// The main session the server picks for [from, to]: the newest wake date, the most minutes asleep (the earlier on a tie).
  static func mainOf(_ mains: [[String: Any]], from: String, to: String) -> [String: Any]? {
    var best: [String: Any]?
    for s in mains {
      guard let d = s["wake_date"] as? String, from <= d, d <= to else { continue }
      guard let b = best, let bd = b["wake_date"] as? String else { best = s; continue }
      if d > bd { best = s; continue }
      if d == bd, let a = TodayModel.num(s["asleep_min"]), a > (TodayModel.num(b["asleep_min"]) ?? -Double.infinity) { best = s }
    }
    return best
  }

  /// The local stage segments overlapping [start, end] (sleep_sessions.stages: [[stage, start, end]] in UTC seconds) as the
  /// hypnogram's input (instants in epoch milliseconds).
  static func stageSegments(_ db: Database, start: Double, end: Double) throws -> [[String: Any]] {
    var out: [[String: Any]] = []
    for r in try Row.fetchAll(db, sql: "SELECT stages FROM sleep_sessions WHERE stages IS NOT NULL AND start < ? AND end > ? ORDER BY start",
                              arguments: [end, start]) {
      guard let data = r["stages"] as Data?, let list = (try? JSONSerialization.jsonObject(with: data)) as? [[Any]] else { continue }
      for seg in list where seg.count >= 3 {
        guard let stage = seg[0] as? String, let a = TodayModel.num(seg[1]), let b = TodayModel.num(seg[2]) else { continue }
        out.append(["stage": stage, "start": a * 1000, "end": b * 1000])
      }
    }
    return out
  }

  /// The newest wake date with a main sleep.
  static func latestNight(_ db: Database) throws -> String? {
    try String.fetchOne(db, sql: "SELECT max(date) FROM sleep_sessions WHERE is_main")
  }

  /// sleep_night/view.load on the local tables (the timeline's raw segments and the night's start / end ride along).
  static func nightData(_ db: Database, day: String, today: String, tz: String) throws -> [String: Any]? {
    let lo = LocalDay.add(day, -nightDays)
    let sessions = try SeriesLocal.sessions(db, from: lo, to: day)
    let nz = SeriesLocal.nz
    var scores: [String: Any] = [:]
    for r in try Row.fetchAll(db, sql: "SELECT date, payload FROM daily_scores WHERE date BETWEEN ? AND ?", arguments: [lo, day]) {
      let p = payload(r), det = p["details"] as? [String: Any], d: String = r["date"]
      let ms = fieldsRow(d, p)["hrv_night_ms"]
      scores[d] = ["date": d, "sleep_score": nz(p["sleep_score"]), "sleep_performance": nz(p["sleep_performance"]),
                   "sleep_actual_min": nz(p["sleep_actual_min"]), "sleep_need_min": nz(p["sleep_need_min"]),
                   "sleep_debt_min": nz(p["sleep_debt_min"]), "hrv_ms": nz(ms), "rhr_night": nz(p["rhr_night"]),
                   "resp_rate_night": nz(p["resp_rate_night"]), "sleep": nz(det?["sleep"]), "recovery": nz(det?["recovery"]),
                   "flags": nz(p["flags"])]
    }
    guard !sessions.isEmpty || !scores.isEmpty else { return nil }
    let prev = try String.fetchOne(db, sql: "SELECT max(date) FROM sleep_sessions WHERE is_main AND date < ?", arguments: [day])
    let next = try String.fetchOne(db, sql: "SELECT min(date) FROM sleep_sessions WHERE is_main AND date > ? AND date <= ?",
                                   arguments: [day, today])
    var data: [String: Any] = ["tz": tz, "today": today, "day": day, "can_scores": true, "sessions": sessions, "scores": scores,
                               "prev": nz(prev), "next": nz(next), "timeline": NSNull()]
    if let m = mainOf(sessions.filter { $0["is_main"] as? Bool == true }, from: day, to: day),
       let s = TodayModel.num(m["start_at"]), let e = TodayModel.num(m["end_at"]) {
      data["segments"] = try stageSegments(db, start: s / 1000, end: e / 1000)
      data["main"] = [s, e]
    }
    return data
  }

  // MARK: - shadow comparison (field paths only, section 3.4)

  static func compareCoach(_ day: String, _ out: [String: Any], db: AppDatabase) {
    let key = coachKey(day)
    if let server = try? db.doc(TodayDocs.coach(day))?.json, let local = out["strain_coach"] as? [String: Any] {
      record("strain_coach", key, SeriesLocal.compare(local: local, server: server), db: db)
    }
    if let server = try? db.doc(TodayDocs.monitor(day))?.json, let local = out["health_monitor"] as? [String: Any] {
      record("health_monitor", key, SeriesLocal.compare(local: local, server: server), db: db)
    }
    let dated: [String: Any]? = (try? db.doc(DocKey.today(day)))?.json
    let current: [String: Any]? = (try? db.doc(DocKey.todayCurrent))?.json
    if let t = dated ?? current, t["date"] as? String == day {
      // the score rows themselves are passed through (the scores' own comparison is ScoringSync's); the stage timeline's
      // source may be another device on the server
      if let s = t["scores"] as? [String: Any], let l = out["scores"] as? [String: Any] {
        record("today_scores", key, SeriesLocal.compare(local: l, server: s, ignore: ["today", "yesterday", "computed_at",
                                                                                      "last_recovery", "last_sleep"]), db: db)
      }
      if let s = t["sleep"] as? [String: Any], let l = out["sleep"] as? [String: Any] {
        record("today_sleep", key, SeriesLocal.compare(local: l, server: s, ignore: ["stages", "stages_source", "stages_reason"]), db: db)
      }
    }
  }

  static func record(_ page: String, _ key: String, _ c: SeriesLocal.Comparison, db: AppDatabase) {
    var all = (try? db.meta(compareMeta)).flatMap { $0.data(using: .utf8) }
      .flatMap { (try? JSONSerialization.jsonObject(with: $0)) as? [String: Any] } ?? [:]
    all[page] = ["ok": c.ok, "fields_over": c.fieldsOver, "compared": c.compared, "key": key, "at": Date().timeIntervalSince1970]
    if let data = try? JSONSerialization.data(withJSONObject: all, options: [.sortedKeys]) {
      try? db.setMeta(compareMeta, String(decoding: data, as: UTF8.self))
    }
  }
}
