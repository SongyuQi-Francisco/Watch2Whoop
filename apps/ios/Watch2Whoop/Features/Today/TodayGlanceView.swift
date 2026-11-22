import SwiftUI

/// What the page asks of `TodayView` (the page's state stays there).
struct TodayGlanceActions {
  /// A door to a level below (2.1.0, drilldown option C′): 今日详情 (the dial, the ladder, the attention / 体征 face, a
  /// swipe up) or a metric page straight from here (the two readings). The second value is the door the level grows out
  /// of (`.zoomDoor`), nil for none.
  var level: (LevelRoute, String?) -> Void
  var calendar: () -> Void
  var backToToday: () -> Void
  /// One day back (−1) or forward (+1): the swipe, and the dial's VoiceOver actions.
  var step: (Int) -> Void
  var syncTap: () -> Void
  var syncCenter: () -> Void
  var recoveryNext: (RecoveryNext.Action) -> Void
  var food: () -> Void
  var circles: () -> Void
}

/// The first glance of Today (docs/design/2.0.1 `01-today`, drilldown `c-today` C′): date + the depth ladder + sync
/// capsule, the recovery dial, two readings (sleep, strain), one row of three faces (体征 or 关注, 饮食, 圈子). Nothing below
/// it: 训练 · 今晚 · 提醒 and the sync nudges live on 今日详情. The dial and the readings carry the door mark 「›」 (they have
/// no card fill; the faces have the shape of a button already). No section titles, no sentences.
struct TodayGlancePage: View, Equatable {
  static func == (a: TodayGlancePage, b: TodayGlancePage) -> Bool {
    a.model == b.model && a.isPast == b.isPast && a.circlesOn == b.circlesOn && a.day == b.day && a.member == b.member
      && (a.memberFaces == nil) == (b.memberFaces == nil)
  }

  /// Someone else's first glance (`MemberScreen`, read-only): no sync capsule and no Jarvis; the dial and the two readings
  /// only when their scores are shared, the ladder only when 今日详情 has anything (nothing is drawn as an empty shell).
  struct Member: Equatable {
    var scores: Bool
    var detail: Bool
  }

  let model: TodayModel
  let isPast: Bool
  let circlesOn: Bool
  /// The day shown (nil = the user's today), for the food face.
  let day: String?
  let session: AccountSession
  let run: TodayGlanceActions
  var member: Member?
  /// Someone else's faces (`MemberGlanceFaces`), in place of mine.
  var memberFaces: AnyView?
  @Environment(\.askAI) private var askAI

  private var g: TodayGlance { model.glance }
  private var showsScores: Bool { member?.scores ?? true }
  private var showsLadder: Bool { member?.detail ?? true }
  /// The door 今日详情 grows out of: the dial, when it is there.
  private var dialDoor: String? { showsScores ? "glance.dial" : nil }

  var body: some View {
    VStack(spacing: 0) {
      header
      if showsScores {
        hero.padding(.top, Rhythm.gap(32))
        readings.padding(.top, Rhythm.gap(40))
        faces.padding(.top, Rhythm.gap(44))
      } else {
        faces.padding(.top, Rhythm.gap(32))
      }
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
      // a look-back day's 「回到今天」 takes the middle: the ladder moves next to the capsule
      if isPast { ladder }
      if member == nil { TodaySyncCapsule(card: model.sync, page: session.sync, tap: run.syncTap, center: run.syncCenter) }
    }
    .frame(height: 44)
    // the ladder sits in the middle of the header (dd.css `.hdr .ladder.mid`), whatever the date and the capsule take
    .overlay { if !isPast { ladder } }
  }

  @ViewBuilder private var ladder: some View {
    if showsLadder { LevelLadder(level: 1, down: { run.level(.day(anchor: nil), dialDoor) }) }
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
      if d.value == nil, let n = model.recoveryNext { run.recoveryNext(n.action) } else { run.level(.day(anchor: nil), dialDoor) }
    } label: {
      TickDial(fraction: TodayGlanceRules.dialFraction(d.value), tone: d.tone,
               usual: d.usual.map { ($0.lowerBound / 100)...($0.upperBound / 100) },
               zones: TodayGlanceRules.recoveryZones.map { $0 / 100 }, side: side) {
        VStack(spacing: 0) {
          HStack(spacing: 3) {
            Text(tr("恢复", "Recovery")).font(.system(size: 13, weight: .medium)).tracking(2).foregroundColor(Palette.text2)
            if d.value != nil || model.recoveryNext == nil { DoorMark() }
          }
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
    }
    .buttonStyle(PressScale())
    .zoomDoor("glance.dial")
    .frame(maxWidth: .infinity)
    .contextMenu {
      if member == nil { Button { askAI(Self.recoveryPrompt(model)) } label: { Text(tr("问 Jarvis：恢复", "Ask Jarvis: recovery")) } }
      Button { run.level(.day(anchor: nil), dialDoor) } label: { Text(tr("今日详情", "Today's details")) }
    }
    .accessibilityElement(children: .ignore)
    .accessibilityLabel(dialLabel)
    .accessibilityHint(d.value == nil && model.recoveryNext != nil ? "" : tr("打开今日详情", "Opens today's details"))
    .accessibilityAddTraits(.isButton)
    .accessibilityActions {
      Button(tr("前一天", "Previous day")) { run.step(-1) }
      if isPast { Button(tr("后一天", "Next day")) { run.step(1) } }   // today has no next day
    }
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
      reading(g.sleep, label: tr("睡眠", "Sleep"), metric: "sleep_score")
      reading(g.strain, label: tr("负荷", "Strain"), metric: "strain")
    }
  }

  private func reading(_ x: TodayGlance.Gauge, label: String, metric: String) -> some View {
    Button { run.level(.metric(metric), "glance.reading.\(metric)") } label: {
      VStack(alignment: .leading, spacing: 8) {
        HStack(alignment: .firstTextBaseline, spacing: 6) {
          Text(x.text ?? "—").font(Typo.reading).foregroundColor(x.text == nil ? Palette.text2 : Palette.text)
          Text(label).font(Typo.label).foregroundColor(Palette.text2)
          Spacer(minLength: 0)
          DoorMark()
        }
        BulletGauge(value: x.value, max: x.max, usual: x.usual, target: x.target, step: x.step, majors: x.majors, tone: x.tone)
      }
      .frame(maxWidth: .infinity, minHeight: 59, alignment: .topLeading)
      .contentShape(Rectangle())
    }
    .buttonStyle(PressScale())
    .zoomDoor("glance.reading.\(metric)")
    .accessibilityElement(children: .ignore)
    .accessibilityLabel("\(label) \(x.text ?? "—")")
    .accessibilityHint(tr("打开\(label)的趋势", "Opens the \(label.lowercased()) trend"))
    .accessibilityAddTraits(.isButton)
  }

  // MARK: faces

  @ViewBuilder private var faces: some View {
    if let memberFaces {
      memberFaces
    } else {
      HStack(spacing: 8) {
        TodaySlotOne(model: model, sync: session.sync, run: run)
        TodayFoodFace(session: session, day: day, sync: session.sync, open: run.food, syncNow: run.syncTap)
        if circlesOn && !isPast { TodayCirclesFace(session: session, open: run.circles) }
      }
      // the circles are read here, on the row that is always there: the face itself is not drawn until they are known
      .task(id: circlesOn && !isPast) { if circlesOn && !isPast { await CirclesStore.shared(for: session).refresh() } }
    }
  }
}

/// Slot 1: the attention face when something is off on the day (tap = 今日详情 at 需要注意, with 体征 under it), else
/// 体征 (tap = 今日详情 at 体征), else nothing. When the page shows old data it says so (kit-states stale: refresh-cw, the
/// number in text2, the age in mid; a tap syncs).
struct TodaySlotOne: View {
  let model: TodayModel
  @ObservedObject var sync: SyncCoordinator
  let run: TodayGlanceActions

  var body: some View {
    SlotOneFace(model: model, age: TodayGlanceRules.staleAge(page: sync.status, card: model.sync), run: run)
  }
}

/// Slot 1's drawing, without the sync state (`age` nil: fresh; someone else's first glance never has one).
struct SlotOneFace: View {
  let model: TodayModel
  let age: String?
  let run: TodayGlanceActions

  var body: some View {
    let g = model.glance
    if let a = model.attention.first {
      Button { age != nil ? run.syncTap() : run.level(.day(anchor: "attention"), "glance.slot1") } label: {
        Face(icon: .circleAlert, label: a.label, number: a.value, unit: a.unit, dimmed: age != nil, dot: age == nil ? a.tone : nil, age: age) {
          if !g.attentionWeek.isEmpty { Lollipops(values: g.attentionWeek, tone: a.tone) } else { Color.clear }
        }
      }
      .buttonStyle(PressScale())
      .zoomDoor("glance.slot1")
      .contextMenu {
        Button { run.level(.day(anchor: "attention"), "glance.slot1") } label: { Text(tr("看详情", "Details")) }
        if !model.vitals.isEmpty { Button { run.level(.day(anchor: "vitals"), "glance.slot1") } label: { Text(tr("体征", "Vitals")) } }
      }
    } else if !model.vitals.isEmpty {
      let c = TodayGlanceRules.vitalsCount(model.vitals)
      Button { age != nil ? run.syncTap() : run.level(.day(anchor: "vitals"), "glance.slot1") } label: {
        Face(icon: .heartPulse, label: tr("体征", "Vitals"), number: "\(c.ok)/\(c.total)", dimmed: age != nil, age: age) {
          VitalPills(vitals: model.vitals)
        }
      }
      .buttonStyle(PressScale())
      .zoomDoor("glance.slot1")
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

/// The 饮食 face: eaten today against the goal (ink gauge, target tick at the goal). No share or no log: no face. Offline
/// it is the cached copy and says so (kit-states stale; a tap syncs).
struct TodayFoodFace: View {
  let day: String?
  @ObservedObject var sync: SyncCoordinator
  let open: () -> Void
  let syncNow: () -> Void
  @StateObject private var store: FoodStore

  init(session: AccountSession, day: String?, sync: SyncCoordinator, open: @escaping () -> Void, syncNow: @escaping () -> Void) {
    self.day = day
    self.sync = sync
    self.open = open
    self.syncNow = syncNow
    _store = StateObject(wrappedValue: FoodStore(session: session, cardOnly: true))
  }

  var body: some View {
    Group {
      if let n = store.nutrition, n.error == nil {
        // the food log has no sync time of its own: only an offline page makes it stale
        let age = TodayGlanceRules.staleAge(page: sync.status, card: nil)
        FoodFace(nutrition: n, age: age) { age != nil ? syncNow() : open() }
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

/// The 饮食 face's drawing (my first glance's and someone else's): eaten today against the goal.
struct FoodFace: View {
  let nutrition: NutritionDay
  var age: String?
  let tap: () -> Void

  var body: some View {
    let kcal = nutrition.intake.kcal ?? 0, goal = nutrition.goal.kcal ?? 0
    Button(action: tap) {
      Face(icon: .soup, label: tr("饮食", "Food"), number: RecordFormat.num(nutrition.intake.kcal), unit: "kcal", dimmed: age != nil, age: age) {
        // no goal: a fixed 2,500 kcal scale (the design's), never a bar that is always full
        let top = goal > 0 ? max(goal * 1.2, kcal) : max(2500, kcal * 1.2)
        BulletGauge(value: kcal, max: top, target: goal > 0 ? goal...goal : nil, step: top / 5, tone: .text)
      }
    }
    .buttonStyle(PressScale())
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
    let mode = CirclesFaceMode.of(listLoaded: store.list != nil, hasCircles: store.hasCircles, overviewLoaded: store.overview != nil)
    if mode != .hidden { face(mode: mode, others: others, trained: trained) }
  }

  private func face(mode: CirclesFaceMode, others: [MemberCard], trained: Int) -> some View {
    Button(action: open) {
      if mode == .count {
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
  }
}

/// The header's sync capsule, observing the sync service on its own (the page around it stays Equatable on its model).
struct TodaySyncCapsule: View {
  let card: TodayModel.SyncCard?
  /// The page's own refresh (offline = the page is the cached copy).
  @ObservedObject var page: SyncCoordinator
  let tap: () -> Void
  let center: () -> Void
  @ObservedObject private var sync = HealthSyncService.shared

  var body: some View {
    let d = sync.diagnosis
    let state = SyncCapsuleState.of(overall: d.overall, page: page.status, card: card, background: d.background)
    SyncCapsule(state: state)
      .onTapGesture { state.opensCenter ? center() : tap() }
      .onLongPressGesture(perform: center)
      .accessibilityAddTraits(.isButton)
      .accessibilityAction(named: Text(tr("同步中心", "Sync center")), center)
  }
}
