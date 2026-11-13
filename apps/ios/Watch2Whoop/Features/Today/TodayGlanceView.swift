import SwiftUI

/// What a tap on the Today page opens (2.0.1): the three L1 sheets of the hero and its readings.
enum TodayL1: String, Identifiable {
  case recovery, strain, sleep
  var id: String { rawValue }

  /// Height of the half sheet (design: recovery 444, strain / sleep a little taller for their fourth row).
  var height: CGFloat { self == .recovery ? 444 : 500 }
}

/// What the page asks of `TodayView` (the page's state stays there).
struct TodayGlanceActions {
  var l1: (TodayL1) -> Void
  var sheet: (TodaySheet) -> Void
  var open: (TodayOpen) -> Void
  var calendar: () -> Void
  var backToToday: () -> Void
  var syncTap: () -> Void
  var syncCenter: () -> Void
  var recoveryNext: (RecoveryNext.Action) -> Void
  var food: () -> Void
  var circles: () -> Void
  var training: () -> Void
}

/// The first glance of Today (docs/design/2.0.1 `01-today`): date + sync capsule, the recovery dial, two readings (sleep,
/// strain), one row of three faces (体征 or 关注, 饮食, 圈子). Below the fold: the sync nudges, a second face row (训练,
/// 今晚, 提醒), each only when it has something. No section titles, no sentences.
struct TodayGlancePage: View, Equatable {
  static func == (a: TodayGlancePage, b: TodayGlancePage) -> Bool {
    a.model == b.model && a.isPast == b.isPast && a.circlesOn == b.circlesOn && a.day == b.day
  }

  let model: TodayModel
  let isPast: Bool
  let circlesOn: Bool
  /// The day shown (nil = the user's today), for the food face.
  let day: String?
  let session: AccountSession
  let run: TodayGlanceActions
  @Environment(\.askAI) private var askAI

  private var g: TodayGlance { model.glance }

  var body: some View {
    VStack(spacing: 0) {
      header
      hero.padding(.top, 32)
      readings.padding(.top, 40)
      faces.padding(.top, 44)
    }
  }

  // MARK: header: date (tap = month calendar) + sync capsule (tap = sync now / retry, hold = sync center)

  private var header: some View {
    HStack(spacing: 8) {
      Button(action: run.calendar) {
        HStack(spacing: 4) {
          Text(dateTitle).font(Typo.header).foregroundColor(Palette.text)
          Icon(.chevronDown, size: 13).foregroundColor(Palette.text2)
        }
        .frame(minHeight: 44)
        .contentShape(Rectangle())
      }
      .buttonStyle(PressScale())
      .accessibilityLabel(tr("选择日期", "Pick a day"))
      if isPast {
        Button(action: run.backToToday) { Chip(label: tr("回到今天", "Today")) }.buttonStyle(PressScale())
      }
      Spacer(minLength: 0)
      if !isPast { TodaySyncCapsule(card: model.sync, tap: run.syncTap, center: run.syncCenter) }
    }
    .frame(height: 44)
  }

  private var dateTitle: String { Self.dateTitle(model.date) }

  /// 「11月10日」 / "Nov 10".
  static func dateTitle(_ iso: String?) -> String {
    guard let iso, iso.count >= 10 else { return tr("今天", "Today") }
    let p = iso.prefix(10).split(separator: "-").compactMap { Int($0) }
    guard p.count == 3 else { return tr("今天", "Today") }
    return tr("\(p[1])月\(p[2])日", "\(TodayModel.monthName(p[1])) \(p[2])")
  }

  // MARK: hero: the recovery dial

  static var dialSide: CGFloat { min(280, max(200, UIScreen.main.bounds.height * 0.32)) }

  private var hero: some View {
    let d = g.recovery, side = Self.dialSide, k = side / 280
    return Button {
      if d.value == nil, let n = model.recoveryNext { run.recoveryNext(n.action) } else { run.l1(.recovery) }
    } label: {
      TickDial(fraction: TodayGlanceRules.dialFraction(d.value), tone: d.tone,
               usual: d.usual.map { ($0.lowerBound / 100)...($0.upperBound / 100) },
               zones: TodayGlanceRules.recoveryZones.map { $0 / 100 }, side: side) {
        VStack(spacing: 0) {
          Text(tr("恢复", "Recovery")).font(.system(size: 13, weight: .medium)).tracking(2).foregroundColor(Palette.text2)
          if let v = d.value {
            Text(TodayModel.fmt(v)).font(Typo.hero(100 * k)).tracking(-3 * k).foregroundColor(Palette.text)
              .lineLimit(1).minimumScaleFactor(0.6)
              .overlay(alignment: .topTrailing) {
                Text("%").font(.system(size: 20 * k, weight: .light)).foregroundColor(Palette.text2).offset(x: 18 * k, y: 12 * k)
              }
              .padding(.top, -4 * k)
          } else {
            Rectangle().fill(Palette.text2).frame(width: 40 * k, height: 2).padding(.vertical, 40 * k)
          }
          if !d.week.isEmpty, d.value != nil { MiniBars(values: d.week, tone: d.tone).padding(.top, 2) }
          if let w = d.verdict ?? d.reason {
            Text(w).font(Typo.verdict).foregroundColor(d.value == nil ? Palette.text2 : Palette.text).lineLimit(1)
              .minimumScaleFactor(0.8).padding(.top, 10)
          }
        }
        .frame(width: side * 0.66)
      }
      .equatable()
    }
    .buttonStyle(PressScale())
    .frame(maxWidth: .infinity)
    .contextMenu {
      Button { askAI(Self.recoveryPrompt(model)) } label: { Text(tr("问 Jarvis：恢复", "Ask Jarvis: recovery")) }
      Button { run.l1(.recovery) } label: { Text(tr("看详情", "Details")) }
    }
    .accessibilityElement(children: .ignore)
    .accessibilityLabel(dialLabel)
    .accessibilityAddTraits(.isButton)
  }

  private var dialLabel: String {
    let d = g.recovery
    guard let v = d.value else { return "\(tr("恢复", "Recovery")) — \(d.reason ?? "")" }
    var s = "\(tr("恢复", "Recovery")) \(TodayModel.fmt(v))%"
    if let u = d.usual {
      if v > u.upperBound { s += tr("，高于常态", ", above your usual") }
      else if v < u.lowerBound { s += tr("，低于常态", ", below your usual") }
      else { s += tr("，在常态内", ", within your usual") }
    }
    if let w = d.verdict { s += "，\(w)" }
    return s
  }

  static func recoveryPrompt(_ m: TodayModel) -> String {
    guard let v = m.glance.recovery.value else { return tr("我今天为什么没有恢复分？", "Why do I have no recovery score today?") }
    return tr("我今天恢复 \(TodayModel.fmt(v))%，帮我解读一下", "My recovery is \(TodayModel.fmt(v))% today. What does it mean for me?")
  }

  // MARK: readings: sleep score and strain so far

  private var readings: some View {
    HStack(alignment: .top, spacing: 20) {
      reading(g.sleep, label: tr("睡眠", "Sleep"), l1: .sleep)
      reading(g.strain, label: tr("负荷", "Strain"), l1: .strain)
    }
  }

  private func reading(_ x: TodayGlance.Gauge, label: String, l1: TodayL1) -> some View {
    Button { run.l1(l1) } label: {
      VStack(alignment: .leading, spacing: 8) {
        HStack(alignment: .firstTextBaseline, spacing: 6) {
          Text(x.text ?? "—").font(Typo.reading).foregroundColor(x.text == nil ? Palette.text2 : Palette.text)
          Text(label).font(Typo.label).foregroundColor(Palette.text2)
        }
        BulletGauge(value: x.value, max: x.max, usual: x.usual, target: x.target, step: x.step, majors: x.majors, tone: x.tone)
      }
      .frame(maxWidth: .infinity, minHeight: 59, alignment: .topLeading)
      .contentShape(Rectangle())
    }
    .buttonStyle(PressScale())
    .accessibilityElement(children: .ignore)
    .accessibilityLabel("\(label) \(x.text ?? "—")")
    .accessibilityAddTraits(.isButton)
  }

  // MARK: faces

  private var faces: some View {
    HStack(spacing: 8) {
      slotOne
      TodayFoodFace(session: session, day: day, open: run.food)
      if circlesOn && !isPast { TodayCirclesFace(session: session, open: run.circles) }
    }
  }

  /// Slot 1: the attention face when something is off on the day, else 体征 (the health monitor), else nothing.
  @ViewBuilder private var slotOne: some View {
    if let a = model.attention.first {
      Button { run.open(a.open) } label: {
        Face(icon: .circleAlert, label: a.label, number: a.value, unit: a.unit, dot: a.tone) {
          if !g.attentionWeek.isEmpty { Lollipops(values: g.attentionWeek, tone: a.tone) } else { Color.clear }
        }
      }
      .buttonStyle(PressScale())
    } else if !model.vitals.isEmpty {
      let c = TodayGlanceRules.vitalsCount(model.vitals)
      Button { run.sheet(.monitor) } label: {
        Face(icon: .heartPulse, label: tr("体征", "Vitals"), number: "\(c.ok)/\(c.total)") { VitalPills(vitals: model.vitals) }
      }
      .buttonStyle(PressScale())
    }
  }
}

/// Below the fold of Today: the sync nudges, then 训练 · 今晚 · 提醒 as faces, each only when it has something.
struct TodayMoreFaces: View, Equatable {
  static func == (a: TodayMoreFaces, b: TodayMoreFaces) -> Bool { a.model == b.model }
  let model: TodayModel
  let run: TodayGlanceActions

  var body: some View {
    let newReminders = model.reminders.filter { $0.status == "new" }.count
    if model.training != nil || model.tonight != nil || !model.reminders.isEmpty {
      HStack(spacing: 8) {
        if let t = model.training {
          Button(action: run.training) {
            // logged exercises, else the sessions planned for the day, else a rest day
            let planned = t.logged.isEmpty && !t.planned.isEmpty
            Face(icon: .dumbbell, label: tr("训练", "Training"),
                 number: t.restDay ? tr("休息", "Rest") : "\(planned ? t.planned.count : t.logged.count)",
                 unit: t.restDay ? nil : (planned ? tr("项计划", "planned") : tr("个动作", "moves"))) { Color.clear }
          }
          .buttonStyle(PressScale())
        }
        if let c = model.tonight, let b = c.bedtime {
          Button { run.sheet(.detail(.sleepCoach)) } label: {
            Face(icon: .moon, label: tr("今晚", "Tonight"), number: b) {
              if let need = c.need { Text(TodayModel.hm(need)).font(Typo.unit).foregroundColor(Palette.text2) } else { Color.clear }
            }
          }
          .buttonStyle(PressScale())
        }
        if !model.reminders.isEmpty {
          Button { run.sheet(.reminders) } label: {
            Face(icon: .bell, label: tr("提醒", "Reminders"), number: "\(newReminders > 0 ? newReminders : model.reminders.count)",
                 dot: model.reminders.first { $0.status == "new" }?.tone == .bad ? .bad : (newReminders > 0 ? .mid : nil)) { Color.clear }
          }
          .buttonStyle(PressScale())
        }
      }
      .id("more")
    }
  }
}

// MARK: - face graphics

/// The 体征 face: one pill per vital (its range), the dot where today sits; out of range in `mid`.
struct VitalPills: View {
  let vitals: [TodayModel.Vital]

  var body: some View {
    HStack(spacing: 0) {
      ForEach(Array(vitals.prefix(5).enumerated()), id: \.offset) { i, v in
        if i > 0 { Spacer(minLength: 2) }
        ZStack(alignment: .bottom) {
          Capsule().fill(Palette.band).frame(width: 6.5, height: 24)
          if let p = TodayGlanceRules.vitalPosition(v) {
            Circle().fill(v.off ? Palette.mid : Palette.text).frame(width: 5, height: 5)
              .offset(y: -CGFloat(min(1, max(0, p))) * 19 - 1)
          }
        }
        .frame(height: 24)
        .opacity(v.value == nil ? 0.4 : 1)
      }
    }
    .accessibilityHidden(true)
  }
}

/// The attention face's last days: history in `past`, the latest in the attention colour.
struct Lollipops: View {
  let values: [Double?]
  let tone: Tone

  var body: some View {
    GeometryReader { g in
      let xs = values.compactMap { $0 }
      let lo = xs.min() ?? 0, hi = xs.max() ?? 1, span = hi - lo == 0 ? 1 : hi - lo
      let n = max(1, values.count - 1)
      ZStack(alignment: .bottomLeading) {
        Rectangle().fill(Palette.hairStrong).frame(height: 1)
        ForEach(Array(values.enumerated()), id: \.offset) { i, v in
          if let v {
            let last = i == values.count - 1, h = 6 + CGFloat((v - lo) / span) * (g.size.height - 10)
            VStack(spacing: 0) {
              Circle().fill(last ? tone.color : Palette.past).frame(width: 5, height: 5)
              Rectangle().fill(last ? tone.color : Palette.past).frame(width: 1.5, height: h - 5)
            }
            .offset(x: CGFloat(i) / CGFloat(n) * (g.size.width - 5))
          }
        }
      }
    }
    .accessibilityHidden(true)
  }
}

/// The 饮食 face: eaten today against the goal (ink gauge, target tick at the goal). No share or no log: no face.
struct TodayFoodFace: View {
  let day: String?
  let open: () -> Void
  @StateObject private var store: FoodStore

  init(session: AccountSession, day: String?, open: @escaping () -> Void) {
    self.day = day
    self.open = open
    _store = StateObject(wrappedValue: FoodStore(session: session, cardOnly: true))
  }

  var body: some View {
    Group {
      if let n = store.nutrition, n.error == nil {
        let kcal = n.intake.kcal ?? 0, goal = n.goal.kcal ?? 0
        Button(action: open) {
          Face(icon: .soup, label: tr("饮食", "Food"), number: RecordFormat.num(n.intake.kcal), unit: "kcal") {
            // no goal: a fixed 2,500 kcal scale (the design's), never a bar that is always full
            let top = goal > 0 ? max(goal * 1.2, kcal) : max(2500, kcal * 1.2)
            BulletGauge(value: kcal, max: top, target: goal > 0 ? goal...goal : nil, step: top / 5, tone: .text)
          }
        }
        .buttonStyle(PressScale())
      }
    }
    .task(id: day) {
      store.select(day ?? store.today)
      await store.refresh()
    }
    .onReceive(NotificationCenter.default.publisher(for: JarvisRouter.wroteNotification)) { _ in
      Task { await store.refresh() }
    }
  }
}

/// The 圈子 face: how many of my people trained today and their avatars (ring = trained); no circle yet = invite.
struct TodayCirclesFace: View {
  let open: () -> Void
  @ObservedObject private var store: CirclesStore

  init(session: AccountSession, open: @escaping () -> Void) {
    self.open = open
    store = CirclesStore.shared(for: session)
  }

  var body: some View {
    let others = store.overview?.members ?? []
    let trained = others.filter { $0.trained == true }.count
    Button(action: open) {
      if store.hasCircles || store.list == nil {
        Face(icon: .users, label: tr("圈子", "Circles"), number: "\(trained)", unit: tr("人练了", "trained"),
             dot: store.unread > 0 ? .bad : nil) {
          HStack(spacing: -6) {
            ForEach(Array(others.prefix(5).enumerated()), id: \.offset) { _, p in
              AvatarView(url: p.avatar, name: p.displayName, size: 24)
                .overlay(Circle().stroke(p.trained == true ? Palette.train : Palette.surface, lineWidth: 1.5))
            }
          }
        }
      } else {
        Face(icon: .users, label: tr("圈子", "Circles")) {
          HStack(spacing: 6) {
            Icon(.userPlus, size: 16)
            Text(tr("邀请", "Invite")).font(.system(size: 15, weight: .semibold))
          }
          .foregroundColor(Palette.text)
          .frame(maxWidth: .infinity, minHeight: 32)
          .background(Capsule().fill(Palette.surface2))
        }
      }
    }
    .buttonStyle(PressScale())
    .task { await store.refresh() }
  }
}

/// The header's sync capsule, observing the sync service on its own (the page around it stays Equatable on its model).
struct TodaySyncCapsule: View {
  let card: TodayModel.SyncCard?
  let tap: () -> Void
  let center: () -> Void
  @ObservedObject private var sync = HealthSyncService.shared

  var body: some View {
    SyncCapsule(state: SyncCapsuleState.of(overall: sync.diagnosis.overall, card: card, missingDays: nil))
      .onTapGesture(perform: tap)
      .onLongPressGesture(perform: center)
      .accessibilityAddTraits(.isButton)
      .accessibilityAction(named: Text(tr("同步中心", "Sync center")), center)
  }
}

extension View {
  /// A hero's L1 half sheet (direction.md 7): its own height and large; the page behind is not dimmed and stays usable
  /// while the sheet is at its height (iOS 16.4+; before that a plain dimmed sheet).
  @ViewBuilder func l1Detents(_ height: CGFloat) -> some View {
    let sheet = presentationDetents([.height(height), .large]).presentationDragIndicator(.visible)
    if #available(iOS 16.4, *) {
      sheet.presentationBackgroundInteraction(.enabled(upThrough: .height(height))).presentationCornerRadius(32)
    } else {
      sheet
    }
  }
}
