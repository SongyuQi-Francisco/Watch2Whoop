import Foundation
import GRDB

/// The phone scores the user itself (#340 N2b, docs/ios-native-app.md section 9.1), after every sync:
/// 1. New HealthKit samples of the scoring types go into the local tables (`readHealth`, the host's HealthKit reader);
///    pulled server changes (`DeltaRemote`) have already marked their days in `score_dirty`.
/// 2. At most every 6 hours (or when the server says `stale_algo`) a newer signed bundle is fetched. Switching re-scores
///    the last `AlgoBundles.rescoreDays` days — or, once the whole history is on the phone (`HistoryDownload`), every
///    day from the scoring origin (a *full recompute*, local history).
/// 3. `GET /v1/sync/scoring`: hrv_kind, sync mode, scoring origin, the muscle load of the days to compute, and once after
///    an install the heart-rate histograms of the year before the raw window.
/// 4. Only for users in the sync mode `app`: `LocalScoring` recomputes from the earliest dirty day (within the hot window)
///    or from the full recompute's next day, in chunks of `LocalHistoryParams.scoreChunkDays` (each seeded with the rows
///    the previous one wrote, so memory follows the chunk, not the history), writes the rows (origin `app`), and
///    `PUT /v1/users/me/scores/app` uploads the settled ones not uploaded yet, newest batch first. The server spot-checks
///    them (N2); while they pass, its hourly job no longer scores this user.
/// A full recompute keeps its position in `meta` (`scoring.full_next`): a background task that ends resumes there.
/// One run at a time; the report of the last one is kept in `meta` (`scoring.last`) for the sync center.
actor ScoringSync {
  struct Report: Codable, Equatable {
    var at: Double
    var reason: String
    /// idle | offline | server_mode | stale_algo | no_data | scored | partial (a full recompute that goes on next time)
    /// | waiting_sync (HealthKit upload still going) | failed
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
  /// A full recompute in progress: the next day to compute.
  static let fullKey = "scoring.full_next"
  /// The first day of the last run whose night the server had not sent yet (not uploaded until it has).
  static let unsettledKey = "scoring.unsettled_from"

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

  /// Start a full recompute from the scoring origin (a new bundle once the history is complete; tests).
  func startFullRecompute(from day: String) {
    try? db.setMeta(Self.fullKey, day)
  }

  @discardableResult
  func run(reason: String, now: Date = Date(), deadline: Date? = nil) async -> Report {
    var rep = Report(at: now.timeIntervalSince1970, reason: reason, state: "idle")
    guard !running else { rep.detail = "busy"; return rep }
    running = true
    defer { running = false }
    let store = LocalRawStore(db: db, zone: Self.zone(db))
    if let readHealth, !(await readHealth(store)).isEmpty { try? db.setMeta(Self.readKey, "1") }
    var today = LocalDay.string(now, store.zone)
    if let v = await bundles.update(api: api) { markNewBundle(today: today, version: v) }

    var full = (try? db.meta(Self.fullKey)) ?? nil
    if let f = full, f < store.hotFrom(today: today), !HistoryDownload.isComplete(db) {
      // days without their raw data here would be uploaded as the app's: wait until the history is complete
      full = nil
    }
    let dirty = (try? await db.writer.read { db in try String.fetchAll(db, sql: "SELECT date FROM score_dirty WHERE kind = 'scores' ORDER BY date") }) ?? []
    let pending = (try? await db.writer.read { db in
      try Bool.fetchOne(db, sql: "SELECT EXISTS (SELECT 1 FROM daily_scores WHERE origin = 'app' AND uploaded = 0)")
    }) ?? false
    guard full != nil || !dirty.isEmpty || pending else { return save(rep) }
    // a dirty day before the hot window is not scored here (only a full recompute goes that far back)
    let hotStart = LocalDay.add(store.hotFrom(today: today), 2)
    var first = full ?? max(dirty.first ?? today, hotStart)
    if first > today { first = today }
    // a new user's chain starts on the server's origin (#340 N9, below): with the origin of the last run the first muscle
    // window is fetched from there right away instead of a second time
    if let o = (try? db.doc(Self.contextDoc))?.json?["scoring_origin"] as? String, o >= hotStart, o < first { first = o }

    // the server's part of the inputs (the muscle load of the first window; histograms once after an install)
    let window = min(today, LocalDay.add(first, LocalHistoryParams.muscleWindowDays - 1))
    let firstFetched = first
    var windowEnd = window
    let needHist = (try? db.meta(Self.histKey)) == nil
    var ctxResult = await context(from: first, to: window, hist: needHist ? today : nil)
    let j: [String: Any]
    switch ctxResult {
    case .failure(let state, let detail):
      rep.state = state
      rep.detail = detail
      return save(rep)
    case .success(let got):
      j = got
    }
    guard var ctx = LocalScoring.Context(j) else { rep.state = "failed"; rep.detail = "context"; return save(rep) }
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
        if let v = await bundles.update(api: api, force: true) { markNewBundle(today: today, version: v) }
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

    // The server starts every chain (CTL / ATL, sleep need from the day before) on its scoring origin, its first day
    // with a resting heart rate (#340 N9). A new user's first rounds come before the server derived anything: the phone
    // scores for its own display, but rows from before the origin would seed the next runs with a different start
    // (CTL 559 vs 491 in the regression), and an upload without an origin passed the spot check unchecked. So: nothing
    // is uploaded until the server has an origin; once it has, the phone's rows before it go and the chain is recomputed
    // from the origin.
    if let origin = ctx.origin {
      let dropped = (try? await db.writer.write { db -> Int in
        try db.execute(sql: "DELETE FROM daily_scores WHERE origin = 'app' AND date < ?", arguments: [origin])
        return db.changesCount
      }) ?? 0
      if dropped > 0 || first < origin { first = origin }
      // While the whole history since the origin is in the phone's raw window (a new user's first ~2 months), every run
      // is the server's full recompute from the origin rather than a seeded one: the CTL / ATL start is the mean of the
      // first 14 days with a TRIMP, so a chain seeded from a run that had fewer days kept a different start (the
      // regression's new user: CTL 466 on the phone, 491 on the server, spot check failed). ≤ 60 days, cheap.
      if origin >= hotStart { first = origin }
    }
    // a first moved before the context's muscle window needs that window fetched again (the loop does it)
    if first < firstFetched { windowEnd = LocalDay.add(first, -1) }

    // compute [first, today] chunk by chunk (a full recompute may stop at the deadline and go on next time)
    let started = Date().timeIntervalSince1970
    let fullNow = full
    rep.first = first
    var a = first
    var unsettled: String?
    var reachedToday = false
    while a <= today {
      if Task.isCancelled || deadline.map({ Date() >= $0 }) == true { break }
      if a > windowEnd {   // the next muscle window
        let w = min(today, LocalDay.add(a, LocalHistoryParams.muscleWindowDays - 1))
        ctxResult = await context(from: a, to: w, hist: nil)
        guard case .success(let more) = ctxResult, let c = LocalScoring.Context(more) else { break }
        ctx = c
        windowEnd = w
      }
      let b = min(today, LocalDay.add(a, LocalHistoryParams.scoreChunkDays - 1), windowEnd)
      let result: LocalScoring.Result
      do {
        result = try LocalScoring.run(db: db, runtime: runtime, context: ctx, first: a, last: b, today: today, replace: true)
      } catch {
        rep.state = "failed"
        rep.detail = String(describing: error)
        return save(rep)
      }
      runtime.collectGarbage()
      rep.written += result.written
      if result.skipped != nil, rep.detail == nil { rep.detail = result.skipped }
      if let u = result.unsettled.first { unsettled = min(unsettled ?? u, u) }
      // the chunk's days are computed: their dirty marks go (an unsettled night's stay: scored again when it comes)
      clearDirty(from: a, through: b, before: started, keepFrom: unsettled)
      if fullNow != nil { try? db.setMeta(Self.fullKey, LocalDay.add(b, 1)) }
      if b >= today { reachedToday = true }
      a = LocalDay.add(b, 1)
    }
    if reachedToday {
      if fullNow != nil { try? await db.writer.write { db in try db.execute(sql: "DELETE FROM meta WHERE key = ?", arguments: [Self.fullKey]) } }
      if let unsettled { try? db.setMeta(Self.unsettledKey, unsettled) } else {
        try? await db.writer.write { db in try db.execute(sql: "DELETE FROM meta WHERE key = ?", arguments: [Self.unsettledKey]) }
      }
    }
    rep.state = reachedToday ? (rep.written.isEmpty && rep.detail != nil ? "no_data" : "scored") : "partial"

    // upload what is settled and not uploaded yet: before the first night the server has not sent yet
    let cutoff = ((try? db.meta(Self.unsettledKey)) ?? nil) ?? "9999-12-31"
    let algo = runtime.algoVersions["scores"]
    let days = ((try? await db.writer.read { db in
      try String.fetchAll(db, sql: """
        SELECT date FROM daily_scores WHERE origin = 'app' AND uploaded = 0 AND date < ? AND algo_version IS ? ORDER BY date DESC
        """, arguments: [cutoff, algo])
    }) ?? [])
    guard ctx.origin != nil else {   // see above: the server has not derived a first day yet
      rep.state = "waiting_sync"
      rep.detail = "no_origin"
      return save(rep)
    }
    // samples this phone read from HealthKit must be on the server first (with none, the inputs are the server's anyway)
    let phoneReads = (try? db.meta(Self.readKey)) == "1"
    if !days.isEmpty, phoneReads, let gate = uploadGate, !(await gate()) {
      rep.state = "waiting_sync"
      return save(rep)
    }
    // newest batch first: the server's spot check looks at the recent days of an upload
    for batch in stride(from: 0, to: days.count, by: LocalHistoryParams.uploadBatchDays).map({
      Array(days[$0..<min(days.count, $0 + LocalHistoryParams.uploadBatchDays)]).reversed()
    }) {
      let (status, body) = await upload(days: Array(batch), runtime: runtime)
      rep.upload = status
      if let ok = (body?["app_scoring"] as? Bool) { rep.appScoring = ok }
      if let check = body?["check"] as? [String: Any], check["ok"] as? Bool == false {
        rep.detail = "check \(check["date"] as? String ?? "?"): \((check["diffs"] as? [String: Any])?.keys.sorted() ?? [])"
      }
      switch status {
      case "ok", "mismatch":
        // mismatch: the server stored its own row for the checked day; the pull brings it back
        try? await db.writer.write { db in
          for d in batch { try db.execute(sql: "UPDATE daily_scores SET uploaded = 1 WHERE date = ? AND origin = 'app'", arguments: [d]) }
        }
        rep.uploaded += batch
      case "stale_algo":
        if let v = await bundles.update(api: api, force: true) { markNewBundle(today: today, version: v) }
      default:
        break
      }
      if status != "ok" { break }
    }
    rep.uploaded.sort()
    try? store.prune(today: today)
    return save(rep)
  }


  private enum ContextResult {
    case success([String: Any])
    case failure(String, String?)
  }

  /// `GET /v1/sync/scoring?from=&to=` (+ the histograms of the year before the raw window once).
  private func context(from: String, to: String, hist today: String?) async -> ContextResult {
    var query = ["from": from, "to": to]
    if let today {
      query["hist_from"] = LocalDay.add(today, -425)
      query["hist_to"] = LocalDay.add(LocalRawStore.rawStart(today: today), -1)
    }
    do {
      return .success(try await api.json(.get(Endpoint.syncScoring, query)))
    } catch APIError.notFound {
      return .failure("server_mode", nil)   // the server has no native sync for this user
    } catch {
      return .failure("offline", String(describing: error))
    }
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

  /// A new bundle: with the whole history on the phone every day is scored again with it (from the scoring origin,
  /// chunk by chunk); otherwise the last days (the older rows stay the server's).
  private func markNewBundle(today: String, version: String) {
    if HistoryDownload.isComplete(db) {
      let origin = (try? db.doc(Self.contextDoc))?.json?["scoring_origin"] as? String
      let oldest = (try? db.writer.read { db in try String.fetchOne(db, sql: "SELECT min(date) FROM hist_days") }) ?? nil
      if let from = origin ?? oldest {
        try? db.setMeta(Self.fullKey, from)
        return
      }
    }
    let days = Set((0..<AlgoBundles.rescoreDays).map { LocalDay.add(today, -$0) })
    try? db.writer.write { db in try LocalRawStore.markDirty(db, days) }
  }

  /// Days computed: their marks go, unless they were set again while this run was going or their night is still to come.
  private func clearDirty(from a: String, through b: String, before t: Double, keepFrom: String?) {
    let upto = keepFrom.map { min(b, LocalDay.add($0, -1)) } ?? b
    try? db.writer.write { db in
      try db.execute(sql: "DELETE FROM score_dirty WHERE kind = 'scores' AND date <= ? AND at <= ?", arguments: [upto, t])
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
