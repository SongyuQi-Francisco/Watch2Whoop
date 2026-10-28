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
    .overlay { JarvisLayer(tab: $selection) }                                             // N6
    .environment(\.askAI, AskAIAction { p in Task { @MainActor in JarvisRouter.ask(prompt: p) } })   // N6
    .onChange(of: session.circlesTab) { on in
      if selection == .circles && !on { selection = .today }
      if selection == .food && on { selection = .record }
    }
  }

  // Each tab is owned by one page agent (docs/ios-native-app.md section 9); they replace their placeholder here.
  @ViewBuilder private func screen(_ tab: AppTab) -> some View {
    switch tab {
    case .today: TodayView(session: session)                                              // N3
    case .trends: TrendsView(session: session)                                            // N4
    case .circles: CirclesTab()                                                           // N7
    case .food: PlaceholderTab(tab: tab, owner: "N5")
    case .record: PlaceholderTab(tab: tab, owner: "N5")
    case .me: MeTab()                                                                     // N8
    }
  }
}
