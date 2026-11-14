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

/// The sync mark (plan 2.6): icon shape = state, text = an age or a count, never a clock time.
struct SyncMark: View {
  var state: WatchSyncState
  var onTap: () -> Void

  var body: some View {
    if let p = parts {
      let (icon, color, label) = p
      Button(action: onTap) {
        HStack(spacing: 3) {
          Image(systemName: icon).font(W.font(11, .semibold)).foregroundStyle(color)
          Text(label).font(W.font(12.5)).foregroundStyle(color == W.text2 || color == W.text ? W.text2 : color)
        }
      }
      .buttonStyle(.plain)
      .accessibilityLabel(label)
    }
  }

  private var parts: (String, Color, String)? {
    switch state {
    case .signedOut, .noPlan: return nil
    case .linked(let m): return ("checkmark.circle", W.text2, SyncMark.age(m))
    case .catchingUp(let done, let total):
      return ("arrow.triangle.2.circlepath", W.text, done == 0 && total <= 1 ? tr("同步中", "Syncing") : tr("补交 \(done)/\(total)", "Sending \(done)/\(total)"))
    case .away(let n): return ("iphone.slash", W.mid, tr("待传 \(n)", "\(n) waiting"))
    case .failed: return ("exclamationmark.circle", W.bad, tr("重试", "Retry"))
    }
  }

  static func age(_ minutes: Int?) -> String {
    guard let m = minutes else { return "—" }
    if m < 1 { return tr("刚刚", "now") }
    if m < WatchParams.ageHoursAfterMin { return tr("\(m)分前", "\(m)m ago") }
    return tr("\(m / 60)时前", "\(m / 60)h ago")
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
