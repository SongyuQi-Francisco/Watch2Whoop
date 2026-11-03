import Foundation
import UIKit
import os

/// The colour theme (flag `slim_mode`, #126; the Today page's `html[data-theme="slim"]` in slim.js). Colours only, no
/// layout. `Palette`'s colours ask `isSlim` each time they are resolved, so with the normal look they give exactly the
/// colours they always gave.
enum Theme {
  /// Read once, before the first frame, from what the last start saved: the first screen already has the right colours.
  private static let slim = OSAllocatedUnfairLock(initialState: AppearanceChoice.saved?.isSlim == true)

  /// Asked by every colour of `Palette` while it is resolved (not always on the main thread).
  static var isSlim: Bool { slim.withLock { $0 } }

  /// Sets the look and remembers it for the next start. nil = the flag is off for this person, or nobody is signed in:
  /// the normal look, nothing remembered.
  @MainActor static func apply(_ choice: AppearanceChoice?) {
    AppearanceChoice.saved = choice
    let want = choice?.isSlim == true
    let changed = slim.withLock { (now: inout Bool) -> Bool in
      defer { now = want }
      return now != want
    }
    if changed { repaint() }
  }

  /// Redraws what is on screen, including open sheets, and keeps every page and its state where it is. SwiftUI only
  /// resolves a colour again when something it depends on changes, and that cannot be a value of ours on iOS 16, so
  /// the windows go through the other interface style for a moment, under a picture of the screen, which then fades.
  @MainActor private static func repaint() {
    let windows = UIApplication.shared.connectedScenes.compactMap { $0 as? UIWindowScene }.flatMap(\.windows)
    for window in windows where !window.isHidden && window.bounds.width > 0 {
      let cover = window.snapshotView(afterScreenUpdates: false)
      if let cover {
        cover.isUserInteractionEnabled = false
        window.addSubview(cover)
      }
      let was = window.overrideUserInterfaceStyle
      window.overrideUserInterfaceStyle = window.traitCollection.userInterfaceStyle == .dark ? .light : .dark
      DispatchQueue.main.asyncAfter(deadline: .now() + 0.05) {
        window.overrideUserInterfaceStyle = was
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.05) {
          guard let cover else { return }
          UIView.animate(withDuration: UIAccessibility.isReduceMotionEnabled ? 0 : 0.18, animations: { cover.alpha = 0 },
                         completion: { _ in cover.removeFromSuperview() })
        }
      }
    }
  }
}

extension AppearanceChoice {
  static let flag = "slim_mode"
  private static let savedKey = "w2w.appearance.v1"

  var isSlim: Bool { theme == "slim" }
  /// The quote belongs to the slim look (slim.js `quoteOn`).
  var showsQuote: Bool { isSlim && quotes }

  /// `/v1/web/me` → `appearance`.
  init?(json: Any?) {
    guard let o = json as? [String: Any], let theme = o["theme"] as? String else { return nil }
    self.init(theme: theme, quotes: (o["quotes"] as? Bool) ?? true)
  }

  /// The local copy, for the first frame of the next start.
  static var saved: AppearanceChoice? {
    get { UserDefaults.standard.data(forKey: savedKey).flatMap { try? JSONDecoder().decode(AppearanceChoice.self, from: $0) } }
    set {
      if let newValue, let d = try? JSONEncoder().encode(newValue) {
        UserDefaults.standard.set(d, forKey: savedKey)
      } else {
        UserDefaults.standard.removeObject(forKey: savedKey)
      }
    }
  }
}

/// PUT /v1/users/me/appearance: only the fields set are changed.
struct AppearancePatch: Encodable, Equatable {
  var theme: String?
  var quotes: Bool?
}
