import SwiftUI

/// Someone else's pages, read-only (app.js viewMember / `#viewing`): opened from a member card or 「我」, over the whole app, so
/// neither the tab bar nor Jarvis is reachable behind it. The banner stays on top with the way out; closing drops
/// everything that was fetched (`MemberViewStore`). While the app is not in front the content is covered, so the system's
/// app-switcher picture holds nothing of theirs.
struct MemberScreen: View {
  let exit: () -> Void
  @EnvironmentObject private var session: AccountSession
  @Environment(\.scenePhase) private var scene
  @StateObject private var store: MemberViewStore
  @State private var tab: MemberTab = .today
  @State private var path: [SleepRoute] = []
  @State private var explain: TrendsSheet?
  @State private var trendsRange: TrendRange = .today
  @State private var trendsEnd: String?
  @StateObject private var inertTrends = TrendsStore()

  init(person: ViewablePerson, client: APIClient, tab: MemberTab = .today, exit: @escaping () -> Void) {
    _store = StateObject(wrappedValue: MemberViewStore(person: person, client: client))
    _tab = State(initialValue: tab)
    self.exit = exit
  }

  var body: some View {
    // not a safeAreaInset: outside a NavigationStack it does not move the stack's root, which would lie under the banner
    VStack(spacing: 0) {
      banner
      NavigationStack(path: $path) {
        content
          .toolbar(.hidden, for: .navigationBar)
          .navigationDestination(for: SleepRoute.self) { r in
            SleepDetailView(initialDate: r.date) { key, title in explain = .explain(key: key, title: title) }
          }
      }
    }
    .environment(\.memberView, store)
    .sheet(item: $explain) { s in
      TrendsSheetView(sheet: s, page: nil, end: nil, store: inertTrends, close: { explain = nil })
        .environmentObject(session)
        .environment(\.memberView, store)
    }
    .overlay { if scene != .active { Palette.bg.ignoresSafeArea() } }
    .task {
      await store.start()
      devOpen()
    }
    .onDisappear { store.close() }
  }

  /// `SIMCTL_CHILD_W2W_DEV_MEMBER_OPEN=night|explain` (Debug, simulator screenshots): the sleep detail page, or an explain sheet.
  private func devOpen() {
    #if DEBUG
    switch RecordDevShot.value("W2W_DEV_MEMBER_OPEN") {
    case "night": path.append(SleepRoute(date: nil))
    case "explain": explain = .explain(key: "hrv", title: "HRV")
    default: break
    }
    #endif
  }

  // MARK: - banner

  private var banner: some View {
    let name = store.person.shownName
    return HStack(spacing: 10) {
      AvatarView(url: store.person.avatar, name: store.person.displayName, size: 30)
      VStack(alignment: .leading, spacing: 1) {
        Text(tr("正在查看：\(name)（只读）", "Viewing \(name) (read-only)"))
          .font(Typo.bodyStrong).foregroundColor(Palette.text).lineLimit(1).minimumScaleFactor(0.8)
        if store.person.remark?.isEmpty == false {
          Text(store.person.displayName).font(Typo.caption).foregroundColor(Palette.text2).lineLimit(1)
        }
      }
      Spacer(minLength: 6)
      Button(action: exit) {
        Text(tr("退出", "Exit")).font(Typo.bodyStrong).foregroundColor(Palette.bg)
          .padding(.horizontal, 14).frame(minHeight: 36)
          .background(Capsule().fill(Palette.text))
      }
      .buttonStyle(PressScale())
    }
    .padding(.horizontal, Metrics.gutter).padding(.vertical, 8)
    .background(Palette.surface.ignoresSafeArea(edges: .top))
    .overlay(Rectangle().fill(Palette.hair).frame(height: 1), alignment: .bottom)
  }

  // MARK: - pages

  @ViewBuilder private var content: some View {
    switch store.phase {
    case .loading:
      ProgressView().tint(Palette.text3).frame(maxWidth: .infinity, maxHeight: .infinity).background(Palette.bg.ignoresSafeArea())
    case .ready:
      pages
    case .notShared:
      message(tr("未共享", "Not shared"), retry: false)
    case .offline:
      message(tr("需要联网", "Needs a connection"), retry: true)
    case .failed:
      message(tr("出错了", "Something went wrong"), retry: true)
    }
  }

  private func message(_ text: String, retry: Bool) -> some View {
    VStack(spacing: 14) {
      Text(text).font(Typo.body).foregroundColor(Palette.text2)
      if retry {
        Button { Task { await store.start(force: true) } } label: {
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

  private var shown: MemberTab { store.tabs.contains(tab) ? tab : (store.tabs.first ?? .today) }

  private var pages: some View {
    VStack(alignment: .leading, spacing: 0) {
      if store.tabs.count > 1 {
        ScrollView(.horizontal, showsIndicators: false) {
          HStack(spacing: 8) {
            ForEach(store.tabs) { t in PillButton(title: t.title, on: t == shown) { tab = t } }
          }
          .padding(.horizontal, Metrics.gutter).padding(.vertical, 10)
        }
      }
      switch shown {
      case .today:
        MemberTodayPage(store: store, openSleep: { path.append(SleepRoute(date: $0)) }, allMetrics: { day in
          trendsEnd = day
          trendsRange = .today
          tab = .trends
        })
      case .trends:
        MemberTrendsPage(store: store, range: $trendsRange, end: $trendsEnd)
      case .sleep:
        MemberScroll(store: store) {
          TrendsSleepView(end: nil) { day in path.append(SleepRoute(date: day)) }.padding(.top, 4).id(store.refreshes)
        }
      case .food:
        MemberFoodPage(store: store)
      case .record:
        MemberRecordPage(store: store)
      case .health:
        MemberHealthPage(store: store)
      }
    }
    .background(Palette.bg.ignoresSafeArea())
  }
}

/// A page's scroll view, pull to refresh included. Sheets and tasks belong on this, outside the lazy rows: a row scrolled out of
/// the lazy stack could not present anything.
private struct MemberScroll<Content: View>: View {
  @ObservedObject var store: MemberViewStore
  @ViewBuilder var content: () -> Content

  var body: some View {
    ScrollView(.vertical, showsIndicators: false) {
      LazyVStack(alignment: .leading, spacing: 0) { content() }
        .padding(.horizontal, Metrics.gutter)
        .padding(.bottom, 32)
        .frame(maxWidth: Metrics.maxContentWidth)
        .frame(maxWidth: .infinity)
    }
    .refreshable { await store.start(force: true) }
  }
}

/// The date on top of a page: opens the look-back calendar; 「回到今天」 while looking back.
private struct MemberDayHeader: View {
  let overline: String
  let title: String
  let lookingBack: Bool
  let pick: () -> Void
  let back: () -> Void

  var body: some View {
    HStack(alignment: .bottom) {
      Button(action: pick) {
        VStack(alignment: .leading, spacing: 1) {
          HStack(spacing: 4) {
            Text(overline).font(Typo.overline)
            Icon(.calendar, size: 12)
          }
          .foregroundColor(Palette.text2)
          Text(title).font(Typo.title).titleTracking().foregroundColor(Palette.text)
        }
      }
      .buttonStyle(PressScale())
      .accessibilityLabel(tr("选择日期", "Pick a day"))
      Spacer()
      if lookingBack {
        Button(action: back) { Chip(label: tr("回到今天", "Back to today")) }.buttonStyle(PressScale())
      }
    }
    .padding(.top, 12).padding(.bottom, 8)
  }
}

// MARK: - Today

/// The same cards as my Today tab (`TodayDayCards`), from their documents.
struct MemberTodayPage: View {
  @ObservedObject var store: MemberViewStore
  let openSleep: (String?) -> Void
  let allMetrics: (String?) -> Void
  @EnvironmentObject private var session: AccountSession
  @StateObject private var inert = TodayStore()   // the sheets' parameter; without a session it reads and writes nothing
  @State private var day: String?                  // nil = their today
  @State private var model: TodayModel = .empty
  @State private var sheet: TodaySheet?

  var body: some View {
    MemberScroll(store: store) {
      MemberDayHeader(overline: overline, title: model.isPast ? TodayModel.shortDay(model.date) : tr("今天", "Today"), lookingBack: day != nil,
                      pick: { sheet = .calendar }, back: { day = nil })
      TodayDayCards(model: model, handle: handle, open: { sheet = TodaySheet(open: $0) },
                    openStatus: { sheet = .explain(kind: "status", key: $0.key, title: $0.label) },
                    allMetrics: { allMetrics(day) })
    }
    .sheet(item: $sheet) { s in sheetView(s) }
    .task(id: "\(day ?? "")/\(store.today ?? "")/\(store.refreshes)") { await load() }
  }

  private var overline: String {
    guard let d = model.date ?? store.today else { return " " }
    return "\(TodayView.weekday(d)) \(TodayModel.shortDay(d))"
  }

  @ViewBuilder private func sheetView(_ s: TodaySheet) -> some View {
    Group {
      if s == .calendar {
        SheetContainer(title: tr("选择日期", "Pick a day"), close: { sheet = nil }) {
          MonthPicker(selected: day, userToday: store.today) { d in
            day = d == store.today ? nil : d
            sheet = nil
          }
        }
        .presentationDetents([.large])
      } else {
        TodaySheetView(sheet: s, model: model, store: inert, open: { sheet = $0 }, close: { sheet = nil })
      }
    }
    .environmentObject(session)
    .environment(\.memberView, store)
  }

  private func handle(_ a: TodayModel.Action) {
    if case .sleepDetail(let d) = a { openSleep(d ?? model.sleepDate) }
    else if case .weight = a { return }
    else { sheet = TodaySheet(action: a) }
  }

  private func load() async {
    let u = store.user, main = store.todayRequest(day)
    await store.load([main])
    rebuild(main)
    guard let d = day ?? store.today else { return }
    await store.load([Endpoint.dayActivity(d, user: u), Endpoint.strainCoach(d, user: u), Endpoint.healthMonitor(d, user: u),
                      Endpoint.trends(days: TodayParams.trendsDays, end: day, user: u), store.memoryRequest])
    rebuild(main)
  }

  private func rebuild(_ main: APIRequest) {
    let u = store.user, d = day ?? store.today ?? ""
    let m = TodayModel.member(today: store.json(main), activity: store.json(Endpoint.dayActivity(d, user: u)),
                              coach: store.json(Endpoint.strainCoach(d, user: u)), monitor: store.json(Endpoint.healthMonitor(d, user: u)),
                              memory: store.json(store.memoryRequest), trends: store.json(Endpoint.trends(days: TodayParams.trendsDays, end: day, user: u)),
                              theirToday: store.today)
    if m != model { model = m }
  }
}

// MARK: - Trends

/// 指标 and 报告 of the Trends tab, from their documents (the server's copy: nothing is computed here).
struct MemberTrendsPage: View {
  @ObservedObject var store: MemberViewStore
  @Binding var range: TrendRange
  @Binding var end: String?
  @EnvironmentObject private var session: AccountSession
  @StateObject private var inert = TrendsStore()   // the sheets' parameter; without a session it reads and writes nothing
  @State private var mode: TrendsView.Mode = .metrics
  @State private var page: TrendsPage?
  @State private var loading = false
  @State private var offline = false
  @State private var sheet: TrendsSheet?

  private var reportAllowed: Bool { store.state(Endpoint.report("week", end: nil, user: store.user)) != .denied }

  var body: some View {
    MemberScroll(store: store) {
      MemberDayHeader(overline: end.map { d in tr("截至 \(TodayModel.shortDay(d))", "Up to \(TodayModel.shortDay(d))") }
                        ?? store.today.map { "\(TodayView.weekday($0)) \(TodayModel.shortDay($0))" } ?? " ",
                      title: tr("趋势", "Trends"), lookingBack: end != nil, pick: { sheet = .calendar }, back: { end = nil })
      if reportAllowed {
        SegControl(items: [TrendsView.Mode.metrics, .report].map { ($0, $0.label) }, selected: mode) { mode = $0 }
      }
      switch reportAllowed ? mode : .metrics {
      case .report:
        ReportView(end: end) { key, title in sheet = .explain(key: key, title: title) }.padding(.top, 10).id(store.refreshes)
      default:
        TrendsMetricsContent(page: page, range: range, loading: loading, offline: offline, setRange: { range = $0 }, open: { sheet = $0 })
      }
    }
    .sheet(item: $sheet) { s in sheetView(s) }
    .task(id: store.refreshes) { await store.load([Endpoint.report("week", end: nil, user: store.user)]) }
    .task(id: "\(range.rawValue)/\(end ?? "")/\(store.refreshes)") { await load() }
  }

  @ViewBuilder private func sheetView(_ s: TrendsSheet) -> some View {
    Group {
      if case .calendar = s {
        SheetContainer(title: tr("选择结束日期", "Pick the end date"), close: { sheet = nil }) {
          MonthPicker(selected: end, userToday: store.today) { d in
            end = d == store.today ? nil : d
            sheet = nil
          }
        }
        .presentationDetents([.large])
      } else {
        TrendsSheetView(sheet: s, page: page, end: end, store: inert, close: { sheet = nil })
      }
    }
    .environmentObject(session)
    .environment(\.memberView, store)
  }

  private func load() async {
    loading = true
    let u = store.user, days = range.docDays, e = end
    let t = Endpoint.trends(days: days, end: e, user: u), v = Endpoint.vo2max(end: e, user: u), s = Endpoint.sleepDetails(end: e, user: u)
    await store.load([t, v, s])
    loading = false
    offline = store.state(t) == .offline
    page = store.json(t).flatMap(TrendsData.init(json:)).map { TrendsPage(data: $0, local: nil, vo2: store.json(v), sleep: store.json(s)) }
  }
}

// MARK: - Food

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
    MemberScroll(store: store) {
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

// MARK: - Log

/// 训练 (muscle map, week, limits, history) and 日记 (what was logged on a day).
struct MemberRecordPage: View {
  private enum Segment: Hashable { case training, journal }

  @ObservedObject var store: MemberViewStore
  @EnvironmentObject private var session: AccountSession
  @State private var segment: Segment = .training
  @State private var month: String?
  @State private var week: TrainingWeek?
  @State private var calendar: TrainingMonth?
  @State private var muscle: MuscleLoad?
  @State private var rules: [ExerciseRule] = []
  @State private var detail: DayRef?
  @State private var journalDay: String?
  @State private var journal: JournalDay?

  private struct DayRef: Identifiable {
    let day: String
    var id: String { day }
  }

  private var today: String { store.today ?? "" }
  private var shownMonth: String { month ?? String(today.prefix(7)) }
  private var shownDay: String { journalDay ?? today }
  private var trainingAllowed: Bool { store.allowed(store.calendarRequest(String(today.prefix(7)))) }
  private var journalAllowed: Bool { store.allowed(store.journalItemsRequest) }
  private var shown: Segment { trainingAllowed && (segment == .training || !journalAllowed) ? .training : .journal }

  var body: some View {
    MemberScroll(store: store) {
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
        TrainingDayBody(today: today, day: ref.day, plan: week?.days.first { $0.date == ref.day }?.planned ?? []) {
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
    if let w = week { TrainingWeekCard(week: w, today: today) { detail = DayRef(day: $0) } }
    ExerciseLimitsCard(rules: ExerciseRule.merged(rules, week: week))
    TrainingMonthCard(month: shownMonth, calendar: calendar, today: today,
                      showMonth: { m in if m <= String(today.prefix(7)) { month = m } }, openDay: { detail = DayRef(day: $0) })
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

// MARK: - Health record

/// 补剂 / 化验 / 身体事件: each block only when the server lets it through.
struct MemberHealthPage: View {
  @ObservedObject var store: MemberViewStore
  @EnvironmentObject private var session: AccountSession
  @State private var lab: LabSummary?

  private func checkIns(_ today: String) -> APIRequest { store.request("journal", ["start": today, "end": today]) }

  var body: some View {
    let today = store.today ?? ""
    MemberScroll(store: store) {
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
      SheetContainer(title: l.name, close: { lab = nil }) {
        LabTrendView {
          let r = store.request("labs/\(l.key.addingPercentEncoding(withAllowedCharacters: .urlPathAllowed) ?? l.key)/history")
          await store.load([r])
          return LabHistory.parse(store.json(r))
        }
      }
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
