import Foundation

/// Server paths of the food pages (docs/ios-native-app.md section 7.1, 「记录（N5）」).
enum FoodAPI {
  static func today(_ day: String) -> APIRequest { .get("/v1/users/me/today", ["date": day]) }
  static func water(_ day: String) -> APIRequest { .get("/v1/users/me/water", ["date": day]) }
  static let waterPath = "/v1/users/me/water"
  static func waterEntry(_ id: String) -> String { "/v1/users/me/water/\(id)" }
  static let templates = APIRequest.get("/v1/users/me/meal-templates")
  static func logTemplate(_ id: Int) -> String { "/v1/users/me/meal-templates/\(id)/log" }
  static func mealLog(_ id: Int) -> String { "/v1/users/me/meal-logs/\(id)" }
  static func foodEntry(_ id: String) -> String { "/v1/users/me/nutrition/entries/\(id)" }
  static let recipes = APIRequest.get("/v1/users/me/recipes")
  static func recipe(_ id: String) -> APIRequest { .get("/v1/users/me/recipes/\(id)") }
  static func recipePath(_ id: String) -> String { "/v1/users/me/recipes/\(id)" }
  static func grocery(_ week: String) -> APIRequest { .get("/v1/users/me/grocery/list", ["week": week]) }
  static func groceryItem(_ id: Int) -> String { "/v1/users/me/grocery/list/items/\(id)" }
}

/// The 饮食 tab: the day's intake, water, quick meals, recipes and the shopping list, read from the local database and
/// refreshed from the server; every write is local first, then the offline queue (docs section 5.4).
@MainActor
final class FoodStore: LocalPageStore {
  @Published private(set) var day: String
  @Published private(set) var today: String
  @Published private(set) var nutrition: NutritionDay?
  @Published private(set) var water: WaterDay?
  @Published private(set) var templates: [MealTemplate] = []
  @Published private(set) var recipes: [Recipe] = []
  @Published private(set) var grocery: GroceryList?

  init(session: AccountSession) {
    let today = session.db.userToday()
    self.today = today
    self.day = today
    super.init(api: session.api, db: session.db, queue: session.sync.queue)
    observeDay()
  }

  var isToday: Bool { day == today }
  var week: String { RecordDay.monday(today) }

  private func observeDay() {
    observe(keys: [FoodDocs.today(day), FoodDocs.water(day), FoodDocs.templates, FoodDocs.recipes, FoodDocs.grocery(week)],
            kinds: FoodDocs.kinds)
  }

  override func changed() {
    let v = view
    nutrition = NutritionDay.build(today: v.json(FoodDocs.today(day)), day: day, pending: v.pending)
    water = WaterDay.build(v.json(FoodDocs.water(day)), day: day, pending: v.pending)
    templates = MealTemplate.parseList(v.list(FoodDocs.templates), pending: v.pending, today: today)
    recipes = (v.list(FoodDocs.recipes) ?? []).compactMap(Recipe.parse)
    grocery = GroceryList.build(v.json(FoodDocs.grocery(week)), pending: v.pending)
  }

  func select(_ d: String) {
    let d = min(d, today)
    guard d != day else { return }
    day = d
    observeDay()
    changed()
    Task { await refresh() }
  }

  func refresh() async {
    let t = db.userToday()
    if t != today {
      today = t
      observeDay()
    }
    var f = [DocFetch(FoodDocs.today(day), FoodAPI.today(day)), DocFetch(FoodDocs.water(day), FoodAPI.water(day)),
             DocFetch(FoodDocs.templates, FoodAPI.templates), DocFetch(FoodDocs.recipes, FoodAPI.recipes)]
    f.append(DocFetch(FoodDocs.grocery(week), FoodAPI.grocery(week)))
    await send()
    await pull(f)
  }

  /// After a write went through: the day's views again (intake, water, which templates are logged today).
  private func refreshDay() async {
    await pull([DocFetch(FoodDocs.today(day), FoodAPI.today(day)), DocFetch(FoodDocs.water(day), FoodAPI.water(day)),
                DocFetch(FoodDocs.templates, FoodAPI.templates)])
  }

  // MARK: - water

  func addWater(_ ml: Int) async -> WriteOutcome {
    let at = ISO8601DateFormatter.localWithOffset.string(from: Date())
    let out = await write(kind: FoodKind.water, date: today, group: "water", method: .post, path: FoodAPI.waterPath,
                          body: ["ml": ml, "source": "button", "at": at])
    if out.sent { await refreshDay() }
    return out
  }

  /// Undo the newest drink: a queued one is dropped here, a sent one needs the server.
  func undoWater() async -> Bool {
    guard let last = water?.last else { return false }
    let ok = await undo(clientId: last.pending?.clientId) {
      _ = try await self.api.data(APIRequest(method: .delete, path: FoodAPI.waterEntry(last.id)))
    }
    if ok { await refreshDay() }
    return ok
  }

  // MARK: - food entries

  /// Delete one entry (queued: a DELETE with the day).
  func delete(_ e: FoodEntry) async -> WriteOutcome {
    if let p = e.pending, e.isLocal, (try? LocalRecords.cancel(db, clientId: p.clientId)) == true {
      return WriteOutcome(clientId: nil, item: nil)
    }
    let path = FoodAPI.foodEntry(e.id) + "?date=\(e.date)"
    let out = await write(kind: FoodKind.foodDelete, date: e.date, group: "food:\(e.id)", method: .delete, path: path,
                          body: nil, overlay: ["id": e.id], clientIdInBody: false)
    if out.sent { await refreshDay() }
    return out
  }

  /// Set the entry to `quantity` servings (absolute, so a resend never scales twice).
  func setPortion(_ e: FoodEntry, quantity: Double) async -> WriteOutcome {
    let out = await write(kind: FoodKind.portion, date: e.date, group: "food:\(e.id)", method: .patch, path: FoodAPI.foodEntry(e.id),
                          body: ["date": e.date, "quantity": quantity], overlay: ["id": e.id, "quantity": quantity], supersede: true)
    if out.sent { await refreshDay() }
    return out
  }

  // MARK: - quick meals and recipes

  func log(_ t: MealTemplate, again: Bool = false) async -> WriteOutcome {
    var body: [String: Any] = ["date": today]
    if again { body["allow_duplicates"] = true }
    var overlay = t.totals.asJSON
    overlay.merge(["template_id": t.id, "name": t.name, "meal": t.meal.logName]) { $1 }
    let out = await write(kind: FoodKind.mealLog, date: today, group: "meal", method: .post, path: FoodAPI.logTemplate(t.id),
                          body: body, overlay: overlay)
    if out.sent { await refreshDay() }
    return out
  }

  /// Undo today's logging of a template.
  func undoLog(_ t: MealTemplate) async -> Bool {
    if let p = t.pending, (try? LocalRecords.cancel(db, clientId: p.clientId)) == true { return true }
    guard let id = t.todayLog else { return false }
    do {
      _ = try await api.data(APIRequest(method: .delete, path: FoodAPI.mealLog(id)))
      await refreshDay()
      return true
    } catch {
      return false
    }
  }

  func log(_ r: Recipe, servings: Double, meal: MealSlot, again: Bool = false) async -> WriteOutcome {
    var body: [String: Any] = ["date": day, "meal": meal.rawValue, "servings": servings]
    if again { body["allow_duplicates"] = true }
    var overlay = r.perServing.scaled(servings).asJSON
    overlay.merge(["recipe_id": r.id, "name": r.name, "meal": meal.logName, "servings": servings]) { $1 }
    let out = await write(kind: FoodKind.recipeLog, date: day, group: "meal", method: .post, path: FoodAPI.recipePath(r.id) + "/log",
                          body: body, overlay: overlay)
    if out.sent { await pull([DocFetch(FoodDocs.today(day), FoodAPI.today(day)), DocFetch(FoodDocs.recipes, FoodAPI.recipes)]) }
    return out
  }

  /// A refused or conflicting write (a meal already logged that day): drop it before logging it again on purpose.
  func drop(_ out: WriteOutcome) {
    guard let cid = out.clientId else { return }
    _ = try? LocalRecords.cancel(db, clientId: cid)
  }

  /// The recipe's detail document (ingredients, steps), cached.
  func recipeDetail(_ id: String) -> Recipe? {
    (try? db.doc(FoodDocs.recipe(id)))?.json.flatMap(Recipe.parse)
  }

  func loadRecipe(_ id: String) async -> Recipe? {
    _ = await RecordsPuller(api: api, db: db).pull([DocFetch(FoodDocs.recipe(id), FoodAPI.recipe(id))], kinds: [])
    return recipeDetail(id)
  }

  /// 收藏 / 现在能做: settings of the recipe, online (section 5.5).
  func patchRecipe(_ id: String, _ body: [String: Any]) async -> Bool {
    guard let data = try? JSONSerialization.data(withJSONObject: body) else { return false }
    do {
      _ = try await api.data(APIRequest(method: .patch, path: FoodAPI.recipePath(id), body: data))
      await pull([DocFetch(FoodDocs.recipes, FoodAPI.recipes), DocFetch(FoodDocs.recipe(id), FoodAPI.recipe(id))])
      return true
    } catch {
      return false
    }
  }

  // MARK: - grocery

  /// Tick / untick a line: last state wins (older unsent ticks of the line are dropped).
  func setBought(_ item: GroceryList.Item, _ bought: Bool) async {
    let out = await write(kind: FoodKind.grocery, date: nil, group: "grocery:\(item.id)", method: .patch,
                          path: FoodAPI.groceryItem(item.id), body: ["bought": bought], overlay: ["id": item.id, "bought": bought],
                          clientIdInBody: false, supersede: true)
    if out.sent { await pull([DocFetch(FoodDocs.grocery(week), FoodAPI.grocery(week))]) }
  }
}

extension ISO8601DateFormatter {
  /// `2026-01-02T08:10:00+11:00`: a drink logged offline keeps its local time.
  static let localWithOffset: ISO8601DateFormatter = {
    let f = ISO8601DateFormatter()
    f.formatOptions = [.withInternetDateTime]
    f.timeZone = .current
    return f
  }()
}
