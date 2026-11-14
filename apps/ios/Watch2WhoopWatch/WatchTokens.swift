import SwiftUI
import WatchKit

/// Colours and sizes of the watch screens (docs/design/watch/watch.js `C`). The watch is always dark, so every colour
/// is the DARK value of the iPhone app's token of the same name in DesignSystem/Palette.swift; `scripts/check_sources.py`
/// fails when the two drift apart. Palette itself cannot be shared: its dynamic light / dark colours use UIKit API that
/// watchOS does not have.
enum W {
  static let bg = hex(0x000000)
  static let surface = hex(0x121214)
  static let surface2 = hex(0x1D1D20)
  static let text = hex(0xF4F4F6)
  static let text2 = hex(0x8D8D94)
  static let text3 = hex(0x55555B)
  static let good = hex(0x72BF8E)
  static let mid = hex(0xD3AE63)
  static let bad = hex(0xDE7F69)
  static let train = hex(0x5FB3BD)
  /// Ring and gauge tracks (text a.16, Palette.track dark).
  static let track = text.opacity(0.16)
  /// Sets still to do on the set ring and the progress pills (mockups: text a.26).
  static let todo = text.opacity(0.26)
  /// Minor / major ticks of the rest ring (Palette.tick / tickMajor dark).
  static let tick = text2.opacity(0.50)
  static let tickMajor = text.opacity(0.72)
  static let hair = Color.white.opacity(0.10)

  static func hex(_ v: UInt32) -> Color {
    Color(red: Double((v >> 16) & 0xFF) / 255, green: Double((v >> 8) & 0xFF) / 255, blue: Double(v & 0xFF) / 255)
  }

  /// The mockups are drawn at 46 mm (208 pt wide); 42 mm is the same layout at 0.899.
  static let k: CGFloat = {
    let w = WKInterfaceDevice.current().screenBounds.width
    return min(1.08, max(0.85, w / 208))
  }()

  static func font(_ size: CGFloat, _ weight: Font.Weight = .medium) -> Font {
    .system(size: size * k, weight: weight).monospacedDigit()
  }
}

/// The haptics of plan 2.1 (「触感」 rows of the mockups).
enum Haptics {
  static func play(_ h: SetSession.Haptic) {
    let d = WKInterfaceDevice.current()
    switch h {
    case .start: d.play(.start)
    case .success: d.play(.success)
    case .restOver: d.play(.notification)
    case .end: d.play(.stop)
    }
  }

  /// S5 「补交完成一下」 / S6 「失败震动」.
  static func delivered() { WKInterfaceDevice.current().play(.directionUp) }
  static func failed() { WKInterfaceDevice.current().play(.failure) }
}
