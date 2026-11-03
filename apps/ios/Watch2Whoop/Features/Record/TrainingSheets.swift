import SwiftUI

/// The sheets of 记录 → 训练, each with its own navigation (library → exercise → alternative, muscle → exercise).
enum TrainingSheet: Identifiable {
  case library, custom, split, goals, log
  case exercise(String)
  case muscle(MuscleLoad.Muscle, MuscleLoad.Range)
  case planDay(String)
  case suggestion(TrainingSuggestion)

  var id: String {
    switch self {
    case .library: return "library"
    case .custom: return "custom"
    case .split: return "split"
    case .goals: return "goals"
    case .log: return "log"
    case .exercise(let s): return "exercise:\(s)"
    case .muscle(let m, _): return "muscle:\(m.key)"
    case .planDay(let d): return "plan:\(d)"
    case .suggestion(let s): return "suggestion:\(s.id)"
    }
  }

  /// Debug screenshots (`W2W_DEV_TRAINING`, RecordDevShot): library, custom, split, goals, log, exercise:<slug>, plan:<date>,
  /// muscle:<key>.
  static func dev(_ value: String, muscle: MuscleLoad?) -> TrainingSheet? {
    let parts = value.split(separator: ":", maxSplits: 1).map(String.init)
    switch parts.first {
    case "library": return .library
    case "custom": return .custom
    case "split": return .split
    case "goals": return .goals
    case "log": return .log
    case "exercise": return parts.count > 1 ? .exercise(parts[1]) : nil
    case "plan": return parts.count > 1 ? .planDay(parts[1]) : nil
    case "muscle": return muscle?.muscles.first { parts.count > 1 && $0.key == parts[1] }.map { TrainingSheet.muscle($0, .week) }
    default: return nil
    }
  }
}

/// What the pages of a training sheet share: the store, how to close the sheet, how to say something in it.
struct TrainingNav {
  let store: TrainingStore
  /// Closes the sheet; the message shows on the training page.
  let finish: (Toast?) -> Void
  /// A message over the sheet.
  let say: (Toast) -> Void
  /// A question for Jarvis: asked once the sheet has gone.
  var ask: ((String) -> Void)? = nil
}

struct TrainingSheetHost: View {
  @ObservedObject var store: TrainingStore
  let start: TrainingSheet
  var ask: ((String) -> Void)? = nil
  let finish: (Toast?) -> Void
  @State private var toast: Toast?

  var body: some View {
    let nav = TrainingNav(store: store, finish: finish, say: { toast = $0 }, ask: ask)
    NavigationStack {
      switch start {
      case .library: ExerciseLibraryView(nav: nav)
      case .custom: CustomExercisesView(nav: nav)
      case .split: SplitPickerView(nav: nav)
      case .goals: TrainingGoalsView(nav: nav)
      case .log: TrainingLogView(nav: nav)
      case .exercise(let slug): ExerciseDetailView(nav: nav, slug: slug)
      case .muscle(let m, let range): MuscleDetailView(nav: nav, muscle: m, range: range)
      case .planDay(let day): PlanDayView(nav: nav, day: day)
      case .suggestion(let s): SuggestionView(nav: nav, suggestion: s)
      }
    }
    .tint(Palette.text)
    .toast($toast)
    .presentationDragIndicator(.visible)
  }
}

/// A page of a training sheet: title bar with a close button, scrolling content on the sheet's background.
struct TrainingPage<Content: View>: View {
  let nav: TrainingNav
  let title: String
  @ViewBuilder var content: () -> Content

  var body: some View {
    ScrollView(.vertical, showsIndicators: false) {
      content()
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.horizontal, Metrics.gutter).padding(.top, 8).padding(.bottom, 28)
    }
    .background(Palette.sheet.ignoresSafeArea())
    .navigationTitle(title)
    .navigationBarTitleDisplayMode(.inline)
    .toolbar {
      ToolbarItem(placement: .navigationBarTrailing) {
        Button { nav.finish(nil) } label: { Icon(.x, size: 18).foregroundColor(Palette.text2) }
          .accessibilityLabel(tr("关闭", "Close"))
      }
    }
  }
}

/// Rows in one block on a sheet's background.
struct SheetRows<Content: View>: View {
  @ViewBuilder var content: () -> Content

  var body: some View {
    VStack(spacing: 0) { content() }
      .padding(.horizontal, 14)
      .background(RoundedRectangle(cornerRadius: Metrics.cardRadius, style: .continuous).fill(Palette.inSheet))
  }
}

/// A tag that is on or off (`.cx-chip`, `.mml-chip`).
struct ToggleChip: View {
  let title: String
  let on: Bool
  var background = Palette.inSheet
  let action: () -> Void

  var body: some View {
    Button(action: action) {
      Text(title).font(.system(size: 14, weight: .medium)).lineLimit(1)
        .foregroundColor(on ? Palette.onAccent : Palette.text)
        .padding(.vertical, 8).padding(.horizontal, 12)
        .background(Capsule().fill(on ? Palette.accent : background))
    }
    .buttonStyle(PressScale())
    .accessibilityAddTraits(on ? .isSelected : [])
  }
}

/// Offline or failed, with a retry.
struct RetryNote: View {
  let text: String
  let retry: () -> Void

  var body: some View {
    VStack(spacing: 10) {
      EmptyNote(text: text)
      Button(tr("重试", "Retry"), action: retry)
        .font(.system(size: 14, weight: .medium)).foregroundColor(Palette.text)
        .padding(.vertical, 8).padding(.horizontal, 16)
        .background(Capsule().fill(Palette.inSheet))
        .buttonStyle(PressScale())
    }
    .frame(maxWidth: .infinity)
  }
}

/// 禁用 / 注意, exactly as the server says.
struct ExerciseStatusBadge: View {
  let status: String
  var showActive = false

  var body: some View {
    switch status {
    case "banned": Badge(label: tr("禁用", "Banned"), tone: .bad)
    case "caution": Badge(label: tr("注意", "Caution"), tone: .mid)
    default: if showActive { Badge(label: tr("正常", "Normal"), tone: .good) }
    }
  }
}

/// A prepared question for the user's AI; hidden when no AI is set up (app.js canAskAI).
struct AskJarvisButton: View {
  let title: String
  let prompt: String
  var background = Palette.inSheet
  /// How to ask from inside a sheet; nil = directly.
  var ask: ((String) -> Void)? = nil
  @Environment(\.askAI) private var askAI
  @Environment(\.memberView) private var member

  private var canAsk: Bool { member == nil && JarvisRouter.current?.ai.mode != JarvisAI.Mode.none }

  var body: some View {
    if canAsk {
      SecondaryButton(icon: .sparkles, title: title, background: background) {
        if let ask { ask(prompt) } else { askAI(prompt) }
      }
    }
  }
}

// MARK: - remove a planned exercise (training-day.js askRemove, issue #172)

/// This day only (undo in the toast), or out of the session template (asks a second time).
struct RemoveExerciseDialogs: ViewModifier {
  @Binding var target: RemoveTarget?
  let store: TrainingStore
  let say: (Toast) -> Void
  @State private var everyWeek: RemoveTarget?

  func body(content: Content) -> some View {
    content
      .confirmationDialog(target?.name ?? "", isPresented: Binding(get: { target != nil }, set: { if !$0 { target = nil } }),
                          titleVisibility: .visible, presenting: target) { t in
        Button(t.isToday ? tr("只删今天的", "Remove for today only") : tr("只删这一天的", "Remove for this day only")) { removeDay(t) }
        if t.canRemoveFromTemplate {
          Button(tr("以后都不要这个动作…", "Never include it again…"), role: .destructive) {
            // The second dialog can only come up after the first has gone.
            Task {
              try? await Task.sleep(nanoseconds: 350_000_000)
              everyWeek = t
            }
          }
        }
        Button(tr("取消", "Cancel"), role: .cancel) {}
      }
      .confirmationDialog(sessionWarning, isPresented: Binding(get: { everyWeek != nil }, set: { if !$0 { everyWeek = nil } }),
                          titleVisibility: .visible, presenting: everyWeek) { t in
        Button(tr("确定，以后都不要", "Yes, never again"), role: .destructive) { removeTemplate(t) }
        Button(tr("返回", "Back"), role: .cancel) {}
      }
  }

  private var sessionWarning: String {
    let name = everyWeek?.sessionTitle ?? everyWeek?.sessionName ?? ""
    return tr("会从训练组「\(name)」里删掉，以后每周都不再出现。", "It will be deleted from the session “\(name)” and won't appear in any week from now on.")
  }

  private func removeDay(_ t: RemoveTarget) {
    Task {
      if let error = await store.removeForDay(t) {
        say(Toast(error))
        return
      }
      say(Toast(tr("已移除「\(t.name)」", "Removed “\(t.name)”"), actionTitle: tr("撤销", "Undo")) {
        Task {
          if let error = await store.restore(t.slug, day: t.date) { say(Toast(error)) }
        }
      })
    }
  }

  private func removeTemplate(_ t: RemoveTarget) {
    Task {
      let name = t.sessionTitle ?? t.sessionName ?? ""
      say(Toast(await store.removeFromTemplate(t) ?? tr("已从「\(name)」里删除", "Deleted from “\(name)”")))
    }
  }
}

// MARK: - 切换分化

struct SplitPickerView: View {
  let nav: TrainingNav
  @State private var chosen = ""
  @State private var busy = false

  private var splits: [SplitOption] { nav.store.week?.splits ?? [] }

  var body: some View {
    TrainingPage(nav: nav, title: tr("周训练分化计划", "Weekly split")) {
      VStack(alignment: .leading, spacing: 14) {
        WrapLayout(spacing: 8) {
          ForEach(splits) { s in ToggleChip(title: s.name, on: s.type == chosen) { chosen = s.type } }
        }
        if let cur = splits.first(where: { $0.type == chosen }) {
          Text(cur.summary).font(Typo.caption).foregroundColor(Palette.text2).fixedSize(horizontal: false, vertical: true)
          SheetRows {
            ForEach(Array(cur.days.enumerated()), id: \.element.id) { i, d in
              if i > 0 { RowDivider(inset: 0) }
              HStack(alignment: .firstTextBaseline) {
                Text("\(ExerciseVocab.weekday(d.dayOfWeek)) · \(d.name)").font(Typo.body).foregroundColor(Palette.text)
                  .fixedSize(horizontal: false, vertical: true)
                Spacer(minLength: 10)
                Text(tr("\(d.exerciseCount) 动作", "\(d.exerciseCount) exercises")).font(Typo.caption.monospacedDigit()).foregroundColor(Palette.text2)
              }
              .padding(.vertical, 11)
            }
          }
          PrimaryButton(title: tr("启用该周计划（自动避开禁用动作）", "Use this weekly plan (avoids restricted exercises)"), busy: busy) { activate(cur) }
        }
      }
    }
    .onAppear { if chosen.isEmpty { chosen = SplitOption.initial(splits, current: nav.store.week?.splitType) } }
  }

  private func activate(_ s: SplitOption) {
    busy = true
    Task {
      let out = await nav.store.activate(s)
      busy = false
      if out.ok { nav.finish(Toast(out.message)) } else { nav.say(Toast(out.message)) }
    }
  }
}

// MARK: - 训练设置

struct TrainingGoalsView: View {
  let nav: TrainingNav
  @State private var goals: TrainingGoals?
  @State private var failed: String?
  @State private var focus: Set<String> = []
  @State private var text = ""
  @State private var note: String?
  @State private var busy = false

  private static let maxGoal = 200

  var body: some View {
    TrainingPage(nav: nav, title: tr("训练设置", "Training settings")) {
      if let g = goals {
        VStack(alignment: .leading, spacing: 12) {
          HStack(spacing: 6) {
            Text(tr("重点肌群", "Focus muscles")).font(Typo.section).foregroundColor(Palette.text2)
            Text(tr("最多 \(g.maxFocus) 个", "at most \(g.maxFocus)")).font(Typo.label).foregroundColor(Palette.text3)
          }
          WrapLayout(spacing: 8) {
            ForEach(g.muscles) { m in
              ToggleChip(title: m.name, on: focus.contains(m.key)) { toggle(m.key, max: g.maxFocus) }
            }
          }
          Text(tr("体态 / 形体目标", "Posture / physique goal")).font(Typo.section).foregroundColor(Palette.text2).padding(.top, 4)
          TextField(tr("例如：改善圆肩，背更厚，腿更有线条", "e.g. fix rounded shoulders, a thicker back, more defined legs"), text: $text, axis: .vertical)
            .lineLimit(2...5)
            .font(Typo.body).foregroundColor(Palette.text)
            .padding(12)
            .background(RoundedRectangle(cornerRadius: 12, style: .continuous).fill(Palette.inSheet))
            .onChange(of: text) { t in if t.count > Self.maxGoal { text = String(t.prefix(Self.maxGoal)) } }
          if let note { Text(note).font(Typo.caption).foregroundColor(Palette.bad) }
          PrimaryButton(title: tr("保存", "Save"), busy: busy) { save(g) }
        }
      } else if let failed {
        RetryNote(text: failed) { Task { await load() } }
      } else {
        EmptyNote(text: "…")
      }
    }
    .task { await load() }
  }

  private func load() async {
    failed = nil
    do {
      let g = try await nav.store.trainingGoals()
      goals = g
      focus = Set(g?.focus ?? [])
      text = g?.goal ?? ""
    } catch {
      failed = TrainingStore.message(error)
    }
  }

  private func toggle(_ key: String, max: Int) {
    note = nil
    if focus.contains(key) { focus.remove(key) }
    else if focus.count >= max { note = tr("最多 \(max) 个", "at most \(max)") }
    else { focus.insert(key) }
  }

  private func save(_ g: TrainingGoals) {
    busy = true
    let keys = g.muscles.map(\.key).filter(focus.contains)
    Task {
      let error = await nav.store.saveGoals(focus: keys, goal: text)
      busy = false
      if let error { note = error } else { nav.finish(Toast(tr("已保存", "Saved"))) }
    }
  }
}

// MARK: - 训练记录

/// The training log's link and the self-service connect (app.js trainingLogSheet).
struct TrainingLogView: View {
  let nav: TrainingNav
  @State private var link: TrainingLogLink?
  @State private var error: String?
  @State private var busy = false

  var body: some View {
    TrainingPage(nav: nav, title: tr("训练记录", "Training log")) {
      let shown = link ?? nav.store.week?.log
      VStack(alignment: .leading, spacing: 12) {
        if let shown {
          HStack {
            Text(tr("训练记录", "Training log")).font(Typo.body).foregroundColor(Palette.text2)
            Spacer()
            Badge(label: shown.word.text, tone: shown.word.tone)
          }
          if let hint = shown.hint { Text(hint).font(Typo.caption).foregroundColor(Palette.text2).fixedSize(horizontal: false, vertical: true) }
          if let error = error ?? shown.error { Text(error).font(Typo.caption).foregroundColor(Palette.bad).fixedSize(horizontal: false, vertical: true) }
          if shown.canConnect {
            PrimaryButton(title: busy ? tr("连接中…", "Connecting…") : tr("连接", "Connect"), busy: busy) { connect() }
          }
        } else {
          EmptyNote(text: tr("暂无", "None"))
        }
      }
    }
  }

  private func connect() {
    busy = true
    error = nil
    Task {
      let out = await nav.store.connectTrainingLog()
      busy = false
      if out.link?.connected == true {
        nav.finish(Toast(tr("训练记录已连接", "Training log connected")))
      } else {
        link = out.link ?? link
        error = out.error
      }
    }
  }
}

// MARK: - a progression suggestion

struct SuggestionView: View {
  let nav: TrainingNav
  let suggestion: TrainingSuggestion

  var body: some View {
    TrainingPage(nav: nav, title: suggestion.name) {
      VStack(alignment: .leading, spacing: 12) {
        if let t = suggestion.title { Text(t).font(Typo.body).foregroundColor(Palette.text).fixedSize(horizontal: false, vertical: true) }
        if let kg = suggestion.nextKg { KeyValueRow(label: tr("下次重量", "Next weight"), value: "\(RecordFormat.num(kg, 1)) kg") }
        if let slug = suggestion.slug {
          NavigationLink { ExerciseDetailView(nav: nav, slug: slug) } label: {
            HStack {
              Text(tr("动作指导", "How to")).font(Typo.bodyStrong).foregroundColor(Palette.text)
              Spacer()
              Icon(.chevronRight, size: 14).foregroundColor(Palette.text3)
            }
            .padding(14)
            .background(RoundedRectangle(cornerRadius: Metrics.tileRadius, style: .continuous).fill(Palette.inSheet))
          }
          .buttonStyle(PressScale())
        }
      }
    }
  }
}

// MARK: - a future day's plan

/// One upcoming day of the plan: tap an exercise for its guide, × to remove it, and the ones removed for this day can be
/// put back (app.js planSheet).
struct PlanDayView: View {
  let nav: TrainingNav
  let day: String
  @ObservedObject private var store: TrainingStore
  @State private var removing: RemoveTarget?

  init(nav: TrainingNav, day: String) {
    self.nav = nav
    self.day = day
    store = nav.store
  }

  private var entry: TrainingWeek.Day? { store.week?.days.first { $0.date == day } }

  var body: some View {
    TrainingPage(nav: nav, title: tr("计划 · \(RecordDay.short(day))", "Plan · \(RecordDay.short(day))")) {
      VStack(alignment: .leading, spacing: 14) {
        if let from = entry?.changedFrom {
          KeyValueRow(label: tr("原计划", "Original plan"), value: from.isEmpty ? tr("休息", "Rest") : from.joined(separator: " · "))
        }
        if let sessions = entry?.sessions, !sessions.isEmpty {
          ForEach(sessions) { s in session(s) }
        } else {
          EmptyNote(text: entry?.restDay == true ? tr("休息", "Rest day") : tr("无计划", "No plan"))
        }
        if let gone = entry?.removed, !gone.isEmpty {
          RemovedRow(removed: gone, background: Palette.inSheet) { slug in restore(slug) }
        }
      }
    }
    .modifier(RemoveExerciseDialogs(target: $removing, store: store, say: nav.say))
  }

  private func session(_ s: PlanSession) -> some View {
    VStack(alignment: .leading, spacing: 8) {
      Text(s.name).font(Typo.section).foregroundColor(Palette.text2)
      SheetRows {
        ForEach(Array(s.exercises.enumerated()), id: \.element.id) { i, e in
          if i > 0 { RowDivider(inset: 0) }
          row(e, s)
        }
      }
    }
  }

  private func row(_ e: WeekExercise, _ s: PlanSession) -> some View {
    HStack(spacing: 6) {
      NavigationLink { ExerciseDetailView(nav: nav, slug: e.slug ?? e.name) } label: {
        PlanExerciseLabel(e: e)
        .padding(.vertical, 10)
        .contentShape(Rectangle())
      }
      .buttonStyle(PressScale())
      if let slug = e.slug {
        Button {
          removing = RemoveTarget(slug: slug, name: e.name, date: day, isToday: false, dayOfWeek: s.dayOfWeek, sessionName: s.rawName,
                                  sessionTitle: s.name, addedForDay: s.addedForDay)
        } label: {
          Icon(.x, size: 16).foregroundColor(Palette.text3).frame(width: 36, height: 44)
        }
        .accessibilityLabel(tr("移除这个动作", "Remove this exercise"))
      }
    }
  }

  private func restore(_ slug: String) {
    Task { if let error = await store.restore(slug, day: day) { nav.say(Toast(error)) } }
  }
}

/// 已移除: the exercises taken off this date, each with 恢复.
struct RemovedRow: View {
  let removed: [RemovedExercise]
  var background = Palette.surface
  let restore: (String) -> Void

  var body: some View {
    VStack(alignment: .leading, spacing: 8) {
      Text(tr("已移除", "Removed")).font(Typo.label).foregroundColor(Palette.text2)
      WrapLayout(spacing: 6) {
        ForEach(removed) { r in
          Button { restore(r.slug) } label: {
            HStack(spacing: 5) {
              Icon(.undo2, size: 13)
              Text("\(r.name) · \(tr("恢复", "Restore"))").lineLimit(1)
            }
            .font(.system(size: 13, weight: .medium)).foregroundColor(Palette.text)
            .padding(.vertical, 7).padding(.horizontal, 12)
            .background(Capsule().fill(background))
          }
          .buttonStyle(PressScale())
        }
      }
    }
  }
}
