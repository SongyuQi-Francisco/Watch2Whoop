import Foundation
import HealthKit
import UIKit

/// The host side of the app's own HealthKit sync (#219), ported from the Flutter app's Dart glue (lib/src/sync_engine.dart,
/// raw_sync.dart, backfill.dart, resync_flow.dart) for #340 N8. 1.0.9 has no Open Wearables SDK: every upload goes
/// through the engine (mode `app`, contract section 7.1).
/// - SyncEngine.swift does the rounds itself (observers, BGAppRefresh / BGProcessing, anchors after 200, Wi-Fi-only
///   history); this hands it the server, the token, the registry and the server's engine block, and starts foreground
///   rounds (on open, 「立即同步」).
/// - RawUploadPass: beat-to-beat series, ECGs and drinks (RawSync.swift) before each foreground round.
/// - Backfill.swift: the history backfill (90 days / all), background URLSession, resumable; a slice after each round.
/// - Server jobs (`/v1/ingest/resync`): a forced re-sync of a range (engine range re-sync) or the history backfill; their
///   phone part is stepped here and reported until `phone_done`.
/// One per process (the engine is process-wide); `attach` binds it to the signed-in account.
@MainActor
final class HealthSyncService: ObservableObject {
  static let shared = HealthSyncService()

  @Published private(set) var attached = false
  @Published private(set) var info: IngestInfo?
  /// unauthorized / offline from the last server call, nil when it answered.
  @Published private(set) var serverProblem: String?
  @Published private(set) var engine = EngineSnapshot()
  @Published private(set) var backfill = BackfillState()
  @Published private(set) var server: ServerSyncStatus?
  /// The server's data status per data type (why a type is old: not recorded / not delivered / no permission).
  @Published private(set) var dataStatus: ServerDataStatus?
  @Published private(set) var phoneLatest: [String: Date] = [:]
  @Published private(set) var permission: SyncDiagnosis.Permission = .unknown
  @Published private(set) var running = false
  @Published private(set) var lastSkipped: Set<String> = []
  @Published private(set) var rawStop: String?
  @Published private(set) var rawReadProblem: String?
  @Published private(set) var lastOk: Date?
  @Published private(set) var job: ResyncJob?
  @Published private(set) var jobPercent = 0
  @Published var consentRequired = false

  /// After each round: the account's data layer pulls (session.sync.refresh) and, with N2's sync, the phone scores the
  /// changed days and uploads them now that the round's samples are on the server (#340 N2b: also after a round without
  /// uploads, since a scoring run may have waited for this one).
  var onRoundDone: (() async -> Void)?
  /// Beat-to-beat readings the server took: into the account's local tables (#340 N2b; items as RawSync reads them).
  var onHeartbeats: (([[String: Any]], [String]) -> Void)?

  let registry: [HealthType]
  let settings: SyncSettings
  private var api: APIClient?
  private var server_: String = ""
  private var token: String = ""
  private var lastOpenRound: Date?
  private var stepping = false
  private let raw = NativeRawSource()

  init(registry: [HealthType] = HealthRegistry.bundled, settings: SyncSettings = SyncSettings()) {
    self.registry = registry
    self.settings = settings
    self.lastOk = settings.lastOk
    self.job = settings.resync
  }

  static var appVersion: String {
    let v = Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "0"
    let b = Bundle.main.object(forInfoDictionaryKey: "CFBundleVersion") as? String ?? "0"
    return "\(v)+\(b)"
  }

  var engineTypes: [HealthType] { registry.filter(\.isEngine) }

  /// Everything this phone read from HealthKit is on the server (#340 N2b: the phone uploads the scores it computed only
  /// then, so the server's spot check sees the same inputs): no round running, the last one complete (not stopped by the
  /// time budget, offline …) and the first sync done. Nothing to wait for when the engine is not active for this user.
  var uploadedEverything: Bool {
    guard attached, engine.active else { return true }
    return !running && !engine.running && engine.lastStop == nil && engine.firstSyncDone
  }

  /// From when the server has all of this phone's HealthKit data (#340 N9): the first sync's window start, or as far
  /// back as the history backfill got (everything in [cursor, to) is on the server). The phone scores from raw samples
  /// only from there (`HealthKitScoringReader`); older days come in as the server's stored rows (the seeded run).
  var uploadedSince: Date {
    let first = settings.backfillTo()
    if !backfill.isEmpty, backfill.cursor > 0 { return Date(timeIntervalSince1970: min(first, backfill.cursor)) }
    return Date(timeIntervalSince1970: first)
  }

  var diagnosis: SyncDiagnosis {
    var i = SyncDiagnosis.Input()
    i.permission = permission
    i.info = info
    i.serverProblem = serverProblem
    i.engine = engine
    i.server = server
    i.rows = rows
    i.backfill = backfill
    i.lastSkipped = lastSkipped
    i.rawStop = rawStop
    i.rawReadProblem = rawReadProblem
    i.backgroundRefresh = UIApplication.shared.backgroundRefreshStatus == .available
    i.lowPower = ProcessInfo.processInfo.isLowPowerModeEnabled
    i.consentRequired = consentRequired
    i.running = running || engine.running
    i.lastOk = lastOk
    i.result = server == nil ? nil : result
    return SyncDiagnosis.of(i)
  }

  /// The one post-sync answer (SyncResult): 「已同步」 or type → reason → action.
  var result: SyncResult {
    var i = SyncResult.Input()
    i.rows = rows
    i.status = dataStatus
    i.lastStop = engine.lastStop
    i.backgroundRefresh = UIApplication.shared.backgroundRefreshStatus == .available
    i.lastOk = lastOk
    return SyncResult.of(i)
  }

  /// Phone vs server per type. A phone sample older than what the sync reads (`uploadedSince`) is "older than the synced
  /// range", and one of a day the server has through another way in (`latest_day`: an export, Open Wearables) is synced.
  var rows: [TypeRow] {
    TypeRow.rows(registry, serverTypes: info?.types ?? [], phone: phoneLatest, server: server?.latest ?? [:],
                 serverDay: server?.latestDay ?? [:], uploadedSince: engine.active ? uploadedSince : nil)
  }

  // MARK: - account

  /// Binds the sync to the signed-in account. A different account than the one the engine state belongs to starts from
  /// scratch; state that came from 1.0.8 (no account recorded yet) is kept, so the anchors carry on after the upgrade.
  func attach(api: APIClient, server: String, token: String, accountKey: String) async {
    if let previous = settings.account, previous != accountKey { await resetPhoneState() }
    settings.account = accountKey
    settings.pinFirstWindow()   // before the engine's first read (N9: no gap between the first sync and the backfill)
    self.api = api
    self.server_ = server
    self.token = token
    attached = true
    await refreshPermission()
    await reloadDetails(server: false)
  }

  /// Sign-out / account deleted: stop and forget everything of this account on the phone (the server keeps its data).
  func detach() async {
    await resetPhoneState()
    settings.clear()
    api = nil
    token = ""
    attached = false
    info = nil
    server = nil
    dataStatus = nil
    engine = EngineSnapshot()
    backfill = BackfillState()
    job = nil
    lastOk = nil
  }

  private func resetPhoneState() async {
    await HealthSyncBridge.Engine.reset()
    _ = await HealthSyncBridge.History.cancel()
    await raw.reset()
    await raw.reset(anchorSet: "resync")
    settings.resync = nil
  }

  // MARK: - permission (one sheet for every type, #344)

  func refreshPermission() async {
    guard HKHealthStore.isHealthDataAvailable() else { permission = .unavailable; return }
    let s = (try? await HealthSyncBridge.raw("rawAuthStatus", ["types": HealthRegistry.permissionTypes(registry)])) as? String
    switch s {
    case "unnecessary": permission = .asked
    case "shouldRequest": permission = .notAsked
    default: permission = .unknown
    }
  }

  /// The one Health sheet for every type the app reads. true = iOS no longer needs to ask (what was chosen stays unknown).
  @discardableResult
  func requestPermission() async -> Bool {
    guard HKHealthStore.isHealthDataAvailable() else { permission = .unavailable; return false }
    let out = (try? await HealthSyncBridge.raw("rawRequestAuth", ["types": HealthRegistry.permissionTypes(registry)])) as? [String: Any]
    await refreshPermission()
    return out?["asked"] as? Bool ?? false
  }

  // MARK: - server

  /// GET /v1/ingest/healthkit: the engine block; hands it to the native engine (which starts or stops its observers).
  @discardableResult
  func refreshInfo() async -> IngestInfo? {
    guard let api else { return nil }
    do {
      let j = try await api.json(.get(Endpoint.ingest))
      let i = IngestInfo(j)
      info = i
      serverProblem = nil
      _ = await HealthSyncBridge.Engine.configure(engineArgs(i))
      return i
    } catch APIError.unauthorized {
      serverProblem = "unauthorized"
    } catch APIError.conflict(let d) {
      if d == "consent_required" { consentRequired = true }
      serverProblem = nil
    } catch let e as APIError {
      if case .network = e { serverProblem = "offline" } else { serverProblem = "offline" }
    } catch {
      serverProblem = "offline"
    }
    return nil
  }

  private func engineArgs(_ i: IngestInfo) -> [String: Any] {
    ["server": server_, "token": token, "daysBack": SyncSettings.firstSyncDays, "appVersion": Self.appVersion,
     "engine": i.engineRaw.isEmpty ? ["enabled": i.engineEnabled, "mode": i.mode, "accepts": Array(i.accepts), "types": Array(i.types)]
       : i.engineRaw,
     "registry": engineTypes.map(\.engineMap)]
  }

  // MARK: - rounds

  /// Cold start and every return to the foreground (the phone is unlocked then, so Health can be read): one round, at
  /// most every 3 minutes unless forced, never two at once.
  func syncOnOpen(force: Bool = false) async {
    guard attached, !running else { return }
    if !force, let t = lastOpenRound, Date().timeIntervalSince(t) < SyncSettings.openGap { return }
    lastOpenRound = Date()
    await round(trigger: "foreground")
  }

  /// 「立即同步」: a round now. Returns a short line for the screen.
  func syncNow() async -> String? {
    guard attached, !running else { return nil }
    lastOpenRound = Date()
    return await round(trigger: "foreground")
  }

  @discardableResult
  private func round(trigger: String) async -> String? {
    running = true
    defer { running = false }
    await refreshPermission()
    guard let i = await refreshInfo() else {
      await reloadDetails(server: false)
      return serverProblem == "unauthorized" ? tr("登录失效", "Signed out") : tr("连不上服务器", "Can't reach the server")
    }
    var message: String?
    // beat-to-beat series, ECGs, drinks (RawSync.swift)
    if permission != .notAsked, permission != .unavailable {
      let days = settings.daysBack
      if SyncSettings.needsAnchorReset(previous: settings.rawRange, next: days) { await raw.reset() }
      settings.rawRange = days
      let r = await rawPass(daysBack: days, anchorSet: "", water: i.waterEnabled)
      rawStop = r.stop == "budget" ? nil : r.stop
      rawReadProblem = r.readProblem
      if r.stop == "consent" { consentRequired = true }
    }
    // the engine's round (raw samples, sleep stages, workouts, 15-minute buckets, deletions)
    if i.engineActive {
      let m = await HealthSyncBridge.Engine.run(engineArgs(i), trigger: trigger)
      let stop = m["stop"] as? String
      lastSkipped = Set(((m["skipped"] as? [String: Any]) ?? [:]).values.compactMap { $0 as? String })
      let samples = (m["samples"] as? NSNumber)?.intValue ?? 0
      let types = (m["uploaded"] as? [Any])?.count ?? 0
      if stop == "consent_required" { consentRequired = true }
      // nothing new: no separate line; the sync result says 「已同步」 or which types are not (the owner's report
      // 2026-10-31: 「没有新数据」 next to 「N 类没同步」)
      if (stop == nil || stop == "budget") && samples > 0 {
        message = tr("已上传 \(samples) 条（\(types) 类）", "Uploaded \(samples) items (\(types) types)")
      }
      if stop == nil || stop == "budget" || stop == "busy" {
        lastOk = Date()
        settings.lastOk = lastOk
      }
      // new data first, then a slice of the history
      let history = BackfillState(await HealthSyncBridge.History.status())
      if history.active { _ = await HealthSyncBridge.History.pump(seconds: SyncSettings.pumpSeconds) }
    }
    await stepJob()
    await reloadDetails(server: true)
    await reportDevice()
    if let onRoundDone { await onRoundDone() }
    return message
  }

  private func rawPass(daysBack: Int, anchorSet: String, water: Bool) async -> RawPassResult {
    guard let api else { return RawPassResult() }
    let beats = onHeartbeats
    let pass = RawUploadPass(source: raw, upload: { body in await Self.upload(api, body) }, appVersion: Self.appVersion,
                             stored: { kind, page in if kind == "heartbeat" { beats?(page.items, page.deleted) } })
    return await pass.run(daysBack: daysBack, waterEnabled: water, anchorSet: anchorSet)
  }

  private static func upload(_ api: APIClient, _ body: [String: Any]) async -> RawUploadOutcome {
    guard let data = try? JSONSerialization.data(withJSONObject: body) else { return .failed }
    do {
      let d = try await api.data(APIRequest(method: .post, path: Endpoint.ingest, body: data, idempotent: false, timeout: 45))
      return .ok((try? JSONSerialization.jsonObject(with: d) as? [String: Any]) ?? [:])
    } catch let e as APIError {
      switch e {
      case .unauthorized: return .unauthorized
      case .conflict: return .consent
      case .http(413, _): return .tooLarge
      case .network: return .offline
      default: return .failed
      }
    } catch {
      return .offline
    }
  }

  func setCellularHistory(_ on: Bool) async {
    SyncEngine.shared.setCellularHistory(on)
    try? await Task.sleep(nanoseconds: 200_000_000)
    await reloadDetails(server: false)
  }

  // MARK: - history backfill and forced re-sync (server jobs, /v1/ingest/resync)

  /// 「补全历史」: the server's job (it re-imports and recomputes once the phone part is done), then the native backfill of
  /// [days] (0 = all history) back from the first-sync window. Resumable, continues in the background. nil = started.
  func startHistory(days: Int) async -> String? {
    guard attached, let api else { return tr("还没有登录", "Not signed in") }
    if permission == .notAsked { await requestPermission() }
    guard let i = await refreshInfo() else { return tr("连不上服务器，稍后再试", "Can't reach the server; try again later") }
    guard i.engineActive else { return tr("服务器还没为你打开 app 同步", "Your server has not turned on app sync for you") }
    do {
      let r = try await api.json(try .json(.post, Endpoint.historyBackfill, ["days": days == 0 ? nil : days] as [String: Int?]))
      if r["accepted"] as? Bool == false, let why = refusal(r) { return why }
    } catch APIError.notFound {
      // an older server: the phone part alone
    } catch let e as APIError {
      return e.userMessage
    } catch {
      return String(describing: error)
    }
    if SyncSettings.needsAnchorReset(previous: settings.daysBack, next: days) { settings.daysBack = days }
    let types = engineTypes.filter { i.types.contains($0.hk) && ($0.minIos == 0 || ProcessInfo.processInfo.operatingSystemVersion.majorVersion >= $0.minIos) }
    let started = await HealthSyncBridge.History.start([
      "server": server_, "token": token, "days": days, "to": settings.backfillTo(), "powerOnly": false,
      "appVersion": Self.appVersion, "types": types.map(\.backfillMap),
    ])
    guard started != nil else { return tr("没有能补的类型", "Nothing to fill in") }
    await raw.reset(anchorSet: "resync")
    let j = ResyncJob(days: days == 0 ? nil : days, startedAt: Date(), history: true)
    settings.resync = j
    job = j
    await report(percent: 1, done: false)
    Task { await stepJob() }
    await reloadDetails(server: false)
    return nil
  }

  /// 「立即重新同步」 of the last [days] days (nil = all): the server's job, then the engine's range re-sync (it also drops
  /// what the phone no longer has) and RawSync with its own anchor set. nil = started.
  func startResync(days: Int?) async -> String? {
    guard attached, let api else { return tr("还没有登录", "Not signed in") }
    guard let i = await refreshInfo() else { return tr("连不上服务器，稍后再试", "Can't reach the server; try again later") }
    guard i.engineActive else { return tr("服务器还没为你打开 app 同步", "Your server has not turned on app sync for you") }
    do {
      let r = try await api.json(try .json(.post, Endpoint.ingestResync, ["days": days] as [String: Int?]))
      if r["accepted"] as? Bool != true { return refusal(r) ?? tr("现在不能重新同步", "Re-sync is not possible right now") }
    } catch APIError.notFound {
      return tr("这台服务器还不支持重新同步", "This server cannot re-sync yet")
    } catch let e as APIError {
      return e.userMessage
    } catch {
      return String(describing: error)
    }
    guard await HealthSyncBridge.Engine.startRange(days: days ?? 0) != nil else {
      return tr("同步引擎没开，先点「立即同步」", "The sync engine is off; tap Sync now first")
    }
    await raw.reset(anchorSet: "resync")
    let j = ResyncJob(days: days, startedAt: Date(), history: false)
    settings.resync = j
    job = j
    Task { await stepJob() }
    await reloadDetails(server: false)
    return nil
  }

  func cancelJob() async {
    if let api { _ = try? await api.data(APIRequest(method: .delete, path: Endpoint.ingestResync)) }
    if job?.history == false { SyncEngine.shared.cancelRange() }
    settings.resync = nil
    job = nil
    await reloadDetails(server: false)
  }

  /// One step of a running job's phone part: a RawSync pass with the re-sync anchors, the engine's range / the backfill
  /// progress, a progress report; when both are done, `phone_done`.
  func stepJob() async {
    guard var j = settings.resync, !stepping, attached else { return }
    stepping = true
    defer { stepping = false }
    if !j.rawDone {
      let r = await rawPass(daysBack: j.days ?? 0, anchorSet: "resync", water: info?.waterEnabled ?? false)
      if r.stop == nil || r.stop == "consent" { j.rawDone = true }
    }
    var enginePercent = 100
    if !j.engineDone {
      if j.history {
        let b = BackfillState(await HealthSyncBridge.History.status())
        if b.done || (!b.active && b.error != nil) || b.isEmpty { j.engineDone = true } else { enginePercent = b.percent }
      } else {
        let s = EngineSnapshot(await HealthSyncBridge.Engine.status())
        if let p = s.rangePercent { enginePercent = p } else { j.engineDone = true }   // gone = range_done went
      }
    }
    let done = j.rawDone && j.engineDone
    let percent = done ? 100 : max(1, min(99, ((j.rawDone ? 100 : 0) + enginePercent) / 2))
    jobPercent = percent
    if await report(percent: percent, done: done), done {
      settings.resync = nil
      job = nil
      return
    }
    settings.resync = j
    job = j
  }

  @discardableResult
  private func report(percent: Int, done: Bool) async -> Bool {
    guard let api else { return false }
    var body: [String: Any] = ["phone_percent": percent]
    if done { body["phone_done"] = true }
    guard let data = try? JSONSerialization.data(withJSONObject: body) else { return false }
    return (try? await api.data(APIRequest(method: .put, path: Endpoint.ingestResync, body: data))) != nil
  }

  private func refusal(_ r: [String: Any]) -> String? {
    switch r["reason"] as? String {
    case "busy": return tr("已经有一个在进行，等它完成", "One is already running; wait for it to finish")
    case "throttled": return tr("刚做过，稍后再试", "Done recently; try again later")
    case "consent": return tr("确认同意书后才能补", "Accept the consent first")
    case let x?: return x
    case nil: return nil
    }
  }

  // MARK: - status

  /// The engine's and the backfill's state; with [server] also the server's view and the newest sample per type on the phone.
  func reloadDetails(server withServer: Bool = true) async {
    engine = EngineSnapshot(await HealthSyncBridge.Engine.status())
    backfill = BackfillState(await HealthSyncBridge.History.status())
    guard withServer, let api else { return }
    let types = engineTypes.map(\.hk)
    if let m = (try? await HealthSyncBridge.raw("healthLatest", ["types": types])) as? [String: Any] {
      phoneLatest = m.compactMapValues { ($0 as? String).flatMap(ISO8601.parse) }
    }
    if let j = try? await api.json(.get(Endpoint.ingestSyncStatus)) { server = ServerSyncStatus(j) }
    if let j = try? await api.json(.get(Endpoint.dataStatus)) { dataStatus = ServerDataStatus(j) }
  }

  /// PUT /v1/ingest/sync-device: what the phone knows about its side (the admin's sync health view). Best effort.
  private func reportDevice() async {
    guard let api else { return }
    let bg: String
    switch UIApplication.shared.backgroundRefreshStatus {
    case .available: bg = "available"
    case .denied: bg = "denied"
    default: bg = "restricted"
    }
    var body: [String: Any] = ["app_version": Self.appVersion, "background_refresh": bg,
                               "low_power": ProcessInfo.processInfo.isLowPowerModeEnabled,
                               "health_readable": permission == .asked,
                               "last_attempt_at": ISO8601DateFormatter().string(from: Date())]
    if let lastOk { body["last_upload_at"] = ISO8601DateFormatter().string(from: lastOk) }
    switch engine.lastStop {
    case "unauthorized", "backoff": body["last_error"] = "auth"
    case "offline": body["last_error"] = "network"
    case "locked": body["last_error"] = "locked"
    case nil, "budget", "busy", "off": break
    default: body["last_error"] = "other"
    }
    guard let data = try? JSONSerialization.data(withJSONObject: body) else { return }
    _ = try? await api.data(APIRequest(method: .put, path: Endpoint.ingestSyncDevice, body: data))
  }
}
