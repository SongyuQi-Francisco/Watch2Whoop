import SwiftUI
import UIKit

/// Colour tokens of the Today page (body_engine/src/body_engine/web/static/app.css `:root` and its dark twin), so the
/// native app looks the same: neutral greys + one low-saturation colour per state, cards without borders, pure black in
/// dark mode. Change a colour in app.css and here together (`scripts/check_sources.py` compares them in CI).
/// `.slim(...)` is the slim look (flag `slim_mode`, #126; `html[data-theme="slim"]` in slim.js, compared the same way): the
/// colours follow `Theme.isSlim` when they are resolved, and without it they are exactly the normal ones.
enum Palette {
  static let bg = dynamic(0xFFFFFF, 0x000000).slim(0xFBFAF7, 0x0A0A0B)
  static let surface = dynamic(0xF4F4F6, 0x121214).slim(0xF1EFE9, 0x151517)
  static let surface2 = dynamic(0xE9E9ED, 0x1D1D20).slim(0xE5E2DA, 0x212125)
  static let text = dynamic(0x111113, 0xF4F4F6).slim(0x15140F, 0xF6F4EE)
  static let text2 = dynamic(0x6C6C73, 0x8D8D94).slim(0x625F55, 0x9A978C)
  static let text3 = dynamic(0xA3A3AA, 0x55555B).slim(0x9D998D, 0x5D5B53)
  static let hair = dynamic(0x000000, 0xFFFFFF, lightAlpha: 0.06, darkAlpha: 0.07).slim(0x15140F, 0xFFFFFF, lightAlpha: 0.09, darkAlpha: 0.08)
  static let good = dynamic(0x4E9A6B, 0x72BF8E).slim(0x2F8A2F, 0xB8F23D)
  static let mid = dynamic(0xB58A34, 0xD3AE63).slim(0xC27C00, 0xFFB224)
  static let bad = dynamic(0xBE5A44, 0xDE7F69).slim(0xD23A2A, 0xFF5A4D)
  static let sleep = dynamic(0x6870C4, 0x8D93E4).slim(0x5560D0, 0x8F9BFF)
  /// Strain ring / training (the page's --train, used by the native Today of 1.0.9).
  static let train = dynamic(0x3F8F9A, 0x5FB3BD).slim(0xD93F0B, 0xFF5A1F)
  static let goodSoft = dynamic(0x4E9A6B, 0x72BF8E, lightAlpha: 0.12, darkAlpha: 0.14).slim(0x2F8A2F, 0xB8F23D, lightAlpha: 0.13, darkAlpha: 0.14)
  static let midSoft = dynamic(0xB58A34, 0xD3AE63, lightAlpha: 0.13, darkAlpha: 0.14).slim(0xC27C00, 0xFFB224, lightAlpha: 0.14, darkAlpha: 0.15)
  static let badSoft = dynamic(0xBE5A44, 0xDE7F69, lightAlpha: 0.12, darkAlpha: 0.14).slim(0xD23A2A, 0xFF5A4D, lightAlpha: 0.13, darkAlpha: 0.15)
  static let scrim = dynamic(0x000000, 0x000000, lightAlpha: 0.28, darkAlpha: 0.6).slim(0x15140F, 0x000000, lightAlpha: 0.32, darkAlpha: 0.62)
  static let sheet = dynamic(0xFFFFFF, 0x161618).slim(0xFFFEFB, 0x131315)
  /// Inputs, buttons and cards inside sheets.
  static let inSheet = dynamic(0xF4F4F6, 0x232326).slim(0xF1EFE9, 0x212125)
  /// Primary buttons, selected chips and days, the selected tab (slim.js `--accent` / `--on-accent`). In the normal look they
  /// are just --text and --bg, the page's `.btn-primary`.
  static let accent = dynamic(0x111113, 0xF4F4F6).slim(0xD93F0B, 0xFF5A1F)
  static let onAccent = dynamic(0xFFFFFF, 0x000000).slim(0xFFFFFF, 0x0A0A0B)
  static let chrome = dynamic(0xFFFFFF, 0x000000, lightAlpha: 0.86, darkAlpha: 0.82).slim(0xFBFAF7, 0x0A0A0B, lightAlpha: 0.86, darkAlpha: 0.82)

  // 2.0.1 instrument tokens (docs/design/2.0.1 kit.css, direction.md 2.1): opacities of text / text2, no new hex. The
  // slim look takes the same opacities of its own text / text2.
  /// 1.5 pt tracks of the dial, the gauges and rings (text a.16 / a.12).
  static let track = derived(.text, lightAlpha: 0.12, darkAlpha: 0.16)
  /// Minor scale ticks (text2 a.55 / a.50).
  static let tick = derived(.text2, lightAlpha: 0.55, darkAlpha: 0.50)
  /// Origin, zone edges, major ticks (text a.85 / a.72).
  static let tickMajor = derived(.text, lightAlpha: 0.85, darkAlpha: 0.72)
  /// The usual range as a line beside a scale (text2 a.62).
  static let bandLine = derived(.text2, lightAlpha: 0.62, darkAlpha: 0.62)
  /// The usual range as an area behind a chart (text2 a.16 / a.24).
  static let band = derived(.text2, lightAlpha: 0.16, darkAlpha: 0.24)
  /// History bars; only today takes colour (text2 a.34 / a.42).
  static let past = derived(.text2, lightAlpha: 0.34, darkAlpha: 0.42)
  /// Sparkline stroke (text a.55 / a.62).
  static let spark = derived(.text, lightAlpha: 0.55, darkAlpha: 0.62)
  /// Chart baseline (black a.14 / white a.14).
  static let hairStrong = dynamic(0x000000, 0xFFFFFF, lightAlpha: 0.14, darkAlpha: 0.14).slim(0x15140F, 0xFFFFFF, lightAlpha: 0.14, darkAlpha: 0.14)

  /// The ink a derived token is an opacity of.
  enum Ink { case text, text2 }

  /// An opacity of text or text2, in both looks (the hex values are those of `text` / `text2` above).
  static func derived(_ ink: Ink, lightAlpha: CGFloat, darkAlpha: CGFloat) -> Color {
    switch ink {
    case .text:
      return dynamic(0x111113, 0xF4F4F6, lightAlpha: lightAlpha, darkAlpha: darkAlpha)
        .slim(0x15140F, 0xF6F4EE, lightAlpha: lightAlpha, darkAlpha: darkAlpha)
    case .text2:
      return dynamic(0x6C6C73, 0x8D8D94, lightAlpha: lightAlpha, darkAlpha: darkAlpha)
        .slim(0x625F55, 0x9A978C, lightAlpha: lightAlpha, darkAlpha: darkAlpha)
    }
  }

  /// The raw values, for tests and for UIKit appearance proxies.
  static func uiColor(_ light: UInt32, _ dark: UInt32, lightAlpha: CGFloat = 1, darkAlpha: CGFloat = 1, slim: Slim? = nil) -> UIColor {
    UIColor { t in
      let isDark = t.userInterfaceStyle == .dark
      if let slim, Theme.isSlim { return isDark ? rgb(slim.dark, slim.darkAlpha) : rgb(slim.light, slim.lightAlpha) }
      return isDark ? rgb(dark, darkAlpha) : rgb(light, lightAlpha)
    }
  }

  /// A token's values in the slim look.
  struct Slim {
    let light, dark: UInt32
    var lightAlpha: CGFloat = 1
    var darkAlpha: CGFloat = 1
  }

  /// A token's normal values, waiting for its `.slim(...)`.
  private struct Normal {
    let light, dark: UInt32
    let lightAlpha, darkAlpha: CGFloat

    func slim(_ l: UInt32, _ d: UInt32, lightAlpha la: CGFloat = 1, darkAlpha da: CGFloat = 1) -> Color {
      Color(uiColor(light, dark, lightAlpha: lightAlpha, darkAlpha: darkAlpha, slim: Slim(light: l, dark: d, lightAlpha: la, darkAlpha: da)))
    }
  }

  private static func dynamic(_ light: UInt32, _ dark: UInt32, lightAlpha: CGFloat = 1, darkAlpha: CGFloat = 1) -> Normal {
    Normal(light: light, dark: dark, lightAlpha: lightAlpha, darkAlpha: darkAlpha)
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
