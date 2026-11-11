import SwiftUI
import UIKit

/// The Jarvis key at the bottom centre, above the tab bar, and everything that grows out of it. MainTabView lays it over
/// the tabs: `.overlay { JarvisLayer(tab: $selection) }`.
///
/// 2.0.1: the key is the cockpit (Features/Jarvis/Cockpit, docs/design/2.0.1/jarvis-cockpit.html, approved 2026-11-12):
/// a tap fans out five panels (记一餐, 记训练, 说话, 拍照, 提问), 记一餐 and 记训练 open a second ring, the voice flow
/// listens with the words above the core and sends itself after a pause, and the reply unfolds as a card above the core.
/// A hold on the closed core talks at once (a question). Where the AI is not in the app (butler link, subscription, none)
/// a tap does what the Today page's key does (JarvisRouter.tapKey). The sheets (chat, 快记, camera) and the toast stay here.
struct JarvisLayer: View {
  @Binding var tab: AppTab
  @EnvironmentObject private var session: AccountSession
  @StateObject private var router = JarvisRouter()
  @StateObject private var cockpit = CockpitController(voice: JarvisVoice())

  static let keySize: CGFloat = 58

  var body: some View {
    GeometryReader { g in
      let c = Self.keyCenter(in: g.size)
      ZStack(alignment: .topLeading) {
        CockpitView(controller: cockpit, center: c, size: g.size)
        toast(c, width: g.size.width)
      }
      .frame(width: g.size.width, height: g.size.height, alignment: .topLeading)
    }
    .sheet(item: $router.sheet) { s in sheet(s) }
    .onAppear {
      router.attach(session)
      cockpit.attach(router: router, session: session)
      router.page = tab.rawValue
      router.selectTab = { t in tab = t }
      cockpit.voice.onInterrupted = {
        cockpit.close()
        router.show(tr("被系统打断，没有发送", "Interrupted by the system; nothing was sent"))
      }
      #if DEBUG
      JarvisDemo.run(router: router, cockpit: cockpit)
      #endif
    }
    .onChange(of: tab) { t in
      router.page = t.rawValue
      if cockpit.mode != .idle { cockpit.close() }
    }
    .onChange(of: router.sheet) { s in
      if s != nil && cockpit.mode != .idle { cockpit.close() }
    }
    // didBecomeActive, not willEnterForeground: a stream that drops while the app is only inactive (Control Center, a call
    // banner, the moment between background and active) is then picked up too, instead of waiting for the next return
    .onReceive(NotificationCenter.default.publisher(for: UIApplication.didBecomeActiveNotification)) { _ in
      Task { await router.chat?.resume() }
    }
    .onReceive(NotificationCenter.default.publisher(for: UIApplication.didEnterBackgroundNotification)) { _ in
      if cockpit.mode == .listen || cockpit.mode == .confirm { cockpit.close() }
    }
  }

  /// Above the tab bar, centred (the tab bar is 49 pt high before iOS 26; iOS 26's floating bar sits a little higher).
  static func keyCenter(in size: CGSize) -> CGPoint {
    var bar: CGFloat = 49
    if #available(iOS 26.0, *) { bar = 62 }
    return CGPoint(x: size.width / 2, y: size.height - bar - keySize / 2 - 10)
  }

  static func mmss(_ s: Double) -> String { "\(Int(s) / 60):" + String(format: "%02d", Int(s) % 60) }

  // MARK: - toast and sheets

  private var ink: Color { Palette.text }

  @ViewBuilder private func toast(_ c: CGPoint, width: CGFloat) -> some View {
    if let t = router.toast {
      Text(t).font(Typo.caption).foregroundColor(Palette.bg)
        .multilineTextAlignment(.center)
        .padding(.vertical, 10).padding(.horizontal, 16)
        .background(Capsule().fill(ink))
        .frame(maxWidth: width - 48)
        .position(x: c.x, y: c.y - Self.keySize / 2 - 34)
        .transition(.opacity.combined(with: .move(edge: .bottom)))
        .allowsHitTesting(false)
    }
  }

  @ViewBuilder private func sheet(_ s: JarvisRouter.Sheet) -> some View {
    switch s {
    case .chat:
      if let chat = router.chat {
        JarvisChatView(chat: chat, router: router).environmentObject(session)
      }
    case .quick:
      if let chat = router.chat {
        QuickLogSheet(chat: chat, router: router).environmentObject(session)
          .presentationDetents([.medium])
          .presentationDragIndicator(.visible)
      }
    case .mealPhoto:
      CockpitMealPhotoView(session: session, router: router) { router.sheet = nil }
        .presentationDetents([.large])
        .interactiveDismissDisabled()
    case .templates:
      CockpitTemplatesSheet(session: session) { router.sheet = nil }
        .presentationDetents([.medium, .large])
        .presentationDragIndicator(.visible)
    case .plan:
      CockpitPlanSheet(session: session, close: { router.sheet = nil }, start: {
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.35) { router.open(page: "record.training") }
      })
      .presentationDetents([.large])
    case .backfill(let o):
      CockpitBackfillSheet(client: ManualWorkoutAPI(api: session.api), options: o, today: session.db.userToday()) { router.sheet = nil }
        .presentationDetents([.large])
        .presentationDragIndicator(.visible)
    case .camera:
      CameraPicker { img in
        router.sheet = nil
        if let img { DispatchQueue.main.asyncAfter(deadline: .now() + 0.4) { router.sendPhoto(img) } } else { router.cancelPhoto() }
      }
      .ignoresSafeArea()
    }
  }
}

/// Loudness bars, newest on the right, one ink.
struct Waveform: View {
  let levels: [Double]
  var active = true

  var body: some View {
    GeometryReader { g in
      let n = levels.count, gap: CGFloat = 3
      let w = max(1, (g.size.width - gap * CGFloat(n - 1)) / CGFloat(n))
      HStack(alignment: .center, spacing: gap) {
        ForEach(0..<n, id: \.self) { i in
          Capsule().fill(Palette.text.opacity(active ? 1 : 0.3))
            .frame(width: w, height: max(3, g.size.height * CGFloat(0.08 + levels[i] * 0.92)))
        }
      }
      .frame(width: g.size.width, height: g.size.height)
      .animation(.linear(duration: 0.05), value: levels)
    }
    .accessibilityHidden(true)
  }
}

/// Feedback generators made once and warmed up when a finger lands on the key, so the first tick does not stall a frame.
@MainActor
enum JarvisHaptics {
  static let light = UIImpactFeedbackGenerator(style: .light)
  static let medium = UIImpactFeedbackGenerator(style: .medium)
  static let selection = UISelectionFeedbackGenerator()
  static let notice = UINotificationFeedbackGenerator()

  static func prepare() {
    light.prepare()
    medium.prepare()
    selection.prepare()
  }
}
