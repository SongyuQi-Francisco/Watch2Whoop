import Foundation
import GRDB

/// The bedtime goal (入睡目标; goals/sleep.py + goals/store.py, `GET / PUT / DELETE /v1/users/me/goals/sleep`) on the
/// phone. Documents:
///
/// | Key | What |
/// |---|---|
/// | `goal.sleep` | the server's view as it came, with `settings` (the stored goal row, rules/context.GOAL_FIELDS) |
/// | `goal.sleep.draft` | `{settings}` of a write still waiting in the offline queue (what the server will store) |
/// | `goal.sleep.local` | the phone's view (`OBS.sleepGoal` on the local sleep tables) while package `coach` is not off |
///
/// The settings the phone computes with are the draft's while a write is queued, else the server's. CoachLocal passes the
/// view of tonight to the sleep coach (as insights/coach._goal_view does), so the coach document carries the real goal.
/// Shadow compares the phone's view with the server's (meta `coach.compare`, page `sleep_goal`); the page draws the
/// phone's in primary, and also while a queued write makes the server's copy stale.
enum BedtimeGoal {
  static let serverKey = "goal.sleep"
  static let draftKey = "goal.sleep.draft"
  static let localKey = "goal.sleep.local"
  /// Offline-queue group and kind (last write wins: a newer save or clear replaces a queued one).
  static let group = "goal.sleep"
  static let kind = "sleep_goal"
  /// goals/store.HISTORY_DAYS: the nights before tonight the view reads.
  static let historyDays = 21
  /// goals/params.DEFAULT_PACE_MIN / DEFAULT_STEP_DAYS / PACE_RANGE.
  static let defaultPace = 20
  static let defaultStepDays = 3
  static let paceRange = 5...60
  /// The page's pace choices, minutes earlier per step (the Today page's PACES: 温和 / 标准 / 积极).
  static let paces = [10, 15, 20]

  // MARK: - time helpers (goals/sleep.py)

  /// Tonight's sleep day at `now`: the evening the night belongs to, the local date of (wall time − 12 h).
  static func tonight(_ now: Date, tz: TimeZone) -> String {
    var cal = Calendar(identifier: .gregorian)
    cal.timeZone = tz
    let day = LocalDay.string(now, tz)
    return cal.component(.hour, from: now) < 12 ? LocalDay.add(day, -1) : day
  }

  /// "HH:MM" → minutes relative to the midnight after the evening, [−720, 720) (sleep.to_off); nil when not a time.
  static func toOff(_ hhmm: String?) -> Int? {
    guard let p = hhmm?.split(separator: ":"), p.count >= 2, let h = Int(p[0]), let m = Int(p[1]),
          (0...23).contains(h), (0...59).contains(m) else { return nil }
    return ((h * 60 + m + 720) % 1440 + 1440) % 1440 - 720
  }

  /// Minutes relative to midnight → "HH:MM" (sleep.fmt).
  static func fmt(_ off: Int) -> String {
    let m = ((off % 1440) + 1440) % 1440
    return String(format: "%02d:%02d", m / 60, m % 60)
  }

  /// sleep.round5 on whole minutes (no .5 ties there).
  static func round5(_ x: Int) -> Int { Int((Double(x) / 5).rounded()) * 5 }

  // MARK: - settings

  /// A write of the goal is still on its way (pending or being sent; a failed one is the 「没提交」 list's).
  static func pendingWrite(_ db: Database) throws -> Bool {
    try Bool.fetchOne(db, sql: "SELECT EXISTS (SELECT 1 FROM outbox WHERE group_key = ? AND state IN ('pending', 'sending'))",
                      arguments: [group]) ?? false
  }

  /// The goal settings the phone computes with: the queued write's draft, else the server's cached row; nil = no goal.
  static func settings(_ db: Database) throws -> [String: Any]? {
    if try pendingWrite(db), let d = try DocRecord.fetchOne(db, key: draftKey)?.json {
      return d["settings"] as? [String: Any]
    }
    return try DocRecord.fetchOne(db, key: serverKey)?.json?["settings"] as? [String: Any]
  }

  /// What the server will store for this write (goals/store.set_goal): values not given fall back to the old goal, then
  /// to the defaults; the stepping restarts tonight from the recent average bedtime (rounded to 5 minutes), or from the
  /// target without one. `body` nil = clear (no goal). The server's own row replaces this once the write went through.
  static func draft(body: [String: Any]?, old: [String: Any]?, avgBedtime: String?, tonight: String) -> [String: Any]? {
    guard let body, let target = body["target_bedtime"] as? String, let targetOff = toOff(target) else { return nil }
    func pick(_ k: String, _ fallback: Any) -> Any {
      if let v = body[k], !(v is NSNull) { return v }
      if let v = old?[k], !(v is NSNull) { return v }
      return fallback
    }
    let start = toOff(avgBedtime).map(round5) ?? targetOff
    return ["target_bedtime": target, "target_wake": body["target_wake"] ?? NSNull(),
            "target_duration_min": pick("target_duration_min", NSNull()), "pace_min_per_step": pick("pace_min_per_step", defaultPace),
            "step_days": pick("step_days", defaultStepDays), "start_date": tonight, "start_bedtime_off": start,
            "status": body["status"] as? String ?? "active"]
  }

  /// The PUT body from the page's choices (target wake only when fixed).
  static func body(target: String, wake: String?, pace: Int) -> [String: Any] {
    var b: [String: Any] = ["target_bedtime": target, "pace_min_per_step": min(max(pace, paceRange.lowerBound), paceRange.upperBound)]
    if let wake { b["target_wake"] = wake }
    return b
  }

  /// Local first: the draft settings, then the queue entry (`PUT` with `body`, or `DELETE` when nil). Older queued
  /// writes of the goal are dropped (last write wins). Returns the entry's client id.
  @discardableResult
  static func write(_ appDb: AppDatabase, queue: OfflineQueue, body: [String: Any]?, now: Date = Date(), tz: TimeZone) throws -> String {
    let night = tonight(now, tz: tz)
    let (old, avg) = try appDb.writer.read { db -> ([String: Any]?, String?) in
      let shown = try DocRecord.fetchOne(db, key: localKey)?.json ?? DocRecord.fetchOne(db, key: serverKey)?.json
      return (try settings(db), (shown?["progress"] as? [String: Any])?["avg_bedtime_7d"] as? String)
    }
    let drafted = draft(body: body, old: old, avgBedtime: avg, tonight: night)
    try queue.supersede(group: group)
    let doc: [String: Any] = ["settings": drafted ?? NSNull(), "at": now.timeIntervalSince1970]
    try appDb.saveDoc(draftKey, json: try JSONSerialization.data(withJSONObject: doc, options: [.sortedKeys]))
    let data = try body.map { try JSONSerialization.data(withJSONObject: $0, options: [.sortedKeys]) }
    return try queue.enqueue(kind: kind, group: group, method: body == nil ? .delete : .put, path: Endpoint.sleepGoalPath,
                             body: data).clientId
  }

  // MARK: - the phone's view

  /// goals/store.view's loads on the local tables: the main sleeps of the 21 nights before `night` (+ the morning after)
  /// and the newest sleep need up to the morning after (latest_need).
  static func inputs(_ db: Database, night: String) throws -> (sessions: [[String: Any]], need: Any) {
    let sessions = try SeriesLocal.sessions(db, from: LocalDay.add(night, -historyDays), to: LocalDay.add(night, 1))
      .map { s -> [String: Any] in
        ["start_at": s["start_at"] ?? NSNull(), "end_at": s["end_at"] ?? NSNull(), "asleep_min": s["asleep_min"] ?? NSNull(),
         "is_main": s["is_main"] ?? false]
      }
    var need: Any = NSNull()
    for r in try Row.fetchAll(db, sql: "SELECT payload FROM daily_scores WHERE date <= ? ORDER BY date DESC LIMIT 60",
                              arguments: [LocalDay.add(night, 1)]) {
      if let v = TodayModel.num(CoachLocal.payload(r)["sleep_need_min"]) { need = v; break }
    }
    return (sessions, need)
  }

  /// The phone's view of `night` (`OBS.sleepGoal`), saved as `goal.sleep.local`; in shadow it is compared with the
  /// server's cached view of the same night when no write is queued.
  @discardableResult
  static func compute(_ appDb: AppDatabase, runtime rt: OpenBodyScoreRuntime, tz: String, night: String) throws -> [String: Any]? {
    let (goal, input, pending) = try appDb.writer.read { db in (try settings(db), try inputs(db, night: night), try pendingWrite(db)) }
    guard var out = try rt.invoke("sleepGoal", [goal ?? NSNull(), input.sessions, ["tz": tz, "night": night, "need": input.need]])
      as? [String: Any] else { return nil }
    out["computed_at"] = Date().timeIntervalSince1970
    try appDb.saveDoc(localKey, json: try JSONSerialization.data(withJSONObject: out))
    if !pending, let server = try appDb.doc(serverKey)?.json, server["night"] as? String == night {
      CoachLocal.record("sleep_goal", localKey, SeriesLocal.compare(local: out, server: server, ignore: ["settings"]), db: appDb)
    }
    return out
  }

  /// The view the sleep coach takes (insights/coach._goal_view): only with a target tonight (an active goal).
  static func coachGoal(_ view: [String: Any]?) -> Any {
    guard let view, view["tonight_target_bedtime"] as? String != nil else { return NSNull() }
    return view
  }

  /// What the page draws: the phone's view in primary, or while a queued write makes the server's stale (when the
  /// package computes at all); else the server's.
  static func shown(_ db: Database) throws -> Shown {
    let mode = CoachLocal.mode(db), pending = try pendingWrite(db)
    let local = mode == .off ? nil : try DocRecord.fetchOne(db, key: localKey)
    if let local, mode == .primary || pending { return Shown(body: local.body, local: true, pending: pending) }
    return Shown(body: try DocRecord.fetchOne(db, key: serverKey)?.body, local: false, pending: pending)
  }

  struct Shown: Equatable {
    var body: Data?
    var local: Bool
    var pending: Bool
  }

  // MARK: - refresh

  /// The page's refresh: queued writes first, then the server's view; the phone's view when the package computes.
  static func refresh(api: APIClient, db: AppDatabase, queue: OfflineQueue, tz: String) async -> DocPuller.Outcome {
    _ = try? await queue.drain(send: OfflineQueue.apiSender(api))
    let out = await DocPuller(api: api, db: db).pull([DocFetch(serverKey, Endpoint.sleepGoal())])
    if await CoachLocal.currentMode(db: db, api: api) != .off, let rt = try? AlgoBundles(db: db).runtime() {
      let zone = TimeZone(identifier: tz) ?? .current
      _ = try? compute(db, runtime: rt, tz: tz, night: tonight(Date(), tz: zone))
    }
    return out
  }
}

/// The page's numbers from a view (the server's `/goals/sleep` answer or the phone's `OBS.sleepGoal`).
struct BedtimeGoalModel: Equatable {
  struct Night: Equatable, Identifiable {
    var night: String
    var bedtime: String?
    var target: String?
    var onTarget: Bool?
    var id: String { night }
  }

  struct Step: Equatable {
    var date: String
    var bedtime: String
  }

  var night: String
  var target: String?
  var targetWake: String?
  var paused: Bool
  var tonight: String?
  var tonightWake: String?
  var start: String?
  var startDate: String?
  var pace: Int?
  var stepDays: Int?
  var duration: Double?
  /// The plan step in effect tonight (1-based) of `stages`; the next step's date and bedtime.
  var stage: Int?
  var stages: Int
  var next: Step?
  var avg7: String?
  var avgWake7: String?
  var nights7: Int
  var onTarget7: Int?
  var gap: Double?
  var behind: Double?
  var eta: String?
  var trend: Double?
  /// Seven slots, the nights before tonight (oldest first); a night without a main sleep has no bedtime.
  var nights: [Night]

  var hasGoal: Bool { target != nil }

  init?(json: [String: Any]) {
    guard let night = json["night"] as? String else { return nil }
    self.night = night
    target = json["target_bedtime"] as? String
    targetWake = json["target_wake"] as? String
    paused = json["status"] as? String == "paused"
    tonight = json["tonight_target_bedtime"] as? String
    tonightWake = json["tonight_target_wake"] as? String
    start = json["start_bedtime"] as? String
    startDate = json["start_date"] as? String
    pace = TodayModel.num(json["pace_min_per_step"]).map { Int($0) }
    stepDays = TodayModel.num(json["step_days"]).map { Int($0) }
    duration = TodayModel.num(json["target_duration_min"])
    let plan = (json["plan"] as? [[String: Any]] ?? []).compactMap { p -> (String, String)? in
      guard let d = p["from_date"] as? String, let b = p["bedtime"] as? String else { return nil }
      return (d, b)
    }
    stages = plan.count
    let done = plan.filter { $0.0 <= night }.count
    stage = done > 0 ? done : nil
    next = plan.first { $0.0 > night }.map { Step(date: $0.0, bedtime: $0.1) }
    let p = json["progress"] as? [String: Any] ?? [:]
    avg7 = p["avg_bedtime_7d"] as? String
    avgWake7 = p["avg_wake_7d"] as? String
    nights7 = TodayModel.num(p["nights_7d"]).map { Int($0) } ?? 0
    onTarget7 = TodayModel.num(p["on_target_nights_7d"]).map { Int($0) }
    gap = TodayModel.num(p["gap_min"])
    behind = TodayModel.num(p["behind_plan_min"])
    eta = p["eta_date"] as? String
    trend = TodayModel.num(p["trend_min_per_week"])
    var by: [String: [String: Any]] = [:]
    for r in json["recent_nights"] as? [[String: Any]] ?? [] { if let n = r["night"] as? String { by[n] = r } }
    nights = (1...7).reversed().map { k -> Night in
      let n = LocalDay.add(night, -k), r = by[n]
      return Night(night: n, bedtime: r?["bedtime"] as? String, target: r?["target"] as? String, onTarget: r?["on_target"] as? Bool)
    }
  }

  /// Minutes after 18:00 (the chart's scale: later = further down, across midnight; the Today page's offset18).
  static func offset18(_ hhmm: String?) -> Double? {
    guard let off = BedtimeGoal.toOff(hhmm) else { return nil }
    return Double(off + 360)   // 18:00 = −360 on the sleep-day scale
  }
}
