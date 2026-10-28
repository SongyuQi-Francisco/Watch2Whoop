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
  @State private var logged: MealTemplate?
  @State private var busy = false

  enum Mode: Hashable { case diet, shopping }

  init(store: FoodStore, toast: Binding<Toast?>) {
    self.store = store
    _toast = toast
  }

  var body: some View {
    content
    .task { await store.refresh() }
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
    .sheet(item: $recipe) { r in
      SheetContainer(title: r.name, close: { recipe = nil }) {
        RecipeSheet(recipe: r, store: store) { t in
          recipe = nil
          toast = t
        }
      }
      .presentationDetents([.large])
      .presentationDragIndicator(.visible)
    }
    .confirmationDialog(logged.map { tr("今天已记过「\($0.name)」", "\($0.name) is logged today") } ?? "",
                        isPresented: Binding(get: { logged != nil }, set: { if !$0 { logged = nil } }), titleVisibility: .visible) {
      if let t = logged {
        Button(tr("再记一份", "Log another")) { logTemplate(t, again: true) }
        Button(tr("撤销今天这次", "Undo today's"), role: .destructive) { undoTemplate(t) }
      }
      Button(tr("取消", "Cancel"), role: .cancel) {}
    }
  }

  private var content: some View {
    VStack(alignment: .leading, spacing: 22) {
      SegmentedControl(items: [(Mode.diet, tr("饮食", "Food")), (.shopping, tr("采购", "Shopping"))], selection: $mode)
      RecordStatusLine(offline: store.offline, refreshing: store.refreshing && store.nutrition == nil)
      if mode == .diet { diet } else { GroceryView(store: store) }
    }
  }

  @ViewBuilder private var diet: some View {
    HStack {
      DayStepper(day: store.day, today: store.today) { store.select($0) }
      Spacer()
    }
    intakeSection
    if let w = store.water { FoodWaterCard(water: w, canWrite: store.isToday, add: addWater, undo: undoWater).id("n5.water") }
    if store.isToday && !store.templates.isEmpty { templatesSection.id("n5.templates") }
    recipesRow
  }

  // MARK: intake

  @ViewBuilder private var intakeSection: some View {
    VStack(alignment: .leading, spacing: 10) {
      HStack {
        SectionHeader(title: store.isToday ? tr("今天吃了", "Eaten today") : tr("\(RecordDay.short(store.day)) 吃了", "Eaten on \(RecordDay.short(store.day))"))
        if let name = store.nutrition?.goalName { Badge(label: name) }
      }
      if let n = store.nutrition {
        Card {
          HStack(spacing: 4) {
            GoalRing(label: tr("热量", "Energy"), value: n.intake.kcal, goal: n.goal.kcal, unit: "kcal", tone: .train)
            GoalRing(label: tr("蛋白质", "Protein"), value: n.intake.protein, goal: n.goal.protein, unit: "g", tone: .good)
            GoalRing(label: tr("碳水", "Carbs"), value: n.intake.carbs, goal: n.goal.carbs, unit: "g", tone: .mid)
            GoalRing(label: tr("脂肪", "Fat"), value: n.intake.fat, goal: n.goal.fat, unit: "g", tone: .sleep)
          }
        }
        if let err = n.error { Banner(text: err, tone: .mid) }
        if n.entries.isEmpty {
          EmptyNote(text: tr("还没有记录", "Nothing logged yet"))
        } else {
          ForEach(n.meals(), id: \.0) { meal, xs in mealCard(meal, xs) }
        }
      } else {
        Card { EmptyNote(text: store.refreshing ? "…" : tr("暂无", "None")) }
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
        Button { if !e.isLocal { entry = e } } label: {
          HStack(spacing: 8) {
            Text(e.food).font(Typo.body).foregroundColor(Palette.text).lineLimit(2)
            if let p = e.pending { PendingMark(needsAttention: p.needsAttention) }
            Spacer(minLength: 8)
            Text("\(RecordFormat.num(e.macros.kcal)) kcal").font(Typo.body.monospacedDigit()).foregroundColor(Palette.text2)
            if !e.isLocal { Icon(.chevronRight, size: 14).foregroundColor(Palette.text3) }
          }
          .padding(.vertical, 10)
          .contentShape(Rectangle())
          .overlay(Rectangle().fill(Palette.hair).frame(height: 1), alignment: .top)
        }
        .buttonStyle(PressScale())
      }
    }
    .padding(16)
    .background(RoundedRectangle(cornerRadius: Metrics.cardRadius, style: .continuous).fill(Palette.surface))
  }

  // MARK: quick meals

  private var templatesSection: some View {
    VStack(alignment: .leading, spacing: 10) {
      SectionHeader(title: tr("快捷记录", "Quick log"))
      ScrollView(.horizontal, showsIndicators: false) {
        HStack(spacing: 10) {
          ForEach(store.templates) { t in
            Button { tapTemplate(t) } label: { TemplateCard(t: t) }
              .buttonStyle(PressScale())
              .disabled(busy)
          }
        }
      }
    }
  }

  private func tapTemplate(_ t: MealTemplate) {
    if t.todayLog != nil || t.pending != nil { logged = t } else { logTemplate(t, again: false) }
  }

  private func logTemplate(_ t: MealTemplate, again: Bool) {
    busy = true
    Task {
      defer { busy = false }
      let out = await store.log(t, again: again)
      if out.conflict {
        store.drop(out)
        toast = Toast(tr("今天已记过「\(t.name)」", "\(t.name) is already logged today"), actionTitle: tr("再记一份", "Log another")) {
          logTemplate(t, again: true)
        }
      } else if out.sent || out.queued {
        toast = Toast(out.sent ? tr("已记 · \(t.name)", "Logged · \(t.name)") : out.message, actionTitle: tr("撤销", "Undo")) {
          undoTemplate(t)
        }
      } else {
        toast = Toast(out.message)
      }
    }
  }

  private func undoTemplate(_ t: MealTemplate) {
    Task {
      let cur = store.templates.first { $0.id == t.id } ?? t
      toast = Toast(await store.undoLog(cur) ? tr("已撤销", "Undone") : tr("撤销要联网，稍后再试", "Undo needs a connection, try again later"))
    }
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

/// A quick-meal card in the horizontal row: name, kcal · protein, 「已记」 when logged today.
private struct TemplateCard: View {
  let t: MealTemplate

  var body: some View {
    VStack(alignment: .leading, spacing: 6) {
      HStack(spacing: 6) {
        Text(t.name).font(Typo.bodyStrong).foregroundColor(Palette.text).lineLimit(1)
        if let p = t.pending { PendingMark(needsAttention: p.needsAttention) }
      }
      Text("\(RecordFormat.num(t.totals.kcal)) kcal · \(RecordFormat.num(t.totals.protein)) g")
        .font(Typo.caption.monospacedDigit()).foregroundColor(Palette.text2)
      HStack(spacing: 6) {
        Text(t.meal.title).font(Typo.label).foregroundColor(Palette.text3)
        if t.todayLog != nil || t.pending != nil { Badge(label: tr("已记", "Logged"), tone: .good) }
      }
    }
    .frame(width: 150, alignment: .topLeading)
    .frame(minHeight: 72, alignment: .topLeading)
    .padding(14)
    .background(RoundedRectangle(cornerRadius: Metrics.tileRadius, style: .continuous).fill(Palette.surface))
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
        if water.goal != nil {
          Text("\(Int((water.fraction * 100).rounded()))%").font(Typo.label.monospacedDigit()).foregroundColor(Palette.text2)
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
                .accessibilityLabel(tr("撤销最近一次", "Undo the last drink"))
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
}
