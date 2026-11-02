import SwiftUI

/// 「每餐推荐」: `/recipes/meal-plan?date=` (issue #43). Each meal not logged yet gets 2 recipes, ranked by the server (taste,
/// pantry, health caps). Same reading as the page (app.js mealPlanHtml).
struct MealPlan: Equatable {
  struct Slot: Equatable, Identifiable {
    var meal: MealSlot
    /// What the meal should come to; 0 = the day's target is reached.
    var targetKcal: Double?
    var targetProtein: Double?
    var suggestions: [Suggestion]
    var id: String { meal.rawValue }
  }

  struct Suggestion: Equatable, Identifiable {
    var recipe: Recipe
    /// The meal as suggested: with the rice it needs (`with_staple`).
    var kcal: Double?
    var protein: Double?
    var withStaple: Bool
    var labels: [(String, Tone)]
    var id: String { recipe.id }

    static func == (a: Suggestion, b: Suggestion) -> Bool {
      a.recipe == b.recipe && a.kcal == b.kcal && a.protein == b.protein && a.withStaple == b.withStaple && a.labels.map(\.0) == b.labels.map(\.0)
    }

    var meta: String {
      [kcal.map { "\(RecordFormat.num($0)) kcal" + (withStaple ? " " + tr("含饭", "with rice") : "") },
       protein.map { "\(RecordFormat.num($0)) g" }, recipe.minutes.map { "\($0)′" }].compactMap { $0 }.joined(separator: " · ")
    }
  }

  /// The food log cannot be read right now: the server's short label, shown with a retry.
  var unavailable: String?
  var remainingKcal: Double?
  var remainingProtein: Double?
  var slots: [Slot]

  static func parse(_ j: [String: Any]?) -> MealPlan? {
    guard let j else { return nil }
    if j["unavailable"] != nil {
      return MealPlan(unavailable: (j["label"] as? String) ?? tr("饮食记录暂时读不到", "Food log unavailable"), slots: [])
    }
    let n = RecordFormat.double
    let slots = ((j["slots"] as? [[String: Any]]) ?? []).compactMap { s -> Slot? in
      let xs = ((s["suggestions"] as? [[String: Any]]) ?? []).compactMap { x -> Suggestion? in
        guard let r = Recipe.parse(x) else { return nil }
        let labels = ((x["labels"] as? [[String: Any]]) ?? []).compactMap { l -> (String, Tone)? in
          (l["label"] as? String).map { ($0, Recipe.tone(l["tone"] as? String)) }
        }
        return Suggestion(recipe: r, kcal: n(x["meal_kcal"]) ?? r.perServing.kcal, protein: n(x["meal_protein_g"]) ?? r.perServing.protein,
                          withStaple: (x["with_staple"] as? Bool) ?? false, labels: labels)
      }
      guard !xs.isEmpty else { return nil }
      let t = s["target"] as? [String: Any]
      return Slot(meal: MealSlot(s["slot"] as? String), targetKcal: n(t?["kcal"]), targetProtein: n(t?["protein_g"]), suggestions: xs)
    }
    let r = j["remaining"] as? [String: Any]
    return MealPlan(remainingKcal: n(r?["kcal"]), remainingProtein: n(r?["protein_g"]), slots: slots)
  }

  var isEmpty: Bool { unavailable == nil && slots.isEmpty }
}

/// The section on the 饮食 page: for each meal its target and two recipe cards; tapping one opens the recipe on that meal.
struct MealPlanSection: View {
  let plan: MealPlan
  let retry: () -> Void
  let open: (Recipe, MealSlot) -> Void

  var body: some View {
    VStack(alignment: .leading, spacing: 10) {
      HStack(alignment: .firstTextBaseline) {
        SectionHeader(title: tr("每餐推荐", "Meal ideas"))
        if plan.unavailable == nil, let k = plan.remainingKcal {
          let kcal = RecordFormat.num(max(0, k)), protein = RecordFormat.num(max(0, plan.remainingProtein ?? 0))
          Text(tr("余 \(kcal) kcal · \(protein) g 蛋白", "\(kcal) kcal · \(protein) g protein left"))
            .font(Typo.caption.monospacedDigit()).foregroundColor(Palette.text2).lineLimit(1).minimumScaleFactor(0.8)
        }
      }
      if let u = plan.unavailable {
        Card {
          VStack(spacing: 8) {
            Text(u).font(Typo.caption).foregroundColor(Palette.text2)
            Button(tr("重试", "Retry"), action: retry).font(.system(size: 14, weight: .medium)).foregroundColor(Palette.text)
          }
          .frame(maxWidth: .infinity)
        }
      }
      ForEach(plan.slots) { s in
        VStack(alignment: .leading, spacing: 8) {
          HStack(alignment: .firstTextBaseline) {
            Text(s.meal.title).font(Typo.bodyStrong).foregroundColor(Palette.text)
            Spacer()
            if let k = s.targetKcal {
              Text(k > 0 ? "≈ \(RecordFormat.num(k)) kcal · \(RecordFormat.num(s.targetProtein)) g" : tr("已到目标", "Target reached"))
                .font(Typo.caption.monospacedDigit()).foregroundColor(Palette.text2)
            }
          }
          LazyVGrid(columns: [GridItem(.flexible(), spacing: 10), GridItem(.flexible(), spacing: 10)], spacing: 10) {
            ForEach(s.suggestions) { x in
              Button { open(x.recipe, s.meal) } label: { SuggestionCard(x: x) }.buttonStyle(PressScale())
            }
          }
        }
      }
    }
  }
}

/// One suggested recipe: name (★ when a favourite), the meal's nutrition, short labels from the server.
private struct SuggestionCard: View {
  let x: MealPlan.Suggestion

  var body: some View {
    VStack(alignment: .leading, spacing: 6) {
      HStack(alignment: .top, spacing: 4) {
        Text(x.recipe.name).font(Typo.bodyStrong).foregroundColor(Palette.text).lineLimit(2).multilineTextAlignment(.leading)
        Spacer(minLength: 0)
        if x.recipe.favorite { Icon(.star, size: 14).foregroundColor(Palette.mid) }
      }
      Text(x.meta).font(Typo.caption.monospacedDigit()).foregroundColor(Palette.text2).lineLimit(2).multilineTextAlignment(.leading)
      if !x.labels.isEmpty {
        HStack(spacing: 4) { ForEach(x.labels.prefix(2), id: \.0) { Badge(label: $0.0, tone: $0.1) } }
      }
    }
    .frame(maxWidth: .infinity, minHeight: 84, alignment: .topLeading)
    .padding(12)
    .background(RoundedRectangle(cornerRadius: Metrics.tileRadius, style: .continuous).fill(Palette.surface))
  }
}
