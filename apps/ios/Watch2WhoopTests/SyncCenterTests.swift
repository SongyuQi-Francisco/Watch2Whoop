import XCTest
@testable import Watch2Whoop

/// N8 sync center: the registry, the parsed engine / server state, "not recorded / not synced", the plain-words diagnosis,
/// the RawSync upload pass and the settings taken over from the Flutter app.
final class SyncCenterTests: XCTestCase {
  // MARK: - registry and rows

  func testBundledRegistryMatchesTheFlutterAsset() {
    let r = HealthRegistry.bundled
    XCTAssertGreaterThan(r.count, 40)
    XCTAssertEqual(r.first?.hk, "HKCategoryTypeIdentifierSleepAnalysis")
    XCTAssertTrue(r.contains { $0.hk == "HKQuantityTypeIdentifierHeartRate" && $0.isEngine })
    XCTAssertFalse(r.first { $0.key == "heartbeat_series" }?.isEngine ?? true)   // RawSync's
    XCTAssertTrue(HealthRegistry.permissionTypes(r).contains("HKQuantityTypeIdentifierWaistCircumference"))
  }

  func testVerdictAndRowOrder() {
    let now = Date(timeIntervalSince1970: 2_000_000_000)
    XCTAssertEqual(TypeVerdict(phone: nil, server: now), .notCollected)
    XCTAssertEqual(TypeVerdict(phone: now, server: nil), .notSynced)
    XCTAssertEqual(TypeVerdict(phone: now, server: now.addingTimeInterval(-10 * 60)), .synced)   // within the slack
    XCTAssertEqual(TypeVerdict(phone: now, server: now.addingTimeInterval(-3600)), .notSynced)
    let reg = [HealthType(hk: "A", key: "a", kind: "quantity"), HealthType(hk: "B", key: "b", kind: "category"),
               HealthType(hk: "C", key: "c", kind: "quantity"), HealthType(hk: "E", key: "e", kind: "ecg")]
    let rows = TypeRow.rows(reg, serverTypes: [], phone: ["A": now, "B": now], server: ["A": now])
    XCTAssertEqual(rows.map(\.id), ["B", "A", "C"])   // not synced, synced, not recorded; ECG is RawSync's
    XCTAssertEqual(TypeRow.rows(reg, serverTypes: ["A"], phone: [:], server: [:]).map(\.id), ["A"])
  }

  func testEngineServerAndBackfillState() {
    let e = EngineSnapshot(["mode": "app", "active": true, "running": false, "cellularHistory": true,
                            "firstSync": ["types": 10, "done": 4],
                            "types": ["A": ["backfilled": true, "okAt": 1_900_000_000.0]],
                            "range": ["percent": 42], "lastRun": ["started": 1_900_000_100.0, "result": "ok", "samples": 12],
                            "lastStop": "", "log": ["2026-10-29T08:00:00Z\tround ok"]])
    XCTAssertEqual(e.firstPercent, 40)
    XCTAssertFalse(e.firstSyncDone)
    XCTAssertEqual(e.rangePercent, 42)
    XCTAssertNil(e.lastStop)
    XCTAssertEqual(e.types["A"]?.firstDone, true)
    let s = ServerSyncStatus(["mode": "app", "types": [["type": "A", "latest_sample_at": "2026-10-29T08:00:00+00:00"]],
                              "failures": ["recovered": false, "last": ["code": "too_large", "reason": "format"]],
                              "pipeline": ["up_to_date": true, "pending_days": 0]])
    XCTAssertNotNil(s.latest["A"])
    XCTAssertEqual(s.openFailure, "too_large")
    let b = BackfillState(["active": true, "done": false, "target": 90, "percent": 30, "to": 1_800_000_000.0])
    XCTAssertEqual(b.percent, 30)
    XCTAssertFalse(b.isEmpty)
    let i = IngestInfo(["water_enabled": true, "engine": ["enabled": true, "mode": "app", "accepts": ["phase", "samples"], "types": ["A"]]])
    XCTAssertTrue(i.engineActive)
    XCTAssertFalse(IngestInfo(["engine": ["enabled": true, "mode": "ow", "accepts": ["phase"]]]).engineActive)
  }

  /// N9: the phone scores only from what the server has (a new user with more history in Health than the first sync's
  /// 14 days failed every spot check: CTL / ATL from the extra days, a recovery the server could not compute yet).
  @MainActor
  func testScoringReadsOnlyWhatTheServerHas() {
    let defaults = UserDefaults(suiteName: "n9.scoring.\(UUID().uuidString)")!
    let first = 1_800_000_000.0
    defaults.set(first, forKey: "w2w.native.backfill.to.v1")
    let service = HealthSyncService(registry: [], settings: SyncSettings(defaults: defaults))
    XCTAssertEqual(service.uploadedSince.timeIntervalSince1970, first)   // no backfill: the first sync's window
    let now = Date(timeIntervalSince1970: first + 14 * 86_400)
    XCTAssertEqual(HealthKitScoringReader.since(now: now, uploadedSince: service.uploadedSince).timeIntervalSince1970, first)
    // a backfill that went further back widens it up to the raw window of 60 days
    let window = now.addingTimeInterval(-Double(LocalRawStore.keepDays) * 86_400)
    XCTAssertEqual(HealthKitScoringReader.since(now: now, uploadedSince: Date(timeIntervalSince1970: first - 90 * 86_400)), window)
    XCTAssertEqual(HealthKitScoringReader.since(now: now, uploadedSince: nil), window)
    // only a start that moved back drops the anchors
    XCTAssertTrue(HealthKitScoringReader.needsReread(previous: first, next: first - 86_400))
    XCTAssertFalse(HealthKitScoringReader.needsReread(previous: first, next: first + 86_400))
    XCTAssertFalse(HealthKitScoringReader.needsReread(previous: first, next: first - 60))
    let b = BackfillState(["active": true, "to": first, "from": first - 90 * 86_400, "cursor": first - 30 * 86_400, "batches": 3])
    XCTAssertEqual(b.cursor, first - 30 * 86_400)
    XCTAssertEqual(b.from, first - 90 * 86_400)
  }

  /// N9: the first-sync window is pinned when the account is attached, an hour inside the engine's own start (which is
  /// `now - 14 days` to the second, not the start of that day), so the backfill's end overlaps it instead of leaving a gap.
  func testFirstWindowIsPinnedOnceAtAttach() {
    let defaults = UserDefaults(suiteName: "n9.window.\(UUID().uuidString)")!
    let s = SyncSettings(defaults: defaults)
    let now = Date(timeIntervalSince1970: 1_800_000_000)
    s.pinFirstWindow(now: now)
    XCTAssertEqual(s.backfillTo(now: now), 1_800_000_000 - 14 * 86_400 + 3600)
    s.pinFirstWindow(now: now.addingTimeInterval(86_400))   // later attaches keep it
    XCTAssertEqual(s.backfillTo(), 1_800_000_000 - 14 * 86_400 + 3600)
    let upgraded = UserDefaults(suiteName: "n9.window.\(UUID().uuidString)")!
    upgraded.set(1_700_000_000.0, forKey: "flutter.w2w.backfill.to.v1")   // a backfill 1.0.8 started keeps its window
    SyncSettings(defaults: upgraded).pinFirstWindow(now: now)
    XCTAssertEqual(SyncSettings(defaults: upgraded).backfillTo(), 1_700_000_000)
  }

  // MARK: - diagnosis

  private func activeInfo() -> IngestInfo {
    IngestInfo(["engine": ["enabled": true, "mode": "app", "accepts": ["phase"], "types": ["A"]]])
  }

  func testDiagnosisAsksForHealthAccess() {
    var i = SyncDiagnosis.Input()
    i.permission = .notAsked
    i.info = activeInfo()
    let d = SyncDiagnosis.of(i)
    XCTAssertEqual(d.overall, .attention)
    XCTAssertEqual(d.issues.first?.action, .requestPermission)
  }

  func testDiagnosisFineWithLastSync() {
    var i = SyncDiagnosis.Input()
    i.permission = .asked
    i.info = activeInfo()
    i.engine = EngineSnapshot(["mode": "app", "active": true])
    let now = Date()
    i.lastOk = now.addingTimeInterval(-300)
    let d = SyncDiagnosis.of(i, now: now)
    XCTAssertEqual(d.overall, .ok)
    XCTAssertTrue(d.issues.isEmpty)
  }

  func testDiagnosisPlainWords() {
    var i = SyncDiagnosis.Input()
    i.permission = .asked
    i.info = activeInfo()
    i.engine = EngineSnapshot(["mode": "app", "active": true, "lastStop": "locked", "cellularHistory": false])
    i.lastSkipped = ["wifi_only"]
    i.backgroundRefresh = false
    let ids = SyncDiagnosis.of(i).issues.map(\.id)
    XCTAssertEqual(ids, ["wifi"])   // a locked phone and background refresh off are in the sync result now
    XCTAssertEqual(SyncDiagnosis.of(i).overall, .ok)   // none of these blocks the sync
    i.serverProblem = "offline"
    XCTAssertEqual(SyncDiagnosis.of(i).overall, .attention)
  }

  func testDiagnosisEngineOffAndNothingReadable() {
    var i = SyncDiagnosis.Input()
    i.permission = .asked
    i.info = IngestInfo(["engine": ["enabled": false, "mode": "ow"]])
    XCTAssertEqual(SyncDiagnosis.of(i).overall, .off)
    i.info = activeInfo()
    i.rows = [TypeRow(type: HealthType(hk: "A", key: "a", kind: "quantity"), phone: nil, server: nil, verdict: .notCollected)]
    XCTAssertEqual(SyncDiagnosis.of(i).issues.first?.action, .openHealthApp)
  }

  // MARK: - one post-sync answer (the owner's report 2026-10-31)

  private static let sydney: Calendar = {
    var c = Calendar(identifier: .gregorian)
    c.timeZone = TimeZone(identifier: "Australia/Sydney")!
    return c
  }()

  /// The bug: the engine was caught up (「没有新数据」) while the comparison said the weight was 「没同步」: the newest weight
  /// on the phone was older than the first sync's 14 days, so the engine never read it and the server's engine samples had
  /// none. Now it is synced when the server has that day through another way in (an Apple Health export), and "older than
  /// the synced range" (fill in history) when it does not; only a sample inside the range and newer than the server's is
  /// "not synced".
  func testOldPhoneSampleIsNotNotSynced() {
    let now = Date(timeIntervalSince1970: 1_793_400_000)   // 2026-10-31 in Sydney
    let since = now.addingTimeInterval(-14 * 86_400)
    let oldWeight = now.addingTimeInterval(-400 * 86_400)
    let cal = Self.sydney
    // before the fix: phone sample, nothing from the engine on the server
    XCTAssertEqual(TypeVerdict(phone: oldWeight, server: nil), .notSynced)
    // the export brought that day: synced
    let day = TypeVerdict.dayString(oldWeight, cal)
    XCTAssertEqual(TypeVerdict(phone: oldWeight, server: nil, serverDay: day, uploadedSince: since, now: now, calendar: cal), .synced)
    // nothing of it on the server: older than what the sync reads
    XCTAssertEqual(TypeVerdict(phone: oldWeight, server: nil, uploadedSince: since, now: now, calendar: cal), .outsideRange)
    // inside the range and newer than the server: still not synced (a day of today never counts as the whole day)
    let recent = now.addingTimeInterval(-2 * 3600)
    XCTAssertEqual(TypeVerdict(phone: recent, server: now.addingTimeInterval(-5 * 3600), serverDay: TypeVerdict.dayString(now, cal),
                               uploadedSince: since, now: now, calendar: cal), .notSynced)
    let s = ServerSyncStatus(["types": [["type": "W", "latest_sample_at": NSNull(), "latest_day": "2025-09-26"],
                                        ["type": "H", "latest_sample_at": "2026-10-31T08:00:00+11:00", "latest_day": "2026-10-31"]]])
    XCTAssertNil(s.latest["W"])
    XCTAssertEqual(s.latestDay["W"], "2025-09-26")
    XCTAssertNotNil(s.latest["H"])
  }

  private func row(_ hk: String, _ status: String, _ verdict: TypeVerdict, phone: Date? = nil) -> TypeRow {
    TypeRow(type: HealthType(hk: hk, key: hk.lowercased(), kind: "quantity", status: status), phone: phone, server: nil,
            verdict: verdict)
  }

  func testResultIsSyncedWhenOnlyNothingNewWasRecorded() {
    let now = Date(timeIntervalSince1970: 1_793_400_000)
    let status = ServerDataStatus(["types": [
      ["key": "heart_rate", "label": "心率", "status": "ok"],
      ["key": "weight", "label": "体重", "status": "stale", "reason": "not_recorded", "action": "use_scale", "alarm": false],
      ["key": "wrist_temperature", "label": "手腕温度", "status": "stale", "reason": "not_recorded", "action": "wear_watch_sleep"],
      ["key": "ecg", "label": "心电图", "status": "never", "reason": "on_demand", "action": "record_ecg"],
      ["key": "vo2max", "label": "VO₂max", "status": "never", "reason": "unsupported"],
    ], "delivery": ["state": "ok", "action": "enable_background_refresh"]])
    XCTAssertEqual(status.delivery, "ok")
    var i = SyncResult.Input()
    i.rows = [row("HR", "heart_rate", .synced, phone: now), row("W", "weight", .synced, phone: now)]
    i.status = status
    i.lastOk = now.addingTimeInterval(-60)
    i.now = now
    i.lastStop = "locked"
    i.backgroundRefresh = false
    let r = SyncResult.of(i)
    XCTAssertTrue(r.synced)
    XCTAssertTrue(r.problems.isEmpty)
    XCTAssertEqual(r.quiet.map(\.id), ["weight", "wrist_temperature", "ecg"])
    XCTAssertEqual(r.quiet.map(\.hint), ["use_scale", "wear_watch_sleep", "record_ecg"])
    XCTAssertEqual(r.warnings.map(\.reason), [.locked, .backgroundOff])
    XCTAssertEqual(r.warnings.last?.action, .backgroundHelp)
    // the headline agrees: 已同步, never next to a type that is not
    var d = SyncDiagnosis.Input()
    d.permission = .asked
    d.info = activeInfo()
    d.engine = EngineSnapshot(["mode": "app", "active": true])
    d.lastOk = i.lastOk
    d.result = r
    XCTAssertEqual(SyncDiagnosis.of(d, now: now).overall, .ok)
    XCTAssertEqual(SyncDiagnosis.of(d, now: now).headline, tr("已同步 · 1 分钟前", "Synced · 1 min ago"))
  }

  func testResultListsTypeReasonAction() {
    let now = Date(timeIntervalSince1970: 1_793_400_000)
    var i = SyncResult.Input()
    i.rows = [row("HR", "heart_rate", .notSynced, phone: now), row("W", "weight", .outsideRange, phone: now.addingTimeInterval(-400 * 86_400)),
              row("S", "steps", .synced, phone: now)]
    i.status = ServerDataStatus(["types": [
      ["key": "weight", "label": "体重", "status": "stale", "reason": "not_delivered", "action": "open_app", "alarm": true],
      ["key": "hrv", "label": "HRV", "status": "never", "reason": "no_permission", "action": "authorize", "alarm": true],
      ["key": "sleep", "label": "睡眠", "status": "stale", "reason": "not_delivered", "action": "enable_background_refresh"],
      ["key": "steps", "label": "步数", "status": "stale", "reason": "not_delivered"],
    ]])
    i.now = now
    i.lastOk = now.addingTimeInterval(-3600)   // not just synced: tap Sync now first
    var r = SyncResult.of(i)
    XCTAssertFalse(r.synced)
    XCTAssertEqual(r.problems.map(\.reason), [.notSynced, .outsideRange, .noPermission, .notDelivered, .notDelivered])
    XCTAssertEqual(r.problems.map(\.action), [.syncNow, .fillHistory, .openHealthApp, .syncNow, .syncNow])
    XCTAssertEqual(r.historyDays, 0)   // the weight is older than 90 days: all history
    // right after a round: a type still behind needs a re-sync; the server's "not delivered" for a type the phone has
    // nothing newer of was simply not recorded
    i.lastOk = now.addingTimeInterval(-60)
    r = SyncResult.of(i)
    XCTAssertEqual(r.problems.map(\.reason), [.notSynced, .outsideRange, .noPermission])
    XCTAssertEqual(r.problems.first?.action, .resync)
    XCTAssertEqual(r.quiet.map(\.id), ["sleep", "steps"])
    var d = SyncDiagnosis.Input()
    d.permission = .asked
    d.info = activeInfo()
    d.engine = EngineSnapshot(["mode": "app", "active": true])
    d.lastOk = i.lastOk
    d.result = r
    XCTAssertEqual(SyncDiagnosis.of(d, now: now).overall, .attention)
    XCTAssertEqual(SyncDiagnosis.of(d, now: now).headline, tr("3 项没同步", "3 not synced"))
    XCTAssertFalse(SyncResult.reasonLabel(r.problems[1]).isEmpty)
    XCTAssertEqual(SyncResult.backgroundSteps.count, 4)
  }

  // MARK: - RawSync pass

  private final class FakeSource: RawPageSource {
    var pages: [String: [RawPage]] = [:]
    var failures: [String: HealthSyncBridge.Failure] = [:]
    var committed: [String: String] = [:]

    func read(kind: String, daysBack: Int, limit: Int) async throws -> RawPage {
      if let f = failures[kind] { throw f }
      guard var list = pages[kind], !list.isEmpty else { return RawPage() }
      let p = list.removeFirst()
      pages[kind] = list
      return p
    }

    func commit(kind: String, anchor: String) async { committed[kind] = anchor }
  }

  func testRawPassCommitsOnlyAfterTheServerTookIt() async {
    let src = FakeSource()
    src.pages["heartbeat"] = [RawPage(items: [["uuid": "u1"]], anchor: "a1", more: true), RawPage(items: [["uuid": "u2"]], anchor: "a2")]
    src.pages["water"] = [RawPage(items: [["uuid": "w1"]], anchor: "w")]
    var bodies: [[String: Any]] = []
    let pass = RawUploadPass(source: src, upload: { b in
      bodies.append(b)
      return .ok(["heartbeats": (b["heartbeats"] as? [Any])?.count ?? 0, "water_enabled": true, "water": (b["water"] as? [Any])?.count ?? 0])
    }, batchId: { "batch" })
    let r = await pass.run(daysBack: 14, waterEnabled: true)
    XCTAssertNil(r.stop)
    XCTAssertEqual(r.heartbeats, 2)
    XCTAssertEqual(r.water, 1)
    XCTAssertEqual(src.committed, ["heartbeat": "a2", "water": "w"])
    XCTAssertEqual(bodies.first?["batch_id"] as? String, "batch")
  }

  func testRawPass401KeepsAnchor() async {
    let src = FakeSource()
    src.pages["heartbeat"] = [RawPage(items: [["uuid": "u1"]], anchor: "a1")]
    let r = await RawUploadPass(source: src, upload: { _ in .unauthorized }).run(daysBack: 14, waterEnabled: true)
    XCTAssertEqual(r.stop, "unauthorized")
    XCTAssertTrue(src.committed.isEmpty)
  }

  func testRawPassConsentStillSendsWaterAndReportsUnaskedTypes() async {
    let src = FakeSource()
    src.pages["heartbeat"] = [RawPage(items: [["uuid": "u1"]], anchor: "a1")]
    src.pages["water"] = [RawPage(items: [["uuid": "w1"]], anchor: "w")]
    src.failures["ecg"] = HealthSyncBridge.Failure(code: "healthkit", message: "not determined", details: "5")
    let r = await RawUploadPass(source: src, upload: { b in b["heartbeats"] != nil ? .consent : .ok(["water_enabled": true, "water": 1]) })
      .run(daysBack: 14, waterEnabled: true)
    XCTAssertEqual(r.stop, "consent")
    XCTAssertEqual(src.committed, ["water": "w"])
    let off = await RawUploadPass(source: FakeSource(), upload: { _ in .failed }).run(daysBack: 14, waterEnabled: false)
    XCTAssertTrue(off.waterOff)
    let unasked = FakeSource()
    unasked.failures["heartbeat"] = HealthSyncBridge.Failure(code: "healthkit", message: "not determined", details: "5")
    let u = await RawUploadPass(source: unasked, upload: { _ in .failed }).run(daysBack: 14, waterEnabled: false)
    XCTAssertEqual(u.readProblem, "not_asked")
  }

  func testRawBatchShape() {
    let b = RawUploadPass.batch(kind: "ecg", page: RawPage(items: [["uuid": "e"]], deleted: ["x"]), batchId: "id", device: "iPhone", appVersion: "1.0.9+1")
    XCTAssertEqual((b["ecgs"] as? [Any])?.count, 1)
    XCTAssertEqual(b["deleted"] as? [String], ["x"])
    XCTAssertEqual(b["app_version"] as? String, "1.0.9+1")
  }

  // MARK: - settings taken over from 1.0.8

  func testSettingsComeFromTheFlutterApp() {
    let suite = "w2w.tests.syncsettings"
    let d = UserDefaults(suiteName: suite)!
    d.removePersistentDomain(forName: suite)
    defer { d.removePersistentDomain(forName: suite) }
    let s = SyncSettings(defaults: d)
    XCTAssertEqual(s.daysBack, SyncSettings.firstSyncDays)
    XCTAssertNil(s.rawRange)
    d.set(#"{"todayUrl":"https://body.example.com/app/","syncDaysBack":90}"#, forKey: "flutter.w2w.config.v1")
    d.set(90, forKey: "flutter.w2w.raw.range.v1")
    d.set(1_800_000_000.0, forKey: "flutter.w2w.backfill.to.v1")
    XCTAssertEqual(s.daysBack, 90)
    XCTAssertEqual(s.rawRange, 90)
    XCTAssertEqual(s.backfillTo(), 1_800_000_000.0)   // the same window, so Backfill.swift keeps its progress
    s.daysBack = 0
    XCTAssertEqual(s.daysBack, 0)
    XCTAssertTrue(SyncSettings.needsAnchorReset(previous: 14, next: 90))
    XCTAssertTrue(SyncSettings.needsAnchorReset(previous: 90, next: 0))
    XCTAssertFalse(SyncSettings.needsAnchorReset(previous: 0, next: 90))
    XCTAssertFalse(SyncSettings.needsAnchorReset(previous: nil, next: 90))
    s.resync = ResyncJob(days: 7, startedAt: Date(timeIntervalSince1970: 0), history: false)
    XCTAssertEqual(s.resync?.days, 7)
    s.clear()
    XCTAssertNil(s.resync)
  }
}
