import Foundation

/// The one answer after a sync (the owner's report 2026-10-31: 「同步按钮显示已最新，但仍提示部分数据未同步」). Pure
/// (unit-tested): either 「已同步」 with the time, or a short list type → reason → action, from the same facts the rest of
/// the sync center shows:
/// - the phone vs server comparison per HealthKit type (TypeRow): newer on the phone = not synced (sync again, then
///   re-sync the last days); older than what the sync reads (first sync 14 days, the history backfill) = fill in history;
/// - the server's data status per data type (ServerDataStatus, `reason` / `action` from servers of 2026-10-31): never
///   anything of a type a worn watch records daily = probably no read permission (HealthKit never says); not delivered;
///   or quiet: nothing new recorded on the phone (the watch not worn, no scale, on-demand like ECG, outdoor only like
///   VO₂ max, drinks logged in Health only, a type logged elsewhere);
/// - the phone: the last round stopped because the phone was locked; Background App Refresh off.
/// `synced` is true when nothing the phone has is missing on the server; the phone-side warnings and the quiet types do
/// not change it.
struct SyncResult: Equatable {
  enum Reason: String, Equatable {
    case notSynced, outsideRange, notDelivered, noPermission, locked, backgroundOff, notRecorded
  }

  struct Item: Equatable, Identifiable {
    let id: String
    let label: String
    let reason: Reason
    /// What makes a not-recorded type appear (the server's action code: use_scale, wear_watch_sleep …), nil = unknown.
    var hint: String?
    var action: SyncDiagnosis.Action?
  }

  struct Input {
    var rows: [TypeRow] = []
    var status: ServerDataStatus?
    var lastStop: String?
    var backgroundRefresh = true
    var lastOk: Date?
    var now = Date()
  }

  let synced: Bool
  let at: Date?
  /// Data the server is missing (each with its one action).
  let problems: [Item]
  /// The phone side that will stop the next background syncs.
  let warnings: [Item]
  /// Nothing new on the phone either: not a sync problem.
  let quiet: [Item]
  /// For outsideRange: how much history the backfill should read (90 days, 0 = all history).
  let historyDays: Int

  /// A round this recent counts as "just synced": a type still behind then needs a re-sync, not another tap.
  static let fresh: TimeInterval = 15 * 60
  static let historyWindow: TimeInterval = 90 * 86_400

  static func of(_ i: Input) -> SyncResult {
    var problems: [Item] = [], quiet: [Item] = [], warnings: [Item] = []
    let justSynced = i.lastOk.map { i.now.timeIntervalSince($0) < fresh } ?? false
    var historyDays = SyncSettings.backfillDays
    for r in i.rows {
      switch r.verdict {
      case .notSynced:
        problems.append(Item(id: r.id, label: r.type.label, reason: .notSynced, action: justSynced ? .resync : .syncNow))
      case .outsideRange:
        problems.append(Item(id: r.id, label: r.type.label, reason: .outsideRange, action: .fillHistory))
        if let p = r.phone, i.now.timeIntervalSince(p) > historyWindow { historyDays = 0 }
      default: break
      }
    }
    let flagged = Set(i.rows.filter { $0.verdict == .notSynced || $0.verdict == .outsideRange }.compactMap(\.type.status))
    for k in i.status?.types ?? [] where k.status != "ok" && !flagged.contains(k.key) {
      let phone = i.rows.filter { $0.type.status == k.key }
      let phoneHasNothingNewer = phone.allSatisfy { $0.verdict == .synced || $0.verdict == .notCollected }
      switch k.reason {
      case "not_delivered"?:
        // the server infers it from its side; a round that just ran found nothing newer on the phone, so it was not recorded
        if justSynced && phoneHasNothingNewer {
          quiet.append(Item(id: k.key, label: k.label, reason: .notRecorded))
        } else {
          problems.append(Item(id: k.key, label: k.label, reason: .notDelivered, action: .syncNow))
        }
      case "no_permission"?:
        problems.append(Item(id: k.key, label: k.label, reason: .noPermission, action: .openHealthApp))
      case "not_recorded"?, "on_demand"?, "logged_elsewhere"?:
        quiet.append(Item(id: k.key, label: k.label, reason: .notRecorded, hint: k.action))
      case nil where phoneHasNothingNewer:   // an older server: no reasons, the phone has nothing newer either
        quiet.append(Item(id: k.key, label: k.label, reason: .notRecorded))
      default:
        break   // unsupported / flag_off: nothing the person can do here
      }
    }
    if i.lastStop == "locked" {
      warnings.append(Item(id: "locked", label: tr("后台同步", "Background sync"), reason: .locked))
    }
    if !i.backgroundRefresh {
      warnings.append(Item(id: "background", label: tr("后台 App 刷新", "Background App Refresh"), reason: .backgroundOff,
                           action: .backgroundHelp))
    }
    return SyncResult(synced: problems.isEmpty, at: i.lastOk, problems: problems, warnings: warnings, quiet: quiet,
                      historyDays: historyDays)
  }

  // MARK: - words (short labels; the longer text is in the info sheet)

  static func reasonLabel(_ item: Item) -> String {
    switch item.reason {
    case .notSynced: return tr("没同步", "Not synced")
    case .outsideRange: return tr("历史没补", "History not filled")
    case .notDelivered: return tr("没传上来", "Not delivered")
    case .noPermission: return tr("可能没开读取", "Access may be off")
    case .locked: return tr("锁屏时读不到", "Phone was locked")
    case .backgroundOff: return tr("关着", "Off")
    case .notRecorded: return hintLabel(item.hint)
    }
  }

  static func hintLabel(_ hint: String?) -> String {
    switch hint {
    case "use_scale": return tr("需要体重秤", "Needs a scale")
    case "wear_watch_sleep": return tr("戴表睡觉才有", "Wear the watch to bed")
    case "wear_watch": return tr("戴手表才有", "Wear the watch")
    case "outdoor_workout": return tr("户外运动后才有", "After outdoor workouts")
    case "record_ecg": return tr("测心电图才有", "Record an ECG")
    case "log_in_health": return tr("在「健康」记录才有", "Log it in Health")
    case "log_workout": return tr("运动后才有", "After a workout")
    case "wear_headphones": return tr("戴耳机才有", "With headphones")
    default: return tr("手机上没有新记录", "Nothing new on the phone")
    }
  }

  /// One paragraph per reason shown, for the info sheet.
  static func explanation(_ r: Reason) -> String {
    switch r {
    case .notSynced:
      return tr("手机上有比服务器新的数据。先点「立即同步」；刚同步过还这样，用「重新同步」把最近几天重新上传。",
                "The phone has newer data than the server. Tap Sync now; if it just synced, use Re-sync to upload the last days again.")
    case .outsideRange:
      return tr("手机上最新的一条比同步读取的范围还早（首次只读近 14 天）。用「补全历史」把更早的数据传上去。",
                "The newest record on the phone is older than what the sync reads (the first sync reads 14 days). Fill in history to upload it.")
    case .notDelivered:
      return tr("服务器最近没收到这台手机的同步。打开 app 点「立即同步」，并检查下面的后台设置。",
                "The server has not received this phone's sync lately. Open the app, tap Sync now and check the background settings below.")
    case .noPermission:
      return tr("这一项从来没收到过。iOS 不告诉 app 有没有读取权限：在「健康」→ 头像 →「App」→「Watch2Whoop」里打开它。",
                "Nothing of this ever arrived. iOS does not tell apps whether reading is allowed: turn it on in Health → profile → Apps → Watch2Whoop.")
    case .locked:
      return tr("iPhone 锁着时 iOS 不让 app 读「健康」。上次后台同步碰上锁屏，解锁后会自动接着传。",
                "iOS does not let apps read Health while the iPhone is locked. The last background sync met a locked phone; it continues once unlocked.")
    case .backgroundOff:
      return tr("「后台 App 刷新」关着时只有打开 app 才同步。",
                "With Background App Refresh off, it only syncs while the app is open.")
    case .notRecorded:
      return tr("手机上也没有更新的记录，不是同步的问题：比如没戴手表、没戴表睡觉、没有体重秤、心电图要自己测、VO₂max 只在户外运动后估算、饮水只读「健康」里记的。体重在这里记录的也算。",
                "The phone has nothing newer either, so it is not a sync problem: the watch not worn (or not in bed), no scale, ECGs only when you record one, VO₂ max only after outdoor workouts, water only as logged in Health. Weights logged here count too.")
    }
  }

  /// Steps to turn Background App Refresh on (the person does it; the app never changes settings).
  static var backgroundSteps: [String] {
    [tr("打开 iPhone「设置」，下滑找到「Watch2Whoop」", "Open iPhone Settings and scroll to Watch2Whoop"),
     tr("打开「后台 App 刷新」", "Turn on Background App Refresh"),
     tr("如果那里是灰的：「设置」→「通用」→「后台 App 刷新」→ 选「无线局域网与蜂窝网络」", "If it is greyed out: Settings → General → Background App Refresh → Wi-Fi & Cellular Data"),
     tr("低电量模式也会暂停后台刷新", "Low Power Mode also pauses it")]
  }
}
