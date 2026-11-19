import SwiftUI
import WidgetKit

// The three watch-face complications (docs/watch-app-plan.md 2.5; mockups C1 / C4 on the plan branch, `compCircle`,
// `compRect` in docs/design/watch/watch.js). They draw a `FaceEntry` and nothing else: every number comes from the
// phone through `FaceTimeline`. Compiled into the widget extension and, for the Debug gallery, into the watch app.
// Sizes follow the space the face gives (a circle is drawn in the mockups' 46 pt box and scaled).

/// Circular: recovery (mockup C1 `compCircle('rec')`): the number on the axis, the arc in the zone colour from 12
/// o'clock; old numbers grey (S3), a missing one 「—」 over the empty track.
struct FaceRecoveryCircle: View {
  var entry: FaceEntry

  var body: some View {
    GeometryReader { g in
      let u = min(g.size.width, g.size.height) / 46
      ZStack {
        AccessoryWidgetBackground()
        Circle().stroke(W.track, lineWidth: 3 * u).padding(4 * u + 1.5 * u)
        if let r = entry.recovery {
          Circle().trim(from: 0, to: max(0.005, min(1, r / 100)))
            .stroke(FaceStyle.recoveryColor(entry), style: StrokeStyle(lineWidth: 3 * u, lineCap: .round))
            .rotationEffect(.degrees(-90))
            .padding(4 * u + 1.5 * u)
            .widgetAccentable()
        }
        CenteredNumber(text: entry.recovery.map { String(Int($0.rounded())) } ?? "—", size: 15.6 * u, weight: .semibold,
                       color: entry.grey || entry.recovery == nil ? W.text2 : W.text)
      }
      .frame(width: g.size.width, height: g.size.height)
    }
    .accessibilityElement(children: .ignore)
    .accessibilityLabel(tr("恢复", "Recovery") + " " + (entry.recovery.map { String(Int($0.rounded())) } ?? "—"))
    .widgetURL(WatchDeepLink.home.url)
  }
}

/// Circular key: 「开始训练」 (mockup C1 `compCircle('dumbbell')`). It draws what it opens, like home's left capsule:
/// the dumbbell on a training day (and during a session), the bowl on a day without training (quick log), the dumbbell
/// dimmed when today's plan has not reached the watch.
struct FaceStartKey: View {
  var entry: FaceEntry

  var body: some View {
    GeometryReader { g in
      let u = min(g.size.width, g.size.height) / 46
      ZStack {
        AccessoryWidgetBackground()
        Image(systemName: FaceStyle.keyIcon(entry.key))
          .font(.system(size: 20 * u, weight: .semibold))
          .foregroundStyle(FaceStyle.keyColor(entry.key))
          .widgetAccentable()
      }
      .frame(width: g.size.width, height: g.size.height)
    }
    .accessibilityElement(children: .ignore)
    .accessibilityLabel(FaceStyle.keyLabel(entry.key))
    .widgetURL(WatchDeepLink.start.url)
  }
}

/// Rectangular (mockup C1 `compRect('today')` / C4 `compRect('live')`): recovery's ring with sleep and strain beside it;
/// while a session runs, the rest countdown (counting by itself) and the exercise with its next set.
struct FaceTodayRect: View {
  var entry: FaceEntry

  var body: some View {
    GeometryReader { g in
      let u = min(g.size.height / 54, g.size.width / 172)
      HStack(spacing: 8 * u) {
        if let live = entry.live {
          liveRing(live, u: u)
          liveText(live, u: u)
        } else {
          recoveryRing(u: u)
          readings(u: u)
        }
      }
      .frame(width: g.size.width, height: g.size.height, alignment: .leading)
    }
    .accessibilityElement(children: .combine)
    .widgetURL(WatchDeepLink.home.url)
  }

  // MARK: idle: recovery, sleep, strain

  private func recoveryRing(u: CGFloat) -> some View {
    let d = 46 * u
    return ZStack {
      Circle().stroke(W.track, lineWidth: 3 * u)
      if let r = entry.recovery {
        Circle().trim(from: 0, to: max(0.005, min(1, r / 100)))
          .stroke(FaceStyle.recoveryColor(entry), style: StrokeStyle(lineWidth: 3 * u, lineCap: .round))
          .rotationEffect(.degrees(-90))
          .widgetAccentable()
      }
      CenteredNumber(text: entry.recovery.map { String(Int($0.rounded())) } ?? "—", size: 18 * u, weight: .semibold,
                     color: entry.grey || entry.recovery == nil ? W.text2 : W.text)
    }
    .frame(width: d - 3 * u, height: d - 3 * u)
    .frame(width: d, height: d)
    .accessibilityLabel(tr("恢复", "Recovery") + " " + (entry.recovery.map { String(Int($0.rounded())) } ?? "—"))
  }

  private func readings(u: CGFloat) -> some View {
    VStack(alignment: .leading, spacing: 8 * u) {
      reading(tr("睡眠", "Sleep"), entry.sleep, max: WatchParams.sleepMax, digits: 0, color: W.sleep, u: u)
      reading(tr("负荷", "Strain"), entry.strain, max: WatchParams.strainMax, digits: 1, color: W.train, u: u)
    }
    .frame(maxWidth: .infinity)
  }

  private func reading(_ label: String, _ v: Double?, max: Double, digits: Int, color: Color, u: CGFloat) -> some View {
    HStack(spacing: 6 * u) {
      Text(label).font(.system(size: 10.5 * u, weight: .medium)).foregroundStyle(W.text2).lineLimit(1).fixedSize()
      GeometryReader { b in
        ZStack(alignment: .leading) {
          Capsule().fill(W.track).frame(height: 3 * u)
          if let v {
            Capsule().fill(entry.grey ? W.text2 : color)
              .frame(width: b.size.width * CGFloat(Swift.max(0.02, Swift.min(1, v / max))), height: 4 * u)
              .widgetAccentable()
          }
        }
        .frame(maxHeight: .infinity)
      }
      .frame(height: 4 * u)
      Text(v.map { String(format: "%.\(digits)f", $0) } ?? "—")
        .font(.system(size: 15 * u, weight: .semibold).monospacedDigit())
        .foregroundStyle(entry.grey || v == nil ? W.text2 : W.text).lineLimit(1).fixedSize()
    }
    .accessibilityElement(children: .ignore)
    .accessibilityLabel(label + " " + (v.map { String(format: "%.\(digits)f", $0) } ?? "—"))
  }

  // MARK: live: rest countdown, exercise, next set

  @ViewBuilder private func liveRing(_ l: FaceLive, u: CGFloat) -> some View {
    let d = 46 * u
    ZStack {
      if let rest = l.rest {
        // the system draws and runs both: no entry per second, no app wake-up
        ProgressView(timerInterval: rest, countsDown: true, label: { EmptyView() }, currentValueLabel: { EmptyView() })
          .progressViewStyle(.circular)
          .tint(W.train)
          .widgetAccentable()
        Text(timerInterval: rest, countsDown: true)
          .font(.system(size: 13 * u, weight: .semibold).monospacedDigit())
          .foregroundStyle(W.text)
          .multilineTextAlignment(.center)
          .frame(width: d - 10 * u)
          .minimumScaleFactor(0.7)
      } else {
        Circle().stroke(W.track, lineWidth: 3 * u).padding(1.5 * u)
        Image(systemName: "dumbbell").font(.system(size: 16 * u, weight: .semibold)).foregroundStyle(W.train)
          .widgetAccentable()
      }
    }
    .frame(width: d, height: d)
  }

  private func liveText(_ l: FaceLive, u: CGFloat) -> some View {
    VStack(alignment: .leading, spacing: 3 * u) {
      HStack(alignment: .firstTextBaseline) {
        Text(l.exercise).font(.system(size: 15 * u, weight: .semibold)).foregroundStyle(W.text).lineLimit(1)
          .minimumScaleFactor(0.7)
        Spacer(minLength: 4 * u)
        Text("\(l.done)/\(l.sets)").font(.system(size: 14 * u, weight: .medium).monospacedDigit()).foregroundStyle(W.text2)
          .fixedSize()
      }
      FaceSetPills(done: l.done, total: l.sets, u: u)
      HStack(spacing: 3 * u) {
        Text(l.rest == nil ? tr("这一组", "This set") : tr("下一组", "Next"))
          .font(.system(size: 10 * u, weight: .medium)).foregroundStyle(W.text2).fixedSize()
        Text(SetText.line(weightKg: l.weightKg, reps: l.reps, durationS: l.durationS))
          .font(.system(size: 11 * u, weight: .medium).monospacedDigit()).foregroundStyle(W.text).lineLimit(1)
          .minimumScaleFactor(0.7)
      }
    }
    .frame(maxWidth: .infinity, alignment: .leading)
  }
}

/// The exercise's sets as pills (mockup `segs`: done = train, to do = the todo ink).
struct FaceSetPills: View {
  var done: Int
  var total: Int
  var u: CGFloat

  var body: some View {
    HStack(spacing: 3 * u) {
      ForEach(0..<max(1, total), id: \.self) { i in
        Capsule().fill(i < done ? W.train : W.todo).frame(height: 3 * u)
          .widgetAccentable(i < done)
      }
    }
    .frame(maxWidth: .infinity)
    .accessibilityHidden(true)
  }
}

/// The few drawing decisions the three share.
enum FaceStyle {
  /// The zone colour of recovery; grey when the phone's numbers are old (S3: the same as the home dial's grey arc).
  static func recoveryColor(_ e: FaceEntry) -> Color { e.grey ? W.text2.opacity(0.6) : W.tone(e.tone) }

  static func keyIcon(_ k: FaceKey) -> String {
    switch k {
    case .train, .session, .unavailable: return "dumbbell"
    case .meal: return "fork.knife"
    }
  }

  static func keyColor(_ k: FaceKey) -> Color {
    switch k {
    case .train, .session: return W.train
    case .meal: return W.text
    case .unavailable: return W.text3
    }
  }

  static func keyLabel(_ k: FaceKey) -> String {
    switch k {
    case .train, .unavailable: return tr("开始训练", "Start workout")
    case .session: return tr("当前训练", "Current workout")
    case .meal: return tr("记一餐", "Log a meal")
    }
  }
}
