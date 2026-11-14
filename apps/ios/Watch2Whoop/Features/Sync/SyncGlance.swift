import Foundation

/// Display constants of the 2.0.1 sync dial (docs/design/2.0.1 `07-me`, `08-sync-center`). Not health thresholds.
enum SyncParams {
  /// Ticks of the sync dial: one per day of the server's missing-days window (sync/params.py MISSING_DAYS_WINDOW = 30).
  static let dialDays = 30
  /// Days of each type lane in the sync center (design: 14-day dot strips).
  static let laneDays = 14
  /// Side of the sync center's dial (design: 200 pt).
  static let centerDialSide: Double = 200
}

/// The last 30 days as the sync dial and the type lanes draw them, from `GET /sync/missing-days` (dates and kind codes
/// only). Pure (unit-tested in `SyncGlanceTests`).
struct SyncDays: Equatable {
  enum Mark: Equatable {
    /// The server has every core kind of the day.
    case ok
    /// The server lacks at least one core kind (a re-sync of the day may fill it).
    case missing
    /// Before the account's first data: not counted.
    case before
  }

  struct Lane: Equatable, Identifiable {
    let key: String
    let label: String
    /// Oldest to newest, `SyncParams.laneDays` long.
    let marks: [Mark]
    var id: String { key }
  }

  /// Oldest to newest, the window's days (the newest is the server's yesterday: today is still coming in).
  var days: [String]
  var marks: [Mark]
  var lanes: [Lane]

  var missing: Int { marks.filter { $0 == .missing }.count }
  /// Days that count (since the first data).
  var counted: Int { marks.filter { $0 != .before }.count }
  var synced: Int { counted - missing }
  /// The days of the lanes (the window's last `laneDays`).
  var laneDays: [String] { Array(days.suffix(lanes.first?.marks.count ?? 0)) }

  /// - fallbackEnd: the window's last day when the server did not say (an older server): the phone's yesterday.
  static func of(_ m: MissingDays, fallbackEnd: String, window: Int = SyncParams.dialDays,
                 laneDays: Int = SyncParams.laneDays) -> SyncDays {
    let end = m.end ?? fallbackEnd
    var days: [String] = []
    for i in stride(from: window - 1, through: 0, by: -1) { days.append(LocalDay.add(end, -i)) }
    var missingKinds: [String: Set<String>] = [:]
    for d in m.days { missingKinds[d.date] = Set(d.kinds) }
    // no first day and nothing missing: the account has no data of these kinds yet
    let noData = m.since == nil && m.days.isEmpty
    func before(_ day: String) -> Bool {
      if noData { return true }
      if let s = m.since { return day < s }
      return false
    }
    var marks: [Mark] = []
    for day in days {
      if before(day) { marks.append(.before) }
      else if let k = missingKinds[day], !k.isEmpty { marks.append(.missing) }
      else { marks.append(.ok) }
    }
    let laneDates = Array(days.suffix(laneDays))
    var lanes: [Lane] = []
    for kind in m.kinds {
      var lm: [Mark] = []
      for day in laneDates {
        if before(day) { lm.append(.before) }
        else if missingKinds[day]?.contains(kind.key) == true { lm.append(.missing) }
        else { lm.append(.ok) }
      }
      lanes.append(Lane(key: kind.key, label: kind.label, marks: lm))
    }
    return SyncDays(days: days, marks: marks, lanes: lanes)
  }
}

/// The one primary button of the sync center and the one self-help step under it.
enum SyncGlanceRules {
  enum Primary: Equatable { case turnOn, syncNow, retry }

  static func primary(permission: SyncDiagnosis.Permission, issues: [SyncDiagnosis.Issue]) -> Primary {
    if permission == .notAsked { return .turnOn }
    if issues.contains(where: { $0.action == .retry }) { return .retry }
    return .syncNow
  }

  static func title(_ p: Primary) -> String {
    switch p {
    case .turnOn: return tr("开启同步", "Turn on sync")
    case .syncNow: return tr("立即同步", "Sync now")
    case .retry: return tr("重试", "Retry")
    }
  }

  /// The first issue the primary button does not already cover (one with its own self-help action), else the first hint
  /// with an action (Background App Refresh off). Nil = nothing for the user to do.
  static func helpStep(issues: [SyncDiagnosis.Issue], hints: [SyncDiagnosis.Issue]) -> SyncDiagnosis.Issue? {
    let own: (SyncDiagnosis.Issue) -> Bool = { i in
      guard let a = i.action else { return false }
      return a != .syncNow && a != .retry && a != .requestPermission
    }
    if let i = issues.first(where: own) { return i }
    return hints.first { $0.action != nil }
  }

  /// A short label (≤ 6 characters, direction.md 6) for the issues that have one; the action's own title otherwise.
  static func shortLabel(_ i: SyncDiagnosis.Issue) -> String {
    switch i.id {
    case "background": return tr("后台刷新已关", "Background off")
    case "consent": return tr("确认同意书", "Accept consent")
    case "signin": return tr("重新登录", "Sign in again")
    case "lowpower": return tr("低电量模式", "Low Power Mode")
    case "wifi": return tr("只用无线局域网", "Wi-Fi only")
    case "history": return tr("历史没传完", "History paused")
    default: return i.actionTitle ?? tr("去处理", "Fix")
    }
  }

  /// The dial's verdict: 「已同步」 when nothing is missing, 「可补传」 when some days are, 「没有数据」 before the first.
  static func verdict(_ d: SyncDays) -> String {
    if d.counted == 0 { return tr("没有数据", "No data yet") }
    return d.missing > 0 ? tr("可补传", "Can re-sync") : tr("已同步", "Synced")
  }
}
