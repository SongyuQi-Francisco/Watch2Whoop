import SwiftUI

/// A pushed page of someone else's view: a level of their 今天 (the same pages as mine) or one of their records.
enum MemberRoute: Hashable {
  case level(LevelRoute)
  case page(MemberPage)
}

/// Someone else's pages, read-only (2.1.0; app.js viewMember / `#viewing`). The same three levels as my 今天 — the first
/// glance (dial, two readings, their faces), 今日详情, a metric page — drawn by the same views from their documents in
/// memory (`MemberViewStore`, `MemberDayStore`), plus their food, training, health record and weekly report as pages.
/// Opened from a member card or 「我 → 共享给我的」 over the whole app, so neither the tab bar nor Jarvis is reachable behind
/// it. The bar on top says whose data this is on every level and is the way out; closing drops everything that was fetched.
/// Nothing can be written from here: no Jarvis, no sync, no ＋. While the app is not in front the content is covered, so the
/// system's app-switcher picture holds nothing of theirs.
struct MemberScreen: View {
  let exit: () -> Void
  @EnvironmentObject private var session: AccountSession
  @Environment(\.scenePhase) private var scene
  @StateObject private var store: MemberViewStore
  @StateObject private var day: MemberDayStore
  @State private var path: [MemberRoute] = []
  @Namespace private var zoomSpace
  @StateObject private var zoom = ZoomTrail()
  @State private var sheet: TodaySheet?
  @State private var trainingDay: MemberDayRef?
  @StateObject private var inert = TodayStore()   // the sheets' parameter; without a session it reads and writes nothing
  /// Debug screenshots: the levels / pages to open (`W2W_DEV_MEMBER=<id>:<path>`).
  private let devPath: String?

  init(person: ViewablePerson, client: APIClient, devPath: String? = nil, exit: @escaping () -> Void) {
    let s = MemberViewStore(person: person, client: client)
    _store = StateObject(wrappedValue: s)
    _day = StateObject(wrappedValue: MemberDayStore(member: s))
    self.devPath = devPath
    self.exit = exit
  }

  var body: some View {
    // not a safeAreaInset: outside a NavigationStack it does not move the stack's root, which would lie under the bar
    VStack(spacing: 0) {
      MemberBar(person: store.person, exit: exit)
      NavigationStack(path: $path) {
        root
          .toolbar(.hidden, for: .navigationBar)
          .navigationDestination(for: MemberRoute.self) { r in destination(r).zoomLevel(r) }
      }
      .zoomScope(zoomSpace, zoom)
      .sheet(item: $trainingDay) { ref in trainingSheet(ref) }
    }
    .background(Palette.bg.ignoresSafeArea())
    .environment(\.memberView, store)
    .sheet(item: $sheet) { s in sheetView(s) }
    .overlay { if scene != .active { Palette.bg.ignoresSafeArea() } }
    .task {
      await store.start()
      await day.load()
      devOpen()
    }
    .onDisappear { store.close() }
  }

  // MARK: - level 1: the first glance

  @ViewBuilder private var root: some View {
    switch store.phase {
    case .loading:
      ProgressView().tint(Palette.text3).frame(maxWidth: .infinity, maxHeight: .infinity).background(Palette.bg.ignoresSafeArea())
    case .ready:
      glance
    case .notShared:
      message(tr("未共享", "Not shared"), retry: false)
    case .offline:
      message(tr("需要联网", "Needs a connection"), retry: true)
    case .failed:
      message(tr("出错了", "Something went wrong"), retry: true)
    }
  }

  private var glance: some View {
    ScrollView(.vertical, showsIndicators: false) {
      VStack(spacing: 0) {
        TodayGlancePage(model: day.model, isPast: day.model.isPast, circlesOn: false, day: day.date, session: session, run: glanceActions,
                        member: .init(scores: store.scores, detail: store.hasDay),
                        memberFaces: AnyView(MemberGlanceFaces(model: day.model, day: day, pages: store.pages, run: glanceActions,
                                                               open: openPage)))
          .equatable()
        // nothing of their day is shared: the doors 今日详情 would have carry on here
        if !store.hasDay { MemberDoors(pages: store.pages.filter(MemberDoors.inDetail.contains), open: openPage).padding(.top, 22) }
      }
      .padding(.horizontal, Metrics.gutter)
      .frame(maxWidth: Metrics.maxContentWidth)
      .frame(maxWidth: .infinity)
    }
    .background(Palette.bg.ignoresSafeArea())
    .refreshable { await day.refresh() }
    .modifier(DaySwipe(step: { day.step($0) }, up: store.hasDay ? { openLevel(.day(anchor: nil), store.scores ? "glance.dial" : nil) } : nil))
  }

  private func message(_ text: String, retry: Bool) -> some View {
    VStack(spacing: 14) {
      Text(text).font(Typo.body).foregroundColor(Palette.text2)
      if retry {
        Button {
          Task {
            await store.start(force: true)
            await day.load()
          }
        } label: {
          Text(tr("重试", "Retry")).font(Typo.bodyStrong).foregroundColor(Palette.text)
            .padding(.horizontal, 18).frame(minHeight: 40)
            .background(Capsule().fill(Palette.surface))
        }
        .buttonStyle(PressScale())
      }
    }
    .frame(maxWidth: .infinity, maxHeight: .infinity)
    .background(Palette.bg.ignoresSafeArea())
  }

  private var glanceActions: TodayGlanceActions {
    TodayGlanceActions(
      level: { r, door in openLevel(r, door) },
      calendar: { sheet = .calendar },
      backToToday: { day.select(nil) },
      step: { day.step($0) },
      // what follows leads to my own data: never drawn on their glance (no capsule, no self-help step, no 圈子 face)
      syncTap: {}, syncCenter: {}, recoveryNext: { _ in },
      food: { openPage(.food, "glance.food") },
      circles: {})
  }

  // MARK: - levels 2 and 3, and the record pages

  @ViewBuilder private func destination(_ r: MemberRoute) -> some View {
    switch r {
    case .level(.day(let anchor)):
      TodayDayPage(anchor: anchor, store: day, run: dayActions,
                   footer: AnyView(MemberDoors(pages: store.pages.filter(MemberDoors.inDetail.contains), open: openPage)))
    case .level(.metric(let id)):
      MetricPage(id: id, end: day.date, range: LevelParams.metricDefaultRange,
                 crumb: .dial(day.model.glance.recovery.value, day.model.glance.recovery.tone), today: day.model,
                 push: { r, door in push(.level(r), door) }, up: up,
                 explain: { key, title in sheet = .explain(kind: "metric", key: key, title: title) },
                 load: { sheet = .load }, member: store)
    case .level(.night(let d)):
      SleepDetailView(initialDate: d) { key, title in sheet = .explain(kind: "metric", key: key, title: title) }
    case .level(.all):
      EmptyView()   // 身体's own level: not part of someone else's view
    case .page(.food):
      MemberFoodPage(store: store)
    case .page(.training):
      MemberTrainingPage(store: store)
    case .page(.health):
      MemberHealthPage(store: store)
    case .page(.report):
      MemberReportPage(store: store)
    }
  }

  private var dayActions: TodayDayActions {
    TodayDayActions(
      push: { r, door in push(.level(r), door) },
      up: { path = [] },
      sheet: { sheet = $0 },
      calendar: { sheet = .calendar },
      backToToday: { day.select(nil) },
      step: { day.step($0) },
      training: { if let d = day.day { trainingDay = MemberDayRef(day: d) } })
  }

  /// A door of the first glance (back then returns to it).
  private func openLevel(_ r: LevelRoute, _ door: String?) {
    guard path.isEmpty else { return }
    push(.level(r), door)
  }

  private func openPage(_ p: MemberPage, _ door: String?) { push(.page(p), door) }

  /// The next page, grown out of `door` (nil: an ordinary push).
  private func push(_ r: MemberRoute, _ door: String?) {
    if let door { zoom.open(door, to: r) }
    path.append(r)
  }

  /// The small dial of a metric page: up one level (LevelNav.up; the record pages never lie under a metric page).
  private func up() {
    let levels = path.compactMap { r -> LevelRoute? in
      if case .level(let l) = r { return l }
      return nil
    }
    path = LevelNav.up(levels, second: .day(anchor: nil)).map(MemberRoute.level)
  }

  // MARK: - sheets (theirs, read-only)

  @ViewBuilder private func sheetView(_ s: TodaySheet) -> some View {
    Group {
      switch s {
      case .calendar:
        SheetContainer(title: tr("选择日期", "Pick a day"), close: { sheet = nil }) {
          MonthPicker(selected: day.date, userToday: store.today) { d in
            day.select(d)
            sheet = nil
          }
        }
        .presentationDetents([.large])
      case .detail(.lab(let key, let title)):
        SheetContainer(title: title, close: { sheet = nil }) { MemberLabTrend(store: store, key: key) }
          .presentationDetents([.large])
      default:
        TodaySheetView(sheet: s, model: day.model, store: inert, open: { sheet = $0 }, close: { sheet = nil })
      }
    }
    .environmentObject(session)
    .environment(\.memberView, store)
  }

  private func trainingSheet(_ ref: MemberDayRef) -> some View {
    SheetContainer(title: tr("训练 · \(RecordDay.short(ref.day))", "Training · \(RecordDay.short(ref.day))"), close: { trainingDay = nil }) {
      TrainingDayBody {
        let r = Endpoint.dayActivity(ref.day, user: store.user)
        await store.load([r])
        return store.json(r)
      }
    }
    .presentationDetents([.medium, .large])
    .environmentObject(session)
    .environment(\.memberView, store)
  }

  /// `SIMCTL_CHILD_W2W_DEV_MEMBER=<user id>:<path>` (Debug, simulator screenshots; MainTabView): `day`, `day:<anchor>`,
  /// `metric:<id>`, `night`, `food`, `training`, `health`, `report`, comma-separated (`day,metric:hrv`); a leading `zoom+`
  /// opens them one after the other through their doors (the zoom transition, for frame grabs).
  /// `SIMCTL_CHILD_W2W_DEV_MEMBER_OPEN=explain|calendar` opens a sheet.
  private func devOpen() {
    #if DEBUG
    if let p = devPath {
      let zoomed = p.hasPrefix("zoom+")
      let steps = (zoomed ? String(p.dropFirst(5)) : p).split(separator: ",").map(String.init)
      let routes: [(MemberRoute, String?)] = steps.compactMap { s in
        if let page = MemberPage(rawValue: s) { return (.page(page), "glance.\(page.rawValue)") }
        guard let l = DevShot.route(s) else { return nil }
        if case .metric(let id) = l { return (.level(l), path.isEmpty ? "glance.reading.\(id)" : "day.row.\(id)") }
        return (.level(l), "glance.dial")
      }
      if zoomed {
        Task { @MainActor in
          for (i, r) in routes.enumerated() {
            try? await Task.sleep(nanoseconds: 2_000_000_000)
            // the first step leaves the glance; a metric after 今日详情 grows out of its row there
            var door = r.1
            if i > 0, case .level(.metric(let id)) = r.0 { door = "day.row.\(id)" }
            push(r.0, door)
          }
        }
      } else {
        path = routes.map(\.0)
      }
    }
    switch RecordDevShot.value("W2W_DEV_MEMBER_OPEN") {
    case "explain": sheet = .explain(kind: "metric", key: "hrv", title: "HRV")
    case "calendar": sheet = .calendar
    default: break
    }
    #endif
  }
}

/// A day of someone else's training (the read-only training day sheet).
struct MemberDayRef: Identifiable, Hashable {
  let day: String
  var id: String { day }
}

/// The bar on top of someone else's view, on every level: whose data this is (avatar, name, the read-only lock) and the
/// way out.
struct MemberBar: View {
  let person: ViewablePerson
  let exit: () -> Void

  var body: some View {
    HStack(spacing: 10) {
      AvatarView(url: person.avatar, name: person.displayName, size: 30)
      VStack(alignment: .leading, spacing: 1) {
        Text(person.shownName).font(Typo.bodyStrong).foregroundColor(Palette.text).lineLimit(1).minimumScaleFactor(0.8)
        if person.remark?.isEmpty == false {
          Text(person.displayName).font(Typo.caption).foregroundColor(Palette.text2).lineLimit(1)
        }
      }
      HStack(spacing: 4) {
        Icon(.lock, size: 12)
        Text(tr("只读", "Read-only")).font(.system(size: 12, weight: .medium))
      }
      .foregroundColor(Palette.text2)
      .padding(.horizontal, 8).frame(height: 24)
      .background(Capsule().fill(Palette.surface2))
      Spacer(minLength: 6)
      Button(action: exit) {
        Icon(.x, size: 18).foregroundColor(Palette.text)
          .frame(width: 34, height: 34).background(Circle().fill(Palette.surface2))
          .frame(width: 44, height: 44).contentShape(Rectangle())
      }
      .buttonStyle(PressScale())
      .accessibilityLabel(tr("退出", "Exit"))
    }
    .padding(.leading, Metrics.gutter).padding(.trailing, Metrics.gutter - 6).padding(.vertical, 4)
    .background(Palette.surface.ignoresSafeArea(edges: .top))
    .overlay(Rectangle().fill(Palette.hair).frame(height: 1), alignment: .bottom)
    .accessibilityElement(children: .contain)
    .accessibilityLabel(tr("正在查看 \(person.shownName) 的数据，只读", "Viewing \(person.shownName)'s data, read-only"))
  }
}

/// Someone else's faces on their first glance (where mine has 体征 / 关注, 饮食, 圈子): slot 1 as mine, then 饮食 and 训练,
/// each only when their share lets that page through. No 圈子: that face is about me.
struct MemberGlanceFaces: View {
  let model: TodayModel
  @ObservedObject var day: MemberDayStore
  let pages: [MemberPage]
  let run: TodayGlanceActions
  let open: (MemberPage, String?) -> Void

  var body: some View {
    HStack(spacing: 8) {
      SlotOneFace(model: model, age: nil, run: run)
      if pages.contains(.food) { food }
      if pages.contains(.training) { training }
    }
  }

  @ViewBuilder private var food: some View {
    if let n = day.nutrition, n.error == nil {
      FoodFace(nutrition: n) { open(.food, "glance.food") }.zoomDoor("glance.food")
    } else {
      // water or recipes only: the door, without a number of the day
      Button { open(.food, "glance.food") } label: { Face(icon: .soup, label: tr("饮食", "Food"), number: "—", dimmed: true) { Color.clear } }
        .buttonStyle(PressScale())
        .zoomDoor("glance.food")
    }
  }

  /// 训练 as my 记录 → 历史 face: the month's sessions, the last trained days' strain.
  private var training: some View {
    let m = day.month
    let strains = (m.map { RecordGlance.trainedDays($0) } ?? []).prefix(7).reversed().map { $0.strain }
    return Button { open(.training, "glance.training") } label: {
      Face(icon: .dumbbell, label: tr("训练", "Training"), number: m?.trainingDays.map(String.init) ?? "—", unit: tr("次", "times")) {
        if !strains.isEmpty { MiniBars(values: Array(strains), tone: .train, maxValue: RecordParams.strainScaleMax, height: 24) } else { Color.clear }
      }
    }
    .buttonStyle(PressScale())
    .zoomDoor("glance.training")
  }
}

/// The doors at the end of someone else's 今日详情 (or of their first glance when nothing of their day is shared): their
/// health record and weekly report, each only when shared.
struct MemberDoors: View {
  /// The pages these doors open (饮食 and 训练 are faces of the first glance).
  static let inDetail: [MemberPage] = [.health, .report]

  let pages: [MemberPage]
  let open: (MemberPage, String?) -> Void

  var body: some View {
    if !pages.isEmpty {
      RowGroup {
        ForEach(Array(pages.enumerated()), id: \.element) { i, p in
          if i > 0 { RowDivider(inset: 0) }
          ListRow(icon: p.icon, title: p.title) { open(p, "doors.\(p.rawValue)") }
            .zoomDoor("doors.\(p.rawValue)")
        }
      }
    }
  }
}

// MARK: - the record pages

/// The day's intake, water and their recipes.
struct MemberFoodPage: View {
  @ObservedObject var store: MemberViewStore
  @EnvironmentObject private var session: AccountSession
  @State private var day: String?
  @State private var nutrition: NutritionDay?
  @State private var water: WaterDay?
  @State private var recipes: [Recipe] = []
  @State private var recipe: Recipe?
  @State private var loading = true

  private var shownDay: String { day ?? store.today ?? "" }
  private var today: String { store.today ?? "" }

  var body: some View {
    RecordPage(title: MemberPage.food.title) {
      VStack(alignment: .leading, spacing: 22) {
        HStack {
          DayStepper(day: shownDay, today: today) { day = min($0, today) }
          Spacer()
        }
        if nutrition != nil || loading {
          FoodIntakeSection(nutrition: nutrition, day: shownDay, isToday: shownDay == today, refreshing: loading)
        }
        if let w = water { FoodWaterCard(water: w, canWrite: false, add: { _ in }, undo: {}) }
        if !recipes.isEmpty {
          VStack(alignment: .leading, spacing: 10) {
            HStack {
              SectionHeader(title: tr("食谱", "Recipes"))
              Text("\(recipes.count)").font(Typo.label).foregroundColor(Palette.text3)
            }
            LazyVGrid(columns: [GridItem(.flexible(), spacing: 10), GridItem(.flexible(), spacing: 10)], spacing: 10) {
              ForEach(recipes) { r in
                Button { recipe = r } label: { RecipeCard(r: r) }.buttonStyle(PressScale())
              }
            }
          }
        }
      }
      .padding(.top, 4)
    }
    .task(id: "\(shownDay)/\(store.refreshes)") { await load() }
    .sheet(item: $recipe) { r in
      SheetContainer(title: r.name, close: { recipe = nil }) { MemberRecipe(store: store, recipe: r) }
        .presentationDetents([.large])
        .presentationDragIndicator(.visible)
        .environmentObject(session)
        .environment(\.memberView, store)
    }
  }

  private func load() async {
    guard !shownDay.isEmpty else { return }
    loading = true
    let t = store.todayRequest(shownDay == today ? nil : shownDay), w = store.waterRequest(shownDay), r = store.request("recipes")
    await store.load([t, w, r])
    loading = false
    var n = NutritionDay.build(today: MemberViewStore.withoutRefused(store.json(t)), day: shownDay, pending: [])
    if n?.error != nil { n?.error = tr("暂时读不到饮食记录", "Food log unavailable") }   // the server's text is for the owner's own page
    nutrition = n
    water = WaterDay.build(store.json(w), day: shownDay, pending: [])
    recipes = (store.list(r) ?? []).compactMap(Recipe.parse)
  }
}

/// A recipe of someone else: its numbers, ingredients and steps; logging is for one's own recipes.
private struct MemberRecipe: View {
  @ObservedObject var store: MemberViewStore
  @State var recipe: Recipe

  var body: some View {
    RecipeDetails(recipe: recipe)
      .task {
        let r = store.request("recipes/\(recipe.id)")
        await store.load([r])
        if let d = store.json(r).flatMap(Recipe.parse) { recipe = d }
      }
  }
}

/// 训练 (muscle map, week, limits, history) and 日记 (what was logged on a day).
struct MemberTrainingPage: View {
  private enum Segment: Hashable { case training, journal }

  @ObservedObject var store: MemberViewStore
  @EnvironmentObject private var session: AccountSession
  @State private var segment: Segment = .training
  @State private var month: String?
  @State private var week: TrainingWeek?
  @State private var calendar: TrainingMonth?
  @State private var muscle: MuscleLoad?
  @State private var rules: [ExerciseRule] = []
  @State private var detail: MemberDayRef?
  @State private var journalDay: String?
  @State private var journal: JournalDay?

  private var today: String { store.today ?? "" }
  private var shownMonth: String { month ?? String(today.prefix(7)) }
  private var shownDay: String { journalDay ?? today }
  private var trainingAllowed: Bool { store.allowed(store.calendarRequest(String(today.prefix(7)))) }
  private var journalAllowed: Bool { store.allowed(store.journalItemsRequest) }
  private var shown: Segment { trainingAllowed && (segment == .training || !journalAllowed) ? .training : .journal }

  var body: some View {
    RecordPage(title: MemberPage.training.title) {
      VStack(alignment: .leading, spacing: 22) {
        if trainingAllowed && journalAllowed {
          SegmentedControl(items: [(Segment.training, tr("训练", "Training")), (.journal, tr("日记", "Journal"))], selection: $segment)
        }
        if shown == .training { training } else { journalSection }
      }
      .padding(.top, 4)
    }
    .task(id: "\(shownMonth)/\(store.refreshes)") { await loadTraining() }
    .task(id: "\(shownDay)/\(store.refreshes)") { await loadJournal() }
    .sheet(item: $detail) { ref in
      SheetContainer(title: tr("训练 · \(RecordDay.short(ref.day))", "Training · \(RecordDay.short(ref.day))"), close: { detail = nil }) {
        TrainingDayBody {
          let r = Endpoint.dayActivity(ref.day, user: store.user)
          await store.load([r])
          return store.json(r)
        }
      }
      .presentationDetents([.medium, .large])
      .environmentObject(session)
      .environment(\.memberView, store)
    }
  }

  @ViewBuilder private var training: some View {
    if let m = muscle { MuscleLoadCard(load: m) }
    if let w = week { TrainingWeekCard(week: w, today: today, openDay: { d in if d <= today { detail = MemberDayRef(day: d) } }) }
    ExerciseLimitsCard(rules: ExerciseRule.merged(rules, week: week))
    TrainingMonthCard(month: shownMonth, calendar: calendar, today: today,
                      showMonth: { m in if m <= String(today.prefix(7)) { month = m } }, openDay: { detail = MemberDayRef(day: $0) })
  }

  @ViewBuilder private var journalSection: some View {
    HStack {
      DayStepper(day: shownDay, today: today) { journalDay = min($0, today) }
      Spacer()
    }
    if let j = journal, !j.logged.isEmpty {
      TagCloud(items: j.logged, journal: j, tap: { _ in }).allowsHitTesting(false)
    } else {
      Card { EmptyNote(text: tr("这天没有记录", "Nothing logged that day")) }
    }
  }

  private func loadTraining() async {
    guard !today.isEmpty else { return }
    let monday = RecordDay.monday(today), lastDay = RecordDay.add(monday, 6)
    let months = Array(Set([shownMonth, String(monday.prefix(7)), String(lastDay.prefix(7))]))
    let w = store.request("training/week", ["date": today]), m = store.request("training/muscle-load"), r = store.request("exercises")
    await store.load([w, m, r] + months.map(store.calendarRequest))
    let days = Array(Set([String(monday.prefix(7)), String(lastDay.prefix(7))])).flatMap { (store.json(store.calendarRequest($0))?["days"] as? [[String: Any]]) ?? [] }
    week = TrainingWeek.build(store.json(w), calendar: days, monday: monday)
    calendar = TrainingMonth.build(store.json(store.calendarRequest(shownMonth)), month: shownMonth)
    muscle = MuscleLoad.parse(store.json(m))
    rules = ExerciseRule.parseList(store.list(r))
  }

  private func loadJournal() async {
    guard !today.isEmpty else { return }
    let f = store.request("journal/frequent"), e = store.request("journal", ["start": shownDay, "end": shownDay])
    await store.load([store.journalItemsRequest, f, e])
    journal = JournalDay.build(items: store.list(store.journalItemsRequest), frequent: store.list(f), entries: store.list(e), day: shownDay, pending: [])
  }
}

/// 补剂 / 化验 / 身体事件: each block only when the server lets it through.
struct MemberHealthPage: View {
  @ObservedObject var store: MemberViewStore
  @EnvironmentObject private var session: AccountSession
  @State private var lab: LabSummary?

  private func checkIns(_ today: String) -> APIRequest { store.request("journal", ["start": today, "end": today]) }

  var body: some View {
    let today = store.today ?? ""
    RecordPage(title: MemberPage.health.title) {
      VStack(alignment: .leading, spacing: 22) {
        if let list = store.list(store.supplementsRequest) {
          section(tr("补剂", "Supplements")) {
            SupplementsList(supplements: Supplement.build(list, journal: store.list(checkIns(today)), today: today, pending: []))
          }
        }
        if let list = store.list(store.labsRequest) {
          section(tr("化验", "Lab results")) {
            LabsList(labs: LabSummary.parseList(list), emptyText: tr("暂无", "None")) { lab = $0 }
          }
        }
        if let list = store.list(store.eventsRequest(today)) {
          section(tr("身体事件", "Body events")) { EventsList(events: BodyEvent.parseList(list)) }
        }
      }
      .padding(.top, 4)
    }
    .task(id: "\(today)/\(store.refreshes)") { await store.load([checkIns(today)]) }   // the day's supplement check-ins, when shared
    .sheet(item: $lab) { l in
      SheetContainer(title: l.name, close: { lab = nil }) { MemberLabTrend(store: store, key: l.key) }
        .presentationDetents([.large])
        .environmentObject(session)
        .environment(\.memberView, store)
    }
  }

  private func section<C: View>(_ title: String, @ViewBuilder _ content: () -> C) -> some View {
    VStack(alignment: .leading, spacing: 10) {
      SectionHeader(title: title)
      content()
    }
  }
}

/// One lab value of someone else over time (their health record, or a 身体状态 card with a lab value in 今日详情).
struct MemberLabTrend: View {
  @ObservedObject var store: MemberViewStore
  let key: String

  var body: some View {
    LabTrendView {
      let r = store.request("labs/\(key.addingPercentEncoding(withAllowedCharacters: .urlPathAllowed) ?? key)/history")
      await store.load([r])
      return LabHistory.parse(store.json(r))
    }
  }
}

/// Their week / month report (the same view as 身体 → 报告), from their documents.
struct MemberReportPage: View {
  @ObservedObject var store: MemberViewStore
  @EnvironmentObject private var session: AccountSession
  @State private var explain: TrendsSheet?
  @StateObject private var inert = TrendsStore()   // the sheet's parameter; without a session it reads and writes nothing

  var body: some View {
    RecordPage(title: MemberPage.report.title) {
      ReportView(end: nil) { key, title in explain = .explain(key: key, title: title) }.padding(.top, 4).id(store.refreshes)
    }
    .sheet(item: $explain) { s in
      TrendsSheetView(sheet: s, page: nil, end: nil, store: inert, close: { explain = nil })
        .environmentObject(session)
        .environment(\.memberView, store)
    }
  }
}
