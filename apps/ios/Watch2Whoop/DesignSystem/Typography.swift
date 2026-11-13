import SwiftUI

/// The page's type scale (app.css): system font (SF Pro / PingFang SC), tabular digits for numbers. Sizes follow the page
/// in points; they scale with Dynamic Type relative to the nearest text style.
enum Typo {
  /// Page title (`.top-title`, 26 bold, tight tracking).
  static let title = Font.system(size: 26, weight: .bold)
  /// Small date above the title (`.top-date`).
  static let overline = Font.system(size: 12, weight: .medium)
  /// Body text (15).
  static let body = Font.system(size: 15)
  static let bodyStrong = Font.system(size: 15, weight: .semibold)
  /// Section title above a group of tiles (13 semibold, secondary colour).
  static let section = Font.system(size: 13, weight: .semibold)
  /// Secondary line (13).
  static let caption = Font.system(size: 13)
  /// Small label (12 medium).
  static let label = Font.system(size: 12, weight: .medium)
  /// The recovery word under the rings (`.hero-word`, 17 semibold).
  static let heroWord = Font.system(size: 17, weight: .semibold)
  /// A number in a tile (21 semibold, tabular).
  static let tileValue = Font.system(size: 21, weight: .semibold).monospacedDigit()
  /// Unit after a number (11 medium).
  static let unit = Font.system(size: 11, weight: .medium)

  // 2.0.1 instrument scale (docs/design/2.0.1 direction.md 2.3): fixed sizes, tabular digits.
  /// The one hero number of a tab root (100 / ultra-light, tracking −3).
  static func hero(_ size: CGFloat = 100) -> Font { .system(size: size, weight: .ultraLight).monospacedDigit() }
  /// A reading attached to the hero (28 / regular).
  static let reading = Font.system(size: 28, weight: .regular).monospacedDigit()
  /// A face's number (22 / medium).
  static let face = Font.system(size: 22, weight: .medium).monospacedDigit()
  /// A value in an L1 row (19 / medium).
  static let row = Font.system(size: 19, weight: .medium).monospacedDigit()
  /// Header date and sheet titles (17 / semibold).
  static let header = Font.system(size: 17, weight: .semibold)
  /// The verdict word under the hero (14 / medium, ink).
  static let verdict = Font.system(size: 14, weight: .medium)
  /// Chart text: dates, scale numbers (10 / medium, always text2).
  static let axis = Font.system(size: 10, weight: .medium).monospacedDigit()

  /// Big number inside a ring, sized from the ring's side.
  static func ringValue(side: CGFloat, big: Bool) -> Font {
    .system(size: side * (big ? 0.3 : 0.26), weight: .bold).monospacedDigit()
  }
}

extension View {
  /// The page title's tracking (-0.02em).
  func titleTracking() -> some View { tracking(-0.52) }
}
