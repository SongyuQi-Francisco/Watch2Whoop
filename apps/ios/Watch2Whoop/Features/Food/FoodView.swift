import SwiftUI

/// 饮食 as its own tab (the `friends` flag off). With the flag on it is a segment of 记录 (`FoodContent` there).
struct FoodTab: View {
  @StateObject private var store: FoodStore
  @State private var toast: Toast?

  init(session: AccountSession) {
    _store = StateObject(wrappedValue: FoodStore(session: session))
  }

  var body: some View {
    NavigationStack {
      PageScaffold(title: tr("饮食", "Food")) {
        FoodContent(store: store, toast: $toast).padding(.top, 4)
      }
      .refreshable { await store.refresh() }
      .toast($toast)
    }
  }
}

/// The food page: 饮食 (the day's intake, water, quick meals, recipes) and 采购 (this week's shopping list). The
/// owner (the 饮食 tab, or 记录 with the `friends` flag) keeps the store and the toast, and pulls to refresh.
struct FoodContent: View {
  @ObservedObject var store: FoodStore
  @Binding var toast: Toast?
  @State private var mode: Mode = .diet
  @State private var entry: FoodEntry?
  @State private var recipe: Recipe?
  /// The meal a suggested recipe opens on.
  @State private var recipeSlot: MealSlot?
  /// 选择: the entries picked in the day's list; nil = not picking.
  @State private var picked: Set<String>?
  @State private var other: OtherSheet?
  @State private var allRecipes = false
  /// A Jarvis card's recipe, until the recipes are read.
  @State private var askedRecipe: String?
  /// 2.0.1 (记录 → 饮食): the dial, readings and faces on top, the day's meals below the fold, 采购 as a page.
  var glance = false
  /// 2.0.1: 日记 (behaviours, numbers, supplements) lives below the fold of 饮食; nil = no row.
  var openJournal: (() -> Void)? = nil
  @State private var l1: FoodL1?

  /// The L1 half sheets of the glance (direction.md 3: face → L1).
  enum FoodL1: String, Identifiable {
    case meals, quick, water
    var id: String { rawValue }
    var height: CGFloat { self == .meals ? 520 : 340 }
  }

  enum Mode: Hashable { case diet, shopping }

  enum OtherSheet: Identifiable {
    case diet
    case combine(FoodCombine.Action, [FoodEntry])
    #if DEBUG
    case dev(String)
    #endif

    var id: String {
      switch self {
      case .diet: return "diet"
      case .combine(let a, _): return "combine.\(a.rawValue)"
      #if DEBUG
      case .dev(let what): return "dev.\(what)"
      #endif
      }
    }
  }

  init(store: FoodStore, toast: Binding<Toast?>, glance: Bool = false, openJournal: (() -> Void)? = nil) {
    self.store = store
    _toast = toast
    self.glance = glance
    self.openJournal = openJournal
  }

  var body: some View {
    content
    .task {
      devShot()
      await store.refresh()
    }
    .onReceive(NotificationCenter.default.publisher(for: JarvisRouter.wroteNotification)) { _ in
      Task { await store.refresh() }
    }
    .onAppear { syncJarvisPage() }
    .onDisappear { leaveJarvisPage() }
    .onChange(of: mode) { _ in syncJarvisPage() }
    .onChange(of: store.day) { _ in picked = nil }
    .sheet(item: $other) { s in otherSheet(s) }
    .navigationDestination(isPresented: $allRecipes) { RecipesView(store: store) { r in recipe = r } }
    .onReceive(JarvisPageLink.shared.$food) { r in
      guard let r else { return }
      JarvisPageLink.shared.food = nil
      show(r)
    }
    .onChange(of: store.recipes.count) { _ in if let id = askedRecipe { openRecipe(id) } }
    .navigationDestination(isPresented: Binding(get: { glance && mode == .shopping }, set: { if !$0 { mode = .diet } })) {
      RecordPage(title: tr("采购", "Shopping")) { GroceryView(store: store, toast: $toast).padding(.top, 12) }
    }
    .sheet(item: $l1) { k in glanceSheet(k).l1Detents(k.height) }
    .sheet(item: $entry) { e in
      SheetContainer(title: e.food, close: { entry = nil }) {
        FoodEntrySheet(entry: e, store: store) { msg in
          entry = nil
          if let msg { toast = Toast(msg) }
        }
      }
      .presentationDetents([.medium, .large])
      .presentationDragIndicator(.visible)
    }
    .sheet(item: $recipe, onDismiss: { recipeSlot = nil }) { r in
      SheetContainer(title: r.name, close: { recipe = nil }) {
        RecipeSheet(recipe: r, slot: recipeSlot, store: store) { t in
          recipe = nil
          toast = t
        }
      }
      .presentationDetents([.large])
      .presentationDragIndicator(.visible)
    }
  }

  /// What a Jarvis card's button asked for.
  private func show(_ r: JarvisPageLink.FoodPage) {
    mode = r == .shopping ? .shopping : .diet
    switch r {
    case .recipes: allRecipes = true
    case .recipe(let id): openRecipe(id)
    case .diet, .shopping: break
    }
  }

  private func openRecipe(_ id: String) {
    if let r = store.recipes.first(where: { $0.id == id }) {
      askedRecipe = nil
      recipe = r
    } else {
      askedRecipe = id
    }
  }

  @ViewBuilder private var content: some View {
    if glance { glanceContent } else { classicContent }
  }

  /// 2.0.1: the first glance, then (below the fold) the day's meals, the meal plan, 采购, 饮食档案 and 日记.
  private var glanceContent: some View {
    VStack(alignment: .leading, spacing: 0) {
      FoodGlanceTop(store: store, meals: { l1 = .meals }, quick: { l1 = .quick }, water: { l1 = .water }, recipes: { allRecipes = true })
      VStack(alignment: .leading, spacing: 22) {
        RecordStatusLine(offline: store.offline, refreshing: store.refreshing && store.nutrition == nil)
        HStack {
          DayStepper(day: store.day, today: store.today) { store.select($0) }
          Spacer()
        }
        FoodIntakeSection(nutrition: store.nutrition, day: store.day, isToday: store.isToday, refreshing: store.refreshing, picked: picked,
                          selectButton: selectButton, pickBar: pickedBar, open: tapEntry)
        if store.isToday, let plan = store.mealPlan, !plan.isEmpty {
          MealPlanSection(plan: plan, retry: { Task { await store.refreshMealPlan() } }) { r, slot in
            recipeSlot = slot
            recipe = r
          }
        }
        RowGroup {
          ListRow(icon: .shoppingCart, title: tr("采购", "Shopping")) { mode = .shopping }
          RowDivider(inset: 0)
          ListRow(icon: .bookOpen, title: tr("食谱", "Recipes"), value: "\(store.recipes.count)") { allRecipes = true }
          RowDivider(inset: 0)
          ListRow(icon: .fileText, title: tr("我的饮食档案", "My diet profile"), value: store.dietBrief?.meta) { other = .diet }
          if let openJournal {
            RowDivider(inset: 0)
            ListRow(icon: .calendarDays, title: tr("日记", "Journal"), action: openJournal)
          }
        }
      }
      .padding(.top, 200)
      .id("n5.meals")
    }
  }

  @ViewBuilder private func glanceSheet(_ k: FoodL1) -> some View {
    switch k {
    case .meals:
      SheetContainer(title: tr("今天吃了", "Eaten today"), close: { l1 = nil }) {
        FoodIntakeSection(nutrition: store.nutrition, day: store.day, isToday: store.isToday, refreshing: store.refreshing,
                          open: { e in l1 = nil; DispatchQueue.main.asyncAfter(deadline: .now() + 0.45) { tapEntry(e) } })
      }
    case .quick:
      SheetContainer(title: tr("快记", "Quick log"), close: { l1 = nil }) {
        QuickMeals(store: store, toast: $toast, cardBackground: Palette.inSheet)
      }
    case .water:
      SheetContainer(title: tr("饮水", "Water"), close: { l1 = nil }) {
        if let w = store.water { FoodWaterCard(water: w, canWrite: store.isToday, add: addWater, undo: undoWater) }
      }
    }
  }

  private var classicContent: some View {
    VStack(alignment: .leading, spacing: 22) {
      SegmentedControl(items: [(Mode.diet, tr("饮食", "Food")), (.shopping, tr("采购", "Shopping"))], selection: $mode)
      RecordStatusLine(offline: store.offline, refreshing: store.refreshing && store.nutrition == nil)
      if let b = store.dietBrief { dietEntry(b) }
      if mode == .diet { diet } else { GroceryView(store: store, toast: $toast) }
    }
  }

  /// Debug screenshots (`W2W_DEV_FOOD=shopping|diet|pick|scan|products|prices|product|history|grams|allmeals|combine`, with
  /// `W2W_DEV_TAB` / `W2W_DEV_RECORD` / `W2W_DEV_SCROLL`).
  private func devShot() {
    #if DEBUG
    switch RecordDevShot.value("W2W_DEV_FOOD") {
    case nil: break
    case "shopping": mode = .shopping
    case "diet": other = .diet
    case "pick": picked = []
    case "scan":
      mode = .shopping
      DispatchQueue.main.asyncAfter(deadline: .now() + 2) { JarvisRouter.current?.devScanShot() }
    case let what?: other = .dev(what)
    }
    #endif
  }

  #if DEBUG
  /// The sheets and sub-pages that otherwise need a tap, for simulator screenshots.
  @ViewBuilder private func devSheet(_ what: String) -> some View {
    switch what {
    case "products": NavigationStack { GroceryProductsView(store: store) }
    case "prices": NavigationStack { StoreProductsView(store: store) }
    case "product":
      if let p = store.products.first(where: { !$0.nutrition.isEmpty }) {
        SheetContainer(title: p.name, close: { other = nil }) { GroceryProductSheet(p: p, staplesOn: true) }
      }
    case "history":
      if let p = store.storeProducts.first {
        SheetContainer(title: p.name, close: { other = nil }) { PriceHistorySheet(product: p, store: store) }
      }
    case "grams":
      if let t = store.templates.first(where: \.needsConfirmation) {
        SheetContainer(title: t.name, close: { other = nil }) { MealTemplateSheet(template: t) { _ in } }
      }
    case "allmeals":
      SheetContainer(title: tr("快捷记录", "Quick log"), close: { other = nil }) {
        AllTemplatesSheet(templates: MealTemplate.byUse(store.templates), tap: { _ in }, edit: { _ in })
      }
    case "combine":
      if let n = store.nutrition {
        SheetContainer(title: tr("合并成一条", "Merge into one"), close: { other = nil }) {
          FoodCombineSheet(action: .merge, picked: n.entries, store: store) { _ in }
        }
      }
    default: EmptyView()
    }
  }
  #endif

  /// Which page Jarvis is asked from (its quick questions, the server's extra tools): food also as a segment of 记录, as on the
  /// page. A tick later, so it comes after the Jarvis layer's own change for the tab.
  private func syncJarvisPage() {
    let page = mode == .shopping ? "food.shopping" : "food"
    DispatchQueue.main.async { JarvisRouter.current?.page = page }
  }

  /// Back to 训练 / 日记 inside 记录 (leaving for another tab, the layer sets that tab's page).
  private func leaveJarvisPage() {
    guard let r = JarvisRouter.current, r.page.hasPrefix("food") else { return }
    r.page = AppTab.record.rawValue
  }

  /// 「我的饮食档案」: one line above both modes.
  private func dietEntry(_ b: DietProfileBrief) -> some View {
    RowGroup {
      Button { other = .diet } label: {
        HStack(spacing: 12) {
          Icon(.fileText, size: 20).foregroundColor(Palette.text2)
          Text(tr("我的饮食档案", "My diet profile")).font(Typo.body).foregroundColor(Palette.text)
          Spacer(minLength: 8)
          Text(b.meta).font(Typo.caption).foregroundColor(Palette.text2).lineLimit(1).minimumScaleFactor(0.8)
          Icon(.chevronRight, size: 16).foregroundColor(Palette.text3)
        }
        .padding(.vertical, 12)
        .contentShape(Rectangle())
      }
      .buttonStyle(PressScale())
    }
  }

  @ViewBuilder private func otherSheet(_ s: OtherSheet) -> some View {
    switch s {
    #if DEBUG
    case .dev(let what): devSheet(what)
    #endif
    case .diet:
      SheetContainer(title: tr("我的饮食档案", "My diet profile"), close: { other = nil }) {
        DietProfileSheet(store: store)
      }
      .presentationDetents([.large])
      .presentationDragIndicator(.visible)
    case .combine(let action, let entries):
      SheetContainer(title: action == .merge ? tr("合并成一条", "Merge into one") : tr("存成模板", "Save as template"), close: { other = nil }) {
        FoodCombineSheet(action: action, picked: entries, store: store) { t in
          other = nil
          picked = nil
          toast = t
        }
      }
      .presentationDetents([.medium, .large])
      .presentationDragIndicator(.visible)
    }
  }

  @ViewBuilder private var diet: some View {
    HStack {
      DayStepper(day: store.day, today: store.today) { store.select($0) }
      Spacer()
    }
    FoodIntakeSection(nutrition: store.nutrition, day: store.day, isToday: store.isToday, refreshing: store.refreshing, picked: picked,
                      selectButton: selectButton, pickBar: pickedBar, open: tapEntry)
    if let w = store.water { FoodWaterCard(water: w, canWrite: store.isToday, add: addWater, undo: undoWater).id("n5.water") }
    if store.isToday && !store.templates.isEmpty {
      VStack(alignment: .leading, spacing: 10) {
        SectionHeader(title: tr("快捷记录", "Quick log"))
        QuickMeals(store: store, toast: $toast)
      }
      .id("n5.templates")
    }
    if store.isToday, let plan = store.mealPlan, !plan.isEmpty {
      MealPlanSection(plan: plan, retry: { Task { await store.refreshMealPlan() } }) { r, slot in
        recipeSlot = slot
        recipe = r
      }
      .id("n5.mealplan")
    }
    recipesRow
  }

  // MARK: intake

  /// 选择 / 完成, when entries can be merged.
  private var selectButton: AnyView? {
    guard store.mergeOn, store.nutrition?.entries.contains(where: { !$0.isLocal }) == true else { return nil }
    return AnyView(
      Button(picked == nil ? tr("选择", "Select") : tr("完成", "Done")) { picked = picked == nil ? [] : nil }
        .font(.system(size: 14, weight: .medium)).foregroundColor(Palette.text2)
        .padding(.leading, 6))
  }

  private var pickedBar: AnyView? {
    guard let picked, let n = store.nutrition, !n.entries.isEmpty else { return nil }
    return AnyView(pickBar(picked, entries: n.entries))
  }

  private func tapEntry(_ e: FoodEntry) {
    guard !e.isLocal else { return }
    guard var p = picked else {
      entry = e
      return
    }
    if p.contains(e.id) { p.remove(e.id) } else { p.insert(e.id) }
    picked = p
  }

  /// 已选 N 条 · 存成模板 · 合并成一条 (a merge needs at least two). Both need the server (docs section 5.5).
  private func pickBar(_ ids: Set<String>, entries: [FoodEntry]) -> some View {
    let chosen = entries.filter { ids.contains($0.id) }
    return HStack(spacing: 8) {
      Text(tr("已选 \(chosen.count) 条", "\(chosen.count) selected")).font(Typo.caption.monospacedDigit()).foregroundColor(Palette.text2).lineLimit(1)
      Spacer(minLength: 4)
      pickButton(tr("存成模板", "Save as template"), filled: false, enabled: !chosen.isEmpty) { combine(.template, chosen) }
      pickButton(tr("合并成一条", "Merge"), filled: true, enabled: chosen.count >= 2) { combine(.merge, chosen) }
    }
    .padding(12)
    .background(RoundedRectangle(cornerRadius: Metrics.tileRadius, style: .continuous).fill(Palette.surface))
  }

  private func pickButton(_ title: String, filled: Bool, enabled: Bool, action: @escaping () -> Void) -> some View {
    Button(action: action) {
      Text(title).font(.system(size: 13, weight: .medium)).foregroundColor(filled ? Palette.onAccent : Palette.text).lineLimit(1)
        .padding(.vertical, 8).padding(.horizontal, 12)
        .background(Capsule().fill(filled ? Palette.accent : Palette.bg))
    }
    .buttonStyle(PressScale())
    .disabled(!enabled)
    .opacity(enabled && !store.offline ? 1 : 0.4)
  }

  private func combine(_ action: FoodCombine.Action, _ entries: [FoodEntry]) {
    if store.offline {
      toast = Toast(tr("需要联网", "Needs a connection"))
      return
    }
    other = .combine(action, entries)
  }

  // MARK: water

  private func addWater(_ ml: Int) {
    Task {
      let out = await store.addWater(ml)
      if !out.sent { toast = Toast(out.message) }
    }
  }

  private func undoWater() {
    Task {
      toast = Toast(await store.undoWater() ? tr("已撤销", "Undone") : tr("撤销要联网，稍后再试", "Undo needs a connection, try again later"))
    }
  }

  // MARK: recipes

  private var recipesRow: some View {
    RowGroup {
      NavigationLink {
        RecipesView(store: store) { r in recipe = r }
      } label: {
        HStack(spacing: 12) {
          Icon(.bookOpen, size: 20).foregroundColor(Palette.text2)
          Text(tr("食谱", "Recipes")).font(Typo.body).foregroundColor(Palette.text)
          Spacer()
          Text("\(store.recipes.count)").font(Typo.body.monospacedDigit()).foregroundColor(Palette.text2)
          Icon(.chevronRight, size: 16).foregroundColor(Palette.text3)
        }
        .padding(.vertical, 12)
        .contentShape(Rectangle())
      }
      .buttonStyle(PressScale())
    }
  }
}

/// The day's intake: the goal rings and each meal's entries. `open` nil = read-only (someone else's page); picking entries to
/// merge belongs to my own page, which passes its button and bar.
struct FoodIntakeSection: View {
  let nutrition: NutritionDay?
  let day: String
  let isToday: Bool
  let refreshing: Bool
  var picked: Set<String>?
  var selectButton: AnyView?
  var pickBar: AnyView?
  var open: ((FoodEntry) -> Void)?

  var body: some View {
    VStack(alignment: .leading, spacing: 10) {
      HStack {
        SectionHeader(title: isToday ? tr("今天吃了", "Eaten today") : tr("\(RecordDay.short(day)) 吃了", "Eaten on \(RecordDay.short(day))"))
        if let name = nutrition?.goalName { Badge(label: name) }
        if nutrition?.notLogged == true { Badge(label: tr("未记录", "Not logged")) }
        if let selectButton { selectButton }
      }
      if let n = nutrition {
        Card {
          HStack(spacing: 4) {
            GoalRing(label: tr("热量", "Energy"), value: n.intake.kcal, goal: n.goal.kcal, unit: "kcal", tone: .train)
            GoalRing(label: tr("蛋白质", "Protein"), value: n.intake.protein, goal: n.goal.protein, unit: "g", tone: .good)
            GoalRing(label: tr("碳水", "Carbs"), value: n.intake.carbs, goal: n.goal.carbs, unit: "g", tone: .mid)
            GoalRing(label: tr("脂肪", "Fat"), value: n.intake.fat, goal: n.goal.fat, unit: "g", tone: .sleep)
          }
        }
        let extras = n.extras
        if !extras.isEmpty {
          HStack(spacing: 8) {
            ForEach(extras, id: \.0) { label, value in
              VStack(alignment: .leading, spacing: 2) {
                Text(label).font(Typo.label).foregroundColor(Palette.text2).lineLimit(1).minimumScaleFactor(0.8)
                Text(value).font(Typo.caption.monospacedDigit()).foregroundColor(Palette.text).lineLimit(1)
              }
              .frame(maxWidth: .infinity, alignment: .leading)
            }
          }
          .padding(.horizontal, 4)
        }
        if let err = n.error { Banner(text: err, tone: .mid) }
        if n.entries.isEmpty {
          EmptyNote(text: tr("还没有记录", "Nothing logged yet"))
        } else {
          ForEach(n.meals(), id: \.0) { meal, xs in mealCard(meal, xs) }
          if let pickBar { pickBar }
        }
      } else {
        Card { EmptyNote(text: refreshing ? "…" : tr("暂无", "None")) }
      }
    }
  }

  private func mealCard(_ meal: MealSlot, _ xs: [FoodEntry]) -> some View {
    let total = xs.reduce(Macros()) { $0 + $1.macros }
    return VStack(alignment: .leading, spacing: 0) {
      HStack(alignment: .firstTextBaseline) {
        Text(meal.title).font(Typo.bodyStrong).foregroundColor(Palette.text)
        Spacer()
        Text("\(RecordFormat.num(total.kcal)) kcal · \(tr("蛋白", "protein")) \(RecordFormat.num(total.protein)) g")
          .font(Typo.caption.monospacedDigit()).foregroundColor(Palette.text2)
      }
      .padding(.bottom, 6)
      ForEach(xs) { e in
        Button { open?(e) } label: {
          HStack(spacing: 8) {
            if let picked, !e.isLocal {
              ZStack {
                Circle().stroke(picked.contains(e.id) ? Palette.good : Palette.text3, lineWidth: 1.5).frame(width: 20, height: 20)
                if picked.contains(e.id) { Icon(.check, size: 12).foregroundColor(Palette.good) }
              }
            }
            Text(e.food).font(Typo.body).foregroundColor(Palette.text).lineLimit(2)
            if let p = e.pending { PendingMark(needsAttention: p.needsAttention) }
            Spacer(minLength: 8)
            Text("\(RecordFormat.num(e.macros.kcal)) kcal").font(Typo.body.monospacedDigit()).foregroundColor(Palette.text2)
            if !e.isLocal && picked == nil && open != nil { Icon(.chevronRight, size: 14).foregroundColor(Palette.text3) }
          }
          .padding(.vertical, 10)
          .contentShape(Rectangle())
          .overlay(Rectangle().fill(Palette.hair).frame(height: 1), alignment: .top)
        }
        .buttonStyle(PressScale())
        .disabled(open == nil)
      }
    }
    .padding(16)
    .background(RoundedRectangle(cornerRadius: Metrics.cardRadius, style: .continuous).fill(Palette.surface))
  }
}

/// 「今天喝水」: total / goal, a bar, +250 / +500 and undo of the newest drink.
struct FoodWaterCard: View {
  let water: WaterDay
  let canWrite: Bool
  let add: (Int) -> Void
  let undo: () -> Void

  var body: some View {
    VStack(alignment: .leading, spacing: 10) {
      HStack {
        SectionHeader(title: canWrite ? tr("今天喝水", "Water today") : tr("喝水", "Water"))
        if let pct = water.percent {
          Text("\(pct)%").font(Typo.label.monospacedDigit()).foregroundColor(Palette.text2)
        }
      }
      Card {
        VStack(alignment: .leading, spacing: 10) {
          HStack(alignment: .firstTextBaseline, spacing: 4) {
            Icon(.droplet, size: 18).foregroundColor(Palette.sleep)
            Text(RecordFormat.num(water.total)).font(.system(size: 22, weight: .semibold).monospacedDigit()).foregroundColor(Palette.text)
            Text("/ \(RecordFormat.num(water.goal)) ml").font(Typo.caption).foregroundColor(Palette.text2)
            if water.drinks.contains(where: { $0.pending != nil }) {
              PendingMark(needsAttention: water.drinks.contains { $0.pending?.needsAttention == true })
            }
          }
          ProgressBar(fraction: water.fraction, tone: .sleep)
          if let note = water.goalNote { Text(note).font(Typo.label).foregroundColor(Palette.text3) }
          if canWrite {
            HStack(spacing: 8) {
              ForEach(water.quick, id: \.self) { ml in
                Button { add(ml) } label: {
                  Text("+\(ml) ml").font(Typo.bodyStrong.monospacedDigit()).foregroundColor(Palette.text)
                    .frame(maxWidth: .infinity, minHeight: 40)
                    .background(Capsule().fill(Palette.bg))
                }
                .buttonStyle(PressScale())
              }
              if water.last != nil {
                Button(action: undo) {
                  Icon(.undo2, size: 18).foregroundColor(Palette.text2).frame(width: 44, height: 40)
                }
                .buttonStyle(PressScale())
                .accessibilityLabel(tr("撤销最近一次（\(RecordFormat.num(water.last?.ml)) ml）", "Undo the last drink (\(RecordFormat.num(water.last?.ml)) ml)"))
              }
            }
          }
        }
      }
    }
  }
}

/// One food entry: its nutrition, the portion (改份量) and delete.
struct FoodEntrySheet: View {
  let entry: FoodEntry
  @ObservedObject var store: FoodStore
  let done: (String?) -> Void
  @State private var quantity: Double
  @State private var confirmDelete = false
  @State private var busy = false

  init(entry: FoodEntry, store: FoodStore, done: @escaping (String?) -> Void) {
    self.entry = entry
    self.store = store
    self.done = done
    _quantity = State(initialValue: entry.quantity)
  }

  private var factor: Double { entry.quantity > 0 ? quantity / entry.quantity : 1 }

  var body: some View {
    VStack(alignment: .leading, spacing: 0) {
      let m = entry.macros.scaled(factor)
      KeyValueRow(label: tr("份量", "Amount"), value: [entry.unit, RecordFormat.num(quantity, 2) == "1" ? nil : "× \(RecordFormat.num(quantity, 2))"]
        .compactMap { $0 }.joined(separator: " "))
      if let t = entry.time { KeyValueRow(label: tr("时间", "Time"), value: t) }
      KeyValueRow(label: tr("热量", "Energy"), value: "\(RecordFormat.num(m.kcal)) kcal")
      KeyValueRow(label: tr("蛋白质", "Protein"), value: "\(RecordFormat.num(m.protein, 1)) g")
      KeyValueRow(label: tr("碳水", "Carbs"), value: "\(RecordFormat.num(m.carbs, 1)) g")
      KeyValueRow(label: tr("脂肪", "Fat"), value: "\(RecordFormat.num(m.fat, 1)) g")
      ForEach(entry.extras, id: \.0) { KeyValueRow(label: $0.0, value: $0.1) }
      if entry.date <= store.today {
        VStack(alignment: .leading, spacing: 12) {
          Text(tr("改份量", "Portion")).font(Typo.label).foregroundColor(Palette.text2)
          HStack {
            ValueStepper(text: "× \(RecordFormat.num(quantity, 2))", minus: { quantity = max(0.25, quantity - 0.25) },
                         plus: { quantity = min(10, quantity + 0.25) }, canMinus: quantity > 0.25, canPlus: quantity < 10)
            Spacer()
          }
          PrimaryButton(title: tr("保存份量", "Save portion"), busy: busy) { savePortion() }
            .disabled(abs(quantity - entry.quantity) < 0.001)
            .opacity(abs(quantity - entry.quantity) < 0.001 ? 0.4 : 1)
          if let id = entry.mergeId, store.mergeOn {
            SecondaryButton(title: tr("拆开成原来的几条", "Split back into the original entries"), background: Palette.inSheet) {
              split(id)
            }
          }
          SecondaryButton(icon: .trash, title: tr("删除这条", "Delete this entry"), tone: .bad, background: Palette.inSheet) {
            confirmDelete = true
          }
        }
        .padding(.top, 18)
      }
    }
    .confirmationDialog(tr("从饮食记录里删除，不能恢复", "Delete from the food log? This can't be undone"), isPresented: $confirmDelete,
                        titleVisibility: .visible) {
      Button(tr("确认删除", "Delete"), role: .destructive) { delete() }
      Button(tr("取消", "Cancel"), role: .cancel) {}
    }
  }

  private func savePortion() {
    busy = true
    Task {
      let out = await store.setPortion(entry, quantity: quantity)
      busy = false
      done(out.sent ? tr("已改成 × \(RecordFormat.num(quantity, 2))", "Changed to × \(RecordFormat.num(quantity, 2))") : out.message)
    }
  }

  private func delete() {
    Task {
      let out = await store.delete(entry)
      done(out.sent ? tr("已删除", "Deleted") : out.message)
    }
  }

  private func split(_ id: Int) {
    Task {
      let err = await store.split(mergeId: id)
      done(err ?? tr("已拆开", "Split"))
    }
  }
}
