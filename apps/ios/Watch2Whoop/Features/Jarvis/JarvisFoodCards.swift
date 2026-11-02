import SwiftUI

/// Muscle load (ai/cards.py `_muscle`) and the diet cards (ai/cards_food.py). A row's button sends the app's own sentence
/// built from that row's fields (ai.js ROW), never model text; a recipe row opens that recipe's page.

// MARK: - muscle load

struct MuscleJarvisCard: View {
  let c: JSONValue

  /// The card's rows (one range) in the Record tab's model, so the regions colour as they do there.
  static func load(_ c: JSONValue) -> (MuscleLoad, MuscleLoad.Range) {
    let r = MuscleLoad.Range(rawValue: c["range"]?.string ?? "") ?? .week
    let muscles = (c["muscles"]?.array ?? []).map { m in
      MuscleLoad.Muscle(key: m["key"]?.string ?? "", name: m["name"]?.string ?? "", sets: [r: m["sets"]?.double ?? 0], volume: [r: m["kg"]?.double ?? 0],
                        status: [r: m["status"]?.string ?? "none"], low: nil, high: nil, trainedToday: m["today"]?.bool ?? false,
                        sportMin: [r: m["sport_min"]?.double ?? 0], prevWeek: nil)
    }
    return (MuscleLoad(sex: c["sex"]?.string ?? "male", muscles: muscles, sports: [:], unmatched: []), r)
  }

  /// 偏少 / 没练 by name (at most six), as the page's card has them.
  static func low(_ c: JSONValue) -> [String] {
    (c["muscles"]?.array ?? []).filter { ["none", "low"].contains($0["status"]?.string ?? "") }.compactMap { $0["name"]?.string }
  }

  var body: some View {
    let loaded = Self.load(c), m = loaded.0, range = loaded.1
    let regions = m.regions(range), low = Self.low(c)
    let sports = (c["sports"]?.array ?? []).compactMap { s -> String? in
      guard let n = s["name"]?.string, let v = s["min"]?.double else { return nil }
      return tr("\(n) \(JarvisCards.num(v)) 分钟", "\(n) \(JarvisCards.num(v)) min")
    }
    VStack(alignment: .leading, spacing: 8) {
      CardHead(title: JarvisCards.title("muscle"), badge: range.title)
      HStack(spacing: 8) {
        ForEach([true, false], id: \.self) { front in
          VStack(spacing: 2) {
            if let side = MuscleBodies.shared.side(sex: m.sex, front: front) {
              MuscleBodyView(side: side, regions: regions).frame(height: 200)
            }
            Text(front ? tr("正面", "Front") : tr("背面", "Back")).font(Typo.label).foregroundColor(Palette.text2)
          }
          .frame(maxWidth: .infinity)
        }
      }
      if !low.isEmpty {
        Text(tr("偏少 / 没练", "Low / none") + tr("：", ": ") + low.prefix(6).joined(separator: tr("、", ", ")))
          .font(Typo.caption).foregroundColor(Palette.text2).fixedSize(horizontal: false, vertical: true)
      }
      if !sports.isEmpty {
        Text(tr("运动", "Sports") + tr("：", ": ") + sports.joined(separator: " · ")).font(Typo.caption).foregroundColor(Palette.text2)
      }
      if c["sex"]?.string == nil { Text(tr("按男性图显示", "Male figure shown")).font(Typo.label).foregroundColor(Palette.text3) }
    }
  }
}

// MARK: - goals

struct GoalsJarvisCard: View {
  let c: JSONValue

  var body: some View {
    let t = c["totals"], g = c["goal"]
    VStack(alignment: .leading, spacing: 10) {
      CardHead(title: JarvisCards.title("goals"), date: c["date"]?.string, badge: c["name"]?.string)
      if let g, !g.isNull {
        HStack(spacing: 4) {
          GoalRing(label: tr("热量", "Energy"), value: t?["calories"]?.double, goal: g["calories"]?.double, unit: "kcal", tone: .train)
          GoalRing(label: tr("蛋白", "Protein"), value: t?["protein_g"]?.double, goal: g["protein_g"]?.double, unit: "g", tone: .good)
        }
        CardMeter(label: tr("碳水", "Carbs"), value: t?["carbs_g"]?.double, goal: g["carbs_g"]?.double, unit: "g")
        CardMeter(label: tr("脂肪", "Fat"), value: t?["fat_g"]?.double, goal: g["fat_g"]?.double, unit: "g")
        cap(tr("饱和脂肪上限", "Sat. fat cap"), t?["saturated_fat_g"]?.double, g["sat_fat_max_g"]?.double)
        cap(tr("添加糖上限", "Added sugar cap"), t?["added_sugar_g"]?.double, g["added_sugar_max_g"]?.double)
        cap(tr("纤维", "Fibre"), t?["fiber_g"]?.double, g["fiber_min_g"]?.double)
        if c["logged"]?.bool != true { Text(tr("这天还没有记录", "Nothing logged that day")).font(Typo.caption).foregroundColor(Palette.text2) }
      } else {
        Text(tr("还没有设饮食目标", "No nutrition targets yet")).font(Typo.caption).foregroundColor(Palette.text2)
      }
    }
  }

  /// Only when that target is set (ai.js goalsBody).
  @ViewBuilder private func cap(_ label: String, _ v: Double?, _ max: Double?) -> some View {
    if let max { CardMeter(label: label, value: v, goal: max, unit: "g") }
  }
}

// MARK: - recipe rows

/// The sentence a row's button sends (ai.js ROW).
enum JarvisRows {
  static func recipe(_ r: JSONValue, slot: String?) -> String {
    let name = r["name"]?.string ?? "", id = r["id"]?.string ?? ""
    if let slot, !slot.isEmpty {
      let meal = MealSlot(slot).title
      return tr("按食谱「\(name)」记一份\(meal)（食谱 id \(id)）", "Log one serving of “\(name)” as \(meal) (recipe id \(id))")
    }
    return tr("按食谱「\(name)」记一份（食谱 id \(id)）", "Log one serving of “\(name)” (recipe id \(id))")
  }

  static func template(_ t: JSONValue) -> String {
    let name = t["name"]?.string ?? "", id = t["id"]?.string ?? ""
    return tr("按快捷模板「\(name)」记一次（模板 id \(id)）", "Log the quick meal “\(name)” once (template id \(id))")
  }

  static func templateLine(_ t: JSONValue) -> String {
    let protein = tr("蛋白", "Protein")
    return "\(MealSlot(t["meal"]?.string).title) · \(JarvisCards.num(t["kcal"]?.double)) kcal · \(protein) \(JarvisCards.num(t["protein_g"]?.double)) g"
  }

  /// kcal (with the rice it needs) · protein · minutes (app.js recipeCard).
  static func meta(_ r: JSONValue) -> String {
    var out: [String] = []
    if let k = r["kcal"]?.double { out.append("\(JarvisCards.num(k)) kcal" + (r["with_staple"]?.bool == true ? " " + tr("含饭", "with rice") : "")) }
    if let p = r["protein_g"]?.double { out.append(tr("蛋白", "Protein") + " \(JarvisCards.num(p)) g") }
    if let m = r["min"]?.int { out.append("\(m)′") }
    return out.joined(separator: " · ")
  }

  static func badgeTone(_ t: String?) -> Tone {
    switch t {
    case "good": return .good
    case "warn": return .mid
    case "bad": return .bad
    default: return .text
    }
  }

  static func badges(_ r: JSONValue, limit: Int = 2) -> [(String, Tone)] {
    let all: [(String, Tone)] = (r["badges"]?.array ?? []).compactMap { b in
      guard let label = b["label"]?.string else { return nil }
      return (label, badgeTone(b["tone"]?.string))
    }
    return Array(all.prefix(limit))
  }
}

/// A recipe row: name (★ when a favourite), its numbers, its badges; tapping opens the recipe.
private struct RecipeRow: View {
  let r: JSONValue
  let open: (String, JSONValue) -> Void

  var body: some View {
    Button { open("recipe", r) } label: {
      VStack(alignment: .leading, spacing: 3) {
        HStack(alignment: .top, spacing: 4) {
          Text(r["name"]?.string ?? "").font(Typo.bodyStrong).foregroundColor(Palette.text).lineLimit(2).multilineTextAlignment(.leading)
          if r["favorite"]?.bool == true { Icon(.star, size: 13).foregroundColor(Palette.mid).padding(.top, 2) }
          Spacer(minLength: 0)
        }
        let meta = JarvisRows.meta(r)
        if !meta.isEmpty { Text(meta).font(Typo.caption.monospacedDigit()).foregroundColor(Palette.text2).lineLimit(1).minimumScaleFactor(0.85) }
        let badges = JarvisRows.badges(r)
        if !badges.isEmpty {
          HStack(spacing: 4) { ForEach(Array(badges.enumerated()), id: \.offset) { _, b in ToneBadge(text: b.0, tone: b.1) } }
        }
      }
      .contentShape(Rectangle())
    }
    .buttonStyle(PressScale())
  }
}

/// A small send button on a row (ai.js `.ai-q`): off while Jarvis is answering.
struct RowSendButton: View {
  let title: String
  let busy: Bool
  let run: () -> Void

  var body: some View {
    Button(title, action: run)
      .font(.system(size: 12, weight: .medium)).foregroundColor(Palette.text).lineLimit(1)
      .padding(.vertical, 5).padding(.horizontal, 10)
      .overlay(Capsule().strokeBorder(Palette.text.opacity(0.35), lineWidth: 1))
      .disabled(busy)
  }
}

// MARK: - suggest

struct SuggestJarvisCard: View {
  let c: JSONValue
  let busy: Bool
  let send: (String) -> Void
  let open: (String, JSONValue) -> Void

  /// Slots with something to say (ai.js suggestBody).
  static func slots(_ c: JSONValue) -> [JSONValue] {
    (c["slots"]?.array ?? []).filter { !($0["items"]?.array ?? []).isEmpty || ($0["logged"].map { !$0.isNull } ?? false) }
  }

  var body: some View {
    let slots = Self.slots(c)
    VStack(alignment: .leading, spacing: 10) {
      VStack(alignment: .leading, spacing: 2) {
        CardHead(title: JarvisCards.title("suggest"), date: c["date"]?.string)
        if let k = c["remaining"]?["kcal"]?.double {
          let left = JarvisCards.num(max(0, k)), p = JarvisCards.num(max(0, c["remaining"]?["protein_g"]?.double ?? 0))
          Text(tr("余 \(left) kcal · \(p) g 蛋白", "\(left) kcal · \(p) g protein left")).font(Typo.caption.monospacedDigit()).foregroundColor(Palette.text2)
        }
      }
      if slots.isEmpty { Text(tr("没有合适的菜", "No suitable dish")).font(Typo.caption).foregroundColor(Palette.text2) }
      ForEach(Array(slots.enumerated()), id: \.offset) { _, s in slot(s) }
    }
  }

  private func slot(_ s: JSONValue) -> some View {
    let key = s["slot"]?.string
    return VStack(alignment: .leading, spacing: 8) {
      HStack(alignment: .firstTextBaseline) {
        Text(MealSlot(key).title).font(Typo.section).foregroundColor(Palette.text2)
        Spacer(minLength: 4)
        Text(line(s)).font(Typo.label.monospacedDigit()).foregroundColor(Palette.text3)
      }
      ForEach(Array((s["items"]?.array ?? []).enumerated()), id: \.offset) { _, r in
        HStack(alignment: .center, spacing: 8) {
          RecipeRow(r: r, open: open)
          RowSendButton(title: tr("记下这餐", "Log this meal"), busy: busy) { send(JarvisRows.recipe(r, slot: key)) }
        }
      }
    }
  }

  private func line(_ s: JSONValue) -> String {
    if let k = s["logged"]?["kcal"]?.double { let v = JarvisCards.num(k); return tr("已吃 \(v) kcal", "Eaten \(v) kcal") }
    if let k = s["target"]?["kcal"]?.double, k > 0 { return "≈ \(JarvisCards.num(k)) kcal · \(JarvisCards.num(s["target"]?["protein_g"]?.double)) g" }
    return ""
  }
}

// MARK: - one recipe

struct RecipeJarvisCard: View {
  let c: JSONValue

  static func meta(_ c: JSONValue) -> String {
    var out: [String] = []
    if let m = c["min"]?.int { out.append("\(m)′") }
    if let n = c["steps"]?.int, n > 0 { out.append(tr("\(n) 步", "\(n) steps")) }
    if let p = c["per_serving"]?.string, !p.isEmpty { out.append(tr("每份", "Per serving") + " \(p)") }
    return out.joined(separator: " · ")
  }

  var body: some View {
    let meta = Self.meta(c), ings = c["ingredients"]?.array ?? [], badges = JarvisRows.badges(c, limit: 4)
    VStack(alignment: .leading, spacing: 8) {
      HStack(alignment: .top, spacing: 4) {
        Text(c["name"]?.string ?? JarvisCards.title("recipe")).font(Typo.bodyStrong).foregroundColor(Palette.text)
        if c["favorite"]?.bool == true { Icon(.star, size: 14).foregroundColor(Palette.mid).padding(.top, 2) }
        Spacer(minLength: 0)
      }
      if !meta.isEmpty { Text(meta).font(Typo.caption.monospacedDigit()).foregroundColor(Palette.text2) }
      if !badges.isEmpty { FlowRow(spacing: 6) { ForEach(Array(badges.enumerated()), id: \.offset) { _, b in ToneBadge(text: b.0, tone: b.1) } } }
      HStack(spacing: 0) {
        cell(tr("热量", "Energy"), c["kcal"]?.double, "kcal")
        cell(tr("蛋白", "Protein"), c["protein_g"]?.double, "g")
        cell(tr("碳水", "Carbs"), c["carbs_g"]?.double, "g")
        cell(tr("脂肪", "Fat"), c["fat_g"]?.double, "g")
      }
      if !ings.isEmpty {
        Text(tr("每份食材", "Per serving")).font(Typo.label).foregroundColor(Palette.text2).padding(.top, 2)
        ForEach(Array(ings.enumerated()), id: \.offset) { _, x in
          HStack {
            Text(x["food"]?.string ?? "").font(Typo.caption).foregroundColor(Palette.text).lineLimit(1)
            Spacer(minLength: 4)
            if let g = x["grams"]?.double { Text("\(JarvisCards.num(g)) g").font(Typo.caption.monospacedDigit()).foregroundColor(Palette.text2) }
          }
        }
        if let n = c["more_ingredients"]?.int, n > 0 { Text(tr("还有 \(n) 样", "\(n) more")).font(Typo.label).foregroundColor(Palette.text3) }
      }
    }
  }

  private func cell(_ l: String, _ v: Double?, _ u: String) -> some View {
    VStack(alignment: .leading, spacing: 2) {
      Text(l).font(Typo.label).foregroundColor(Palette.text2)
      HStack(alignment: .firstTextBaseline, spacing: 2) {
        Text(JarvisCards.num(v)).font(Typo.bodyStrong.monospacedDigit()).foregroundColor(Palette.text)
        Text(u).font(Typo.unit).foregroundColor(Palette.text2)
      }
    }
    .frame(maxWidth: .infinity, alignment: .leading)
  }
}

// MARK: - recipe list

struct RecipesJarvisCard: View {
  let c: JSONValue
  let open: (String, JSONValue) -> Void

  var body: some View {
    let items = c["items"]?.array ?? [], slot = c["slot"]?.string, total = c["total"]?.int ?? 0
    let title = slot.map { MealSlot($0).title } ?? JarvisCards.title("recipes")
    VStack(alignment: .leading, spacing: 10) {
      CardHead(title: title, badge: tr("\(total) 道", "\(total) dishes"))
      if items.isEmpty { Text(tr("暂无食谱", "No recipes yet")).font(Typo.caption).foregroundColor(Palette.text2) }
      ForEach(Array(items.enumerated()), id: \.offset) { _, r in RecipeRow(r: r, open: open) }
    }
  }
}

// MARK: - quick meals

struct TemplatesJarvisCard: View {
  let c: JSONValue
  let busy: Bool
  let send: (String) -> Void

  var body: some View {
    let items = c["items"]?.array ?? [], total = c["total"]?.int ?? 0
    VStack(alignment: .leading, spacing: 10) {
      CardHead(title: JarvisCards.title("templates"), badge: tr("\(total) 个", "\(total)"))
      if items.isEmpty { Text(tr("还没有快捷记餐模板", "No quick meals yet")).font(Typo.caption).foregroundColor(Palette.text2) }
      ForEach(Array(items.enumerated()), id: \.offset) { _, t in
        HStack(spacing: 8) {
          VStack(alignment: .leading, spacing: 2) {
            Text(t["name"]?.string ?? "").font(Typo.bodyStrong).foregroundColor(Palette.text).lineLimit(1)
            Text(JarvisRows.templateLine(t)).font(Typo.caption.monospacedDigit()).foregroundColor(Palette.text2).lineLimit(1).minimumScaleFactor(0.85)
          }
          Spacer(minLength: 4)
          if t["logged"]?.bool == true {
            Text(tr("已记 ✓", "Logged ✓")).font(Typo.label).foregroundColor(Palette.text2)
          } else {
            RowSendButton(title: t["needs_confirmation"]?.bool == true ? tr("确认克数", "Confirm grams") : tr("记一次", "Log it"), busy: busy) {
              send(JarvisRows.template(t))
            }
          }
        }
      }
    }
  }
}

// MARK: - shopping

struct ShoppingJarvisCard: View {
  let c: JSONValue

  /// To buy first (ai.js shoppingBody), in the list's order.
  static func ordered(_ c: JSONValue) -> [JSONValue] {
    let all = c["items"]?.array ?? []
    return all.filter { $0["bought"]?.bool != true } + all.filter { $0["bought"]?.bool == true }
  }

  /// `≈ AUD 12.50`, per currency, in a stable order.
  static func cost(_ c: JSONValue) -> String {
    (c["est_total"]?.object ?? [:]).sorted { $0.key < $1.key }.compactMap { cur, v in v.double.map { "≈ \(cur) \(JarvisCards.num($0, 2))" } }.joined(separator: " · ")
  }

  var body: some View {
    let bought = c["progress"]?["bought"]?.double ?? 0, total = c["progress"]?["total"]?.double ?? 0
    let items = Self.ordered(c), shown = items.prefix(12), cost = Self.cost(c)
    let warnings = (c["warnings"]?.array ?? []).compactMap(\.string)
    let more = items.count - shown.count + (c["more"]?.int ?? 0)
    VStack(alignment: .leading, spacing: 8) {
      CardHead(title: JarvisCards.title("shopping"), date: c["week_start"]?.string)
      if total > 0 {
        CardMeter(label: tr("已买", "Bought"), value: bought, goal: total, unit: "")
        if !cost.isEmpty { Text(cost).font(Typo.caption.monospacedDigit()).foregroundColor(Palette.text2) }
        if !warnings.isEmpty { FlowRow(spacing: 6) { ForEach(warnings, id: \.self) { ToneBadge(text: $0, tone: .mid) } } }
        ForEach(Array(shown.enumerated()), id: \.offset) { _, it in
          let done = it["bought"]?.bool == true
          HStack(spacing: 8) {
            if done { Icon(.check, size: 16).foregroundColor(Palette.text3) }
            else { Circle().strokeBorder(Palette.text3, lineWidth: 1).frame(width: 14, height: 14).padding(1) }
            Text(it["name"]?.string ?? "").font(Typo.body).foregroundColor(done ? Palette.text3 : Palette.text).strikethrough(done).lineLimit(1)
            if let cat = it["category_label"]?.string { Text(cat).font(Typo.label).foregroundColor(Palette.text3).lineLimit(1) }
            Spacer(minLength: 4)
            Text(it["qty"]?.string ?? "").font(Typo.caption.monospacedDigit()).foregroundColor(Palette.text2).lineLimit(1)
          }
        }
        if more > 0 { Text(tr("还有 \(more) 样", "\(more) more")).font(Typo.label).foregroundColor(Palette.text3) }
      } else {
        Text(tr("这周还没有采购清单", "No shopping list this week")).font(Typo.caption).foregroundColor(Palette.text2)
      }
    }
  }
}
