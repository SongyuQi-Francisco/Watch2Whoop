import SwiftUI
import UIKit
import UserNotifications

@main
struct Watch2WhoopApp: App {
  @UIApplicationDelegateAdaptor(AppDelegate.self) private var appDelegate
  @StateObject private var model = AppModel()
  @Environment(\.scenePhase) private var scenePhase

  var body: some Scene {
    WindowGroup {
      RootView()
        .environmentObject(model)
        .task { await model.start() }
        .onOpenURL { model.handle(url: $0) }
    }
    .onChange(of: scenePhase) { phase in
      // Every return to the foreground: one sync round (the phone is unlocked, so Health can be read), at most every
      // 3 minutes (HealthSyncService.syncOnOpen); the engine's observers and BGTasks cover the time in between.
      guard phase == .active, model.phase == .signedIn else { return }
      Task { await HealthSyncService.shared.syncOnOpen() }
    }
  }
}

/// UIKit hooks the sync needs before any view exists. Both BGTask handlers must be registered before launch finishes;
/// HealthKit's observers are set up again on every launch, including the background launches HealthKit makes for them.
final class AppDelegate: NSObject, UIApplicationDelegate {
  func application(_ application: UIApplication,
                   didFinishLaunchingWithOptions launchOptions: [UIApplication.LaunchOptionsKey: Any]? = nil) -> Bool {
    // History backfill (Backfill.swift): its BGProcessingTask, and its background URLSession re-attached so iOS can
    // deliver uploads that finished while the app was not running.
    Backfill.register()
    Backfill.shared.attach()
    // The app's own sync engine (#219): BGAppRefresh `watch2whoop.sync.refresh`, BGProcessing `watch2whoop.sync.process`,
    // HealthKit observers + background delivery, unlock / network-back resumes. It keeps its own configuration (server,
    // token in the Keychain, registry, mode), so background rounds run without any view or account session.
    SyncEngine.register()
    Appearance.apply()
    UNUserNotificationCenter.current().delegate = NotificationTaps.shared   // taps → the post (N7); set before launch ends
    return true
  }

  func application(_ application: UIApplication, handleEventsForBackgroundURLSession identifier: String,
                   completionHandler: @escaping () -> Void) {
    if identifier == Backfill.sessionId {
      Backfill.shared.sessionCompletion = completionHandler
      Backfill.shared.attach()
    } else {
      completionHandler()
    }
  }

  func application(_ application: UIApplication, didRegisterForRemoteNotificationsWithDeviceToken deviceToken: Data) {
    Task { @MainActor in PushRegistration.shared.received(deviceToken) }
  }

  func application(_ application: UIApplication, didFailToRegisterForRemoteNotificationsWithError error: Error) {
    Task { @MainActor in PushRegistration.shared.failed() }
  }
}

/// UIKit appearance that SwiftUI does not expose on iOS 16 (tab bar colours of the page's `.tab`).
enum Appearance {
  static func apply() {
    let tab = UITabBarAppearance()
    tab.configureWithDefaultBackground()
    let normal = Palette.uiColor(0xA3A3AA, 0x55555B)          // --text-3
    let selected = Palette.uiColor(0x111113, 0xF4F4F6)        // --text
    for layout in [tab.stackedLayoutAppearance, tab.inlineLayoutAppearance, tab.compactInlineLayoutAppearance] {
      layout.normal.iconColor = normal
      layout.normal.titleTextAttributes = [.foregroundColor: normal, .font: UIFont.systemFont(ofSize: 10.5, weight: .medium)]
      layout.selected.iconColor = selected
      layout.selected.titleTextAttributes = [.foregroundColor: selected, .font: UIFont.systemFont(ofSize: 10.5, weight: .medium)]
    }
    UITabBar.appearance().standardAppearance = tab
    UITabBar.appearance().scrollEdgeAppearance = tab
  }
}
