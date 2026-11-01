import BackgroundTasks
import Foundation
import HealthKit
import Network
import UIKit

/// The app's own HealthKit sync engine (#219, docs/apple-sync-engine.md §3.1, docs/apple-sync-engine-app-contract.md).
///
/// A1 read in Swift and uploaded from Dart. From A2 the whole round is native, because HealthKit background delivery and
/// BGTasks wake the app without Flutter: reading, columnar gzip batches, POST /v1/ingest/healthkit, anchors (committed
/// only after a 200), retries, runs[] and the server's `engine` block. Dart (lib/src/sync_engine.dart) only hands over
/// the server, the token, the registry and the server's last engine block, starts foreground rounds and shows the state.
///
/// What a round reads, per registry type the server lists in `engine.types` (and this iOS knows):
/// - raw quantities and categories (sleep stages: HealthKit's integer 0–5): the newest 48 hours first (phase `recent`,
///   no anchor), then the anchored crawl from [EngineConfig.daysBack] days ago (`backfill` until it read everything once,
///   `incremental` afterwards); deletions go in `deleted[]`;
/// - workouts (A3): HKWorkout with its statistics, events and metadata (no routes, D2);
/// - cumulative types (A3, `upload` has `stats`): 15-minute HKStatisticsCollectionQuery buckets, UTC-aligned. An anchored
///   query of the type (anchor `stats:<type>`) only says *what changed*; the buckets from the earliest changed sample to
///   now are sent again (the server replaces that span), so late watch samples and deletions correct the buckets;
/// - a range re-sync (A3, contract §8): day windows of the range with `range`, then the buckets, then `range_done`.
/// Older history (beyond the first-sync window) is Backfill.swift's job.
///
/// When it runs (A2): Dart's foreground rounds; HKObserverQuery + background delivery per registry `bg` (immediate /
/// hourly; in shadow mode every observed type immediate, as Open Wearables already asks); BGAppRefresh
/// (`watch2whoop.sync.refresh`, ≥ 30 min); BGProcessing (`watch2whoop.sync.process`, network + power) for the first
/// crawl and range re-syncs; and again after the phone is unlocked (HealthKit error 6 = locked) or the network is back.
/// Observer wakes are debounced 3 s, at least 5 minutes apart unless sleep / workouts changed, and HealthKit's completion
/// handlers are called after the round. Background rounds stop sending when < 8 s are left.
/// Battery and data: nothing changed = no request; history (the first crawl, range re-syncs) only on Wi-Fi unless the user
/// allows cellular, never in Low Power Mode or when the phone is hot; Low Power Mode wakes read only `immediate` types.
/// Nothing about health values is logged; the run log and runs[] carry counts and codes only.
///
/// A module of its own: no Flutter and no Open Wearables in here (the SwiftUI app of #340 reuses it as it is). The Flutter
/// channel is a thin layer in SyncEngineChannel.swift; what the host must do when the server's mode becomes `app` (stop
/// Open Wearables) is the [onModeApp] hook.
final class SyncEngine: NSObject {
  static let shared = SyncEngine()
  static let refreshTaskId = "watch2whoop.sync.refresh"
  static let processTaskId = "watch2whoop.sync.process"
  static let recentWindow: TimeInterval = 48 * 3600

  let store = HKHealthStore()
  private let queue = DispatchQueue(label: "watch2whoop.sync.engine")
  private let anchorPrefix = "w2w.sync.anchor."
  private let monitor = NWPathMonitor()
  private var path: NWPath?
  private var observers: [HKObserverQuery] = []
  private var observed = Set<String>()
  private var running = false
  private var currentTask: Task<EngineRoundResult, Never>?
  private var wakeTypes = Set<String>()
  private var wakeCompletions: [() -> Void] = []
  private var debounce: DispatchWorkItem?
  private var started = false

  /// Called on the main thread when the server's mode becomes `app` (contract §7): the host stops Open Wearables and
  /// then calls [rearm] (OW's stop disables background delivery of its types and cancels every BGTask request).
  var onModeApp: (() -> Void)?

  // MARK: - launch (AppDelegate.didFinishLaunching, before it returns)

  /// BGTask handlers must be registered before launch finishes; observers are set up again on every launch, including the
  /// background launches HealthKit makes for them.
  static func register() {
    BGTaskScheduler.shared.register(forTaskWithIdentifier: refreshTaskId, using: nil) { task in
      SyncEngine.shared.runTask(task, trigger: "refresh")
    }
    BGTaskScheduler.shared.register(forTaskWithIdentifier: processTaskId, using: nil) { task in
      SyncEngine.shared.runTask(task, trigger: "processing")
    }
    shared.start()
  }

  private func start() {
    guard !started else { return }
    started = true
    monitor.pathUpdateHandler = { [weak self] p in self?.pathChanged(p) }
    monitor.start(queue: queue)
    NotificationCenter.default.addObserver(self, selector: #selector(unlocked),
                                           name: UIApplication.protectedDataDidBecomeAvailableNotification, object: nil)
    queue.async { self.rearm() }
  }

  // MARK: - API for the host (SyncEngineChannel.swift: the Flutter channel watch2whoop/native, methods engine*)

  /// One foreground round with the configuration in [args] (see [configure]); [done] gets the round's map on the main thread.
  func runForeground(_ args: [String: Any], trigger: String = "foreground", _ done: @escaping ([String: Any]) -> Void) {
    queue.async {
      _ = self.configure(args)
      self.launchRound(trigger: trigger, background: false, budget: 90) { r in DispatchQueue.main.async { done(r.map) } }
    }
  }

  /// A background-style round on demand (debug: the simulator never runs BGTasks; observers only fire with the app open).
  func runBackgroundStyle(trigger: String, _ done: @escaping ([String: Any]) -> Void) {
    queue.async {
      self.launchRound(trigger: trigger, background: true, budget: 25) { r in DispatchQueue.main.async { done(r.map) } }
    }
  }

  func configureAsync(_ args: [String: Any], _ done: @escaping ([String: Any]) -> Void) {
    queue.async {
      let r = self.configure(args)
      DispatchQueue.main.async { done(r) }
    }
  }

  func status(_ done: @escaping ([String: Any]) -> Void) {
    queue.async {
      let s = self.statusMap()
      DispatchQueue.main.async { done(s) }
    }
  }

  func setCellularHistory(_ on: Bool) {
    queue.async {
      var c = EngineState.config()
      c.cellularHistory = on
      EngineState.save(c)
      self.scheduleProcessingIfNeeded(c)
    }
  }

  /// Range re-sync of the last [days] days (0 = all history); nil when the engine is off.
  func startRange(days: Int, _ done: @escaping ([String: Any]?) -> Void) {
    queue.async {
      let r = self.startRange(days)
      DispatchQueue.main.async { done(r) }
    }
  }

  func cancelRange() { queue.async { EngineState.saveRange(nil) } }

  func rearmAsync() { queue.async { self.rearm() } }

  /// Sign-out / another account: every anchor, the state, the token, the BGTask requests.
  func reset(_ done: @escaping () -> Void) {
    queue.async {
      self.stopObservers()
      for key in UserDefaults.standard.dictionaryRepresentation().keys where key.hasPrefix(self.anchorPrefix) {
        UserDefaults.standard.removeObject(forKey: key)
      }
      EngineState.clear()
      EngineKeychain.delete()
      BGTaskScheduler.shared.cancel(taskRequestWithIdentifier: Self.refreshTaskId)
      BGTaskScheduler.shared.cancel(taskRequestWithIdentifier: Self.processTaskId)
      DispatchQueue.main.async { done() }
    }
  }

  /// Stores what Dart knows: server, token (Keychain), registry, the server's engine block, the first-sync window. Starts or
  /// stops the observers to match; returns the native mode.
  private func configure(_ args: [String: Any]) -> [String: Any] {
    var c = EngineState.config()
    let before = c
    if let s = args["server"] as? String, !s.isEmpty { c.server = s }
    if let v = args["appVersion"] as? String, !v.isEmpty { c.appVersion = v }
    if let d = args["daysBack"] as? Int { c.daysBack = d }
    if let t = args["token"] as? String, !t.isEmpty, t != EngineKeychain.read() {
      EngineKeychain.write(t)
      EngineState.setNotBefore(0)   // a new token: the 401 backoff no longer applies
    }
    if let reg = args["registry"] as? [[String: Any]] {
      c.types = reg.compactMap(EngineTypeSpec.init(map:))
    }
    if let e = args["engine"] as? [String: Any] { c.apply(engine: e) }
    EngineState.save(c)
    EngineState.migrateFromDart()
    if before.mode != c.mode { modeChanged(from: before.mode, to: c.mode) }
    rearm()
    return ["mode": c.mode, "active": c.active]
  }

  // MARK: - mode (contract §7)

  private func modeChanged(from: String, to: String) {
    EngineLog.add("mode \(from) -> \(to)")
    if to == "app" {
      // the app's engine is the source now: the host stops Open Wearables (credentials and anchors stay for a rollback;
      // Dart starts it again when the server goes back to shadow), then the engine arms itself again
      DispatchQueue.main.async {
        self.onModeApp?()
        self.queue.async { self.rearm() }
      }
    }
  }

  // MARK: - observers, background delivery, BGTasks

  /// Observers and background delivery for the registry's `bg` types, the BGTask requests (again after OW cancelled
  /// them). Idempotent; with the engine off it stops its observers (background delivery is left alone: OW may use it).
  func rearm() {
    let c = EngineState.config()
    guard c.active, HKHealthStore.isHealthDataAvailable() else {
      stopObservers()
      return
    }
    let ios = ProcessInfo.processInfo.operatingSystemVersion.majorVersion
    for t in c.types where t.bg != "none" && c.accepted.contains(t.hk) && (t.minIos == 0 || ios >= t.minIos) {
      guard let type = EngineHK.sampleType(t) else { continue }
      // shadow: OW already asks for immediate delivery of its types; a lower frequency here would slow OW down too
      let freq: HKUpdateFrequency = (c.mode == "app" && t.bg == "hourly") ? .hourly : .immediate
      store.enableBackgroundDelivery(for: type, frequency: freq) { _, _ in }
      guard !observed.contains(t.hk) else { continue }
      observed.insert(t.hk)
      let hk = t.hk
      let q = HKObserverQuery(sampleType: type, predicate: nil) { [weak self] _, completion, error in
        guard let self, error == nil else { completion(); return }
        self.woke(hk, completion)
      }
      observers.append(q)
      store.execute(q)
    }
    ensureRefreshScheduled()
    scheduleProcessingIfNeeded(c)
    Backfill.shared.rescheduleIfActive()
  }

  private func stopObservers() {
    for q in observers { store.stop(q) }
    observers.removeAll()
    observed.removeAll()
  }

  private func ensureRefreshScheduled() {
    BGTaskScheduler.shared.getPendingTaskRequests { reqs in
      guard !reqs.contains(where: { $0.identifier == Self.refreshTaskId }) else { return }
      let r = BGAppRefreshTaskRequest(identifier: Self.refreshTaskId)
      r.earliestBeginDate = Date(timeIntervalSinceNow: 30 * 60)
      do { try BGTaskScheduler.shared.submit(r) } catch { NSLog("SyncEngine: refresh not scheduled: %@", "\(error)") }
    }
  }

  /// A BGProcessingTask (network, on power) while there is heavy work: a range re-sync or a first crawl not finished.
  private func scheduleProcessingIfNeeded(_ c: EngineConfig) {
    guard c.active, EngineState.range() != nil || !EngineState.progress().allBackfilled(c.readTypes()) else { return }
    BGTaskScheduler.shared.getPendingTaskRequests { reqs in
      guard !reqs.contains(where: { $0.identifier == Self.processTaskId }) else { return }
      let r = BGProcessingTaskRequest(identifier: Self.processTaskId)
      r.requiresNetworkConnectivity = true
      r.requiresExternalPower = true
      r.earliestBeginDate = Date(timeIntervalSinceNow: 15 * 60)
      do { try BGTaskScheduler.shared.submit(r) } catch { NSLog("SyncEngine: processing not scheduled: %@", "\(error)") }
    }
  }

  private func woke(_ hk: String, _ completion: @escaping () -> Void) {
    queue.async {
      self.wakeTypes.insert(hk)
      self.wakeCompletions.append(completion)
      self.debounce?.cancel()
      let item = DispatchWorkItem { [weak self] in self?.observerRound() }
      self.debounce = item
      self.queue.asyncAfter(deadline: .now() + 3, execute: item)
    }
  }

  private static let urgentTypes: Set<String> = ["HKCategoryTypeIdentifierSleepAnalysis", "HKWorkoutTypeIdentifier"]

  /// One round for the observers' wakes; HealthKit's completion handlers after it (it stops delivering after three
  /// wakes without them).
  private func observerRound() {
    guard !running else { return }   // the running round calls them when it ends, and looks again
    let completions = wakeCompletions, types = wakeTypes
    wakeCompletions.removeAll()
    wakeTypes.removeAll()
    let finish = { completions.forEach { $0() } }
    let urgent = !types.isDisjoint(with: Self.urgentTypes)
    let last = EngineState.lastBackgroundRound()
    if !urgent, Date().timeIntervalSince1970 - last < 5 * 60 {
      finish()   // at most every 5 minutes; BGAppRefresh and the next open pick up the rest
      return
    }
    beginBackground { end, remaining in
      self.launchRound(trigger: "observer", background: true, budget: max(0, min(25, remaining - 8))) { _ in
        finish()
        end()
      }
    }
  }

  /// UIApplication.beginBackgroundTask around [work] with the time iOS gives (main thread for UIApplication).
  private func beginBackground(_ work: @escaping (_ end: @escaping () -> Void, _ remaining: Double) -> Void) {
    DispatchQueue.main.async {
      let app = UIApplication.shared
      var id = UIBackgroundTaskIdentifier.invalid
      id = app.beginBackgroundTask(withName: "w2w.sync") {
        self.currentTask?.cancel()
        if id != .invalid { app.endBackgroundTask(id); id = .invalid }
      }
      let foreground = app.applicationState == .active
      let remaining = foreground ? 60 : min(app.backgroundTimeRemaining, 30)
      let end = { DispatchQueue.main.async { if id != .invalid { app.endBackgroundTask(id); id = .invalid } } }
      self.queue.async { work(end, remaining) }
    }
  }

  private func runTask(_ task: BGTask, trigger: String) {
    task.expirationHandler = { [weak self] in self?.currentTask?.cancel() }
    queue.async {
      let c = EngineState.config()
      if trigger == "refresh" { self.ensureRefreshScheduledAfterRun() }
      guard c.active else { task.setTaskCompleted(success: true); return }
      // processing: on power, so keep going while there is work (the expiration handler cancels the round)
      let budget: Double = trigger == "processing" ? 600 : 25
      self.launchRound(trigger: trigger, background: trigger != "processing", budget: budget) { r in
        if trigger == "processing" { self.scheduleProcessingIfNeeded(EngineState.config()) }
        task.setTaskCompleted(success: r.stop == nil || r.stop == "budget")
      }
    }
  }

  private func ensureRefreshScheduledAfterRun() {
    // the request that woke us is used up: the next one
    let r = BGAppRefreshTaskRequest(identifier: Self.refreshTaskId)
    r.earliestBeginDate = Date(timeIntervalSinceNow: 30 * 60)
    try? BGTaskScheduler.shared.submit(r)
  }

  @objc private func unlocked() {
    queue.async {
      guard EngineState.lastStop() == "locked" else { return }
      self.beginBackground { end, remaining in
        self.launchRound(trigger: "unlock", background: true, budget: max(0, min(25, remaining - 8))) { _ in end() }
      }
    }
  }

  private func pathChanged(_ p: NWPath) {
    let was = path?.status
    path = p
    guard p.status == .satisfied, was != nil, was != .satisfied, EngineState.lastStop() == "offline" else { return }
    beginBackground { end, remaining in
      self.launchRound(trigger: "network", background: true, budget: max(0, min(25, remaining - 8))) { _ in end() }
    }
  }

  // MARK: - rounds

  /// Starts one round unless one is running (its caller then gets `busy`); [done] on the engine queue.
  private func launchRound(trigger: String, background: Bool, budget: Double, _ done: @escaping (EngineRoundResult) -> Void) {
    guard !running else { done(EngineRoundResult(stop: "busy")); return }
    let c = EngineState.config()
    guard c.active, let token = EngineKeychain.read() else { done(EngineRoundResult(stop: "off")); return }
    if EngineState.notBefore() > Date().timeIntervalSince1970 { done(EngineRoundResult(stop: "backoff")); return }
    running = true
    let p = path
    let net = EngineNet(satisfied: p == nil || p!.status == .satisfied, unmetered: !(p?.isExpensive ?? false),
                        constrained: p?.isConstrained ?? false)
    let task = Task<EngineRoundResult, Never> {
      let protected = await MainActor.run { UIApplication.shared.isProtectedDataAvailable }
      let round = EngineRound(engine: self, config: c, token: token, trigger: trigger, background: background,
                              budget: budget, net: net, protectedData: protected)
      return await round.run()
    }
    currentTask = task
    Task {
      let r = await task.value
      self.queue.async {
        self.running = false
        self.currentTask = nil
        EngineState.setLastStop(r.stop)
        if background { EngineState.setLastBackgroundRound(Date().timeIntervalSince1970) }
        let after = EngineState.config()
        if after.mode != c.mode { self.modeChanged(from: c.mode, to: after.mode) }
        if after.active != c.active { self.rearm() } else { self.scheduleProcessingIfNeeded(after) }
        done(r)
        // wakes that came in meanwhile: one more (debounced) round
        if !self.wakeCompletions.isEmpty {
          self.debounce?.cancel()
          let item = DispatchWorkItem { [weak self] in self?.observerRound() }
          self.debounce = item
          self.queue.asyncAfter(deadline: .now() + 3, execute: item)
        }
      }
    }
  }

  // MARK: - range re-sync (contract §8)

  private func startRange(_ days: Int) -> [String: Any]? {
    let c = EngineState.config()
    guard c.active else { return nil }
    let now = Date().timeIntervalSince1970
    var start = days > 0 ? now - Double(days) * 86400 : store.earliestPermittedSampleDate().timeIntervalSince1970
    start = max(start, EngineLimits.epochMin, now - EngineLimits.maxRangeDays * 86400)
    let types = c.readTypes()
    let rawTypes = types.filter { $0.kind == "category" || $0.kind == "workout" || ($0.kind == "quantity" && $0.raw) }.map(\.hk)
    let statsTypes = types.filter { $0.kind == "quantity" && $0.stats }.map(\.hk)
    let r = EngineRange(id: UUID().uuidString.lowercased(), start: start, end: now, types: Array(rawTypes.prefix(100)),
                        statsTypes: statsTypes, cursor: start, startedAt: now)
    EngineState.saveRange(r)
    EngineLog.add("range started: \(days == 0 ? "all" : "\(days) days"), \(rawTypes.count)+\(statsTypes.count) types")
    scheduleProcessingIfNeeded(c)
    return r.map
  }

  // MARK: - status for Dart (sync center)

  private func statusMap() -> [String: Any] {
    let c = EngineState.config()
    let p = EngineState.progress()
    let types = c.readTypes()
    let states = EngineState.typeStates()
    var perType: [String: Any] = [:]
    for t in types {
      var m: [String: Any] = ["recent": p.recent.contains(t.hk) || (t.kind == "quantity" && !t.raw),
                              "backfilled": t.firstSyncDone(p)]
      if let s = states[t.hk] {
        if s.okAt > 0 { m["okAt"] = s.okAt }
        if let e = s.error { m["error"] = e; m["errorAt"] = s.errorAt }
      }
      perType[t.hk] = m
    }
    let done = types.filter { $0.firstSyncDone(p) }.count
    var out: [String: Any] = [
      "mode": c.mode, "enabled": c.enabled, "active": c.active, "cellularHistory": c.cellularHistory,
      "daysBack": c.daysBack, "running": running, "types": perType,
      "firstSync": ["types": types.count, "done": done, "percent": types.isEmpty ? 0 : done * 100 / types.count],
      "log": EngineLog.lines(), "observing": observed.count,
      "lastBackgroundAt": EngineState.lastBackgroundRound(), "lastStop": EngineState.lastStop() ?? NSNull(),
      "notBefore": EngineState.notBefore(),
    ]
    if let r = EngineState.range() { out["range"] = r.map }
    if let r = EngineState.lastRun() { out["lastRun"] = r.map }
    return out
  }

  // MARK: - anchors (A1 keys, unchanged)

  func anchor(_ key: String) -> HKQueryAnchor? {
    guard let s = UserDefaults.standard.string(forKey: anchorPrefix + key), let data = Data(base64Encoded: s) else { return nil }
    return try? NSKeyedUnarchiver.unarchivedObject(ofClass: HKQueryAnchor.self, from: data)
  }

  func commit(_ key: String, _ anchor: HKQueryAnchor?) {
    guard let anchor, let data = try? NSKeyedArchiver.archivedData(withRootObject: anchor, requiringSecureCoding: true) else { return }
    UserDefaults.standard.set(data.base64EncodedString(), forKey: anchorPrefix + key)
  }

  // MARK: - columnar page (also Backfill.swift)

  /// Columnar page with a source table (samples refer to it by index); values in [unit] (categories: HealthKit's integer).
  func page(_ samples: [HKSample], unit: HKUnit?, deleted: [HKDeletedObject], anchor: HKQueryAnchor?, more: Bool)
    -> [String: Any] {
    let sources = EngineSources()
    var cols = EngineColumns()
    for s in samples { cols.add(s, unit: unit, sources: sources) }
    return [
      "uuid": cols.uuid, "start": cols.start, "end": cols.end, "value": cols.value, "src": cols.src, "tz": cols.tz,
      "sources": sources.list, "deleted": deleted.map { $0.uuid.uuidString.lowercased() },
      "anchor": NSNull(), "more": more, "count": samples.count, "tzName": TimeZone.current.identifier,
    ]
  }

  /// watch / phone / other from the product type ("Watch7,1", "iPhone17,3") or the device (as OW's _inferDeviceType).
  static func deviceType(product: String, device: HKDevice?) -> String {
    let p = product.lowercased(), m = (device?.model ?? "").lowercased(), n = (device?.name ?? "").lowercased()
    if p.hasPrefix("watch") || m.contains("watch") || n.contains("watch") { return "watch" }
    if p.hasPrefix("iphone") || m.contains("iphone") || n.contains("iphone") { return "phone" }
    return "other"
  }

  static func osVersion(_ v: OperatingSystemVersion) -> String {
    "\(v.majorVersion).\(v.minorVersion)\(v.patchVersion > 0 ? ".\(v.patchVersion)" : "")"
  }
}

// MARK: - limits of the server (rawdata/engine.py, rawdata/api.py)

enum EngineLimits {
  static let epochMin = 946_684_800.0       // 2000-01-01
  static let epochMax = 4_102_444_800.0     // 2100-01-01
  static let maxBytes = 1_000_000           // gzip per request (the proxy takes 2 MB)
  static let groupItems = 5000
  static let batchItems = 8000              // ours: well under the server's 20000, keeps a batch near 1 MB
  static let buckets = 2880                 // per stats group (30 days; the server takes 10000)
  static let sources = 90                   // the server takes 100
  static let workouts = 150                 // the server takes 200
  static let bucket = 900.0
  static let maxRangeDays = 3600.0
  static let maxSampleSpan = 7.0 * 86400
}

// MARK: - registry and config

struct EngineTypeSpec: Codable {
  var hk: String
  var kind: String
  var unit: String
  var upload: String
  var bg: String
  var order: Int
  var minIos: Int
  var page: [Int]

  var raw: Bool { upload.contains("raw") }
  var stats: Bool { upload.contains("stats") }
  /// Read by the engine (heartbeat series, ECGs and water stay with RawSync).
  var engine: Bool { kind == "quantity" || kind == "category" || kind == "workout" }

  init?(map m: [String: Any]) {
    guard let hk = m["hk"] as? String, let kind = m["kind"] as? String else { return nil }
    self.hk = hk
    self.kind = kind
    unit = m["unit"] as? String ?? ""
    upload = m["upload"] as? String ?? "raw"
    bg = m["bg"] as? String ?? "none"
    order = m["order"] as? Int ?? 50
    minIos = m["min_ios"] as? Int ?? 0
    page = (m["page"] as? [Int]) ?? [2000, 200]
  }

  func pageSize(background: Bool) -> Int { max(1, background ? (page.count > 1 ? page[1] : 200) : (page.first ?? 2000)) }
}

struct EngineConfig: Codable {
  var server = ""
  var appVersion = ""
  var types: [EngineTypeSpec] = []
  var enabled = false
  var mode = "ow"
  var accepted: [String] = []
  var accepts: [String] = []
  var infoAt: Double = 0
  var daysBack = 14
  var cellularHistory = false

  var active: Bool { enabled && (mode == "shadow" || mode == "app") && accepts.contains("phase") && !server.isEmpty }

  /// The registry types this round may read: engine kinds, taken by the server, known to this iOS.
  func readTypes() -> [EngineTypeSpec] {
    let ios = ProcessInfo.processInfo.operatingSystemVersion.majorVersion
    let ok = Set(accepted)
    return types.filter { $0.engine && ok.contains($0.hk) && ($0.minIos == 0 || ios >= $0.minIos) }
  }

  mutating func apply(engine e: [String: Any]) {
    enabled = e["enabled"] as? Bool ?? false
    mode = enabled ? (e["mode"] as? String ?? "shadow") : "ow"
    accepted = (e["types"] as? [Any])?.compactMap { $0 as? String } ?? []
    accepts = (e["accepts"] as? [Any])?.compactMap { $0 as? String } ?? []
    infoAt = Date().timeIntervalSince1970
  }
}

struct EngineProgress: Codable {
  var recent = Set<String>()
  var backfilled = Set<String>()

  func allBackfilled(_ types: [EngineTypeSpec]) -> Bool { types.allSatisfy { $0.firstSyncDone(self) } }
}

struct EngineTypeState: Codable {
  var okAt: Double = 0
  var error: String?
  var errorAt: Double = 0
  var limit: Int?                 // a smaller page after a 413
}

struct EngineRun: Codable {
  var id: String
  var trigger: String
  var result: String
  var error: String?
  var started: Double
  var durationMs: Int
  var batches: Int
  var bytes: Int
  var samples: Int

  var map: [String: Any] {
    var m: [String: Any] = ["id": id, "trigger": trigger, "result": result, "started": started, "duration_ms": durationMs,
                            "batches": batches, "bytes": bytes, "samples": samples]
    if let error { m["error"] = String(error.prefix(80)) }
    return m
  }
}

struct EngineRange: Codable {
  var id: String
  var start: Double
  var end: Double
  var types: [String]
  var statsTypes: [String]
  var cursor: Double
  var statsDone = false
  var startedAt: Double
  var batches = 0
  var samples = 0
  var error: String?

  var percent: Int {
    if statsDone { return 99 }
    return Int(min(98, max(0, (cursor - start) / max(1, end - start) * 98)))
  }

  var map: [String: Any] {
    ["id": id, "start": start, "end": end, "cursor": cursor, "percent": percent, "batches": batches, "samples": samples,
     "error": error ?? NSNull(), "startedAt": startedAt, "active": true]
  }
}

/// Persisted engine state (UserDefaults; counts, times, codes and anchors only).
enum EngineState {
  private static let d = UserDefaults.standard
  private static let configKey = "w2w.sync.engine.config.v1"
  private static let progressKey = "w2w.sync.engine.progress.v1"
  private static let typesKey = "w2w.sync.engine.types.v1"
  private static let runsKey = "w2w.sync.engine.runs.v1"
  private static let lastRunKey = "w2w.sync.engine.lastRun.v1"
  private static let rangeKey = "w2w.sync.engine.range.v1"
  private static let notBeforeKey = "w2w.sync.engine.notBefore"
  private static let lastBgKey = "w2w.sync.engine.lastBackground"
  private static let lastStopKey = "w2w.sync.engine.lastStop"
  private static let lock = NSLock()

  private static func get<T: Decodable>(_ key: String, _ t: T.Type) -> T? {
    guard let data = d.data(forKey: key) else { return nil }
    return try? JSONDecoder().decode(t, from: data)
  }

  private static func put<T: Encodable>(_ key: String, _ v: T?) {
    if let v, let data = try? JSONEncoder().encode(v) { d.set(data, forKey: key) } else { d.removeObject(forKey: key) }
  }

  static func config() -> EngineConfig { get(configKey, EngineConfig.self) ?? EngineConfig() }
  static func save(_ c: EngineConfig) { put(configKey, c) }
  static func progress() -> EngineProgress { get(progressKey, EngineProgress.self) ?? EngineProgress() }
  static func update(_ f: (inout EngineProgress) -> Void) {
    lock.lock(); defer { lock.unlock() }
    var p = progress()
    f(&p)
    put(progressKey, p)
  }
  static func typeStates() -> [String: EngineTypeState] { get(typesKey, [String: EngineTypeState].self) ?? [:] }
  static func updateType(_ hk: String, _ f: (inout EngineTypeState) -> Void) {
    lock.lock(); defer { lock.unlock() }
    var all = typeStates()
    var s = all[hk] ?? EngineTypeState()
    f(&s)
    all[hk] = s
    put(typesKey, all)
  }
  static func pendingRuns() -> [EngineRun] { get(runsKey, [EngineRun].self) ?? [] }
  static func addRun(_ r: EngineRun) {
    lock.lock(); defer { lock.unlock() }
    put(runsKey, Array((pendingRuns() + [r]).suffix(40)))
    put(lastRunKey, r)
  }
  static func dropRuns(_ ids: Set<String>) {
    lock.lock(); defer { lock.unlock() }
    put(runsKey, pendingRuns().filter { !ids.contains($0.id) })
  }
  static func lastRun() -> EngineRun? { get(lastRunKey, EngineRun.self) }
  static func range() -> EngineRange? { get(rangeKey, EngineRange.self) }
  static func saveRange(_ r: EngineRange?) { put(rangeKey, r) }
  static func notBefore() -> Double { d.double(forKey: notBeforeKey) }
  static func setNotBefore(_ t: Double) { d.set(t, forKey: notBeforeKey) }
  static func lastBackgroundRound() -> Double { d.double(forKey: lastBgKey) }
  static func setLastBackgroundRound(_ t: Double) { d.set(t, forKey: lastBgKey) }
  static func lastStop() -> String? { d.string(forKey: lastStopKey) }
  static func setLastStop(_ s: String?) { d.set(s ?? "", forKey: lastStopKey) }

  /// A1 kept "read the newest 48 hours" / "first crawl done" in Dart (shared_preferences = "flutter." keys).
  static func migrateFromDart() {
    let recentKey = "flutter.w2w.sync.recent.v1", fullKey = "flutter.w2w.sync.backfilled.v1"
    let recent = d.stringArray(forKey: recentKey) ?? [], full = d.stringArray(forKey: fullKey) ?? []
    guard !recent.isEmpty || !full.isEmpty else { return }
    update { p in
      p.recent.formUnion(recent)
      p.backfilled.formUnion(full)
    }
    d.removeObject(forKey: recentKey)
    d.removeObject(forKey: fullKey)
  }

  static func clear() {
    for k in [configKey, progressKey, typesKey, runsKey, lastRunKey, rangeKey, notBeforeKey, lastBgKey, lastStopKey] {
      d.removeObject(forKey: k)
    }
    EngineLog.clear()
  }
}

/// The run log for the sync center (≤ 200 lines: time, trigger, result, counts; never health values).
enum EngineLog {
  private static let key = "w2w.sync.engine.log.v1"
  private static let lock = NSLock()
  private static let fmt: ISO8601DateFormatter = ISO8601DateFormatter()

  static func add(_ line: String) {
    lock.lock(); defer { lock.unlock() }
    var l = UserDefaults.standard.stringArray(forKey: key) ?? []
    l.append("\(fmt.string(from: Date()))\t\(line)")
    if l.count > 200 { l.removeFirst(l.count - 200) }
    UserDefaults.standard.set(l, forKey: key)
  }

  static func lines() -> [String] { UserDefaults.standard.stringArray(forKey: key) ?? [] }
  static func clear() { UserDefaults.standard.removeObject(forKey: key) }
}

/// The token for background rounds: Keychain, readable after the first unlock, this device only.
enum EngineKeychain {
  private static let base: [String: Any] = [kSecClass as String: kSecClassGenericPassword,
                                            kSecAttrService as String: "w2w.sync", kSecAttrAccount as String: "token"]

  static func write(_ token: String) {
    delete()
    var q = base
    q[kSecValueData as String] = Data(token.utf8)
    q[kSecAttrAccessible as String] = kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly
    SecItemAdd(q as CFDictionary, nil)
  }

  static func read() -> String? {
    var q = base
    q[kSecReturnData as String] = true
    q[kSecMatchLimit as String] = kSecMatchLimitOne
    var out: AnyObject?
    guard SecItemCopyMatching(q as CFDictionary, &out) == errSecSuccess, let d = out as? Data else { return nil }
    return String(data: d, encoding: .utf8)
  }

  static func delete() { SecItemDelete(base as CFDictionary) }
}

// MARK: - HealthKit reading

enum EngineHKError: Error {
  case locked, notAsked, other(String)

  static func from(_ e: Error) -> EngineHKError {
    let ns = e as NSError
    if ns.domain == HKErrorDomain {
      if ns.code == HKError.Code.errorDatabaseInaccessible.rawValue { return .locked }
      if ns.code == HKError.Code.errorAuthorizationNotDetermined.rawValue { return .notAsked }
    }
    return .other("hk\(ns.code)")
  }
}

enum EngineHK {
  static func sampleType(_ t: EngineTypeSpec) -> HKSampleType? {
    switch t.kind {
    case "quantity": return HKQuantityType.quantityType(forIdentifier: HKQuantityTypeIdentifier(rawValue: t.hk))
    case "category": return HKCategoryType.categoryType(forIdentifier: HKCategoryTypeIdentifier(rawValue: t.hk))
    case "workout": return HKObjectType.workoutType()
    default: return nil
    }
  }

  static func anchored(_ store: HKHealthStore, _ type: HKSampleType, predicate: NSPredicate?, anchor: HKQueryAnchor?,
                       limit: Int) async throws -> ([HKSample], [HKDeletedObject], HKQueryAnchor?) {
    try await withCheckedThrowingContinuation { cont in
      let q = HKAnchoredObjectQuery(type: type, predicate: predicate, anchor: anchor, limit: limit) { _, s, d, a, e in
        if let e { cont.resume(throwing: EngineHKError.from(e)) } else { cont.resume(returning: (s ?? [], d ?? [], a)) }
      }
      store.execute(q)
    }
  }

  static func samples(_ store: HKHealthStore, _ type: HKSampleType, predicate: NSPredicate?, limit: Int,
                      newestFirst: Bool) async throws -> [HKSample] {
    try await withCheckedThrowingContinuation { cont in
      let q = HKSampleQuery(sampleType: type, predicate: predicate, limit: limit,
                            sortDescriptors: [NSSortDescriptor(key: HKSampleSortIdentifierStartDate, ascending: !newestFirst)]) { _, s, e in
        if let e { cont.resume(throwing: EngineHKError.from(e)) } else { cont.resume(returning: s ?? []) }
      }
      store.execute(q)
    }
  }

  /// Sums of 15-minute buckets from [start] (aligned to 900 s UTC) for [count] buckets, in [unit]; 0 where empty.
  static func buckets(_ store: HKHealthStore, _ type: HKQuantityType, unit: HKUnit, start: Double, count: Int) async throws -> [Double] {
    let r: Result<[Double], Error> = await withCheckedContinuation { cont in
      buckets(store, type, unit: unit, start: start, count: count, result: { cont.resume(returning: $0) })
    }
    switch r {
    case .success(let v): return v
    case .failure(let e): throw EngineHKError.from(e)
    }
  }

  /// Callback form (Backfill.swift): nil on a HealthKit error.
  static func buckets(_ store: HKHealthStore, _ type: HKQuantityType, unit: HKUnit, start: Double, count: Int,
                      _ done: @escaping ([Double]?) -> Void) {
    buckets(store, type, unit: unit, start: start, count: count, result: { r in done(try? r.get()) })
  }

  private static func buckets(_ store: HKHealthStore, _ type: HKQuantityType, unit: HKUnit, start: Double, count: Int,
                              result done: @escaping (Result<[Double], Error>) -> Void) {
    let from = Date(timeIntervalSince1970: start), to = Date(timeIntervalSince1970: start + Double(count) * EngineLimits.bucket)
    let q = HKStatisticsCollectionQuery(quantityType: type,
                                        quantitySamplePredicate: HKQuery.predicateForSamples(withStart: from, end: to),
                                        options: .cumulativeSum, anchorDate: Date(timeIntervalSince1970: 0),
                                        intervalComponents: DateComponents(minute: 15))
    q.initialResultsHandler = { _, coll, e in
      if let e { done(.failure(e)); return }
      var out = [Double](repeating: 0, count: count)
      coll?.enumerateStatistics(from: from, to: to) { s, _ in
        let i = Int(((s.startDate.timeIntervalSince1970 - start) / EngineLimits.bucket).rounded())
        guard i >= 0, i < count, let q = s.sumQuantity(), q.is(compatibleWith: unit) else { return }
        let v = q.doubleValue(for: unit)
        if v.isFinite, v > 0 { out[i] = v }
      }
      done(.success(out))
    }
    store.execute(q)
  }
}

/// The batch's source table (sources[]; samples refer to it by index).
final class EngineSources {
  private(set) var list: [[String: Any]] = []
  private var index: [String: Int] = [:]

  func add(_ s: HKObject) -> Int {
    let rev = s.sourceRevision, dev = s.device
    let product = rev.productType ?? ""
    let os = SyncEngine.osVersion(rev.operatingSystemVersion)
    let key = [rev.source.name, rev.source.bundleIdentifier, product, dev?.name ?? "", dev?.model ?? "", os].joined(separator: "|")
    if let i = index[key] { return i }
    list.append([
      "name": String(rev.source.name.prefix(120)),
      "bundle": String(rev.source.bundleIdentifier.prefix(200)),
      "product": String(product.prefix(60)),
      "device_type": SyncEngine.deviceType(product: product, device: dev),
      "device_name": String((dev?.name ?? "").prefix(120)),
      "device_model": String((dev?.model ?? "").prefix(120)),
      "manufacturer": String((dev?.manufacturer ?? "").prefix(120)),
      "os_version": String(os.prefix(40)),
    ])
    index[key] = list.count - 1
    return list.count - 1
  }
}

/// One type's samples as columns (samples[] / categories[] of the contract).
struct EngineColumns {
  var uuid: [String] = [], start: [Double] = [], end: [Double] = [], value: [Double] = [], src: [Int] = []
  var tz: [Any] = []
  var count: Int { uuid.count }

  /// Adds [s] unless the server would refuse the whole batch for it (times outside 2000–2100, not finite, > 7 days).
  mutating func add(_ s: HKSample, unit: HKUnit?, sources: EngineSources) {
    let v: Double
    if let unit, let q = s as? HKQuantitySample {
      guard q.quantity.is(compatibleWith: unit) else { return }   // doubleValue would raise an uncatchable exception
      v = q.quantity.doubleValue(for: unit)
    } else if let c = s as? HKCategorySample {
      v = Double(c.value)
    } else {
      return
    }
    let a = s.startDate.timeIntervalSince1970, b = s.endDate.timeIntervalSince1970
    guard v.isFinite, a >= EngineLimits.epochMin, b < EngineLimits.epochMax, b >= a else { return }
    uuid.append(s.uuid.uuidString.lowercased())
    start.append(a)
    end.append(b)
    value.append(v)
    src.append(sources.add(s))
    tz.append((s.metadata?[HKMetadataKeyTimeZone] as? String).map { String($0.prefix(64)) } ?? NSNull())
  }

  /// Groups of at most 5000 for the request (the server's group limit).
  func groups(type: String, unit: String) -> [[String: Any]] {
    var out: [[String: Any]] = []
    var i = 0
    while i < count {
      let j = min(count, i + EngineLimits.groupItems)
      var g: [String: Any] = ["type": type, "unit": unit, "uuid": Array(uuid[i..<j]), "start": Array(start[i..<j]),
                              "end": Array(end[i..<j]), "value": Array(value[i..<j]), "src": Array(src[i..<j])]
      if tz[i..<j].contains(where: { !($0 is NSNull) }) { g["tz"] = Array(tz[i..<j]) }
      out.append(g)
      i = j
    }
    return out
  }
}

// MARK: - workouts (A3)

enum EngineWorkout {
  /// One HKWorkout for workouts[]: statistics, events and metadata the server takes (rawdata/api.py WorkoutIn); values
  /// outside the server's bounds are left out (one bad value would refuse the batch). nil = not sendable.
  static func map(_ w: HKWorkout, src: Int) -> [String: Any]? {
    let a = w.startDate.timeIntervalSince1970, b = w.endDate.timeIntervalSince1970
    guard a >= EngineLimits.epochMin, b < EngineLimits.epochMax, b >= a, w.duration >= 0, w.duration <= EngineLimits.maxSampleSpan
    else { return nil }
    var stats: [String: Any] = [:]
    func put(_ k: String, _ v: Double?, _ hi: Double) { if let v, v.isFinite, v >= 0, v <= hi { stats[k] = v } }
    let kcal = HKUnit.kilocalorie(), m = HKUnit.meter(), bpm = HKUnit.count().unitDivided(by: .minute())
    if #available(iOS 16.0, *) {
      func st(_ id: HKQuantityTypeIdentifier) -> HKStatistics? { w.statistics(for: HKQuantityType(id)) }
      put("active_kcal", st(.activeEnergyBurned)?.sumQuantity()?.doubleValue(for: kcal), 20000)
      put("basal_kcal", st(.basalEnergyBurned)?.sumQuantity()?.doubleValue(for: kcal), 20000)
      let dist: [HKQuantityTypeIdentifier] = [.distanceWalkingRunning, .distanceCycling, .distanceSwimming,
                                              .distanceWheelchair, .distanceDownhillSnowSports]
      put("distance_m", dist.compactMap { st($0)?.sumQuantity()?.doubleValue(for: m) }.max(), 1_000_000)
      put("steps", st(.stepCount)?.sumQuantity()?.doubleValue(for: .count()), 500_000)
      put("strokes", st(.swimmingStrokeCount)?.sumQuantity()?.doubleValue(for: .count()), 100_000)
      let hr = st(.heartRate)
      put("min_hr", hr?.minimumQuantity()?.doubleValue(for: bpm), 300)
      put("avg_hr", hr?.averageQuantity()?.doubleValue(for: bpm), 300)
      put("max_hr", hr?.maximumQuantity()?.doubleValue(for: bpm), 300)
      var power = st(.runningPower), speed = st(.runningSpeed)
      if #available(iOS 17.0, *) {
        power = power ?? st(.cyclingPower)
        speed = speed ?? st(.cyclingSpeed)
        put("avg_cadence", st(.cyclingCadence)?.averageQuantity()?.doubleValue(for: bpm), 400)
      }
      put("avg_power_w", power?.averageQuantity()?.doubleValue(for: .watt()), 3000)
      put("max_power_w", power?.maximumQuantity()?.doubleValue(for: .watt()), 5000)
      put("avg_speed_mps", speed?.averageQuantity()?.doubleValue(for: HKUnit.meter().unitDivided(by: .second())), 100)
    } else {
      put("active_kcal", w.totalEnergyBurned?.doubleValue(for: kcal), 20000)
      put("distance_m", w.totalDistance?.doubleValue(for: m), 1_000_000)
      put("strokes", w.totalSwimmingStrokeCount?.doubleValue(for: .count()), 100_000)
    }
    let md = w.metadata ?? [:]
    put("elevation_m", (md[HKMetadataKeyElevationAscended] as? HKQuantity)?.doubleValue(for: m), 10000)
    var meta: [String: Any] = [:]
    if let v = md[HKMetadataKeyIndoorWorkout] as? Bool { meta["indoor"] = v }
    if let v = md[HKMetadataKeyIndoorWorkout] as? NSNumber, meta["indoor"] == nil { meta["indoor"] = v.boolValue }
    if let v = md[HKMetadataKeyTimeZone] as? String { meta["tz"] = String(v.prefix(64)) }
    if let q = md[HKMetadataKeyAverageMETs] as? HKQuantity {
      let met = HKUnit.kilocalorie().unitDivided(by: HKUnit.gramUnit(with: .kilo).unitMultiplied(by: .hour()))
      if q.is(compatibleWith: met) { meta["avg_met"] = q.doubleValue(for: met) }
    }
    if let v = md[HKMetadataKeySwimmingLocationType] as? NSNumber { meta["swim_location"] = v.doubleValue }
    if let q = md[HKMetadataKeyLapLength] as? HKQuantity, q.is(compatibleWith: m) { meta["lap_length_m"] = q.doubleValue(for: m) }
    if let q = md[HKMetadataKeyWeatherTemperature] as? HKQuantity, q.is(compatibleWith: .degreeCelsius()) {
      meta["weather_temp_c"] = q.doubleValue(for: .degreeCelsius())
    }
    if let q = md[HKMetadataKeyWeatherHumidity] as? HKQuantity, q.is(compatibleWith: .percent()) {
      meta["weather_humidity"] = q.doubleValue(for: .percent())
    }
    if #available(iOS 16.0, *), w.workoutActivities.count > 1 { meta["activities"] = Double(w.workoutActivities.count) }
    var events: [[String: Any]] = []
    for e in (w.workoutEvents ?? []).prefix(500) {
      let s = e.dateInterval.start.timeIntervalSince1970, t = e.dateInterval.end.timeIntervalSince1970
      guard s >= EngineLimits.epochMin, t < EngineLimits.epochMax else { continue }
      events.append(["type": eventName(e.type), "start": s, "end": t])
    }
    return ["uuid": w.uuid.uuidString.lowercased(), "activity": Int(w.workoutActivityType.rawValue),
            "activity_name": activityName(w.workoutActivityType), "start": a, "end": b, "duration_s": w.duration,
            "stats": stats, "events": events, "meta": meta, "src": src]
  }

  static func eventName(_ t: HKWorkoutEventType) -> String {
    switch t {
    case .pause: return "pause"
    case .resume: return "resume"
    case .lap: return "lap"
    case .marker: return "marker"
    case .motionPaused: return "motion_paused"
    case .motionResumed: return "motion_resumed"
    case .segment: return "segment"
    case .pauseOrResumeRequest: return "pause_or_resume_request"
    @unknown default: return "other"
    }
  }

  /// snake_case names (as Open Wearables' SDK); the server keeps the raw value too.
  static func activityName(_ t: HKWorkoutActivityType) -> String {
    var names: [HKWorkoutActivityType: String] = [
      .americanFootball: "american_football", .archery: "archery", .australianFootball: "australian_football",
      .badminton: "badminton", .baseball: "baseball", .basketball: "basketball", .bowling: "bowling", .boxing: "boxing",
      .climbing: "climbing", .cricket: "cricket", .crossTraining: "cross_training", .curling: "curling",
      .cycling: "cycling", .dance: "dance", .elliptical: "elliptical", .equestrianSports: "equestrian_sports",
      .fencing: "fencing", .fishing: "fishing", .functionalStrengthTraining: "functional_strength_training",
      .golf: "golf", .gymnastics: "gymnastics", .handball: "handball", .hiking: "hiking", .hockey: "hockey",
      .hunting: "hunting", .lacrosse: "lacrosse", .martialArts: "martial_arts", .mindAndBody: "mind_and_body",
      .paddleSports: "paddle_sports", .play: "play", .preparationAndRecovery: "preparation_and_recovery",
      .racquetball: "racquetball", .rowing: "rowing", .rugby: "rugby", .running: "running", .sailing: "sailing",
      .skatingSports: "skating_sports", .snowSports: "snow_sports", .soccer: "soccer", .softball: "softball",
      .squash: "squash", .stairClimbing: "stair_climbing", .surfingSports: "surfing_sports", .swimming: "swimming",
      .tableTennis: "table_tennis", .tennis: "tennis", .trackAndField: "track_and_field",
      .traditionalStrengthTraining: "traditional_strength_training", .volleyball: "volleyball", .walking: "walking",
      .waterFitness: "water_fitness", .waterPolo: "water_polo", .waterSports: "water_sports", .wrestling: "wrestling",
      .yoga: "yoga", .barre: "barre", .coreTraining: "core_training", .crossCountrySkiing: "cross_country_skiing",
      .downhillSkiing: "downhill_skiing", .flexibility: "flexibility", .highIntensityIntervalTraining: "hiit",
      .jumpRope: "jump_rope", .kickboxing: "kickboxing", .pilates: "pilates", .snowboarding: "snowboarding",
      .stairs: "stairs", .stepTraining: "step_training", .wheelchairWalkPace: "wheelchair_walk_pace",
      .wheelchairRunPace: "wheelchair_run_pace", .taiChi: "tai_chi", .mixedCardio: "mixed_cardio",
      .handCycling: "hand_cycling", .discSports: "disc_sports", .fitnessGaming: "fitness_gaming", .other: "other",
    ]
    if #available(iOS 16.0, *) {
      names[.cardioDance] = "cardio_dance"
      names[.socialDance] = "social_dance"
      names[.pickleball] = "pickleball"
      names[.cooldown] = "cooldown"
      names[.swimBikeRun] = "swim_bike_run"
      names[.transition] = "transition"
    }
    return names[t] ?? "type_\(t.rawValue)"
  }
}

// MARK: - one round

struct EngineNet {
  var satisfied: Bool
  var unmetered: Bool
  var constrained: Bool
}

struct EngineRoundResult {
  var stop: String?
  var batches = 0, samples = 0, bytes = 0, types = 0
  var skipped: [String: String] = [:]
  var uploaded = Set<String>()
  var retryAfter: Double?
  var mode: String?

  var map: [String: Any] {
    var m: [String: Any] = ["batches": batches, "samples": samples, "bytes": bytes, "types": types, "skipped": skipped,
                            "uploaded": Array(uploaded), "mode": mode ?? NSNull(), "stop": stop ?? NSNull()]
    if let retryAfter { m["retryAfter"] = retryAfter }
    return m
  }
}

/// What one type adds to a batch: its groups / workouts / buckets / deletions and what to commit after the 200.
final class EnginePayload {
  let hk: String
  var cols = EngineColumns()
  var unit = ""
  var field = "samples"             // samples / categories
  var workouts: [[String: Any]] = []
  var stats: [[String: Any]] = []
  var deleted: [String] = []
  var commits: [String: HKQueryAnchor] = [:]
  var markRecent = false, markBackfilled = false, markStats = false
  var phase = "incremental"

  init(hk: String) { self.hk = hk }
  var items: Int { cols.count + workouts.count + stats.reduce(0) { $0 + (($1["values"] as? [Double])?.count ?? 0) } }
  var hasData: Bool { items > 0 || !deleted.isEmpty }
}

/// Upload outcomes (contract §4).
enum EngineUpload {
  case ok([String: Any])
  case unauthorized
  case forbidden(String?)
  case refused(String?)
  case tooLarge
  case rejected(String?)
  case rateLimited(Double)
  case failed(Int)
  case offline
  case cancelled
}

final class EngineRound {
  let engine: SyncEngine
  var config: EngineConfig
  let token: String
  let trigger: String
  let background: Bool
  let deadline: Date
  let net: EngineNet
  let protectedData: Bool
  let t0 = Date()
  var result = EngineRoundResult()
  let sources = EngineSources()
  var payloads: [EnginePayload] = []
  var progress: EngineProgress
  var states: [String: EngineTypeState]
  private let session: URLSession

  init(engine: SyncEngine, config: EngineConfig, token: String, trigger: String, background: Bool, budget: Double,
       net: EngineNet, protectedData: Bool) {
    self.engine = engine
    self.config = config
    self.token = token
    self.trigger = trigger
    self.background = background
    deadline = Date().addingTimeInterval(budget)
    self.net = net
    self.protectedData = protectedData
    progress = EngineState.progress()
    states = EngineState.typeStates()
    let c = URLSessionConfiguration.ephemeral
    c.timeoutIntervalForRequest = 30
    c.timeoutIntervalForResource = 60
    c.waitsForConnectivity = false
    session = URLSession(configuration: c)
  }

  private var cancelled: Bool { Task.isCancelled }
  private var outOfTime: Bool { cancelled || Date().addingTimeInterval(background ? 8 : 0) >= deadline }
  private var maxBatches: Int { trigger == "processing" ? 400 : (background ? 12 : 40) }

  /// History (the first crawl, ranges): Wi-Fi only unless allowed; never in Low Power Mode or when hot.
  private var historyAllowed: (Bool, String) {
    if ProcessInfo.processInfo.isLowPowerModeEnabled { return (false, "low_power") }
    if ProcessInfo.processInfo.thermalState.rawValue >= ProcessInfo.ThermalState.serious.rawValue { return (false, "thermal") }
    if net.constrained || (!net.unmetered && !config.cellularHistory) { return (false, "wifi_only") }
    return (true, "")
  }

  func run() async -> EngineRoundResult {
    await work()
    finish()
    await reportRuns()
    return result
  }

  private func work() async {
    if !protectedData {
      result.stop = "locked"     // HealthKit is not readable while the phone is locked: on unlock (protectedData…)
      return
    }
    if !net.satisfied {
      result.stop = "offline"    // nothing would get through: on the network's return
      return
    }
    // the engine block at least every 6 hours (contract §2); in the foreground when it is older than 10 minutes (the
    // mode decides whether Open Wearables runs, and a round without uploads gets no answer that carries it)
    if Date().timeIntervalSince1970 - config.infoAt > (background ? 6 * 3600 : 600) {
      await refreshInfo()
      if !config.active { result.stop = "off"; return }
    }
    let lowPower = background && ProcessInfo.processInfo.isLowPowerModeEnabled
    var types = config.readTypes()   // the registry's order: sleep and the overnight readings first, heart rate last
    if lowPower { types = types.filter { $0.bg == "immediate" } }
    for t in types {
      if result.stop != nil { break }
      if outOfTime || result.batches >= maxBatches { result.stop = "budget"; break }
      result.types += 1
      if t.kind == "workout" || t.kind == "category" || (t.kind == "quantity" && t.raw) { await rawType(t) }
      if result.stop == nil, t.kind == "quantity", t.stats, result.skipped[t.hk] == nil { await statsType(t) }
    }
    // what was read still goes when time ran out or the phone got locked (no anchor moves without a 200)
    if result.stop == nil || ((result.stop == "budget" || result.stop == "locked") && !cancelled) { await flush() }
    if result.stop == nil, !lowPower, EngineState.range() != nil, !background || trigger == "processing" {
      await rangeWork()
    }
  }

  // MARK: raw samples, categories, workouts

  private func rawType(_ t: EngineTypeSpec) async {
    guard let type = EngineHK.sampleType(t) else { return }
    var unit: HKUnit?
    if t.kind == "quantity" {
      let u = HKUnit(from: t.unit)
      guard let q = type as? HKQuantityType, q.is(compatibleWith: u) else { skip(t.hk, "bad_unit"); return }
      unit = u
    }
    var limit = min(t.pageSize(background: background && trigger != "processing"), states[t.hk]?.limit ?? Int.max)
    if trigger == "processing" { limit = min(limit, t.pageSize(background: false)) }
    var recent = !progress.recent.contains(t.hk)
    let backfilled = progress.backfilled.contains(t.hk)
    var anchor = engine.anchor(t.hk)
    let since = config.daysBack > 0 ? Date().addingTimeInterval(-Double(config.daysBack) * 86400) : nil
    while true {
      if outOfTime || result.batches >= maxBatches { result.stop = "budget"; return }
      let phase = recent ? "recent" : (backfilled ? "incremental" : "backfill")
      if phase == "backfill" {
        let (ok, why) = historyAllowed
        if !ok { skip(t.hk, why, error: false); return }
      }
      let p = payload(t.hk)
      if p.phase != "backfill" { p.phase = phase }
      if t.kind == "category" { p.field = "categories" }
      p.unit = t.kind == "category" ? "" : t.unit
      do {
        if recent {
          let list = try await EngineHK.samples(engine.store, type, predicate: HKQuery.predicateForSamples(
            withStart: Date().addingTimeInterval(-SyncEngine.recentWindow), end: nil), limit: limit, newestFirst: true)
          add(list, to: p, unit: unit)
          p.markRecent = true
          recent = false
          continue
        }
        // the window only limits the first crawl: afterwards no predicate, so deletions (no dates) and samples that
        // arrive late with old dates still come through
        let (list, deleted, next) = try await EngineHK.anchored(engine.store, type,
            predicate: backfilled ? nil : since.map { HKQuery.predicateForSamples(withStart: $0, end: nil) }, anchor: anchor,
            limit: limit)
        add(list, to: p, unit: unit)
        p.deleted += deleted.map { $0.uuid.uuidString.lowercased() }
        if let next { p.commits[t.hk] = next; anchor = next }
        let more = list.count + deleted.count >= limit
        if !more {
          if !backfilled { p.markBackfilled = true }
          await flushIfFull()
          return
        }
      } catch let e as EngineHKError {
        hkFailed(t.hk, e)
        return
      } catch {
        skip(t.hk, "read")
        return
      }
      await flushIfFull()
      if result.stop != nil || result.skipped[t.hk] != nil { return }
    }
  }

  private func add(_ list: [HKSample], to p: EnginePayload, unit: HKUnit?) {
    for s in list {
      if let w = s as? HKWorkout {
        if let m = EngineWorkout.map(w, src: sources.add(w)) { p.workouts.append(m) }
      } else {
        p.cols.add(s, unit: unit, sources: sources)
      }
    }
  }

  // MARK: 15-minute buckets of cumulative types

  /// How far back a change of a cumulative type is sent again (older: Backfill.swift / a range re-sync).
  private static let statsLookback = 35.0 * 86400

  private func statsType(_ t: EngineTypeSpec) async {
    guard let type = HKQuantityType.quantityType(forIdentifier: HKQuantityTypeIdentifier(rawValue: t.hk)) else { return }
    let unit = HKUnit(from: t.unit)
    guard type.is(compatibleWith: unit) else { skip(t.hk, "bad_unit"); return }
    let key = "stats:" + t.hk
    let first = !progress.backfilled.contains(key)
    let history = historyAllowed.0
    let now = Date().timeIntervalSince1970
    let window = HKQuery.predicateForSamples(withStart: Date(timeIntervalSince1970: now - Self.statsLookback), end: nil)
    let small = background && trigger != "processing"
    let pageSize = small ? 500 : 5000
    var anchor = engine.anchor(key)
    var earliest: Double?
    var commit: HKQueryAnchor?
    var pages = 0
    // what changed since the last commit (samples of the last 35 days): the earliest start among them; a deletion =
    // the last 7 days again. The buckets themselves come from the statistics query, not from these samples.
    while pages < (small ? 3 : 20) {
      if outOfTime { result.stop = "budget"; return }
      do {
        let (list, deleted, next) = try await EngineHK.anchored(engine.store, type, predicate: first ? window : nil, anchor: anchor,
                                                                limit: pageSize)
        pages += 1
        if let m = list.map({ $0.startDate.timeIntervalSince1970 }).min() { earliest = min(earliest ?? m, m) }
        if !deleted.isEmpty { earliest = min(earliest ?? now, now - 7 * 86400) }
        if let next { commit = next; anchor = next }
        if list.count + deleted.count < pageSize { break }
      } catch let e as EngineHKError {
        hkFailed(t.hk, e)
        return
      } catch {
        skip(t.hk, "read")
        return
      }
    }
    if first && earliest == nil {
      // nothing of this type in the window (e.g. no bike, no pool): no buckets of zeros, just remember where we are
      if let commit { engine.commit(key, commit) }
      if history { EngineState.update { $0.backfilled.insert(key) }; progress = EngineState.progress() }
      return
    }
    var from: Double
    if first {
      // the first time: the first-sync window on Wi-Fi, else only the newest 2 days (the rest once history may go)
      from = now - (history ? Double(max(config.daysBack, 2)) : 2) * 86400
    } else if let earliest {
      from = earliest
    } else {
      if let commit { engine.commit(key, commit) }   // nothing changed: no request
      return
    }
    from = max(from, now - Self.statsLookback, EngineLimits.epochMin)
    let start = (from / EngineLimits.bucket).rounded(.down) * EngineLimits.bucket
    let count = Int(((now - start) / EngineLimits.bucket).rounded(.up))
    guard count > 0 else { return }
    let p = payload(t.hk)
    do {
      var s = start, left = count
      while left > 0 {
        let n = min(left, EngineLimits.buckets)
        let values = try await EngineHK.buckets(engine.store, type, unit: unit, start: s, count: n)
        p.stats.append(["type": t.hk, "unit": t.unit, "bucket_s": Int(EngineLimits.bucket), "start": Int(s), "values": values])
        s += Double(n) * EngineLimits.bucket
        left -= n
      }
    } catch let e as EngineHKError {
      hkFailed(t.hk, e)
      return
    } catch {
      skip(t.hk, "read")
      return
    }
    if let commit { p.commits[key] = commit }
    if first && history {
      p.markStats = true
      p.phase = "backfill"
    }
    await flushIfFull()
  }

  // MARK: range re-sync

  private func rangeWork() async {
    guard var r = EngineState.range() else { return }
    let (ok, why) = historyAllowed
    if !ok { result.skipped["range"] = why; return }
    let byHk = Dictionary(config.readTypes().map { ($0.hk, $0) }, uniquingKeysWith: { a, _ in a })
    let rangeField: [String: Any] = ["id": r.id, "start": r.start, "end": r.end, "types": r.types]
    while r.cursor < r.end {
      if outOfTime || result.batches >= maxBatches { result.stop = "budget"; return }
      let end = min(r.end, r.cursor + 86400)
      let pred = HKQuery.predicateForSamples(withStart: Date(timeIntervalSince1970: r.cursor),
                                             end: Date(timeIntervalSince1970: end), options: [.strictStartDate])
      var any = false
      for hk in r.types {
        guard let t = byHk[hk], let type = EngineHK.sampleType(t) else { continue }
        let unit: HKUnit? = t.kind == "quantity" ? HKUnit(from: t.unit) : nil
        if let unit, let q = type as? HKQuantityType, !q.is(compatibleWith: unit) { continue }
        do {
          let list = try await EngineHK.samples(engine.store, type, predicate: pred, limit: HKObjectQueryNoLimit, newestFirst: false)
          guard !list.isEmpty else { continue }
          any = true
          let p = payload(hk)
          p.phase = "range"
          p.field = t.kind == "category" ? "categories" : "samples"
          p.unit = t.kind == "category" ? "" : t.unit
          add(list, to: p, unit: unit)
        } catch let e as EngineHKError {
          if case .locked = e { result.stop = "locked"; return }
          continue   // a type never asked: nothing of it in the range either
        } catch {
          continue
        }
      }
      if any {
        await flush(range: rangeField)
        if result.stop != nil { r.error = result.stop; EngineState.saveRange(r); return }
        r.cursor = end
      } else {
        // nothing that day: straight to the day of the next sample (years of "all history" may be empty)
        let next = await nextStart(after: end, types: r.types.compactMap { byHk[$0] }) ?? r.end
        r.cursor = max(end, min(r.end, (next / 86400).rounded(.down) * 86400))
      }
      r.batches = result.batches
      EngineState.saveRange(r)
    }
    if !r.statsDone {
      for hk in r.statsTypes {
        guard let t = byHk[hk], let type = HKQuantityType.quantityType(forIdentifier: HKQuantityTypeIdentifier(rawValue: hk)) else { continue }
        let unit = HKUnit(from: t.unit)
        guard type.is(compatibleWith: unit) else { continue }
        let start = (r.start / EngineLimits.bucket).rounded(.down) * EngineLimits.bucket
        var s = start, left = Int(((r.end - start) / EngineLimits.bucket).rounded(.up))
        let recent = Date().timeIntervalSince1970 - 90 * 86400
        while left > 0 {
          if outOfTime { result.stop = "budget"; return }
          let n = min(left, EngineLimits.buckets)
          guard let values = try? await EngineHK.buckets(engine.store, type, unit: unit, start: s, count: n) else { break }
          // empty spans only where the server may hold something to correct (the last 90 days)
          if values.contains(where: { $0 > 0 }) || s + Double(n) * EngineLimits.bucket > recent {
            let p = payload(hk)
            p.phase = "range"
            p.stats.append(["type": hk, "unit": t.unit, "bucket_s": Int(EngineLimits.bucket), "start": Int(s), "values": values])
            await flushIfFull(range: rangeField)
            if result.stop != nil { return }
          }
          s += Double(n) * EngineLimits.bucket
          left -= n
        }
      }
      await flush(range: rangeField)
      if result.stop != nil { return }
      r.statsDone = true
      EngineState.saveRange(r)
    }
    // every page went: range_done (the server drops what was not sent again)
    let body: [String: Any] = ["batch_id": UUID().uuidString.lowercased(), "device": "iPhone", "app_version": config.appVersion,
                               "phase": "range", "range_done": r.id]
    switch await upload(body, history: true) {
    case .ok:
      EngineState.saveRange(nil)
      EngineLog.add("range done: \(r.batches) batches")
    case .refused, .rejected:
      EngineState.saveRange(nil)   // the server no longer knows it: nothing to finish
    default:
      break
    }
  }

  /// The start of the oldest sample of [types] at or after [t]; nil when there is none.
  private func nextStart(after t: Double, types: [EngineTypeSpec]) async -> Double? {
    let pred = HKQuery.predicateForSamples(withStart: Date(timeIntervalSince1970: t), end: nil, options: [.strictStartDate])
    var best: Double?
    for spec in types {
      guard let type = EngineHK.sampleType(spec),
            let first = try? await EngineHK.samples(engine.store, type, predicate: pred, limit: 1, newestFirst: false).first
      else { continue }
      let v = first.startDate.timeIntervalSince1970
      best = min(best ?? v, v)
    }
    return best
  }

  // MARK: batches

  private func payload(_ hk: String) -> EnginePayload {
    if let p = payloads.last(where: { $0.hk == hk }) { return p }
    let p = EnginePayload(hk: hk)
    payloads.append(p)
    return p
  }

  private func flushIfFull(range: [String: Any]? = nil) async {
    let items = payloads.reduce(0) { $0 + $1.items }
    if items >= EngineLimits.batchItems || sources.list.count >= EngineLimits.sources
      || payloads.reduce(0, { $0 + $1.workouts.count }) >= EngineLimits.workouts {
      await flush(range: range)
    }
  }

  /// Sends what was read (when anything was); commits anchors and progress after the 200.
  private func flush(range: [String: Any]? = nil) async {
    let all = payloads
    payloads = []
    guard !all.isEmpty else { return }
    let data = all.filter(\.hasData)
    if data.isEmpty {
      // nothing new: no request (contract §3.1.8); empty anchored pages still move the anchor
      all.forEach(apply)
      return
    }
    all.filter { !$0.hasData }.forEach(apply)
    await send(data, range: range)
  }

  private func body(_ list: [EnginePayload], range: [String: Any]?, runs: [EngineRun]) -> [String: Any] {
    var samples: [[String: Any]] = [], categories: [[String: Any]] = [], workouts: [[String: Any]] = []
    var stats: [[String: Any]] = [], deleted: [String] = []
    for p in list {
      let g = p.cols.groups(type: p.hk, unit: p.unit)
      if p.field == "categories" { categories += g } else { samples += g }
      workouts += p.workouts
      stats += p.stats
      deleted += p.deleted
    }
    let phase = range != nil ? "range" : (list.contains { $0.phase == "backfill" } ? "backfill"
      : (list.allSatisfy { $0.phase == "recent" } ? "recent" : "incremental"))
    var b: [String: Any] = ["batch_id": UUID().uuidString.lowercased(), "device": "iPhone", "app_version": config.appVersion,
                            "phase": phase, "tz": TimeZone.current.identifier, "sources": sources.list]
    if !samples.isEmpty { b["samples"] = samples }
    if !categories.isEmpty { b["categories"] = categories }
    if !workouts.isEmpty { b["workouts"] = workouts }
    if !stats.isEmpty { b["stats"] = stats }
    if !deleted.isEmpty { b["deleted"] = Array(Set(deleted)) }
    if let range { b["range"] = range }
    if !runs.isEmpty { b["runs"] = runs.map(\.map) }
    return b
  }

  private func send(_ list: [EnginePayload], range: [String: Any]?) async {
    let runs = Array(EngineState.pendingRuns().prefix(20))
    let b = body(list, range: range, runs: runs)
    let history = range != nil || (b["phase"] as? String) == "backfill"
    guard let json = try? JSONSerialization.data(withJSONObject: b), let gz = Gzip.compress(json) else {
      list.forEach { skip($0.hk, "encode") }
      return
    }
    if gz.count > EngineLimits.maxBytes && list.count > 1 {
      let half = list.count / 2
      await send(Array(list[..<half]), range: range)
      if result.stop == nil { await send(Array(list[half...]), range: range) }
      return
    }
    if gz.count > EngineLimits.maxBytes {
      tooLarge(list[0])
      return
    }
    let outcome = await upload(gz: gz, history: history)
    result.batches += 1
    result.bytes += gz.count
    switch outcome {
    case .ok(let answer):
      list.forEach(apply)
      EngineState.dropRuns(Set(runs.map(\.id)))
      result.samples += list.reduce(0) { $0 + $1.items }
      if let st = answer["status"] as? [String: Any], let e = st["engine"] as? [String: Any] { takeEngine(e) }
    case .refused(let detail):
      if detail == "type_paused" {
        await refreshInfo()
        let ok = Set(config.accepted)
        let keep = list.filter { ok.contains($0.hk) }
        list.filter { !ok.contains($0.hk) }.forEach { skip($0.hk, "type_paused", error: false) }
        if !keep.isEmpty && keep.count < list.count { await send(keep, range: range) } else { keep.forEach { skip($0.hk, "type_paused", error: false) } }
      } else {
        if detail == "engine_disabled" { await refreshInfo() }
        result.stop = detail ?? "refused"   // consent_required: the page shows the consent; nothing moves
      }
    case .rejected:
      // an app bug for some type (contract §4): each type alone, the bad one stays at its anchor
      if list.count > 1 {
        for p in list where result.stop == nil { await send([p], range: range) }
      } else {
        skip(list[0].hk, "contract")
      }
    case .tooLarge:
      if list.count > 1 {
        let half = list.count / 2
        await send(Array(list[..<half]), range: range)
        if result.stop == nil { await send(Array(list[half...]), range: range) }
      } else {
        tooLarge(list[0])
      }
    case .rateLimited(let wait):
      result.stop = "rate_limited"
      result.retryAfter = wait
      EngineState.setNotBefore(Date().timeIntervalSince1970 + wait)
    case .unauthorized:
      result.stop = "unauthorized"
      EngineState.setNotBefore(Date().timeIntervalSince1970 + 15 * 60)   // no retry loop (contract §4)
    case .forbidden(let d):
      result.stop = d ?? "forbidden"
      EngineState.setNotBefore(Date().timeIntervalSince1970 + 15 * 60)
    case .failed:
      result.stop = "failed"
    case .offline:
      result.stop = "offline"
    case .cancelled:
      result.stop = "expired"
    }
  }

  private func apply(_ p: EnginePayload) {
    for (k, a) in p.commits { engine.commit(k, a) }
    if p.markRecent || p.markBackfilled || p.markStats {
      EngineState.update { s in
        if p.markRecent { s.recent.insert(p.hk) }
        if p.markBackfilled { s.backfilled.insert(p.hk) }
        if p.markStats { s.backfilled.insert("stats:" + p.hk) }
      }
      progress = EngineState.progress()
    }
    if p.hasData {
      result.uploaded.insert(p.hk)
      EngineState.updateType(p.hk) { s in s.okAt = Date().timeIntervalSince1970; s.error = nil }
    }
  }

  private func tooLarge(_ p: EnginePayload) {
    let next = max(10, (states[p.hk]?.limit ?? 2000) / 2)
    EngineState.updateType(p.hk) { s in s.limit = next }
    skip(p.hk, "too_large")
  }

  private func skip(_ hk: String, _ why: String, error: Bool = true) {
    result.skipped[hk] = why
    payloads.removeAll { $0.hk == hk }
    if error { EngineState.updateType(hk) { s in s.error = why; s.errorAt = Date().timeIntervalSince1970 } }
  }

  private func hkFailed(_ hk: String, _ e: EngineHKError) {
    switch e {
    case .locked: result.stop = "locked"; payloads.removeAll { $0.hk == hk }
    case .notAsked: skip(hk, "not_asked")
    case .other(let code): skip(hk, code)
    }
  }

  private func takeEngine(_ e: [String: Any]) {
    config.apply(engine: e)
    EngineState.save(config)
    result.mode = config.mode
  }

  private func refreshInfo() async {
    guard let url = endpoint("/v1/ingest/healthkit") else { return }
    var req = URLRequest(url: url)
    req.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
    req.setValue("application/json", forHTTPHeaderField: "Accept")
    req.setValue(config.appVersion, forHTTPHeaderField: "X-App-Version")
    guard let (data, res) = try? await session.data(for: req), (res as? HTTPURLResponse)?.statusCode == 200,
          let j = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any], let e = j["engine"] as? [String: Any]
    else { return }
    takeEngine(e)
  }

  private func endpoint(_ path: String) -> URL? {
    guard let base = URL(string: config.server), var c = URLComponents(url: base, resolvingAgainstBaseURL: false) else { return nil }
    c.path = path
    c.query = nil
    c.fragment = nil
    return c.url
  }

  private func upload(_ body: [String: Any], history: Bool) async -> EngineUpload {
    guard let json = try? JSONSerialization.data(withJSONObject: body), let gz = Gzip.compress(json) else { return .rejected("encode") }
    result.batches += 1
    result.bytes += gz.count
    return await upload(gz: gz, history: history)
  }

  /// POST with retries of the same batch (same bytes, same batch_id): network errors / 5xx after 2, 4, 8 s, a short
  /// 429 in the foreground; never past the round's deadline.
  private func upload(gz: Data, history: Bool) async -> EngineUpload {
    var attempt = 0
    while true {
      let r = await post(gz, history: history)
      let wait: Double
      switch r {
      case .offline, .failed: wait = [2.0, 4.0, 8.0][min(attempt, 2)] + Double.random(in: 0...0.5)
      case .rateLimited(let s) where !background && s <= 15: wait = s + Double.random(in: 0...0.5)
      default: return r
      }
      attempt += 1
      if attempt > 3 || cancelled || Date().addingTimeInterval(wait + (background ? 8 : 0)) >= deadline { return r }
      try? await Task.sleep(nanoseconds: UInt64(wait * 1_000_000_000))
    }
  }

  private func post(_ gz: Data, history: Bool) async -> EngineUpload {
    guard let url = endpoint("/v1/ingest/healthkit") else { return .failed(0) }
    var req = URLRequest(url: url)
    req.httpMethod = "POST"
    req.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
    req.setValue("application/json", forHTTPHeaderField: "Content-Type")
    req.setValue("gzip", forHTTPHeaderField: "Content-Encoding")
    req.setValue("application/json", forHTTPHeaderField: "Accept")
    req.setValue(config.appVersion, forHTTPHeaderField: "X-App-Version")
    // history over Wi-Fi only unless allowed; increments on any network (contract §5)
    req.allowsExpensiveNetworkAccess = !history || config.cellularHistory
    req.allowsConstrainedNetworkAccess = !history
    do {
      let (data, res) = try await session.upload(for: req, from: gz)
      let http = res as? HTTPURLResponse
      let code = http?.statusCode ?? 0
      let j = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any] ?? [:]
      let detail = j["detail"] as? String
      switch code {
      case 200: return .ok(j)
      case 401: return .unauthorized
      case 403: return .forbidden(detail)
      case 409: return .refused(detail)
      case 413: return .tooLarge
      case 400, 415, 422: return .rejected(detail)
      case 429: return .rateLimited(Double(http?.value(forHTTPHeaderField: "Retry-After") ?? "") ?? 60)
      default: return .failed(code)
      }
    } catch {
      if Task.isCancelled || (error as? URLError)?.code == .cancelled { return .cancelled }
      return .offline
    }
  }

  // MARK: end of the round: runs[]

  /// After a round that uploaded, its runs[] entry (and older pending ones) goes at once; quiet rounds keep theirs for a
  /// later batch unless many are waiting or they are old.
  private func reportRuns() async {
    guard result.stop == nil || result.stop == "budget" else { return }
    guard result.batches > 0 || SyncEngine.shouldSendRunsAlone(), !cancelled else { return }
    let runs = Array(EngineState.pendingRuns().prefix(40))
    guard !runs.isEmpty else { return }
    let body: [String: Any] = ["batch_id": UUID().uuidString.lowercased(), "device": "iPhone",
                               "app_version": config.appVersion, "runs": runs.map(\.map)]
    if case .ok = await upload(body, history: false) { EngineState.dropRuns(Set(runs.map(\.id))) }
  }

  private func finish() {
    let resultCode: String
    switch result.stop {
    case nil: resultCode = result.batches == 0 && result.skipped.isEmpty ? "nothing" : (result.skipped.isEmpty ? "ok" : "partial")
    case "budget": resultCode = "partial"
    case "locked": resultCode = "locked"
    case "offline": resultCode = "offline"
    case "expired": resultCode = "expired"
    default: resultCode = "error"
    }
    let contract = result.skipped.values.contains("contract")
    let error = result.stop.flatMap { $0 == "budget" ? nil : $0 } ?? (contract ? "contract" : nil)
    let run = EngineRun(id: UUID().uuidString.lowercased(), trigger: trigger, result: resultCode, error: error,
                        started: t0.timeIntervalSince1970, durationMs: Int(Date().timeIntervalSince(t0) * 1000),
                        batches: result.batches, bytes: result.bytes, samples: result.samples)
    EngineState.addRun(run)
    EngineLog.add("\(trigger) \(resultCode)\(error.map { " (\($0))" } ?? ""): \(result.samples) items, \(result.uploaded.count) types, "
                  + "\(result.batches) batches\(result.skipped.isEmpty ? "" : ", skipped \(Set(result.skipped.values).sorted().joined(separator: "/"))")")
    result.mode = result.mode ?? config.mode
  }
}

extension SyncEngine {
  /// After a round with uploads the pending runs[] went along; rounds with nothing to send keep theirs until a later
  /// batch, or send them alone once there are many or they are old (sync_runs measures background reliability).
  static func shouldSendRunsAlone() -> Bool {
    let runs = EngineState.pendingRuns()
    guard let first = runs.first else { return false }
    return runs.count >= 12 || Date().timeIntervalSince1970 - first.started > 6 * 3600
  }
}

extension EngineRoundResult {
  init(stop: String) {
    self.init()
    self.stop = stop
  }
}

extension EngineTypeSpec {
  /// The first crawl of this type is done (cumulative-only types: their first buckets went).
  func firstSyncDone(_ p: EngineProgress) -> Bool {
    kind == "quantity" && !raw ? p.backfilled.contains("stats:" + hk) : p.backfilled.contains(hk)
  }
}
