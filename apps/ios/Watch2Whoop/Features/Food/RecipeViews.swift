import SwiftUI

/// 饮食 → 食谱: search and the recipes by meal (favourites first). Tapping one opens its sheet on the food page.
struct RecipesView: View {
  @ObservedObject var store: FoodStore
  let open: (Recipe) -> Void
  @State private var query = ""

  init(store: FoodStore, open: @escaping (Recipe) -> Void) {
    self.store = store
    self.open = open
  }

  var body: some View {
    ScrollView(.vertical, showsIndicators: false) {
      VStack(alignment: .leading, spacing: 18) {
        HStack(spacing: 8) {
          Icon(.search, size: 18).foregroundColor(Palette.text3)
          TextField(tr("搜索", "Search"), text: $query)
            .font(Typo.body).textInputAutocapitalization(.never).autocorrectionDisabled()
        }
        .padding(.horizontal, 14).frame(minHeight: 44)
        .background(RoundedRectangle(cornerRadius: 12, style: .continuous).fill(Palette.surface))
        let groups = Recipe.grouped(store.recipes, query: query)
        if groups.isEmpty {
          EmptyNote(text: query.isEmpty ? tr("暂无食谱", "No recipes yet") : tr("没有找到", "Nothing found"))
        }
        ForEach(groups, id: \.0) { meal, list in
          VStack(alignment: .leading, spacing: 10) {
            HStack {
              SectionHeader(title: meal.title)
              Text("\(list.count)").font(Typo.label).foregroundColor(Palette.text3)
            }
            LazyVGrid(columns: [GridItem(.flexible(), spacing: 10), GridItem(.flexible(), spacing: 10)], spacing: 10) {
              ForEach(list) { r in
                Button { open(r) } label: { RecipeCard(r: r) }.buttonStyle(PressScale())
              }
            }
          }
        }
      }
      .padding(.horizontal, Metrics.gutter).padding(.vertical, 12)
      .frame(maxWidth: Metrics.maxContentWidth).frame(maxWidth: .infinity)
    }
    .background(Palette.bg.ignoresSafeArea())
    .navigationTitle(tr("食谱", "Recipes"))
    .navigationBarTitleDisplayMode(.inline)
    .refreshable { await store.refresh() }
  }
}

/// A recipe tile: name (★ when a favourite), kcal · protein · minutes, constraint badges.
struct RecipeCard: View {
  let r: Recipe

  var body: some View {
    VStack(alignment: .leading, spacing: 6) {
      HStack(alignment: .top, spacing: 4) {
        Text(r.name).font(Typo.bodyStrong).foregroundColor(Palette.text).lineLimit(2).multilineTextAlignment(.leading)
        Spacer(minLength: 0)
        if r.favorite { Icon(.star, size: 14).foregroundColor(Palette.mid) }
      }
      Text([r.perServing.kcal.map { "\(RecordFormat.num($0)) kcal" }, r.perServing.protein.map { "\(RecordFormat.num($0)) g" },
            r.minutes.map { "\($0)′" }].compactMap { $0 }.joined(separator: " · "))
        .font(Typo.caption.monospacedDigit()).foregroundColor(Palette.text2)
      if !r.badges.isEmpty {
        HStack(spacing: 4) { ForEach(r.badges.prefix(2), id: \.0) { Badge(label: $0.0, tone: $0.1) } }
      }
    }
    .frame(maxWidth: .infinity, minHeight: 84, alignment: .topLeading)
    .padding(12)
    .background(RoundedRectangle(cornerRadius: Metrics.tileRadius, style: .continuous).fill(Palette.surface))
    .opacity(r.canMake ? 1 : 0.55)
  }
}

/// A recipe: nutrition × servings, ingredients, steps; meal, servings, 收藏 / 现在能做, 「记 N 份」.
struct RecipeSheet: View {
  @ObservedObject var store: FoodStore
  let done: (Toast?) -> Void
  @State private var recipe: Recipe
  @State private var servings = 1.0
  @State private var meal: MealSlot
  @State private var busy = false
  @State private var message: String?

  init(recipe: Recipe, store: FoodStore, done: @escaping (Toast?) -> Void) {
    self.store = store
    self.done = done
    let detail = store.recipeDetail(recipe.id) ?? recipe
    _recipe = State(initialValue: detail)
    _meal = State(initialValue: recipe.slots.contains(MealSlot.now()) ? MealSlot.now() : (recipe.slots.first ?? MealSlot.now()))
  }

  var body: some View {
    VStack(alignment: .leading, spacing: 16) {
      let m = recipe.perServing.scaled(servings)
      HStack(spacing: 8) {
        MetricTile(label: tr("热量", "Energy"), value: RecordFormat.num(m.kcal), unit: "kcal")
        MetricTile(label: tr("蛋白", "Protein"), value: RecordFormat.num(m.protein), unit: "g")
        MetricTile(label: tr("碳水", "Carbs"), value: RecordFormat.num(m.carbs), unit: "g")
        MetricTile(label: tr("脂肪", "Fat"), value: RecordFormat.num(m.fat), unit: "g")
      }
      if let s = recipe.servingLabel {
        Text(tr("每份 = \(s)", "1 serving = \(s)")).font(Typo.caption).foregroundColor(Palette.text2)
      }
      if !recipe.badges.isEmpty {
        ScrollView(.horizontal, showsIndicators: false) {
          HStack(spacing: 6) { ForEach(recipe.badges, id: \.0) { Badge(label: $0.0, tone: $0.1) } }
        }
      }
      if !recipe.ingredients.isEmpty {
        VStack(alignment: .leading, spacing: 0) {
          SectionHeader(title: servings == 1 ? tr("食材", "Ingredients") : tr("食材 · \(RecordFormat.num(servings, 1)) 份", "Ingredients · \(RecordFormat.num(servings, 1))"))
            .padding(.bottom, 4)
          ForEach(Array(recipe.ingredients.enumerated()), id: \.offset) { _, i in
            KeyValueRow(label: i.food, value: i.grams.map { "\(RecordFormat.num($0 * servings / max(recipe.servings, 0.5))) g" } ?? i.amount ?? "—")
          }
        }
      }
      if !recipe.steps.isEmpty {
        VStack(alignment: .leading, spacing: 8) {
          SectionHeader(title: tr("做法", "Method"))
          ForEach(Array(recipe.steps.enumerated()), id: \.offset) { n, s in
            HStack(alignment: .firstTextBaseline, spacing: 10) {
              Text("\(n + 1)").font(Typo.label.monospacedDigit()).foregroundColor(Palette.text3).frame(width: 16)
              Text(s).font(Typo.body).foregroundColor(Palette.text).fixedSize(horizontal: false, vertical: true)
            }
          }
        }
      }
      VStack(alignment: .leading, spacing: 12) {
        SegmentedControl(items: MealSlot.allCases.map { ($0, $0.title) }, selection: $meal)
        HStack {
          ValueStepper(text: tr("\(RecordFormat.num(servings, 1)) 份", "\(RecordFormat.num(servings, 1)) serv."),
                       minus: { servings = max(0.5, servings - 0.5) }, plus: { servings = min(6, servings + 0.5) },
                       canMinus: servings > 0.5, canPlus: servings < 6)
          Spacer()
          Button { toggle("favorite", !recipe.favorite) } label: {
            Icon(.star, size: 20).foregroundColor(recipe.favorite ? Palette.mid : Palette.text3).frame(width: 44, height: 40)
          }
          .accessibilityLabel(recipe.favorite ? tr("取消收藏", "Unfavourite") : tr("收藏", "Favourite"))
          Button(recipe.canMake ? tr("现在能做", "Can make") : tr("现在做不了", "Can't make now")) { toggle("can_make", !recipe.canMake) }
            .font(.system(size: 13, weight: .medium)).foregroundColor(Palette.text2)
        }
        PrimaryButton(title: store.isToday ? tr("记 \(RecordFormat.num(servings, 1)) 份", "Log \(RecordFormat.num(servings, 1))")
                        : tr("记到 \(RecordDay.short(store.day))", "Log to \(RecordDay.short(store.day))"), busy: busy) { log(again: false) }
        if let message { Text(message).font(Typo.caption).foregroundColor(Palette.text2) }
      }
      .padding(.top, 4)
    }
    .task {
      if let d = await store.loadRecipe(recipe.id) { recipe = d }
    }
  }

  private func toggle(_ key: String, _ value: Bool) {
    Task {
      if await store.patchRecipe(recipe.id, [key: value]) {
        if key == "favorite" { recipe.favorite = value } else { recipe.canMake = value }
      } else {
        message = tr("需要联网", "Needs a connection")
      }
    }
  }

  private func log(again: Bool) {
    busy = true
    Task {
      let out = await store.log(recipe, servings: servings, meal: meal, again: again)
      busy = false
      if out.conflict {
        store.drop(out)
        let r = recipe, s = servings, m = meal
        done(Toast(tr("这天已经记过「\(r.name)」", "\(r.name) is already logged that day"), actionTitle: tr("再记一份", "Log another")) {
          Task { _ = await store.log(r, servings: s, meal: m, again: true) }
        })
      } else {
        done(Toast(out.sent ? tr("已记 · \(RecordFormat.num(servings, 1)) 份", "Logged · \(RecordFormat.num(servings, 1))") : out.message))
      }
    }
  }
}

/// 饮食 → 采购: this week's shopping list by category; tap a line to tick it (last tick wins, queued offline).
struct GroceryView: View {
  @ObservedObject var store: FoodStore

  var body: some View {
    VStack(alignment: .leading, spacing: 18) {
      if let g = store.grocery, !g.items.isEmpty {
        HStack {
          SectionHeader(title: tr("本周采购", "This week's list"))
          Text("\(g.bought)/\(g.items.count)").font(Typo.label.monospacedDigit()).foregroundColor(Palette.text2)
        }
        ProgressBar(fraction: Double(g.bought) / Double(max(1, g.items.count)))
        ForEach(g.byCategory(), id: \.0) { cat, items in
          RowGroup(title: cat) {
            ForEach(Array(items.enumerated()), id: \.element.id) { n, item in
              if n > 0 { RowDivider(inset: 34) }
              Button { Task { await store.setBought(item, !item.bought) } } label: {
                HStack(spacing: 12) {
                  ZStack {
                    Circle().stroke(item.bought ? Palette.good : Palette.text3, lineWidth: 1.5).frame(width: 22, height: 22)
                    if item.bought { Icon(.check, size: 14).foregroundColor(Palette.good) }
                  }
                  VStack(alignment: .leading, spacing: 2) {
                    Text(item.name).font(Typo.body).foregroundColor(item.bought ? Palette.text3 : Palette.text)
                      .strikethrough(item.bought, color: Palette.text3)
                    if let note = item.note { Text(note).font(Typo.caption).foregroundColor(Palette.text3) }
                  }
                  if let p = item.pending { PendingMark(needsAttention: p.needsAttention) }
                  Spacer()
                  if let q = item.qty { Text(q).font(Typo.caption.monospacedDigit()).foregroundColor(Palette.text2) }
                }
                .padding(.vertical, 11)
                .contentShape(Rectangle())
              }
              .buttonStyle(PressScale())
              .accessibilityAddTraits(item.bought ? .isSelected : [])
            }
          }
        }
      } else {
        Card {
          VStack(spacing: 8) {
            Icon(.shoppingCart, size: 28).foregroundColor(Palette.text3)
            Text(tr("这周还没有采购清单。让你的 AI 按计划生成一份。", "No shopping list this week. Ask your AI to make one from your plan."))
              .font(Typo.caption).foregroundColor(Palette.text2).multilineTextAlignment(.center)
          }
          .frame(maxWidth: .infinity).padding(.vertical, 12)
        }
      }
    }
  }
}
