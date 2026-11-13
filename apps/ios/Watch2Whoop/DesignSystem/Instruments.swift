import SwiftUI

// The 2.0.1 instrument kit (docs/design/2.0.1 direction.md 2.5, kit.js): one chart language for every tab root. Value in
// the series colour, your usual in grey (a lane beside a scale, an area behind a chart), targets as ticks in the series
// colour, scales as ticks. No axes, no grid, no gradients. Plain shapes and Canvas (iOS 16).

/// The hero dial: a 1.5 pt track, a 5 pt arc from 12 o'clock, a tick scale inside (long ticks = origin and zone edges),
/// your usual as a grey lane between the arc and the ticks. The content (number, label, verdict) sits in the middle. Not
/// Equatable on purpose: the middle (verdict, reason, the 7 bars) changes with the day and the language, not only the arc.
struct TickDial<Center: View>: View {
  /// 0…1 of the scale; 0 = no arc (no data).
  let fraction: Double
  let tone: Tone
  /// Your usual on the 0…1 scale.
  var usual: ClosedRange<Double>?
  /// Zone edges on the 0…1 scale (long ticks).
  var zones: [Double] = []
  let side: CGFloat
  @ViewBuilder var center: () -> Center
  @Environment(\.accessibilityReduceMotion) private var reduceMotion

  var body: some View {
    let arcW: CGFloat = 5, r = side / 2 - arcW / 2
    ZStack {
      Circle().stroke(Palette.track, lineWidth: 1.5).frame(width: r * 2, height: r * 2)
      Canvas { ctx, size in
        let c = CGPoint(x: size.width / 2, y: size.height / 2)
        let count = side < 200 ? 50 : 100
        let outer = r - 10, minor: CGFloat = 6, major: CGFloat = 13
        for i in 0..<count {
          let f = Double(i) / Double(count)
          let isMajor = i == 0 || zones.contains { abs($0 - f) < 0.5 / Double(count) }
          let a = Angle.degrees(f * 360 - 90).radians
          let len = isMajor ? major : minor
          var p = Path()
          p.move(to: CGPoint(x: c.x + cos(a) * outer, y: c.y + sin(a) * outer))
          p.addLine(to: CGPoint(x: c.x + cos(a) * (outer - len), y: c.y + sin(a) * (outer - len)))
          ctx.stroke(p, with: .color(isMajor ? Palette.tickMajor : Palette.tick), lineWidth: isMajor ? 1.5 : 1)
        }
      }
      .frame(width: side, height: side)
      if let u = usual {
        Circle().trim(from: u.lowerBound, to: max(u.lowerBound + 0.004, u.upperBound))
          .stroke(Palette.bandLine, style: StrokeStyle(lineWidth: 3, lineCap: .round))
          .rotationEffect(.degrees(-90))
          .frame(width: (r - 6) * 2, height: (r - 6) * 2)
      }
      // the arc grows from 12 o'clock to the value (spring); with Reduce Motion it is simply there
      Circle().trim(from: 0, to: fraction)
        .stroke(tone.color, style: StrokeStyle(lineWidth: arcW, lineCap: .round))
        .rotationEffect(.degrees(-90))
        .frame(width: r * 2, height: r * 2)
        .opacity(fraction > 0 ? 1 : 0)
        .animation(reduceMotion ? nil : .spring(response: 0.6, dampingFraction: 0.9), value: fraction)
      center()
    }
    .frame(width: side, height: side)
  }
}

/// Seven small bars under the hero number: history in `past`, only the last (today) in its colour.
struct MiniBars: View {
  let values: [Double?]
  let tone: Tone
  var maxValue: Double = 100
  var height: CGFloat = 16

  var body: some View {
    HStack(alignment: .bottom, spacing: 3) {
      ForEach(Array(values.enumerated()), id: \.offset) { i, v in
        Capsule()
          .fill(i == values.count - 1 ? tone.color : Palette.past)
          .frame(width: 3.5, height: max(3.5, CGFloat((v ?? 0) / maxValue) * height))
          .opacity(v == nil ? 0.4 : 1)
      }
    }
    .frame(height: height, alignment: .bottom)
    .accessibilityHidden(true)
  }
}

/// A bullet gauge (a reading's graphic): 1.5 pt track, the value as a 4 pt capsule, your usual as a grey line under the
/// track, the target as ticks in the series colour with a 22 % tint between them, a tick scale below.
struct BulletGauge: View, Equatable {
  let value: Double?
  let max: Double
  var usual: ClosedRange<Double>?
  var target: ClosedRange<Double>?
  var step: Double
  var majors: [Double] = []
  let tone: Tone
  var scale = true

  var body: some View {
    GeometryReader { g in
      let w = g.size.width
      let x = { (v: Double) in CGFloat(TodayGlanceRules.fraction(v, max: max)) * w }
      ZStack(alignment: .topLeading) {
        Rectangle().fill(Palette.track).frame(width: w, height: 1.5).offset(y: 5.25)
        if let t = target {
          Rectangle().fill(tone.color.opacity(0.22)).frame(width: Swift.max(1, x(t.upperBound) - x(t.lowerBound)), height: 10)
            .offset(x: x(t.lowerBound), y: 1)
          ForEach(Array(TodayGlanceRules.edges(t).enumerated()), id: \.offset) { _, v in
            Rectangle().fill(tone.color).frame(width: 1.5, height: 12).offset(x: x(v) - 0.75, y: 0)
          }
        }
        if let v = value, v > 0 {
          Capsule().fill(tone.color).frame(width: Swift.max(4, x(v)), height: 4).offset(y: 4)
        }
        if let u = usual {
          Capsule().fill(Palette.bandLine).frame(width: Swift.max(3, x(u.upperBound) - x(u.lowerBound)), height: 2.5)
            .offset(x: x(u.lowerBound), y: 9)
        }
        if scale {
          Canvas { ctx, size in
            guard step > 0 else { return }
            var v = 0.0
            while v <= max + 0.0001 {
              let isMajor = v == 0 || abs(v - max) < 0.0001 || majors.contains(v)
              let px = Swift.min(size.width - 0.75, Swift.max(0.75, CGFloat(v / max) * size.width))
              var p = Path()
              p.move(to: CGPoint(x: px, y: 0))
              p.addLine(to: CGPoint(x: px, y: isMajor ? 6 : 4))
              ctx.stroke(p, with: .color(isMajor ? Palette.tickMajor : Palette.tick), lineWidth: 1)
              v += step
            }
          }
          .frame(width: w, height: 6)
          .offset(y: 16)
        }
      }
    }
    .frame(height: scale ? 22 : 13)
    .accessibilityHidden(true)
  }
}

/// A face (module) on a tab root: icon + label, one number, one graphic at the bottom (direction.md 3).
struct Face<Graphic: View>: View {
  let icon: Icon.Name
  let label: String
  var number: String?
  var unit: String?
  var dimmed = false
  /// Attention dot (mid / bad) at the top right.
  var dot: Tone?
  /// Stale age at the right of the header (mid).
  var age: String?
  @ViewBuilder var graphic: () -> Graphic

  var body: some View {
    VStack(alignment: .leading, spacing: 0) {
      HStack(spacing: 5) {
        Icon(age != nil ? .refreshCw : icon, size: 13)
        Text(label).font(.system(size: 12, weight: .medium)).lineLimit(1).minimumScaleFactor(0.8)
        Spacer(minLength: 0)
        if let age { Text(age).font(.system(size: 12, weight: .medium).monospacedDigit()).foregroundColor(Palette.mid).lineLimit(1) }
        if let dot { Circle().fill(dot.color).frame(width: 6, height: 6) }
      }
      .foregroundColor(Palette.text2)
      .frame(height: 16)
      if let number {
        HStack(alignment: .firstTextBaseline, spacing: 3) {
          Text(number).font(Typo.face).foregroundColor(dimmed ? Palette.text2 : Palette.text).lineLimit(1).minimumScaleFactor(0.7)
          if let unit { Text(unit).font(Typo.unit).foregroundColor(Palette.text2).lineLimit(1) }
        }
        .padding(.top, 10)
      }
      Spacer(minLength: 4)
      graphic().frame(height: 26, alignment: .bottom)
    }
    .padding(.horizontal, 12).padding(.vertical, 14)
    .frame(maxWidth: .infinity, minHeight: 120, maxHeight: 120, alignment: .topLeading)
    .background(RoundedRectangle(cornerRadius: Metrics.cardRadius, style: .continuous).fill(Palette.surface))
    .contentShape(RoundedRectangle(cornerRadius: Metrics.cardRadius, style: .continuous))
  }
}

/// The sync capsule at the top right of a tab root: icon shape + age (never a clock time).
struct SyncCapsule: View {
  let state: SyncCapsuleState
  @State private var turning = false
  @Environment(\.accessibilityReduceMotion) private var reduceMotion

  var body: some View {
    HStack(spacing: 6) {
      icon
      Text(text).font(.system(size: 13, weight: .medium).monospacedDigit()).lineLimit(1)
    }
    .foregroundColor(color)
    .padding(.horizontal, 12)
    .frame(height: 32)
    .background(Capsule().fill(Palette.surface))
    .frame(minHeight: 44)
    .contentShape(Rectangle())
    .accessibilityElement(children: .combine)
  }

  @ViewBuilder private var icon: some View {
    switch state {
    case .ok: Icon(.circleCheck, size: 15).foregroundColor(Palette.text2)
    case .busy:
      Icon(.refreshCw, size: 15).foregroundColor(Palette.text)
        .rotationEffect(.degrees(turning ? 360 : 0))
        .onAppear {
          guard !reduceMotion else { return }
          withAnimation(.linear(duration: 1.1).repeatForever(autoreverses: false)) { turning = true }
        }
        .onDisappear { turning = false }   // the next sync turns it again
    case .stale: Icon(.refreshCw, size: 15).foregroundColor(Palette.mid)
    case .behind: Icon(.circleAlert, size: 15).foregroundColor(Palette.mid)
    case .off: Icon(.circleAlert, size: 15).foregroundColor(Palette.text2)
    case .failed: Icon(.circleAlert, size: 15).foregroundColor(Palette.bad)
    }
  }

  private var text: String {
    switch state {
    case .ok(let ago): return ago ?? tr("已同步", "Synced")
    case .busy: return tr("同步中", "Syncing")
    case .stale(let s): return s
    case .behind(let s): return s
    case .off: return tr("未开同步", "Sync off")
    case .failed: return tr("重试", "Retry")
    }
  }

  private var color: Color {
    switch state {
    case .ok: return Palette.text2
    case .busy: return Palette.text2
    case .stale, .behind: return Palette.mid
    case .off: return Palette.text2
    case .failed: return Palette.bad
    }
  }
}

/// A 14-day line on your usual band (L1 rows): band as an area with the line in `spark`, a dot on today.
struct BandSpark: View, Equatable {
  let values: [Double?]
  var usual: ClosedRange<Double>?

  var body: some View {
    Canvas { ctx, size in
      let pts = values.enumerated().compactMap { i, v in v.map { (i, $0) } }
      guard pts.count > 1 else { return }
      var lo = pts.map(\.1).min()!, hi = pts.map(\.1).max()!
      if let u = usual { lo = Swift.min(lo, u.lowerBound); hi = Swift.max(hi, u.upperBound) }
      let span = hi - lo == 0 ? 1 : hi - lo, n = CGFloat(Swift.max(1, values.count - 1))
      let y = { (v: Double) in size.height - 3 - CGFloat((v - lo) / span) * (size.height - 6) }
      if let u = usual {
        let r = CGRect(x: 0, y: y(u.upperBound), width: size.width, height: Swift.max(1, y(u.lowerBound) - y(u.upperBound)))
        ctx.fill(Path(roundedRect: r, cornerRadius: 2), with: .color(Palette.band))
      }
      var p = Path()
      for (k, (i, v)) in pts.enumerated() {
        let pt = CGPoint(x: CGFloat(i) / n * (size.width - 3), y: y(v))
        if k == 0 { p.move(to: pt) } else { p.addLine(to: pt) }
      }
      ctx.stroke(p, with: .color(Palette.spark), style: StrokeStyle(lineWidth: 1.4, lineCap: .round, lineJoin: .round))
      if let last = pts.last, last.0 == values.count - 1 {
        let c = CGPoint(x: CGFloat(last.0) / n * (size.width - 3), y: y(last.1))
        ctx.fill(Path(ellipseIn: CGRect(x: c.x - 3, y: c.y - 3, width: 6, height: 6)), with: .color(Palette.text))
      }
    }
    .accessibilityHidden(true)
  }
}

/// Bars of a score over days (recovery L1): your usual as an area with dashed edges, history in `past`, today in colour.
struct UsualBars: View, Equatable {
  let values: [Double?]
  var usual: ClosedRange<Double>?
  var max: Double = 100
  let tone: Tone

  var body: some View {
    GeometryReader { g in
      let w = g.size.width, h = g.size.height, n = Swift.max(1, values.count)
      let gap: CGFloat = 4, bw = Swift.max(2, (w - gap * CGFloat(n - 1)) / CGFloat(n))
      let y = { (v: Double) in h - CGFloat(Swift.min(1, Swift.max(0, v / max))) * h }
      ZStack(alignment: .topLeading) {
        if let u = usual {
          Rectangle().fill(Palette.band).frame(width: w, height: Swift.max(1, y(u.lowerBound) - y(u.upperBound))).offset(y: y(u.upperBound))
          ForEach(Array(TodayGlanceRules.edges(u).enumerated()), id: \.offset) { _, v in
            Path { p in p.move(to: CGPoint(x: 0, y: y(v))); p.addLine(to: CGPoint(x: w, y: y(v))) }
              .stroke(Palette.bandLine, style: StrokeStyle(lineWidth: 1, dash: [2, 2]))
          }
        }
        ForEach(Array(values.enumerated()), id: \.offset) { i, v in
          if let v {
            let today = i == values.count - 1
            RoundedRectangle(cornerRadius: bw / 2, style: .continuous)
              .fill(today ? tone.color : Palette.past)
              .frame(width: bw, height: Swift.max(bw, h - y(v)))
              .offset(x: CGFloat(i) * (bw + gap), y: y(v))
          }
        }
        Rectangle().fill(Palette.hairStrong).frame(width: w, height: 1).offset(y: h)
      }
    }
    .accessibilityHidden(true)
  }
}
