import SwiftUI

// Building blocks of the record pages (N5: 饮食, 记录, 健康档案), in the Today page's look: the `.seg` switch, the
// toast with an action (「撤销」), a day switcher, short state badges, key–value rows, a stepper and the small ring.

/// The page's segmented switch (`.seg`): surface track, the chosen item on the page background.
struct SegmentedControl<T: Hashable>: View {
  let items: [(T, String)]
  @Binding var selection: T

  var body: some View {
    HStack(spacing: 2) {
      ForEach(items, id: \.0) { item in
        let on = item.0 == selection
        Button { withAnimation(.easeOut(duration: 0.15)) { selection = item.0 } } label: {
          Text(item.1).font(.system(size: 14, weight: on ? .semibold : .medium))
            .foregroundColor(on ? Palette.text : Palette.text2)
            .lineLimit(1).minimumScaleFactor(0.8)
            .frame(maxWidth: .infinity, minHeight: 32)
            .background(RoundedRectangle(cornerRadius: 9, style: .continuous).fill(on ? Palette.bg : .clear)
              .shadow(color: .black.opacity(on ? 0.06 : 0), radius: 2, y: 1))
        }
        .buttonStyle(.plain)
        .accessibilityAddTraits(on ? .isSelected : [])
      }
    }
    .padding(3)
    .background(RoundedRectangle(cornerRadius: 12, style: .continuous).fill(Palette.surface))
  }
}

/// A short message at the bottom with an optional action, gone after a few seconds (the page's `toast()`).
struct Toast: Equatable, Identifiable {
  let id = UUID()
  var text: String
  var actionTitle: String?
  var action: (() -> Void)?

  init(_ text: String, actionTitle: String? = nil, action: (() -> Void)? = nil) {
    self.text = text
    self.actionTitle = actionTitle
    self.action = action
  }

  static func == (a: Toast, b: Toast) -> Bool { a.id == b.id }
}

private struct ToastModifier: ViewModifier {
  @Binding var toast: Toast?

  func body(content: Content) -> some View {
    content.overlay(alignment: .bottom) {
      if let t = toast {
        HStack(spacing: 12) {
          Text(t.text).font(Typo.caption).foregroundColor(Palette.bg).lineLimit(2)
          Spacer(minLength: 4)
          if let title = t.actionTitle, let action = t.action {
            Button(title) {
              toast = nil
              action()
            }
            .font(.system(size: 14, weight: .semibold)).foregroundColor(Palette.bg)
          }
        }
        .padding(.horizontal, 16).padding(.vertical, 12)
        .background(RoundedRectangle(cornerRadius: 14, style: .continuous).fill(Palette.text))
        .padding(.horizontal, Metrics.gutter).padding(.bottom, 12)
        .transition(.move(edge: .bottom).combined(with: .opacity))
        .task(id: t.id) {
          try? await Task.sleep(nanoseconds: 4_500_000_000)
          if toast?.id == t.id { withAnimation { toast = nil } }
        }
        .accessibilityElement(children: .contain)
      }
    }
    .animation(.spring(response: 0.3, dampingFraction: 0.85), value: toast)
  }
}

extension View {
  func toast(_ toast: Binding<Toast?>) -> some View { modifier(ToastModifier(toast: toast)) }
}

/// ‹ 10/28 › with 「今天」: looks back at another day; never past the user's today.
struct DayStepper: View {
  let day: String
  let today: String
  let change: (String) -> Void

  var body: some View {
    HStack(spacing: 4) {
      Button { change(RecordDay.add(day, -1)) } label: {
        Icon(.chevronLeft, size: 18).foregroundColor(Palette.text2).frame(width: 36, height: 36)
      }
      .accessibilityLabel(tr("前一天", "Previous day"))
      Text(day == today ? tr("今天", "Today") : day == RecordDay.add(today, -1) ? tr("昨天", "Yesterday") : RecordDay.short(day))
        .font(.system(size: 15, weight: .semibold)).foregroundColor(Palette.text)
        .frame(minWidth: 56)
      Button { change(RecordDay.add(day, 1)) } label: {
        Icon(.chevronRight, size: 18).foregroundColor(day >= today ? Palette.text3 : Palette.text2).frame(width: 36, height: 36)
      }
      .disabled(day >= today)
      .accessibilityLabel(tr("后一天", "Next day"))
      if day != today {
        Button(tr("回到今天", "Back to today")) { change(today) }
          .font(.system(size: 13, weight: .medium)).foregroundColor(Palette.text2)
      }
    }
    .buttonStyle(PressScale())
  }
}

/// A short state label with a soft background (the page's `.badge good|mid|bad|plain`).
struct Badge: View {
  let label: String
  var tone: Tone = .text

  var body: some View {
    Text(label).font(.system(size: 12, weight: .medium))
      .foregroundColor(tone == .text ? Palette.text2 : tone.color)
      .padding(.vertical, 3).padding(.horizontal, 8)
      .background(Capsule().fill(background))
      .lineLimit(1)
  }

  private var background: Color {
    switch tone {
    case .good: return Palette.goodSoft
    case .mid: return Palette.midSoft
    case .bad: return Palette.badSoft
    default: return Palette.surface2
    }
  }
}

/// Label left, value right (the page's `kv()`), with a hairline above.
struct KeyValueRow: View {
  let label: String
  let value: String
  var tone: Tone = .text

  var body: some View {
    HStack(alignment: .firstTextBaseline) {
      Text(label).font(Typo.body).foregroundColor(Palette.text2)
      Spacer(minLength: 12)
      Text(value).font(Typo.body.monospacedDigit()).foregroundColor(tone.color).multilineTextAlignment(.trailing)
    }
    .padding(.vertical, 10)
    .overlay(Rectangle().fill(Palette.hair).frame(height: 1), alignment: .top)
    .accessibilityElement(children: .combine)
  }
}

/// − value + (servings, portions).
struct ValueStepper: View {
  let text: String
  let minus: () -> Void
  let plus: () -> Void
  var canMinus = true
  var canPlus = true

  var body: some View {
    HStack(spacing: 0) {
      Button(action: minus) { Icon(.minus, size: 18).frame(width: 44, height: 40) }
        .disabled(!canMinus).accessibilityLabel(tr("减少", "Less"))
      Text(text).font(.system(size: 15, weight: .semibold).monospacedDigit()).frame(minWidth: 64)
      Button(action: plus) { Icon(.plus, size: 18).frame(width: 44, height: 40) }
        .disabled(!canPlus).accessibilityLabel(tr("增加", "More"))
    }
    .foregroundColor(Palette.text)
    .background(Capsule().fill(Palette.inSheet))
    .buttonStyle(PressScale())
  }
}

/// A pending write's mark: a clock while queued or sending, an alert when the server refused it.
struct PendingMark: View {
  let needsAttention: Bool

  var body: some View {
    Group {
      if needsAttention {
        Icon(.circleAlert, size: 14).foregroundColor(Palette.bad)
          .accessibilityLabel(tr("没提交", "Not sent"))
      } else {
        Icon(.clock, size: 14).foregroundColor(Palette.text3)
          .accessibilityLabel(tr("等待提交", "Waiting to be sent"))
      }
    }
  }
}

/// A small progress ring with the amount inside and the target under it (food: kcal, protein, carbs, fat).
struct GoalRing: View {
  let label: String
  let value: Double?
  let goal: Double?
  var unit = ""
  var tone: Tone = .text
  var side: CGFloat = 68

  private var fraction: Double { (goal ?? 0) > 0 ? (value ?? 0) / goal! : 0 }
  private var over: Bool { fraction > 1.1 }

  var body: some View {
    VStack(spacing: 6) {
      ZStack {
        Circle().stroke(Palette.surface2, lineWidth: 7)
        Circle().trim(from: 0, to: min(1, max(0, fraction)))
          .stroke(over ? Palette.bad : tone.color, style: StrokeStyle(lineWidth: 7, lineCap: .round))
          .rotationEffect(.degrees(-90))
          .animation(.spring(response: 0.6, dampingFraction: 0.85), value: fraction)
        VStack(spacing: 0) {
          Text(RecordFormat.num(value)).font(.system(size: side * 0.24, weight: .bold).monospacedDigit())
            .foregroundColor(value == nil ? Palette.text3 : Palette.text).lineLimit(1).minimumScaleFactor(0.6)
          if !unit.isEmpty { Text(unit).font(.system(size: 10, weight: .medium)).foregroundColor(Palette.text3) }
        }
        .padding(.horizontal, 9)
      }
      .frame(width: side, height: side)
      VStack(spacing: 1) {
        Text(label).font(Typo.label).foregroundColor(Palette.text2)
        Text(goal.map { "/ \(RecordFormat.num($0))" } ?? "—").font(.system(size: 11).monospacedDigit()).foregroundColor(Palette.text3)
      }
    }
    .frame(maxWidth: .infinity)
    .accessibilityElement(children: .ignore)
    .accessibilityLabel("\(label) \(RecordFormat.num(value)) / \(goal.map { RecordFormat.num($0) } ?? "—") \(unit)")
  }
}

/// A faint centred line for an empty block.
struct EmptyNote: View {
  let text: String
  var body: some View {
    Text(text).font(Typo.caption).foregroundColor(Palette.text3)
      .frame(maxWidth: .infinity).padding(.vertical, 14)
  }
}

/// One line under the title while offline or refreshing (cached data stays on screen).
struct RecordStatusLine: View {
  let offline: Bool
  let refreshing: Bool

  var body: some View {
    if offline || refreshing {
      HStack(spacing: 6) {
        if refreshing { ProgressView().scaleEffect(0.7).tint(Palette.text3) }
        Text(offline ? tr("离线，显示上次的数据", "Offline, showing the last data") : tr("更新中", "Updating"))
      }
      .font(Typo.label).foregroundColor(Palette.text3)
      .frame(maxWidth: .infinity)
    }
  }
}

/// Days as the server writes them (`YYYY-MM-DD`, the user's local day), for the record pages.
enum RecordDay {
  static func string(_ d: Date, _ zone: TimeZone = .current) -> String {
    var cal = Calendar(identifier: .gregorian)
    cal.timeZone = zone
    let c = cal.dateComponents([.year, .month, .day], from: d)
    return String(format: "%04d-%02d-%02d", c.year ?? 0, c.month ?? 0, c.day ?? 0)
  }

  static func date(_ s: String) -> Date? {
    let p = s.prefix(10).split(separator: "-").compactMap { Int($0) }
    guard p.count == 3 else { return nil }
    var cal = Calendar(identifier: .gregorian)
    cal.timeZone = TimeZone(identifier: "UTC")!
    return cal.date(from: DateComponents(year: p[0], month: p[1], day: p[2], hour: 12))
  }

  static func add(_ s: String, _ days: Int) -> String {
    guard let d = date(s) else { return s }
    return string(d.addingTimeInterval(Double(days) * 86_400), TimeZone(identifier: "UTC")!)
  }

  /// 1 = Monday … 7 = Sunday.
  static func weekday(_ s: String) -> Int {
    guard let d = date(s) else { return 1 }
    var cal = Calendar(identifier: .gregorian)
    cal.timeZone = TimeZone(identifier: "UTC")!
    return (cal.component(.weekday, from: d) + 5) % 7 + 1
  }

  static func monday(_ s: String) -> String { add(s, 1 - weekday(s)) }

  /// "10/28" (zh) / "28 Oct" (en).
  static func short(_ s: String) -> String {
    guard s.count >= 10, let m = Int(s.dropFirst(5).prefix(2)), let d = Int(s.dropFirst(8).prefix(2)) else { return s }
    let months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    return tr("\(m)/\(d)", "\(d) \(months[max(0, min(11, m - 1))])")
  }
}

/// Numbers as the pages print them: no trailing zeros, tabular.
enum RecordFormat {
  static func num(_ v: Double?, _ digits: Int = 0) -> String {
    guard let v, v.isFinite else { return "—" }
    let f = NumberFormatter()
    f.numberStyle = .decimal
    f.maximumFractionDigits = digits
    f.minimumFractionDigits = 0
    f.usesGroupingSeparator = abs(v) >= 10_000
    return f.string(from: NSNumber(value: v)) ?? "\(v)"
  }

  static func double(_ v: Any?) -> Double? {
    switch v {
    case let n as NSNumber: return n.doubleValue
    case let s as String: return Double(s)
    default: return nil
    }
  }

  static func string(_ v: Any?) -> String? {
    switch v {
    case let s as String: return s
    case let n as NSNumber: return n.stringValue
    default: return nil
    }
  }
}
