import SwiftUI

/// The Apple Watch app, phase 1 of docs/watch-app-plan.md: logging sets during a strength session. Everything else
/// (voice, meals, today at a glance, complications) comes in later phases.
@main
struct Watch2WhoopWatchApp: App {
  @StateObject private var model = WatchModel()

  var body: some Scene {
    WindowGroup {
      RootView()
        .environmentObject(model)
        .onAppear { model.start() }
    }
  }
}
