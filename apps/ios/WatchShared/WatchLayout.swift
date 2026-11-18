import Foundation

/// Where text goes inside a dial or ring (owner's acceptance 2026-11-19: 「数字没有居中」). A number is centred on its
/// own: its digits' ink box sits on the ring's centre, and a unit (「%」, 「/18」) hangs beside it without moving it.
/// The rest of the text is placed by its baseline at the mockups' offsets from the centre. Pure geometry, so the rules
/// are unit-tested; the watch's `CenteredNumber` and `.baseline(at:)` apply them.
enum DialText {
  /// The height of the digits' ink as a share of the font size, for the light weight the dials use. Measured on the
  /// simulator (2026-11-19): 28.5 pt of ink for the 42 pt number at 46 mm, 25.5 pt for 37.8 pt at 42 mm.
  static let digitHeightEm = 0.68

  static func digitHeight(_ size: Double) -> Double { size * digitHeightEm }

  /// The baseline that puts the digits' ink box on `centerY` (digits have no descenders: the box is baseline − height
  /// … baseline).
  static func baseline(centerY: Double, size: Double) -> Double { centerY + digitHeight(size) / 2 }

  /// The hung unit's leading edge: the number is centred on `centerX`, the unit starts `gap` after its right edge.
  static func unitLeading(centerX: Double, numberWidth: Double, gap: Double) -> Double { centerX + numberWidth / 2 + gap }
}

/// The home screen's positions (mockups D1 / D2, drawn at 46 mm = 208 × 248 pt; the watch scales them by the screen's
/// width). The top row is the system's clock row, which on watchOS 26 sits lower than in the mockups, so the dial, the
/// readings and their gaps keep the mockup's distances from the clock row; the two keys keep their place at the bottom.
enum HomeLayout {
  static let designWidth = 208.0
  static let designHeight = 248.0
  /// Centre of the clock row in the mockups.
  static let mockClockY = 19.5
  /// Dial: centre below the clock row, outer radius of the arc (57 pt + half its 4 pt stroke).
  static let dialBelowClock = 94.2 - 19.5
  static let dialRadius = 57.0
  static let dialOuter = 59.0
  /// Text of the dial, baselines from its centre (mockup: 「恢复」 at −21, the number's ink centred, the word at +32) and
  /// the unit hung beside the number (2.5 pt gap, its baseline 2 pt above the number's).
  static let labelBaseline = -21.0
  static let numberSize = 42.0
  static let wordBaseline = 32.0
  static let unitGap = 2.5
  static let unitRaise = 2.0
  /// Readings (label baseline and bar) below the dial's centre, as in the mockup (174 and 184 with the centre at 94).
  static let readingsBaseline = 174.0 - 94.2
  static let barBelowCentre = 184.0 - 94.2
  /// The keys row: centre and height (mockup y 202–238).
  static let keysCentreY = 220.0
  static let keysHeight = 36.0

  /// The dial's centre for a clock row centred at `clockY` (design points).
  static func dialCentre(clockY: Double) -> Double { clockY + dialBelowClock }

  /// Room left between the readings' bar and the keys (design points); the layout never lets it go below `minGap`.
  static func barToKeys(clockY: Double) -> Double {
    (keysCentreY - keysHeight / 2) - (dialCentre(clockY: clockY) + barBelowCentre + 4)
  }

  static let minGap = 6.0
}

/// The system clock's row at the top of an app screen (watchOS 26): where its digits' ink is centred and how far its
/// right edge is from the screen's right edge, per screen (points). watchOS gives no layout value for it: a root screen
/// without toolbar items has no top safe area (the clock is drawn over the content), and a `topBarLeading` item sits
/// about 7 pt below the clock. Measured on the simulators of every watch watchOS 26 runs on, 2026-11-19; it follows each
/// model's corner radius (the 44 mm SE and the 42 mm Series 11 are nearly the same height, the clock is 3.5 pt apart).
enum ClockRow {
  struct Row: Equatable {
    var centreY: Double
    var side: Double
  }

  /// Keyed by the screen's width in points.
  static let measured: [Int: Row] = [
    162: Row(centreY: 15.5, side: 11.5),   // 40 mm (SE, Series 4–6)
    176: Row(centreY: 20.5, side: 13.5),   // 41 mm (Series 7–9)
    184: Row(centreY: 19.0, side: 13.5),   // 44 mm (SE, Series 4–6)
    187: Row(centreY: 22.5, side: 14.5),   // 42 mm (Series 10–11)
    198: Row(centreY: 23.0, side: 14.0),   // 45 mm (Series 7–9)
    205: Row(centreY: 27.0, side: 17.0),   // 49 mm (Ultra, Ultra 2)
    208: Row(centreY: 25.5, side: 17.0),   // 46 mm (Series 10–11)
    211: Row(centreY: 28.0, side: 19.5),   // 49 mm (Ultra 3)
  ]

  /// A screen not in the table (a watch that came later): the mean share of the height and width of the measured ones.
  static func row(width: Double, height: Double) -> Row {
    if let r = measured[Int(width.rounded())] { return r }
    return Row(centreY: (height * 0.1036 * 10).rounded() / 10, side: (width * 0.078 * 10).rounded() / 10)
  }
}
