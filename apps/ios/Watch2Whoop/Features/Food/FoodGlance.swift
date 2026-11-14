import SwiftUI

/// 记录 → 饮食, the first glance (docs/design/2.0.1 `10-food`): the kcal dial (ink arc, the goal as a long tick, left and
/// goal under the number), three readings (蛋白 · 碳水 · 脂肪 against their goals), three faces (快记 → quick meals, 饮水 →
/// water, 食谱 → recipes). Logging itself goes through the Jarvis key (camera, voice) and the faces; the meals of the day
/// are below the fold.
struct FoodGlanceTop: View {
  @ObservedObject var store: FoodStore
  let meals: () -> Void
  let quick: () -> Void
  let water: () -> Void
  let recipes: () -> Void
  @Environment(\.askAI) private var askAI

  static var dialSide: CGFloat { TodayGlancePage.dialSide }

  var body: some View {
    VStack(spacing: 0) {
      hero.padding(.top, 24)
      readings.padding(.top, 36)
      faces.padding(.top, 40)
    }
  }

  // MARK: hero

  private var hero: some View {
    let n = store.nutrition
    let d = RecordGlance.foodDial(kcal: n?.intake.kcal, goal: n?.goal.kcal)
    let side = Self.dialSide, k = side / 280
    return Button(action: meals) {
      TickDial(fraction: d.fraction, tone: .text, zones: d.target.map { [$0] } ?? [], side: side) {
        VStack(spacing: 0) {
          Text("kcal").font(.system(size: 15 * k, weight: .medium)).tracking(2).foregroundColor(Palette.text2)
          Text(n.map { RecordFormat.num($0.intake.kcal ?? 0) } ?? "—").font(Typo.hero(100 * k)).tracking(-3 * k)
            .foregroundColor(n == nil ? Palette.text2 : Palette.text).lineLimit(1).minimumScaleFactor(0.5)
          if let left = d.left {
            Text(left >= 0 ? tr("剩 \(RecordFormat.num(left))", "\(RecordFormat.num(left)) left") : tr("超 \(RecordFormat.num(-left))", "\(RecordFormat.num(-left)) over"))
              .font(.system(size: 17 * k, weight: .medium).monospacedDigit()).foregroundColor(left >= 0 ? Palette.text2 : Palette.mid)
            Text(tr("目标 \(RecordFormat.num(n?.goal.kcal))", "Goal \(RecordFormat.num(n?.goal.kcal))"))
              .font(.system(size: 15 * k, weight: .medium).monospacedDigit()).foregroundColor(Palette.text2).padding(.top, 4)
          }
        }
        .frame(width: side * 0.7)
      }
    }
    .buttonStyle(PressScale())
    .frame(maxWidth: .infinity)
    .contextMenu {
      Button { askAI(tr("今天吃得怎么样？还该吃什么", "How is my eating today, and what should I still eat?")) } label: {
        Text(tr("问 Jarvis：今天的饮食", "Ask Jarvis: today's food"))
      }
      Button(action: meals) { Text(tr("看详情", "Details")) }
    }
    .accessibilityElement(children: .ignore)
    .accessibilityLabel(dialLabel)
    .accessibilityAddTraits(.isButton)
  }

  private var dialLabel: String {
    guard let n = store.nutrition else { return tr("饮食", "Food") }
    let eaten = RecordFormat.num(n.intake.kcal ?? 0)
    guard let g = n.goal.kcal, g > 0 else { return tr("已吃 \(eaten) 千卡", "Eaten \(eaten) kcal") }
    return tr("已吃 \(eaten) 千卡，目标 \(RecordFormat.num(g))", "Eaten \(eaten) kcal of \(RecordFormat.num(g))")
  }

  // MARK: readings: the three macros

  private var readings: some View {
    let n = store.nutrition
    return HStack(alignment: .top, spacing: 16) {
      macro(n?.intake.protein, goal: n?.goal.protein, key: "protein", label: tr("蛋白", "Protein"))
      macro(n?.intake.carbs, goal: n?.goal.carbs, key: "carbs", label: tr("碳水", "Carbs"))
      macro(n?.intake.fat, goal: n?.goal.fat, key: "fat", label: tr("脂肪", "Fat"))
    }
  }

  private func macro(_ v: Double?, goal: Double?, key: String, label: String) -> some View {
    let top = RecordGlance.macroScale(v, goal: goal, key: key)
    return Button(action: meals) {
      VStack(alignment: .leading, spacing: 8) {
        HStack(alignment: .firstTextBaseline, spacing: 4) {
          Text(store.nutrition == nil ? "—" : RecordFormat.num(v ?? 0)).font(Typo.reading).foregroundColor(Palette.text)
            .lineLimit(1).minimumScaleFactor(0.7)
          Text("g").font(Typo.unit).foregroundColor(Palette.text2)
          Text(label).font(Typo.label).foregroundColor(Palette.text2).lineLimit(1).minimumScaleFactor(0.8)
        }
        BulletGauge(value: v, max: top, target: (goal ?? 0) > 0 ? goal!...goal! : nil, step: top / 10, tone: .text)
      }
      .frame(maxWidth: .infinity, minHeight: 59, alignment: .topLeading)
      .contentShape(Rectangle())
    }
    .buttonStyle(PressScale())
    .accessibilityElement(children: .ignore)
    .accessibilityLabel("\(label) \(RecordFormat.num(v ?? 0)) g")
    .accessibilityAddTraits(.isButton)
  }

  // MARK: faces: 快记 · 饮水 · 食谱

  private var faces: some View {
    HStack(spacing: 8) {
      if store.isToday, let t = MealTemplate.byUse(store.templates).first { quickFace(t) }
      if let w = store.water { waterFace(w) }
      if !store.recipes.isEmpty { recipesFace }
    }
  }

  private func quickFace(_ t: MealTemplate) -> some View {
    Button(action: quick) {
      Face(icon: .zap, label: "\(tr("快记", "Quick")) \(t.name)", number: RecordFormat.num(t.totals.kcal), unit: "kcal") {
        SplitBar(shares: RecordGlance.energySplit(t.totals))
      }
    }
    .buttonStyle(PressScale())
  }

  private func waterFace(_ w: WaterDay) -> some View {
    let goal = w.goal ?? 0
    let top = goal > 0 ? max(goal * RecordParams.goalHeadroom, w.total) : max(3000, w.total * RecordParams.goalHeadroom)
    return Button(action: water) {
      Face(icon: .droplet, label: tr("饮水", "Water"), number: RecordFormat.num(w.total), unit: "ml") {
        BulletGauge(value: w.total, max: top, target: goal > 0 ? goal...goal : nil, step: top / 5, tone: .text)
      }
    }
    .buttonStyle(PressScale())
  }

  private var recipesFace: some View {
    let can = store.recipes.filter(\.canMake).count
    return Button(action: recipes) {
      Face(icon: .bookOpen, label: tr("食谱", "Recipes"), number: "\(can)", unit: tr("道能做", "ready")) {
        RecipeTicks(recipes: Array(store.recipes.prefix(RecordParams.recipeTicks)))
      }
    }
    .buttonStyle(PressScale())
  }
}

/// A meal's energy split as three capsules (protein · carbs · fat), ink in three shades.
private struct SplitBar: View {
  let shares: [Double]

  var body: some View {
    GeometryReader { g in
      let gap: CGFloat = 4, w = g.size.width - gap * CGFloat(max(0, shares.count - 1))
      HStack(spacing: gap) {
        ForEach(Array(shares.enumerated()), id: \.offset) { i, s in
          Capsule().fill(Palette.text.opacity([0.95, 0.6, 0.32][min(i, 2)])).frame(width: max(4, w * CGFloat(s)), height: 6)
        }
      }
      .frame(height: g.size.height, alignment: .bottom)
    }
    .accessibilityHidden(true)
  }
}

/// One tick per recipe: tall when it can be made with what is at home.
private struct RecipeTicks: View {
  let recipes: [Recipe]

  var body: some View {
    HStack(alignment: .bottom, spacing: 3) {
      ForEach(Array(recipes.enumerated()), id: \.offset) { _, r in
        Capsule().fill(r.canMake ? Palette.text : Palette.past).frame(width: 2, height: r.canMake ? 22 : 10)
      }
      Spacer(minLength: 0)
    }
    .frame(height: 26, alignment: .bottom)
    .accessibilityHidden(true)
  }
}
