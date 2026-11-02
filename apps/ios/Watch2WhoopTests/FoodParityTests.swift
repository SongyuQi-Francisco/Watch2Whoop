import XCTest
@testable import Watch2Whoop

// 饮食 and 采购 as the Today page has them (#340, #292): every model is fed the server's own shapes. Fictional data only.

private func json(_ s: String) -> [String: Any] { (try? JSONSerialization.jsonObject(with: Data(s.utf8))) as? [String: Any] ?? [:] }
private func list(_ s: String) -> [[String: Any]] { (try? JSONSerialization.jsonObject(with: Data(s.utf8))) as? [[String: Any]] ?? [] }

private func rec(_ kind: String, _ payload: [String: Any], date: String? = nil, state: OutboxItem.State? = .pending) -> PendingRecord {
  PendingRecord(kind: kind, clientId: UUID().uuidString, date: date, payload: try! JSONSerialization.data(withJSONObject: payload),
                createdAt: 0, state: state, lastError: nil)
}

final class GroceryParityTests: XCTestCase {
  private let g = json("""
  {"week_start": "2026-01-05", "week_end": "2026-01-11", "unpriced": 1, "est_total": {"AUD": 25.5},
   "half_price": {"items": 1, "save": {"AUD": 4}},
   "prices": {"by_store": {"coles": {"label": "Coles", "total": 12.4, "items": 2}, "aldi": {"label": "Aldi", "total": 3, "items": 1}},
              "savings": {"AUD": 2.5}, "missing": ["Rice", "Beans"], "stale": ["Milk"]},
   "warnings": [{"code": "red_meat", "label": "Red meat 500g"}, {"code": "product", "label": "High salt"}],
   "items": [
     {"id": 1, "name": "Chicken", "category_label": "Meat", "qty_label": "2 pk · 1 kg", "est_cost": 16, "currency": "AUD", "bought": false,
      "offer": {"label": "Coles", "price": 8, "was_price": 12, "special": "half_price", "half_price": true, "save_total": 8, "age_days": 3, "stale": false},
      "links": [{"store": "coles", "label": "Coles", "kind": "product", "url": "https://shop.example/p/1"},
                {"store": "x", "label": "X", "kind": "search", "url": "http://shop.example/q"}, {"label": "Bad", "url": "javascript:alert(1)"}],
      "pantry_label": "2 at home", "staple": true},
     {"id": 2, "name": "Yoghurt", "category_label": "Dairy", "qty_label": "1 kg", "est_cost": 6, "paid_price": 5, "currency": "AUD", "bought": true,
      "purchase": {"verdict": "cheap"}, "links": []},
     {"id": 3, "name": "Rice", "category_label": "Grains", "qty_label": "1 kg", "bought": false,
      "offer": {"label": "WWS", "price": 3, "special": "price_drop", "half_price": false, "age_days": 0}}
   ]}
  """)

  func testListCarriesTotalsPricesAndWarnings() throws {
    let l = try XCTUnwrap(GroceryList.build(g, pending: []))
    XCTAssertEqual(l.weekEnd, "2026-01-11")
    XCTAssertEqual(l.unpriced, 1)
    XCTAssertEqual(Money.joined(l.estTotal), "A$26")
    XCTAssertEqual(l.halfItems, 1)
    XCTAssertEqual(Money.joined(l.halfSave), "A$4")
    let p = try XCTUnwrap(l.prices)
    XCTAssertEqual(p.stores.map(\.label), ["Aldi", "Coles"])
    XCTAssertEqual(p.missing, 2)
    XCTAssertEqual(p.stale, 1)
    XCTAssertEqual(l.warnings.map(\.code), ["red_meat", "product"])
  }

  func testItemValueBadgeAgeAndLinks() throws {
    let l = try XCTUnwrap(GroceryList.build(g, pending: []))
    let chicken = l.items[0], yoghurt = l.items[1], rice = l.items[2]
    XCTAssertEqual(chicken.value, "2 pk · 1 kg · A$16.00")
    XCTAssertEqual(chicken.badge?.0, tr("半价 -A$8", "Half price -A$8"))
    XCTAssertEqual(chicken.offerAge, tr("3 天前", "3d ago"))
    XCTAssertEqual(chicken.links.map(\.label), ["Coles"], "only https links; the app never hands anything else to the system")
    XCTAssertTrue(chicken.links[0].isProduct)
    XCTAssertEqual(chicken.pantryLabel, "2 at home")
    XCTAssertTrue(chicken.staple)

    XCTAssertEqual(yoghurt.value, "1 kg · A$5.00", "what was paid wins over the estimate")
    XCTAssertEqual(yoghurt.badge?.0, tr("买得便宜", "Good price"))
    XCTAssertNil(yoghurt.offerAge)

    XCTAssertEqual(rice.value, "1 kg")
    XCTAssertEqual(rice.badge?.0, tr("特价", "On special"))
    XCTAssertNil(rice.offerAge, "today's price shows no age")
  }

  func testMoney() {
    XCTAssertEqual(Money.full(12.5, "AUD"), "A$12.50")
    XCTAssertEqual(Money.full(120, "CNY"), "¥120")
    XCTAssertEqual(Money.full(3, "SEK"), "SEK 3.00")
    XCTAssertEqual(Money.short(12.5, "AUD"), "A$13", ".5 goes up, as on the page")
    XCTAssertEqual(Money.short(10.5, "AUD"), "A$11")
    XCTAssertEqual(Money.short(2.5, "AUD"), "A$2.5")
    XCTAssertEqual(Money.short(5.25, "AUD"), "A$5.3")
    XCTAssertEqual(Money.short(2, "AUD"), "A$2")
    XCTAssertEqual(Money.full(nil, "AUD"), "")
  }

  func testProductsParseSearchAndNutrition() throws {
    let p = try XCTUnwrap(GroceryProduct.parse(json("""
    {"id": 7, "name": "Greek yoghurt", "brand": "Acme", "store": "coles", "store_label": "Coles", "category_label": "Dairy", "size_label": "1 kg",
     "price": 6.5, "currency": "AUD", "unit_price": 6.5, "package_unit": "g", "nutrition": {"kcal": 60, "protein_g": 9.5, "sodium_mg": 40},
     "serving_size": 170, "serving_unit": "g", "nutrition_serving": {"kcal": 102},
     "allergens": ["Milk"], "allergen_traces": [], "matches": ["yogurt"], "flags": ["High sugar"],
     "image_url": "http://img.example/a.png", "links": [],
     "staple": true, "staple_info": {"every_days": 7, "qty": 2, "unit": "pack", "next_due": "2026-01-20", "due": true}}
    """)))
    XCTAssertEqual(p.value, "1 kg · A$6.50")
    XCTAssertEqual(p.unitPriceLabel, "A$6.50 / kg")
    XCTAssertEqual(GroceryProduct.nutritionRows(p.nutrition).count, 3)
    XCTAssertEqual(GroceryProduct.nutritionRows(p.nutrition).first?.1, "60 kcal")
    XCTAssertEqual(p.staple?.summary, tr("每 7 天 · 2 包", "Every 7 days · 2 pk"))
    XCTAssertNil(p.imageURL, "only an https picture link")
    XCTAssertEqual(p.allergens, ["Milk"])
    XCTAssertTrue(p.hit("YOGURT"), "searches what it stands for")
    XCTAssertTrue(p.hit("acme"))
    XCTAssertTrue(p.hit(" "))
    XCTAssertFalse(p.hit("rice"))
  }

  func testStoreProductAndPriceHistory() throws {
    let s = try XCTUnwrap(StoreProduct.parse(json("""
    {"id": 3, "name": "Eggs 12", "size": "700g", "store_label": "Coles", "url": "https://shop.example/e", "offer": {"price": 5, "age_days": 9, "stale": true}}
    """)))
    XCTAssertEqual(s.link?.label, "Coles")
    XCTAssertEqual(s.offer?.stale, true)
    XCTAssertEqual(GroceryOffer.ageLabel(s.offer?.ageDays ?? 0), tr("9 天前", "9d ago"))
    XCTAssertEqual(GroceryOffer.ageLabel(0), tr("今天", "Today"))

    let h = try XCTUnwrap(PriceHistory.parse(json("""
    {"name": "Eggs 12", "store_label": "Coles", "size": "700g", "url": null, "points": [{"price": 6}, {"price": 5}], "latest": 5, "latest_age_days": 9,
     "min": 5, "avg": 5.5, "specials": {"count": 2, "every_days": 14}, "last_purchase": {"date": "2026-01-02", "unit_paid": 5, "on_special": true, "verdict": "cheap"}}
    """)))
    XCTAssertEqual(h.points, [6, 5])
    XCTAssertEqual(h.specialsLabel, tr("2 次 · 约每 14 天", "2 times · about every 14 days"))
    XCTAssertEqual(h.lastPurchase?.verdict, "cheap")
    XCTAssertNil(PriceHistory.parse(nil))
    var none = h
    none.specials = 0
    XCTAssertEqual(none.specialsLabel, tr("没见过", "Never seen"))
  }

  private let pantry = list("""
  [{"id": 1, "name": "Rice", "quantity": 500, "unit": "g", "label": "500 g", "category_label": "Grains", "days_ago": 0, "product_id": 9, "best_before": "2026-02-01", "days_left": 2, "expiring": true},
   {"id": 2, "name": "Eggs", "quantity": 6, "unit": "pcs", "label": "6 pcs", "category_label": "Dairy", "days_ago": 4, "days_left": null, "expiring": false},
   {"id": 3, "name": "Oats", "quantity": 200, "unit": "g", "label": "200 g", "category_label": "Grains", "days_ago": 1}]
  """)

  func testPantryByCategoryAgeAndExpiry() {
    let items = PantryItem.build(pantry, pending: [])
    XCTAssertEqual(PantryItem.byCategory(items).map(\.0), ["Grains", "Dairy"])
    XCTAssertEqual(PantryItem.byCategory(items)[0].1.map(\.name), ["Rice", "Oats"])
    XCTAssertEqual(items[0].expiry?.0, tr("2 天后到期", "Expires in 2d"))
    XCTAssertEqual(items[0].ageLabel, tr("今天更新", "Updated today"))
    XCTAssertEqual(items[1].ageLabel, tr("4 天前更新", "Updated 4d ago"))
    XCTAssertNil(items[1].expiry)
    var it = items[0]
    it.daysLeft = 0
    XCTAssertEqual(it.expiry?.1, .bad)
    it.daysLeft = -1
    XCTAssertEqual(it.expiry?.0, tr("已过期", "Expired"))
  }

  func testUsedUpIsHiddenUntilTheServerAgreesAndUndoPutsItBack() throws {
    let items = PantryItem.build(pantry, pending: [])
    let rice = items[0]
    XCTAssertEqual(PantryItem.build(pantry, pending: [rec(FoodKind.pantry, ["id": 1, "name": "RICE", "quantity": 0])]).map(\.name), ["Eggs", "Oats"],
                   "by name, case-insensitively, as the server keys it")
    // the used-up already reached the server (the row is gone there), the undo is still queued
    let serverGone = pantry.filter { ($0["name"] as? String) != "Rice" }
    let back = PantryItem.build(serverGone, pending: [rec(FoodKind.pantry, rice.restoreJSON)])
    XCTAssertTrue(back.contains { $0.name == "Rice" && $0.quantity == 500 && $0.pending != nil })
    XCTAssertEqual(back.first { $0.name == "Rice" }?.bestBefore, "2026-02-01")

    let used = try XCTUnwrap(rice.putBody(quantity: 0)["items"] as? [[String: Any]]).first
    XCTAssertEqual(used?["quantity"] as? Double, 0)
    XCTAssertNil(used?["product_id"], "the row goes, nothing to keep")
    let restored = try XCTUnwrap(rice.putBody(quantity: 500)["items"] as? [[String: Any]]).first
    XCTAssertEqual(restored?["product_id"] as? Int, 9)
    XCTAssertEqual(restored?["best_before"] as? String, "2026-02-01")
    XCTAssertEqual(restored?["unit"] as? String, "g")
  }

  func testStoresToggleAndSpecials() throws {
    let stores = GroceryStoreOption.parse(json("""
    {"connected": ["coles"], "stores": [{"key": "coles", "label": "Coles", "connected": true}, {"key": "woolworths", "label": "WWS", "connected": false}]}
    """))
    XCTAssertEqual(stores.map(\.key), ["coles", "woolworths"])
    XCTAssertEqual(GroceryStoreOption.toggled(stores, "woolworths"), ["coles", "woolworths"])
    XCTAssertEqual(GroceryStoreOption.toggled(stores, "coles"), [])

    let s = try XCTUnwrap(GrocerySpecials.parse(json("""
    {"week_start": "2026-01-07", "valid_to": "2026-01-13", "items": [
      {"label": "Coles", "name": "Coles Tuna 95g", "price": 1.5, "was_price": 3, "flags": ["Red meat"], "url": "https://shop.example/t"},
      {"label": "WWS", "name": "Beans", "price": 1, "flags": [], "url": null}]}
    """)))
    XCTAssertEqual(s.items[0].name, "Tuna 95g", "the store's name comes off the front")
    XCTAssertEqual(s.items[0].flag, "Red meat")
    XCTAssertNotNil(s.items[0].link)
    XCTAssertNil(s.items[1].link)
    XCTAssertTrue(GrocerySpecials.parse(json(#"{"enabled": false, "items": []}"#))?.items.isEmpty == true, "the store sync stays off: nothing to show")
  }
}

final class FoodParityTests: XCTestCase {
  func testExtrasUnderTheRingsAndNotLogged() throws {
    let day = json("""
    {"nutrition": {"logged": true, "goal": {"calories": 2000}, "intake": {"calories": 500},
     "entries": [{"id": "a", "meal_type": "lunch", "food": "A", "calories": 300, "saturated_fat_g": 1.5, "added_sugar_g": 2, "merge_id": 4},
                 {"id": "b", "meal_type": "lunch", "food": "B", "calories": 200, "saturated_fat_g": 2}]}}
    """)
    let n = try XCTUnwrap(NutritionDay.build(today: day, day: "2026-01-02", pending: []))
    XCTAssertEqual(n.extras.map(\.1), ["3.5 g", "2 g", "2"], "no fibre on any entry: left out; the count stays")
    XCTAssertEqual(n.entries[0].mergeId, 4)
    XCTAssertFalse(n.notLogged)

    let empty = json(#"{"nutrition": {"logged": false, "goal": {}, "intake": {}, "entries": []}}"#)
    let e = try XCTUnwrap(NutritionDay.build(today: empty, day: "2026-01-02", pending: []))
    XCTAssertTrue(e.notLogged)
    XCTAssertTrue(e.extras.isEmpty)
    let queued = rec(FoodKind.mealLog, ["name": "Oats", "meal": "breakfast", "calories": 100], date: "2026-01-02")
    XCTAssertFalse(try XCTUnwrap(NutritionDay.build(today: empty, day: "2026-01-02", pending: [queued])).notLogged,
                   "a meal on its way counts as logged")
  }

  func testTheTodayCardsBar() throws {
    func day(_ kcal: Int) -> NutritionDay? {
      NutritionDay.build(today: json(#"{"nutrition": {"logged": true, "goal": {"calories": 2000}, "intake": {"calories": \#(kcal)}, "entries": []}}"#),
                         day: "2026-01-02", pending: [])
    }
    XCTAssertEqual(try XCTUnwrap(day(1000)).kcalFraction, 0.5, accuracy: 0.001)
    XCTAssertFalse(try XCTUnwrap(day(2100)).kcalOver, "at the 5% margin it is not over yet")
    XCTAssertTrue(try XCTUnwrap(day(2101)).kcalOver)
    XCTAssertNil(NutritionDay.build(today: json(#"{"nutrition": {"code": "forbidden"}}"#), day: "2026-01-02", pending: []), "no share: no card")
    let err = NutritionDay.build(today: json(#"{"nutrition": {"error": "down", "goal": {}, "intake": {}, "entries": []}}"#), day: "2026-01-02", pending: [])
    XCTAssertEqual(err?.error, "down", "the card stays out; the page says the same")
    XCTAssertEqual(err?.notLogged, false)
  }

  func testWaterGoalNotePercentAndUndoLikeThePage() throws {
    let w = json("""
    {"date": "2026-01-02", "total_ml": 750, "percent": 25,
     "goal": {"ml": 3000, "base_ml": 2500, "source": "profile", "extras": [{"kind": "training", "ml": 500}]},
     "entries": [{"id": 3, "ml": 250, "source": "apple_health"}, {"id": 2, "ml": 500, "source": "button"}],
     "undo": {"id": 2, "ml": 500}, "quick_ml": [250, 500]}
    """)
    let d = try XCTUnwrap(WaterDay.build(w, day: "2026-01-02", pending: []))
    XCTAssertEqual(d.last?.id, "2", "the newest is from Health and is removed there; undo takes the newest logged here")
    XCTAssertEqual(d.percent, 25)
    XCTAssertEqual(d.goalNote, tr("目标来自饮食档案", "Goal from your diet profile") + " · " + tr("训练日", "Training day") + " +500")

    let gen = json(#"{"goal": {"ml": 2500, "source": "default", "extras": [{"kind": "sauna", "ml": 300}]}, "entries": [{"id": 1, "ml": 30000}], "undo": null}"#)
    let g = try XCTUnwrap(WaterDay.build(gen, day: "2026-01-02", pending: []))
    XCTAssertEqual(g.percent, 999, "capped")
    XCTAssertEqual(g.goalNote, tr("通用建议值", "General recommendation") + " · " + tr("桑拿", "Sauna") + " +300")
    XCTAssertNil(g.last, "only Health samples (or nothing) today: no undo")

    let undoing = try XCTUnwrap(WaterDay.build(w, day: "2026-01-02", pending: [rec(FoodKind.waterDelete, ["id": 2], date: "2026-01-02")]))
    XCTAssertNil(undoing.last, "the drink being undone is already off the card")
    XCTAssertNil(WaterDay.build(json(#"{"entries": []}"#), day: "2026-01-02", pending: [])?.goalNote)
  }

  private let templates = list("""
  [{"id": 1, "name": "Oats", "meal": "breakfast", "uses_30d": 2, "totals": {"kcal": 390, "protein_g": 16},
    "items": [{"food": "oats", "grams": 50, "kcal": 190, "protein_g": 6}, {"food": "milk", "grams": 200, "kcal": 200, "protein_g": 10}], "today_log": null},
   {"id": 2, "name": "Shake", "meal": "snacks", "uses_30d": 9, "needs_confirmation": true, "totals": {"kcal": 120},
    "items": [{"food": "whey", "kcal": 120}], "today_log": null},
   {"id": 3, "name": "Toast", "meal": "breakfast", "totals": {"kcal": 200}, "items": [], "today_log": {"log_id": 8}},
   {"id": 4, "name": "Rice", "meal": "lunch", "uses_30d": 2, "totals": {"kcal": 300}, "items": [], "today_log": null}]
  """)

  func testTemplatesMostUsedFirstAndKnowWhatToConfirm() {
    let t = MealTemplate.parseList(templates, pending: [], today: "2026-01-02")
    XCTAssertEqual(MealTemplate.byUse(t).map(\.id), [2, 1, 4, 3], "ties keep the server's order")
    XCTAssertTrue(t[1].needsConfirmation)
    XCTAssertEqual(MealTemplateStyle.subtitle(t[1]), tr("待确认", "To confirm"))
    XCTAssertTrue(MealTemplateStyle.needsAttention(t[1]))
    XCTAssertEqual(MealTemplateStyle.subtitle(t[0]), "390 kcal")
    XCTAssertEqual(MealTemplateStyle.subtitle(t[2]), tr("已记 ✓", "Logged ✓"))
    XCTAssertTrue(t[2].isLogged)
    XCTAssertEqual(t[0].foods.map(\.name), ["oats", "milk"])
  }

  func testChangedGramsScaleEachFoodAndOnlyThenGoToTheServer() throws {
    let t = MealTemplate.parseList(templates, pending: [], today: "2026-01-02")[0]
    XCTAssertNil(t.overrides(grams: [50, 200]), "nothing changed: the template as saved")
    XCTAssertNil(t.overrides(grams: []))
    let items = try XCTUnwrap(t.overrides(grams: [100, 200]))
    XCTAssertEqual(items[0]["grams"] as? Double, 100)
    XCTAssertEqual(items[1]["food"] as? String, "milk")
    let m = t.totals(grams: [100, 200])
    XCTAssertEqual(m.kcal, 580)
    XCTAssertEqual(m.protein, 22)
    XCTAssertEqual(t.totals(grams: [nil, nil]).kcal, 390)
  }

  func testDefaultNameOfCombinedEntries() {
    func e(_ s: String) -> FoodEntry { FoodEntry(id: s, date: "2026-01-02", time: nil, meal: .lunch, food: s, quantity: 1, unit: nil, macros: Macros()) }
    XCTAssertEqual(FoodCombine.defaultName([e("Rice（150 g）"), e("Chicken (200g)"), e("Soup (bowl)")], action: .merge), "Rice + Chicken + Soup (bowl)")
    XCTAssertEqual(FoodCombine.defaultName([e(String(repeating: "x", count: 40))], action: .template).count, 30)
    XCTAssertEqual(FoodCombine.defaultName([e(String(repeating: "x", count: 60))], action: .merge).count, 50)
  }

  func testMealPlanSlotsAndUnavailable() throws {
    let p = try XCTUnwrap(MealPlan.parse(json("""
    {"date": "2026-01-02", "remaining": {"kcal": 900, "protein_g": 60},
     "slots": [{"slot": "breakfast", "logged": {"kcal": 300}, "target": null, "suggestions": []},
               {"slot": "lunch", "logged": null, "target": {"kcal": 500, "protein_g": 30},
                "suggestions": [{"id": 5, "name": "Tomato eggs", "kcal": 300, "protein_g": 18, "meal_kcal": 480, "meal_protein_g": 22, "with_staple": true, "prep_min": 10,
                                 "favorite": true, "labels": [{"label": "Have 2/3", "tone": "neutral"}, {"label": "High sat. fat", "tone": "warn"}, {"label": "Over", "tone": "bad"}]}]},
               {"slot": "snack", "target": {"kcal": 0, "protein_g": 0}, "suggestions": [{"id": 6, "name": "Fruit", "kcal": 80}]}]}
    """)))
    XCTAssertEqual(p.slots.map(\.meal), [.lunch, .snack], "only meals with something to suggest")
    XCTAssertEqual(p.remainingKcal, 900)
    let s = p.slots[0].suggestions[0]
    XCTAssertEqual(s.meta, tr("480 kcal 含饭", "480 kcal with rice") + " · 22 g · 10′")
    XCTAssertEqual(s.labels.map(\.1), [.text, .mid, .bad])
    XCTAssertEqual(s.recipe.id, "5")
    XCTAssertEqual(p.slots[1].targetKcal, 0)
    XCTAssertEqual(p.slots[1].suggestions[0].kcal, 80, "without a meal figure: the recipe's own")

    let u = try XCTUnwrap(MealPlan.parse(json(#"{"unavailable": "sparky", "label": "Food log unavailable", "slots": []}"#)))
    XCTAssertEqual(u.unavailable, "Food log unavailable")
    XCTAssertFalse(u.isEmpty, "shown with a retry")
    XCTAssertTrue(try XCTUnwrap(MealPlan.parse(json(#"{"slots": []}"#))).isEmpty)
    XCTAssertNil(MealPlan.parse(nil))
  }

  func testDietProfileBriefMeta() throws {
    XCTAssertEqual(try XCTUnwrap(DietProfileBrief.parse(json(#"{"version": null, "sections": 0, "goal_effective_from": null, "updated_on": null}"#))).meta,
                   tr("未建立", "Not set up"))
    XCTAssertEqual(try XCTUnwrap(DietProfileBrief.parse(json(#"{"version": "v3", "sections": 2, "updated_on": "2026-01-05"}"#))).meta,
                   tr("v3（1/5 更新）", "v3 (updated 5 Jan)"))
    XCTAssertEqual(try XCTUnwrap(DietProfileBrief.parse(json(#"{"sections": 0, "goal_effective_from": "2026-01-03"}"#))).meta,
                   tr("目标 1/3 起", "Goal from 3 Jan"))
    XCTAssertNil(DietProfileBrief.parse(nil))
  }

  func testDietProfileBlocks() throws {
    let p = try XCTUnwrap(DietProfile.parse(json("""
    {"version": "v2", "hidden": [],
     "goals": {"sets": [{"name": "Training day", "calories": 2100, "protein_g": 150, "carbs_g": 220, "fat_g": 60, "sat_fat_max_g": 15, "days": 5, "water_ml": 3000}],
               "effective_from": "2026-01-01", "reason": "first version"},
     "energy": {"start": "2025-12-01", "end": "2025-12-28", "days": 28, "basal_kcal": 1800, "active_kcal": 700, "total_kcal": 2500},
     "plan": {"target_kcal": 2000, "deficit_kcal": 500, "loss_kg_per_week": 0.45, "fast": true},
     "weight": {"kg": 80.5},
     "constraint_rules": {"current": [{"text": "Red meat at most 250 g a week", "basis": "own choice", "effective_from": "2026-01-02"}, {"text": "No basis"}],
                          "history": [{"text": "Old limit", "effective_to": "2026-01-03"}]},
     "sections": [
       {"section": "targets", "data": {"steps": 8000, "note": "a note"}, "text": "ignored"},
       {"section": "rationale", "data": {"goal_weight_kg": 75, "checkpoints": [{"date": "2026-03-01", "weight_kg": 78, "note": "check"}]}},
       {"section": "constraints", "data": {"items": [{"rule": "Dairy first", "basis": "doctor"}]}},
       {"section": "day_plan", "data": {"meals": [{"meal": "Lunch", "foods": "rice, fish", "kcal": 600, "protein_g": 40}], "staples": ["eggs"]}},
       {"section": "adjust_rules", "data": null, "text": "Plain text rule"}],
     "versions": [{"version": "v2", "changed_on": "2026-01-02", "changes": "more protein", "reason": "training"}],
     "goal_history": [{"effective_from": "2026-01-01", "training_days": 5, "training_kcal": 2100, "rest_kcal": 1900, "reason": "start"}]}
    """)))
    XCTAssertEqual(p.goalRows.map(\.label), ["Training day", tr("生效", "In effect"), tr("喝水", "Water"), tr("步数", "Steps")])
    XCTAssertEqual(p.goalRows[0].value, "2100 kcal")
    XCTAssertTrue(p.goalRows[0].sub?.contains("≤ 15 g") == true)
    XCTAssertEqual(p.goalRows[2].value, "3000 ml", "water from the goal set when the targets section has none")
    XCTAssertEqual(p.goalNote, "a note")
    XCTAssertNil(p.goalText, "a section with data shows the data")
    XCTAssertEqual(p.energyTiles.map(\.value), ["1800", "700", "2500"])
    XCTAssertEqual(p.planTiles[1].value, "−500")
    XCTAssertEqual(p.planTiles[2].value, "−0.45")
    XCTAssertTrue(p.planFast)
    XCTAssertEqual(p.weightRows[0].value, "80.5 → 75 kg")
    XCTAssertEqual(p.weightRows.count, 2)
    XCTAssertEqual(p.rules.map(\.text), ["Red meat at most 250 g a week", "No basis"])
    XCTAssertEqual(p.constraints.first?.detail, "doctor")
    XCTAssertEqual(p.dayRows.first?.value, "600 kcal")
    XCTAssertEqual(p.staples, ["eggs"])
    XCTAssertEqual(p.adjustText, "Plain text rule")
    XCTAssertEqual(p.log.map(\.at), ["2026-01-03", "2026-01-02", "2026-01-01"], "newest first, all three kinds")
    XCTAssertFalse(p.isEmpty)

    let none = try XCTUnwrap(DietProfile.parse(json(#"{"hidden": ["goals", "energy"], "sections": [], "versions": []}"#)))
    XCTAssertTrue(none.hidesGoals)
    XCTAssertTrue(none.isEmpty)

    let surplus = try XCTUnwrap(DietProfile.parse(json(#"{"plan": {"target_kcal": 3000, "deficit_kcal": -200, "loss_kg_per_week": -0.2}}"#)))
    XCTAssertEqual(surplus.planTiles[1].value, "+200")
    XCTAssertEqual(surplus.planTiles[2].value, "+0.2")
  }

  func testDietProfileBlockState() throws {
    let d = try XCTUnwrap(UserDefaults(suiteName: "w2w.test.\(UUID().uuidString)"))
    XCTAssertTrue(DietProfileBlocks.isOpen("goals", d))
    XCTAssertFalse(DietProfileBlocks.isOpen("versions", d), "the version log starts closed")
    DietProfileBlocks.set("goals", open: false, d)
    DietProfileBlocks.set("versions", open: true, d)
    XCTAssertFalse(DietProfileBlocks.isOpen("goals", d))
    XCTAssertTrue(DietProfileBlocks.isOpen("versions", d))
  }
}

/// #292: one tap from 采购 to the camera, then Jarvis with the picture and the question.
@MainActor final class ReceiptScanTests: XCTestCase {
  private func ai(_ s: String) -> JarvisAI { JarvisAI(JSONValue.parse(Data(s.utf8)) ?? .null) }

  func testRouteFollowsTheAIInUse() {
    XCTAssertEqual(JarvisRouter.scanRoute(ai(#"{"mode": "none"}"#)), .none)
    XCTAssertEqual(JarvisRouter.scanRoute(ai(#"{"mode": "mcp", "display_name": "Claude"}"#)), .elsewhere)
    XCTAssertEqual(JarvisRouter.scanRoute(ai(#"{"mode": "butler"}"#)), .elsewhere)
    XCTAssertEqual(JarvisRouter.scanRoute(ai(#"{"mode": "api", "vision": true}"#)), .camera)
    XCTAssertEqual(JarvisRouter.scanRoute(ai(#"{"mode": "api", "vision": false}"#)), .noVision)
    XCTAssertEqual(JarvisRouter.scanRoute(ai(#"{"mode": "fallback", "consent": true, "available": true, "vision": true}"#)), .camera)
    XCTAssertEqual(JarvisRouter.scanRoute(ai(#"{"mode": "fallback", "consent": false, "available": false, "reason": "fallback_consent", "message": "agree"}"#)), .consent)
    XCTAssertEqual(JarvisRouter.scanRoute(ai(#"{"mode": "fallback", "consent": true, "available": false, "reason": "quota", "message": "used up"}"#)), .unavailable("used up"))
    XCTAssertEqual(JarvisRouter.scanRoute(ai(#"{"mode": "fallback", "consent": false, "available": false, "reason": "quota", "message": "used up"}"#)), .unavailable("used up"),
                   "the allowance is checked before the terms: no point agreeing first")
    XCTAssertEqual(JarvisRouter.scanRoute(JarvisAI()), .none)
  }

  func testThePromptIsPlainWordsAndTheShoppingPageHasTheTool() {
    XCTAssertFalse(JarvisRouter.receiptPrompt.contains("record_store_prices"), "tool names are the AI's business, not the user's")
    XCTAssertFalse(JarvisRouter.quick(for: "food.shopping").isEmpty)
  }

  func testAPhotoFromTheScanWaitsInTheBoxWithItsQuestion() throws {
    let router = JarvisRouter()
    let chat = JarvisChat(api: APIClient(baseURL: try XCTUnwrap(URL(string: "http://127.0.0.1:9")), token: { nil }))
    router.prepareForTesting(chat: chat, photoPrompt: JarvisRouter.receiptPrompt)
    let image = UIGraphicsImageRenderer(size: CGSize(width: 8, height: 8)).image { _ in }
    router.sendPhoto(image)
    XCTAssertEqual(chat.draft, JarvisRouter.receiptPrompt)
    XCTAssertEqual(chat.attachments.count, 1)
    XCTAssertTrue(chat.items.isEmpty, "nothing goes out before the user sends it")
    XCTAssertEqual(router.sheet, .chat)
  }
}

/// The Today card follows the user's today: their date is the server's, which can differ from the phone's the store starts with.
@MainActor final class TodayFoodCardDayTests: XCTestCase {
  private func session() throws -> AccountSession {
    let cfg = AccountConfig(server: "https://body.example.com", userId: 7, displayName: "Test")
    return AccountSession(config: cfg, api: PathStub.client(), db: try AppDatabase.inMemory(), flags: [])
  }

  private func save(_ s: AccountSession, day: String, kcal: Int) throws {
    let doc = Data(#"{"date": "\#(day)", "nutrition": {"goal": {"kcal": 2000}, "intake": {"kcal": \#(kcal)}, "entries": []}}"#.utf8)
    try s.db.saveDoc(DocKey.today(day), json: doc)
    try s.db.saveDoc(DocKey.todayCurrent, json: doc)
  }

  private func until(_ cond: () -> Bool, _ file: StaticString = #filePath, _ line: UInt = #line) async {
    let deadline = Date().addingTimeInterval(30)
    while !cond(), Date() < deadline { try? await Task.sleep(nanoseconds: 10_000_000) }
    XCTAssertTrue(cond(), "timed out after 30 s", file: file, line: line)
  }

  func testFirstDocumentWithAnotherDateShowsTheCard() async throws {
    let s = try session()
    let store = FoodStore(session: s, cardOnly: true)
    XCTAssertNil(store.nutrition)
    let serverDay = RecordDay.add(store.day, -1)
    try save(s, day: serverDay, kcal: 500)
    await until { store.nutrition != nil }
    XCTAssertEqual(store.day, serverDay)
    XCTAssertEqual(store.today, serverDay)
    XCTAssertTrue(store.isToday)
    XCTAssertEqual(store.nutrition?.intake.kcal, 500)
  }

  func testMidnightMovesTheCardToTheNewDay() async throws {
    let s = try session()
    let first = RecordDay.string(Date())
    try save(s, day: first, kcal: 500)
    let store = FoodStore(session: s, cardOnly: true)
    XCTAssertEqual(store.nutrition?.intake.kcal, 500)
    let next = RecordDay.add(first, 1)
    try save(s, day: next, kcal: 100)
    await until { store.day == next && store.nutrition?.intake.kcal == 100 }
  }

  func testADayLookedBackAtStaysWhenTodayMoves() async throws {
    let s = try session()
    let first = RecordDay.string(Date())
    try save(s, day: first, kcal: 500)
    let store = FoodStore(session: s, cardOnly: true)
    let past = RecordDay.add(first, -3)
    store.select(past)
    XCTAssertEqual(store.day, past)
    let next = RecordDay.add(first, 1)
    try save(s, day: next, kcal: 100)
    await until { store.today == next }
    XCTAssertEqual(store.day, past)
    XCTAssertFalse(store.isToday)
  }
}
