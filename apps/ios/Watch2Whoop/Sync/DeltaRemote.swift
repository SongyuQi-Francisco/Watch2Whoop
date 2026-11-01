import Foundation
import GRDB

/// N2's incremental pull (`POST /v1/sync/pull`, docs/ios-native-app.md sections 5.2 and 7.3) for what the phone scores
/// from (#340 N2b): the streams `scores`, `daily`, `sleep` and — during the first full restore only — `raw`.
/// - Each page is written in one transaction together with its cursors; `more` = pull the next page.
/// - An empty cursor is a full restore (newest day first). The raw restore stops at `LocalRawStore.keepDays`
///   (`raw_since`) so the phone scores within seconds; once it is done the raw stream is not pulled again: the phone
///   reads HealthKit itself, so pulling its own uploads back would only double the traffic. Everything older comes in
///   the background with the stream raw_history (`HistoryDownload`, local history); until then it reaches the scores
///   as stored rows (`scores`) and heart-rate histograms (`GET /v1/sync/scoring`).
/// - 422 `bad_cursor`: the cursors of the streams asked for are cleared and those streams restored again.
/// - `records` and `docs` are N5's (the user's records); the today documents still come from the legacy GETs.
struct DeltaRemote: RemoteSync {
  let api: APIClient
  var zone: () -> TimeZone = { .current }
  static let streams = ["scores", "daily", "sleep"]
  static let rawRestoredKey = "sync.raw_restored"
  static let maxPages = 400

  struct Outcome: Equatable {
    var changes = 0
    var pages = 0
    /// Local days whose scoring inputs changed (daily / sleep / raw); also marked in `score_dirty`.
    var dirty: Set<String> = []
    var dataVersion: Int?
  }

  func pull(_ scope: PullScope, into db: AppDatabase) async throws -> [String] {
    _ = try await pullAll(into: db)
    return []
  }

  func pullAll(into db: AppDatabase, today: String? = nil) async throws -> Outcome {
    var out = Outcome()
    let zone = zone()
    let day = today ?? LocalDay.string(Date(), zone)
    let store = LocalRawStore(db: db, zone: zone)
    var withRaw = (try db.meta(Self.rawRestoredKey)) != "1"
    while out.pages < Self.maxPages {
      var names = Self.streams
      if withRaw { names.append("raw") }
      let cursors = try await db.writer.read { db -> [String: String] in
        var c: [String: String] = [:]
        for n in names { c[n] = try String.fetchOne(db, sql: "SELECT cursor FROM sync_cursors WHERE stream = ?", arguments: [n]) ?? "" }
        return c
      }
      var body: [String: Any] = ["cursors": cursors, "limit": 500]
      if withRaw { body["raw_since"] = LocalRawStore.rawStart(today: day) }
      let page: [String: Any]
      do {
        page = try await api.json(APIRequest(method: .post, path: Endpoint.syncPull,
                                             body: try JSONSerialization.data(withJSONObject: body), idempotent: true, timeout: 60))
      } catch APIError.http(422, let detail) {
        // `bad_cursor` ({message, code}): restore those streams; a 422 with empty cursors is something else
        guard cursors.values.contains(where: { !$0.isEmpty }) else { throw APIError.http(422, detail) }
        try await db.writer.write { db in
          for n in names { try db.execute(sql: "DELETE FROM sync_cursors WHERE stream = ?", arguments: [n]) }
        }
        out.pages += 1
        continue
      }
      out.pages += 1
      let changes = (page["changes"] as? [[String: Any]]) ?? []
      let next = (page["cursors"] as? [String: String]) ?? [:]
      let dirty = try await db.writer.write { db -> Set<String> in
        var dirty = Set<String>()
        for ch in changes { dirty.formUnion(try Self.apply(ch, db: db, store: store)) }
        for (stream, cursor) in next {
          try SyncCursor(stream: stream, cursor: cursor, pulledAt: Date().timeIntervalSince1970).save(db)
        }
        try LocalRawStore.markDirty(db, dirty)
        return dirty
      }
      out.changes += changes.count
      out.dirty.formUnion(dirty)
      out.dataVersion = (page["data_version"] as? NSNumber)?.intValue ?? out.dataVersion
      if withRaw, let raw = next["raw"], raw.hasPrefix("c.") {
        // the bounded raw restore is complete: from now on the phone's own HealthKit reads feed hk_samples
        try db.setMeta(Self.rawRestoredKey, "1")
        // the hot window starts where the restore stopped; the history download (HistoryDownload) fills in before it
        if (try db.meta(LocalRawStore.hotFromKey)) == nil {
          try db.setMeta(LocalRawStore.hotFromKey, LocalRawStore.rawStart(today: day))
        }
        try await db.writer.write { db in try db.execute(sql: "DELETE FROM sync_cursors WHERE stream = 'raw'") }
        withRaw = false
      }
      if page["more"] as? Bool != true { break }
    }
    if let v = out.dataVersion { try db.setMeta("sync.data_version", String(v)) }
    return out
  }

  // MARK: - applying one change (inside the page's transaction)

  /// Writes one change; returns the local days whose scoring inputs it changed.
  static func apply(_ ch: [String: Any], db: Database, store: LocalRawStore) throws -> Set<String> {
    guard let stream = ch["stream"] as? String, let key = ch["key"] as? String else { return [] }
    let day = ch["date"] as? String ?? key
    let body = ch["body"] as? [String: Any]
    let deleted = (ch["op"] as? String) == "delete" || body == nil
    let now = Date().timeIntervalSince1970
    switch stream {
    case "scores":
      if deleted {
        try db.execute(sql: "DELETE FROM daily_scores WHERE date = ? AND origin <> 'app'", arguments: [day])
        try db.execute(sql: "DELETE FROM daily_scores WHERE date = ? AND origin = 'app' AND uploaded = 1", arguments: [day])
        return []
      }
      guard let b = body else { return [] }
      var row = b
      row["date"] = day
      let payload = try JSONSerialization.data(withJSONObject: row, options: [.sortedKeys])
      try DailyScore(date: day, recovery: LocalRawStore.num(b["recovery"]), sleepScore: LocalRawStore.num(b["sleep_score"]),
                     strain: LocalRawStore.num(b["strain"]), hrvMs: nil, rhr: LocalRawStore.num(b["rhr_night"]),
                     payload: payload, algoVersion: b["algo_version"] as? String,
                     origin: b["origin"] as? String ?? "server", computedAt: LocalRawStore.num(b["computed_at"]) ?? now,
                     uploaded: true).save(db)
      return []
    case "daily":
      try db.execute(sql: "DELETE FROM daily_metrics WHERE date = ?", arguments: [day])
      for (metric, v) in (body?["metrics"] as? [String: Any]) ?? [:] {
        let m = v as? [String: Any] ?? [:]
        try db.execute(sql: "INSERT INTO daily_metrics (date, metric, value, unit, source, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                       arguments: [day, metric, LocalRawStore.num(m["value"]), m["unit"] as? String, m["source"] as? String, now])
      }
      // 2.0.0 M1: every source's value of the day (`sources`), from which the phone chooses itself (DeriveStage)
      try db.execute(sql: "DELETE FROM daily_metric_sources WHERE date = ? AND origin = 'server'", arguments: [day])
      for (metric, v) in (body?["sources"] as? [String: Any]) ?? [:] {
        for s in (v as? [[String: Any]]) ?? [] {
          guard let source = s["source"] as? String, let value = LocalRawStore.num(s["value"]), let agg = s["agg"] as? String else {
            continue
          }
          let flags = (s["flags"] as? [String: Any]).flatMap { try? JSONSerialization.data(withJSONObject: $0, options: [.sortedKeys]) }
          try db.execute(sql: """
            INSERT OR REPLACE INTO daily_metric_sources (date, metric, source, origin, value, unit, agg, n, min, max, flags, updated_at)
            VALUES (?, ?, ?, 'server', ?, ?, ?, ?, ?, ?, ?, ?)
            """, arguments: [day, metric, source, value, s["unit"] as? String, agg, (s["n"] as? NSNumber)?.intValue,
                             LocalRawStore.num(s["min"]), LocalRawStore.num(s["max"]), flags, now])
        }
      }
      return [day]
    case "sleep":
      try db.execute(sql: "DELETE FROM sleep_sessions WHERE date = ?", arguments: [day])
      let sessions = (body?["sessions"] as? [[String: Any]]) ?? []
      // the server's stage segments of that day go with its first session ([stage, start, end] like the local ones)
      let segs: [[Any]] = ((body?["segments"] as? [[Any]]) ?? []).compactMap { s in
        guard s.count >= 3, let a = LocalRawStore.num(s[0]), let b = LocalRawStore.num(s[1]), let st = s[2] as? String else { return nil }
        return [st, a, b]
      }
      for (i, s) in sessions.enumerated() {
        guard let a = LocalRawStore.num(s["start"]), let b = LocalRawStore.num(s["end"]) else { continue }
        let id = s["id"].map { "s:\($0)" } ?? "s:\(day):\(i)"
        let stages = i == 0 && body?["segments"] != nil ? try JSONSerialization.data(withJSONObject: segs) : nil
        try db.execute(sql: """
          INSERT INTO sleep_sessions (id, date, start, end, is_main, stages, source, updated_at, payload)
          VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET date = excluded.date, start = excluded.start,
            end = excluded.end, is_main = excluded.is_main, stages = excluded.stages, source = excluded.source,
            updated_at = excluded.updated_at, payload = excluded.payload
          """, arguments: [id, day, a, b, s["is_main"] as? Bool ?? true, stages, s["source"] as? String, now,
                           try JSONSerialization.data(withJSONObject: s, options: [.sortedKeys])])
      }
      return [day]
    case "raw":
      guard let b = body else { return [] }
      var days = Set<String>(), hrDays = Set<String>()
      if let s = b["samples"] as? [String: Any], let cols = s["cols"] as? [String], let rows = s["rows"] as? [[Any]] {
        let ix = Dictionary(uniqueKeysWithValues: cols.enumerated().map { ($1, $0) })
        for r in rows {
          guard let u = ix["uuid"], let ty = ix["type"], let st = ix["start"], let en = ix["end"], let va = ix["value"],
                let uuid = r[u] as? String, let type = r[ty] as? String, LocalRawStore.types[type] != nil,
                let a = LocalRawStore.num(r[st]), let e = LocalRawStore.num(r[en]), let v = LocalRawStore.num(r[va]) else { continue }
          // the server keeps SpO2 in percent; HealthKit's fraction is what the phone stores (the bundle converts)
          let value = type == "HKQuantityTypeIdentifierOxygenSaturation" && v > 1 ? v / 100 : v
          try db.execute(sql: """
            INSERT INTO hk_samples (uuid, type, start, end, value, tz, uploaded) VALUES (?, ?, ?, ?, ?, ?, 1)
            ON CONFLICT(uuid) DO NOTHING
            """, arguments: [uuid.lowercased(), type, a, e, value, ix["tz"].flatMap { r[$0] as? String }])
          let sample = LocalRawStore.Sample(uuid: uuid, type: type, start: a, end: e, value: value)
          days.insert(store.scoringDay(sample))
          if type == LocalRawStore.heartRate { hrDays.insert(store.day(a)) }
        }
      }
      if let s = b["beats"] as? [String: Any], let cols = s["cols"] as? [String], let rows = s["rows"] as? [[Any]] {
        let ix = Dictionary(uniqueKeysWithValues: cols.enumerated().map { ($1, $0) })
        for r in rows {
          guard let u = ix["uuid"], let st = ix["start"], let en = ix["end"], let uuid = r[u] as? String,
                let a = LocalRawStore.num(r[st]), let e = LocalRawStore.num(r[en]) else { continue }
          try db.execute(sql: """
            INSERT INTO heartbeat_series (uuid, start, end, rmssd, sdnn, origin) VALUES (?, ?, ?, ?, ?, 'server')
            ON CONFLICT(uuid) DO NOTHING
            """, arguments: [uuid.lowercased(), a, e, ix["rmssd"].flatMap { LocalRawStore.num(r[$0]) },
                             ix["sdnn"].flatMap { LocalRawStore.num(r[$0]) }])
          days.insert(store.day(a))
        }
      }
      try store.refreshHistograms(db, days: hrDays)
      return days
    default:
      return []
    }
  }
}
