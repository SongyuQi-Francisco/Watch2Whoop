import SwiftUI
import UIKit

/// Colour tokens of the Today page (body_engine/src/body_engine/web/static/app.css `:root` and its dark twin), so the
/// native app looks the same: neutral greys + one low-saturation colour per state, cards without borders, pure black in
/// dark mode. Change a colour in app.css and here together (`scripts/check_sources.py` compares them in CI).
enum Palette {
  static let bg = dynamic(0xFFFFFF, 0x000000)
  static let surface = dynamic(0xF4F4F6, 0x121214)
  static let surface2 = dynamic(0xE9E9ED, 0x1D1D20)
  static let text = dynamic(0x111113, 0xF4F4F6)
  static let text2 = dynamic(0x6C6C73, 0x8D8D94)
  static let text3 = dynamic(0xA3A3AA, 0x55555B)
  static let hair = dynamic(0x000000, 0xFFFFFF, lightAlpha: 0.06, darkAlpha: 0.07)
  static let good = dynamic(0x4E9A6B, 0x72BF8E)
  static let mid = dynamic(0xB58A34, 0xD3AE63)
  static let bad = dynamic(0xBE5A44, 0xDE7F69)
  static let sleep = dynamic(0x6870C4, 0x8D93E4)
  /// Strain ring / training (the page's --train, used by the native Today of 1.0.9).
  static let train = dynamic(0x3F8F9A, 0x5FB3BD)
  static let goodSoft = dynamic(0x4E9A6B, 0x72BF8E, lightAlpha: 0.12, darkAlpha: 0.14)
  static let midSoft = dynamic(0xB58A34, 0xD3AE63, lightAlpha: 0.13, darkAlpha: 0.14)
  static let badSoft = dynamic(0xBE5A44, 0xDE7F69, lightAlpha: 0.12, darkAlpha: 0.14)
  static let scrim = dynamic(0x000000, 0x000000, lightAlpha: 0.28, darkAlpha: 0.6)
  static let sheet = dynamic(0xFFFFFF, 0x161618)
  /// Inputs, buttons and cards inside sheets.
  static let inSheet = dynamic(0xF4F4F6, 0x232326)
  static let chrome = dynamic(0xFFFFFF, 0x000000, lightAlpha: 0.86, darkAlpha: 0.82)

  /// The raw values, for tests and for UIKit appearance proxies.
  static func uiColor(_ light: UInt32, _ dark: UInt32, lightAlpha: CGFloat = 1, darkAlpha: CGFloat = 1) -> UIColor {
    UIColor { $0.userInterfaceStyle == .dark ? rgb(dark, darkAlpha) : rgb(light, lightAlpha) }
  }

  private static func dynamic(_ light: UInt32, _ dark: UInt32, lightAlpha: CGFloat = 1, darkAlpha: CGFloat = 1) -> Color {
    Color(uiColor(light, dark, lightAlpha: lightAlpha, darkAlpha: darkAlpha))
  }

  static func rgb(_ hex: UInt32, _ alpha: CGFloat = 1) -> UIColor {
    UIColor(red: CGFloat((hex >> 16) & 0xFF) / 255, green: CGFloat((hex >> 8) & 0xFF) / 255,
            blue: CGFloat(hex & 0xFF) / 255, alpha: alpha)
  }
}

/// A state colour by name: the server and the 1.0.9 bridge say "good" / "mid" / "bad" / "sleep" / "train" / "text".
enum Tone: String {
  case good, mid, bad, sleep, train, text

  var color: Color {
    switch self {
    case .good: return Palette.good
    case .mid: return Palette.mid
    case .bad: return Palette.bad
    case .sleep: return Palette.sleep
    case .train: return Palette.train
    case .text: return Palette.text
    }
  }

  /// Recovery zone from the server (`scores.recovery_zone`: green / yellow / red).
  init(zone: String?) {
    switch zone {
    case "green": self = .good
    case "yellow": self = .mid
    case "red": self = .bad
    default: self = .text
    }
  }
}

/// Corner radii and spacing of the page (app.css `--radius` 18 for cards, 14 for tiles).
enum Metrics {
  static let cardRadius: CGFloat = 18
  static let tileRadius: CGFloat = 14
  static let gutter: CGFloat = 20
  static let maxContentWidth: CGFloat = 640
}
