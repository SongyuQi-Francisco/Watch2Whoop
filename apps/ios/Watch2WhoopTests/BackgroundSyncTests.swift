import XCTest
@testable import Watch2Whoop

/// The background sync's rule (BackgroundCheck): what ran first, then iOS's flag; and the same verdict on every screen
/// (the sync center's hint, the capsule on the first screens, the Today banner) and in the device report.
final class BackgroundSyncTests: XCTestCase {
  private let now = Date(timeIntervalSince1970: 1_795_000_000)
  private let h: TimeInterval = 3600

  private struct Row {
    let name: String
    var status: BackgroundCheck.Status = .available
    var lowPower = false
    /// Hours ago (nil = none).
    var lastWake: Double?
    var since: Double? = 240
    var unavailable: Double?
    var newest: Double? = 0.1
    let expected: BackgroundCheck.Verdict
  }

  private func at(_ hoursAgo: Double?) -> Date? { hoursAgo.map { now.addingTimeInterval(-$0 * h) } }

  func testRuleTable() {
    let idle = BackgroundSyncParams.idleHours
    let rows: [Row] = [
      // 1. evidence wins: a recent wake means running, whatever the flag says
      Row(name: "recent wake, flag on", lastWake: 1, expected: .running(at(1)!)),
      Row(name: "recent wake, flag denied", status: .denied, lastWake: 2, expected: .running(at(2)!)),
      Row(name: "recent wake, restricted", status: .restricted, lastWake: 2, expected: .running(at(2)!)),
      Row(name: "recent wake, Low Power", status: .denied, lowPower: true, lastWake: 3, expected: .running(at(3)!)),
      Row(name: "wake just inside the threshold", lastWake: idle - 0.01, expected: .running(at(idle - 0.01)!)),
      Row(name: "a wake stamped in the future is no evidence", status: .denied, lastWake: -2, expected: .off(.switchedOff)),
      // 2. iOS says not available: the cause
      Row(name: "denied, no wake", status: .denied, expected: .off(.switchedOff)),
      Row(name: "denied, old wake", status: .denied, lastWake: 72, expected: .off(.switchedOff)),
      Row(name: "restricted", status: .restricted, lastWake: 72, expected: .off(.restricted)),
      Row(name: "restricted beats Low Power", status: .restricted, lowPower: true, expected: .off(.restricted)),
      Row(name: "Low Power while denied", status: .denied, lowPower: true, lastWake: 72, expected: .off(.lowPower)),
      Row(name: "Low Power while the flag still says available", lowPower: true, lastWake: 72, expected: .off(.lowPower)),
      // 3. iOS says available, nothing ran for the threshold while new data existed
      Row(name: "on, last wake 3 days ago, new data", lastWake: 72, expected: .notRunning(last: at(72))),
      Row(name: "on, never woke since recording started", lastWake: nil, since: 48, expected: .notRunning(last: nil)),
      // 4. waiting: too early to tell, or nothing new on the phone
      Row(name: "on, recording started an hour ago", lastWake: nil, since: 1, expected: .waiting),
      Row(name: "on, just switched on (seen off an hour ago)", lastWake: 72, unavailable: 1, expected: .waiting),
      Row(name: "on, seen off long ago", lastWake: 72, unavailable: 30, expected: .notRunning(last: at(72))),
      Row(name: "on, no new data since the last wake", lastWake: 72, newest: 80, expected: .waiting),
      Row(name: "on, the newest data unknown", lastWake: 72, newest: nil, expected: .waiting),
      Row(name: "on, nothing recorded at all", lastWake: nil, since: nil, expected: .waiting),
    ]
    for r in rows {
      var i = BackgroundCheck.Input()
      i.status = r.status
      i.lowPower = r.lowPower
      i.wakes = BackgroundWakes.Snapshot(last: at(r.lastWake), lastKind: r.lastWake == nil ? nil : .observer, count7d: 0,
                                         since: at(r.since), lastUnavailable: at(r.unavailable))
      i.newestData = at(r.newest)
      i.now = now
      XCTAssertEqual(BackgroundCheck.of(i), r.expected, r.name)
    }
  }

  func testCodesWarningsAndWords() {
    let cases: [(BackgroundCheck.Verdict, String, Bool, String?)] = [
      (.running(now), "running", false, nil),
      (.waiting, "waiting", false, nil),
      (.off(.switchedOff), "off", true, "background"),
      (.off(.lowPower), "low_power", true, "background_lowpower"),
      (.off(.restricted), "restricted", false, "background_restricted"),
      (.notRunning(last: nil), "not_running", true, "background_idle"),
    ]
    for (v, code, warns, id) in cases {
      XCTAssertEqual(v.code, code)
      XCTAssertEqual(v.warns, warns, code)
      let hint = BackgroundCheck.hint(v, now: now)
      XCTAssertEqual(hint?.id, id, code)
      if let hint {
        XCTAssertFalse(hint.short.isEmpty)
        XCTAssertLessThanOrEqual(hint.short.count, Lang.isZh ? 6 : 18, code)   // the capsule is one short line
        XCTAssertFalse(hint.steps.isEmpty, code)
      }
    }
    // the switches the app can open: its own page in Settings (where its own switch is); none for Low Power / restrictions
    XCTAssertEqual(BackgroundCheck.hint(.off(.switchedOff))?.opensSettings, true)
    XCTAssertEqual(BackgroundCheck.hint(.notRunning(last: nil))?.opensSettings, true)
    XCTAssertEqual(BackgroundCheck.hint(.off(.lowPower))?.opensSettings, false)
    XCTAssertEqual(BackgroundCheck.hint(.off(.restricted))?.opensSettings, false)
    // the reported codes are the server's (migration 1210, overview.BACKGROUND_STATES)
    XCTAssertEqual(Set(cases.map(\.0.code)), ["running", "waiting", "off", "low_power", "restricted", "not_running"])
  }

  func testSwitchedOffNamesBothSwitchesInBothLanguages() {
    for lang in ["zh", "en"] {
      Lang.override = lang
      defer { Lang.override = nil }
      let steps = BackgroundCheck.hint(.off(.switchedOff))!.steps.joined(separator: "\n")
      if lang == "zh" {
        XCTAssertTrue(steps.contains("设置 → 通用 → 后台 App 刷新"))
        XCTAssertTrue(steps.contains("设置 → App → Watch2Whoop"))
        XCTAssertTrue(steps.contains("无线局域网与蜂窝数据"))
      } else {
        XCTAssertTrue(steps.contains("Settings → General → Background App Refresh"))
        XCTAssertTrue(steps.contains("Settings → Apps → Watch2Whoop"))
        XCTAssertTrue(steps.contains("Wi-Fi & Cellular Data"))
      }
      let low = BackgroundCheck.hint(.off(.lowPower))!.steps.joined()
      XCTAssertTrue(low.contains(lang == "zh" ? "设置 → 电池" : "Settings → Battery"))
      let idle = BackgroundCheck.hint(.notRunning(last: now.addingTimeInterval(-2 * 86_400)), now: now)!
      XCTAssertTrue(idle.line.contains(lang == "zh" ? "2 天前" : "2 d ago"), idle.line)
    }
  }

  func testWakeLogCountsAndKinds() {
    BackgroundWakes.reset()
    defer { BackgroundWakes.reset() }
    BackgroundWakes.start(now: now.addingTimeInterval(-20 * 86_400))
    BackgroundWakes.start(now: now)   // once per install
    BackgroundWakes.record(.refresh, at: now.addingTimeInterval(-9 * 86_400))
    BackgroundWakes.record(.observer, at: now.addingTimeInterval(-3 * h))
    BackgroundWakes.record(.push, at: now.addingTimeInterval(-1 * h))
    BackgroundWakes.wake("nonsense")
    let s = BackgroundWakes.snapshot(now: now)
    XCTAssertEqual(s.last, now.addingTimeInterval(-1 * h))
    XCTAssertEqual(s.lastKind, .push)
    XCTAssertEqual(s.count7d, 2)
    XCTAssertEqual(s.since, now.addingTimeInterval(-20 * 86_400))
    XCTAssertNil(s.lastUnavailable)
    BackgroundWakes.noteUnavailable(at: now)
    XCTAssertEqual(BackgroundWakes.snapshot(now: now).lastUnavailable, now)
    for i in 0..<(BackgroundSyncParams.keptWakes + 5) { BackgroundWakes.record(.refresh, at: now.addingTimeInterval(Double(-i))) }
    XCTAssertEqual(BackgroundWakes.snapshot(now: now).count7d, BackgroundSyncParams.keptWakes)
  }

  // MARK: - one verdict on every screen

  private func diagnosis(_ v: BackgroundCheck.Verdict) -> SyncDiagnosis {
    var i = SyncDiagnosis.Input()
    i.permission = .asked
    i.info = IngestInfo(["engine": ["enabled": true, "mode": "app", "accepts": ["phase", "samples"]]])
    i.engine = EngineSnapshot(["mode": "app", "active": true, "firstSync": ["types": 3, "done": 3]])
    i.lastOk = now.addingTimeInterval(-60)
    i.server = ServerSyncStatus(["mode": "app", "pipeline": ["up_to_date": true]])
    i.result = SyncResult.of(SyncResult.Input())
    i.background = v
    i.lowPower = v == .off(.lowPower)
    return SyncDiagnosis.of(i, now: now)
  }

  func testSameVerdictInTheSyncCenterTheCapsuleAndTheBanner() {
    let serverSaysOff = TodayModel.SyncCard(state: "issue", label: "后台刷新被关", tone: .mid, ago: "3 分钟前",
                                            issueCode: "background_off", issueLabel: "后台刷新被关", minutes: 3)
    for v in [BackgroundCheck.Verdict.running(now), .waiting, .off(.switchedOff), .off(.lowPower), .off(.restricted),
              .notRunning(last: nil)] {
      let d = diagnosis(v)
      XCTAssertEqual(d.overall, .ok, v.code)   // never a sync failure: the foreground sync works
      XCTAssertEqual(d.background, v)
      let hint = BackgroundCheck.hint(v)
      XCTAssertEqual(d.hints.map(\.id), hint.map { [$0.id] } ?? [], v.code)
      XCTAssertEqual(d.hints.first?.action, hint == nil ? nil : .backgroundHelp)
      XCTAssertFalse(d.issues.contains { $0.id == "lowpower" }, v.code)   // the Low Power hint says it, with the steps
      // the capsule: the phone's verdict; the server's phone-side code from an older report gives way to it
      let capsule = SyncCapsuleState.of(overall: d.overall, page: .idle, card: serverSaysOff, background: d.background)
      if v.warns {
        XCTAssertEqual(capsule, .behind(hint!.short), v.code)
      } else {
        XCTAssertEqual(capsule, .ok(ago: "3 分钟前"), v.code)
      }
      // the Today banner
      let stuck = SyncStuck.of(d, card: serverSaysOff)
      XCTAssertEqual(stuck?.text, v.warns ? hint?.line : nil, v.code)
      XCTAssertEqual(stuck?.retry, v.warns ? false : nil)
      // the sync center's help step under the primary button
      XCTAssertEqual(SyncGlanceRules.helpStep(issues: d.issues, hints: d.hints)?.id, hint?.id, v.code)
      if let step = SyncGlanceRules.helpStep(issues: d.issues, hints: d.hints) {
        XCTAssertEqual(SyncGlanceRules.shortLabel(step), hint?.short)
      }
    }
    // without the phone's verdict (older call sites) the server's card still counts as before
    XCTAssertEqual(SyncCapsuleState.of(overall: .ok, page: .idle, card: serverSaysOff), .behind("3 分钟前"))
  }

  func testLowPowerIssueStaysWhenBackgroundStillRuns() {
    var i = SyncDiagnosis.Input()
    i.permission = .asked
    i.lowPower = true
    i.background = .running(now)
    XCTAssertTrue(SyncDiagnosis.of(i, now: now).issues.contains { $0.id == "lowpower" })
    XCTAssertTrue(SyncDiagnosis.of(i, now: now).hints.isEmpty)
  }
}
