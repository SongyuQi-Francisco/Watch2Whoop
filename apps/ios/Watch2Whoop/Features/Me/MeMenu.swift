import Foundation

// 我 since 2.0.1 (feedback on 2.0.0: too many rows, too mixed): the first level keeps what people open most — four groups,
// at most 12 rows — and everything else sits on a page one tap further, pushed like the sync center (never folded open in
// place). Pure, so the tests can check that no entry is more than two taps away; 删除账号 included (App Review looks for it:
// 我 → 账号 → 删除账号).

/// A page one level down from 我.
enum MePage: String, CaseIterable, Hashable {
  case shared, healthRecord, data, preferences, help, account

  var title: String {
    switch self {
    case .shared: return tr("共享给我的", "Shared with me")
    case .healthRecord: return tr("健康档案", "Health record")
    case .data: return tr("数据与导入", "Data and import")
    case .preferences: return tr("偏好设置", "Preferences")
    case .help: return tr("帮助与关于", "Help and about")
    case .account: return tr("账号", "Account")
    }
  }

  var icon: Icon.Name {
    switch self {
    case .shared: return .users
    case .healthRecord: return .heart
    case .data: return .database
    case .preferences: return .settings
    case .help: return .info
    case .account: return .user
    }
  }
}

/// One row of 我 or of one of its pages.
enum MeItem: Hashable {
  case page(MePage)
  // first level
  case inbox, syncCenter, unsent, ai, notifications, invite
  // 健康档案
  case supplements, labs, events, bodyAge, memory
  // 数据与导入
  case dataStatus, hevy
  // 偏好设置
  case bedtimeGoal, trainingSettings, language, appearance
  // 帮助与关于
  case handbook, feedback, whatsNew, privacy, terms, licences, version
  // 账号
  case profile, signIn, lateInvite, signOut, deleteAccount

  var title: String {
    switch self {
    case .page(let p): return p.title
    case .inbox: return tr("消息", "Messages")
    case .syncCenter: return tr("同步中心", "Sync center")
    case .unsent: return tr("没提交的修改", "Unsent changes")
    case .ai: return tr("AI 设置", "AI settings")
    case .notifications: return tr("通知", "Notifications")
    case .invite: return tr("邀请好友", "Invite friends")
    case .supplements: return tr("补剂", "Supplements")
    case .labs: return tr("化验", "Lab results")
    case .events: return tr("身体事件", "Body events")
    case .bodyAge: return tr("身体年龄", "Body age")
    case .memory: return tr("长期记忆", "Long-term memory")
    case .dataStatus: return tr("数据状态", "Data status")
    case .hevy: return tr("从 Hevy 导入", "Import from Hevy")
    case .bedtimeGoal: return tr("入睡目标", "Bedtime goal")
    case .trainingSettings: return tr("训练设置", "Training settings")
    case .language: return tr("语言", "Language")
    case .appearance: return tr("外观", "Appearance")
    case .handbook: return tr("使用手册", "User guide")
    case .feedback: return tr("反馈记录", "Feedback")
    case .whatsNew: return tr("更新记录", "What's new")
    case .privacy: return LegalLinks.title(.privacy)
    case .terms: return LegalLinks.title(.terms)
    case .licences: return tr("开源许可", "Open-source licences")
    case .version: return tr("版本", "Version")
    case .profile: return tr("个人资料", "Profile")
    case .signIn: return tr("登录方式", "Sign-in methods")
    case .lateInvite: return tr("填写邀请码", "Enter an invite code")
    case .signOut: return tr("退出登录", "Sign out")
    case .deleteAccount: return tr("删除账号", "Delete account")
    }
  }

  var icon: Icon.Name {
    switch self {
    case .page(let p): return p.icon
    case .inbox: return .inbox
    case .syncCenter: return .heartPulse
    case .unsent: return .clock   // the clock every page shows on a change still waiting to be sent
    case .ai: return .sparkles
    case .notifications: return .bell
    case .invite: return .userPlus
    case .supplements: return .pill
    case .labs: return .flaskConical
    case .events: return .calendarDays
    case .bodyAge: return .activity
    case .memory: return .archive
    case .dataStatus: return .database
    case .hevy: return .cloudUpload
    case .bedtimeGoal: return .moon
    case .trainingSettings: return .dumbbell
    case .language: return .languages
    case .appearance: return .palette
    case .handbook: return .bookOpenText
    case .feedback: return .messageCircle
    case .whatsNew: return .megaphone
    case .privacy: return .lock
    case .terms: return .fileText
    case .licences: return .bookOpen
    case .version: return .info
    case .profile: return .user
    case .signIn: return .logIn
    case .lateInvite: return .keyRound
    case .signOut: return .logOut
    case .deleteAccount: return .userX
    }
  }

  /// The sheet a tap opens; nil for pages, links, the language menu, the version and the account actions (MeView).
  var sheet: MeSheet? {
    switch self {
    case .unsent: return .unsent
    case .ai: return .ai
    case .notifications: return .notifications
    case .invite: return .invite
    case .supplements: return .records(.supplements)
    case .labs: return .records(.labs)
    case .events: return .records(.events)
    case .bodyAge: return .bodyAge
    case .memory: return .memory
    case .dataStatus: return .dataStatus
    case .hevy: return .hevy
    case .bedtimeGoal: return .bedtimeGoal
    case .trainingSettings: return .trainingGoals
    case .appearance: return .appearance
    case .feedback: return .feedback
    case .whatsNew: return .whatsNew
    case .licences: return .licences
    case .profile: return .profile
    case .signIn: return .signIn
    case .lateInvite: return .lateInvite
    case .page, .inbox, .syncCenter, .language, .handbook, .privacy, .terms, .version, .signOut, .deleteAccount: return nil
    }
  }
}

/// Rows in one card, with an optional section title above it.
struct MeMenuGroup: Equatable {
  var title: String?
  var items: [MeItem]
}

/// What 我 shows for this account right now: the flags and the server's answers decide which rows exist.
struct MeMenu: Equatable {
  var flags: Set<String> = []
  /// Messages (flag `inbox`) and its unread count.
  var inbox = false
  var unread = 0
  /// Changes not on the server yet.
  var unsent = 0
  /// People who share with me.
  var shared = 0
  /// The server offers referrals / still takes an invite code (AccountViews.swift).
  var referrals = false
  var inviteOptional = false
  /// The signed-in server has a user guide / privacy policy / terms address (AboutViews.swift).
  var handbook = false
  var privacy = false
  var terms = false

  /// The first level, in order. 消息 and 同步 first (what needs attention), then the data, the settings people change,
  /// and the rest; 邀请好友 stays here (circles are a growth hook, never weaker), 共享给我的 only when someone shares.
  var firstLevel: [MeMenuGroup] {
    var attention: [MeItem] = inbox ? [.inbox] : []
    attention.append(.syncCenter)
    if unsent > 0 { attention.append(.unsent) }   // next to sync, and only while there is something to send
    var data: [MeItem] = shared > 0 ? [.page(.shared)] : []
    data += entry(.healthRecord) + entry(.data)
    var more: [MeItem] = referrals ? [.invite] : []
    more += entry(.help) + entry(.account)
    return [
      MeMenuGroup(items: attention),
      MeMenuGroup(title: tr("数据", "Data"), items: data),
      MeMenuGroup(title: tr("设置", "Settings"), items: [.ai, .notifications] + entry(.preferences)),
      MeMenuGroup(items: more),
    ].filter { !$0.items.isEmpty }
  }

  /// The rows of a page. 共享给我的 lists the people themselves (MeView), so it has no rows here.
  func groups(_ page: MePage) -> [MeMenuGroup] {
    switch page {
    case .shared:
      return []
    case .healthRecord:
      return [MeMenuGroup(items: [.supplements, .labs, .events, .bodyAge, .memory])]
    case .data:
      return [MeMenuGroup(items: [.dataStatus] + (flags.contains("hevy_import") ? [.hevy] : []))]
    case .preferences:
      var rows: [MeItem] = [.bedtimeGoal]
      if flags.contains("muscle_load") { rows.append(.trainingSettings) }
      rows.append(.language)
      if flags.contains(AppearanceChoice.flag) { rows.append(.appearance) }
      return [MeMenuGroup(items: rows)]
    case .help:
      return [
        MeMenuGroup(title: tr("帮助", "Help"), items: (handbook ? [.handbook] : []) + [.feedback, .whatsNew]),
        MeMenuGroup(title: tr("关于", "About"), items: (privacy ? [.privacy] : []) + (terms ? [.terms] : []) + [.licences, .version]),
      ]
    case .account:
      return [
        MeMenuGroup(items: [.profile, .signIn] + (inviteOptional ? [.lateInvite] : [])),
        MeMenuGroup(items: [.signOut, .deleteAccount]),
      ]
    }
  }

  /// A page's row on the first level: the page, or its only row when it would hold just one (no page for a single row).
  func entry(_ page: MePage) -> [MeItem] {
    let rows = groups(page).flatMap(\.items)
    return rows.count == 1 ? rows : rows.isEmpty ? [] : [.page(page)]
  }
}
