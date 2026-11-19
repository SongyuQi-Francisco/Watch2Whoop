import SwiftUI

/// The Apple Watch app of docs/watch-app-plan.md: logging sets (phase 1), today at a glance and quick log (2a), hold to
/// talk (2b). The watch-face widgets come later (phase 3).
@main
struct Watch2WhoopWatchApp: App {
  @StateObject private var model = WatchModel()

  var body: some Scene {
    WindowGroup {
      RootView()
        .environmentObject(model)
        .environmentObject(model.voice)
        .onAppear { model.start() }
    }
  }
}
