import SwiftUI

/// The tab bar of 2.0.1 (docs/design/2.0.1 direction.md 4): 今天 · 身体 · 记录 · 圈子 · 我, plus the Jarvis key. 身体 is the
/// trends page (`trends`), 记录 holds 训练 and 饮食 (饮食 is a segment of it, never a tab of its own), 圈子 is a top-level
/// tab while the `friends` flag is on (off: four tabs). `food` stays only so old links and saved state still land: it
/// always lands on 记录 (`landing`).
enum AppTab: String, CaseIterable, Identifiable {
  case today, trends, circles, food, record, me
  var id: String { rawValue }

  var title: String {
    switch self {
    case .today: return tr("今天", "Today")
    case .trends: return tr("身体", "Body")
    case .circles: return tr("圈子", "Circles")
    case .food: return tr("饮食", "Food")
    case .record: return tr("记录", "Log")
    case .me: return tr("我", "Me")
    }
  }

  var icon: Icon.Name {
    switch self {
    case .today: return .circleGauge
    case .trends: return .activity
    case .circles: return .users
    case .food: return .soup
    case .record: return .squarePlus
    case .me: return .user
    }
  }

  /// The tabs shown, left to right.
  static func visible(circles: Bool) -> [AppTab] {
    circles ? [.today, .trends, .record, .circles, .me] : [.today, .trends, .record, .me]
  }
}

struct MainTabView: View {
  @EnvironmentObject private var session: AccountSession
  @SceneStorage("w2w.tab") private var selection: AppTab = .today

  var body: some View {
    TabView(selection: $selection) {
      ForEach(AppTab.visible(circles: session.circlesTab)) { tab in
        TabRoot(tab: tab, session: session)
          .tabItem {
            Label { Text(tab.title) } icon: { Image(tab.icon.assetName).renderingMode(.template) }
          }
          .tag(tab)
      }
    }
    .tint(Palette.accent)
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
    .task { await devThemeFlip() }
    .task { await perfScript() }
    .onChange(of: session.circlesTab) { on in
      if selection == .circles && !on { selection = .today }
    }
    .onChange(of: selection) { s in if s == .food { selection = .record } }   // a saved or linked 饮食 → 记录
    .onAppear { if selection == .food { selection = .record } }
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

  /// `SIMCTL_CHILD_W2W_DEV_THEME_FLIP=<seconds>` (Debug, simulator screenshots): switches the theme that long after the tabs appear.
  private func devThemeFlip() async {
    #if DEBUG
    guard let s = RecordDevShot.value("W2W_DEV_THEME_FLIP").flatMap(Double.init) else { return }
    try? await Task.sleep(nanoseconds: UInt64(s * 1_000_000_000))
    try? await session.saveAppearance(AppearancePatch(theme: session.appearance?.isSlim == true ? "default" : "slim"))
    #endif
  }

  /// `SIMCTL_CHILD_W2W_PERF_SCRIPT=tabs` (Debug): steps through the tabs and logs how long each step keeps the main thread.
  private func perfScript() async {
    #if DEBUG
    await PerfScript.run(selection: $selection, tabs: AppTab.visible(circles: session.circlesTab))
    #endif
  }

  private static var devMemberTab: MemberTab {
    #if DEBUG
    if let t = RecordDevShot.value("W2W_DEV_MEMBER")?.split(separator: ":").dropFirst().first.flatMap({ MemberTab(rawValue: String($0)) }) { return t }
    #endif
    return .today
  }
}

/// One tab's page. Equatable on the tab alone (2.0.1): switching tabs re-runs MainTabView's body, and without this every
/// tab's page counted as changed (their state objects are built by closures, which never compare equal) and was built
/// again, the ones not on screen included. The pages still follow the session and their own stores as before.
private struct TabRoot: View, Equatable {
  let tab: AppTab
  let session: AccountSession

  static func == (a: TabRoot, b: TabRoot) -> Bool { a.tab == b.tab && a.session === b.session }

  // Each tab is owned by one page agent (docs/ios-native-app.md section 9); they replace their placeholder here.
  var body: some View {
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
