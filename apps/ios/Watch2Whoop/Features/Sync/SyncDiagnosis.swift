import Foundation

/// "Where is it stuck, and what can I do?" in plain words, from everything the sync center knows. Pure (unit-tested):
/// the view shows the headline, then each issue with its one self-help button.
struct SyncDiagnosis: Equatable {
  enum Overall: Equatable { case ok, syncing, attention, off }

  /// The one button an issue offers.
  enum Action: Equatable {
    case requestPermission    // the one Health sheet
    case openHealthApp        // Health → profile → Apps → Watch2Whoop
    case openSettings         // this app's page in iOS Settings (background app refresh)
    case syncNow
    case retry                // ask the server again
    case signIn
    case consent
    case allowCellular
    case retryHistory
    case resync               // re-upload the last 7 days (a type still behind right after a sync)
    case fillHistory          // the history backfill (a type older than what the sync reads)
    case backgroundHelp       // the steps to turn Background App Refresh on (the person does it in iOS Settings)
  }

  struct Issue: Equatable, Identifiable {
    let id: String
    let text: String
    let action: Action?
    var actionTitle: String? {
      switch action {
      case .requestPermission: return tr("允许读取", "Allow access")
      case .openHealthApp: return tr("打开「健康」", "Open Health")
      case .openSettings: return tr("打开设置", "Open Settings")
      case .syncNow: return tr("立即同步", "Sync now")
      case .retry: return tr("重试", "Retry")
      case .signIn: return tr("重新登录", "Sign in again")
      case .consent: return tr("查看同意书", "Review consent")
      case .allowCellular: return tr("允许蜂窝数据", "Allow cellular")
      case .retryHistory: return tr("继续补传", "Resume")
      case .resync: return tr("重新同步", "Re-sync")
      case .fillHistory: return tr("补全历史", "Fill in history")
      case .backgroundHelp: return tr("怎么打开", "How to")
      case nil: return nil
      }
    }
  }

  /// What HealthKit says about the one permission sheet.
  enum Permission: Equatable { case unknown, notAsked, asked, unavailable }

  struct Input {
    var permission: Permission = .unknown
    /// nil = the server did not answer (yet).
    var info: IngestInfo?
    /// The last server call failed this way: unauthorized / offline / nil.
    var serverProblem: String?
    var engine: EngineSnapshot?
    var server: ServerSyncStatus?
    var rows: [TypeRow] = []
    var backfill: BackfillState?
    /// The last foreground round: types skipped (`wifi_only` …) and why it stopped.
    var lastSkipped: Set<String> = []
    var rawStop: String?
    var rawReadProblem: String?
    var backgroundRefresh = true
    var lowPower = false
    var consentRequired = false
    var running = false
    var lastOk: Date?
    /// The post-sync answer (SyncResult): its problems make the headline, so it never says 「已同步」 next to a type
    /// that is not.
    var result: SyncResult?
  }

  let overall: Overall
  let headline: String
  let issues: [Issue]

  init(overall: Overall, headline: String, issues: [Issue]) {
    self.overall = overall
    self.headline = headline
    self.issues = issues
  }

  static func of(_ i: Input, now: Date = Date()) -> SyncDiagnosis {
    var out: [Issue] = []
    func add(_ id: String, _ text: String, _ a: Action?) { if !out.contains(where: { $0.id == id }) { out.append(Issue(id: id, text: text, action: a)) } }

    if i.permission == .unavailable {
      return SyncDiagnosis(overall: .off, headline: tr("这台设备没有「健康」", "Health is not available on this device"), issues: [])
    }
    switch i.serverProblem {
    case "unauthorized": add("signin", tr("登录失效了，重新登录后接着同步", "You were signed out; sign in again to keep syncing"), .signIn)
    case "offline": add("offline", tr("连不上服务器：没有网络或网络很慢，联网后自动补传", "Can't reach the server (no or slow network); it catches up once online"), .retry)
    default: break
    }
    if i.consentRequired || i.engine?.lastStop == "consent_required" || i.rawStop == "consent" {
      add("consent", tr("确认同意书后才会上传", "Uploads start after you accept the consent"), .consent)
    }
    if i.permission == .notAsked {
      add("permission", tr("还没允许读取「健康」，数据传不上去", "Health access was never allowed, so nothing can be sent"), .requestPermission)
    } else if i.rawReadProblem == "not_asked" {
      add("permission", tr("心跳序列、心电图还没允许读取", "Heartbeat series and ECGs were never allowed"), .requestPermission)
    }
    if let info = i.info, !info.engineActive {
      add("engine_off", tr("服务器还没为你打开 app 同步，请联系管理员", "Your server has not turned on app sync for you yet; ask the admin"), .retry)
    }
    let collected = i.rows.filter { $0.verdict != .notCollected }
    if i.permission == .asked && !i.rows.isEmpty && collected.isEmpty && i.serverProblem == nil {
      add("nothing", tr("手机上一项健康数据都读不到：多半是没打开读取权限", "Nothing can be read from Health: access is probably turned off"), .openHealthApp)
    }
    // a locked phone and Background App Refresh off are in the sync result (its warnings), next to the types
    switch i.engine?.lastStop {
    case "rate_limited": add("rate", tr("服务器让稍后再传，会自动继续", "The server asked to wait; it continues by itself"), nil)
    case "expired": add("expired", tr("后台时间用完，下次接着传", "Background time ran out; it continues next time"), nil)
    case "unauthorized", "backoff": add("signin", tr("登录失效了，重新登录后接着同步", "You were signed out; sign in again to keep syncing"), .signIn)
    case "offline": add("offline", tr("没有网络，联网后自动补传", "No network; it catches up once online"), .retry)
    case "failed": add("failed", tr("上次同步没完成，会自动重试", "The last sync did not finish; it retries by itself"), .syncNow)
    default: break
    }
    if i.lastSkipped.contains("wifi_only"), i.engine?.cellularHistory == false {
      add("wifi", tr("首次同步和补历史只用 Wi-Fi：连上 Wi-Fi，或允许用蜂窝数据", "The first sync and history use Wi-Fi only: join Wi-Fi or allow cellular"), .allowCellular)
    }
    if i.lowPower {
      add("lowpower", tr("低电量模式下只传重要数据，历史等充电后再补", "Low Power Mode: only the most important data goes; history waits"), nil)
    }
    if let f = i.server?.openFailure {
      add("refused", tr("服务器拒收了上次上传（\(f)），点「立即同步」重试", "The server refused the last upload (\(f)); tap Sync now"), .syncNow)
    }
    if let b = i.backfill, b.active, let e = b.error, !e.isEmpty {
      add("history", tr("补历史暂停了，会自动继续", "History upload paused; it continues by itself"), .retryHistory)
    }

    let blocking: Set<String> = ["signin", "offline", "consent", "permission", "engine_off", "nothing", "refused", "failed"]
    let overall: Overall
    let headline: String
    if out.contains(where: { $0.id == "engine_off" }) && i.engine?.active != true {
      overall = .off
      headline = tr("同步还没开启", "Sync is not on yet")
    } else if i.running {
      overall = .syncing
      headline = tr("正在同步", "Syncing")
    } else if out.contains(where: { blocking.contains($0.id) }) {
      overall = .attention
      headline = tr("同步需要处理", "Sync needs attention")
    } else if let r = i.result, !r.synced {
      overall = .attention
      headline = tr("\(r.problems.count) 项没同步", "\(r.problems.count) not synced")
    } else if let at = i.lastOk {
      overall = .ok
      headline = tr("已同步 · \(Self.ago(at, now: now))", "Synced · \(Self.ago(at, now: now))")
    } else {
      overall = .ok
      headline = tr("同步已开启", "Sync is on")
    }
    return SyncDiagnosis(overall: overall, headline: headline, issues: out)
  }

  /// "刚刚" / "5 分钟前" / "3 小时前" / "2 天前".
  static func ago(_ d: Date?, now: Date = Date()) -> String {
    guard let d else { return "—" }
    let s = max(0, now.timeIntervalSince(d))
    if s < 60 { return tr("刚刚", "just now") }
    if s < 3600 { let m = Int(s / 60); return tr("\(m) 分钟前", "\(m) min ago") }
    if s < 86400 { let h = Int(s / 3600); return tr("\(h) 小时前", "\(h) h ago") }
    let days = Int(s / 86400)
    return tr("\(days) 天前", "\(days) d ago")
  }
}
