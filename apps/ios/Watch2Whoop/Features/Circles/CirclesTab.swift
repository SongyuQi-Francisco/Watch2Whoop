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
  @Environment(\.flowsSettled) private var settled
  @SceneStorage("w2w.tab") private var tab: AppTab = .today
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
              glance(proxy)
              // below the fold (vertical scroll only): the 2.0.0 feed, members and leaderboards
              modeBar.padding(.top, 110).id("circles.list")
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
        .statusBarCover()
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
      take(app.route, settled: settled, tab: tab)
      askPush(settled: settled, tab: tab)
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
    .onChange(of: app.route) { take($0, settled: settled, tab: tab) }
    // the new value as an argument: inside these closures the view's own `settled` is still the old one
    .onChange(of: settled) { on in take(app.route, settled: on, tab: tab); askPush(settled: on, tab: tab) }
    .onChange(of: tab) { t in take(app.route, settled: settled, tab: t); askPush(settled: settled, tab: t) }
    .onChange(of: phase) { p in if p == .active { Task { await store.refresh() } } }
  }

  /// The first visit here is when the question has a reason (likes, comments), never at launch (notify.js askOnce). iOS
  /// asks only once; after that this is a no-op.
  private func askPush(settled: Bool, tab: AppTab) {
    guard settled, tab == .circles, store.session.flags.contains("notifications") else { return }
    Task { await PushRegistration.shared.askOnFirstVisit(api: store.session.api) }
  }

  /// A route meant for this tab (a notification, a link): consumed here, once the tab is on screen. The tab bar builds this
  /// view before it is shown; taking the route then would leave `CircleRouting` nothing to switch to the tab for.
  private func take(_ r: AppModel.Route?, settled: Bool, tab: AppTab) {
    guard settled, tab == .circles else { return }
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

  /// 2.0.1 header (`06-circles`): the circle picker on the left (全部 / each circle, 我的圈子, 加入), the sync capsule right.
  /// No page title on a tab root.
  private var header: some View {
    HStack(spacing: 8) {
      Menu {
        if store.byCircle && !store.chips.isEmpty {
          Button { store.select(nil) } label: { Text(tr("全部圈子", "All circles")) }
          ForEach(store.chips) { c in Button { store.select(c.id) } label: { Text(c.name) } }
        }
        Button { sheet = .manage } label: { Text(tr("我的圈子", "My circles")) }
        Button { sheet = .join("") } label: { Text(tr("用邀请码加入", "Join with a code")) }
      } label: {
        HStack(spacing: 4) {
          Text(circleTitle).font(Typo.header).foregroundColor(Palette.text).lineLimit(1)
          Icon(.chevronDown, size: 13).foregroundColor(Palette.text2)
        }
        .frame(minHeight: 44)
        .contentShape(Rectangle())
      }
      .accessibilityLabel(tr("选择圈子", "Pick a circle"))
      if store.offline {
        Chip(label: tr("离线", "Offline"))
      } else if store.refreshing && store.list == nil {
        Chip(label: tr("正在更新", "Updating"))
      }
      Spacer(minLength: 0)
      TodaySyncCapsule(card: nil, page: store.session.sync, tap: { Task { _ = await HealthSyncService.shared.syncNow() } },
                       center: { app.route = .syncCenter })
    }
    .frame(height: 44)
  }

  private var circleTitle: String {
    if let id = store.circle, let c = store.chips.first(where: { $0.id == id }) { return c.name }
    return tr("全部圈子", "All circles")
  }

  /// The first glance: who trained today on the recovery dial, the group's sleep and strain, faces 动态 · 排行 · 邀请.
  @ViewBuilder private func glance(_ proxy: ScrollViewProxy) -> some View {
    let people = CircleGlanceRules.people(store.overview)
    let count = CircleGlanceRules.trained(people)
    let me = store.overview?.me
    let others = people.filter { !($0.isSelf || $0.shared == nil) }
    VStack(spacing: 0) {
      CircleDial(people: CircleGlanceRules.dial(people), trained: count.trained, total: count.total,
                 side: TodayGlancePage.dialSide) { p in openPerson(p.id, proxy) }
        .frame(maxWidth: .infinity)
        .padding(.top, 18)
      HStack(alignment: .top, spacing: 20) {
        groupReading(tr("平均睡眠", "Avg sleep"), people.map { $0.scores?.sleepScore }, others: others.map { $0.scores?.sleepScore },
                     mine: me?.scores?.sleepScore, max: 100, step: 10, digits: 0, tone: .sleep)
        groupReading(tr("平均负荷", "Avg strain"), people.map { $0.scores?.strain }, others: others.map { $0.scores?.strain },
                     mine: me?.scores?.strain, max: 21, step: 3, digits: 1, tone: .train)
      }
      .padding(.top, 40)
      faces(proxy).padding(.top, 44)
    }
  }

  @ViewBuilder private func groupReading(_ label: String, _ all: [Double?], others: [Double?], mine: Double?, max: Double,
                                         step: Double, digits: Int, tone: Tone) -> some View {
    if let mean = CircleGlanceRules.mean(all) {
      VStack(alignment: .leading, spacing: 8) {
        HStack(alignment: .firstTextBaseline, spacing: 6) {
          Text(TodayModel.fmt(mean, digits: digits)).font(Typo.reading).foregroundColor(Palette.text)
          Text(label).font(Typo.label).foregroundColor(Palette.text2)
        }
        GroupGauge(values: others.compactMap { $0 }, mine: mine, mean: mean, max: max, step: step, tone: tone)
      }
      .frame(maxWidth: .infinity, minHeight: 59, alignment: .topLeading)
      .accessibilityElement(children: .ignore)
      .accessibilityLabel("\(label) \(TodayModel.fmt(mean, digits: digits))")
    }
  }

  private func faces(_ proxy: ScrollViewProxy) -> some View {
    HStack(spacing: 8) {
      if store.feedOn {
        Button { show(.feed, proxy) } label: {
          Face(icon: .messagesSquare, label: tr("动态", "Feed"), number: "\(store.unread)", unit: tr("条未读", "unread"),
               dot: store.unread > 0 ? .bad : nil) { Color.clear }
        }
        .buttonStyle(PressScale())
      }
      if store.boardOn {
        let rank = CircleGlanceRules.myRank(store.board)
        Button { show(.board, proxy) } label: {
          Face(icon: .trophy, label: tr("排行", "Ranking"), number: rank.map { "\($0.rank)" } ?? "—",
               unit: rank.map { tr("/\($0.of) 名", "of \($0.of)") }, dimmed: rank == nil) {
            RankBars(bars: CircleGlanceRules.rankBars(store.board))
          }
        }
        .buttonStyle(PressScale())
      }
      Button { invite() } label: {
        Face(icon: .userPlus, label: tr("邀请", "Invite")) {
          HStack(spacing: 6) {
            Icon(.share, size: 16)
            Text(tr("分享", "Share")).font(.system(size: 15, weight: .semibold))
          }
          .foregroundColor(Palette.text)
          .frame(maxWidth: .infinity, minHeight: 32)
          .background(Capsule().fill(Palette.surface2))
        }
      }
      .buttonStyle(PressScale())
    }
  }

  private func invite() {
    sheet = (store.list?.circles.contains(where: \.isOwner) ?? false) ? .invite(nil) : .create
  }

  private func show(_ m: CirclesStore.Mode, _ proxy: ScrollViewProxy) {
    withAnimation(.easeOut(duration: 0.18)) { store.setMode(m) }
    if m == .feed { store.markSeen() }
    withAnimation { proxy.scrollTo("circles.list", anchor: .top) }
  }

  /// An avatar on the dial: that person's read-only pages when they share with me, else the member list.
  private func openPerson(_ id: Int, _ proxy: ScrollViewProxy) {
    if let p = store.session.viewable.first(where: { $0.id == id }) {
      store.session.viewing = p
    } else {
      show(.members, proxy)
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
  /// Switches the tab bar when a notification or a link points at a tab: to 朋友 for the circles (N7; the tab itself then
  /// opens the post or the join sheet), else to the tab it names (AppLink). One line in `MainTabView`.
  func circleRouting(selection: Binding<AppTab>) -> some View { modifier(CircleRouting(selection: selection)) }
}

private struct CircleRouting: ViewModifier {
  @EnvironmentObject private var app: AppModel
  @EnvironmentObject private var session: AccountSession
  @Environment(\.flowsSettled) private var settled
  @Binding var selection: AppTab

  func body(content: Content) -> some View {
    content
      .onAppear { follow(app.route, settled: settled) }   // a tap that launched the app came before the tabs
      .onChange(of: app.route) { follow($0, settled: settled) }
      // the new value as an argument: inside this closure the modifier's own `settled` is still the old one
      .onChange(of: settled) { on in follow(app.route, settled: on) }
  }

  private func follow(_ r: AppModel.Route?, settled: Bool) {
    guard settled else { return }
    switch r {
    case .circles?, .circleJoin?:
      if session.circlesTab { selection = .circles } else { app.route = nil }
    case .tab(let t)?:
      let d = t.destination(circles: session.circlesTab)
      if let seg = d.segment { JarvisPageLink.shared.segment = seg }   // 饮食 is a segment of 记录
      selection = d.tab
      app.route = nil
    default:
      break
    }
  }
}
