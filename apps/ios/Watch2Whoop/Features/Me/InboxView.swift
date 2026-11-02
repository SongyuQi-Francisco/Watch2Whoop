import SwiftUI
import UIKit

/// 我 → 消息 (#359): notices, what's new, circle invites as cards, circle activity, newest first. Tap to read; swipe to hide.
/// Opens on the saved list, offline too; only joining and ignoring need a connection.
struct InboxView: View {
  @EnvironmentObject private var app: AppModel
  @EnvironmentObject private var session: AccountSession
  @ObservedObject var store: InboxStore
  @ObservedObject var circles: CirclesStore
  @State private var detail: InboxItem?
  @State private var consentFor: InboxItem?
  @State private var joining: Set<String> = []
  @State private var muteFor: InboxItem?
  @State private var gateId = "inbox:\(UUID().uuidString)"

  var body: some View {
    ScrollViewReader { proxy in
      List {
        if store.offline {
          Text(tr("离线 · 显示上次的内容", "Offline · showing what was saved")).font(Typo.overline).foregroundColor(Palette.mid)
            .inboxRow()
        }
        if store.items.isEmpty {
          empty.inboxRow()
        }
        ForEach(store.items) { item in
          row(item)
            .id(item.id)
            .inboxRow()
            .swipeActions(edge: .trailing, allowsFullSwipe: true) {
              Button { withAnimation { store.hide(item) } } label: {
                Text(tr("隐藏", "Hide"))
              }
              .tint(Palette.bad)
              if !item.read {
                Button { store.markRead(item) } label: { Text(tr("已读", "Read")) }.tint(Palette.text2)
              }
            }
            .onAppear { if item.id == store.items.last?.id { Task { await store.loadMore() } } }
        }
      }
      .listStyle(.plain)
      .scrollContentBackground(.hidden)
      .background(Palette.bg.ignoresSafeArea())
      .refreshable { await store.refresh(force: true) }
      .onChange(of: store.focus) { _ in take(proxy) }
      .onChange(of: store.items.map(\.id)) { _ in take(proxy) }
      .onAppear { take(proxy) }
    }
    .navigationTitle(tr("消息", "Messages"))
    .navigationBarTitleDisplayMode(.inline)
    .toolbar {
      ToolbarItem(placement: .navigationBarTrailing) {
        if store.unread > 0 {
          Button(tr("全部已读", "Mark all read")) { store.markAllRead() }
            .font(Typo.caption).foregroundColor(Palette.text)
        }
      }
    }
    .overlay(alignment: .top) { ToastOverlay(store: circles) }
    .task { await store.refresh(force: true) }
    .rulesGate(circles, host: gateId)
    .sheet(item: $detail) { InboxDetail(item: $0, open: open) }
    .fullScreenCover(item: $consentFor) { item in
      ConsentView(reconfirm: true) {
        consentFor = nil
        store.consentGiven()
        var again = item
        again.invite?.consentOk = true
        try? await Task.sleep(nanoseconds: 450_000_000)   // the cover goes first: the rules sheet may come next
        join(again)
      }
      .overlay(alignment: .topLeading) {
        Button { consentFor = nil } label: {
          Icon(.x, size: 18).foregroundColor(Palette.text2)
            .frame(width: 32, height: 32)
            .background(Circle().fill(Palette.surface))
        }
        .accessibilityLabel(tr("关闭", "Close"))
        .padding(.leading, Metrics.gutter).padding(.top, 6)
      }
      .environmentObject(session).environmentObject(app)
    }
    .confirmationDialog(tr("不再接收此人的邀请？", "No more invites from this person?"),
                        isPresented: Binding(get: { muteFor != nil }, set: { if !$0 { muteFor = nil } }), titleVisibility: .visible) {
      Button(tr("不再接收", "No more invites"), role: .destructive) {
        if let m = muteFor { Task { await store.mute(m) } }
      }
      Button(tr("取消", "Cancel"), role: .cancel) {}
    } message: {
      Text(tr("对方不会知道。可以在「我的圈子 → 已屏蔽」恢复。", "They won't know. Undo under My circles → Blocked."))
    }
    .onChange(of: store.toast) { t in
      if let t { circles.toast = t; store.toast = nil }
    }
  }

  private var empty: some View {
    VStack(spacing: 10) {
      Icon(.inbox, size: 36).foregroundColor(Palette.text3)
      Text(tr("还没有消息", "No messages yet")).font(Typo.body).foregroundColor(Palette.text2)
    }
    .frame(maxWidth: .infinity)
    .padding(.top, 80)
  }

  /// A push or a link asked for this row.
  private func take(_ proxy: ScrollViewProxy) {
    guard let id = store.focus, store.items.contains(where: { $0.id == id }) else { return }
    store.focus = nil
    withAnimation { proxy.scrollTo(id, anchor: .top) }
    store.markRead(id: id)
  }

  // MARK: - rows

  @ViewBuilder private func row(_ item: InboxItem) -> some View {
    if let card = item.invite {
      InviteCardRow(item: item, card: card, busy: joining.contains(item.id),
                    join: { join(item) }, ignore: { Task { await store.ignore(item) } }, mute: { muteFor = item },
                    read: { store.markRead(item) })
    } else {
      Button { tap(item) } label: { MessageRow(item: item) }
        .buttonStyle(PressScale())
    }
  }

  private func tap(_ item: InboxItem) {
    store.markRead(item)
    switch item.type {
    case "circle":
      if let c = item.circleId { circles.select(c) }
      if let u = item.url { app.handle(link: u) } else { app.route = .circles(post: nil) }
    default:
      detail = item
    }
  }

  /// A notice's button: a page of the app, a `watch2whoop://` link, or an https page in the browser.
  private func open(_ url: String) {
    if url.hasPrefix("/") {
      app.handle(link: url)
    } else if let u = URL(string: url) {
      if u.scheme == "watch2whoop" { app.handle(url: u) } else if u.scheme == "https" { UIApplication.shared.open(u) }
    }
  }

  // MARK: - joining: the consent and the community rules first when they are missing, then straight on

  private func join(_ item: InboxItem) {
    guard let card = item.invite else { return }
    if !card.consentOk {
      consentFor = item
      return
    }
    circles.requireRules(host: gateId) { Task { await runJoin(item) } }
  }

  private func runJoin(_ item: InboxItem, askedRules: Bool = false) async {
    joining.insert(item.id)
    defer { joining.remove(item.id) }
    switch await store.accept(item) {
    case .joined:
      UINotificationFeedbackGenerator().notificationOccurred(.success)
      circles.toast = tr("已加入", "Joined")
      await circles.reloadAll()
    case .needsConsent:
      consentFor = item
    case .needsRules:
      guard !askedRules else {   // accepted a moment ago and the server still asks: not a loop, a message
        circles.toast = tr("没能加入，稍后再试", "Couldn't join; try again shortly")
        return
      }
      await circles.refreshList()
      circles.requireRules(host: gateId) { Task { await runJoin(item, askedRules: true) } }
    case .gone:
      circles.toast = tr("这个邀请已失效", "This invite is no longer valid")
    case .refused(let why):
      circles.toast = why
    }
  }
}

private extension View {
  /// A list row without the list's look: the cards are the look.
  func inboxRow() -> some View {
    self.listRowBackground(Color.clear)
      .listRowSeparator(.hidden)
      .listRowInsets(EdgeInsets(top: 5, leading: Metrics.gutter, bottom: 5, trailing: Metrics.gutter))
  }
}

/// A notice, what's new, a system message or a circle interaction.
private struct MessageRow: View {
  let item: InboxItem

  var body: some View {
    HStack(alignment: .top, spacing: 12) {
      Icon(Self.icon(item), size: 20).foregroundColor(item.read ? Palette.text3 : Palette.text2).padding(.top, 1)
      VStack(alignment: .leading, spacing: 3) {
        Text(item.title).font(item.read ? Typo.body : Typo.bodyStrong).foregroundColor(Palette.text)
          .multilineTextAlignment(.leading).lineLimit(2)
        if let b = item.body, !b.isEmpty, item.type != "whatsnew" {
          Text(b).font(Typo.caption).foregroundColor(Palette.text2).multilineTextAlignment(.leading).lineLimit(2)
        }
        Text(InboxLogic.when(item.at)).font(Typo.label).foregroundColor(Palette.text3)
      }
      Spacer(minLength: 4)
      if !item.read { Circle().fill(Palette.bad).frame(width: 8, height: 8).padding(.top, 5) }
    }
    .padding(14)
    .frame(maxWidth: .infinity, alignment: .leading)
    .background(RoundedRectangle(cornerRadius: Metrics.cardRadius, style: .continuous).fill(Palette.surface))
    .contentShape(Rectangle())
    .accessibilityElement(children: .combine)
    .accessibilityValue(item.read ? "" : tr("未读", "Unread"))
  }

  static func icon(_ item: InboxItem) -> Icon.Name {
    switch item.type {
    case "notice": return .megaphone
    case "whatsnew": return .sparkles
    case "ai_card": return .sparkles
    case "circle":
      switch item.kind {
      case "circle.like": return .heart
      case "circle.comment": return .messageCircle
      case "circle.joined": return .userPlus
      case "circle.invite_accepted": return .circleCheck
      default: return .users
      }
    default: return .bell
    }
  }
}

/// An invite: who, how big, what joining shares; 加入 is one tap.
private struct InviteCardRow: View {
  let item: InboxItem
  let card: InboxItem.Card
  let busy: Bool
  let join: () -> Void
  let ignore: () -> Void
  let mute: () -> Void
  let read: () -> Void

  var body: some View {
    VStack(alignment: .leading, spacing: 12) {
      HStack(alignment: .top, spacing: 12) {
        AvatarView(url: card.avatar, name: card.circleName, size: 44)
        VStack(alignment: .leading, spacing: 2) {
          Text(card.circleName).font(.system(size: 16, weight: .semibold)).foregroundColor(Palette.text).lineLimit(2)
          Text(subtitle).font(Typo.caption).foregroundColor(Palette.text2).lineLimit(1)
        }
        Spacer(minLength: 4)
        if !item.read { Circle().fill(Palette.bad).frame(width: 8, height: 8).padding(.top, 5) }
        if card.canMute {
          Menu {
            Button(tr("不再接收此人的邀请", "No more invites from this person"), action: mute)
          } label: {
            Icon(.ellipsis, size: 18).foregroundColor(Palette.text2).frame(width: 28, height: 28).contentShape(Rectangle())
          }
          .accessibilityLabel(tr("更多", "More"))
        }
      }
      VStack(alignment: .leading, spacing: 6) {
        Text(card.shares.isEmpty ? tr("加入后不共享内容", "Joining shares nothing") : tr("加入后成员能看到", "Members will see"))
          .font(Typo.label).foregroundColor(Palette.text2)
        if !card.shares.isEmpty {
          ShareChips(labels: card.shares.map(\.label))
        }
        Text(tr("可随时修改或退出 · \(FeedFormat.timeLeft(card.expiresAt))", "Change or leave any time · \(FeedFormat.timeLeft(card.expiresAt))"))
          .font(Typo.label).foregroundColor(Palette.text3)
      }
      HStack(spacing: 10) {
        SecondaryButton(title: tr("忽略", "Ignore"), background: Palette.surface2, action: ignore)
        PrimaryButton(title: tr("加入", "Join"), busy: busy, action: join)
      }
    }
    .padding(14)
    .frame(maxWidth: .infinity, alignment: .leading)
    .background(RoundedRectangle(cornerRadius: Metrics.cardRadius, style: .continuous).fill(Palette.surface))
    .contentShape(Rectangle())
    .onTapGesture(perform: read)
  }

  private var subtitle: String {
    let n = InboxLogic.people(card.members)
    guard let o = card.ownerName, !o.isEmpty else { return n }
    return tr("\(o) 邀请你 · \(n)", "From \(o) · \(n)")
  }
}

/// The share labels, wrapping onto as many lines as they need.
private struct ShareChips: View {
  let labels: [String]

  var body: some View {
    FlowLayout(spacing: 6) {
      ForEach(labels, id: \.self) { l in
        Text(l).font(.system(size: 13, weight: .medium)).foregroundColor(Palette.text)
          .padding(.vertical, 5).padding(.horizontal, 10)
          .background(Capsule().fill(Palette.surface2))
      }
    }
  }
}

/// One message in full: a notice with its button, a week of what's new, a system message.
private struct InboxDetail: View {
  let item: InboxItem
  let open: (String) -> Void
  @Environment(\.dismiss) private var dismiss

  var body: some View {
    SheetContainer(title: item.title, close: { dismiss() }) {
      VStack(alignment: .leading, spacing: 14) {
        if !item.entries.isEmpty {
          RowGroup {
            ForEach(Array(item.entries.enumerated()), id: \.offset) { i, e in
              if i > 0 { RowDivider(inset: 0) }
              VStack(alignment: .leading, spacing: 2) {
                Text(e.text).font(Typo.body).foregroundColor(Palette.text).fixedSize(horizontal: false, vertical: true)
                Text(FeedFormat.day(e.date)).font(Typo.label).foregroundColor(Palette.text3)
              }
              .padding(.vertical, 10)
              .frame(maxWidth: .infinity, alignment: .leading)
            }
          }
        } else if let b = item.body, !b.isEmpty {
          Text(b).font(Typo.body).foregroundColor(Palette.text).fixedSize(horizontal: false, vertical: true)
        }
        Text(InboxLogic.when(item.at)).font(Typo.label).foregroundColor(Palette.text3)
        if item.type == "notice", let u = item.url, !u.isEmpty {
          PrimaryButton(title: item.linkLabel ?? tr("打开", "Open")) {
            dismiss()
            open(u)
          }
        }
      }
    }
    .presentationDetents([.medium, .large])
    .presentationDragIndicator(.visible)
  }
}

// MARK: - routing (MainTabView)

extension View {
  /// Switches the tab bar to 我 when a push or a link points into the inbox; the tab opens the page. One line in `MainTabView`.
  func inboxRouting(selection: Binding<AppTab>) -> some View { modifier(InboxRouting(selection: selection)) }
}

private struct InboxRouting: ViewModifier {
  @EnvironmentObject private var app: AppModel
  @EnvironmentObject private var session: AccountSession
  @Binding var selection: AppTab

  func body(content: Content) -> some View {
    content
      .onAppear { follow(app.route) }   // a tap that launched the app came before the tabs
      .onChange(of: app.route) { follow($0) }
  }

  private func follow(_ r: AppModel.Route?) {
    guard case .inbox? = r else { return }
    if session.flags.contains(InboxStore.flag) { selection = .me } else { app.route = nil }
  }
}
