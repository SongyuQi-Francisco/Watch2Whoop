import SwiftUI

/// Starting → sign-in / connecting / the tabs.
struct RootView: View {
  @EnvironmentObject private var model: AppModel

  var body: some View {
    Group {
      switch model.phase {
      case .starting:
        Palette.bg.ignoresSafeArea()
      case .signedOut(let message):
        OnboardingView(message: message)                                                  // N8
      case .connecting(let message):
        ConnectingView(message: message)
      case .signedIn:
        if let session = model.session {
          MainTabView()
            .environmentObject(session)
            .modifier(SignedInFlows(session: session))                                     // N8
        }
      }
    }
    .id(model.langRevision)   // a language change redraws everything with the new `tr()`
    .background(Palette.bg.ignoresSafeArea())
  }
}

private struct ConnectingView: View {
  @EnvironmentObject private var model: AppModel
  let message: String?

  var body: some View {
    VStack(spacing: 16) {
      ProgressView().tint(Palette.text2)
      Text(message ?? tr("正在连接服务器", "Connecting to your server"))
        .font(Typo.caption).foregroundColor(Palette.text2).multilineTextAlignment(.center)
      if message != nil {
        Button(tr("重试", "Retry")) { Task { await model.connect() } }
          .font(Typo.bodyStrong).foregroundColor(Palette.text)
        Button(tr("退出登录", "Sign out")) { Task { await model.signOut() } }
          .font(Typo.caption).foregroundColor(Palette.text2)
      }
    }
    .padding(Metrics.gutter)
    .frame(maxWidth: .infinity, maxHeight: .infinity)
    .background(Palette.bg.ignoresSafeArea())
  }
}
