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
    XCTAssertEqual(ids, ["locked", "wifi", "bg"])
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
