import SwiftUI

/// What the AI-first Today asks of TodayView (its sheets, its navigation, its tab).
struct TodayHomeActions {
  var action: (TodayModel.Action) -> Void
  var open: (TodayOpen) -> Void
  var sheet: (TodaySheet) -> Void
  var go: (TodayHomeDest) -> Void
  var refresh: () -> Void
  var allMetrics: () -> Void
  var circles: () -> Void
  /// The day's training sheet (the same one as the classic page's training card).
  var training: () -> Void
  /// The button under an empty recovery ring.
  var recoveryNext: (RecoveryNext.Action) -> Void
  var syncCenter: () -> Void
  var syncNow: () -> Void
}

/// Asking the user's AI from the page: tapped and typed questions go out at once where the AI runs in the app.
@MainActor
enum TodayHomeAI {
  static func ask(_ question: String) { JarvisRouter.current?.send(prompt: question) }

  static var canPhoto: Bool {
    guard let ai = JarvisRouter.current?.ai else { return false }
    return ai.inApp && ai.vision
  }

  static func run(_ q: TodayHomeQuestion) {
    if q.photo { JarvisRouter.current?.run(.photo) } else { ask(q.text) }
  }
}

/// The page below the header, flag `ui_ai_first` on (web: ui-ai-first.js `today`). Same look and the same cards as the classic
/// page, in the new structure: three rings → the search field that also asks Jarvis, with questions made from the user's numbers
/// → 重点 → the circles card → my modules (each says why it is there) → 全部功能. Long press, or ⋯ on a module, has a menu whose
/// first row asks Jarvis about that card. Its body is a flat list of rows, so TodayView's LazyVStack stays lazy.
struct TodayHomePage: View {
  let model: TodayModel
  let home: TodayHome
  let flags: Set<String>
  let circlesTab: Bool
  @ObservedObject var store: TodayHomeStore
  @ObservedObject var sync: SyncCoordinator
  let hasData: Bool
  /// The food card of the classic page: 训练与饮食 on the web page is training and food together.
  let foodCard: AnyView
  let run: TodayHomeActions

  @State private var canPhoto = false

  private var m: TodayModel { model }

  var body: some View {
    rings.padding(.top, 10)
    if let w = m.word {
      Text(w).font(Typo.heroWord).foregroundColor(m.wordMuted ? Palette.text2 : Palette.text)
        .frame(maxWidth: .infinity).padding(.top, 12)
    }
    if let s = m.sub {
      Text(s).font(Typo.caption).foregroundColor(Palette.text2).frame(maxWidth: .infinity).padding(.top, 3)
    }
    if let n = m.recoveryNext {
      Button { run.recoveryNext(n.action) } label: { Chip(label: n.label) }
        .buttonStyle(PressScale())
        .frame(maxWidth: .infinity).padding(.top, 10)
    }
    if m.loadChip != nil || m.local {
      HStack(spacing: 8) {
        if let l = m.loadChip {
          Button { run.sheet(.load) } label: { Chip(label: "\(tr("负荷", "Load")) \(l)") }.buttonStyle(PressScale())
        }
        if m.local { Chip(label: tr("本机计算", "On this phone")) }
      }
      .frame(maxWidth: .infinity).padding(.top, 14)
    }
    if let e = m.error { ToneBadge(text: e).frame(maxWidth: .infinity).padding(.top, 10) }

    if !m.isPast { askBlock.padding(.top, 14) }

    focusBlock

    if m.circles != nil {
      CircleSummaryCard { run.circles() }
        .contextMenu {
          TodayHomeMenu(question: TodayHomeAsk.question(card: "circles", label: tr("圈子", "Circles"), value: nil),
                        details: run.circles)
        }
        .padding(.top, 22)
    }

    // One row of TodayView's LazyVStack, not one per card: with the cards as separate rows of a ForEach the stack's placement
    // never settled after the first swipe (main thread at 100 % in LazySubviewPlacements.updateValue, the page frozen).
    let cards = modules
    if !cards.isEmpty {
      VStack(alignment: .leading, spacing: 0) {
        ForEach(cards) { module($0).padding(.top, 22) }
      }
    }

    if let s = m.sync { SyncCardView(card: s, sync: sync, open: run.syncCenter, syncNow: run.syncNow).padding(.top, 22) }

    if m.date != nil {
      Button(action: run.allMetrics) {
        HStack(spacing: 4) {
          Text(tr("全部指标", "All metrics")).font(Typo.bodyStrong)
          Icon(.chevronRight, size: 14)
        }
        .foregroundColor(Palette.text2)
        .frame(maxWidth: .infinity, alignment: .trailing)
        .padding(.vertical, 6)
        .contentShape(Rectangle())
      }
      .buttonStyle(PressScale())
      .padding(.top, 12)
    }

    RowGroup {
      ListRow(title: tr("全部功能", "All features"), value: tr("随时能看", "Always available")) { run.go(.allFeatures) }
    }
    .padding(.top, 12)
    .id("allFeatures")

    Button(action: run.syncCenter) { SyncStatusLine(sync: sync, hasData: hasData) }
      .buttonStyle(.plain)
      .padding(.top, 18)
  }

  // MARK: - rings

  /// The classic page's sizes: recovery in the middle, 1.4× the others.
  private var rings: some View {
    let width = min(UIScreen.main.bounds.width, Metrics.maxContentWidth) - Metrics.gutter * 2
    let side = min(100, (max(width, 200) - 24) / 3.7), big = side * 1.4
    return HStack(alignment: .center, spacing: 12) {
      ForEach(TodayHome.rings(m), id: \.key) { r in
        let isBig = r.key == "recovery"
        let open = { run.action(r.key == "sleep" ? .sleepDetail(m.sleepDate) : r.action) }
        Button(action: open) {
          RingView(value: r.value, label: r.label, fraction: r.fraction, tone: r.tone, side: isBig ? big : side, big: isBig)
        }
        .buttonStyle(PressScale())
        .contextMenu {
          TodayHomeMenu(question: TodayHomeAsk.question(card: r.key, label: r.label, value: r.value), details: open)
        }
      }
    }
    .frame(maxWidth: .infinity)
    .onAppear { if hasData { PerfProbe.shared.ringsShown() } }
    .onChange(of: hasData) { if $0 { PerfProbe.shared.ringsShown() } }
  }

  // MARK: - the search field and the questions

  private var askBlock: some View {
    let qs = TodayHomeAsk.today(m, photo: canPhoto)
    let example = qs.first(where: \.isExample)?.label ?? TodayHomeAsk.defaultExample
    return VStack(alignment: .leading, spacing: 10) {
      TodayHomeAskBar(placeholder: tr("搜索，或问 Jarvis：\(example)", "Search, or ask Jarvis: \(example)"), circles: circlesTab, go: run.go)
      FlowLayout(spacing: 6) {
        ForEach(qs) { q in
          Button { TodayHomeAI.run(q) } label: {
            HStack(spacing: 4) {
              if !q.photo { Icon(.sparkles, size: 14).foregroundColor(Palette.text2) }
              Text(q.label).font(.system(size: 13, weight: .medium)).foregroundColor(Palette.text)
            }
            .padding(.vertical, 7).padding(.horizontal, 12)
            .background(Capsule().fill(Palette.surface))
          }
          .buttonStyle(PressScale())
        }
      }
    }
    // the router exists once the tab bar's Jarvis layer has appeared, and knows its AI a moment later
    .task {
      for _ in 0..<4 {
        canPhoto = TodayHomeAI.canPhoto
        if canPhoto { break }
        try? await Task.sleep(nanoseconds: 1_000_000_000)
      }
    }
  }

  // MARK: - 重点

  @ViewBuilder private var focusBlock: some View {
    let items = TodayHome.focusItems(m)
    if !items.attention.isEmpty || !items.reminders.isEmpty {
      VStack(alignment: .leading, spacing: 10) {
        SectionTitle(title: tr("重点", "Today's focus"),
                     trailing: m.reminders.count > items.reminders.count ? tr("全部 \(m.reminders.count)", "All \(m.reminders.count)") : nil) {
          run.sheet(.reminders)
        }
        if !items.attention.isEmpty {
          AttentionList(items: items.attention) { run.open($0.open) }
            .contextMenu { TodayHomeMenu(question: TodayHomeAsk.question(card: "reminders", label: "", value: nil)) }
        }
        ForEach(items.reminders) { r in
          ReminderRow(reminder: r) { run.sheet(.reminder(r.id)) }
            .contextMenu {
              TodayHomeMenu(question: TodayHomeAsk.question(card: "reminders", label: "", value: nil),
                            details: { run.sheet(.reminder(r.id)) })
            }
        }
      }
      .padding(.top, 22)
      .id("focus")
    }
  }

  // MARK: - my modules

  private var modules: [TodayHome.Module] {
    home.shown { TodayHome.has($0, model: m, flags: flags) }
  }

  private func module(_ mod: TodayHome.Module) -> some View {
    let name = TodayHome.moduleName(mod.key)
    let question = TodayHomeAsk.question(card: mod.key, label: name, value: nil)
    let details = self.details(mod.key)
    // only what the document lists can be taken off (no document yet: nothing to change)
    let remove: (() -> Void)? = store.home == nil ? nil : { store.setModule(mod.key, state: "hidden") }
    return VStack(alignment: .leading, spacing: TodayHome.cardHasHeading(mod.key, model: m) ? 2 : 10) {
      HStack(spacing: 6) {
        // the training and muscle cards bring their own heading (TodayTrainingCards.swift)
        if !TodayHome.cardHasHeading(mod.key, model: m) {
          Text(name).font(Typo.section).foregroundColor(Palette.text2).lineLimit(1)
        }
        if let r = mod.reason { ToneBadge(text: r.label) }
        Spacer(minLength: 4)
        Menu {
          TodayHomeMenu(question: question, details: details, remove: remove)
        } label: {
          Icon(.ellipsis, size: 18).foregroundColor(Palette.text3).frame(width: 32, height: 24).contentShape(Rectangle())
        }
        .accessibilityLabel(tr("更多", "More"))
      }
      moduleContent(mod.key)
        .contextMenu { TodayHomeMenu(question: question, details: details, remove: remove) }
    }
    .id(mod.key)
  }

  private func details(_ key: String) -> () -> Void {
    switch key {
    case "health_monitor": return { run.sheet(.monitor) }
    case "strain_coach": return { run.sheet(.coach) }
    case "sleep": return { run.action(.sleepDetail(m.sleepDate)) }
    case "sleep_goal": return { run.go(.bedtimeGoal) }
    case "journal": return { run.go(.record(.journal)) }
    case "training" where m.training != nil: return run.training
    default: return { run.go(.record(.training)) }
    }
  }

  @ViewBuilder private func moduleContent(_ key: String) -> some View {
    switch key {
    case "health_monitor":
      MonitorCard(vitals: m.vitals, off: m.vitalsOff, showTitle: false) { run.sheet(.monitor) }
    case "strain_coach":
      if let c = TodayHome.coachCard(m) { CoreCardView(card: c) { run.sheet(.coach) } }
    case "sleep":
      tiles("sleep")
    case "sleep_goal":
      VStack(alignment: .leading, spacing: 8) {
        if let c = m.tonight { SleepCoachCard(coach: c, isPast: m.isPast) { run.sheet(.detail(.sleepCoach)) } }
        NavigationLink { BedtimeGoalPage() } label: { BedtimeGoalRow() }.buttonStyle(PressScale())
      }
    case "training":
      VStack(alignment: .leading, spacing: 0) {
        if let t = m.training {
          TodayTrainingCard(training: t, open: run.training, history: { run.go(.record(.training)) })
        } else {
          link(.dumbbell, tr("训练计划和记录", "Training plan and log")) { run.go(.record(.training)) }
        }
        foodCard
      }
    case "muscle_load":
      if let mm = m.muscle {
        TodayMuscleCard(muscle: mm, open: { run.go(.record(.training)) })
      } else {
        link(.flame, tr("肌肉负荷图", "Muscle load map")) { run.go(.record(.training)) }
      }
    case "journal":
      link(.fileText, tr("今天的日记", "Today's journal")) { run.go(.record(.journal)) }
    default:
      EmptyView()
    }
  }

  @ViewBuilder private func tiles(_ section: String) -> some View {
    if let s = m.sections.first(where: { $0.id == section }), s.metrics.contains(where: { $0.value != nil }) {
      HStack(spacing: 8) { ForEach(s.metrics) { TapTile(metric: $0, action: run.action) } }
    }
  }

  private func link(_ icon: Icon.Name, _ title: String, _ action: @escaping () -> Void) -> some View {
    RowGroup { ListRow(icon: icon, title: title, action: action) }
  }
}

/// The rows of a card's menu (long press, or ⋯). The first asks Jarvis about exactly that card.
struct TodayHomeMenu: View {
  let question: String
  var details: (() -> Void)?
  var remove: (() -> Void)?

  var body: some View {
    Button { TodayHomeAI.ask(question) } label: {
      Label {
        Text(tr("问 Jarvis：\(question)", "Ask Jarvis: \(question)"))
      } icon: {
        Image(Icon.Name.sparkles.assetName).renderingMode(.template)
      }
    }
    if let details { Button(tr("看详情", "Details"), action: details) }
    if let remove { Button(tr("从首页移走", "Take off my Today"), action: remove) }
  }
}

/// The search field that also asks Jarvis: typing lists the matching features under it; Enter opens the exact or only match,
/// anything else goes to Jarvis (web: ui-ai-first.js askBar / bindAsk).
struct TodayHomeAskBar: View {
  let placeholder: String
  let circles: Bool
  let go: (TodayHomeDest) -> Void
  @State private var text = ""
  @FocusState private var focused: Bool

  var body: some View {
    VStack(alignment: .leading, spacing: 8) {
      HStack(spacing: 8) {
        Icon(.sparkles, size: 16).foregroundColor(Palette.text2)
        TextField(placeholder, text: $text)
          .font(Typo.body)
          .focused($focused)
          .submitLabel(.send)
          .onSubmit(submit)
      }
      .padding(.horizontal, 14).frame(minHeight: 44)
      .background(RoundedRectangle(cornerRadius: 12, style: .continuous).fill(Palette.surface))
      let hits = TodayHomeFeatures.matches(text, circles: circles)
      if !hits.isEmpty {
        RowGroup {
          ForEach(Array(hits.enumerated()), id: \.element.id) { i, f in
            if i > 0 { RowDivider(inset: 0) }
            ListRow(title: f.name, value: f.area) { open(f) }
          }
        }
      }
    }
  }

  private func open(_ f: TodayHomeFeature) {
    focused = false
    text = ""
    go(f.dest)
  }

  private func submit() {
    let q = text.trimmingCharacters(in: .whitespacesAndNewlines)
    guard !q.isEmpty else { return }
    if let f = TodayHomeFeatures.direct(q, circles: circles) { open(f); return }
    focused = false
    text = ""
    TodayHomeAI.ask(q)
  }
}

// MARK: - sheets of the page

enum TodayHomeSheet: Identifiable, Equatable {
  /// The first screen: the user's own data, then what they care about.
  case overview(TodayHome.Overview)
  case focus, allFeatures, bodyStatus, bedtimeGoal

  var id: String {
    switch self {
    case .overview: return "overview"
    case .focus: return "focus"
    case .allFeatures: return "allFeatures"
    case .bodyStatus: return "bodyStatus"
    case .bedtimeGoal: return "bedtimeGoal"
    }
  }
}

struct TodayHomeSheetView: View {
  let sheet: TodayHomeSheet
  let model: TodayModel
  let flags: Set<String>
  let circles: Bool
  @ObservedObject var store: TodayHomeStore
  let close: () -> Void
  /// Closes the sheet, then goes there.
  let go: (TodayHomeDest) -> Void

  private var home: TodayHome { store.home ?? .fallback }

  var body: some View {
    SheetContainer(title: title, close: close) { content }
      .presentationDetents(detents)
      .presentationDragIndicator(.visible)
  }

  private var title: String {
    switch sheet {
    case .overview: return tr("你的身体概况", "Your body at a glance")
    case .focus: return tr("关注点", "Focus")
    case .allFeatures: return tr("全部功能", "All features")
    case .bodyStatus: return tr("身体状态", "Body status")
    case .bedtimeGoal: return tr("入睡目标", "Bedtime goal")
    }
  }

  private var detents: Set<PresentationDetent> {
    switch sheet {
    case .focus: return [.medium]
    // the first screen's buttons must be in sight without a drag
    case .overview, .allFeatures, .bedtimeGoal: return [.large]
    default: return [.medium, .large]
    }
  }

  @ViewBuilder private var content: some View {
    switch sheet {
    case .overview(let o):
      TodayHomeFocusPicker(overview: o, veteran: home.pace?.veteran == true, home: home, store: store, close: close, go: go)
    case .focus:
      TodayHomeFocusPicker(overview: nil, veteran: false, home: home, store: store, close: close, go: go)
    case .allFeatures:
      TodayHomeAllFeatures(home: home, model: model, flags: flags, circles: circles, documented: store.home != nil, store: store, go: go)
    case .bodyStatus:
      statusGrid
    case .bedtimeGoal:
      BedtimeGoalView()
    }
  }

  /// The status cards the classic Today shows (me.status); a tap opens the same sheets.
  @ViewBuilder private var statusGrid: some View {
    VStack(alignment: .leading, spacing: 10) {
      ForEach(model.status) { g in
        if model.status.count > 1, !g.name.isEmpty {
          Text(g.name).font(Typo.label).foregroundColor(Palette.text3).padding(.top, 2)
        }
        LazyVGrid(columns: [GridItem(.flexible(), spacing: 8), GridItem(.flexible(), spacing: 8)], spacing: 8) {
          ForEach(g.items) { it in
            StatusCell(item: it) {
              if it.key == "weight" { go(.sheet(.weight)) }
              else if let lab = it.labKey { go(.sheet(.detail(.lab(key: lab, title: it.label)))) }
              else { go(.sheet(.explain(kind: "status", key: it.key, title: it.label))) }
            }
          }
        }
      }
    }
  }
}

/// The first screen (web: ui-ai-first.js focusSheet): what the history covers, up to three findings, then the focus as quick-reply
/// chips. Without an overview only the chips: the same sheet to change them later.
struct TodayHomeFocusPicker: View {
  let overview: TodayHome.Overview?
  let veteran: Bool
  let home: TodayHome
  @ObservedObject var store: TodayHomeStore
  let close: () -> Void
  let go: (TodayHomeDest) -> Void
  @State private var picked: Set<String>

  init(overview: TodayHome.Overview?, veteran: Bool, home: TodayHome, store: TodayHomeStore, close: @escaping () -> Void,
       go: @escaping (TodayHomeDest) -> Void) {
    self.overview = overview
    self.veteran = veteran
    self.home = home
    self.store = store
    self.close = close
    self.go = go
    _picked = State(initialValue: Set(home.focus))
  }

  var body: some View {
    VStack(alignment: .leading, spacing: 8) {
      if let o = overview {
        if o.days > 0 {
          Text(tr("已读入 \(o.days) 天的数据 · \(o.nights) 晚睡眠 · \(o.workouts) 次训练", "\(o.days) days of data · \(o.nights) nights of sleep · \(o.workouts) workouts"))
            .font(Typo.caption).foregroundColor(Palette.text2)
        } else {
          Text(tr("先戴一晚，明早给你第一份报告", "Wear your watch tonight: your first report is tomorrow morning"))
            .font(Typo.caption).foregroundColor(Palette.text2)
        }
        ForEach(o.findings) { finding($0) }
        HStack {
          SectionHeader(title: tr("你最关心哪些？", "What matters most to you?"))
          Text(tr("可多选", "Pick any")).font(Typo.label).foregroundColor(Palette.text3).fixedSize()
        }
        .padding(.top, 14)
      }
      FlowLayout(spacing: 8) {
        ForEach(home.focusOptions, id: \.self) { k in
          let on = picked.contains(k)
          Button { if on { picked.remove(k) } else { picked.insert(k) } } label: {
            Text(TodayHome.focusName(k)).font(.system(size: 14, weight: .medium))
              .foregroundColor(on ? Palette.bg : Palette.text)
              .padding(.vertical, 8).padding(.horizontal, 12)
              .background(Capsule().fill(on ? Palette.text : Palette.inSheet))
          }
          .buttonStyle(PressScale())
          .accessibilityAddTraits(on ? .isSelected : [])
        }
      }
      HStack(spacing: 10) {
        PrimaryButton(title: tr("好了", "Done")) {
          store.setFocus(home.focusOptions.filter(picked.contains))
          close()
        }
        // design D8: an existing user is told where everything else is; a new one may skip
        if overview != nil {
          if veteran {
            SecondaryButton(title: tr("看全部功能", "All features"), background: Palette.inSheet) { go(.allFeatures) }
          } else {
            SecondaryButton(title: tr("先跳过", "Skip for now"), background: Palette.inSheet, action: close)
          }
        }
      }
      .padding(.top, 14)
    }
  }

  private func finding(_ f: TodayHome.Finding) -> some View {
    VStack(alignment: .leading, spacing: 4) {
      HStack {
        Text(f.area).font(Typo.label).foregroundColor(Palette.text2)
        Spacer(minLength: 6)
        ToneBadge(text: f.value, tone: f.tone)
      }
      Text(f.label).font(Typo.caption).foregroundColor(Palette.text2).fixedSize(horizontal: false, vertical: true)
    }
    .padding(14)
    .frame(maxWidth: .infinity, alignment: .leading)
    .background(RoundedRectangle(cornerRadius: Metrics.cardRadius, style: .continuous).fill(Palette.inSheet))
  }
}

/// 全部功能 (web: ui-ai-first.js allPage): the search field, my Today (the focus, what is on it, what could be), every feature by
/// area. Nothing is locked.
struct TodayHomeAllFeatures: View {
  let home: TodayHome
  let model: TodayModel
  let flags: Set<String>
  let circles: Bool
  /// The account's document is here (no document yet: the cards cannot be moved).
  let documented: Bool
  @ObservedObject var store: TodayHomeStore
  let go: (TodayHomeDest) -> Void

  var body: some View {
    let shown = home.shown { TodayHome.has($0, model: model, flags: flags) }
    let off = home.notShown(shown)
    VStack(alignment: .leading, spacing: 18) {
      TodayHomeAskBar(placeholder: tr("搜索，或问 Jarvis：怎么记喝水？", "Search, or ask Jarvis: How do I log water?"), circles: circles, go: go)
      if documented {
        RowGroup(title: "\(tr("我的首页", "My Today")) · \(tr("最多 \(home.maxModules) 块", "Up to \(home.maxModules)"))") {
          ListRow(title: tr("关注点", "Focus"),
                  value: home.focus.isEmpty ? tr("未选", "None") : home.focus.map(TodayHome.focusName).joined(separator: " · ")) { go(.focus) }
          ForEach(shown) { m in
            RowDivider(inset: 0)
            moduleRow(m.key, action: tr("移走", "Take off")) { store.setModule(m.key, state: "hidden") }
          }
          ForEach(off, id: \.self) { k in
            RowDivider(inset: 0)
            moduleRow(k, action: tr("放到首页", "Put on my Today")) { store.setModule(k, state: home.hidden.contains(k) ? "auto" : "pinned") }
          }
        }
      }
      ForEach(TodayHomeFeatures.all(circles: circles), id: \.area) { a in
        RowGroup(title: a.area) {
          ForEach(Array(a.items.enumerated()), id: \.element.id) { i, f in
            if i > 0 { RowDivider(inset: 0) }
            ListRow(title: f.name) { go(f.dest) }
          }
        }
      }
    }
  }

  private func moduleRow(_ key: String, action: String, _ run: @escaping () -> Void) -> some View {
    HStack {
      Text(TodayHome.moduleName(key)).font(Typo.body).foregroundColor(Palette.text)
      Spacer(minLength: 8)
      Button(action, action: run).font(Typo.caption).foregroundColor(Palette.text2).buttonStyle(PressScale())
    }
    .padding(.vertical, 12)
  }
}

/// 我 with the flag on: the same search field and the way to 全部功能, which Today shows (web: ui-ai-first.js `after` on 我).
struct TodayHomeMeEntry: View {
  @EnvironmentObject private var session: AccountSession
  @SceneStorage("w2w.tab") private var tab: AppTab = .today

  var body: some View {
    TodayHomeAskBar(placeholder: tr("搜设置和功能，或问 Jarvis", "Search settings and features, or ask Jarvis"),
                    circles: session.circlesTab, go: go)
    RowGroup {
      ListRow(icon: .sparkles, title: tr("全部功能", "All features")) { go(.allFeatures) }
    }
  }

  private func go(_ d: TodayHomeDest) {
    if d == .me { return }
    // Today may not have been built yet on this launch; the link replays once it is, and its `go` may move on to another tab
    TodayHomeLink.shared.pending = d
    tab = .today
  }
}
