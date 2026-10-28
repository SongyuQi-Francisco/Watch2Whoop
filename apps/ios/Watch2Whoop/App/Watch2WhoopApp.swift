import SwiftUI
import UIKit

@main
struct Watch2WhoopApp: App {
  @UIApplicationDelegateAdaptor(AppDelegate.self) private var appDelegate
  @StateObject private var model = AppModel()

  var body: some Scene {
    WindowGroup {
      RootView()
        .environmentObject(model)
        .task { await model.start() }
    }
  }
}

/// UIKit hooks the sync engine needs before any view exists: the backfill's background task and its background
/// URLSession (copied Backfill.swift), re-attached at every launch so iOS can deliver finished uploads.
final class AppDelegate: NSObject, UIApplicationDelegate {
  func application(_ application: UIApplication,
                   didFinishLaunchingWithOptions launchOptions: [UIApplication.LaunchOptionsKey: Any]? = nil) -> Bool {
    Backfill.register()
    Backfill.shared.attach()
    Appearance.apply()
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
