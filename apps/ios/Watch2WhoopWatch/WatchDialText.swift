import SwiftUI

// The parts of the watch app's drawing that the watch-face complications (Watch2WhoopWatchWidgets/) use too, so the
// face and the app draw a number and a set the same way. Compiled into the watch app and the widget extension: no
// WatchKit here, nothing that needs the app process.

/// A number centred on its own (owner, 2026-11-19: 「数字没有居中」): its digits' ink box sits on the centre of the
/// enclosing `ZStack` (plus `dy`), and its unit (「%」, 「/18」) hangs after it without moving it (`DialText`). Used in
/// every dial and ring: home, the start screen, the rest countdown, the summary.
struct CenteredNumber: View {
  var text: String
  var size: CGFloat
  var weight: Font.Weight = .light
  var color: Color = W.text
  /// Tabular figures, only for a number that ticks (the countdown): they keep it still, but put the ink of a 「1」 off
  /// centre, so a number that does not change uses proportional figures.
  var monospaced = false
  var unit: String? = nil
  var unitSize: CGFloat = 12
  var unitWeight: Font.Weight = .medium
  var unitColor: Color = W.text2
  var unitGap: CGFloat = 2
  /// The unit's baseline this far above the number's.
  var unitRaise: CGFloat = 0
  /// The digits' ink centre this far below the centre (the mockups put some numbers off the ring's centre on purpose).
  var dy: CGFloat = 0

  var body: some View {
    HungUnitLayout(gap: unitGap, raise: unitRaise, inkHalf: CGFloat(DialText.digitHeight(Double(size))) / 2, dy: dy) {
      Text(text).font(monospaced ? .system(size: size, weight: weight).monospacedDigit() : .system(size: size, weight: weight))
        .foregroundStyle(color).lineLimit(1).fixedSize()
      if let unit {
        Text(unit).font(.system(size: unitSize, weight: unitWeight)).foregroundStyle(unitColor).lineLimit(1).fixedSize()
      }
    }
    .accessibilityElement(children: .combine)
  }
}

/// The layout behind `CenteredNumber`: it is as big as the number alone (so the number is what gets centred), the unit
/// is placed after the number's right edge (`gap`) with its baseline `raise` above the number's, outside the bounds, and
/// the layout's vertical centre guide is the digits' ink centre (`DialText`) moved by `dy`.
struct HungUnitLayout: Layout {
  var gap: CGFloat
  var raise: CGFloat
  /// Half the digits' ink height.
  var inkHalf: CGFloat
  var dy: CGFloat

  func sizeThatFits(proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) -> CGSize {
    subviews.first?.sizeThatFits(.unspecified) ?? .zero
  }

  func placeSubviews(in bounds: CGRect, proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) {
    guard let number = subviews.first else { return }
    let n = number.dimensions(in: .unspecified)
    number.place(at: bounds.origin, proposal: .unspecified)
    guard subviews.count > 1 else { return }
    let unit = subviews[1]
    let u = unit.dimensions(in: .unspecified)
    let x = CGFloat(DialText.unitLeading(centerX: Double(bounds.midX), numberWidth: Double(n.width), gap: Double(gap)))
    unit.place(at: CGPoint(x: x, y: bounds.minY + n[.lastTextBaseline] - raise - u[.lastTextBaseline]), proposal: .unspecified)
  }

  func explicitAlignment(of guide: VerticalAlignment, in bounds: CGRect, proposal: ProposedViewSize, subviews: Subviews,
                         cache: inout ()) -> CGFloat? {
    guard let number = subviews.first else { return nil }
    let baseline = number.dimensions(in: .unspecified)[.lastTextBaseline]
    switch guide {
    case .center: return bounds.minY + baseline - inkHalf - dy
    case .lastTextBaseline, .firstTextBaseline: return bounds.minY + baseline
    default: return nil
    }
  }
}

extension View {
  /// In a `ZStack` with centre alignment: this text's baseline goes `dy` below the stack's centre.
  func centredBaseline(_ dy: CGFloat) -> some View {
    alignmentGuide(VerticalAlignment.center) { $0[.lastTextBaseline] - dy }
  }
}

/// 「60 kg × 8」 for the small lines.
enum SetText {
  static func line(weightKg: Double?, reps: Int?, durationS: Double?) -> String {
    if let d = durationS { return tr("\(Int(d)) 秒", "\(Int(d)) s") }
    let r = reps.map(String.init) ?? "—"
    guard let w = weightKg else { return tr("\(r) 次", "\(r) reps") }
    return "\(WatchNum.kg(w)) kg × \(r)"
  }

  static func delta(_ d: SetDelta) -> String? {
    switch d {
    case .weight(let w): return (w > 0 ? "+" : "−") + "\(WatchNum.kg(abs(w))) kg"
    case .reps(let r): return r > 0 ? tr("+\(r) 次", "+\(r) reps") : tr("−\(-r) 次", "−\(-r) reps")
    case .seconds(let s): return s > 0 ? tr("+\(s) 秒", "+\(s) s") : tr("−\(-s) 秒", "−\(-s) s")
    case .same: return tr("持平", "Same")
    case .none: return nil
    }
  }
}
