import SwiftUI

/// 饮食 → 采购: a photo of a receipt or price tag for the user's AI, this week's list by category (tap a line to tick it; last
/// tick wins, queued offline), what is at home, half price from the user's own records, the price records, the product
/// library and which supermarkets are connected. Same reading as the page (web/static/grocery.js). Input comes from the
/// user's AI; store links open in the system, the app never loads a store website itself.
struct GroceryView: View {
  @ObservedObject var store: FoodStore
  @Binding var toast: Toast?

  private var needsConnection: String { tr("需要联网", "Needs a connection") }

  var body: some View {
    VStack(alignment: .leading, spacing: 18) {
      SecondaryButton(icon: .camera, title: tr("拍小票 / 价签", "Scan receipt / price tag")) { scan() }
        .opacity(store.offline ? 0.5 : 1)
      listSection
      if store.pantryOn { pantrySection.id("n5.pantry") }
      specialsSection
      libraryRows.id("n5.library")
      storesSection.id("n5.stores")
    }
    .task { await store.refreshShopping() }
  }

  /// One tap (#292): the camera, then Jarvis with the picture and the question in the box. The AI records the prices.
  private func scan() {
    if store.offline {
      toast = Toast(needsConnection)
      return
    }
    JarvisRouter.current?.scanPrices()
  }

  // MARK: - this week's list

  /// 复制 / 分享 (FB-19): the whole list as plain text, grouped by category, for the supermarket. The system share sheet also
  /// has Copy; the copy button here copies at once and says so.
  @ViewBuilder private func exportButtons(_ g: GroceryList) -> some View {
    let text = g.exportText()
    if !text.isEmpty {
      Button { copy(text, count: g.toBuy.count) } label: {
        Icon(.copy, size: 18).foregroundColor(Palette.text2).frame(width: 32, height: 32)
      }
      .buttonStyle(PressScale())
      .accessibilityLabel(tr("复制清单", "Copy list"))
      ShareLink(item: text, subject: Text(g.exportTitle)) {
        Icon(.share, size: 18).foregroundColor(Palette.text2).frame(width: 32, height: 32)
      }
      .accessibilityLabel(tr("分享清单", "Share list"))
    }
  }

  private func copy(_ text: String, count: Int) {
    UIPasteboard.general.string = text
    UINotificationFeedbackGenerator().notificationOccurred(.success)
    toast = Toast(tr("已复制 · \(count) 项", "Copied · \(count) items"))
  }

  @ViewBuilder private var listSection: some View {
    if let g = store.grocery, !g.items.isEmpty {
      HStack(spacing: 4) {
        SectionHeader(title: tr("本周采购", "This week's list"))
        if let a = g.week, let b = g.weekEnd {
          Text("\(RecordDay.short(a))–\(RecordDay.short(b))").font(Typo.label.monospacedDigit()).foregroundColor(Palette.text3).lineLimit(1)
        }
        exportButtons(g)
      }
      stats(g)
      priceBadges(g)
      ProgressBar(fraction: Double(g.bought) / Double(max(1, g.items.count)))
      ForEach(g.byCategory(), id: \.0) { cat, items in
        RowGroup(title: cat) {
          ForEach(Array(items.enumerated()), id: \.element.id) { n, item in
            if n > 0 { RowDivider(inset: 34) }
            GroceryItemRow(item: item, pantryOn: store.pantryOn, staplesOn: store.staplesOn) {
              Task { await store.setBought(item, !item.bought) }
            }
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

  private func stats(_ g: GroceryList) -> some View {
    HStack(spacing: 8) {
      MetricTile(label: tr("已买", "Bought"), value: "\(g.bought)", unit: "/ \(g.items.count)")
      MetricTile(label: tr("估价", "Estimate"), value: Money.joined(g.estTotal) ?? "—")
      MetricTile(label: tr("未估价", "No estimate"), value: "\(g.unpriced)", unit: g.unpriced == 1 ? tr("项", "item") : tr("项", "items"))
      if g.halfItems > 0 {
        MetricTile(label: tr("半价省 · \(g.halfItems)", "Half off · \(g.halfItems)"), value: Money.joined(g.halfSave) ?? "—", tone: .good)
      }
    }
  }

  /// Totals per store from the user's own recorded prices, what specials saved, what still needs a price, health hints.
  @ViewBuilder private func priceBadges(_ g: GroceryList) -> some View {
    let p = g.prices
    if (p.map { !$0.isEmpty } ?? false) || !g.warnings.isEmpty {
      ScrollView(.horizontal, showsIndicators: false) {
        HStack(spacing: 6) {
          ForEach(p?.stores ?? [], id: \.label) { s in Badge(label: "\(s.label) \(Money.short(s.total, "AUD")) · \(s.items)") }
          ForEach(p?.savings ?? [], id: \.0) { c, v in Badge(label: tr("特价省 \(Money.short(v, c))", "Specials saved \(Money.short(v, c))"), tone: .good) }
          if let m = p?.missing, m > 0 { Badge(label: tr("缺价 \(m)", "\(m) no price"), tone: .mid) }
          if let s = p?.stale, s > 0 { Badge(label: tr("旧价 \(s)", "\(s) old price")) }
          ForEach(g.warnings, id: \.label) { w in Badge(label: w.label, tone: w.code == "product" ? .mid : .bad) }
        }
      }
    }
  }

  // MARK: - at home

  private var pantrySection: some View {
    VStack(alignment: .leading, spacing: 10) {
      HStack(spacing: 6) {
        SectionHeader(title: tr("家里存货", "At home"))
        if !store.pantry.isEmpty { Text("\(store.pantry.count)").font(Typo.label.monospacedDigit()).foregroundColor(Palette.text3) }
      }
      if store.pantryAutoOn, let auto = store.pantryAuto {
        Text(auto.label).font(Typo.caption).foregroundColor(Palette.text2)
      }
      if store.pantry.isEmpty {
        Card { EmptyNote(text: tr("暂无存货", "Nothing at home yet")) }
      } else {
        ForEach(PantryItem.byCategory(store.pantry), id: \.0) { cat, items in
          RowGroup(title: cat) {
            ForEach(Array(items.enumerated()), id: \.element.id) { n, item in
              if n > 0 { RowDivider(inset: 0) }
              pantryRow(item)
            }
          }
        }
      }
    }
  }

  private func pantryRow(_ p: PantryItem) -> some View {
    HStack(spacing: 10) {
      VStack(alignment: .leading, spacing: 3) {
        HStack(spacing: 6) {
          Text(p.name).font(Typo.body).foregroundColor(Palette.text).lineLimit(2)
          if let m = p.pending { PendingMark(needsAttention: m.needsAttention) }
        }
        if store.pantryAutoOn, let e = p.expiry {
          Badge(label: e.0, tone: e.1)
        } else if let a = p.ageLabel {
          Text(a).font(Typo.caption).foregroundColor(Palette.text3)
        }
      }
      Spacer(minLength: 8)
      Text(p.label).font(Typo.body.monospacedDigit()).foregroundColor(Palette.text2)
      Button { usedUp(p) } label: {
        Text(tr("用完了", "Used up")).font(.system(size: 12, weight: .medium)).foregroundColor(Palette.text)
          .padding(.vertical, 6).padding(.horizontal, 10)
          .background(Capsule().fill(Palette.bg))
      }
      .buttonStyle(PressScale())
      .accessibilityLabel(tr("\(p.name) 用完了", "\(p.name) used up"))
    }
    .padding(.vertical, 10)
  }

  private func usedUp(_ p: PantryItem) {
    Task {
      let out = await store.usedUp(p)
      guard out.sent || out.queued else {
        toast = Toast(out.message)
        return
      }
      toast = Toast(out.sent ? tr("已用完 · \(p.name)", "Used up · \(p.name)") : out.message, actionTitle: tr("撤销", "Undo")) {
        Task {
          let back = await store.restorePantry(p, after: out)
          toast = Toast(back.sent ? tr("已撤销 · \(p.name)", "Undone · \(p.name)") : back.message)
        }
      }
    }
  }

  // MARK: - half price

  @ViewBuilder private var specialsSection: some View {
    if let sp = store.specials, !sp.items.isEmpty {
      VStack(alignment: .leading, spacing: 10) {
        HStack(spacing: 6) {
          SectionHeader(title: tr("本周半价", "Half price this week"))
          if let a = sp.weekStart, let b = sp.validTo {
            Text("\(RecordDay.short(a))–\(RecordDay.short(b))").font(Typo.label.monospacedDigit()).foregroundColor(Palette.text3)
          }
        }
        RowGroup {
          ForEach(Array(sp.items.enumerated()), id: \.element.id) { n, x in
            if n > 0 { RowDivider(inset: 0) }
            specialRow(x)
          }
        }
      }
    }
  }

  @ViewBuilder private func specialRow(_ x: GrocerySpecials.Item) -> some View {
    let row = HStack(spacing: 8) {
      Text(x.name).font(Typo.body).foregroundColor(Palette.text).lineLimit(2).multilineTextAlignment(.leading)
      if let f = x.flag { Badge(label: f, tone: .mid) }
      Spacer(minLength: 6)
      VStack(alignment: .trailing, spacing: 2) {
        Text("\(x.label) \(Money.full(x.price, "AUD"))").font(Typo.body.monospacedDigit()).foregroundColor(Palette.text)
        if let w = x.wasPrice { Text(Money.full(w, "AUD")).font(Typo.caption.monospacedDigit()).foregroundColor(Palette.text3).strikethrough() }
      }
    }
    .padding(.vertical, 10)
    if let l = x.link {
      Link(destination: l.url) { row }.buttonStyle(PressScale())
    } else {
      row
    }
  }

  // MARK: - records and library

  private var libraryRows: some View {
    RowGroup {
      if !store.storeProducts.isEmpty {
        NavigationLink {
          StoreProductsView(store: store)
        } label: {
          navRow(icon: .chartLine, title: tr("价格记录", "Price records"), count: store.storeProducts.count)
        }
        .buttonStyle(PressScale())
        RowDivider(inset: 32)
      }
      NavigationLink {
        GroceryProductsView(store: store)
      } label: {
        navRow(icon: .search, title: tr("商品库", "Product library"), count: store.products.count)
      }
      .buttonStyle(PressScale())
    }
  }

  private func navRow(icon: Icon.Name, title: String, count: Int) -> some View {
    HStack(spacing: 12) {
      Icon(icon, size: 20).foregroundColor(Palette.text2)
      Text(title).font(Typo.body).foregroundColor(Palette.text)
      Spacer()
      Text("\(count)").font(Typo.body.monospacedDigit()).foregroundColor(Palette.text2)
      Icon(.chevronRight, size: 16).foregroundColor(Palette.text3)
    }
    .padding(.vertical, 12)
    .contentShape(Rectangle())
  }

  // MARK: - supermarkets

  @ViewBuilder private var storesSection: some View {
    if !store.stores.isEmpty {
      VStack(alignment: .leading, spacing: 10) {
        SectionHeader(title: tr("超市", "Supermarkets"))
        HStack(spacing: 8) {
          ForEach(store.stores) { s in
            Button { toggle(s) } label: {
              Text(s.label).font(.system(size: 14, weight: .medium))
                .foregroundColor(s.connected ? Palette.onAccent : Palette.text2)
                .padding(.vertical, 8).padding(.horizontal, 16)
                .background(Capsule().fill(s.connected ? Palette.accent : Palette.surface))
            }
            .buttonStyle(PressScale())
            .opacity(store.offline ? 0.5 : 1)
            .accessibilityAddTraits(s.connected ? .isSelected : [])
          }
        }
      }
    }
  }

  private func toggle(_ s: GroceryStoreOption) {
    if store.offline {
      toast = Toast(needsConnection)
      return
    }
    Task {
      if let err = await store.toggleStore(s.key) { toast = Toast(err) }
    }
  }
}

/// One line of the list: tick, name, badges, quantity · cost. Store links under it stay separate from the tick.
private struct GroceryItemRow: View {
  let item: GroceryList.Item
  let pantryOn: Bool
  let staplesOn: Bool
  let tick: () -> Void

  private var badges: [(String, Tone)] {
    var out: [(String, Tone)] = []
    if let b = item.badge { out.append(b) }
    if item.staple && staplesOn { out.append((tr("常买", "Usual"), .text)) }
    if !item.bought, pantryOn, let h = item.pantryLabel { out.append((h, .text)) }
    return out
  }

  var body: some View {
    VStack(alignment: .leading, spacing: 0) {
      Button(action: tick) {
        HStack(spacing: 12) {
          ZStack {
            Circle().stroke(item.bought ? Palette.good : Palette.text3, lineWidth: 1.5).frame(width: 22, height: 22)
            if item.bought { Icon(.check, size: 14).foregroundColor(Palette.good) }
          }
          VStack(alignment: .leading, spacing: 3) {
            Text(item.name).font(Typo.body).foregroundColor(item.bought ? Palette.text3 : Palette.text)
              .strikethrough(item.bought, color: Palette.text3).multilineTextAlignment(.leading)
            if let note = item.note { Text(note).font(Typo.caption).foregroundColor(Palette.text3).multilineTextAlignment(.leading) }
            if !badges.isEmpty {
              HStack(spacing: 4) { ForEach(badges, id: \.0) { Badge(label: $0.0, tone: $0.1) } }
            }
          }
          if let p = item.pending { PendingMark(needsAttention: p.needsAttention) }
          Spacer()
          VStack(alignment: .trailing, spacing: 2) {
            if !item.value.isEmpty {
              Text(item.value).font(Typo.caption.monospacedDigit()).foregroundColor(Palette.text2).multilineTextAlignment(.trailing)
            }
            if let age = item.offerAge {
              Text(age).font(Typo.label).foregroundColor(item.offer?.stale == true ? Palette.mid : Palette.text3)
            }
          }
        }
        .padding(.vertical, 11)
        .contentShape(Rectangle())
      }
      .buttonStyle(PressScale())
      .accessibilityAddTraits(item.bought ? .isSelected : [])
      if !item.bought && !item.links.isEmpty {
        StoreLinks(links: item.links, name: item.name).padding(.leading, 34).padding(.bottom, 10)
      }
    }
  }
}

/// Small store chips next to a row: a filled one is the saved product page, a plain one a search by name.
struct StoreLinks: View {
  let links: [GroceryLink]
  let name: String
  /// A plain chip's background: it has to differ from what is behind it.
  var plain = Palette.bg

  var body: some View {
    HStack(spacing: 6) {
      ForEach(links) { l in
        Link(destination: l.url) {
          Text(l.label).font(.system(size: 12, weight: .medium))
            .foregroundColor(l.isProduct ? Palette.text : Palette.text2)
            .padding(.vertical, 4).padding(.horizontal, 10)
            .background(Capsule().fill(l.isProduct ? Palette.surface2 : plain))
        }
        .accessibilityLabel(l.isProduct ? tr("\(l.label) 商品页：\(name)", "\(l.label) product page: \(name)")
                              : tr("\(l.label) 搜索：\(name)", "\(l.label) search: \(name)"))
      }
    }
  }
}

/// 采购 → 商品库: search and the user's products; tapping one opens its sheet.
struct GroceryProductsView: View {
  @ObservedObject var store: FoodStore
  @State private var query = ""
  @State private var open: GroceryProduct?

  var body: some View {
    ScrollView(.vertical, showsIndicators: false) {
      VStack(alignment: .leading, spacing: 14) {
        HStack(spacing: 8) {
          Icon(.search, size: 18).foregroundColor(Palette.text3)
          TextField(tr("搜索", "Search"), text: $query)
            .font(Typo.body).textInputAutocapitalization(.never).autocorrectionDisabled()
            .accessibilityLabel(tr("搜索商品", "Search products"))
        }
        .padding(.horizontal, 14).frame(minHeight: 44)
        .background(RoundedRectangle(cornerRadius: 12, style: .continuous).fill(Palette.surface))
        let shown = store.products.filter { $0.hit(query) }
        if shown.isEmpty {
          EmptyNote(text: query.isEmpty ? tr("暂无商品", "No products yet") : tr("没有找到", "Nothing found"))
        } else {
          LazyVStack(alignment: .leading, spacing: 8) {
            ForEach(shown) { p in row(p) }
          }
        }
      }
      .padding(.horizontal, Metrics.gutter).padding(.vertical, 12)
      .frame(maxWidth: Metrics.maxContentWidth).frame(maxWidth: .infinity)
    }
    .background(Palette.bg.ignoresSafeArea())
    .navigationTitle(tr("商品库", "Product library"))
    .navigationBarTitleDisplayMode(.inline)
    .refreshable { await store.refreshShopping() }
    .sheet(item: $open) { p in
      SheetContainer(title: p.name, close: { open = nil }) {
        GroceryProductSheet(p: p, staplesOn: store.staplesOn)
      }
      .presentationDetents([.medium, .large])
      .presentationDragIndicator(.visible)
    }
  }

  private func row(_ p: GroceryProduct) -> some View {
    VStack(alignment: .leading, spacing: 8) {
      Button { open = p } label: {
        HStack(alignment: .firstTextBaseline, spacing: 8) {
          VStack(alignment: .leading, spacing: 3) {
            Text(p.name).font(Typo.body).foregroundColor(Palette.text).multilineTextAlignment(.leading)
            if let b = p.brand { Text(b).font(Typo.caption).foregroundColor(Palette.text3) }
            let badges = rowBadges(p)
            if !badges.isEmpty { HStack(spacing: 4) { ForEach(badges, id: \.0) { Badge(label: $0.0, tone: $0.1) } } }
          }
          Spacer(minLength: 8)
          Text(p.value).font(Typo.caption.monospacedDigit()).foregroundColor(Palette.text2).multilineTextAlignment(.trailing)
        }
        .contentShape(Rectangle())
      }
      .buttonStyle(PressScale())
      if !p.links.isEmpty { StoreLinks(links: p.links, name: p.name) }
    }
    .padding(12)
    .frame(maxWidth: .infinity, alignment: .leading)
    .background(RoundedRectangle(cornerRadius: Metrics.tileRadius, style: .continuous).fill(Palette.surface))
  }

  private func rowBadges(_ p: GroceryProduct) -> [(String, Tone)] {
    var out: [(String, Tone)] = []
    if store.staplesOn, let s = p.staple, let d = s.nextDue {
      out.append((tr("下次该买 \(RecordDay.short(d))", "Next \(RecordDay.short(d))"), s.due ? .mid : .text))
    }
    if let o = p.offer, o.halfPrice, let b = o.badge { out.append(b) }
    if let f = p.flags.first { out.append((f, .mid)) }
    return out
  }
}

/// One product: flags, usual-item info, price, nutrition per 100 g and per serving, allergens, ingredients, where it came from.
struct GroceryProductSheet: View {
  let p: GroceryProduct
  let staplesOn: Bool

  var body: some View {
    VStack(alignment: .leading, spacing: 0) {
      if !p.flags.isEmpty {
        HStack(spacing: 4) { ForEach(p.flags, id: \.self) { Badge(label: $0, tone: .mid) } }.padding(.bottom, 10)
      }
      if staplesOn, let s = p.staple {
        KeyValueRow(label: tr("常买", "Usual item"), value: s.summary)
        if let d = s.nextDue { KeyValueRow(label: tr("下次该买", "Next due"), value: RecordDay.short(d)) }
      }
      kv(tr("品牌", "Brand"), p.brand)
      kv(tr("门店", "Store"), p.storeLabel)
      if !p.links.isEmpty { linkRow(tr("链接", "Link")) { StoreLinks(links: p.links, name: p.name, plain: Palette.inSheet) } }
      kv(tr("类别", "Category"), p.categoryLabel)
      kv(tr("规格", "Size"), p.sizeLabel)
      kv(tr("价格", "Price"), p.price != nil ? Money.full(p.price, p.currency) : nil)
      kv(tr("单价", "Unit price"), p.unitPriceLabel)
      kv(tr("价格日期", "Price date"), p.priceOn.map(RecordDay.short))
      if let o = p.offer {
        let was = o.wasPrice.map { " / \(Money.full($0, "AUD"))" } ?? ""
        let half = o.halfPrice ? " · " + tr("半价", "half price") : ""
        KeyValueRow(label: tr("本周价", "This week"), value: "\(o.label ?? "") \(Money.full(o.price, "AUD"))\(was)\(half)")
      }
      nutrition(title: p.perLabel, GroceryProduct.nutritionRows(p.nutrition))
      if let size = p.servingSize {
        let serving = [RecordFormat.num(size, 1), p.servingUnit].compactMap { $0 }.joined(separator: " ")
        nutrition(title: tr("每份 · \(serving)", "Per serving · \(serving)"), GroceryProduct.nutritionRows(p.servingNutrition))
      }
      if let a = p.allergens {
        if a.isEmpty {
          KeyValueRow(label: tr("过敏原", "Allergens"), value: tr("无", "None"))
        } else {
          linkRow(tr("过敏原", "Allergens")) { badges(a, tone: .mid) }
        }
      }
      if !p.traces.isEmpty { linkRow(tr("可能含有", "May contain")) { badges(p.traces, tone: .text) } }
      kv(tr("配料", "Ingredients"), p.ingredients)
      if let u = p.imageURL {
        linkRow(tr("图片", "Picture")) {
          Link(tr("查看", "View"), destination: u).font(Typo.body).foregroundColor(Palette.text)
        }
      }
      kv(tr("来源", "Source"), p.nutritionSource)
      kv(tr("条码", "Barcode"), p.barcode)
    }
  }

  @ViewBuilder private func kv(_ label: String, _ value: String?) -> some View {
    if let value, !value.isEmpty { KeyValueRow(label: label, value: value) }
  }

  private func linkRow<C: View>(_ label: String, @ViewBuilder _ content: () -> C) -> some View {
    HStack(alignment: .firstTextBaseline) {
      Text(label).font(Typo.body).foregroundColor(Palette.text2)
      Spacer(minLength: 12)
      content()
    }
    .padding(.vertical, 10)
    .overlay(Rectangle().fill(Palette.hair).frame(height: 1), alignment: .top)
  }

  private func badges(_ xs: [String], tone: Tone) -> some View {
    VStack(alignment: .trailing, spacing: 4) { ForEach(xs, id: \.self) { Badge(label: $0, tone: tone) } }
  }

  @ViewBuilder private func nutrition(title: String, _ rows: [(String, String)]) -> some View {
    if !rows.isEmpty {
      SectionHeader(title: title).padding(.top, 16).padding(.bottom, 6)
      ForEach(rows, id: \.0) { KeyValueRow(label: $0.0, value: $0.1) }
    }
  }
}

/// 采购 → 价格记录: the store products the user's AI recorded, each with its latest price and how old it is.
struct StoreProductsView: View {
  @ObservedObject var store: FoodStore
  @State private var open: StoreProduct?

  var body: some View {
    ScrollView(.vertical, showsIndicators: false) {
      LazyVStack(alignment: .leading, spacing: 8) {
        ForEach(store.storeProducts) { p in row(p) }
      }
      .padding(.horizontal, Metrics.gutter).padding(.vertical, 12)
      .frame(maxWidth: Metrics.maxContentWidth).frame(maxWidth: .infinity)
    }
    .background(Palette.bg.ignoresSafeArea())
    .navigationTitle(tr("价格记录", "Price records"))
    .navigationBarTitleDisplayMode(.inline)
    .refreshable { await store.refreshShopping() }
    .sheet(item: $open) { p in
      SheetContainer(title: p.name, close: { open = nil }) {
        PriceHistorySheet(product: p, store: store)
      }
      .presentationDetents([.medium, .large])
      .presentationDragIndicator(.visible)
    }
  }

  private func row(_ p: StoreProduct) -> some View {
    VStack(alignment: .leading, spacing: 8) {
      Button { open = p } label: {
        HStack(alignment: .firstTextBaseline, spacing: 8) {
          VStack(alignment: .leading, spacing: 3) {
            Text(p.name).font(Typo.body).foregroundColor(Palette.text).multilineTextAlignment(.leading)
            if let s = p.size { Text(s).font(Typo.caption).foregroundColor(Palette.text3) }
            if let b = p.offer?.badge { Badge(label: b.0, tone: b.1) }
          }
          Spacer(minLength: 8)
          VStack(alignment: .trailing, spacing: 2) {
            Text(p.offer.flatMap { $0.price }.map { Money.full($0, "AUD") } ?? "—").font(Typo.body.monospacedDigit()).foregroundColor(Palette.text)
            if let o = p.offer, let d = o.ageDays {
              Text(GroceryOffer.ageLabel(d)).font(Typo.label).foregroundColor(o.stale ? Palette.mid : Palette.text3)
            }
          }
        }
        .contentShape(Rectangle())
      }
      .buttonStyle(PressScale())
      if let l = p.link {
        StoreLinks(links: [l], name: p.name)
      } else if let s = p.storeLabel {
        Badge(label: s)
      }
    }
    .padding(12)
    .frame(maxWidth: .infinity, alignment: .leading)
    .background(RoundedRectangle(cornerRadius: Metrics.tileRadius, style: .continuous).fill(Palette.surface))
  }
}

/// One store product's price history: the curve, latest / lowest / average, how often on special, the last purchase.
struct PriceHistorySheet: View {
  let product: StoreProduct
  @ObservedObject var store: FoodStore
  @State private var h: PriceHistory?
  @State private var loaded = false

  var body: some View {
    VStack(alignment: .leading, spacing: 0) {
      if let h {
        if h.points.count > 1 {
          Sparkline(values: h.points.map { Optional($0) }).frame(height: 64).padding(.bottom, 12)
            .accessibilityHidden(true)
        }
        kv(tr("超市", "Store"), h.storeLabel)
        kv(tr("规格", "Size"), h.size)
        kv(tr("最近", "Latest"), h.latest.map { v in
          [Money.full(v, "AUD"), h.latestAge.map(GroceryOffer.ageLabel)].compactMap { $0 }.joined(separator: " · ")
        })
        kv(tr("最低", "Lowest"), h.low.map { Money.full($0, "AUD") })
        kv(tr("平均", "Average"), h.average.map { Money.full($0, "AUD") })
        KeyValueRow(label: tr("打折", "Specials"), value: h.specialsLabel)
        if let b = h.lastPurchase {
          let paid = b.unitPaid.map { Money.full($0, "AUD") } ?? "—"
          let special = b.onSpecial ? " · " + tr("特价", "on special") : ""
          KeyValueRow(label: tr("上次买", "Last bought"), value: "\(RecordDay.short(b.date)) · \(paid)\(special)")
          if let v = b.verdict { verdictBadge(v).padding(.top, 8) }
        }
        if let l = h.link {
          HStack {
            Text(tr("链接", "Link")).font(Typo.body).foregroundColor(Palette.text2)
            Spacer()
            StoreLinks(links: [l], name: h.name, plain: Palette.inSheet)
          }
          .padding(.vertical, 10)
          .overlay(Rectangle().fill(Palette.hair).frame(height: 1), alignment: .top)
        }
      } else if loaded {
        EmptyNote(text: tr("没有找到", "Nothing found"))
      } else {
        ProgressView().frame(maxWidth: .infinity)
      }
    }
    .task {
      h = store.priceHistory(product.id)
      if let d = await store.loadPriceHistory(product.id) { h = d }
      loaded = true
    }
  }

  @ViewBuilder private func kv(_ label: String, _ value: String?) -> some View {
    if let value, !value.isEmpty { KeyValueRow(label: label, value: value) }
  }

  private func verdictBadge(_ v: String) -> Badge {
    switch v {
    case "cheap": return Badge(label: tr("买得便宜", "Good price"), tone: .good)
    case "dear": return Badge(label: tr("买贵了", "Paid too much"), tone: .bad)
    default: return Badge(label: tr("正常", "Normal"))
    }
  }
}
