import SwiftUI

/// One screen at a time, switched by the session's phase (plan 2.1: states of one page, not a navigation stack).
struct RootView: View {
  @EnvironmentObject var m: WatchModel

  var body: some View {
    NavigationStack {
      screen
        .background(W.bg)
        // the title sits in the status row beside the clock (mockups), never over the progress pills
        .navigationBarTitleDisplayMode(.inline)
    }
  }

  @ViewBuilder private var screen: some View {
    if let s = m.session, m.running {
      switch s.phase {
      case .set: SetScreen(s: s)
      case .logged: LoggedScreen(s: s)
      case .rest(let t): RestScreen(s: s, timer: t)
      case .summary: SummaryScreen(s: s)
      case .ready, .finished: EmptyView()
      }
    } else if case .signedOut = m.syncState {
      SignInScreen()
    } else if let plan = m.todaysPlan {
      StartScreen(plan: plan)
    } else {
      NoPlanScreen()
    }
  }
}

// MARK: - T1 start

struct StartScreen: View {
  @EnvironmentObject var m: WatchModel
  var plan: WatchPlan

  var body: some View {
    VStack(spacing: 0) {
      ZStack {
        SetRing(plan: plan.exercises.map(\.sets), done: plan.exercises.reduce(0) { $0 + $1.done }, current: nil)
          .frame(width: 112 * W.k, height: 112 * W.k)
        VStack(spacing: 0) {
          Text(plan.title).font(W.font(12)).foregroundStyle(W.text2).lineLimit(1).frame(maxWidth: 84 * W.k)
          Text("\(plan.totalSets)").font(W.font(42, .light))
          Text(tr("组", "sets")).font(W.font(11)).foregroundStyle(W.text2)
        }
      }
      .frame(maxHeight: .infinity)
      PrimaryButton(label: tr("开始", "Start"), icon: "play.fill") { m.primary() }
    }
    .toolbar { ToolbarItem(placement: .topBarLeading) { SyncMark(state: m.syncState) { m.retry() } } }
  }
}

// MARK: - T2 / T3 / T4 / T7 set

struct SetScreen: View {
  @EnvironmentObject var m: WatchModel
  var s: SetSession
  @State private var crownAt: Date?

  var body: some View {
    let ex = s.current
    VStack(spacing: 4 * W.k) {
      SetPills(total: ex?.sets ?? 1, done: s.setIndex, current: s.setIndex)
      numbers(ex)
        .frame(maxHeight: .infinity)
      if s.setIndex == 0 && s.plan.exercises.count > 1 { dots }
      HStack {
        HeartRate(bpm: m.lightMode ? nil : s.heartRate)
        Spacer(minLength: 4)
        rightOfHeart
      }
      PrimaryButton(label: tr("完成", "Done"), icon: "checkmark", height: 50) { m.primary() }
    }
    .navigationTitle(ex?.name ?? "")
    .endWorkoutButton { m.end() }
    .crownSteps { n in
      crownAt = Date()
      m.crown(n)
    }
    .gesture(DragGesture(minimumDistance: 24).onEnded { v in
      guard abs(v.translation.width) > abs(v.translation.height) * 1.5 else { return }
      m.swipe(v.translation.width < 0 ? 1 : -1)
    })
  }

  /// The underline shows which number the crown changes, while it is being turned or after a tap on the weight.
  private var showFocus: Bool {
    s.focus == .weight || m.demoShowFocus || (crownAt.map { m.now.timeIntervalSince($0) < 1.5 } ?? false)
  }

  @ViewBuilder private func numbers(_ ex: WatchExercise?) -> some View {
    HStack(alignment: .top, spacing: 6 * W.k) {
      if ex?.weighted == true {
        big(WatchNum.kg(s.weightKg), unit: "kg", focused: showFocus && s.focus == .weight, dim: showFocus && s.focus != .weight)
          .onTapGesture { m.select(.weight) }
        Text("×").font(W.font(20, .light)).foregroundStyle(W.text3).padding(.top, 14 * W.k)
      }
      if s.isTimed {
        big(s.durationS.map { String(Int($0)) } ?? "—", unit: tr("秒", "s"), focused: showFocus, dim: false)
      } else {
        big(s.reps.map(String.init) ?? "—", unit: tr("次", "reps"), focused: showFocus && s.focus == .reps,
            dim: showFocus && s.focus != .reps)
          .onTapGesture { m.select(.reps) }
      }
    }
  }

  private func big(_ value: String, unit: String, focused: Bool, dim: Bool) -> some View {
    VStack(spacing: 1) {
      Text(value).font(W.font(value.count > 3 ? 32 : 44, .light)).foregroundStyle(dim ? W.text2 : W.text)
        .minimumScaleFactor(0.6).lineLimit(1)
      Text(unit).font(W.font(11.5)).foregroundStyle(W.text2)
      Capsule().fill(focused ? W.train : .clear).frame(width: 36 * W.k, height: 2)
    }
    .frame(minWidth: 70 * W.k)
    .contentShape(Rectangle())
  }

  private var dots: some View {
    HStack(spacing: 5) {
      ForEach(s.plan.exercises.indices, id: \.self) { i in
        Circle().fill(i < s.exercise ? W.train : i == s.exercise ? W.text : W.todo)
          .frame(width: (i == s.exercise ? 4.8 : 3.6) * W.k, height: (i == s.exercise ? 4.8 : 3.6) * W.k)
      }
    }
  }

  @ViewBuilder private var rightOfHeart: some View {
    if case .away(let n) = m.syncState {
      Label(tr("待传 \(n)", "\(n) waiting"), systemImage: "iphone.slash").font(W.font(12.5)).foregroundStyle(W.mid)
    } else if case .failed = m.syncState {
      SyncMark(state: m.syncState) { m.retry() }
    } else if let last = s.lastSet() {
      HStack(alignment: .firstTextBaseline, spacing: 3) {
        Text(tr("上次", "Last")).font(W.font(11)).foregroundStyle(W.text2)
        Text(last.weightKg.map { "\(WatchNum.kg($0))×\(last.reps ?? 0)" } ?? "\(last.reps ?? 0)").font(W.font(12.5)).foregroundStyle(W.text2)
      }
    }
  }
}

/// The stop key in the top corner (set and rest screens): ending takes two taps, the key and its confirmation (plan 2.1).
private struct EndWorkoutButton: ViewModifier {
  var end: () -> Void
  @State private var confirm = false

  func body(content: Content) -> some View {
    content
      .toolbar {
        ToolbarItem(placement: .topBarLeading) {
          Button { confirm = true } label: { Image(systemName: "stop.fill") }
            .accessibilityLabel(tr("结束训练", "End workout"))
        }
      }
      .confirmationDialog(tr("结束训练？", "End the workout?"), isPresented: $confirm) {
        Button(tr("结束", "End"), role: .destructive, action: end)
        Button(tr("继续练", "Keep going"), role: .cancel) {}
      }
  }
}

extension View {
  fileprivate func endWorkoutButton(_ end: @escaping () -> Void) -> some View { modifier(EndWorkoutButton(end: end)) }
}

// MARK: - T5 logged

struct LoggedScreen: View {
  var s: SetSession

  var body: some View {
    let l = s.lastLogged
    let ex = s.plan.exercises.first { $0.slug == l?.slug }
    VStack(spacing: 6 * W.k) {
      SetPills(total: ex?.sets ?? 1, done: (l?.setIndex ?? 0) + 1, current: nil)
      ZStack {
        Circle().stroke(W.track, lineWidth: 1.5)
        Circle().stroke(W.train, style: StrokeStyle(lineWidth: 4, lineCap: .round))
        Image(systemName: "checkmark").font(W.font(26, .semibold)).foregroundStyle(W.text)
      }
      .frame(width: 60 * W.k, height: 60 * W.k)
      .frame(maxHeight: .infinity)
      if let l {
        Text(SetText.line(weightKg: l.weightKg, reps: l.reps, durationS: l.durationS)).font(W.font(22))
        if let d = SetText.delta(s.delta(of: l)) {
          HStack(alignment: .firstTextBaseline, spacing: 6) {
            Text(tr("比上次", "vs last")).font(W.font(11.5)).foregroundStyle(W.text2)
            Text(d).font(W.font(15, .semibold))
          }
        }
      }
      Spacer(minLength: 0)
    }
    .navigationTitle(ex?.name ?? "")
  }
}

// MARK: - T6 rest

struct RestScreen: View {
  @EnvironmentObject var m: WatchModel
  var s: SetSession
  var timer: RestTimer
  @State private var amending = false

  var body: some View {
    let l = s.lastLogged
    let done = s.plan.exercises.first { $0.slug == l?.slug }
    VStack(spacing: 4 * W.k) {
      SetPills(total: done?.sets ?? 1, done: (l?.setIndex ?? 0) + 1, current: nil)
      // the ring takes what is left between the pills and the heart-rate row (never pushes the title row)
      ring
        .frame(maxWidth: 112 * W.k, minHeight: 0, maxHeight: .infinity)
        .layoutPriority(-1)
        .onTapGesture { amending = true }
      HStack {
        HeartRate(bpm: m.lightMode ? nil : s.heartRate)
        Spacer(minLength: 4)
        if case .away(let n) = m.syncState {
          Label("\(n)", systemImage: "iphone.slash").font(W.font(12)).foregroundStyle(W.mid)
        }
        if let d = s.restDrop, !m.lightMode {
          HStack(spacing: 2) {
            Image(systemName: "arrow.down").font(W.font(12, .bold)).foregroundStyle(W.text2)
            Text("\(d)").font(W.font(19))
          }
        }
      }
      PrimaryButton(label: tr("跳过", "Skip"), kind: .quiet, height: 42) { m.primary() }
    }
    // the ring is this screen's subject: no title row (the pills say which exercise's sets these are)
    .toolbar(.hidden, for: .navigationBar)
    .crownSteps { m.crown($0) }
    .sheet(isPresented: $amending) {
      if let l { AmendScreen(set: l, weighted: done?.weighted ?? false, step: done?.weightStepKg ?? WatchParams.defaultWeightStepKg) }
    }
  }

  /// Ticks, track, the countdown arc and the clock; everything scales with the space the screen leaves for it.
  private var ring: some View {
    let left = timer.remaining(at: m.now)
    return GeometryReader { g in
      let d = min(g.size.width, g.size.height)
      let inset = d * 0.085
      ZStack {
        Canvas { ctx, size in
          let c = CGPoint(x: size.width / 2, y: size.height / 2)
          let r = min(size.width, size.height) / 2
          for k in 0..<36 {
            let a = Double(k) * 10 * .pi / 180
            let major = k % 9 == 0
            var p = Path()
            p.move(to: CGPoint(x: c.x + (r - 4.5) * sin(a), y: c.y - (r - 4.5) * cos(a)))
            p.addLine(to: CGPoint(x: c.x + (major ? r : r - 2) * sin(a), y: c.y - (major ? r : r - 2) * cos(a)))
            ctx.stroke(p, with: .color(major ? W.tickMajor : W.tick), lineWidth: 1)
          }
        }
        Circle().stroke(W.track, lineWidth: 1.5).padding(inset)
        Circle().trim(from: 0, to: timer.fraction(at: m.now))
          .stroke(W.train, style: StrokeStyle(lineWidth: 4, lineCap: .round))
          .rotationEffect(.degrees(-90))
          .padding(inset)
        VStack(spacing: 0) {
          Text(tr("休息", "Rest")).font(.system(size: d * 0.11, weight: .medium)).foregroundStyle(W.text2)
          Text(RestTimer.clock(left)).font(.system(size: d * 0.30, weight: .light).monospacedDigit())
          Text(nextLine).font(.system(size: d * 0.105, weight: .medium)).foregroundStyle(W.text2).lineLimit(1)
            .minimumScaleFactor(0.7).frame(maxWidth: d * 0.72)
        }
      }
      .frame(width: d, height: d)
      .frame(maxWidth: .infinity, maxHeight: .infinity)
    }
  }

  /// The set after the rest; with the exercise's name when it is another exercise.
  private var nextLine: String {
    let nums = SetText.line(weightKg: s.current?.weighted == true ? s.weightKg : nil, reps: s.isTimed ? nil : s.reps,
                            durationS: s.isTimed ? s.durationS : nil)
    guard let cur = s.current, cur.slug != s.lastLogged?.slug else { return nums }
    return "\(cur.name) \(nums)"
  }
}

/// Tap on the rest ring: correct the set just logged (plan 2.1 「改错」; local, no confirmation).
struct AmendScreen: View {
  @EnvironmentObject var m: WatchModel
  @Environment(\.dismiss) private var dismiss
  var set: LoggedSet
  var weighted: Bool
  var step: Double
  @State private var weight: Double?
  @State private var reps: Int?
  @State private var onWeight = false

  var body: some View {
    VStack(spacing: 6) {
      HStack(alignment: .top, spacing: 6) {
        if weighted {
          cell(WatchNum.kg(weight ?? set.weightKg), "kg", on: onWeight).onTapGesture { onWeight = true }
          Text("×").font(W.font(18, .light)).foregroundStyle(W.text3).padding(.top, 10)
        }
        cell((reps ?? set.reps).map(String.init) ?? "—", tr("次", "reps"), on: !onWeight).onTapGesture { onWeight = false }
      }
      .frame(maxHeight: .infinity)
      PrimaryButton(label: tr("保存", "Save"), icon: "checkmark") {
        m.amend(weightKg: weighted ? (weight ?? set.weightKg) : nil, reps: reps ?? set.reps)
        dismiss()
      }
    }
    .crownSteps { n in
      if onWeight && weighted {
        weight = max(0, (weight ?? set.weightKg ?? 0) + Double(n) * step)
      } else {
        reps = min(WatchParams.maxReps, max(0, (reps ?? set.reps ?? 0) + n))
      }
    }
  }

  private func cell(_ v: String, _ unit: String, on: Bool) -> some View {
    VStack(spacing: 1) {
      Text(v).font(W.font(36, .light))
      Text(unit).font(W.font(11)).foregroundStyle(W.text2)
      Capsule().fill(on ? W.train : .clear).frame(width: 30, height: 2)
    }
    .frame(minWidth: 64)
    .contentShape(Rectangle())
  }
}

// MARK: - T9 / T10 summary

struct SummaryScreen: View {
  @EnvironmentObject var m: WatchModel
  var s: SetSession

  var body: some View {
    let sum = s.summary(at: m.now)
    ScrollView {
      VStack(spacing: 10 * W.k) {
        ZStack {
          SetRing(plan: s.plan.exercises.map(\.sets), done: sum.doneSets, current: nil)
            .frame(width: 96 * W.k, height: 96 * W.k)
          VStack(spacing: 0) {
            HStack(alignment: .firstTextBaseline, spacing: 1) {
              Text("\(sum.doneSets)").font(W.font(36, .light))
              Text("/\(sum.plannedSets)").font(W.font(15)).foregroundStyle(W.text2)
            }
            Text(tr("组", "sets")).font(W.font(11)).foregroundStyle(W.text2)
          }
        }
        HStack {
          stat("\(sum.minutes)", tr("分", "min"), tr("用时", "Time"))
          stat(volume(sum.volumeKg), "kg", tr("总量", "Volume"))
          stat(sum.avgHr.map(String.init) ?? "—", "bpm", tr("均心率", "Avg HR"))
        }
        Image(systemName: "chevron.down").font(W.font(12)).foregroundStyle(W.text3)
        VStack(spacing: 0) {
          row(tr("比上次", "vs last")) {
            HStack(spacing: 6) {
              trend("arrow.up.right", sum.better, W.text)
              trend("equal", sum.same, W.text2)
              trend("arrow.down.right", sum.worse, W.text2)
            }
          }
          row(tr("组间回落", "Drop in rest")) {
            HStack(alignment: .firstTextBaseline, spacing: 2) {
              Image(systemName: "arrow.down").font(W.font(11, .bold)).foregroundStyle(W.text2)
              Text(sum.avgDropBpm.map(String.init) ?? "—").font(W.font(17))
              Text("bpm").font(W.font(10)).foregroundStyle(W.text2)
            }
          }
          row(tr("休息", "Rest")) {
            HStack(alignment: .firstTextBaseline, spacing: 6) {
              if let p = sum.plannedRestS {
                Text(tr("计划 ", "plan ") + RestTimer.clock(Double(p))).font(W.font(10.5)).foregroundStyle(W.text2)
              }
              Text(sum.avgRestS.map { RestTimer.clock(Double($0)) } ?? "—").font(W.font(17))
            }
          }
        }
        PrimaryButton(label: tr("完成", "Done"), icon: "checkmark") { m.primary() }
      }
    }
    .defaultScrollAnchor(m.demoFacts ? .bottom : .top)
    .navigationTitle(s.plan.title)
  }

  private func volume(_ kg: Double) -> String {
    let n = Int(kg.rounded())
    let f = NumberFormatter()
    f.numberStyle = .decimal
    return f.string(from: NSNumber(value: n)) ?? "\(n)"
  }

  private func stat(_ v: String, _ unit: String, _ label: String) -> some View {
    VStack(spacing: 1) {
      HStack(alignment: .firstTextBaseline, spacing: 2) {
        Text(v).font(W.font(17))
        Text(unit).font(W.font(10)).foregroundStyle(W.text2)
      }
      Text(label).font(W.font(10.5)).foregroundStyle(W.text2)
    }
    .frame(maxWidth: .infinity)
  }

  private func trend(_ icon: String, _ n: Int, _ c: Color) -> some View {
    HStack(spacing: 2) {
      Image(systemName: icon).font(W.font(11, .bold)).foregroundStyle(c)
      Text("\(n)").font(W.font(17)).foregroundStyle(c)
    }
  }

  private func row<V: View>(_ label: String, @ViewBuilder _ value: () -> V) -> some View {
    VStack(spacing: 0) {
      HStack {
        Text(label).font(W.font(12.5)).foregroundStyle(W.text2)
        Spacer(minLength: 4)
        value()
      }
      .padding(.vertical, 8 * W.k)
      Rectangle().fill(W.hair).frame(height: 1)
    }
  }
}

// MARK: - S8 / S9

struct SignInScreen: View {
  var body: some View {
    VStack(spacing: 8 * W.k) {
      Image(systemName: "iphone").font(.system(size: 40 * W.k, weight: .light)).foregroundStyle(W.text2)
      Text(tr("在手机上登录", "Sign in on your iPhone")).font(W.font(15, .semibold)).multilineTextAlignment(.center)
      Text("Watch2Whoop").font(W.font(11.5)).foregroundStyle(W.text2)
    }
  }
}

struct NoPlanScreen: View {
  @EnvironmentObject var m: WatchModel

  var body: some View {
    VStack(spacing: 8 * W.k) {
      ZStack {
        Circle().stroke(W.track, style: StrokeStyle(lineWidth: 1.5, dash: [2, 5]))
        Image(systemName: "iphone").font(.system(size: 28 * W.k, weight: .light)).foregroundStyle(W.text2)
      }
      .frame(width: 96 * W.k, height: 96 * W.k)
      Text(tr("计划没同步", "No plan for today")).font(W.font(15, .semibold))
      Text(tr("在手机上打开一次", "Open the app on your iPhone")).font(W.font(11.5)).foregroundStyle(W.text2)
    }
    .onTapGesture { m.requestPlan() }
    .onAppear { m.requestPlan() }
  }
}
