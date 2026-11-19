import SwiftUI

/// The one primary button of a screen (mockups: full width, in the bottom corner radius). `primaryAction`: Double Tap
/// presses it (plan 2.1; on watches without Double Tap it is an ordinary button).
struct PrimaryButton: View {
  enum Kind { case primary, quiet }
  var label: String
  var icon: String?
  var kind: Kind = .primary
  var height: CGFloat = 46
  var doubleTap = true
  var action: () -> Void

  var body: some View {
    let b = Button(action: action) {
      HStack(spacing: 6) {
        if let icon { Image(systemName: icon).font(W.font(15, .bold)) }
        Text(label).font(W.font(17, .semibold))
      }
      .foregroundStyle(kind == .primary ? Color.black : W.text)
      .frame(maxWidth: .infinity, minHeight: height * W.k)
      .background(Capsule().fill(kind == .primary ? W.text : W.surface2))
    }
    .buttonStyle(.plain)
    if doubleTap {
      b.handGestureShortcut(.primaryAction)
    } else {
      b
    }
  }
}

/// Progress of one exercise: one pill per set (done = train, current = ink, to do = track), and 「2/4」.
struct SetPills: View {
  var total: Int
  var done: Int
  var current: Int?

  var body: some View {
    HStack(spacing: 6) {
      HStack(spacing: 3) {
        ForEach(0..<max(total, 1), id: \.self) { i in
          Capsule().fill(i < done ? W.train : i == current ? W.text : W.todo).frame(height: 3)
        }
      }
      Text("\(min(max(current.map { $0 + 1 } ?? done, 0), total))/\(total)").font(W.font(12.5)).foregroundStyle(W.text2)
    }
  }
}

/// The sync mark (plan 2.6): icon shape = state, text = an age or a count, never a clock time (the phone capsule's icons).
struct SyncMark: View {
  var state: WatchSyncState
  /// Size factor (home draws it at the canvas's scale; elsewhere the watch's `W.k`).
  var scale: CGFloat = W.k
  var onTap: () -> Void

  var body: some View {
    if let p = parts {
      let (icon, color, label) = p
      Button(action: onTap) {
        HStack(spacing: 3 * scale) {
          Image(systemName: icon).font(.system(size: 11 * scale, weight: .semibold)).foregroundStyle(color)
          Text(label).font(.system(size: 12.5 * scale, weight: .medium).monospacedDigit())
            .foregroundStyle(color == W.text2 || color == W.text ? W.text2 : color)
            .lineLimit(1).fixedSize()
        }
      }
      .buttonStyle(.plain)
      .accessibilityLabel(label)
    }
  }

  private var parts: (String, Color, String)? {
    switch state {
    case .signedOut: return nil
    case .linked(let m): return ("checkmark.circle", W.text2, WatchSyncState.age(m))
    case .phoneSyncing: return ("arrow.triangle.2.circlepath", W.text, tr("同步中", "Syncing"))
    case .catchingUp(let done, let total):
      return ("arrow.triangle.2.circlepath", W.text, done == 0 && total <= 1 ? tr("同步中", "Syncing") : tr("补交 \(done)/\(total)", "Sending \(done)/\(total)"))
    case .away(let n): return ("iphone.slash", W.mid, tr("待传 \(n)", "\(n) waiting"))
    case .failed: return ("exclamationmark.circle", W.bad, tr("重试", "Retry"))
    case .phoneFailed: return ("exclamationmark.circle", W.bad, tr("重试", "Retry"))
    case .offline: return ("wifi.slash", W.mid, tr("离线", "Offline"))
    case .behind(let l): return ("exclamationmark.circle", W.mid, l)
    case .stale(let m): return ("arrow.triangle.2.circlepath", W.mid, WatchSyncState.age(m))
    }
  }
}

/// Heart rate row (T2 / T6): ♥ 132 bpm.
struct HeartRate: View {
  var bpm: Int?

  var body: some View {
    HStack(alignment: .firstTextBaseline, spacing: 3) {
      Image(systemName: "heart").font(W.font(12, .semibold)).foregroundStyle(W.text2)
      Text(bpm.map(String.init) ?? "—").font(W.font(19))
      Text("bpm").font(W.font(10.5)).foregroundStyle(W.text2)
    }
  }
}

/// Arcs of every planned set of the session, with a wider gap between exercises (the phone's set ring; T1 / T9).
struct SetRing: View {
  var plan: [Int]
  var done: Int
  var current: Int?
  var lineWidth: CGFloat = 4

  var body: some View {
    Canvas { ctx, size in
      let tot = max(1, plan.reduce(0, +))
      let n = Double(plan.count)
      let each = (360 - Double(tot - plan.count) * 3 - n * 9) / Double(tot)
      let c = CGPoint(x: size.width / 2, y: size.height / 2)
      let r = min(size.width, size.height) / 2 - lineWidth / 2
      var a = 4.5, k = 0
      for sets in plan {
        for _ in 0..<sets {
          var p = Path()
          p.addArc(center: c, radius: r, startAngle: .degrees(a - 90), endAngle: .degrees(a + each - 90), clockwise: false)
          let col = k < done ? W.train : k == current ? W.text : W.todo
          ctx.stroke(p, with: .color(col), style: StrokeStyle(lineWidth: lineWidth, lineCap: .round))
          a += each + 3
          k += 1
        }
        a += 6
      }
    }
  }
}

/// Turns Digital Crown rotation into whole detents (+1 / -1 …) for `onStep`.
struct CrownSteps: ViewModifier {
  var onStep: (Int) -> Void
  @State private var value = 0.0
  @State private var last = 0

  func body(content: Content) -> some View {
    content
      .focusable()
      .digitalCrownRotation($value, from: -100_000, through: 100_000, by: 1, sensitivity: .low, isContinuous: false,
                            isHapticFeedbackEnabled: true)
      .onChange(of: value) { _, v in
        let n = Int(v.rounded())
        if n != last {
          onStep(n - last)
          last = n
        }
      }
  }
}

extension View {
  func crownSteps(_ onStep: @escaping (Int) -> Void) -> some View { modifier(CrownSteps(onStep: onStep)) }
}

// MARK: - phase 2a: home and quick log

/// The Jarvis core (mockups `core()`, the phone's approved geometry): a dark disc, 24 ticks, three segments, an outlined
/// inverted triangle with a lit inner one. Drawn in a 58 × 58 box scaled to `size`. `dim`: voice is not available
/// (mockup S7: the inner triangle at 35 %, no glow; `WatchVoice`).
struct JarvisCoreMark: View {
  var size: CGFloat
  var dim = false

  var body: some View {
    Canvas { ctx, s in
      let k = s.width / 58
      ctx.scaleBy(x: k, y: k)
      let c = CGPoint(x: 29, y: 29)
      func p(_ r: CGFloat, _ deg: Double) -> CGPoint {
        let a = deg * .pi / 180
        return CGPoint(x: c.x + r * CGFloat(sin(a)), y: c.y - r * CGFloat(cos(a)))
      }
      func tri(_ r: CGFloat) -> Path {
        var t = Path()
        t.move(to: p(r, 60)); t.addLine(to: p(r, 180)); t.addLine(to: p(r, 300)); t.closeSubpath()
        return t
      }
      let disc = Path(ellipseIn: CGRect(x: 0.5, y: 0.5, width: 57, height: 57))
      ctx.fill(disc, with: .color(WJarvis.core))
      ctx.stroke(disc, with: .color(Color.white.opacity(0.22)), lineWidth: 1 / k)
      for i in 0..<24 {
        var t = Path()
        t.move(to: p(22, Double(i) * 15))
        t.addLine(to: p(i % 6 == 0 ? 26.5 : 25, Double(i) * 15))
        ctx.stroke(t, with: .color(W.text2.opacity(i % 6 == 0 ? 0.9 : 0.55)), lineWidth: 1 / k)
      }
      for i in 0..<3 {
        var a = Path()
        let a0 = Double(i) * 120 + 12
        for j in 0...24 {
          let q = p(17.5, a0 + 84 * Double(j) / 24)
          if j == 0 { a.move(to: q) } else { a.addLine(to: q) }
        }
        ctx.stroke(a, with: .color(W.text.opacity(0.9)), style: StrokeStyle(lineWidth: 1.5 * max(0.8, k) / k, lineCap: .round))
      }
      ctx.stroke(tri(11.5), with: .color(W.text), style: StrokeStyle(lineWidth: 1.6 * max(0.8, k) / k, lineJoin: .round))
      var lit = ctx
      if !dim { lit.addFilter(.shadow(color: WJarvis.ice.opacity(0.6), radius: 2.5 / k)) }
      lit.opacity = dim ? 0.35 : 1
      lit.fill(tri(4.6), with: .color(WJarvis.ice))
      lit.stroke(tri(4.6), with: .color(WJarvis.ice), style: StrokeStyle(lineWidth: 1 / k, lineJoin: .round))
    }
    .frame(width: size, height: size)
    .accessibilityHidden(true)
  }
}

/// The recovery dial of home (mockup `SC.today`): 50 ticks with long ones at the zone edges, a thin track, your usual as a
/// grey lane, the arc of the value in its zone colour, 「恢复」, the number with %, the verdict. Missing = 「—」 and no
/// arc; grey (S3) = everything in text2, the arc at 60 %.
struct RecoveryDial: View {
  var home: WatchHome

  var body: some View {
    GeometryReader { g in
      let d = min(g.size.width, g.size.height)
      ZStack {
        ring(k: d / CGFloat(2 * HomeLayout.dialOuter))   // the mockup's dial: arc radius 57 pt, 4 pt stroke
        labels(k: d / CGFloat(2 * HomeLayout.dialOuter))
      }
      .frame(width: d, height: d)
      .frame(maxWidth: .infinity, maxHeight: .infinity)
    }
    .accessibilityElement(children: .ignore)
    .accessibilityLabel(accessibility)
  }

  private var accessibility: String {
    let v = home.recovery.map { "\(Int($0.rounded()))%" } ?? "—"
    return tr("恢复", "Recovery") + " " + v + " " + (home.verdict ?? "")
  }

  private func ring(k: CGFloat) -> some View {
    Canvas { ctx, size in
      let c = CGPoint(x: size.width / 2, y: size.height / 2)
      let r = CGFloat(HomeLayout.dialRadius) * k
      func p(_ rr: CGFloat, _ deg: Double) -> CGPoint {
        let a = deg * .pi / 180
        return CGPoint(x: c.x + rr * CGFloat(sin(a)), y: c.y - rr * CGFloat(cos(a)))
      }
      for i in 0..<50 {
        let major = i == 0 || i == 17 || i == 33
        let inner: CGFloat = major ? 11 : 8.5
        let outer: CGFloat = major ? 4 : 5.5
        var t = Path()
        t.move(to: p(r - inner * k, Double(i) * 7.2))
        t.addLine(to: p(r - outer * k, Double(i) * 7.2))
        ctx.stroke(t, with: .color(major ? W.tickMajor : W.tick), lineWidth: major ? 1.5 : 1)
      }
      ctx.stroke(Path(ellipseIn: CGRect(x: c.x - r, y: c.y - r, width: 2 * r, height: 2 * r)), with: .color(W.track), lineWidth: 1.5)
      if let u = home.usual {
        var lane = Path()
        lane.addArc(center: c, radius: r - 3.5 * k, startAngle: .degrees(u.lowerBound * 3.6 - 90),
                    endAngle: .degrees(u.upperBound * 3.6 - 90), clockwise: false)
        ctx.stroke(lane, with: .color(W.text2.opacity(0.62)), style: StrokeStyle(lineWidth: 2, lineCap: .round))
      }
      if let v = home.recovery {
        let end = max(1, 3.6 * min(100, max(0, v)))
        var arc = Path()
        arc.addArc(center: c, radius: r, startAngle: .degrees(-90), endAngle: .degrees(end - 90), clockwise: false)
        let col: Color = home.grey ? W.text2.opacity(0.6) : W.tone(home.tone)
        ctx.stroke(arc, with: .color(col), style: StrokeStyle(lineWidth: 4, lineCap: .round))
      }
    }
  }

  /// 「恢复」, the number and the word, placed from the dial's centre (`HomeLayout`): the number's digits centred on it
  /// with 「%」 hung beside them, the two labels by their baselines.
  private func labels(k: CGFloat) -> some View {
    let L = HomeLayout.self
    return ZStack {
      Color.clear
      Text(tr("恢复", "Recovery")).font(.system(size: 10.5 * k, weight: .medium)).tracking((Lang.isZh ? 2 : 0.5) * k)
        .foregroundStyle(W.text2).fixedSize()
        .centredBaseline(CGFloat(L.labelBaseline) * k)
      CenteredNumber(text: home.recovery.map { String(Int($0.rounded())) } ?? "—", size: CGFloat(L.numberSize) * k,
                     color: home.grey ? W.text2 : W.text, unit: home.recovery == nil ? nil : "%", unitSize: 12 * k,
                     unitGap: CGFloat(L.unitGap) * k, unitRaise: CGFloat(L.unitRaise) * k)
      Text(home.verdict ?? home.reason ?? " ").font(.system(size: 11.5 * k, weight: .medium))
        .foregroundStyle(home.grey || home.verdict == nil ? W.text2 : W.text).lineLimit(1).minimumScaleFactor(0.6)
        .frame(maxWidth: 66 * k)   // the chord inside the ticks at that height
        .centredBaseline(CGFloat(L.wordBaseline) * k)
    }
  }
}

/// A full-screen canvas in the mockups' coordinates (home; plan 3: one screen = one drawing): children are placed in
/// screen points with `place(...)`, the safe area is ignored (watchOS 26 draws the clock over the content), `s` scales the
/// mockups' 208 pt width to this watch and `clock` is the system clock's row (`ClockRow`).
struct DesignCanvas<Content: View>: View {
  let content: (CanvasMetrics) -> Content

  init(@ViewBuilder content: @escaping (CanvasMetrics) -> Content) {
    self.content = content
  }

  var body: some View {
    GeometryReader { g in
      ZStack(alignment: .topLeading) {
        Color.clear.frame(width: g.size.width, height: g.size.height)
        content(CanvasMetrics(size: g.size))
      }
    }
    .ignoresSafeArea()
  }
}

/// The screen a `DesignCanvas` draws on.
struct CanvasMetrics {
  var size: CGSize
  /// The mockups' 208 pt width → this watch.
  var s: CGFloat { size.width / CGFloat(HomeLayout.designWidth) }
  var sy: CGFloat { size.height / CGFloat(HomeLayout.designHeight) }
  var clock: ClockRow.Row { ClockRow.row(width: Double(size.width), height: Double(size.height)) }
}

extension View {
  /// In a `DesignCanvas`: the view's centre at (x, y).
  func place(centerX x: CGFloat, centerY y: CGFloat) -> some View {
    alignmentGuide(HorizontalAlignment.leading) { $0[HorizontalAlignment.center] - x }
      .alignmentGuide(VerticalAlignment.top) { $0[VerticalAlignment.center] - y }
  }

  /// In a `DesignCanvas`: the view's leading edge at `x`, its vertical centre at `y`.
  func place(leading x: CGFloat, centerY y: CGFloat) -> some View {
    alignmentGuide(HorizontalAlignment.leading) { $0[HorizontalAlignment.leading] - x }
      .alignmentGuide(VerticalAlignment.top) { $0[VerticalAlignment.center] - y }
  }

  /// In a `DesignCanvas`: a text's leading edge at `x`, its baseline at `y`.
  func place(leading x: CGFloat, baseline y: CGFloat) -> some View {
    alignmentGuide(HorizontalAlignment.leading) { $0[HorizontalAlignment.leading] - x }
      .alignmentGuide(VerticalAlignment.top) { $0[.lastTextBaseline] - y }
  }

  /// In a `DesignCanvas`: a text's trailing edge at `x`, its baseline at `y`.
  func place(trailing x: CGFloat, baseline y: CGFloat) -> some View {
    alignmentGuide(HorizontalAlignment.leading) { $0[HorizontalAlignment.trailing] - x }
      .alignmentGuide(VerticalAlignment.top) { $0[.lastTextBaseline] - y }
  }
}

/// One reading under the dial (mockup: 「睡眠 88」 / 「负荷 2.8」 with a bar on a thin track).
struct HomeReading: View {
  var label: String
  var value: Double?
  var digits: Int
  var max: Double
  var color: Color
  var grey: Bool

  var body: some View {
    VStack(spacing: 4 * W.k) {
      HStack(alignment: .lastTextBaseline) {
        Text(label).font(W.font(10.5)).foregroundStyle(W.text2)
        Spacer(minLength: 2)
        Text(value.map { digits == 0 ? String(Int($0.rounded())) : String(format: "%.\(digits)f", $0) } ?? "—")
          .font(W.font(19)).foregroundStyle(grey ? W.text2 : W.text)
      }
      GeometryReader { g in
        ZStack(alignment: .leading) {
          Capsule().fill(W.track).frame(height: 1.5)
          if let v = value {
            Capsule().fill(grey ? W.text2 : color)
              .frame(width: Swift.max(4, g.size.width * CGFloat(Swift.min(1, Swift.max(0, v / max)))), height: 4)
          }
        }
        .frame(height: 4)
      }
      .frame(height: 4)
    }
    .accessibilityElement(children: .combine)
  }
}

/// A quick-log tile (mockup M5): icon, name, the small line (kcal / +ml). The check replaces the icon right after a tap.
struct QuickTile: View {
  var icon: String
  var name: String
  var sub: String
  var action: () -> Void
  @State private var hot = false

  var body: some View {
    Button {
      hot = true
      action()
    } label: {
      VStack(alignment: .leading, spacing: 0) {
        Image(systemName: hot ? "checkmark" : icon).font(W.font(14, .semibold)).foregroundStyle(hot ? W.good : W.text2)
          .frame(height: 18 * W.k)
        Spacer(minLength: 2)
        Text(name).font(W.font(16, .semibold)).foregroundStyle(W.text).lineLimit(1).minimumScaleFactor(0.7)
        Text(sub).font(W.font(11)).foregroundStyle(W.text2).lineLimit(1)
      }
      .padding(.horizontal, 12 * W.k)
      .padding(.vertical, 12 * W.k)
      .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
      .background(RoundedRectangle(cornerRadius: 22 * W.k, style: .continuous).fill(hot ? W.surface2 : W.surface))
    }
    .buttonStyle(.plain)
  }
}

/// Numbers in the receipt: 「1,180」.
enum WatchFmt {
  static func int(_ v: Double) -> String {
    let f = NumberFormatter()
    f.locale = Locale(identifier: "en_US_POSIX")
    f.numberStyle = .decimal
    f.usesGroupingSeparator = true
    f.groupingSeparator = ","
    f.groupingSize = 3
    f.maximumFractionDigits = 0
    return f.string(from: NSNumber(value: v.rounded())) ?? String(Int(v.rounded()))
  }
}
