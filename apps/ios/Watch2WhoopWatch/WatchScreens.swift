import SwiftUI

/// Home is the root whenever no session runs (plan 2.4: 「一个首屏 + 当前场景」); a running session takes the whole app
/// (plan 2.1: states of one page, not a navigation stack) until its summary's 「完成」 brings home back.
struct RootView: View {
  @EnvironmentObject var m: WatchModel

  var body: some View {
    if let s = m.session, m.running {
      NavigationStack {
        session(s)
          .background(W.bg)
          // the title sits in the status row beside the clock (mockups), never over the progress pills
          .navigationBarTitleDisplayMode(.inline)
      }
    } else if case .signedOut = m.syncState {
      SignInScreen()
    } else {
      NavigationStack(path: $m.path) {
        HomeScreen()
          .background(W.bg)
          .navigationDestination(for: WatchRoute.self) { r in
            route(r).background(W.bg).navigationBarTitleDisplayMode(.inline)
          }
      }
    }
  }

  @ViewBuilder private func session(_ s: SetSession) -> some View {
    switch s.phase {
    case .set: SetScreen(s: s)
    case .logged: LoggedScreen(s: s)
    case .rest(let t): RestScreen(s: s, timer: t)
    case .summary: SummaryScreen(s: s)
    case .ready, .finished: EmptyView()
    }
  }

  @ViewBuilder private func route(_ r: WatchRoute) -> some View {
    switch r {
    case .start:
      if let plan = m.todaysPlan { StartScreen(plan: plan) } else { GoneScreen() }
    case .quick: QuickScreen()
    case .receipt(let id):
      if let l = m.quickLogs.log(id) { ReceiptScreen(log: l) } else { GoneScreen() }
    case .undo(let id):
      if let l = m.quickLogs.log(id) { UndoScreen(log: l) } else { GoneScreen() }
    }
  }
}

// MARK: - D1 / D2 / S1–S7 home (今天一眼)

struct HomeScreen: View {
  @EnvironmentObject var m: WatchModel

  var body: some View {
    let h = m.home
    VStack(spacing: 0) {
      RecoveryDial(home: h)
        .frame(width: 118 * W.k, height: 118 * W.k)
        .frame(maxHeight: .infinity)
      HStack(spacing: 16 * W.k) {
        HomeReading(label: tr("睡眠", "Sleep"), value: h.sleep, digits: 0, max: WatchParams.sleepMax, color: W.sleep, grey: h.grey)
        HomeReading(label: tr("负荷", "Strain"), value: h.strain, digits: 1, max: WatchParams.strainMax, color: W.train, grey: h.grey)
      }
      .padding(.horizontal, 12 * W.k)
      .padding(.bottom, 10 * W.k)
      HStack(spacing: 6 * W.k) {
        leftKey(h.key)
        core
      }
      .padding(.horizontal, 8 * W.k)
    }
    .toolbar { ToolbarItem(placement: .topBarLeading) { SyncMark(state: m.syncState) { mark() } } }
  }

  /// Left capsule: today's plan (→ T1), 「记一餐」 on a day without training (→ M5), 「计划没同步」 (→ ask the phone).
  @ViewBuilder private func leftKey(_ k: WatchHome.Key) -> some View {
    let (icon, tint, label, play): (String, Color, String, Bool) = {
      switch k {
      case .train(let t): return ("dumbbell", W.train, t, true)
      case .meal: return ("fork.knife", W.text2, tr("记一餐", "Log a meal"), false)
      case .planMissing:
        return (m.shows(m.askedPlanAt, for: 3) ? "arrow.triangle.2.circlepath" : "iphone", W.text2, tr("计划没同步", "No plan yet"), false)
      }
    }()
    Button { m.homeKey() } label: {
      HStack(spacing: 6 * W.k) {
        Image(systemName: icon).font(W.font(13, .semibold)).foregroundStyle(tint)
        Text(label).font(W.font(14, .semibold)).foregroundStyle(k == .planMissing ? W.text2 : W.text)
          .lineLimit(1).minimumScaleFactor(0.7)
        Spacer(minLength: 0)
        if play { Image(systemName: "play").font(W.font(10, .bold)).foregroundStyle(W.text2) }
      }
      .padding(.horizontal, 12 * W.k)
      .frame(maxWidth: .infinity, minHeight: 36 * W.k)
      .background(Capsule().fill(W.surface))
    }
    .buttonStyle(.plain)
    // Double Tap = the left key (mockups D1 / D2), only while home is on top (a pushed screen has its own primary)
    .handGestureShortcut(.primaryAction, isEnabled: m.path.isEmpty)
  }

  /// The Jarvis core: tap = quick log; hold = talk (`WatchModel.coreHold`), dimmed while voice is unavailable (mockup S7).
  private var core: some View {
    let refused = m.shows(m.voiceRefusedAt, for: 2)
    return ZStack(alignment: .topTrailing) {
      JarvisCoreMark(size: 40 * W.k, dim: !m.home.voice.isAvailable)
      if refused {
        Image(systemName: "mic.slash").font(.system(size: 9 * W.k, weight: .bold)).foregroundStyle(W.mid)
          .padding(3 * W.k).background(Circle().fill(Color.black)).offset(x: 4 * W.k, y: -4 * W.k)
      }
    }
    .contentShape(Circle())
    .onTapGesture { m.coreTap() }
    .onLongPressGesture(minimumDuration: 0.5) { m.coreHold() }
    .accessibilityElement()
    .accessibilityLabel(tr("快记", "Quick log"))
    .accessibilityAddTraits(.isButton)
    .accessibilityAction { m.coreTap() }
  }

  private func mark() {
    if case .failed = m.syncState { m.retry() } else { m.askPhone() }
  }
}

// MARK: - T1 start (from home's left capsule; back = home)

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
      PrimaryButton(label: tr("开始", "Start"), icon: "play.fill") { m.startSession() }
    }
    .ignoresSafeArea(edges: .bottom)   // the primary button in the bottom corner radius (mockup T1)
  }
}

// MARK: - M5 quick log

struct QuickScreen: View {
  @EnvironmentObject var m: WatchModel

  var body: some View {
    let q = m.quick
    let tiles = tiles(q)
    Group {
      if tiles.isEmpty {
        // nothing from the phone yet: one icon + the one hint the plan allows (「在手机上打开一次」)
        VStack(spacing: 8 * W.k) {
          Image(systemName: "iphone").font(.system(size: 30 * W.k, weight: .light)).foregroundStyle(W.text2)
          Text(tr("在手机上打开一次", "Open the app on your iPhone")).font(W.font(12.5)).foregroundStyle(W.text2)
            .multilineTextAlignment(.center)
        }
        .onAppear { m.requestPlan() }
      } else {
        // four tiles fill the screen under the back key, no scrolling (mockup M5)
        GeometryReader { g in
          let gap = 4 * W.k
          let rows = (tiles.count + 1) / 2
          let h = min(110 * W.k, (g.size.height - W.pushedTop - gap * CGFloat(rows - 1)) / CGFloat(max(rows, 1)))
          VStack(spacing: gap) {
            ForEach(0..<rows, id: \.self) { r in
              HStack(spacing: gap) {
                ForEach(tiles[(r * 2)..<min(tiles.count, r * 2 + 2)], id: \.id) { t in
                  QuickTile(icon: t.icon, name: t.name, sub: t.sub, action: t.action).frame(height: h)
                }
                if r * 2 + 1 >= tiles.count { Color.clear.frame(height: h) }
              }
            }
          }
          .padding(.top, W.pushedTop)
          .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .top)
        }
        .ignoresSafeArea(edges: .bottom)
      }
    }
    .navigationTitle(tr("快记", "Quick log"))
  }

  private struct Tile {
    var id: String
    var icon: String
    var name: String
    var sub: String
    var action: () -> Void
  }

  private func tiles(_ q: WatchQuick?) -> [Tile] {
    guard let q else { return [] }
    var out = q.meals.map { meal in
      Tile(id: "m\(meal.id)", icon: "fork.knife", name: meal.name, sub: meal.kcal.map { "\(WatchFmt.int($0)) kcal" } ?? "—") {
        m.logMeal(meal)
      }
    }
    if let ml = q.waterMl {
      out.append(Tile(id: "water", icon: "drop", name: tr("水", "Water"), sub: "+\(ml) ml") { m.logWater(ml) })
    }
    return out
  }
}

// MARK: - M3 receipt

struct ReceiptScreen: View {
  @EnvironmentObject var m: WatchModel
  var log: WatchQuickLog

  var body: some View {
    VStack(alignment: .leading, spacing: 0) {
      Capsule().fill(WJarvis.ice).frame(width: 44 * W.k, height: 2).padding(.top, W.pushedTop - 6 * W.k)
      HStack(spacing: 4 * W.k) {
        Image(systemName: log.undone ? "arrow.uturn.backward.circle" : "checkmark.circle").font(W.font(12, .semibold))
          .foregroundStyle(log.undone ? W.text2 : W.good)
        Text(header).font(W.font(12.5)).foregroundStyle(W.text2).lineLimit(1)
      }
      .padding(.top, 6 * W.k)
      Text(log.name).font(W.font(18, .semibold)).lineLimit(1).fixedSize(horizontal: false, vertical: true).padding(.top, 4 * W.k)
      HStack(alignment: .lastTextBaseline, spacing: 4 * W.k) {
        Text(big).font(W.font(42, .light)).tracking(-1)
        Text(log.kind == .meal ? "kcal" : "ml").font(W.font(11.5)).foregroundStyle(W.text2)
        Spacer(minLength: 2)
        if let p = log.proteinG {
          Text(tr("蛋白", "Protein")).font(W.font(11)).foregroundStyle(W.text2)
          Text(WatchFmt.int(p)).font(W.font(15, .semibold))
          Text("g").font(W.font(10.5)).foregroundStyle(W.text2)
        }
      }
      if let b = log.bar(logs: m.quickLogs.items, quick: m.context?.quick) {
        DayBar(before: b.before, added: b.added, goal: b.goal).padding(.top, 6 * W.k)
        HStack(spacing: 0) {
          Text(tr("今天 ", "Today ")).font(W.font(10.5)).foregroundStyle(W.text2)
          Text(WatchFmt.int(b.before + b.added)).font(W.font(11.5))
          Text(" / \(WatchFmt.int(b.goal)) kcal").font(W.font(10.5)).foregroundStyle(W.text2)
        }
        .padding(.top, 6 * W.k)
      }
      Spacer(minLength: 4)
      if !log.undone {
        // undo takes the confirm screen (M4); no Double Tap on it (mockup M3)
        PrimaryButton(label: tr("撤销", "Undo"), icon: "arrow.uturn.backward", kind: .quiet, height: 42, doubleTap: false) {
          m.path.append(.undo(log.id))
        }
      }
    }
    .padding(.horizontal, 4 * W.k)
    .ignoresSafeArea(edges: .bottom)
  }

  private var header: String {
    if log.undone { return tr("已撤销", "Undone") }
    if log.kind == .water { return tr("已记喝水", "Water logged") }
    let slot = log.slot ?? ""
    return tr("已记到\(slot)", "Logged · \(slot)")
  }

  private var big: String {
    log.kind == .water ? (log.ml.map(String.init) ?? "—") : (log.kcal.map { WatchFmt.int($0) } ?? "—")
  }
}

/// The receipt's day bar: the day before this meal (ink 55 %), this meal (ice), on the goal's track.
struct DayBar: View {
  var before: Double
  var added: Double
  var goal: Double

  var body: some View {
    GeometryReader { g in
      let w = g.size.width
      let a = w * CGFloat(min(1, before / goal))
      let b = min(w - a, w * CGFloat(added / goal))
      ZStack(alignment: .leading) {
        Capsule().fill(W.track)
        Capsule().fill(W.text.opacity(0.55)).frame(width: a)
        Capsule().fill(WJarvis.ice).frame(width: max(0, b - 2)).offset(x: a + 2)
      }
    }
    .frame(height: 4)
  }
}

// MARK: - M4 undo asks first

struct UndoScreen: View {
  @EnvironmentObject var m: WatchModel
  @Environment(\.dismiss) private var dismiss
  var log: WatchQuickLog

  var body: some View {
    VStack(spacing: 8 * W.k) {
      Text(log.kind == .meal ? tr("撤销这一餐？", "Undo this meal?") : tr("撤销这杯水？", "Undo this drink?"))
        .font(W.font(16, .semibold)).multilineTextAlignment(.center)
      Text(detail).font(W.font(12)).foregroundStyle(W.text2).lineLimit(1).fixedSize(horizontal: false, vertical: true)
      Spacer(minLength: 2)
      Button { m.undo(log.id) } label: {
        Text(tr("撤销", "Undo")).font(W.font(17, .semibold)).foregroundStyle(W.bad)
          .frame(maxWidth: .infinity, minHeight: 44 * W.k)
          .background(Capsule().fill(W.bad.opacity(0.16)))
      }
      .buttonStyle(.plain)
      PrimaryButton(label: tr("保留", "Keep"), kind: .quiet, height: 44) { dismiss() }   // Double Tap = 保留 (M4)
    }
    .toolbar(.hidden, for: .navigationBar)
  }

  private var detail: String {
    log.kind == .meal ? "\(log.name) \(log.kcal.map { WatchFmt.int($0) } ?? "—") kcal" : "\(log.name) \(log.ml ?? 0) ml"
  }
}

/// A route whose subject is gone (today's plan changed, a log pruned): one line, the back key leads home.
struct GoneScreen: View {
  var body: some View {
    Image(systemName: "iphone").font(.system(size: 30 * W.k, weight: .light)).foregroundStyle(W.text2)
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

// MARK: - S8

struct SignInScreen: View {
  var body: some View {
    VStack(spacing: 8 * W.k) {
      Image(systemName: "iphone").font(.system(size: 40 * W.k, weight: .light)).foregroundStyle(W.text2)
      Text(tr("在手机上登录", "Sign in on your iPhone")).font(W.font(15, .semibold)).multilineTextAlignment(.center)
      Text("Watch2Whoop").font(W.font(11.5)).foregroundStyle(W.text2)
    }
  }
}
