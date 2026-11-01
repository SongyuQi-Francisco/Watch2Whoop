import SwiftUI

/// A score ring (the page's `.ring`: track in surface-2, round-capped arc from 12 o'clock, number and label inside).
/// Today shows three side by side, recovery in the middle and bigger (memory "UI: keep current style").
struct RingView: View {
  let value: String?
  let label: String
  /// 0…1 of the arc.
  let fraction: Double
  var tone: Tone = .text
  var side: CGFloat = 100
  var big = false

  @Environment(\.accessibilityReduceMotion) private var reduceMotion
  @State private var shown = false

  private var line: CGFloat { side * 12 / 196 * (big ? 1 : 1.25) }   // the page's ring: r 84 of 196, stroke 12

  var body: some View {
    ZStack {
      Circle().stroke(Palette.surface2, lineWidth: line)
      Circle()
        .trim(from: 0, to: shown ? min(1, max(0, fraction)) : 0)
        .stroke(tone.color, style: StrokeStyle(lineWidth: line, lineCap: .round))
        .rotationEffect(.degrees(-90))
      VStack(spacing: big ? 2 : 1) {
        Text(value ?? "—").font(Typo.ringValue(side: side, big: big))
          .foregroundColor(value == nil ? Palette.text3 : Palette.text)
          .minimumScaleFactor(0.6).lineLimit(1)
        Text(label).font(.system(size: big ? 13 : 11, weight: .medium)).foregroundColor(Palette.text2)
          .lineLimit(1).minimumScaleFactor(0.7)
      }
      .padding(.horizontal, line + 4)
    }
    .frame(width: side, height: side)
    .padding(line / 2)
    .onAppear {
      if reduceMotion { shown = true } else { withAnimation(.spring(response: 0.9, dampingFraction: 0.86).delay(0.05)) { shown = true } }
    }
    .accessibilityElement(children: .ignore)
    .accessibilityLabel("\(label) \(value ?? "—")")
  }
}
