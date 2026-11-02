import SwiftUI

/// The five tabs, same order and names as the Today page's tab bar (index.html `.tabbar`):
/// 今天 · 趋势 · 饮食 · 记录 · 我; with the `friends` flag on, 朋友 (circles) takes 饮食's place and 饮食 moves into 记录
/// (app.js applyTabs). Circles always stay a top-level tab (memory "Circles is a core feature").
enum AppTab: String, CaseIterable, Identifiable {
  case today, trends, circles, food, record, me
  var id: String { rawValue }

  var title: String {
    switch self {
    case .today: return tr("今天", "Today")
    case .trends: return tr("趋势", "Trends")
    case .circles: return tr("朋友", "Friends")
    case .food: return tr("饮食", "Food")
    case .record: return tr("记录", "Log")
    case .me: return tr("我", "Me")
    }
  }

  var icon: Icon.Name {
    switch self {
    case .today: return .circleGauge
    case .trends: return .chartLine
    case .circles: return .users
    case .food: return .soup
    case .record: return .squarePlus
    case .me: return .user
    }
  }

  /// The tabs shown, left to right.
  static func visible(circles: Bool) -> [AppTab] {
    [.today, .trends, circles ? .circles : .food, .record, .me]
  }
}

struct MainTabView: View {
  @EnvironmentObject private var session: AccountSession
  @SceneStorage("w2w.tab") private var selection: AppTab = .today

  var body: some View {
    TabView(selection: $selection) {
      ForEach(AppTab.visible(circles: session.circlesTab)) { tab in
        screen(tab)
          .tabItem {
            Label { Text(tab.title) } icon: { Image(tab.icon.assetName).renderingMode(.template) }
          }
          .tag(tab)
      }
    }
    .tint(Palette.text)
    .circleRouting(selection: $selection)                                                  // N7: notification / link → 朋友
    .inboxRouting(selection: $selection)                                                   // #359: push / link → 我 → 消息
    .overlay { JarvisLayer(tab: $selection) }                                             // N6
    .environment(\.askAI, AskAIAction { p in Task { @MainActor in JarvisRouter.ask(prompt: p) } })   // N6
    .devTab($selection)                                                                    // N5: Debug screenshots
    .fullScreenCover(item: $session.viewing) { p in                                        // someone else's pages, read-only
      MemberScreen(person: p, client: session.api.uncached(), tab: Self.devMemberTab, exit: { session.viewing = nil })
        .environmentObject(session)
    }
    .onChange(of: session.viewing) { p in if p == nil { Task { await session.refreshViewable() } } }
    .task { await openDevMember() }
    .onChange(of: session.circlesTab) { on in
      if selection == .circles && !on { selection = .today }
      if selection == .food && on { selection = .record }
    }
  }

  /// `SIMCTL_CHILD_W2W_DEV_MEMBER=<user id>[:today|trends|sleep|food|record|health]` (Debug, simulator screenshots): opens that
  /// person's read-only view, when the server says they share with me.
  private func openDevMember() async {
    #if DEBUG
    guard let v = RecordDevShot.value("W2W_DEV_MEMBER"), let id = Int(v.split(separator: ":")[0]) else { return }
    await session.refreshViewable()
    session.viewing = session.viewable.first { $0.id == id }
    #endif
  }

  private static var devMemberTab: MemberTab {
    #if DEBUG
    if let t = RecordDevShot.value("W2W_DEV_MEMBER")?.split(separator: ":").dropFirst().first.flatMap({ MemberTab(rawValue: String($0)) }) { return t }
    #endif
    return .today
  }

  // Each tab is owned by one page agent (docs/ios-native-app.md section 9); they replace their placeholder here.
  @ViewBuilder private func screen(_ tab: AppTab) -> some View {
    switch tab {
    case .today: TodayView(session: session)                                              // N3
    case .trends: TrendsView(session: session)                                            // N4
    case .circles: CirclesTab()                                                           // N7
    case .food: FoodTab(session: session)                                                 // N5
    case .record: RecordTab(session: session)                                             // N5
    case .me: MeTab()                                                                     // N8
    }
  }
}
