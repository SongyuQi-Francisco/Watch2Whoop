import SwiftUI

/// The pages 记录 pushes (2.0.1): today's session (11), the weekly muscle map (13) and all its muscles, the history (14),
/// the exercise library (15), the week's plan.
enum RecordRoute: Hashable {
  case session, muscles, allMuscles, history, library, week
}

/// 记录 → 训练, the tab root (docs/design/2.0.1 `12-record`): the set ring with today's muscles inside (tap = today's
/// session), two readings (the day's sets, the week's training days), three faces (肌肉 → weekly map, 历史 → month + cards,
/// 动作库 → library). Below the fold the rest of training stays one tap away: the week's plan (split, suggestions,
/// restrictions), training settings and the training log link.
struct TrainingRoot: View {
  @ObservedObject var store: TrainingStore
  @Binding var toast: Toast?
  let push: (RecordRoute) -> Void
  @State private var sheet: TrainingSheet?
  @Environment(\.askAI) private var askAI

  static var ringSide: CGFloat { min(300, max(220, UIScreen.main.bounds.height * 0.34)) }

  var body: some View {
    VStack(spacing: 0) {
      hero.padding(.top, 24)
      readings.padding(.top, 36)
      faces.padding(.top, 40)
      below.padding(.top, 200)
    }
    .task {
      await store.refresh()
      if sheet == nil, let dev = RecordDevShot.value("W2W_DEV_TRAINING") { sheet = TrainingSheet.dev(dev, muscle: store.muscle) }
    }
    .trainingSheets(store: store, sheet: $sheet, toast: $toast)
  }

  // MARK: hero: the set ring

  private var hero: some View {
    let s = store.session
    let arcs = s.map { RecordGlance.arcs(RecordGlance.sets($0, rows: store.rows)) } ?? []
    let shares = s.map { RecordGlance.regionShares($0.exercises, rows: store.rows) } ?? [:]
    let side = Self.ringSide
    return Button { push(.session) } label: {
      SetRing(arcs: arcs, side: side) {
        VStack(spacing: 10) {
          BodyPair(sex: store.muscle?.sex ?? "male", spacing: side * 0.08, fill: BodyPair.shares(shares))
            .frame(height: side * 0.56)
          Text(sessionLabel).font(.system(size: 17, weight: .medium)).tracking(2).foregroundColor(Palette.text2).lineLimit(1)
        }
        .frame(width: side * 0.72)
        .padding(.top, side * 0.08)
      }
    }
    .buttonStyle(PressScale())
    .frame(maxWidth: .infinity)
    .contextMenu {
      Button { askAI(tr("今天练什么", "What should I train today")) } label: { Text(tr("问 Jarvis：今天的训练", "Ask Jarvis: today's training")) }
      Button { push(.session) } label: { Text(tr("看详情", "Details")) }
    }
    .accessibilityElement(children: .ignore)
    .accessibilityLabel(ringLabel)
    .accessibilityAddTraits(.isButton)
  }

  private var sessionLabel: String {
    guard let s = store.session else { return store.refreshing ? "…" : "—" }
    if s.exercises.isEmpty { return s.restDay ? tr("休息", "Rest") : tr("无计划", "No plan") }
    return s.title.isEmpty ? tr("今天", "Today") : TrainingWeek.shortPlan(s.title)
  }

  private var ringLabel: String {
    guard let s = store.session, !s.exercises.isEmpty else { return "\(tr("今天的训练", "Today's training")) \(sessionLabel)" }
    return tr("\(sessionLabel)，\(s.doneSets)/\(s.totalSets) 组", "\(sessionLabel), \(s.doneSets) of \(s.totalSets) sets")
  }

  // MARK: readings: the day's sets, the week

  private var readings: some View {
    HStack(alignment: .top, spacing: 20) {
      setsReading
      weekReading
    }
  }

  private var setsReading: some View {
    let s = store.session
    let sets = s.map { RecordGlance.sets($0, rows: store.rows) } ?? []
    let total = sets.count, done = sets.filter(\.done).count
    return Button { push(.session) } label: {
      VStack(alignment: .leading, spacing: 8) {
        HStack(alignment: .firstTextBaseline, spacing: 6) {
          Text(total > 0 ? (done > 0 ? "\(done)/\(total)" : "\(total)") : "—").font(Typo.reading)
            .foregroundColor(total > 0 ? Palette.text : Palette.text2)
          Text(tr("组", "sets")).font(Typo.label).foregroundColor(Palette.text2)
        }
        BulletGauge(value: Double(done), max: Double(max(1, total)), step: 1, majors: RecordGlance.exerciseEdges(sets), tone: .train)
      }
      .frame(maxWidth: .infinity, minHeight: 59, alignment: .topLeading)
      .contentShape(Rectangle())
    }
    .buttonStyle(PressScale())
    .accessibilityElement(children: .ignore)
    .accessibilityLabel(tr("\(done)/\(total) 组", "\(done) of \(total) sets"))
    .accessibilityAddTraits(.isButton)
  }

  private var weekReading: some View {
    let w = store.week
    return Button { push(.week) } label: {
      VStack(alignment: .leading, spacing: 8) {
        HStack(alignment: .firstTextBaseline, spacing: 4) {
          Text(w.map { "\($0.trainedDays)" } ?? "—").font(Typo.reading).foregroundColor(w == nil ? Palette.text2 : Palette.text)
          if let w, let target = RecordGlance.weekTarget(w) {
            Text("/\(target)").font(.system(size: 17, weight: .regular).monospacedDigit()).foregroundColor(Palette.text2)
          }
          Text(tr("本周", "this week")).font(Typo.label).foregroundColor(Palette.text2).padding(.leading, 2)
        }
        if let w { WeekDotsView(dots: RecordGlance.weekDots(w, today: store.today)).frame(height: 22) }
      }
      .frame(maxWidth: .infinity, minHeight: 59, alignment: .topLeading)
      .contentShape(Rectangle())
    }
    .buttonStyle(PressScale())
    .accessibilityElement(children: .ignore)
    .accessibilityLabel(tr("本周练了 \(w?.trainedDays ?? 0) 天", "Trained \(w?.trainedDays ?? 0) days this week"))
    .accessibilityAddTraits(.isButton)
  }

  // MARK: faces: 肌肉 · 历史 · 动作库

  private var faces: some View {
    HStack(spacing: 8) {
      if let m = store.muscle { muscleFace(m) }
      historyFace
      libraryFace
    }
  }

  private func muscleFace(_ m: MuscleLoad) -> some View {
    let t = RecordGlance.onTarget(m, range: .week)
    let regions = m.regions(.week)
    return Button { push(.muscles) } label: {
      Face(icon: .personStanding, label: tr("肌肉", "Muscles"), number: "\(t.ok)", unit: "/\(t.total)") { Color.clear }
        .overlay(alignment: .bottomTrailing) {
          if let side = MuscleBodies.shared.side(sex: m.sex, front: true) {
            MuscleBodyView(side: side, regions: regions, lineScale: 0.4, fill: { MuscleWeekPage.color(regions[$0]?.status) })
              .frame(height: 70).padding(.trailing, 10).padding(.bottom, 12)
              .allowsHitTesting(false)
          }
        }
    }
    .buttonStyle(PressScale())
  }

  private var historyFace: some View {
    let m = store.calendar
    let strains = (m.map { RecordGlance.trainedDays($0) } ?? []).prefix(7).reversed().map { $0.strain }
    return Button { push(.history) } label: {
      Face(icon: .calendar, label: tr("历史", "History"), number: m?.trainingDays.map(String.init) ?? "—", unit: tr("次", "times")) {
        if !strains.isEmpty { MiniBars(values: Array(strains), tone: .train, maxValue: RecordParams.strainScaleMax, height: 24) } else { Color.clear }
      }
    }
    .buttonStyle(PressScale())
  }

  private var libraryFace: some View {
    Button { push(.library) } label: {
      Face(icon: .bookOpen, label: tr("动作库", "Library")) {
        HStack {
          Icon(.search, size: 15).foregroundColor(Palette.text2)
          Spacer(minLength: 0)
        }
        .padding(.horizontal, 10)
        .frame(height: 26)
        .background(Capsule().fill(Palette.surface2))
      }
    }
    .buttonStyle(PressScale())
  }

  // MARK: below the fold

  private var below: some View {
    RowGroup {
      ListRow(icon: .calendarDays, title: tr("本周计划", "This week's plan"), value: store.week?.planName.map(TrainingWeek.shortPlan)) { push(.week) }
      if let w = store.week, !w.splits.isEmpty {
        RowDivider(inset: 0)
        ListRow(icon: .refreshCw, title: w.hasPlan ? tr("切换分化", "Change split") : tr("选周计划", "Pick a weekly plan")) { sheet = .split }
      }
      RowDivider(inset: 0)
      ListRow(icon: .settings, title: tr("训练设置", "Training settings")) { sheet = .goals }
      if let log = store.week?.log, !log.connected {
        RowDivider(inset: 0)
        ListRow(icon: .link, title: tr("训练记录", "Training log"), value: log.word.text, tone: log.word.tone) { sheet = .log }
      }
    }
    .id("n5.below")
  }
}

// MARK: - sheets shared by the training pages

extension View {
  /// The sheets of 记录 → 训练 (library pages, exercise, muscle, split, settings …): asked from inside a sheet, a Jarvis
  /// question waits until the sheet has gone.
  func trainingSheets(store: TrainingStore, sheet: Binding<TrainingSheet?>, toast: Binding<Toast?>) -> some View {
    modifier(TrainingSheetsModifier(store: store, sheet: sheet, toast: toast))
  }
}

private struct TrainingSheetsModifier: ViewModifier {
  let store: TrainingStore
  @Binding var sheet: TrainingSheet?
  @Binding var toast: Toast?
  @State private var askAfterClose: String?
  @Environment(\.askAI) private var askAI

  func body(content: Content) -> some View {
    content.sheet(item: $sheet, onDismiss: {
      if let p = askAfterClose {
        askAfterClose = nil
        askAI(p)
      }
    }) { s in
      TrainingSheetHost(store: store, start: s, ask: { prompt in
        askAfterClose = prompt
        sheet = nil
      }) { message in
        sheet = nil
        if let message { toast = message }
      }
      .presentationDetents([.large])
    }
  }
}

/// The week's plan, pushed from 记录 → 训练 (本周 reading, 本周计划 row): the week strip with its split and library links,
/// the suggestions and the user's exercise restrictions (what the 2.0.0 training page showed).
struct TrainingWeekPage: View {
  @ObservedObject var store: TrainingStore
  @Binding var toast: Toast?
  @State private var sheet: TrainingSheet?
  @State private var detailDay: String?

  var body: some View {
    RecordPage(title: tr("本周计划", "This week's plan")) {
      VStack(alignment: .leading, spacing: 22) {
        if let w = store.week {
          TrainingWeekCard(week: w, today: store.today, openDay: { d in if d > store.today { sheet = .planDay(d) } else { detailDay = d } },
                           split: { sheet = .split }, library: { sheet = .library }, connectLog: { sheet = .log })
          if !w.suggestions.isEmpty { suggestions(w) }
        } else {
          EmptyNote(text: store.refreshing ? "…" : tr("暂无", "None"))
        }
        ExerciseLimitsCard(rules: ExerciseRule.merged(store.rules, week: store.week)) { sheet = .exercise($0.ref) }
      }
      .padding(.top, 16)
    }
    .trainingSheets(store: store, sheet: $sheet, toast: $toast)
    .trainingDaySheet(store: store, day: $detailDay)
  }

  private func suggestions(_ w: TrainingWeek) -> some View {
    VStack(alignment: .leading, spacing: 10) {
      SectionHeader(title: tr("建议", "Suggestions"))
      RowGroup {
        ForEach(Array(w.suggestions.enumerated()), id: \.element.id) { n, x in
          if n > 0 { RowDivider(inset: 0) }
          Button { sheet = .suggestion(x) } label: {
            HStack(spacing: 8) {
              Text(x.name).font(Typo.body).foregroundColor(Palette.text).lineLimit(1)
              Spacer()
              Badge(label: x.nextKg.map { "→ \(RecordFormat.num($0, 1)) kg" } ?? tr("建议", "Suggestion"), tone: x.warning ? .mid : .good)
              Icon(.chevronRight, size: 14).foregroundColor(Palette.text3)
            }
            .padding(.vertical, 11)
            .contentShape(Rectangle())
          }
          .buttonStyle(PressScale())
        }
      }
    }
  }
}

private struct DayRef: Identifiable {
  let day: String
  var id: String { day }
}

extension View {
  /// A past day's training (watch workouts + logged strength) as a sheet.
  func trainingDaySheet(store: TrainingStore, day: Binding<String?>) -> some View {
    sheet(item: Binding(get: { day.wrappedValue.map { DayRef(day: $0) } }, set: { day.wrappedValue = $0?.day })) { ref in
      SheetContainer(title: tr("训练 · \(RecordDay.short(ref.day))", "Training · \(RecordDay.short(ref.day))"), close: { day.wrappedValue = nil }) {
        TrainingDaySheet(store: store, day: ref.day)
      }
      .presentationDetents([.medium, .large])
    }
  }
}
