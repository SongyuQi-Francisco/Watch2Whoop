import SwiftUI

/// What follows a sign-in, over the tabs, only when needed (zero tutorial, each step once):
/// 1. the consent, when the server says it is required or was revised (text and version from `GET /v1/consent`);
///    nothing of the person's health data is processed before it;
/// 2. the profile (birthday, sex, height), when something is missing — skippable;
/// 3. Health access: every type in one sheet, then the first sync starts.
/// Also: `watch2whoop://sync` / `backfill` links open the sync center, the weekly message opens 更新记录, and a setup link
/// opened while signed in asks before switching accounts. Links wait until the steps above are over (`flowsSettled`): a
/// person who taps an invite link before signing in gets the join sheet after them.
struct SignedInFlows: ViewModifier {
  @ObservedObject var session: AccountSession
  @EnvironmentObject private var app: AppModel
  @StateObject private var flow = SignedInFlow()

  func body(content: Content) -> some View {
    content
      .environment(\.flowsSettled, flow.settled)
      .task { await flow.start(session: session) }
      .fullScreenCover(item: $flow.step) { step in
        Group {
          switch step {
          case .consent(let reconfirm): ConsentView(reconfirm: reconfirm) { await flow.consentDone() }
          case .profile: ProfileSetupView(first: true) { await flow.profileDone() }
          case .health: HealthAccessView { await flow.healthDone() }
          }
        }
        .environmentObject(session)
        .environmentObject(app)
      }
      .sheet(item: Binding(get: { flow.settled ? app.route.flatMap(RouteBox.sheet) : nil },
                           set: { if $0 == nil { app.route = nil } })) { box in
        Group {
          if box.route == .whatsNew {
            SheetContainer(title: tr("更新记录", "What's new"), close: { app.route = nil }) {
              WhatsNewView { app.handle(link: $0) }
            }
            .presentationDetents([.large])
            .presentationDragIndicator(.visible)
          } else {
            NavigationStack {
              SyncCenterView(startHistory: box.historyDays)
                .toolbar {
                  ToolbarItem(placement: .cancellationAction) {
                    Button(tr("完成", "Done")) { app.route = nil }.foregroundColor(Palette.text)
                  }
                }
            }
          }
        }
        .environmentObject(session)
        .environmentObject(app)
      }
      .modifier(SwitchAccountDialog())
      // A setup link opened while a step is showing: the dialog cannot present over the step's cover (N9: the link was
      // dropped without a word). The step steps aside until the question is answered, and comes back after 「取消」.
      .onReceive(app.$pendingSetup) { flow.hold($0 != nil) }
  }
}

/// "Sign in again with this setup info?" for a setup link opened while signed in.
private struct SwitchAccountDialog: ViewModifier {
  @EnvironmentObject private var app: AppModel

  func body(content: Content) -> some View {
    content
      .confirmationDialog(tr("用这个设置信息重新登录？", "Sign in again with this setup info?"),
                          isPresented: Binding(get: { app.pendingSetup != nil }, set: { if !$0 { app.pendingSetup = nil } }),
                          titleVisibility: .visible) {
        Button(tr("退出并重新登录", "Sign out and sign in again"), role: .destructive) {
          let info = app.pendingSetup
          Task {
            await app.signOut()
            app.pendingSetup = info   // the sign-in screen takes it from here
          }
        }
        Button(tr("取消", "Cancel"), role: .cancel) { app.pendingSetup = nil }
      } message: {
        Text(tr("这台手机上的数据会删掉，服务器上的不受影响。", "Data on this iPhone is removed; your server keeps everything."))
      }
  }
}

private struct RouteBox: Identifiable {
  let route: AppModel.Route
  var id: String { "\(route)" }
  /// The routes that open a sheet here: the sync center, 更新记录 (the circle routes belong to the Friends tab, N7; a tab
  /// route to the tab bar).
  static func sheet(_ r: AppModel.Route) -> RouteBox? {
    switch r {
    case .syncCenter, .history, .whatsNew: return RouteBox(route: r)
    default: return nil
    }
  }
  var historyDays: Int? {
    if case .history(let d) = route { return d }
    return nil
  }
}

@MainActor
final class SignedInFlow: ObservableObject {
  enum Step: Identifiable, Equatable {
    case consent(reconfirm: Bool)
    case profile
    case health
    var id: String {
      switch self {
      case .consent: return "consent"
      case .profile: return "profile"
      case .health: return "health"
      }
    }
  }

  @Published var step: Step?
  /// Nothing is still to come: links and first-visit questions may show now (`EnvironmentValues.flowsSettled`).
  @Published private(set) var settled = false
  private var showedStep = false
  /// A setup link is waiting for its question: the step that would show (or was showing) waits too.
  private var held = false
  private var heldStep: Step?
  private var session: AccountSession?
  private var profile: Profile?
  private var started = false
  /// The profile step is offered once per account on this phone ("later" counts).
  static func profileSeenKey(_ s: AccountSession) -> String { "w2w.native.profileSeen.\(s.config.userId ?? 0)" }

  func start(session: AccountSession) async {
    guard !started else { return }
    started = true
    self.session = session
    profile = try? await session.api.send(.get(Endpoint.profile), as: Profile.self)
    if let c = profile?.consent, c.needed {
      HealthSyncService.shared.consentRequired = c.required
      show(.consent(reconfirm: !c.required))
      return
    }
    await next(afterConsent: true)
  }

  private func show(_ s: Step) {
    showedStep = true
    if held { heldStep = s } else { step = s }
  }

  private func settle() async {
    if showedStep { try? await Task.sleep(nanoseconds: 500_000_000) }   // the last cover goes first
    settled = true
  }

  func hold(_ on: Bool) {
    guard on != held else { return }
    held = on
    if on {
      if let s = step { heldStep = s; step = nil }
    } else if let s = heldStep {
      heldStep = nil
      Task {
        try? await Task.sleep(nanoseconds: 450_000_000)   // the dialog goes first
        if !held { step = s }
      }
    }
  }

  func consentDone() async {
    HealthSyncService.shared.consentRequired = false
    step = nil
    await next(afterConsent: true)
  }

  func profileDone() async {
    if let s = session { UserDefaults.standard.set(true, forKey: Self.profileSeenKey(s)) }
    step = nil
    await next(afterConsent: false)
  }

  func healthDone() async {
    step = nil
    Task { await settle() }
    await HealthSyncService.shared.syncOnOpen(force: true)
  }

  private func next(afterConsent: Bool) async {
    guard let s = session else { await settle(); return }
    if afterConsent, let p = profile, !p.missing.isEmpty, !UserDefaults.standard.bool(forKey: Self.profileSeenKey(s)) {
      try? await Task.sleep(nanoseconds: 350_000_000)   // let the previous cover go first
      show(.profile)
      return
    }
    #if DEBUG
    // simulator screenshots: `SIMCTL_CHILD_W2W_DEV_SKIP_HEALTH=1` leaves the Health sheet out (Debug builds only)
    if ProcessInfo.processInfo.environment["W2W_DEV_SKIP_HEALTH"] != nil { await settle(); return }
    #endif
    await HealthSyncService.shared.refreshPermission()
    if HealthSyncService.shared.permission == .notAsked {
      try? await Task.sleep(nanoseconds: 350_000_000)
      show(.health)
    } else {
      await settle()
    }
  }
}
