import CoreGraphics
import Foundation
import UIKit

// The cockpit's keys as satellites (option B 「卫星」, chosen by the owner on 2026-11-20; the rules and their numbers are
// docs/design/2.0.1/jarvis-soften/README.md §2 B, the moving prototype is option-b.html / soften.js `keysOrbit`).
// Round keys on one hairline orbit, no walls between them: the positions follow from one rule (orbit radius, key size,
// gap, count), the same for the first ring (five keys) and the second (five or three), on every phone.

/// One orbit of round keys. Pure: no state, no drawing.
struct CockpitOrbit: Equatable {
  /// The orbit through the key centres.
  var radius: CGFloat
  /// The drawn key (a circle).
  var keyRadius: CGFloat
  /// Empty space between two neighbouring keys, measured along the chord between their centres.
  var gap: CGFloat
  /// The touch area of a key: its whole wedge between these radii (README §1, "the touch area is the wedge").
  var hitInner: CGFloat
  var hitOuter: CGFloat

  /// The phone's cockpit (CockpitGeometry holds the numbers).
  static let phone = CockpitOrbit(radius: CockpitGeometry.orbitRadius, keyRadius: CockpitGeometry.keyRadius,
                                  gap: CockpitGeometry.keyGap, hitInner: CockpitGeometry.wedgeInner,
                                  hitOuter: CockpitGeometry.wedgeOuter)

  /// Centre to centre, along the chord.
  var pitch: CGFloat { 2 * keyRadius + gap }

  /// Degrees between two neighbours, rounded to a whole degree (so the two rings stay on the tick ring's grid:
  /// 116 pt orbit, 48 pt keys, 23.7 pt gap → 36°).
  var step: Double {
    let half = asin(min(1, Double(pitch / (2 * radius))))
    return (2 * half * 180 / .pi).rounded()
  }

  /// The angle of slot `i` of `count`, centred on 12 o'clock (0°), clockwise.
  func angle(slot i: Int, of count: Int) -> Double {
    (Double(i) - Double(count - 1) / 2) * step
  }

  /// Every slot's angle.
  func angles(count: Int) -> [Double] {
    (0..<max(0, count)).map { angle(slot: $0, of: count) }
  }

  /// The centre of a key at `degrees` around `c`.
  func center(_ degrees: Double, around c: CGPoint) -> CGPoint {
    CockpitGeometry.point(radius, degrees, around: c)
  }

  /// The empty space between two neighbouring keys (what the eye reads as distance), in points.
  var emptyBetweenKeys: CGFloat {
    2 * radius * CGFloat(sin(step / 2 * .pi / 180)) - 2 * keyRadius
  }

  /// The widest a label under a key may be: the pitch less a little air between two labels.
  var labelWidth: CGFloat {
    2 * radius * CGFloat(sin(step / 2 * .pi / 180)) - CockpitGeometry.labelAir
  }

  /// The narrowest part of a key's touch wedge (at its inner radius).
  var narrowestHit: CGFloat {
    2 * hitInner * CGFloat(sin(step / 2 * .pi / 180))
  }

  /// Which of `angles` a touch at `p` falls on: the key whose wedge (half a step either side, between `hitInner` and
  /// `hitOuter`) holds it. nil = outside every key.
  func hit(_ p: CGPoint, center c: CGPoint, angles: [Double]) -> Int? {
    let dx = Double(p.x - c.x), dy = Double(c.y - p.y)
    let d = CGFloat(hypot(dx, dy))
    guard d >= hitInner, d <= hitOuter else { return nil }
    let a = atan2(dx, dy) * 180 / .pi
    return angles.firstIndex { abs($0 - a) <= step / 2 }
  }

  /// The outermost points the drawing reaches for `count` keys (keys, their labels and the tick ring), around `c`;
  /// `label` is the label's size.
  func extent(count: Int, around c: CGPoint, label: CGSize) -> CGRect {
    var r = CGRect.null
    for a in angles(count: count) {
      let k = center(a, around: c)
      r = r.union(CGRect(x: k.x - keyRadius, y: k.y - keyRadius, width: 2 * keyRadius, height: 2 * keyRadius))
      let ly = k.y + keyRadius + CockpitGeometry.labelBelow
      r = r.union(CGRect(x: k.x - label.width / 2, y: ly - label.height / 2, width: label.width, height: label.height))
    }
    let g = CockpitGeometry.self
    for a in stride(from: -g.tickSpan, through: g.tickSpan, by: 4) {
      let q = g.point(g.tickMajor, a, around: c)
      r = r.union(CGRect(x: q.x, y: q.y, width: 0, height: 0))
    }
    return r
  }
}

/// The labels under the keys: 10.5 pt, cut to the room the orbit gives (a long quick-meal name gets "…").
enum CockpitLabel {
  static let font = UIFont.systemFont(ofSize: CockpitGeometry.labelSize, weight: .medium)

  static func width(_ s: String) -> CGFloat {
    // the drawing adds 0.3 pt of tracking per character
    (s as NSString).size(withAttributes: [.font: font]).width + 0.3 * CGFloat(s.count)
  }

  /// `s`, or as much of it as fits in `room` followed by "…".
  static func fit(_ s: String, room: CGFloat = CockpitOrbit.phone.labelWidth) -> String {
    if width(s) <= room { return s }
    var chars = Array(s)
    while !chars.isEmpty {
      chars.removeLast()
      let t = String(chars).trimmingCharacters(in: .whitespaces) + "…"
      if width(t) <= room { return t }
    }
    return "…"
  }
}
