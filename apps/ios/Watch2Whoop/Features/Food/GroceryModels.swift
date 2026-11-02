import Foundation

/// 饮食 → 采购 beyond the list itself: store links, the user's own recorded prices, the product library, what is at home,
/// connected supermarkets. All pure (tests feed JSON, FoodParityTests); field names are the server's (`grocery/api.py`).
/// Same reading as the page (`web/static/grocery.js`). Nothing here visits a store website: links are opened by the user.

/// Amounts as the page prints them: a symbol for the usual currencies, the code otherwise.
enum Money {
  private static let symbols = ["AUD": "A$", "CNY": "¥", "USD": "$", "NZD": "NZ$", "EUR": "€", "GBP": "£", "JPY": "¥", "MYR": "RM"]

  private static func prefix(_ cur: String?, spaced: Bool) -> String {
    guard let cur else { return "" }
    return symbols[cur] ?? (spaced ? "\(cur) " : "")
  }

  /// A$12.50; 2 decimals, none from 100 up.
  static func full(_ v: Double?, _ cur: String?) -> String {
    guard let v else { return "" }
    return prefix(cur, spaced: true) + (v >= 100 ? String(format: "%.0f", v.rounded()) : String(format: "%.2f", v))
  }

  /// A$13 / A$2.5: for tiles and badges at phone width.
  static func short(_ v: Double?, _ cur: String?) -> String {
    guard let v else { return "" }
    if v >= 10 { return prefix(cur, spaced: false) + String(format: "%.0f", v.rounded()) }   // .5 up, as the page's Math.round
    let one = String(format: "%.1f", (v * 10).rounded() / 10)
    return prefix(cur, spaced: false) + (one.hasSuffix(".0") ? String(one.dropLast(2)) : one)
  }

  /// `{"AUD": 12.5}` in a stable order.
  static func amounts(_ j: Any?) -> [(String, Double)] {
    ((j as? [String: Any]) ?? [:]).compactMap { k, v in RecordFormat.double(v).map { (k, $0) } }.sorted { $0.0 < $1.0 }
  }

  static func joined(_ a: [(String, Double)]) -> String? {
    a.isEmpty ? nil : a.map { short($0.1, $0.0) }.joined(separator: " + ")
  }
}

/// A link to a connected supermarket: the saved product page, else a search by name.
struct GroceryLink: Equatable, Identifiable {
  var label: String
  var url: URL
  var isProduct: Bool

  var id: String { url.absoluteString }

  /// Only https links are shown: the app hands them to the system and never loads them itself.
  static func make(label: String?, url: String?, kind: String? = "product") -> GroceryLink? {
    guard let label, let url, let u = URL(string: url), u.scheme?.lowercased() == "https" else { return nil }
    return GroceryLink(label: label, url: u, isProduct: kind == "product")
  }

  static func parse(_ j: Any?) -> [GroceryLink] {
    ((j as? [[String: Any]]) ?? []).compactMap { make(label: $0["label"] as? String, url: $0["url"] as? String, kind: $0["kind"] as? String) }
  }
}

/// The price known for a list line or a product: the user's own recorded one first, else the store's weekly price.
struct GroceryOffer: Equatable {
  var label: String?
  var price: Double?
  var wasPrice: Double?
  var special: String?
  var halfPrice = false
  /// What buying it this week saves (the list line's total, else per package).
  var save: Double?
  var ageDays: Int?
  var stale = false

  static func parse(_ j: Any?) -> GroceryOffer? {
    guard let o = j as? [String: Any] else { return nil }
    let n = RecordFormat.double
    return GroceryOffer(label: o["label"] as? String, price: n(o["price"]), wasPrice: n(o["was_price"]), special: o["special"] as? String,
                        halfPrice: (o["half_price"] as? Bool) ?? false, save: n(o["save_total"]) ?? n(o["save"]),
                        ageDays: (o["age_days"] as? NSNumber)?.intValue, stale: (o["stale"] as? Bool) ?? false)
  }

  var badge: (String, Tone)? {
    if halfPrice {
      if let save, save > 0 {
        let amount = Money.short(save, "AUD")
        return (tr("半价 -\(amount)", "Half price -\(amount)"), .good)
      }
      return (tr("半价", "Half price"), .good)
    }
    return special != nil ? (tr("特价", "On special"), .good) : nil
  }

  static func ageLabel(_ days: Int) -> String {
    days <= 0 ? tr("今天", "Today") : tr("\(days) 天前", "\(days)d ago")
  }
}

/// `list.prices`: totals per store from the user's own recorded prices, what specials saved, what still needs a price.
struct GroceryPrices: Equatable {
  struct Store: Equatable {
    var label: String
    var total: Double
    var items: Int
  }

  var stores: [Store]
  var savings: [(String, Double)]
  var missing: Int
  var stale: Int

  static func == (a: GroceryPrices, b: GroceryPrices) -> Bool {
    a.stores == b.stores && a.missing == b.missing && a.stale == b.stale && a.savings.map(\.1) == b.savings.map(\.1)
  }

  var isEmpty: Bool { stores.isEmpty && missing == 0 && stale == 0 }

  static func parse(_ j: Any?) -> GroceryPrices? {
    guard let o = j as? [String: Any] else { return nil }
    let stores = ((o["by_store"] as? [String: Any]) ?? [:]).values.compactMap { v -> Store? in
      guard let s = v as? [String: Any], let label = s["label"] as? String, let t = RecordFormat.double(s["total"]) else { return nil }
      return Store(label: label, total: t, items: (s["items"] as? NSNumber)?.intValue ?? 0)
    }.sorted { $0.label < $1.label }
    return GroceryPrices(stores: stores, savings: Money.amounts(o["savings"]), missing: (o["missing"] as? [Any])?.count ?? 0,
                         stale: (o["stale"] as? [Any])?.count ?? 0)
  }
}

/// A product of the library (`/grocery/products`).
struct GroceryProduct: Equatable, Identifiable {
  struct Staple: Equatable {
    var everyDays: Int?
    var qty: Double?
    var unit: String?
    var nextDue: String?
    var due: Bool

    /// 「每 7 天 · 2 包」.
    var summary: String {
      let n = RecordFormat.num(qty, 2)
      let amount: String
      switch unit {
      case "pack": amount = tr("\(n) 包", "\(n) pk")
      case "pcs": amount = tr("\(n) 个", "\(n) pcs")
      default: amount = "\(n) \(unit ?? "")"
      }
      return tr("每 \(everyDays ?? 0) 天 · \(amount)", "Every \(everyDays ?? 0) days · \(amount)")
    }
  }

  var id: Int
  var name: String
  var brand: String?
  var store: String?
  var storeLabel: String?
  var categoryLabel: String?
  var sizeLabel: String?
  var price: Double?
  var currency: String?
  var priceOn: String?
  var unitPrice: Double?
  var packageUnit: String?
  var nutrition: [String: Double] = [:]
  var servingSize: Double?
  var servingUnit: String?
  var servingNutrition: [String: Double] = [:]
  var imageURL: URL?
  var ingredients: String?
  /// nil = not recorded; empty = recorded as none.
  var allergens: [String]?
  var traces: [String] = []
  var nutritionSource: String?
  var barcode: String?
  var flags: [String] = []
  var matches: [String] = []
  var links: [GroceryLink] = []
  var staple: Staple?
  var offer: GroceryOffer?

  static func parse(_ p: [String: Any]) -> GroceryProduct? {
    guard let id = (p["id"] as? NSNumber)?.intValue, let name = p["name"] as? String else { return nil }
    let n = RecordFormat.double
    func numbers(_ j: Any?) -> [String: Double] {
      ((j as? [String: Any]) ?? [:]).reduce(into: [:]) { o, kv in if let v = n(kv.value) { o[kv.key] = v } }
    }
    var staple: Staple?
    if (p["staple"] as? Bool) == true, let s = p["staple_info"] as? [String: Any] {
      staple = Staple(everyDays: (s["every_days"] as? NSNumber)?.intValue, qty: n(s["qty"]), unit: s["unit"] as? String,
                      nextDue: s["next_due"] as? String, due: (s["due"] as? Bool) ?? false)
    }
    let image = (p["image_url"] as? String).flatMap(URL.init(string:)).flatMap { $0.scheme?.lowercased() == "https" ? $0 : nil }
    return GroceryProduct(id: id, name: name, brand: p["brand"] as? String, store: p["store"] as? String, storeLabel: p["store_label"] as? String,
                          categoryLabel: p["category_label"] as? String, sizeLabel: p["size_label"] as? String, price: n(p["price"]),
                          currency: p["currency"] as? String, priceOn: p["price_on"] as? String, unitPrice: n(p["unit_price"]),
                          packageUnit: p["package_unit"] as? String, nutrition: numbers(p["nutrition"]), servingSize: n(p["serving_size"]),
                          servingUnit: p["serving_unit"] as? String, servingNutrition: numbers(p["nutrition_serving"]), imageURL: image,
                          ingredients: p["ingredients"] as? String, allergens: p["allergens"] as? [String],
                          traces: (p["allergen_traces"] as? [String]) ?? [], nutritionSource: p["nutrition_source"] as? String,
                          barcode: p["barcode"] as? String, flags: (p["flags"] as? [String]) ?? [], matches: (p["matches"] as? [String]) ?? [],
                          links: GroceryLink.parse(p["links"]), staple: staple, offer: GroceryOffer.parse(p["offer"]))
  }

  /// Search over name, brand, store, category and the foods it stands for.
  func hit(_ query: String) -> Bool {
    let q = query.trimmingCharacters(in: .whitespaces).lowercased()
    guard !q.isEmpty else { return true }
    return ([name, brand, store, categoryLabel].compactMap { $0 } + matches).contains { $0.lowercased().contains(q) }
  }

  /// Size · price on the row.
  var value: String {
    [sizeLabel, price != nil ? Money.full(price, currency) : nil].compactMap { $0 }.joined(separator: " · ")
  }

  /// Per kg / L / piece.
  var unitPriceLabel: String? {
    guard let unitPrice else { return nil }
    let per = packageUnit == "pcs" ? tr("个", "each") : packageUnit == "ml" ? "L" : "kg"
    return "\(Money.full(unitPrice, currency)) / \(per)"
  }

  var perLabel: String { packageUnit == "ml" ? tr("每 100 ml", "Per 100 ml") : tr("每 100 g", "Per 100 g") }

  /// The nutrition table of the product sheet (grocery/params.NUTRITION_KEYS, label order): only what the product has.
  static func nutritionRows(_ n: [String: Double]) -> [(String, String)] {
    let rows: [(String, String, String, Int)] = [
      (tr("热量", "Energy"), "kcal", "kcal", 0), (tr("蛋白质", "Protein"), "protein_g", "g", 1), (tr("碳水", "Carbs"), "carbs_g", "g", 1),
      (tr("脂肪", "Fat"), "fat_g", "g", 1), (tr("饱和脂肪", "Saturated fat"), "sat_fat_g", "g", 1), (tr("反式脂肪", "Trans fat"), "trans_fat_g", "g", 1),
      (tr("糖", "Sugar"), "sugar_g", "g", 1), (tr("添加糖", "Added sugar"), "added_sugar_g", "g", 1), (tr("膳食纤维", "Fibre"), "fiber_g", "g", 1),
      (tr("钠", "Sodium"), "sodium_mg", "mg", 0), (tr("钾", "Potassium"), "potassium_mg", "mg", 0), (tr("钙", "Calcium"), "calcium_mg", "mg", 0),
      (tr("铁", "Iron"), "iron_mg", "mg", 1), (tr("胆固醇", "Cholesterol"), "cholesterol_mg", "mg", 0),
    ]
    return rows.compactMap { label, key, unit, digits in n[key].map { (label, "\(RecordFormat.num($0, digits)) \(unit)") } }
  }
}

/// A store product the user's AI recorded a price for (`/grocery/store-products`).
struct StoreProduct: Equatable, Identifiable {
  var id: Int
  var name: String
  var size: String?
  var storeLabel: String?
  var link: GroceryLink?
  var offer: GroceryOffer?

  static func parse(_ p: [String: Any]) -> StoreProduct? {
    guard let id = (p["id"] as? NSNumber)?.intValue, let name = p["name"] as? String else { return nil }
    let label = p["store_label"] as? String
    return StoreProduct(id: id, name: name, size: p["size"] as? String, storeLabel: label,
                        link: GroceryLink.make(label: label, url: p["url"] as? String), offer: GroceryOffer.parse(p["offer"]))
  }
}

/// One product's price history (`/grocery/store-products/{id}`).
struct PriceHistory: Equatable {
  struct Purchase: Equatable {
    var date: String
    var unitPaid: Double?
    var onSpecial: Bool
    var verdict: String?
  }

  var name: String
  var storeLabel: String?
  var size: String?
  var link: GroceryLink?
  var points: [Double]
  var latest: Double?
  var latestAge: Int?
  var low: Double?
  var average: Double?
  var specials: Int
  var specialsEvery: Int?
  var lastPurchase: Purchase?

  static func parse(_ d: [String: Any]?) -> PriceHistory? {
    guard let d, let name = d["name"] as? String else { return nil }
    let n = RecordFormat.double
    let sp = d["specials"] as? [String: Any]
    let lb = d["last_purchase"] as? [String: Any]
    let label = d["store_label"] as? String
    return PriceHistory(name: name, storeLabel: label, size: d["size"] as? String, link: GroceryLink.make(label: label, url: d["url"] as? String),
                        points: ((d["points"] as? [[String: Any]]) ?? []).compactMap { n($0["price"]) }, latest: n(d["latest"]),
                        latestAge: (d["latest_age_days"] as? NSNumber)?.intValue, low: n(d["min"]), average: n(d["avg"]),
                        specials: (sp?["count"] as? NSNumber)?.intValue ?? 0, specialsEvery: (sp?["every_days"] as? NSNumber)?.intValue,
                        lastPurchase: lb.flatMap { b in
                          (b["date"] as? String).map {
                            Purchase(date: $0, unitPaid: n(b["unit_paid"]), onSpecial: (b["on_special"] as? Bool) ?? false, verdict: b["verdict"] as? String)
                          }
                        })
  }

  /// 「3 次 · 约每 14 天」, 「没见过」.
  var specialsLabel: String {
    guard specials > 0 else { return tr("没见过", "Never seen") }
    if let e = specialsEvery { return tr("\(specials) 次 · 约每 \(e) 天", "\(specials) times · about every \(e) days") }
    return tr("\(specials) 次", "\(specials) times")
  }
}

/// What is at home (`/grocery/pantry`), with the writes still in the queue laid over it.
struct PantryItem: Equatable, Identifiable {
  var id: Int
  var name: String
  var quantity: Double
  var unit: String
  var label: String
  var productId: Int?
  var categoryLabel: String
  var daysAgo: Int?
  var bestBefore: String?
  var daysLeft: Int?
  var expiring = false
  var pending: PendingRecord?

  static func == (a: PantryItem, b: PantryItem) -> Bool {
    a.id == b.id && a.name == b.name && a.quantity == b.quantity && a.label == b.label && a.daysAgo == b.daysAgo
      && a.daysLeft == b.daysLeft && a.pending == b.pending
  }

  static func parse(_ p: [String: Any]) -> PantryItem? {
    guard let id = (p["id"] as? NSNumber)?.intValue, let name = p["name"] as? String, let q = RecordFormat.double(p["quantity"]),
          let unit = p["unit"] as? String else { return nil }
    return PantryItem(id: id, name: name, quantity: q, unit: unit, label: (p["label"] as? String) ?? "\(RecordFormat.num(q, 1)) \(unit)",
                      productId: (p["product_id"] as? NSNumber)?.intValue, categoryLabel: (p["category_label"] as? String) ?? tr("其他", "Other"),
                      daysAgo: (p["days_ago"] as? NSNumber)?.intValue, bestBefore: p["best_before"] as? String,
                      daysLeft: (p["days_left"] as? NSNumber)?.intValue, expiring: (p["expiring"] as? Bool) ?? false)
  }

  /// A write is the whole line (name, quantity 0 = used up), so it lies over the server's copy by name; a line put back
  /// (undo of a used-up that already went out) comes from its overlay, which keeps the whole row.
  static func build(_ list: [[String: Any]]?, pending: [PendingRecord]) -> [PantryItem] {
    var items = (list ?? []).compactMap(parse)
    for p in pending where p.kind == FoodKind.pantry {
      let j = p.json
      guard let name = j["name"] as? String else { continue }
      let q = RecordFormat.double(j["quantity"]) ?? 0
      if let i = items.firstIndex(where: { $0.name.lowercased() == name.lowercased() }) {
        if q <= 0 { items.remove(at: i) } else { items[i].pending = p }
      } else if q > 0, var row = parse(j) {
        row.pending = p
        items.append(row)
      }
    }
    return items
  }

  /// In the server's order: by category, then name.
  static func byCategory(_ items: [PantryItem]) -> [(String, [PantryItem])] {
    var order: [String] = []
    var groups: [String: [PantryItem]] = [:]
    for i in items {
      if groups[i.categoryLabel] == nil { order.append(i.categoryLabel) }
      groups[i.categoryLabel, default: []].append(i)
    }
    return order.map { ($0, groups[$0] ?? []) }
  }

  /// 「3 天后到期」 / 「今天到期」 / 「已过期」, when the best before is close.
  var expiry: (String, Tone)? {
    guard expiring, let left = daysLeft else { return nil }
    if left < 0 { return (tr("已过期", "Expired"), .bad) }
    if left == 0 { return (tr("今天到期", "Expires today"), .bad) }
    return (tr("\(left) 天后到期", "Expires in \(left)d"), .mid)
  }

  var ageLabel: String? {
    guard let d = daysAgo else { return nil }
    return d <= 0 ? tr("今天更新", "Updated today") : tr("\(d) 天前更新", "Updated \(d)d ago")
  }

  /// The overlay of a write that puts the line back: everything `parse` needs to draw it before the server has it.
  var restoreJSON: [String: Any] {
    var o: [String: Any] = ["id": id, "name": name, "quantity": quantity, "unit": unit, "label": label, "category_label": categoryLabel]
    if let productId { o["product_id"] = productId }
    if let bestBefore { o["best_before"] = bestBefore }
    if let daysLeft { o["days_left"] = daysLeft }
    if let daysAgo { o["days_ago"] = daysAgo }
    if expiring { o["expiring"] = true }
    return o
  }

  /// The body of `PUT /grocery/pantry`: `quantity` for this line (0 = used up).
  func putBody(quantity q: Double) -> [String: Any] {
    var item: [String: Any] = ["name": name, "quantity": q, "unit": unit]
    if q > 0 {
      if let productId { item["product_id"] = productId }
      if let bestBefore { item["best_before"] = bestBefore }
    }
    return ["items": [item]]
  }
}

/// A supermarket the user can connect (`/grocery/stores`): links and recorded prices only show for connected ones.
struct GroceryStoreOption: Equatable, Identifiable {
  var key: String
  var label: String
  var connected: Bool
  var id: String { key }

  static func parse(_ j: [String: Any]?) -> [GroceryStoreOption] {
    ((j?["stores"] as? [[String: Any]]) ?? []).compactMap { s in
      guard let k = s["key"] as? String, let l = s["label"] as? String else { return nil }
      return GroceryStoreOption(key: k, label: l, connected: (s["connected"] as? Bool) ?? false)
    }
  }

  /// The connected set after tapping `key`.
  static func toggled(_ list: [GroceryStoreOption], _ key: String) -> [String] {
    list.filter { $0.key == key ? !$0.connected : $0.connected }.map(\.key)
  }
}

/// `/grocery/specials`: half price this week, from what the user's own AI recorded (and the store sync, which stays off).
struct GrocerySpecials: Equatable {
  struct Item: Equatable, Identifiable {
    var name: String
    var label: String
    var price: Double?
    var wasPrice: Double?
    var flag: String?
    var link: GroceryLink?
    var id: String { "\(label)|\(name)" }
  }

  var weekStart: String?
  var validTo: String?
  var items: [Item]

  static func parse(_ j: [String: Any]?) -> GrocerySpecials? {
    guard let j else { return nil }
    let items = ((j["items"] as? [[String: Any]]) ?? []).compactMap { x -> Item? in
      guard let name = x["name"] as? String else { return nil }
      let label = (x["label"] as? String) ?? ""
      return Item(name: name.hasPrefix("\(label) ") ? String(name.dropFirst(label.count + 1)) : name, label: label,
                  price: RecordFormat.double(x["price"]), wasPrice: RecordFormat.double(x["was_price"]),
                  flag: (x["flags"] as? [String])?.first, link: GroceryLink.make(label: label, url: x["url"] as? String))
    }
    return GrocerySpecials(weekStart: j["week_start"] as? String, validTo: j["valid_to"] as? String, items: items)
  }
}
