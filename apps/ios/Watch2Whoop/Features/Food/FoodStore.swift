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
  static func mealPlan(_ day: String) -> APIRequest { .get("/v1/users/me/recipes/meal-plan", ["date": day]) }
  static let dietBrief = APIRequest.get("/v1/users/me/diet-profile", ["brief": "true"])
  static let diet = APIRequest.get("/v1/users/me/diet-profile")
  static let combine = "/v1/users/me/nutrition/combine"
  static func merge(_ id: Int) -> String { "/v1/users/me/nutrition/merges/\(id)" }
  static let products = APIRequest.get("/v1/users/me/grocery/products")
  static let storesPath = "/v1/users/me/grocery/stores"
  static let stores = APIRequest.get(storesPath)
  static let storeProducts = APIRequest.get("/v1/users/me/grocery/store-products")
  static func storeProduct(_ id: Int) -> APIRequest { .get("/v1/users/me/grocery/store-products/\(id)") }
  static let pantryPath = "/v1/users/me/grocery/pantry"
  static let pantry = APIRequest.get(pantryPath)
  static let pantryAuto = APIRequest.get("/v1/users/me/grocery/pantry-auto")
  static let specials = APIRequest.get("/v1/users/me/grocery/specials", ["limit": "8"])
}

/// `GET /grocery/pantry-auto`: whether logged meals take from the pantry; the page only shows it.
struct PantryAuto: Equatable {
  var enabled: Bool
  var label: String

  static func parse(_ j: [String: Any]?) -> PantryAuto? {
    guard let j, let label = j["label"] as? String else { return nil }
    return PantryAuto(enabled: (j["enabled"] as? Bool) ?? false, label: label)
  }
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
  @Published private(set) var mealPlan: MealPlan?
  @Published private(set) var dietBrief: DietProfileBrief?
  @Published private(set) var products: [GroceryProduct] = []
  @Published private(set) var stores: [GroceryStoreOption] = []
  @Published private(set) var storeProducts: [StoreProduct] = []
  @Published private(set) var pantry: [PantryItem] = []
  @Published private(set) var pantryAuto: PantryAuto?
  @Published private(set) var specials: GrocerySpecials?

  private let session: AccountSession
  /// 采购 was opened: pull to refresh brings its documents too (they are not fetched before that).
  private var shoppingOpen = false
  /// The Today card: only the day's intake and the quick meals are read. The today document comes with the Today tab's own
  /// refresh, so the card adds no second `GET /today` and no parsing of the library on the tab that must open fastest.
  private let cardOnly: Bool

  init(session: AccountSession, cardOnly: Bool = false) {
    self.session = session
    self.cardOnly = cardOnly
    let today = session.db.userToday()
    self.today = today
    self.day = today
    super.init(api: session.api, db: session.db, queue: session.sync.queue)
    observeDay()
  }

  var isToday: Bool { day == today }
  var week: String { RecordDay.monday(today) }

  /// Rollout flags (docs/rollout.md), the same as the page's.
  var circlesTab: Bool { session.circlesTab }
  var mergeOn: Bool { session.flags.contains("meal_merge") }
  var pantryOn: Bool { session.flags.contains("pantry_view") }
  var staplesOn: Bool { session.flags.contains("grocery_staples") }
  var pantryAutoOn: Bool { pantryOn && session.flags.contains("pantry_auto") }

  private func observeDay() {
    if cardOnly {
      observe(keys: [FoodDocs.today(day), FoodDocs.templates], kinds: FoodDocs.kinds)
      return
    }
    observe(keys: [FoodDocs.today(day), FoodDocs.water(day), FoodDocs.templates, FoodDocs.recipes, FoodDocs.grocery(week),
                   FoodDocs.mealPlan(today), FoodDocs.dietBrief, FoodDocs.products, FoodDocs.stores, FoodDocs.storeProducts,
                   FoodDocs.pantry, FoodDocs.pantryAuto, FoodDocs.specials],
            kinds: FoodDocs.kinds)
  }

  override func changed() {
    let v = view
    nutrition = NutritionDay.build(today: v.json(FoodDocs.today(day)), day: day, pending: v.pending)
    templates = MealTemplate.parseList(v.list(FoodDocs.templates), pending: v.pending, today: today)
    guard !cardOnly else { return }
    water = WaterDay.build(v.json(FoodDocs.water(day)), day: day, pending: v.pending)
    recipes = (v.list(FoodDocs.recipes) ?? []).compactMap(Recipe.parse)
    grocery = GroceryList.build(v.json(FoodDocs.grocery(week)), pending: v.pending)
    mealPlan = MealPlan.parse(v.json(FoodDocs.mealPlan(today)))
    dietBrief = DietProfileBrief.parse(v.json(FoodDocs.dietBrief))
    products = (v.list(FoodDocs.products) ?? []).compactMap(GroceryProduct.parse)
    stores = GroceryStoreOption.parse(v.json(FoodDocs.stores))
    storeProducts = (v.list(FoodDocs.storeProducts) ?? []).compactMap(StoreProduct.parse)
    pantry = PantryItem.build(v.list(FoodDocs.pantry), pending: v.pending)
    pantryAuto = PantryAuto.parse(v.json(FoodDocs.pantryAuto))
    specials = GrocerySpecials.parse(v.json(FoodDocs.specials))
  }

  /// Documents that are not tied to the rows being written: never prune the rows they do not show.
  private func quiet(_ fetches: [DocFetch], kinds: [String] = []) async {
    _ = await RecordsPuller(api: api, db: db).pull(fetches, kinds: kinds)
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
    if cardOnly {
      await send()
      await pull([DocFetch(FoodDocs.templates, FoodAPI.templates)])
      return
    }
    var f = [DocFetch(FoodDocs.today(day), FoodAPI.today(day)), DocFetch(FoodDocs.water(day), FoodAPI.water(day)),
             DocFetch(FoodDocs.templates, FoodAPI.templates), DocFetch(FoodDocs.recipes, FoodAPI.recipes)]
    f.append(DocFetch(FoodDocs.grocery(week), FoodAPI.grocery(week)))
    f.append(DocFetch(FoodDocs.dietBrief, FoodAPI.dietBrief))
    await send()
    // A slow plan or library must not hold back the day's numbers: they load beside it.
    async let plan: Void = refreshMealPlan()
    async let shopping: Void = refreshShoppingIfOpen()
    await pull(f)
    _ = await (plan, shopping)
  }

  /// 每餐推荐 is only for today.
  func refreshMealPlan() async {
    await quiet([DocFetch(FoodDocs.mealPlan(today), FoodAPI.mealPlan(today))])
  }

  /// After a write went through: the day's views again (intake, water, which templates are logged today, the plan).
  private func refreshDay() async {
    if cardOnly {
      await pull([DocFetch(FoodDocs.today(day), FoodAPI.today(day)), DocFetch(FoodDocs.templates, FoodAPI.templates)])
      return
    }
    async let plan: Void = refreshMealPlan()
    await pull([DocFetch(FoodDocs.today(day), FoodAPI.today(day)), DocFetch(FoodDocs.water(day), FoodAPI.water(day)),
                DocFetch(FoodDocs.templates, FoodAPI.templates)])
    _ = await plan
  }

  /// Everything 采购 shows beyond the list (the page loads these when 采购 opens, too).
  func refreshShopping() async {
    shoppingOpen = true
    var f = [DocFetch(FoodDocs.grocery(week), FoodAPI.grocery(week)), DocFetch(FoodDocs.products, FoodAPI.products),
             DocFetch(FoodDocs.stores, FoodAPI.stores), DocFetch(FoodDocs.storeProducts, FoodAPI.storeProducts),
             DocFetch(FoodDocs.specials, FoodAPI.specials)]
    if pantryOn { f.append(DocFetch(FoodDocs.pantry, FoodAPI.pantry)) }
    if pantryAutoOn { f.append(DocFetch(FoodDocs.pantryAuto, FoodAPI.pantryAuto)) }
    await quiet(f)
  }

  private func refreshShoppingIfOpen() async {
    if shoppingOpen { await refreshShopping() }
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

  /// grams: one value per food, when they were changed in the template's sheet.
  func log(_ t: MealTemplate, again: Bool = false, grams: [Double?] = []) async -> WriteOutcome {
    var body: [String: Any] = ["date": today]
    if again { body["allow_duplicates"] = true }
    let changed = t.overrides(grams: grams)
    if let changed { body["items"] = changed }
    var overlay = (changed != nil ? t.totals(grams: grams) : t.totals).asJSON
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
    if out.sent {
      async let plan: Void = refreshMealPlan()
      await pull([DocFetch(FoodDocs.today(day), FoodAPI.today(day)), DocFetch(FoodDocs.recipes, FoodAPI.recipes)])
      _ = await plan
    }
    return out
  }

  // MARK: - several entries into one (online, docs section 5.5)

  struct CombineOutcome {
    var mergeId: Int?
    var name = ""
    var error: String?
  }

  /// `action` .template: a new quick meal; .merge: one entry instead of the picked ones.
  func combine(_ entries: [FoodEntry], action: FoodCombine.Action, name: String?) async -> CombineOutcome {
    struct Body: Encodable {
      let date: String
      let entry_ids: [String]   // swiftlint:disable:this identifier_name
      let action: String
      let name: String?
    }
    let date = entries.first?.date ?? day
    do {
      let r = try APIRequest.json(.post, FoodAPI.combine, Body(date: date, entry_ids: entries.map(\.id), action: action.rawValue, name: name),
                                  idempotent: false)
      let data = try await api.data(r)
      let j = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any]
      let merge = j?["merge"] as? [String: Any], template = j?["template"] as? [String: Any]
      await refreshDay()
      return CombineOutcome(mergeId: (merge?["id"] as? NSNumber)?.intValue, name: (merge?["name"] ?? template?["name"]) as? String ?? name ?? "")
    } catch let e as APIError {
      return CombineOutcome(error: e.userMessage)
    } catch {
      return CombineOutcome(error: tr("需要联网", "Needs a connection"))
    }
  }

  /// 「拆开」: the merged entry goes, the originals come back. Returns the reason when it did not work.
  func split(mergeId: Int) async -> String? {
    do {
      _ = try await api.data(APIRequest(method: .delete, path: FoodAPI.merge(mergeId)))
      await refreshDay()
      return nil
    } catch let e as APIError {
      return e.userMessage
    } catch {
      return tr("需要联网", "Needs a connection")
    }
  }

  // MARK: - diet profile

  func dietProfile() -> DietProfile? {
    DietProfile.parse((try? db.doc(FoodDocs.diet))?.json)
  }

  func loadDietProfile() async -> DietProfile? {
    await quiet([DocFetch(FoodDocs.diet, FoodAPI.diet)])
    return dietProfile()
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
    if out.sent {
      await pull([DocFetch(FoodDocs.grocery(week), FoodAPI.grocery(week))])
      if pantryOn { await pullPantry() }   // ticking adds to the pantry, unticking takes it back
    }
  }

  private func pullPantry() async {
    await quiet([DocFetch(FoodDocs.pantry, FoodAPI.pantry)], kinds: [FoodKind.pantry])
  }

  /// 「用完了」: a whole line, absolute (quantity 0), so a resend changes nothing; last state wins, queued offline.
  func usedUp(_ p: PantryItem) async -> WriteOutcome {
    let out = await write(kind: FoodKind.pantry, date: nil, group: "pantry:\(p.name.lowercased())", method: .put, path: FoodAPI.pantryPath,
                          body: p.putBody(quantity: 0), overlay: ["id": p.id, "name": p.name, "quantity": 0], clientIdInBody: false, supersede: true)
    if out.sent { await pullPantry() }
    return out
  }

  /// Undo of 「用完了」: still queued → dropped here; already sent → the old quantity goes back the same way.
  func restorePantry(_ p: PantryItem, after used: WriteOutcome) async -> WriteOutcome {
    if let cid = used.clientId, (try? LocalRecords.cancel(db, clientId: cid)) == true { return WriteOutcome(clientId: nil, item: nil) }
    let out = await write(kind: FoodKind.pantry, date: nil, group: "pantry:\(p.name.lowercased())", method: .put, path: FoodAPI.pantryPath,
                          body: p.putBody(quantity: p.quantity), overlay: p.restoreJSON, clientIdInBody: false, supersede: true)
    if out.sent { await pullPantry() }
    return out
  }

  /// Which supermarkets are connected: a setting, online (docs section 5.5). Returns the reason when it did not work.
  func toggleStore(_ key: String) async -> String? {
    struct Body: Encodable { let connected: [String] }
    do {
      let r = try APIRequest.json(.put, FoodAPI.storesPath, Body(connected: GroceryStoreOption.toggled(stores, key)), idempotent: false)
      _ = try await api.data(r)
      await refreshShopping()
      return nil
    } catch let e as APIError {
      return e.userMessage
    } catch {
      return tr("需要联网", "Needs a connection")
    }
  }

  // MARK: - price history

  func priceHistory(_ id: Int) -> PriceHistory? {
    PriceHistory.parse((try? db.doc(FoodDocs.storeProduct(id)))?.json)
  }

  func loadPriceHistory(_ id: Int) async -> PriceHistory? {
    await quiet([DocFetch(FoodDocs.storeProduct(id), FoodAPI.storeProduct(id))])
    return priceHistory(id)
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
