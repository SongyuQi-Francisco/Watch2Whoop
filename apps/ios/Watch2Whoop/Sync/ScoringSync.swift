import Foundation
import GRDB

/// The phone scores the user itself (#340 N2b, docs/ios-native-app.md section 9.1), after every sync:
/// 1. New HealthKit samples of the scoring types go into the local tables (`readHealth`, the host's HealthKit reader);
///    pulled server changes (`DeltaRemote`) have already marked their days in `score_dirty`.
/// 2. At most every 6 hours (or when the server says `stale_algo`) a newer signed bundle is fetched; switching re-scores
///    the last `AlgoBundles.rescoreDays` days.
/// 3. `GET /v1/sync/scoring`: hrv_kind, sync mode, scoring origin, the muscle load of the days to compute, and once after
///    an install the heart-rate histograms of the year before the raw window.
/// 4. Only for users in the sync mode `app`: `LocalScoring` recomputes from the earliest dirty day (within the raw window)
///    to today, writes the rows (origin `app`), and `PUT /v1/users/me/scores/app` uploads the settled ones. The server
///    spot-checks them (N2); while they pass, its hourly job no longer scores this user.
/// One run at a time; the report of the last one is kept in `meta` (`scoring.last`) for the sync center.
actor ScoringSync {
  struct Report: Codable, Equatable {
    var at: Double
    var reason: String
    /// idle | offline | server_mode | stale_algo | no_data | scored | waiting_sync (HealthKit upload still going) | failed
    var state: String
    var first: String?
    var written: [String] = []
    var uploaded: [String] = []
    /// The server's answer to the upload: ok | mismatch | stale_algo.
    var upload: String?
    var appScoring: Bool?
    var bundle: String?
    var detail: String?
  }

  static let contextDoc = "scoring:context"
  static let histKey = "scoring.hist_through"
  static let lastKey = "scoring.last"
  static let readKey = "scoring.hk_read"

  private let db: AppDatabase
  private let api: APIClient
  let bundles: AlgoBundles
  private var readHealth: ((LocalRawStore) async -> Set<String>)?
  private var uploadGate: (() async -> Bool)?
  private var running = false

  init(db: AppDatabase, api: APIClient, bundles: AlgoBundles? = nil) {
    self.db = db
    self.api = api
    self.bundles = bundles ?? AlgoBundles(db: db)
  }

  /// The host's HealthKit reader (Features/Sync); nil in tests and before HealthKit is set up.
  func setHealthReader(_ f: ((LocalRawStore) async -> Set<String>)?) { readHealth = f }

  /// true when everything this phone read from HealthKit is on the server too (`HealthSyncService.uploadedEverything`):
  /// the server's spot check sees the same inputs only then. While false the days are scored and shown here but not
  /// uploaded; the sync round's end runs scoring again.
  func setUploadGate(_ f: (() async -> Bool)?) { uploadGate = f }

  /// The zone of the last scoring context (the user's), else the phone's.
  nonisolated static func zone(_ db: AppDatabase) -> TimeZone {
    (try? db.doc(contextDoc))?.json.flatMap { $0["tz"] as? String }.flatMap(TimeZone.init(identifier:)) ?? .current
  }

  @discardableResult
  func run(reason: String, now: Date = Date()) async -> Report {
    var rep = Report(at: now.timeIntervalSince1970, reason: reason, state: "idle")
    guard !running else { rep.detail = "busy"; return rep }
    running = true
    defer { running = false }
    let store = LocalRawStore(db: db, zone: Self.zone(db))
    if let readHealth, !(await readHealth(store)).isEmpty { try? db.setMeta(Self.readKey, "1") }
    var today = LocalDay.string(now, store.zone)
    if let v = await bundles.update(api: api) { markRecent(today: today, version: v) }

    let dirty = (try? await db.writer.read { db in try String.fetchAll(db, sql: "SELECT date FROM score_dirty ORDER BY date") }) ?? []
    guard let earliest = dirty.first else { return save(rep) }
    var first = max(earliest, LocalDay.add(LocalRawStore.rawStart(today: today), 2))
    if first > today { first = today }

    // the server's part of the inputs
    var query = ["from": first, "to": today]
    let needHist = (try? db.meta(Self.histKey)) == nil
    if needHist {
      query["hist_from"] = LocalDay.add(today, -425)
      query["hist_to"] = LocalDay.add(LocalRawStore.rawStart(today: today), -1)
    }
    let j: [String: Any]
    do {
      j = try await api.json(.get(Endpoint.syncScoring, query))
    } catch APIError.notFound {
      rep.state = "server_mode"   // the server has no native sync for this user
      return save(rep)
    } catch {
      rep.state = "offline"
      rep.detail = String(describing: error)
      return save(rep)
    }
    guard let ctx = LocalScoring.Context(j) else { rep.state = "failed"; rep.detail = "context"; return save(rep) }
    rep.appScoring = (j["app_scoring"] as? [String: Any])?["active"] as? Bool
    if let data = try? JSONSerialization.data(withJSONObject: j.filter { $0.key != "hr_hist" && $0.key != "muscle" }) {
      try? db.saveDoc(Self.contextDoc, json: data)
    }
    if let tz = TimeZone(identifier: ctx.tz), tz.identifier != store.zone.identifier {
      store.zone = tz
      today = LocalDay.string(now, tz)
    }
    if needHist, let h = j["hr_hist"] as? [String: Any], let days = h["days"] as? [String: Any] {
      try? store.storeServerHistograms(days)
      try? db.setMeta(Self.histKey, h["to"] as? String ?? today)
    }
    guard ctx.syncMode == "app" else {
      rep.state = "server_mode"   // the scores come from Open Wearables / the Shortcut the phone does not have
      return save(rep)
    }

    // the bundle the server scores with
    var runtime: OpenBodyScoreRuntime
    do {
      runtime = try bundles.runtime()
      if let want = ctx.algoVersion, runtime.algoVersions["scores"] != want {
        if let v = await bundles.update(api: api, force: true) { markRecent(today: today, version: v) }
        runtime = try bundles.runtime()
      }
    } catch {
      rep.state = "failed"
      rep.detail = String(describing: error)
      return save(rep)
    }
    rep.bundle = runtime.version
    if let want = ctx.algoVersion, runtime.algoVersions["scores"] != want {
      rep.state = "stale_algo"
      rep.detail = "\(runtime.algoVersions["scores"] ?? "?") vs \(want)"
      return save(rep)
    }
    if ctx.hrvKind == "rmssd" { _ = try? store.analyzeBeats(runtime) }

    let started = Date().timeIntervalSince1970
    let result: LocalScoring.Result
    do {
      result = try LocalScoring.run(db: db, runtime: runtime, context: ctx, first: first, today: today, replace: true)
    } catch {
      rep.state = "failed"
      rep.detail = String(describing: error)
      return save(rep)
    }
    rep.first = first
    rep.written = result.written
    guard !result.written.isEmpty else {
      rep.state = result.skipped == nil ? "scored" : "no_data"
      rep.detail = result.skipped
      clearDirty(through: today, before: started)
      return save(rep)
    }
    rep.state = "scored"
    // upload what is settled: the days before the first night the server has not sent yet
    let cutoff = result.unsettled.first ?? "9999-12-31"
    let days = result.written.filter { $0 < cutoff }
    // samples this phone read from HealthKit must be on the server first (with none, the inputs are the server's anyway)
    let phoneReads = (try? db.meta(Self.readKey)) == "1"
    if !days.isEmpty, phoneReads, let gate = uploadGate, !(await gate()) {
      rep.state = "waiting_sync"
      return save(rep)
    }
    if !days.isEmpty {
      let (status, body) = await upload(days: days, runtime: runtime)
      rep.upload = status
      if let ok = (body?["app_scoring"] as? Bool) { rep.appScoring = ok }
      if let check = body?["check"] as? [String: Any], check["ok"] as? Bool == false {
        rep.detail = "check \(check["date"] as? String ?? "?"): \((check["diffs"] as? [String: Any])?.keys.sorted() ?? [])"
      }
      switch status {
      case "ok", "mismatch":
        // mismatch: the server stored its own row for the checked day; the pull brings it back
        try? await db.writer.write { db in
          for d in days { try db.execute(sql: "UPDATE daily_scores SET uploaded = 1 WHERE date = ? AND origin = 'app'", arguments: [d]) }
        }
        rep.uploaded = days
        clearDirty(through: days.max()!, before: started)
      case "stale_algo":
        if let v = await bundles.update(api: api, force: true) { markRecent(today: today, version: v) }
      default:
        break
      }
    }
    try? store.prune(today: today)
    return save(rep)
  }

  /// PUT /v1/users/me/scores/app with the rows of these days → (status, answer).
  private func upload(days: [String], runtime: OpenBodyScoreRuntime) async -> (String?, [String: Any]?) {
    let rows: [[String: Any]] = (try? await db.writer.read { db in
      try days.compactMap { d -> [String: Any]? in
        guard let p = try Data.fetchOne(db, sql: "SELECT payload FROM daily_scores WHERE date = ? AND origin = 'app'", arguments: [d]),
              var row = try JSONSerialization.jsonObject(with: p) as? [String: Any] else { return nil }
        row.removeValue(forKey: "date")
        row.removeValue(forKey: "bundle_version")
        return ["date": d, "row": row]
      }
    }) ?? []
    guard !rows.isEmpty, let algo = runtime.algoVersions["scores"] else { return (nil, nil) }
    let body: [String: Any] = ["algo_version": algo, "bundle_version": runtime.version, "days": rows]
    guard let data = try? JSONSerialization.data(withJSONObject: body) else { return (nil, nil) }
    do {
      let j = try await api.json(APIRequest(method: .put, path: Endpoint.scoresUpload, body: data, timeout: 60))
      return (j["status"] as? String, j)
    } catch {
      return ("error: \(error)", nil)
    }
  }

  /// A new bundle: the last days are scored again with it.
  private func markRecent(today: String, version: String) {
    let days = Set((0..<AlgoBundles.rescoreDays).map { LocalDay.add(today, -$0) })
    try? db.writer.write { db in try LocalRawStore.markDirty(db, days) }
  }

  /// Days scored and uploaded: their marks go, unless they were set again while this run was going.
  private func clearDirty(through day: String, before t: Double) {
    try? db.writer.write { db in
      try db.execute(sql: "DELETE FROM score_dirty WHERE date <= ? AND at <= ?", arguments: [day, t])
    }
  }

  private func save(_ r: Report) -> Report {
    if let d = try? JSONEncoder().encode(r) { try? db.setMeta(Self.lastKey, String(decoding: d, as: UTF8.self)) }
    return r
  }

  nonisolated static func lastReport(_ db: AppDatabase) -> Report? {
    (try? db.meta(lastKey)).flatMap { $0 }.flatMap { try? JSONDecoder().decode(Report.self, from: Data($0.utf8)) }
  }
}
