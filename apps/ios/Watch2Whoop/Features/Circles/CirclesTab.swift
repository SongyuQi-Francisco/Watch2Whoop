import SwiftUI
import UIKit

/// 朋友 (circles, #340 N7): the training feed, member cards and leaderboards of my circles, inviting friends and
/// managing circles, like the Today page's Friends tab (app.js RENDER_friends, circles.js, circle-feed.js). Opens on the
/// cached state; pull to refresh; the feed grows as it is scrolled. A red dot on the tab while someone liked or commented
/// on my training since I last looked.
struct CirclesTab: View {
  @EnvironmentObject private var session: AccountSession

  var body: some View {
    CirclesScreen(store: CirclesStore.shared(for: session))
  }
}

private struct CirclesScreen: View {
  @EnvironmentObject private var app: AppModel
  @ObservedObject var store: CirclesStore
  @State private var sheet: CircleSheet?
  @Environment(\.scenePhase) private var phase

  var body: some View {
    NavigationStack {
      ScrollViewReader { proxy in
        ScrollView(.vertical, showsIndicators: false) {
          VStack(alignment: .leading, spacing: 14) {
            header
            if let invites = store.list?.invites, let first = invites.first {
              let from = first.fromName ?? "", name = first.name
              Banner(text: invites.count == 1
                       ? tr("\(from) 邀请你加入「\(name)」", "\(from) invited you to \u{201C}\(name)\u{201D}")
                       : tr("你有 \(invites.count) 个圈子邀请", "You have \(invites.count) circle invites"),
                     tone: .good, actionTitle: tr("查看", "View")) {
                if invites.count == 1 { sheet = .accept(invites[0]) } else { sheet = .manage }
              }
            }
            if store.list != nil && !store.hasCircles {
              EmptyCircles(create: { sheet = .create }, join: { sheet = .join("") })
            } else {
              if store.byCircle && !store.chips.isEmpty { circleBar }
              modeBar
              switch store.mode {
              case .feed where store.feedOn:
                FeedList(store: store) { id in withAnimation { proxy.scrollTo(id, anchor: .top) } }
              case .board where store.boardOn:
                BoardView(store: store)
              default:
                MemberList(store: store)
              }
            }
          }
          .padding(.horizontal, Metrics.gutter)
          .padding(.bottom, 32)
          .frame(maxWidth: Metrics.maxContentWidth)
          .frame(maxWidth: .infinity)
        }
        .refreshable {
          await store.refresh(force: true)
          if store.mode == .feed { store.markSeen() }
        }
        .background(Palette.bg.ignoresSafeArea())
      }
      .toolbar(.hidden, for: .navigationBar)
    }
    .overlay(alignment: .top) { ToastOverlay(store: store) }
    .badge(store.unread)
    .sheet(item: $sheet) { s in
      CircleSheetView(store: store, sheet: s)
        .environmentObject(store.session)
        .presentationDetents(s == .manage ? [.large] : [.medium, .large])
    }
    .task {
      take(app.route)
      await store.refresh()
      if store.mode == .feed { store.markSeen() }
      // the red dot keeps up while the tab is open (circle-feed.js polls every minute)
      while !Task.isCancelled {
        try? await Task.sleep(nanoseconds: 60_000_000_000)
        guard phase == .active else { continue }
        await store.refreshUnread()
        if store.unread > 0 && store.mode == .feed { await store.refreshFeed(); store.markSeen() }
      }
    }
    .onChange(of: app.route) { take($0) }
    .onChange(of: phase) { p in if p == .active { Task { await store.refresh() } } }
  }

  /// A route meant for this tab (a notification, a link): consumed here.
  private func take(_ r: AppModel.Route?) {
    switch r {
    case .circles(let post)?:
      app.route = nil
      guard let post else { return }
      store.select(nil)   // the post may come through any circle
      store.setMode(.feed)
      store.focusPost = post
      Task { await store.refreshFeed() }
    case .circleJoin(let code)?:
      app.route = nil
      sheet = .join(code)
    default:
      break
    }
  }

  // MARK: parts

  private var header: some View {
    HStack(alignment: .center, spacing: 6) {
      VStack(alignment: .leading, spacing: 1) {
        if store.offline {
          Text(tr("离线 · 显示上次的内容", "Offline · showing what was saved")).font(Typo.overline).foregroundColor(Palette.mid)
        } else if store.refreshing && store.list == nil {
          Text(tr("正在更新", "Updating")).font(Typo.overline).foregroundColor(Palette.text2)
        }
        Text(tr("朋友", "Friends")).font(Typo.title).titleTracking().foregroundColor(Palette.text)
      }
      Spacer()
      iconButton(.userPlus, tr("邀请朋友", "Invite friends")) { sheet = (store.list?.circles.contains(where: \.isOwner) ?? false) ? .invite(nil) : .create }
      iconButton(.settings, tr("我的圈子", "My circles")) { sheet = .manage }
    }
    .padding(.top, 10)
  }

  private func iconButton(_ icon: Icon.Name, _ label: String, action: @escaping () -> Void) -> some View {
    Button(action: action) {
      Icon(icon, size: 20).foregroundColor(Palette.text)
        .frame(width: 40, height: 40)
        .background(Circle().fill(Palette.surface))
    }
    .buttonStyle(PressScale())
    .accessibilityLabel(label)
  }

  /// 全部 + one chip per circle (flag circles_by_circle): narrows the feed, the members and the leaderboards.
  private var circleBar: some View {
    ScrollView(.horizontal, showsIndicators: false) {
      HStack(spacing: 8) {
        PillButton(title: tr("全部", "All"), on: store.circle == nil) { store.select(nil) }
        ForEach(store.chips) { c in
          PillButton(title: c.name, avatar: (c.avatar, c.name), on: store.circle == c.id) { store.select(c.id) }
        }
      }
    }
  }

  private var modeBar: some View {
    HStack(spacing: 4) {
      ForEach(store.modes, id: \.self) { m in
        let on = store.mode == m
        Button {
          UISelectionFeedbackGenerator().selectionChanged()
          withAnimation(.easeOut(duration: 0.18)) { store.setMode(m) }
        } label: {
          HStack(spacing: 5) {
            Text(Self.title(m)).font(.system(size: 14, weight: on ? .semibold : .medium))
            if m == .feed && store.unread > 0 && !on { Circle().fill(Palette.bad).frame(width: 7, height: 7) }
          }
          .foregroundColor(on ? Palette.text : Palette.text2)
          .frame(maxWidth: .infinity, minHeight: 34)
          .background(RoundedRectangle(cornerRadius: 10, style: .continuous).fill(on ? Palette.bg : .clear))
        }
        .buttonStyle(.plain)
        .accessibilityAddTraits(on ? .isSelected : [])
      }
    }
    .padding(3)
    .background(RoundedRectangle(cornerRadius: 12, style: .continuous).fill(Palette.surface))
  }

  static func title(_ m: CirclesStore.Mode) -> String {
    switch m {
    case .feed: return tr("动态", "Feed")
    case .members: return tr("成员", "Members")
    case .board: return tr("排行榜", "Leaderboard")
    }
  }
}

private struct EmptyCircles: View {
  let create: () -> Void
  let join: () -> Void

  var body: some View {
    VStack(spacing: 14) {
      Icon(.users, size: 40).foregroundColor(Palette.text3).padding(.top, 30)
      Text(tr("和朋友一起练", "Train with friends")).font(.system(size: 20, weight: .bold)).foregroundColor(Palette.text)
      Text(tr("建一个圈子，邀请朋友或家人；只有你勾选的内容会给他们看", "Make a circle and invite friends or family; they see only what you tick"))
        .font(Typo.caption).foregroundColor(Palette.text2).multilineTextAlignment(.center)
      PrimaryButton(title: tr("建圈子", "New circle"), action: create).padding(.top, 6)
      SecondaryButton(icon: .logIn, title: tr("用邀请码加入", "Join with a code"), action: join)
    }
    .frame(maxWidth: .infinity)
    .padding(.horizontal, 8)
  }
}

// MARK: - routing (MainTabView)

extension View {
  /// Switches the tab bar to 朋友 when a notification or a link points into the circles (N7); the tab itself then
  /// opens the post or the join sheet. One line in `MainTabView`.
  func circleRouting(selection: Binding<AppTab>) -> some View { modifier(CircleRouting(selection: selection)) }
}

private struct CircleRouting: ViewModifier {
  @EnvironmentObject private var app: AppModel
  @EnvironmentObject private var session: AccountSession
  @Binding var selection: AppTab

  func body(content: Content) -> some View {
    content
      .onAppear { follow(app.route) }   // a tap that launched the app came before the tabs
      .onChange(of: app.route) { follow($0) }
  }

  private func follow(_ r: AppModel.Route?) {
    switch r {
    case .circles?, .circleJoin?:
      if session.circlesTab { selection = .circles } else { app.route = nil }
    default:
      break
    }
  }
}
