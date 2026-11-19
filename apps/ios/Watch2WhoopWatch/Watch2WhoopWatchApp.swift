import SwiftUI

/// The Apple Watch app (docs/watch-app-plan.md): logging sets, today at a glance, quick log. The watch-face complications
/// are the Watch2WhoopWatchWidgets extension; they read what this app stores (`WatchStore`).
@main
struct Watch2WhoopWatchApp: App {
  /// One model for the app's whole life: a plain property, not a `@StateObject`, because the background task below runs
  /// without any view (a state object is only kept once a view installs it).
  private let model = WatchModel()

  @ViewBuilder private var root: some View {
    #if DEBUG
    // simulator screenshots of every complication state: `SIMCTL_CHILD_W2W_WATCH_FACES=1 | circle | key | rect | rect2 | live`
    if let g = ProcessInfo.processInfo.environment["W2W_WATCH_FACES"], !g.isEmpty {
      FaceGallery(group: g)
    } else {
      RootView()
    }
    #else
    RootView()
    #endif
  }

  var body: some Scene {
    WindowGroup {
      root
        .environmentObject(model)
        .onAppear { model.start() }
        // a watch-face complication was tapped (Watch2WhoopWatchWidgets): its screen (WatchDeepLink)
        .onOpenURL { model.open($0) }
    }
    // the phone pushed while the app was not running: receive it, so the complications get today's numbers
    .backgroundTask(.watchConnectivity) { await model.receiveInBackground() }
  }
}
