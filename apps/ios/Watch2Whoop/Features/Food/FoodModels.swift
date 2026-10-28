import Foundation

/// The 饮食 tab's data: server views (documents) with the pending writes laid over them. Everything here is pure, so
/// tests feed it JSON (FoodTests). Field names are the server's (`/today` nutrition block, `/meal-templates`, `/recipes`,
/// `/water`, `/grocery/list`).

/// Document keys of the food pages (table `docs`). The day's intake is the nutrition block of N3's `today:<day>`.
enum FoodDocs {
  static func today(_ day: String) -> String { DocKey.today(day) }
  static func water(_ day: String) -> String { "water:\(day)" }
  static let templates = "food:templates"
  static let recipes = "food:recipes"
  static func recipe(_ id: String) -> String { "food:recipe:\(id)" }
  static func grocery(_ week: String) -> String { "grocery:list:\(week)" }

  /// Pending kinds the food pages lay over the documents.
  static let kinds = [FoodKind.water, FoodKind.waterDelete, FoodKind.foodDelete, FoodKind.portion, FoodKind.mealLog,
                      FoodKind.recipeLog, FoodKind.grocery]
}

/// Kinds of the food pages' local rows and queue entries.
enum FoodKind {
  static let water = "water"
  static let waterDelete = "water.delete"
  static let foodDelete = "food.delete"
  static let portion = "food.portion"
  static let mealLog = "meal_log"
  static let recipeLog = "recipe_log"
  static let grocery = "grocery.item"
}

/// The four meals, in the page's order. Food entries say `snacks`, recipes `snack`.
enum MealSlot: String, CaseIterable, Identifiable {
  case breakfast, lunch, dinner, snack
  var id: String { rawValue }

  init(_ raw: String?) {
    switch (raw ?? "").lowercased() {
    case "breakfast": self = .breakfast
    case "lunch": self = .lunch
    case "dinner": self = .dinner
    default: self = .snack
    }
  }

  var title: String {
    switch self {
    case .breakfast: return tr("早餐", "Breakfast")
    case .lunch: return tr("午餐", "Lunch")
    case .dinner: return tr("晚餐", "Dinner")
    case .snack: return tr("加餐", "Snacks")
    }
  }

  /// The food log's name of the meal (`meal_type` / template `meal`).
  var logName: String { self == .snack ? "snacks" : rawValue }

  /// The meal for now (the page's defaultMeal()).
  static func now(_ date: Date = Date()) -> MealSlot {
    let c = Calendar.current.dateComponents([.hour, .minute], from: date)
    let h = Double(c.hour ?? 12) + Double(c.minute ?? 0) / 60
    return h < 10.5 ? .breakfast : h < 15 ? .lunch : h < 21 ? .dinner : .snack
  }
}

struct Macros: Equatable {
  var kcal: Double?
  var protein: Double?
  var carbs: Double?
  var fat: Double?

  init(kcal: Double? = nil, protein: Double? = nil, carbs: Double? = nil, fat: Double? = nil) {
    self.kcal = kcal
    self.protein = protein
    self.carbs = carbs
    self.fat = fat
  }

  /// From a server object with `calories|kcal`, `protein_g`, `carbs_g`, `fat_g`.
  init(_ j: [String: Any]?) {
    let n = RecordFormat.double
    kcal = n(j?["calories"]) ?? n(j?["kcal"])
    protein = n(j?["protein_g"])
    carbs = n(j?["carbs_g"])
    fat = n(j?["fat_g"])
  }

  static func + (a: Macros, b: Macros) -> Macros {
    func add(_ x: Double?, _ y: Double?) -> Double? { x == nil && y == nil ? nil : (x ?? 0) + (y ?? 0) }
    return Macros(kcal: add(a.kcal, b.kcal), protein: add(a.protein, b.protein), carbs: add(a.carbs, b.carbs), fat: add(a.fat, b.fat))
  }

  func scaled(_ f: Double) -> Macros {
    Macros(kcal: kcal.map { $0 * f }, protein: protein.map { $0 * f }, carbs: carbs.map { $0 * f }, fat: fat.map { $0 * f })
  }

  var asJSON: [String: Any] {
    var o: [String: Any] = [:]
    if let kcal { o["calories"] = kcal }
    if let protein { o["protein_g"] = protein }
    if let carbs { o["carbs_g"] = carbs }
    if let fat { o["fat_g"] = fat }
    return o
  }
}

/// One food entry of the day (server, or a pending log not on the server yet).
struct FoodEntry: Equatable, Identifiable {
  var id: String
  var date: String
  var time: String?
  var meal: MealSlot
  var food: String
  var quantity: Double
  var unit: String?
  var macros: Macros
  var extras: [(String, String)] = []
  /// Pending write that made or changes this row (its mark is drawn next to it).
  var pending: PendingRecord?
  /// A row the server has not given an id yet (a quick meal / recipe being logged): read-only.
  var isLocal = false

  static func == (a: FoodEntry, b: FoodEntry) -> Bool {
    a.id == b.id && a.date == b.date && a.food == b.food && a.quantity == b.quantity && a.macros == b.macros
      && a.pending == b.pending && a.meal == b.meal && a.isLocal == b.isLocal
  }

  static func parse(_ e: [String: Any], day: String) -> FoodEntry? {
    guard let id = RecordFormat.string(e["id"]) else { return nil }
    let n = RecordFormat.double
    var extras: [(String, String)] = []
    let optional: [(String, String, String, Int)] = [
      ("saturated_fat_g", tr("饱和脂肪", "Saturated fat"), "g", 1), ("fiber_g", tr("纤维", "Fibre"), "g", 1),
      ("sugar_g", tr("糖", "Sugar"), "g", 1), ("added_sugar_g", tr("添加糖", "Added sugar"), "g", 1), ("sodium_mg", tr("钠", "Sodium"), "mg", 0),
    ]
    for (key, label, unit, digits) in optional {
      if let v = n(e[key]) { extras.append((label, "\(RecordFormat.num(v, digits)) \(unit)")) }
    }
    return FoodEntry(id: id, date: e["date"] as? String ?? day, time: e["time"] as? String, meal: MealSlot(e["meal_type"] as? String),
                     food: e["food"] as? String ?? "—", quantity: n(e["quantity"]) ?? 1, unit: e["unit"] as? String,
                     macros: Macros(e), extras: extras)
  }
}

/// The 「今天吃了」 block: targets, what was eaten, the entries by meal.
struct NutritionDay: Equatable {
  var date: String
  var goal: Macros
  var goalName: String?
  var intake: Macros
  var entries: [FoodEntry]
  /// The day's food log cannot be read right now (server message).
  var error: String?

  func meals() -> [(MealSlot, [FoodEntry])] {
    MealSlot.allCases.compactMap { m in
      let xs = entries.filter { $0.meal == m }
      return xs.isEmpty ? nil : (m, xs)
    }
  }

  /// From the today document's `nutrition`, then the pending writes of that day: entries being deleted are gone,
  /// portions being changed are scaled, quick meals / recipes being logged are there as one row each. The intake is
  /// recomputed from the rows when anything pending touches the day.
  static func build(today: [String: Any]?, day: String, pending: [PendingRecord]) -> NutritionDay? {
    guard let n = today?["nutrition"] as? [String: Any], (n["code"] as? String) != "forbidden" else {
      return pending.contains { $0.date == day } ? NutritionDay(date: day, goal: Macros(), goalName: nil, intake: Macros(), entries: [])
        .applying(pending, day: day) : nil
    }
    let goalJ = n["goal"] as? [String: Any]
    var d = NutritionDay(date: day, goal: Macros(goalJ), goalName: goalJ?["name"] as? String, intake: Macros(n["intake"] as? [String: Any]),
                         entries: ((n["entries"] as? [[String: Any]]) ?? []).compactMap { FoodEntry.parse($0, day: day) },
                         error: n["error"] as? String)
    d = d.applying(pending, day: day)
    return d
  }

  func applying(_ pending: [PendingRecord], day: String) -> NutritionDay {
    let mine = pending.filter { $0.date == day }
    guard !mine.isEmpty else { return self }
    var out = self
    for p in mine {
      let j = p.json
      switch p.kind {
      case FoodKind.foodDelete:
        out.entries.removeAll { $0.id == RecordFormat.string(j["id"]) }
      case FoodKind.portion:
        guard let id = RecordFormat.string(j["id"]), let q = RecordFormat.double(j["quantity"]),
              let i = out.entries.firstIndex(where: { $0.id == id }) else { continue }
        let old = out.entries[i].quantity > 0 ? out.entries[i].quantity : 1
        out.entries[i].macros = out.entries[i].macros.scaled(q / old)
        out.entries[i].quantity = q
        out.entries[i].pending = p
      case FoodKind.mealLog, FoodKind.recipeLog:
        out.entries.append(FoodEntry(id: "c:\(p.clientId)", date: day, time: nil, meal: MealSlot(j["meal"] as? String),
                                     food: j["name"] as? String ?? "—", quantity: RecordFormat.double(j["servings"]) ?? 1,
                                     unit: nil, macros: Macros(j), pending: p, isLocal: true))
      default:
        continue
      }
    }
    out.intake = out.entries.reduce(Macros(kcal: 0, protein: 0, carbs: 0, fat: 0)) { $0 + $1.macros }
    return out
  }
}

/// A quick-meal template (`/meal-templates`).
struct MealTemplate: Equatable, Identifiable {
  var id: Int
  var name: String
  var meal: MealSlot
  var totals: Macros
  var items: String
  /// Logged today: the logging's id (undo = DELETE /meal-logs/{id}).
  var todayLog: Int?
  /// A logging of it still in the queue.
  var pending: PendingRecord?

  static func parseList(_ list: [[String: Any]]?, pending: [PendingRecord], today: String) -> [MealTemplate] {
    (list ?? []).compactMap { t in
      guard let id = (t["id"] as? NSNumber)?.intValue, let name = t["name"] as? String else { return nil }
      let items = ((t["items"] as? [[String: Any]]) ?? []).compactMap { $0["food"] as? String }.joined(separator: " · ")
      let log = (t["today_log"] as? [String: Any]).flatMap { ($0["log_id"] as? NSNumber)?.intValue }
      let p = pending.last { $0.kind == FoodKind.mealLog && $0.date == today && (RecordFormat.double($0.json["template_id"]).map(Int.init)) == id }
      return MealTemplate(id: id, name: name, meal: MealSlot(t["meal"] as? String), totals: Macros(t["totals"] as? [String: Any]),
                          items: items, todayLog: log, pending: p)
    }
  }
}

/// A recipe in the list (`/recipes`) or its detail (`/recipes/{id}`).
struct Recipe: Equatable, Identifiable {
  struct Ingredient: Equatable {
    var food: String
    var grams: Double?
    var amount: String?
  }

  var id: String
  var name: String
  var slots: [MealSlot]
  var tags: [String]
  var perServing: Macros
  var servingLabel: String?
  var minutes: Int?
  var favorite: Bool
  var canMake: Bool
  var badges: [(String, Tone)]
  var servings: Double = 1
  var ingredients: [Ingredient] = []
  var steps: [String] = []

  static func == (a: Recipe, b: Recipe) -> Bool {
    a.id == b.id && a.name == b.name && a.favorite == b.favorite && a.canMake == b.canMake && a.perServing == b.perServing
      && a.ingredients == b.ingredients && a.steps == b.steps && a.tags == b.tags
  }

  static func parse(_ r: [String: Any]) -> Recipe? {
    guard let id = RecordFormat.string(r["id"]), let name = r["name"] as? String else { return nil }
    let prep = (r["prep_min"] as? NSNumber)?.intValue, cook = (r["cook_min"] as? NSNumber)?.intValue
    var badges: [(String, Tone)] = ((r["badges"] as? [[String: Any]]) ?? []).compactMap { b in
      guard let label = b["label"] as? String else { return nil }
      return (label, Self.tone(b["tone"] as? String))
    }
    if badges.isEmpty, let f = r["constraint_flags"] as? [String: Any] {
      switch f["purine_level"] as? String {
      case "high": badges.append((tr("嘌呤高", "High purine"), .bad))
      case "medium": badges.append((tr("嘌呤中", "Medium purine"), .mid))
      default: break
      }
      if (f["sat_fat_level"] as? String) == "high" { badges.append((tr("饱和脂肪高", "High sat fat"), .bad)) }
      if let rm = RecordFormat.double(f["red_meat_g"]), rm > 0 { badges.append((tr("红肉 \(RecordFormat.num(rm))g", "Red meat \(RecordFormat.num(rm))g"), .mid)) }
    }
    let ingredients = ((r["ingredients"] as? [[String: Any]]) ?? []).map {
      Ingredient(food: ($0["food"] as? String) ?? ($0["name"] as? String) ?? "—", grams: RecordFormat.double($0["grams"]),
                 amount: $0["amount"] as? String)
    }
    let steps = ((r["steps"] as? [Any]) ?? []).compactMap { s -> String? in
      (s as? String) ?? ((s as? [String: Any])?["text"] as? String)
    }
    return Recipe(id: id, name: name, slots: ((r["slots"] as? [String]) ?? []).map { MealSlot($0) },
                  tags: (r["tags"] as? [String]) ?? [], perServing: Macros(r), servingLabel: r["per_serving"] as? String,
                  minutes: prep.map { $0 + (cook ?? 0) } ?? cook, favorite: (r["favorite"] as? Bool) ?? false,
                  canMake: (r["can_make"] as? Bool) ?? true, badges: badges, servings: RecordFormat.double(r["servings"]) ?? 1,
                  ingredients: ingredients, steps: steps)
  }

  static func tone(_ s: String?) -> Tone {
    switch s {
    case "good": return .good
    case "warn", "mid": return .mid
    case "bad": return .bad
    default: return .text
    }
  }

  /// The list by meal (a recipe sits under its first meal), favourites first; `query` searches name, tags, ingredients.
  static func grouped(_ list: [Recipe], query: String) -> [(MealSlot, [Recipe])] {
    let q = query.trimmingCharacters(in: .whitespaces).lowercased()
    let hit = list.filter { r in
      q.isEmpty || ([r.name] + r.tags + r.ingredients.map(\.food)).contains { $0.lowercased().contains(q) }
    }
    return MealSlot.allCases.compactMap { m in
      let xs = hit.filter { ($0.slots.first ?? .lunch) == m }.sorted { ($0.favorite ? 0 : 1) < ($1.favorite ? 0 : 1) }
      return xs.isEmpty ? nil : (m, xs)
    }
  }
}

/// One day's water (`/water?date=`), with drinks still in the queue counted in.
struct WaterDay: Equatable {
  struct Drink: Equatable, Identifiable {
    var id: String
    var ml: Double
    var at: String?
    var pending: PendingRecord?
  }

  var date: String
  var total: Double
  var goal: Double?
  var quick: [Int]
  var drinks: [Drink]
  /// The newest drink (undo): a queued one first, else the server's `undo`.
  var last: Drink? { drinks.first }

  var fraction: Double { (goal ?? 0) > 0 ? total / goal! : 0 }

  static func build(_ j: [String: Any]?, day: String, pending: [PendingRecord]) -> WaterDay? {
    guard j != nil || pending.contains(where: { $0.date == day && $0.kind == FoodKind.water }) else { return nil }
    var drinks: [Drink] = ((j?["entries"] as? [[String: Any]]) ?? []).compactMap { e in
      guard let id = RecordFormat.string(e["id"]) else { return nil }
      return Drink(id: id, ml: RecordFormat.double(e["ml"]) ?? 0, at: e["at"] as? String)
    }
    let deleting = Set(pending.filter { $0.kind == FoodKind.waterDelete }.compactMap { RecordFormat.string($0.json["id"]) })
    drinks.removeAll { deleting.contains($0.id) }
    let added = pending.filter { $0.kind == FoodKind.water && $0.date == day }.reversed().map {
      Drink(id: "c:\($0.clientId)", ml: RecordFormat.double($0.json["ml"]) ?? 0, at: $0.json["at"] as? String, pending: $0)
    }
    drinks = added + drinks
    let goal = (j?["goal"] as? [String: Any]).flatMap { RecordFormat.double($0["ml"]) }
    let quick = ((j?["quick_ml"] as? [NSNumber]) ?? []).map(\.intValue)
    return WaterDay(date: day, total: drinks.reduce(0) { $0 + $1.ml }, goal: goal, quick: quick.isEmpty ? [250, 500] : quick,
                    drinks: drinks)
  }
}

/// This week's shopping list (`/grocery/list`), ticks still in the queue applied.
struct GroceryList: Equatable {
  struct Item: Equatable, Identifiable {
    var id: Int
    var name: String
    var qty: String?
    var category: String
    var note: String?
    var bought: Bool
    var pending: PendingRecord?
  }

  var week: String?
  var items: [Item]
  var bought: Int { items.filter(\.bought).count }

  func byCategory() -> [(String, [Item])] {
    var order: [String] = []
    var groups: [String: [Item]] = [:]
    for i in items {
      if groups[i.category] == nil { order.append(i.category) }
      groups[i.category, default: []].append(i)
    }
    return order.map { ($0, groups[$0] ?? []) }
  }

  static func build(_ j: [String: Any]?, pending: [PendingRecord]) -> GroceryList? {
    guard let j else { return nil }
    var items: [Item] = ((j["items"] as? [[String: Any]]) ?? []).compactMap { i in
      guard let id = (i["id"] as? NSNumber)?.intValue else { return nil }
      return Item(id: id, name: i["name"] as? String ?? "—", qty: i["qty_label"] as? String,
                  category: (i["category_label"] as? String) ?? tr("其他", "Other"), note: i["note"] as? String,
                  bought: (i["bought"] as? Bool) ?? false)
    }
    for p in pending where p.kind == FoodKind.grocery {
      guard let id = RecordFormat.double(p.json["id"]).map(Int.init), let i = items.firstIndex(where: { $0.id == id }) else { continue }
      items[i].bought = (p.json["bought"] as? Bool) ?? items[i].bought
      items[i].pending = p
    }
    return GroceryList(week: j["week_start"] as? String, items: items)
  }
}
