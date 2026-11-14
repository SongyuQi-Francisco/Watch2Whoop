import SwiftUI

// The instruments of the 2.0.1 记录 tab (docs/design/2.0.1 screens 10–15), drawn with the kit's tokens: the set ring, the
// pair of bodies inside it, the week's dots, a set's dots, the header's two-way switch.

/// The set ring: one arc per set of the day (a wider gap between exercises); sets still to do on the track, done ones in
/// `train`. The content (the bodies, the session's name) sits in the middle.
struct SetRing<Center: View>: View {
  let arcs: [RecordGlance.Arc]
  let side: CGFloat
  @ViewBuilder var center: () -> Center
  @Environment(\.accessibilityReduceMotion) private var reduceMotion

  var body: some View {
    let r = side / 2 - 3
    ZStack {
      if arcs.isEmpty {
        Circle().stroke(Palette.track, lineWidth: 1.5).frame(width: r * 2, height: r * 2)
      }
      ForEach(Array(arcs.enumerated()), id: \.offset) { _, a in
        Circle().trim(from: a.start, to: a.end)
          .stroke(a.done ? Palette.train : Palette.track, style: StrokeStyle(lineWidth: a.done ? 5 : 3, lineCap: .butt))
          .rotationEffect(.degrees(-90))
          .frame(width: r * 2, height: r * 2)
          .animation(reduceMotion ? nil : .spring(response: 0.5, dampingFraction: 0.9), value: a.done)
      }
      center()
    }
    .frame(width: side, height: side)
  }
}

/// Front and back of the body, regions coloured by `fill` (nil = bare).
struct BodyPair: View {
  let sex: String
  var spacing: CGFloat = 14
  var lineScale: CGFloat = 1
  let fill: (String) -> Color?

  var body: some View {
    HStack(spacing: spacing) {
      ForEach([true, false], id: \.self) { front in
        if let side = MuscleBodies.shared.side(sex: sex, front: front) {
          MuscleBodyView(side: side, regions: [:], lineScale: lineScale, fill: fill)
            .allowsHitTesting(false)
        }
      }
    }
    .accessibilityHidden(true)
  }

  /// `train` at the region's share of the day (direction.md 4: opacity = share).
  static func shares(_ s: [String: Double]) -> (String) -> Color? {
    { region in
      let o = RecordGlance.shareOpacity(s[region])
      return o > 0 ? Palette.train.opacity(o) : nil
    }
  }
}

/// The week as seven dots: trained in `train`, today an open ring, planned days grey, the rest a small dot.
struct WeekDotsView: View {
  let dots: [RecordGlance.WeekDot]

  var body: some View {
    HStack(spacing: 0) {
      ForEach(Array(dots.enumerated()), id: \.offset) { i, d in
        if i > 0 { Spacer(minLength: 2) }
        dot(d).frame(width: 14, height: 14)
      }
    }
    .accessibilityHidden(true)
  }

  @ViewBuilder private func dot(_ d: RecordGlance.WeekDot) -> some View {
    switch d {
    case .trained: Circle().fill(Palette.train)
    case .today: Circle().stroke(Palette.text, lineWidth: 1.5).padding(1)
    case .planned: Circle().fill(Palette.surface2)
    case .none: Circle().fill(Palette.text3).frame(width: 5, height: 5)
    }
  }
}

/// A set's dots on a session row: done in `train`, still to do on the track.
struct SetDots: View {
  let rows: [SetRow]

  var body: some View {
    HStack(spacing: 5) {
      ForEach(Array(rows.enumerated()), id: \.offset) { _, r in
        Circle().fill(r.done ? Palette.train : Palette.surface2).frame(width: 7, height: 7)
      }
    }
    .accessibilityHidden(true)
  }
}

/// The header's two-way switch of 记录 (训练 | 饮食): icon + word in a capsule, the chosen one on `surface2`. The only control
/// row allowed on a tab root (direction.md 4).
struct RecordSegmentSwitch: View {
  @Binding var selection: RecordTab.Segment

  var body: some View {
    HStack(spacing: 2) {
      item(.training, icon: .dumbbell, title: tr("训练", "Training"))
      item(.food, icon: .soup, title: tr("饮食", "Food"))
    }
    .padding(3)
    .background(Capsule().fill(Palette.surface))
  }

  private func item(_ s: RecordTab.Segment, icon: Icon.Name, title: String) -> some View {
    let on = selection == s || (s == .food && selection == .journal)
    return Button { withAnimation(.easeOut(duration: 0.15)) { selection = s } } label: {
      HStack(spacing: 6) {
        Icon(icon, size: 15)
        Text(title).font(.system(size: 15, weight: on ? .semibold : .medium)).lineLimit(1)
      }
      .foregroundColor(on ? Palette.text : Palette.text2)
      .padding(.horizontal, 14)
      .frame(height: 38)
      .background(Capsule().fill(on ? Palette.surface2 : .clear))
      .contentShape(Capsule())
    }
    .buttonStyle(.plain)
    .accessibilityAddTraits(on ? .isSelected : [])
  }
}

/// A pushed page of 记录 (11, 13, 14, 15): back chevron + title on the left, an optional control on the right, the page on
/// black; the tab bar stays.
struct RecordPage<Trailing: View, Content: View>: View {
  let title: String
  @ViewBuilder var trailing: () -> Trailing
  @ViewBuilder var content: () -> Content
  @Environment(\.dismiss) private var dismiss

  var body: some View {
    ScrollView(.vertical, showsIndicators: false) {
      VStack(alignment: .leading, spacing: 0) {
        HStack(spacing: 6) {
          Button { dismiss() } label: {
            Icon(.chevronLeft, size: 22).foregroundColor(Palette.text).frame(width: 32, height: 44).contentShape(Rectangle())
          }
          .accessibilityLabel(tr("返回", "Back"))
          Text(title).font(.system(size: 22, weight: .semibold)).foregroundColor(Palette.text).lineLimit(1)
          Spacer(minLength: 8)
          trailing()
        }
        .frame(height: 44)
        content()
        // nothing sits under the Jarvis key (direction.md 2.4)
        Color.clear.frame(height: 150)
      }
      .padding(.horizontal, Metrics.gutter)
      .frame(maxWidth: Metrics.maxContentWidth)
      .frame(maxWidth: .infinity)
    }
    .background(Palette.bg.ignoresSafeArea())
    .statusBarCover()
    .toolbar(.hidden, for: .navigationBar)
  }
}

extension RecordPage where Trailing == EmptyView {
  init(title: String, @ViewBuilder content: @escaping () -> Content) {
    self.init(title: title, trailing: { EmptyView() }, content: content)
  }
}

/// The weekly map's row bar (13): the sets as a capsule in the status colour, the weekly range as a band with edge ticks.
struct MuscleRangeBar: View, Equatable {
  let value: Double
  let low: Double?
  let high: Double?
  let max: Double
  let tone: Tone

  var body: some View {
    GeometryReader { g in
      let w = g.size.width
      let x = { (v: Double) in CGFloat(Swift.min(1, Swift.max(0, v / max))) * w }
      ZStack(alignment: .leading) {
        Rectangle().fill(Palette.track).frame(width: w, height: 1.5)
        if let lo = low, let hi = high {
          Rectangle().fill(tone.color.opacity(0.22)).frame(width: Swift.max(1, x(hi) - x(lo)), height: 12).offset(x: x(lo))
          Rectangle().fill(tone.color).frame(width: 1.5, height: 16).offset(x: x(lo) - 0.75)
          Rectangle().fill(tone.color).frame(width: 1.5, height: 16).offset(x: x(hi) - 0.75)
        }
        if value > 0 {
          Capsule().fill(tone.color).frame(width: Swift.max(5, x(value)), height: 5)
        }
      }
      .frame(height: g.size.height)
    }
    .frame(height: 18)
    .accessibilityHidden(true)
  }
}
