import SwiftUI

/// 我: the account, sync, the health record, settings, sign-out. Settings are online-only (docs/ios-native-app.md 5.5);
/// each opens as a sheet, the sync center as a page.
struct MeTab: View {
  @EnvironmentObject private var app: AppModel
  @EnvironmentObject private var session: AccountSession
  @ObservedObject private var sync = HealthSyncService.shared
  @State private var sheet: MeSheet?
  @State private var unsent = 0
  @State private var confirmSignOut = false
  @State private var confirmDelete = false
  @State private var deleteText = ""
  @State private var deleteError: String?
  @State private var busy = false

  var body: some View {
    NavigationStack {
      PageScaffold(title: tr("我", "Me")) {
        VStack(alignment: .leading, spacing: 18) {
          header
          RowGroup(title: tr("同步", "Sync")) {
            NavigationLink { SyncCenterView() } label: {
              row(.heartPulse, tr("同步中心", "Sync center"), value: sync.diagnosis.headline, tone: tone(sync.diagnosis.overall))
            }
            .buttonStyle(PressScale())
            RowDivider()
            ListRow(icon: .database, title: tr("数据状态", "Data status")) { sheet = .dataStatus }
            if unsent > 0 {
              RowDivider()
              ListRow(icon: .inbox, title: tr("没提交的修改", "Unsent changes"), value: "\(unsent)", tone: .mid) { sheet = .unsent }
            }
          }
          RowGroup(title: tr("健康档案", "Health record")) {
            ListRow(icon: .pill, title: tr("补剂", "Supplements")) { sheet = .records(.supplements) }
            RowDivider()
            ListRow(icon: .flaskConical, title: tr("化验", "Lab results")) { sheet = .records(.labs) }
            RowDivider()
            ListRow(icon: .calendarDays, title: tr("身体事件", "Body events")) { sheet = .records(.events) }
          }
          RowGroup(title: tr("设置", "Settings")) {
            ListRow(icon: .user, title: tr("个人资料", "Profile")) { sheet = .profile }
            RowDivider()
            ListRow(icon: .moon, title: tr("入睡目标", "Bedtime goal")) { sheet = .bedtimeGoal }
            RowDivider()
            ListRow(icon: .sparkles, title: tr("AI 设置", "AI settings")) { sheet = .ai }
            RowDivider()
            ListRow(icon: .bell, title: tr("通知", "Notifications")) { sheet = .notifications }
            RowDivider()
            languageRow
            if session.flags.contains("slim_mode") {
              RowDivider()
              ListRow(icon: .palette, title: tr("外观", "Appearance")) { sheet = .appearance }
            }
            RowDivider()
            ListRow(icon: .megaphone, title: tr("更新记录", "What's new")) { sheet = .whatsNew }
          }
          AboutRows(server: session.config.server) { sheet = .licences }
          RowGroup {
            ListRow(icon: .logOut, title: tr("退出登录", "Sign out")) { confirmSignOut = true }
            RowDivider()
            ListRow(icon: .userX, title: tr("删除账号", "Delete account"), tone: .bad) { deleteText = ""; deleteError = nil; confirmDelete = true }
          }
        }
        .padding(.top, 8)
      }
      .toolbar(.hidden, for: .navigationBar)
    }
    .task { await countUnsent() }
    .sheet(item: $sheet, onDismiss: { Task { await countUnsent() } }) { s in
      MeSheetView(sheet: s).environmentObject(session).environmentObject(app)
    }
    .confirmationDialog(tr("退出登录？", "Sign out?"), isPresented: $confirmSignOut, titleVisibility: .visible) {
      Button(tr("退出登录", "Sign out"), role: .destructive) { Task { await app.signOut() } }
      Button(tr("取消", "Cancel"), role: .cancel) {}
    } message: {
      Text(unsent > 0
           ? tr("还有 \(unsent) 条修改没提交到服务器，退出会丢掉它们。这台手机上的数据会删掉，服务器上的不受影响。", "\(unsent) changes have not reached the server yet and will be lost. Data on this iPhone is removed; your server keeps everything.")
           : tr("这台手机上的数据会删掉，服务器上的不受影响。", "Data on this iPhone is removed; your server keeps everything."))
    }
    .alert(tr("删除账号", "Delete account"), isPresented: $confirmDelete) {
      TextField("DELETE", text: $deleteText).textInputAutocapitalization(.characters)
      Button(tr("永久删除", "Delete forever"), role: .destructive) {
        guard deleteText == "DELETE" else { deleteError = tr("要输入 DELETE 才能删除", "Type DELETE to delete"); return }
        Task { deleteError = await app.deleteAccount() }
      }
      Button(tr("取消", "Cancel"), role: .cancel) {}
    } message: {
      Text(tr("账号和服务器上的全部数据都会删掉，不能恢复。输入 DELETE 确认。", "Your account and all its data on the server are deleted for good. Type DELETE to confirm."))
    }
    .alert(deleteError ?? "", isPresented: Binding(get: { deleteError != nil && !confirmDelete }, set: { if !$0 { deleteError = nil } })) {
      Button(tr("好", "OK"), role: .cancel) {}
    }
  }

  private var header: some View {
    HStack(spacing: 14) {
      Text(String((session.displayName ?? "?").prefix(1)).uppercased())
        .font(.system(size: 22, weight: .semibold)).foregroundColor(Palette.text)
        .frame(width: 52, height: 52)
        .background(Circle().fill(Palette.surface2))
      VStack(alignment: .leading, spacing: 2) {
        Text(session.displayName ?? tr("我的账号", "My account")).font(Typo.heroWord).foregroundColor(Palette.text)
        Text(URL(string: session.config.server)?.host ?? session.config.server).font(Typo.caption).foregroundColor(Palette.text2)
      }
      Spacer()
    }
    .padding(16)
    .background(RoundedRectangle(cornerRadius: Metrics.cardRadius, style: .continuous).fill(Palette.surface))
  }

  private var languageRow: some View {
    Menu {
      Button(tr("跟随系统", "System")) { setLang(nil) }
      Button("\u{4E2D}\u{6587}") { setLang("zh") }   // each language named in itself
      Button("English") { setLang("en") }
    } label: {
      row(.languages, tr("语言", "Language"), value: Lang.override == nil ? tr("跟随系统", "System") : (Lang.isZh ? "\u{4E2D}\u{6587}" : "English"))
    }
  }

  private func row(_ icon: Icon.Name, _ title: String, value: String? = nil, tone: Tone = .text) -> some View {
    HStack(spacing: 12) {
      Icon(icon, size: 20).foregroundColor(Palette.text2)
      Text(title).font(Typo.body).foregroundColor(Palette.text)
      Spacer(minLength: 8)
      if let value { Text(value).font(Typo.caption).foregroundColor(tone == .text ? Palette.text2 : tone.color).lineLimit(1) }
      Icon(.chevronRight, size: 16).foregroundColor(Palette.text3)
    }
    .padding(.vertical, 12)
    .contentShape(Rectangle())
  }

  private func tone(_ o: SyncDiagnosis.Overall) -> Tone {
    switch o {
    case .ok: return .good
    case .attention: return .mid
    default: return .text
    }
  }

  /// The UI language here; the profile's language too, so texts from the server follow (X-Body-Lang is sent anyway).
  private func setLang(_ code: String?) {
    app.setLanguage(code)
    let api = session.api
    Task {
      let body: [String: String?] = ["language": code]
      _ = try? await api.data(try .json(.patch, Endpoint.profile, body))
    }
  }

  private func countUnsent() async {
    unsent = ((try? session.sync.queue.all()) ?? []).count   // everything not on the server yet
  }
}

enum MeSheet: Identifiable, Equatable {
  case profile, ai, notifications, whatsNew, dataStatus, appearance, unsent, bedtimeGoal, licences
  case records(RecordKind)

  enum RecordKind: String { case supplements, labs, events }

  var id: String {
    if case .records(let k) = self { return "records.\(k.rawValue)" }
    return "\(self)"
  }
}

private struct MeSheetView: View {
  let sheet: MeSheet
  @Environment(\.dismiss) private var dismiss

  var body: some View {
    SheetContainer(title: title, close: { dismiss() }) {
      switch sheet {
      case .profile: ProfileSetupView(first: false) { dismiss() }
      case .ai: AISettingsView()
      case .notifications: NotificationSettingsView()
      case .whatsNew: WhatsNewView()
      case .dataStatus: DataStatusView()
      case .appearance: AppearanceView()
      case .unsent: UnsentChangesView()
      case .bedtimeGoal: BedtimeGoalView()                                                // 2.0.0
      case .licences: LicensesView()
      case .records(let k): HealthRecordView(kind: k)                                    // N5
      }
    }
    .presentationDetents([.large])
    .presentationDragIndicator(.visible)
  }

  private var title: String {
    switch sheet {
    case .profile: return tr("个人资料", "Profile")
    case .ai: return tr("AI 设置", "AI settings")
    case .notifications: return tr("通知", "Notifications")
    case .whatsNew: return tr("更新记录", "What's new")
    case .dataStatus: return tr("数据状态", "Data status")
    case .appearance: return tr("外观", "Appearance")
    case .unsent: return tr("没提交的修改", "Unsent changes")
    case .bedtimeGoal: return tr("入睡目标", "Bedtime goal")
    case .licences: return tr("开源许可", "Open-source licences")
    case .records(.supplements): return tr("补剂", "Supplements")
    case .records(.labs): return tr("化验", "Lab results")
    case .records(.events): return tr("身体事件", "Body events")
    }
  }
}
