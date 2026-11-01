import SwiftUI
import UIKit

/// The sheets the Friends tab opens.
enum CircleSheet: Identifiable, Equatable {
  case manage
  case create
  case join(String)
  case invite(Int?)
  case accept(InboxInvite)
  case myAvatar

  var id: String {
    switch self {
    case .manage: return "manage"
    case .create: return "create"
    case .join(let c): return "join:\(c)"
    case .invite(let c): return "invite:\(c.map(String.init) ?? "")"
    case .accept(let i): return "accept:\(i.id)"
    case .myAvatar: return "avatar"
    }
  }
}

/// One sheet with its own navigation (my circles → a circle → invite / share settings).
struct CircleSheetView: View {
  @ObservedObject var store: CirclesStore
  let sheet: CircleSheet
  @Environment(\.dismiss) private var dismiss

  var body: some View {
    NavigationStack {
      ScrollView(.vertical, showsIndicators: false) {
        content.padding(.horizontal, Metrics.gutter).padding(.top, 8).padding(.bottom, 28)
      }
      .background(Palette.sheet.ignoresSafeArea())
      .navigationTitle(title)
      .navigationBarTitleDisplayMode(.inline)
      .toolbar {
        ToolbarItem(placement: .cancellationAction) {
          Button { dismiss() } label: { Icon(.x, size: 18).foregroundColor(Palette.text2) }
            .accessibilityLabel(tr("关闭", "Close"))
        }
      }
    }
    .tint(Palette.text)
    .presentationDragIndicator(.visible)
    .overlay(alignment: .top) { ToastOverlay(store: store) }
  }

  @ViewBuilder private var content: some View {
    switch sheet {
    case .manage: MyCirclesView(store: store)
    case .create: CreateCircleView(store: store)
    case .join(let code): JoinView(store: store, code: code, done: { dismiss() })
    case .accept(let inv): JoinView(store: store, code: "", invite: inv, done: { dismiss() })
    case .invite(let id): InvitePicker(store: store, circleId: id)
    case .myAvatar:
      AvatarEditor(store: store, title: tr("我的头像", "My picture"), url: store.overview?.me?.avatar,
                   name: store.me.name, circle: nil)
    }
  }

  private var title: String {
    switch sheet {
    case .manage: return tr("我的圈子", "My circles")
    case .create: return tr("建圈子", "New circle")
    case .join, .accept: return tr("加入圈子", "Join a circle")
    case .invite: return tr("邀请朋友", "Invite friends")
    case .myAvatar: return tr("我的头像", "My picture")
    }
  }
}

/// 邀请朋友 from the tab: straight to the circle when there is one I own, else pick one (or make one).
private struct InvitePicker: View {
  @ObservedObject var store: CirclesStore
  let circleId: Int?

  var body: some View {
    let owned = (store.list?.circles ?? []).filter(\.isOwner)
    if let id = circleId ?? (owned.count == 1 ? owned.first?.id : nil) {
      InviteView(store: store, circleId: id)
    } else if owned.isEmpty {
      CreateCircleView(store: store)
    } else {
      VStack(alignment: .leading, spacing: 10) {
        Text(tr("邀请到哪个圈子？", "Invite to which circle?")).font(Typo.bodyStrong).foregroundColor(Palette.text)
        RowGroup {
          ForEach(Array(owned.enumerated()), id: \.element.id) { i, c in
            if i > 0 { RowDivider(inset: 0) }
            NavigationLink { InviteView(store: store, circleId: c.id).padding(.horizontal, Metrics.gutter).navigationTitle(c.name).scrollable() } label: {
              CircleRowLabel(circle: c)
            }
            .buttonStyle(.plain)
          }
        }
      }
    }
  }
}

/// 我的圈子: invites addressed to me, my circles (pinned first, archived folded away), new / join, my picture.
struct MyCirclesView: View {
  @ObservedObject var store: CirclesStore
  @State private var query = ""
  @State private var showArchived = false
  @State private var sub: CircleSheet?

  var body: some View {
    let l = store.list
    VStack(alignment: .leading, spacing: 18) {
      if let l, !l.consentOk {
        Banner(text: tr("同意书更新了，先在「我」里确认", "The consent was updated: confirm it under Me first"), tone: .mid)
      }
      if l?.banned == true {
        Banner(text: tr("你已不能使用圈子", "You can no longer use circles"), tone: .bad)
      }
      if let invites = l?.invites, !invites.isEmpty { InviteInbox(store: store, invites: invites) { sub = .accept($0) } }
      if store.avatarsOn {
        Button { sub = .myAvatar } label: {
          HStack(spacing: 12) {
            AvatarView(url: store.overview?.me?.avatar, name: store.me.name, size: 44)
            VStack(alignment: .leading, spacing: 2) {
              Text(tr("我的头像", "My picture")).font(Typo.bodyStrong).foregroundColor(Palette.text)
              Text(tr("圈子里的人看得到", "People in your circles see it")).font(Typo.caption).foregroundColor(Palette.text2)
            }
            Spacer()
            Icon(.chevronRight, size: 16).foregroundColor(Palette.text3)
          }
          .padding(14)
          .background(RoundedRectangle(cornerRadius: Metrics.cardRadius, style: .continuous).fill(Palette.surface))
        }
        .buttonStyle(PressScale())
      }
      if let l {
        let (shown, archived) = l.ordered
        if l.circles.count >= l.searchFrom {
          FormField(label: "", text: $query, placeholder: tr("搜索圈子 / 成员", "Search circles / members"))
        }
        if l.circles.isEmpty {
          Text(tr("还没有圈子", "No circles yet")).font(Typo.body).foregroundColor(Palette.text2)
            .frame(maxWidth: .infinity).padding(.vertical, 20)
        } else {
          RowGroup(title: tr("\(l.circles.count) 个圈子", "\(l.circles.count) circles")) {
            ForEach(Array(filter(shown).enumerated()), id: \.element.id) { i, c in
              if i > 0 { RowDivider(inset: 0) }
              link(c)
            }
          }
          if !archived.isEmpty {
            Button { withAnimation { showArchived.toggle() } } label: {
              HStack {
                Text(tr("已归档 (\(archived.count))", "Archived (\(archived.count))")).font(Typo.caption).foregroundColor(Palette.text2)
                Icon(.chevronDown, size: 14).foregroundColor(Palette.text3).rotationEffect(.degrees(showArchived ? 180 : 0))
              }
            }
            .buttonStyle(.plain)
            if showArchived || !query.isEmpty {
              RowGroup {
                ForEach(Array(filter(archived).enumerated()), id: \.element.id) { i, c in
                  if i > 0 { RowDivider(inset: 0) }
                  link(c)
                }
              }
            }
          }
        }
      } else {
        ProgressView().tint(Palette.text2).frame(maxWidth: .infinity).padding(.vertical, 30)
      }
      HStack(spacing: 10) {
        SecondaryButton(icon: .plus, title: tr("建圈子", "New circle")) { sub = .create }
        SecondaryButton(icon: .logIn, title: tr("用邀请码加入", "Join with a code")) { sub = .join("") }
      }
      RowGroup {
        NavigationLink {
          BlockedListView(store: store).navigationTitle(tr("已屏蔽", "Blocked")).scrollable()
        } label: {
          HStack(spacing: 12) {
            Icon(.ban, size: 20).foregroundColor(Palette.text2)
            Text(tr("已屏蔽", "Blocked")).font(Typo.body).foregroundColor(Palette.text)
            Spacer(minLength: 8)
            if let n = l?.blockedCount, n > 0 { Text("\(n)").font(Typo.caption).foregroundColor(Palette.text2) }
            Icon(.chevronRight, size: 16).foregroundColor(Palette.text3)
          }
          .padding(.vertical, 12)
          .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
      }
    }
    .task { await store.refreshList() }
    .sheet(item: $sub) { s in CircleSheetView(store: store, sheet: s).environmentObject(store.session) }
  }

  private func link(_ c: FriendCircle) -> some View {
    NavigationLink { CircleDetailView(store: store, circleId: c.id) } label: { CircleRowLabel(circle: c, prefs: store.prefsOn) }
      .buttonStyle(.plain)
  }

  private func filter(_ cs: [FriendCircle]) -> [FriendCircle] {
    let q = query.trimmingCharacters(in: .whitespaces).lowercased()
    guard !q.isEmpty else { return cs }
    return cs.filter { c in
      ([c.name] + c.members.flatMap { [$0.displayName, $0.remark ?? ""] }).contains { $0.lowercased().contains(q) }
    }
  }
}

/// Invites addressed to me: join (pick what to share) or ignore.
private struct InviteInbox: View {
  @ObservedObject var store: CirclesStore
  let invites: [InboxInvite]
  let accept: (InboxInvite) -> Void

  var body: some View {
    VStack(alignment: .leading, spacing: 8) {
      HStack {
        SectionHeader(title: tr("邀请 (\(invites.count))", "Invites (\(invites.count))"))
        if invites.count > 1 && store.prefsOn {
          Button(tr("全部忽略", "Ignore all")) { Task { await store.declineAll() } }
            .font(.system(size: 13, weight: .medium)).foregroundColor(Palette.text2)
        }
      }
      ForEach(invites) { i in
        let from = i.fromName ?? ""
        HStack(spacing: 10) {
          AvatarView(url: nil, name: i.name, size: 36)
          VStack(alignment: .leading, spacing: 2) {
            Text(tr("\(from) 邀请你加入「\(i.name)」", "\(from) invites you to \u{201C}\(i.name)\u{201D}"))
              .font(Typo.body).foregroundColor(Palette.text).fixedSize(horizontal: false, vertical: true)
            Text(tr("\(i.members) 人", "\(i.members) people")).font(Typo.caption).foregroundColor(Palette.text2)
          }
          Spacer(minLength: 4)
          Button(tr("加入", "Join")) { accept(i) }
            .font(.system(size: 14, weight: .semibold)).foregroundColor(Palette.bg)
            .padding(.horizontal, 14).frame(minHeight: 34).background(Capsule().fill(Palette.text))
            .buttonStyle(PressScale())
          Button(tr("忽略", "Ignore")) { Task { await store.decline(i.id) } }
            .font(.system(size: 14)).foregroundColor(Palette.text2)
        }
        .padding(12)
        .background(RoundedRectangle(cornerRadius: Metrics.tileRadius, style: .continuous).fill(Palette.surface))
      }
    }
  }
}

struct CircleRowLabel: View {
  let circle: FriendCircle
  var prefs = false

  var body: some View {
    let n = circle.members.count, shares = CircleScope.list(circle.myScopes)
    HStack(spacing: 12) {
      AvatarView(url: circle.avatar, name: circle.name, size: 36)
      VStack(alignment: .leading, spacing: 2) {
        HStack(spacing: 5) {
          Text(circle.name).font(Typo.bodyStrong).foregroundColor(Palette.text).lineLimit(1)
          if circle.isOwner { Icon(.crown, size: 13).foregroundColor(Palette.mid).accessibilityLabel(tr("圈主", "Owner")) }
          if prefs && circle.prefs.pinned { Icon(.pin, size: 13).foregroundColor(Palette.text3) }
          if prefs && circle.prefs.muted { Icon(.bellOff, size: 13).foregroundColor(Palette.text3) }
        }
        Text(tr("\(n) 人 · 我共享：\(shares)", "\(n) people · I share: \(shares)"))
          .font(Typo.caption).foregroundColor(Palette.text2).lineLimit(1)
      }
      Spacer(minLength: 4)
      Icon(.chevronRight, size: 16).foregroundColor(Palette.text3)
    }
    .padding(.vertical, 10)
    .contentShape(Rectangle())
  }
}

/// One circle: members (the owner removes, sees how each came in), what I share, invite, logo, pin / mute / archive,
/// rename, leave, dissolve.
struct CircleDetailView: View {
  @ObservedObject var store: CirclesStore
  let circleId: Int
  @Environment(\.dismiss) private var dismiss
  @State private var confirm: Confirm?
  @State private var renaming = false
  @State private var newName = ""

  enum Confirm: Identifiable {
    case remove(FriendCircle.Member), leave, dissolve, archive
    var id: String {
      switch self {
      case .remove(let m): return "remove:\(m.id)"
      case .leave: return "leave"
      case .dissolve: return "dissolve"
      case .archive: return "archive"
      }
    }
  }

  private var circle: FriendCircle? { store.list?.circles.first { $0.id == circleId } }

  var body: some View {
    ScrollView(.vertical, showsIndicators: false) {
      if let c = circle {
        VStack(alignment: .leading, spacing: 18) {
          header(c)
          RowGroup(title: tr("成员 \(c.members.count)", "Members \(c.members.count)")) {
            ForEach(Array(c.members.enumerated()), id: \.element.id) { i, m in
              if i > 0 { RowDivider(inset: 0) }
              memberRow(m, owner: c.isOwner)
            }
          }
          RowGroup(title: tr("我的设置", "My settings")) {
            NavigationLink { ScopesEditor(store: store, circle: c) } label: {
              settingRow(.users, tr("给圈子看", "What they see"), value: CircleScope.list(c.myScopes))
            }
            .buttonStyle(.plain)
            if store.prefsOn {
              RowDivider(inset: 0)
              ToggleRow(title: tr("置顶", "Pin"), isOn: Binding(get: { c.prefs.pinned }, set: { v in Task { await store.setPrefs(c.id, pinned: v) } }))
              RowDivider(inset: 0)
              ToggleRow(title: tr("静音", "Mute"), subtitle: tr("不再通知你这个圈子的点赞和评论", "No notifications from this circle"),
                        isOn: Binding(get: { c.prefs.muted }, set: { v in Task { await store.setPrefs(c.id, muted: v) } }))
              RowDivider(inset: 0)
              ToggleRow(title: tr("归档", "Archive"), subtitle: tr("从朋友页隐藏，仍在共享", "Hidden from Friends, still sharing"),
                        isOn: Binding(get: { c.prefs.archived }, set: { v in
                          if v { confirm = .archive } else { Task { await store.setPrefs(c.id, archived: false) } }
                        }))
            }
          }
          if c.isOwner {
            RowGroup(title: tr("圈主", "Owner")) {
              NavigationLink {
                InviteView(store: store, circleId: c.id).padding(.horizontal, Metrics.gutter).navigationTitle(tr("邀请朋友", "Invite friends")).scrollable()
              } label: {
                settingRow(.userPlus, tr("邀请朋友", "Invite friends"),
                           value: (c.invites?.live ?? 0) > 0 ? tr("\(c.invites!.live) 个有效", "\(c.invites!.live) live") : nil)
              }
              .buttonStyle(.plain)
              RowDivider(inset: 0)
              Button { newName = c.name; renaming = true } label: { settingRow(.settings, tr("改名", "Rename"), value: nil) }
                .buttonStyle(.plain)
              if store.avatarsOn && !c.personal {
                RowDivider(inset: 0)
                NavigationLink {
                  AvatarEditor(store: store, title: tr("圈子 logo", "Circle logo"), url: c.avatar, name: c.name, circle: c.id)
                    .padding(.horizontal, Metrics.gutter).navigationTitle(tr("圈子 logo", "Circle logo")).scrollable()
                } label: { settingRow(.image, tr("圈子 logo", "Circle logo"), value: nil) }
                .buttonStyle(.plain)
              }
            }
          }
          VStack(spacing: 10) {
            SecondaryButton(icon: .logOut, title: tr("退出圈子", "Leave circle"), tone: .bad) { confirm = .leave }
            if c.isOwner {
              SecondaryButton(icon: .trash, title: tr("解散圈子", "Dissolve circle"), tone: .bad) { confirm = .dissolve }
            }
          }
        }
        .padding(.horizontal, Metrics.gutter).padding(.vertical, 12)
      } else {
        Text(tr("这个圈子已经不在了", "This circle is gone")).font(Typo.body).foregroundColor(Palette.text2).padding(40)
      }
    }
    .background(Palette.sheet.ignoresSafeArea())
    .navigationTitle(circle?.name ?? "")
    .navigationBarTitleDisplayMode(.inline)
    .alert(tr("改名", "Rename"), isPresented: $renaming) {
      TextField(tr("圈子名称", "Circle name"), text: $newName)
      Button(tr("保存", "Save")) { Task { _ = await store.rename(circleId, newName) } }
      Button(tr("取消", "Cancel"), role: .cancel) {}
    }
    .confirmationDialog(confirmTitle, isPresented: Binding(get: { confirm != nil }, set: { if !$0 { confirm = nil } }), titleVisibility: .visible) {
      Button(confirmAction, role: .destructive) { run() }
      Button(tr("取消", "Cancel"), role: .cancel) {}
    } message: { Text(confirmMessage) }
  }

  private func header(_ c: FriendCircle) -> some View {
    HStack(spacing: 14) {
      AvatarView(url: c.avatar, name: c.name, size: 56)
      VStack(alignment: .leading, spacing: 3) {
        Text(c.name).font(.system(size: 20, weight: .bold)).foregroundColor(Palette.text)
        Text(tr("\(c.members.count) 人", "\(c.members.count) people") + (c.isOwner ? tr(" · 我是圈主", " · I own it") : ""))
          .font(Typo.caption).foregroundColor(Palette.text2)
      }
    }
  }

  private func memberRow(_ m: FriendCircle.Member, owner: Bool) -> some View {
    HStack(spacing: 10) {
      AvatarView(url: m.avatar, name: m.displayName, size: 34)
      VStack(alignment: .leading, spacing: 2) {
        HStack(spacing: 5) {
          Text(m.me ? tr("我", "Me") : m.shownName).font(Typo.body).foregroundColor(Palette.text).lineLimit(1)
          if m.owner { Icon(.crown, size: 13).foregroundColor(Palette.mid) }
        }
        if let line = joinLine(m) { Text(line).font(.system(size: 12)).foregroundColor(Palette.text2).lineLimit(1) }
      }
      Spacer()
      if m.blocked { Chip(label: tr("已屏蔽", "Blocked"), tone: .text) }
      if !m.me {
        Menu {
          if owner { Button(role: .destructive) { confirm = .remove(m) } label: { Text(tr("移出圈子", "Remove from circle")) } }
          if m.blocked {
            Button(tr("取消屏蔽", "Unblock")) { Task { await store.unblock(m.id) } }
          } else {
            ModerationItems(store: store, kind: .profile, target: m.id, user: m.id)
          }
        } label: {
          Icon(.ellipsis, size: 18).foregroundColor(Palette.text2).frame(width: 32, height: 32).contentShape(Rectangle())
        }
        .accessibilityLabel(tr("更多", "More"))
      }
    }
    .padding(.vertical, 8)
  }

  /// How a member came in (owner only, flag circle_join_trail).
  private func joinLine(_ m: FriendCircle.Member) -> String? {
    guard let j = m.join else { return nil }
    let me = j.by?.me == true
    let who = j.by.map { ($0.remark ?? $0.displayName) ?? "" } ?? tr("已注销的用户", "Deleted user")
    let code = j.code ?? ""
    let how: String
    switch j.via {
    case "created": how = tr("建圈", "Created it")
    case "invite_code": how = me ? tr("我邀请的 · 邀请链接", "Invited by me · link") : tr("由 \(who) 邀请 · 邀请链接", "Invited by \(who) · link")
    case "referral": how = me ? tr("用了我的推荐码", "Used my referral code") : tr("用了 \(who) 的推荐码", "Used \(who)'s referral code")
    case "circle_code": how = tr("圈子激活码「\(code)」", "Circle code \u{201C}\(code)\u{201D}")
    default: how = tr("来源未记录", "Not recorded")
    }
    guard let at = m.joinedAt else { return how }
    let day = FeedFormat.day(String(at.prefix(10)))
    return tr("\(how) · \(day) 入圈", "\(how) · joined \(day)")
  }

  private func settingRow(_ icon: Icon.Name, _ title: String, value: String?) -> some View {
    HStack(spacing: 12) {
      Icon(icon, size: 20).foregroundColor(Palette.text2)
      Text(title).font(Typo.body).foregroundColor(Palette.text)
      Spacer(minLength: 8)
      if let value { Text(value).font(Typo.caption).foregroundColor(Palette.text2).lineLimit(1) }
      Icon(.chevronRight, size: 16).foregroundColor(Palette.text3)
    }
    .padding(.vertical, 12)
    .contentShape(Rectangle())
  }

  private var confirmTitle: String {
    let name = circle?.name ?? ""
    switch confirm {
    case .remove(let m): return tr("把 \(m.displayName) 移出圈子？", "Remove \(m.displayName)?")
    case .leave: return tr("退出「\(name)」？", "Leave \u{201C}\(name)\u{201D}?")
    case .dissolve: return tr("解散「\(name)」？", "Dissolve \u{201C}\(name)\u{201D}?")
    case .archive: return tr("归档「\(name)」？", "Archive \u{201C}\(name)\u{201D}?")
    case nil: return ""
    }
  }

  private var confirmMessage: String {
    let shares = CircleScope.list(circle?.myScopes ?? [])
    switch confirm {
    case .remove: return tr("你们立即互相看不到。", "You stop seeing each other at once.")
    case .leave: return tr("圈子里的人立即看不到你的数据。", "Its members stop seeing your data at once.")
    case .dissolve: return tr("所有人立即互相看不到。", "Everyone stops seeing each other at once.")
    case .archive: return tr("归档后从朋友页隐藏，也不再通知你；但你仍在向圈里的人共享：\(shares)。想停止共享请退出圈子。", "It is hidden from Friends and stops notifying you, but you still share: \(shares). Leave the circle to stop sharing.")
    case nil: return ""
    }
  }

  private var confirmAction: String {
    switch confirm {
    case .remove: return tr("移除", "Remove")
    case .leave: return tr("退出", "Leave")
    case .dissolve: return tr("解散", "Dissolve")
    case .archive: return tr("归档", "Archive")
    case nil: return ""
    }
  }

  private func run() {
    guard let c = confirm else { return }
    Task {
      switch c {
      case .remove(let m): await store.remove(m.id, from: circleId)
      case .leave: if await store.leave(circleId) { dismiss() }
      case .dissolve: if await store.dissolve(circleId) { dismiss() }
      case .archive: await store.setPrefs(circleId, archived: true)
      }
    }
  }
}

/// Change what I share with one circle.
private struct ScopesEditor: View {
  @ObservedObject var store: CirclesStore
  let circle: FriendCircle
  @Environment(\.dismiss) private var dismiss
  @State private var chosen: Set<String> = []
  @State private var busy = false

  var body: some View {
    ScrollView {
      VStack(alignment: .leading, spacing: 16) {
        ScopePicker(all: store.list?.scopes ?? CircleScope.all, defaults: store.list?.defaultScopes ?? CircleScope.defaults, chosen: $chosen)
        PrimaryButton(title: tr("保存", "Save"), busy: busy) {
          Task {
            busy = true
            if await store.setScopes(circle.id, Array(chosen)) { store.toast = tr("已保存", "Saved"); dismiss() }
            busy = false
          }
        }
      }
      .padding(.horizontal, Metrics.gutter).padding(.vertical, 12)
    }
    .background(Palette.sheet.ignoresSafeArea())
    .navigationTitle(tr("给「\(circle.name)」看", "What \u{201C}\(circle.name)\u{201D} sees"))
    .navigationBarTitleDisplayMode(.inline)
    .onAppear { chosen = Set(circle.myScopes) }
  }
}

/// A new circle: its name and what I share there; then straight to its invite.
struct CreateCircleView: View {
  @ObservedObject var store: CirclesStore
  @State private var gateId = "create:\(UUID().uuidString)"
  @State private var name = ""
  @State private var chosen: Set<String> = Set(CircleScope.defaults)
  @State private var busy = false
  @State private var created: CreatedCircle?

  var body: some View {
    if let created {
      InviteView(store: store, circleId: created.circle.id, minted: created.invite)
    } else {
      VStack(alignment: .leading, spacing: 16) {
        FormField(label: tr("圈子名称", "Circle name"), text: $name, placeholder: tr("例如：家人 / 健身搭子", "e.g. Family / Gym buddies"), background: Palette.inSheet)
        Text(tr("给圈子里的人看", "What its members see")).font(Typo.bodyStrong).foregroundColor(Palette.text)
        ScopePicker(all: store.list?.scopes ?? CircleScope.all, defaults: store.list?.defaultScopes ?? CircleScope.defaults, chosen: $chosen)
        PrimaryButton(title: tr("建好并邀请", "Create and invite"), busy: busy) {
          let n = name.trimmingCharacters(in: .whitespacesAndNewlines)
          guard !n.isEmpty else { store.toast = tr("先起个名字", "Give it a name first"); return }
          store.requireRules(host: gateId) {   // the community rules first, once (flag circle_rules)
            Task {
              busy = true
              if let r = await store.create(name: String(n.prefix(40)), scopes: Array(chosen)) {
                UINotificationFeedbackGenerator().notificationOccurred(.success)
                withAnimation { created = r }
              }
              busy = false
            }
          }
        }
      }
      .rulesGate(store, host: gateId)
    }
  }
}

extension View {
  /// A pushed page in a sheet: scrolls, sheet background.
  func scrollable() -> some View {
    ScrollView(.vertical, showsIndicators: false) { self.padding(.vertical, 12) }
      .background(Palette.sheet.ignoresSafeArea())
      .navigationBarTitleDisplayMode(.inline)
  }
}

/// A short message at the top that goes away by itself.
struct ToastOverlay: View {
  @ObservedObject var store: CirclesStore

  var body: some View {
    Group {
      if let t = store.toast {
        Text(t).font(.system(size: 14, weight: .medium)).foregroundColor(Palette.bg)
          .padding(.horizontal, 16).padding(.vertical, 10)
          .background(Capsule().fill(Palette.text))
          .padding(.top, 8)
          .transition(.move(edge: .top).combined(with: .opacity))
          .task(id: t) {
            try? await Task.sleep(nanoseconds: 2_200_000_000)
            withAnimation { store.toast = nil }
          }
      }
    }
    .animation(.spring(response: 0.35, dampingFraction: 0.85), value: store.toast)
  }
}
