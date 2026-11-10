import SwiftUI

/// 我: the account card, then a short first level (MeMenu.swift: messages and sync, the data, the settings people change,
/// invites / help / account); the rest one tap further on a page of its own, pushed like the sync center. Settings are
/// online-only (docs/ios-native-app.md 5.5); each opens as a sheet, the sync center, the messages and the pages as pages.
/// The tab's badge is the unread messages (flag `inbox`).
struct MeTab: View {
  @EnvironmentObject private var session: AccountSession

  var body: some View {
    MeScreen(inbox: InboxStore.shared(for: session), circles: CirclesStore.shared(for: session))
  }
}

private struct MeScreen: View {
  @EnvironmentObject private var app: AppModel
  @EnvironmentObject private var session: AccountSession
  @Environment(\.openURL) private var openURL
  @ObservedObject var inbox: InboxStore
  let circles: CirclesStore
  @State private var openInbox = false
  @State private var sheet: MeSheet?
  @State private var handbook: URL?
  @State private var unsent = 0
  @State private var confirmSignOut = false
  @State private var confirmDelete = false
  @State private var deleteText = ""
  @State private var deleteError: String?
  @State private var busy = false
  /// Rows that depend on the server: friends can be invited, a code can still be entered (AccountViews.swift).
  @State private var referrals = false
  @State private var inviteOptional = false
  @State private var openingLink = false
  @State private var linkError: String?
  /// Debug screenshots: the page `W2W_DEV_ME_PAGE` pushes at launch (never set in Release).
  @State private var devPage: MePage?

  private var menu: MeMenu {
    let server = session.config.server
    return MeMenu(flags: session.flags, inbox: inbox.on, unread: inbox.unread, unsent: unsent, shared: session.viewable.count,
                  referrals: referrals, inviteOptional: inviteOptional,
                  handbook: HandbookLink.url(server: server) != nil,
                  privacy: LegalLinks.url(.privacy, server: server) != nil, terms: LegalLinks.url(.terms, server: server) != nil)
  }

  var body: some View {
    let menu = self.menu
    let groups = menu.firstLevel
    NavigationStack {
      ScrollViewReader { proxy in
      PageScaffold(title: tr("我", "Me")) {
        VStack(alignment: .leading, spacing: 18) {
          header
          if TodayHome.isOn(session.flags) { TodayHomeMeEntry() }   // flag ui_ai_first
          ForEach(Array(groups.enumerated()), id: \.offset) { i, g in
            if i == groups.count - 1 && !session.meLinks.isEmpty { links }   // the server's rows, before the last group
            MeRowGroup(group: g, menu: menu, perform: perform).id(i == groups.count - 1 ? "me-end" : "me-\(i)")
          }
        }
        .padding(.top, 8)
      }
      .task { await devShot(proxy) }
      }
      .toolbar(.hidden, for: .navigationBar)
      .navigationDestination(isPresented: $openInbox) { InboxView(store: inbox, circles: circles) }
      .navigationDestination(isPresented: Binding(get: { devPage != nil }, set: { if !$0 { devPage = nil } })) {
        if let devPage { MePageView(page: devPage, menu: menu, perform: perform) }
      }
    }
    .badge(inbox.unread)
    .onAppear { take(app.route) }
    #if DEBUG
    .onAppear {   // simulator screenshots
      if RecordDevShot.value("W2W_DEV_ME_SHEET") == "appearance" { sheet = .appearance }
      devPage = RecordDevShot.value("W2W_DEV_ME_PAGE").flatMap(MePage.init(rawValue:))
    }
    #endif
    .onChange(of: app.route) { take($0) }
    .task {
      await countUnsent()
      await session.refreshViewable()
      await loadAccountRows()
      await inbox.refresh()
    }
    .sheet(item: $sheet, onDismiss: { Task { await countUnsent(); await loadAccountRows() } }) { s in
      MeSheetView(sheet: s).environmentObject(session).environmentObject(app)
    }
    .sheet(isPresented: Binding(get: { handbook != nil }, set: { if !$0 { handbook = nil } })) {
      if let handbook {
        SafariView(url: handbook) { self.handbook = nil }.ignoresSafeArea()
      }
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
    .alert(linkError ?? "", isPresented: Binding(get: { linkError != nil }, set: { if !$0 { linkError = nil } })) {
      Button(tr("好", "OK"), role: .cancel) {}
    }
  }

  /// Debug screenshots (RecordDevShot): `W2W_DEV_SCROLL=me-links` brings the server's rows on screen (`me-end`: the bottom
  /// of the page), `W2W_DEV_MELINK=<key>` then does what a tap on that row does.
  private func devShot(_ proxy: ScrollViewProxy) async {
    guard RecordDevShot.value("W2W_DEV_SCROLL") != nil || RecordDevShot.value("W2W_DEV_MELINK") != nil else { return }
    try? await Task.sleep(nanoseconds: 2_500_000_000)
    if let anchor = RecordDevShot.value("W2W_DEV_SCROLL") { proxy.scrollTo(anchor, anchor: anchor == "me-end" ? .bottom : .center) }
    guard let key = RecordDevShot.value("W2W_DEV_MELINK"), let link = session.meLinks.first(where: { $0.key == key }) else { return }
    try? await Task.sleep(nanoseconds: 5_000_000_000)
    open(link)
  }

  /// Rows the server adds (`me_links`; none by default).
  private var links: some View {
    RowGroup {
      ForEach(Array(session.meLinks.enumerated()), id: \.element.id) { n, link in
        if n > 0 { RowDivider() }
        ListRow(icon: .externalLink, title: link.title) { open(link) }
      }
    }
    .id("me-links")
  }

  /// A tap on a row of 我 or of one of its pages (pages, the sync center and the language menu are links / menus of their own).
  private func perform(_ item: MeItem) {
    if let s = item.sheet { sheet = s; return }
    let server = session.config.server
    switch item {
    case .inbox: openInbox = true
    case .handbook: handbook = HandbookLink.url(server: server)
    case .privacy: LegalLinks.url(.privacy, server: server).map { openURL($0) }
    case .terms: LegalLinks.url(.terms, server: server).map { openURL($0) }
    case .signOut: confirmSignOut = true
    case .deleteAccount: deleteText = ""; deleteError = nil; confirmDelete = true
    default: break
    }
  }

  /// A row from the server: its address opens in the system browser (never in a web view inside the app).
  private func open(_ link: MeLink) {
    guard !openingLink else { return }
    openingLink = true
    Task {
      defer { openingLink = false }
      do {
        openURL(try await link.address(api: session.api))
      } catch let e as APIError {
        linkError = e.userMessage
      } catch {
        linkError = tr("出错了", "Something went wrong")
      }
    }
  }

  /// A push or a link into the inbox (InboxRouting brought the tab bar here).
  private func take(_ r: AppModel.Route?) {
    guard case .inbox(let item)? = r else { return }
    app.route = nil
    guard inbox.on else { return }
    inbox.focus = item
    openInbox = true
  }

  private var header: some View {
    Button { sheet = .profile } label: {
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
        Icon(.chevronRight, size: 16).foregroundColor(Palette.text3)
      }
      .padding(16)
      .background(RoundedRectangle(cornerRadius: Metrics.cardRadius, style: .continuous).fill(Palette.surface))
      .contentShape(Rectangle())
    }
    .buttonStyle(PressScale())
    .accessibilityLabel(tr("打开个人资料", "Open your profile"))
  }

  private func countUnsent() async {
    unsent = ((try? session.sync.queue.all()) ?? []).count   // everything not on the server yet
  }

  /// A failed read leaves the rows as they are: they only add something.
  private func loadAccountRows() async {
    if let m = try? await session.api.send(.get(Endpoint.authMethods), as: AuthService.Methods.self) { referrals = m.referrals }
    if let a = try? await session.api.send(.get(Endpoint.account), as: AccountInfo.self) { inviteOptional = a.inviteOptional }
  }
}

/// One card of 我's rows, on the first level or on a page.
private struct MeRowGroup: View {
  let group: MeMenuGroup
  let menu: MeMenu
  let perform: (MeItem) -> Void

  var body: some View {
    RowGroup(title: group.title) {
      ForEach(Array(group.items.enumerated()), id: \.element) { n, item in
        if n > 0 { RowDivider() }
        MeItemRow(item: item, menu: menu, perform: perform)
      }
    }
  }
}

/// One row: a page or the sync center is pushed, the language is a menu, the version is plain text, everything else is
/// handed to `perform` (a sheet, a confirmation, the browser).
private struct MeItemRow: View {
  let item: MeItem
  let menu: MeMenu
  let perform: (MeItem) -> Void
  @EnvironmentObject private var app: AppModel
  @EnvironmentObject private var session: AccountSession

  var body: some View {
    switch item {
    case .page(let p):
      NavigationLink { MePageView(page: p, menu: menu, perform: perform) } label: {
        MeRowLabel(icon: p.icon, title: p.title, value: p == .shared ? "\(menu.shared)" : nil)
      }
      .buttonStyle(PressScale())
    case .syncCenter:
      MeSyncRow()
    case .language:
      Menu {
        Button(tr("跟随系统", "System")) { setLang(nil) }
        Button("\u{4E2D}\u{6587}") { setLang("zh") }   // each language named in itself
        Button("English") { setLang("en") }
      } label: {
        MeRowLabel(icon: item.icon, title: item.title,
                   value: Lang.override == nil ? tr("跟随系统", "System") : (Lang.isZh ? "\u{4E2D}\u{6587}" : "English"))
      }
    case .version:
      ListRow(icon: item.icon, title: item.title, value: APIClient.appVersion)
    case .inbox:
      ListRow(icon: item.icon, title: item.title, value: menu.unread > 0 ? "\(menu.unread)" : nil, tone: .bad) { perform(item) }
    case .unsent:
      ListRow(icon: item.icon, title: item.title, value: "\(menu.unsent)", tone: .mid) { perform(item) }
    default:
      ListRow(icon: item.icon, title: item.title, tone: item == .deleteAccount ? .bad : .text) { perform(item) }
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
}

/// A row that is a link or a menu rather than a button: the same look as ListRow, the value in the caption size.
private struct MeRowLabel: View {
  let icon: Icon.Name
  let title: String
  var value: String?
  var tone: Tone = .text

  var body: some View {
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
}

/// 同步中心 with the state in a word; only this row follows the sync service.
private struct MeSyncRow: View {
  @ObservedObject private var sync = HealthSyncService.shared

  var body: some View {
    NavigationLink { SyncCenterView() } label: {
      MeRowLabel(icon: MeItem.syncCenter.icon, title: MeItem.syncCenter.title, value: sync.diagnosis.headline,
                 tone: tone(sync.diagnosis.overall))
    }
    .buttonStyle(PressScale())
  }

  private func tone(_ o: SyncDiagnosis.Overall) -> Tone {
    switch o {
    case .ok: return .good
    case .attention: return .mid
    default: return .text
    }
  }
}

/// A page of 我 (健康档案, 数据与导入, 偏好设置, 帮助与关于, 账号), or the people who share with me: a tap on one opens their
/// read-only view over the whole app, as before.
private struct MePageView: View {
  let page: MePage
  let menu: MeMenu
  let perform: (MeItem) -> Void
  @EnvironmentObject private var session: AccountSession

  var body: some View {
    ScrollView(.vertical, showsIndicators: false) {
      VStack(alignment: .leading, spacing: 18) {
        if page == .shared {
          people
        } else {
          ForEach(Array(menu.groups(page).enumerated()), id: \.offset) { _, g in
            MeRowGroup(group: g, menu: menu, perform: perform)
          }
        }
      }
      .padding(.horizontal, Metrics.gutter)
      .padding(.top, 8)
      .padding(.bottom, 32)
      .frame(maxWidth: Metrics.maxContentWidth)
      .frame(maxWidth: .infinity)
    }
    .background(Palette.bg.ignoresSafeArea())
    .navigationTitle(page.title)
    .navigationBarTitleDisplayMode(.inline)
    .task { if page == .shared { await session.refreshViewable() } }   // a share may have just started or ended
  }

  @ViewBuilder private var people: some View {
    if session.viewable.isEmpty {
      Text(tr("现在没有人共享给你", "Nobody shares with you right now"))
        .font(Typo.body).foregroundColor(Palette.text2)
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.top, 8)
    } else {
      RowGroup {
        ForEach(Array(session.viewable.enumerated()), id: \.element.id) { n, p in
          if n > 0 { RowDivider() }
          ListRow(icon: .users, title: p.shownName) { session.viewing = p }
        }
      }
    }
  }
}

enum MeSheet: Identifiable, Equatable {
  case profile, ai, notifications, whatsNew, dataStatus, appearance, unsent, bedtimeGoal, bodyAge, licences
  case memory, trainingGoals, signIn, invite, lateInvite, feedback, hevy
  case records(RecordKind)

  enum RecordKind: String { case supplements, labs, events }

  var id: String {
    if case .records(let k) = self { return "records.\(k.rawValue)" }
    return "\(self)"
  }
}

private struct MeSheetView: View {
  let sheet: MeSheet
  @EnvironmentObject private var app: AppModel
  @Environment(\.dismiss) private var dismiss

  var body: some View {
    SheetContainer(title: title, close: { dismiss() }, keyboardDone: keyboardDone) {
      switch sheet {
      case .profile: ProfileSetupView(first: false) { dismiss() }
      case .ai: AISettingsView()
      case .notifications: NotificationSettingsView()
      case .whatsNew: WhatsNewView { link in dismiss(); app.handle(link: link) }
      case .dataStatus: DataStatusView()
      case .appearance: AppearanceView()
      case .unsent: UnsentChangesView()
      case .bedtimeGoal: BedtimeGoalView()                                                // 2.0.0
      case .bodyAge: BodyAgeView()
      case .licences: LicensesView()
      case .memory: MemoryView()
      case .trainingGoals: TrainingSettingsView { dismiss() }
      case .signIn: SignInMethodsView()
      case .invite: InviteFriendsView()
      case .lateInvite: LateInviteView { dismiss() }
      case .feedback: FeedbackView()
      case .hevy: HevyImportView()
      case .records(let k): HealthRecordView(kind: k)                                    // N5
      }
    }
    .presentationDetents([.large])
    .presentationDragIndicator(.visible)
  }

  /// The forms with a number pad or a field that covers its own button.
  private var keyboardDone: Bool {
    switch sheet {
    case .profile, .signIn, .lateInvite, .trainingGoals: return true
    default: return false
    }
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
    case .bodyAge: return tr("身体年龄", "Body age")
    case .licences: return tr("开源许可", "Open-source licences")
    case .memory: return tr("长期记忆", "Long-term memory")
    case .trainingGoals: return tr("训练设置", "Training settings")
    case .signIn: return tr("登录方式", "Sign-in methods")
    case .invite: return tr("邀请好友", "Invite friends")
    case .lateInvite: return tr("填写邀请码", "Enter an invite code")
    case .feedback: return tr("反馈记录", "Feedback")
    case .hevy: return tr("从 Hevy 导入", "Import from Hevy")
    case .records(.supplements): return tr("补剂", "Supplements")
    case .records(.labs): return tr("化验", "Lab results")
    case .records(.events): return tr("身体事件", "Body events")
    }
  }
}
