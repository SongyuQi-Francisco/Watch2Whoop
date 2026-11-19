import Foundation
import UIKit

// "Is the background sync running?" from evidence, not only from iOS's flag (docs/ios-native-app.md "Background sync:
// evidence, not the flag"). 2026-11-19: a phone reported `backgroundRefreshStatus == .denied` for days while its owner saw
// the switch on, and the app showed one sentence for every cause. Apple's `.denied` means "disabled for this app or for
// the whole system" and does not say which; Low Power Mode turns Background App Refresh off on its own. So the app records
// when iOS really woke it in the background (BackgroundWakes), judges from that first (BackgroundCheck), and names the
// switches to check, with their paths in Settings.

/// When iOS woke the app in the background, by kind (times only, UserDefaults, this phone; not per account: it describes the
/// device). Written at the wake itself, before any work: a BGTask launch, a HealthKit observer call while the app is in the
/// background, a silent push in the background, a finished background transfer. Read by BackgroundCheck and sent with the
/// device report (HealthSyncService.reportDevice).
enum BackgroundWakes {
  enum Kind: String, CaseIterable, Equatable {
    case refresh, processing, observer, push, transfer
  }

  struct Snapshot: Equatable {
    var last: Date?
    var lastKind: Kind?
    /// Wakes in the last `BackgroundSyncParams.countDays`.
    var count7d = 0
    /// The first launch that recorded wakes (2.1.0): before it plus the threshold, "not running" cannot be told.
    var since: Date?
    /// The last time the app saw iOS say background is not available (or Low Power Mode): the idle clock starts again
    /// there, so a switch just turned on is not called "not running".
    var lastUnavailable: Date?
  }

  private static let d = UserDefaults.standard
  private static let lock = NSLock()
  private static let timesKey = "w2w.bg.wakes.v1"
  private static let kindKey = "w2w.bg.lastKind"
  private static let sinceKey = "w2w.bg.since"
  private static let offKey = "w2w.bg.unavailable"

  /// At launch: the start of the recording (once per install).
  static func start(now: Date = Date()) {
    lock.lock(); defer { lock.unlock() }
    if d.double(forKey: sinceKey) == 0 { d.set(now.timeIntervalSince1970, forKey: sinceKey) }
  }

  /// A wake reported by the sync engine (SyncEngine.onWake), the push handler or the transfer handler; any thread.
  /// BGTasks and background transfers only ever come in the background; HealthKit observers and pushes also while the app
  /// is open, so those count only when the app is in the background.
  static func wake(_ code: String) {
    guard let kind = Kind(rawValue: code) else { return }
    switch kind {
    case .refresh, .processing, .transfer:
      record(kind)
    case .observer, .push:
      DispatchQueue.main.async { if UIApplication.shared.applicationState == .background { record(kind) } }
    }
  }

  static func record(_ kind: Kind, at: Date = Date()) {
    lock.lock(); defer { lock.unlock() }
    var t = d.array(forKey: timesKey) as? [Double] ?? []
    t.append(at.timeIntervalSince1970)
    if t.count > BackgroundSyncParams.keptWakes { t.removeFirst(t.count - BackgroundSyncParams.keptWakes) }
    d.set(t, forKey: timesKey)
    d.set(kind.rawValue, forKey: kindKey)
  }

  static func noteUnavailable(at: Date = Date()) {
    d.set(at.timeIntervalSince1970, forKey: offKey)
  }

  static func snapshot(now: Date = Date()) -> Snapshot {
    lock.lock(); defer { lock.unlock() }
    let t = d.array(forKey: timesKey) as? [Double] ?? []
    let from = now.timeIntervalSince1970 - BackgroundSyncParams.countDays * 86_400
    func date(_ k: String) -> Date? { let v = d.double(forKey: k); return v > 0 ? Date(timeIntervalSince1970: v) : nil }
    return Snapshot(last: t.max().map { Date(timeIntervalSince1970: $0) },
                    lastKind: d.string(forKey: kindKey).flatMap(Kind.init(rawValue:)),
                    count7d: t.filter { $0 >= from }.count, since: date(sinceKey), lastUnavailable: date(offKey))
  }

  /// Tests and the debug screenshots only.
  static func reset() {
    lock.lock(); defer { lock.unlock() }
    for k in [timesKey, kindKey, sinceKey, offKey] { d.removeObject(forKey: k) }
  }
}

/// The rule, pure (table-driven tests in BackgroundSyncTests). Evidence first:
/// 1. a background wake within `BackgroundSyncParams.idleHours` → running, whatever iOS's flag says;
/// 2. iOS says background is not available → off, with the cause: restricted (Screen Time / device management; Apple: do
///    not push the person, they may not be able to change it), Low Power Mode (it turns Background App Refresh off), or
///    switched off (for this app or for the whole phone: iOS does not say which);
/// 3. iOS says available, yet nothing ran for the threshold since the clock started (the last wake, the first launch that
///    records, the last time iOS said off) while the phone has data newer than that → not running (force-quit, iOS
///    holding the app back);
/// 4. otherwise waiting: too early to tell, or nothing new on the phone to wake for.
/// The same verdict drives the sync center, the capsule on the first screens, the Today banner and the server's report.
enum BackgroundCheck {
  /// `UIApplication.backgroundRefreshStatus`.
  enum Status: String, Equatable { case available, denied, restricted }

  enum Cause: Equatable { case switchedOff, lowPower, restricted }

  enum Verdict: Equatable {
    case running(Date)
    case waiting
    case off(Cause)
    /// `last`: the last background wake, nil = none recorded on this phone.
    case notRunning(last: Date?)

    /// For the device report (`background_state`, migration 1000).
    var code: String {
      switch self {
      case .running: return "running"
      case .waiting: return "waiting"
      case .off(.switchedOff): return "off"
      case .off(.lowPower): return "low_power"
      case .off(.restricted): return "restricted"
      case .notRunning: return "not_running"
      }
    }

    /// A warning on the first screens (capsule, Today banner). Restricted is shown in the sync center only.
    var warns: Bool {
      switch self {
      case .off(.switchedOff), .off(.lowPower), .notRunning: return true
      case .off(.restricted), .running, .waiting: return false
      }
    }
  }

  struct Input: Equatable {
    var status: Status = .available
    var lowPower = false
    var wakes = BackgroundWakes.Snapshot()
    /// The newest sample on the phone of the types the sync reads (HealthSyncService.phoneLatest), nil = unknown.
    var newestData: Date?
    var now = Date()
  }

  static func status(_ s: UIBackgroundRefreshStatus) -> Status {
    switch s {
    case .available: return .available
    case .denied: return .denied
    case .restricted: return .restricted
    @unknown default: return .restricted
    }
  }

  static func of(_ i: Input, idleHours: Double = BackgroundSyncParams.idleHours) -> Verdict {
    let idle = idleHours * 3600
    if let w = i.wakes.last, w <= i.now, i.now.timeIntervalSince(w) <= idle { return .running(w) }
    if i.status == .restricted { return .off(.restricted) }
    if i.lowPower { return .off(.lowPower) }
    if i.status == .denied { return .off(.switchedOff) }
    guard let start = [i.wakes.last, i.wakes.since, i.wakes.lastUnavailable].compactMap({ $0 }).max(),
          i.now.timeIntervalSince(start) > idle,
          let newest = i.newestData, newest > start else { return .waiting }
    return .notRunning(last: i.wakes.last)
  }

  // MARK: - words (short labels on the first screens; the paths in Settings in the help sheet)

  struct Hint: Equatable {
    /// SyncDiagnosis.Issue id.
    let id: String
    /// The row in the sync center and the Today banner.
    let line: String
    /// The help sheet: what iOS says, then the steps with their paths.
    let intro: String
    let steps: [String]
    /// The sheet's button opens this app's page in Settings (the only page an app may open; it holds the app's own
    /// Background App Refresh switch). False = no switch the app can open (Low Power Mode, restrictions).
    let opensSettings: Bool
    /// ≤ 6 characters (the capsule, the sync center's help step).
    var short: String { BackgroundCheck.short(id: id) ?? "" }
  }

  /// The short label of a hint id.
  static func short(id: String) -> String? {
    switch id {
    case "background": return tr("后台刷新已关", "Background off")
    case "background_lowpower": return tr("低电量模式", "Low Power")
    case "background_restricted": return tr("后台受限", "Background limited")
    case "background_idle": return tr("后台没运行", "Background idle")
    default: return nil
    }
  }

  static func hint(_ v: Verdict, now: Date = Date()) -> Hint? {
    switch v {
    case .running, .waiting:
      return nil
    case .off(.switchedOff):
      return Hint(
        id: "background",
        line: tr("后台 App 刷新关着", "Background App Refresh is off"),
        intro: tr("现在只在打开 app 时同步。iOS 只告诉 app「关了」，不说是这台手机的总开关，还是这个 app 的开关，两个都要开着：", "It syncs only while the app is open. iOS only tells the app it is off, not whether the phone's switch or this app's switch, so both must be on:"),
        steps: [
          tr("这台手机：设置 → 通用 → 后台 App 刷新 → 选「无线局域网与蜂窝数据」（只选无线局域网时，用蜂窝网络就不刷新）", "This phone: Settings → General → Background App Refresh → choose Wi-Fi & Cellular Data (with Wi-Fi only, nothing refreshes on cellular)"),
          tr("这个 app：设置 → App → Watch2Whoop → 打开「后台 App 刷新」", "This app: Settings → Apps → Watch2Whoop → turn on Background App Refresh"),
          tr("两个都开着还这样：把它关掉再打开，然后回到这里", "Both on and still off: turn it off and on again, then come back here"),
        ],
        opensSettings: true)
    case .off(.lowPower):
      return Hint(
        id: "background_lowpower",
        line: tr("低电量模式：后台同步暂停", "Low Power Mode: background sync paused"),
        intro: tr("低电量模式会关掉后台 App 刷新，关掉低电量模式后后台同步才接着跑：", "Low Power Mode turns Background App Refresh off; background sync resumes once it is off:"),
        steps: [
          tr("设置 → 电池 → 低电量模式（iPhone 15 及以后：设置 → 电池 → 电量模式），或在控制中心关掉", "Settings → Battery → Low Power Mode (iPhone 15 and later: Settings → Battery → Power Mode), or turn it off in Control Center"),
          tr("开着「自适应电量」时，电量低于 20% 会自动打开低电量模式", "With Adaptive Power on, Low Power Mode turns itself on below 20% battery"),
        ],
        opensSettings: false)
    case .off(.restricted):
      return Hint(
        id: "background_restricted",
        line: tr("后台刷新被限制", "Background refresh is restricted"),
        intro: tr("这台手机的限制不让 app 在后台运行，现在只在打开 app 时同步，app 自己改不了：", "A restriction on this phone stops apps running in the background, so it syncs only while the app is open; the app cannot change it:"),
        steps: [
          tr("屏幕使用时间：设置 → 屏幕使用时间 → 内容和隐私访问限制，把「后台 App 活动」改成允许", "Screen Time: Settings → Screen Time → Content & Privacy Restrictions, set Background App Activities to Allow"),
          tr("公司或学校管理的手机：问管理员", "A phone managed by a company or school: ask its administrator"),
        ],
        opensSettings: false)
    case .notRunning(let last):
      let when = last.map { tr("上次 \(SyncDiagnosis.ago($0, now: now))", "last \(SyncDiagnosis.ago($0, now: now))") }
      return Hint(
        id: "background_idle",
        line: [tr("后台同步没在运行", "Background sync isn't running"), when].compactMap { $0 }.joined(separator: " · "),
        intro: tr("iOS 说后台刷新开着，但超过 \(Int(BackgroundSyncParams.idleHours)) 小时没在后台运行过，手机上却有新数据：", "iOS says Background App Refresh is on, yet nothing ran in the background for over \(Int(BackgroundSyncParams.idleHours)) h while the phone has new data:"),
        steps: [
          tr("别在多任务界面把 app 划掉：划掉后 iOS 不再在后台唤醒它，直到你再打开一次", "Don't swipe the app away in the app switcher: iOS then stops waking it until you open it again"),
          tr("电量很低时 iOS 也会暂停后台更新", "iOS also pauses background updates when the battery is very low"),
          tr("还不行：设置 → App → Watch2Whoop → 把「后台 App 刷新」关掉再打开", "Still not running: Settings → Apps → Watch2Whoop → turn Background App Refresh off and on again"),
        ],
        opensSettings: true)
    }
  }
}
