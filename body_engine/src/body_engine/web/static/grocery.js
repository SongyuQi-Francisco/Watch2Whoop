/* Grocery (饮食 → 采购, docs/grocery-plan.md): this week's shopping list (tap to tick) and the product library.
   Separate from app.js: registered in window.BodyPlugins with init(ctx), foodShop(info) -> HTML, bindFoodShop(view).
   ctx = { api, openSheet, closeSheet, toast, esc }; info = { owner: "me" | id, self, date }.
   Endpoints (scope nutrition): GET /v1/users/{u}/grocery/list?week=, PATCH …/list/items/{id} {bought}, GET …/products.
   Input comes from the user's AI (save_product, generate_shopping_list); this page only shows numbers and short labels.
   Store links (item.links / product.links from the server: the saved Coles / Woolworths product page, else a search by name) are plain
   <a href>s next to the row. Inside the iOS app (window.Watch2Whoop) no target: the WebView's navigation delegate hands cross-origin
   main-frame links to the system, which opens the store app through universal links (a target=_blank link would do nothing there).
   In a browser they open in a new tab so the Today page stays. The page never loads the store sites itself.
   Links are only for the supermarkets the user has connected (issue #44, none by default): the 「超市」 chips at the bottom
   (GET / PUT …/grocery/stores, own page only) switch them; the server filters links, this page only re-renders.
   With the deployer's opt-in grocery-sync on (docs/grocery-store-data-plan.md §8), items / products carry `offer` (the connected
   store's weekly price; half price first) -> 「半价」 badge and saving; GET …/grocery/specials fills 「本周半价」.
   Prices the user's own AI recorded (record_store_prices, docs/grocery-plan.md §9) come first: item.offer has the store, price,
   special and age_days (shown as 「x 天前」); list.prices has totals per store, savings and what still needs a price; bought lines
   carry purchase.verdict (买得便宜 / 买贵了). 「价格记录」 lists the user's own store products (GET …/store-products) and opens the
   price history of one (GET …/store-products/{id}: curve, min / average / latest, how often on special). No store images: the
   page's CSP only loads its own images, and store images are not copied.
   「家里存货」 (issue #102) lists the pantry (GET …/grocery/pantry: by category, quantity, 「x 天前更新」); on the own page each row has
   「用完了」 (PUT …/pantry with quantity 0, undo in the toast puts the old quantity back). Changing amounts stays with the AI (set_pantry).
   List lines the pantry covered part of carry pantry_label (「家里有 6 个」) to explain why less is bought.
   Usual items (issue #103): list lines of a usual-item product (item.staple; source staple = added because it is due) get 「常买」;
   products carry staple_info.next_due -> 「下次该买 10/28」 on the row and in the sheet. Marking is done by the AI after the user
   confirms (set_staple); the page only shows it.
   Pantry follows logged meals (issue #104): GET …/grocery/pantry-auto -> one status line under 「家里存货」 (switched by the AI,
   set_pantry_auto); pantry rows with a best before close by (expiring) get 「x 天后到期」 / 「今天到期」 / 「已过期」. */
(() => {
  "use strict";
  let C = null;
  let info = null;
  let list = null;      // last GET /grocery/list
  let products = null;  // last GET /grocery/products
  let stores = null;    // last GET /grocery/stores (own page only)
  let specials = null;  // last GET /grocery/specials (this week's half price, grocery-sync)
  let sprods = null;    // last GET /grocery/store-products (this user's own store products and latest prices)
  let pantry = null;    // last GET /grocery/pantry (what is at home)
  let pantryAuto = null; // last GET /grocery/pantry-auto ({enabled, label})
  let q = "";
  const $ = (s, r = document) => r.querySelector(s);
  const esc = (v) => C.esc(v);
  const tr = (k, p) => window.I18N.t(k, p);
  const base = () => `/v1/users/${info.owner}/grocery`;
  const CUR = { AUD: "A$", CNY: "¥", USD: "$", NZD: "NZ$", EUR: "€", GBP: "£", JPY: "¥", MYR: "RM" };
  const money = (v, cur) => (v == null ? "" : `${CUR[cur] || (cur ? `${cur} ` : "")}${Number(v).toFixed(v >= 100 ? 0 : 2)}`);
  const short = (v, cur) => (v == null ? "" : `${CUR[cur] || ""}${Number(v) >= 10 ? Math.round(v) : Number(v).toFixed(1).replace(/\.0$/, "")}`);   // stat cards / badges at phone width
  const num = (v, d = 1) => (v == null ? "—" : Number(v).toLocaleString(window.I18N.locale(), { maximumFractionDigits: d }));
  const md = (iso) => window.I18N.monthDay(new Date(`${iso.slice(0, 10)}T12:00:00`));
  const badge = (cls, t) => `<span class="badge ${cls}">${esc(t)}</span>`;
  const IN_APP = !!window.Watch2Whoop;
  const storeLinks = (links, name) => (links || []).length ? `<span class="glinks">${links.map((l) =>
    `<a class="gstore${l.kind === "product" ? " on" : ""}" href="${esc(l.url)}"${IN_APP ? "" : ` target="_blank"`} rel="noopener noreferrer"
      aria-label="${esc(l.kind === "product" ? tr("{store} 商品页：{name}", { store: l.label, name }) : tr("{store} 搜索：{name}", { store: l.label, name }))}">${esc(l.label)}</a>`).join("")}</span>` : "";
  const CAT_ORDER = ["produce", "meat", "seafood", "dairy_eggs", "grains", "pantry", "frozen", "drinks", "snacks", "supplements", "household", "other"];

  const ageLabel = (d) => (d == null ? "" : d <= 0 ? tr("今天") : tr("{n} 天前", { n: d }));
  const verdictBadge = (v) => (v === "cheap" ? badge("good", tr("买得便宜")) : v === "dear" ? badge("bad", tr("买贵了")) : "");
  const specialBadge = (o) => (o && o.special && !o.half_price ? badge("good", tr("特价")) : "");
  const halfBadge = (o) => (o && o.half_price ? badge("good", o.save_total || o.save ? tr("半价 -{save}", { save: short(o.save_total || o.save, "AUD") }) : tr("半价")) : "");

  function itemRow(i) {
    const val = [i.qty_label, i.est_cost != null ? money(i.paid_price ?? i.est_cost, i.currency) : ""].filter(Boolean).join(" · ");
    const hb = i.bought ? verdictBadge(i.purchase?.verdict) : halfBadge(i.offer) || specialBadge(i.offer);
    const age = !i.bought && i.offer?.age_days > 0   // list rows are tight at phone width: only prices from earlier days show their age
      ? `<small class="gage${i.offer.stale ? " old" : ""}">${esc(ageLabel(i.offer.age_days))}</small>` : "";
    const attrs = info.self ? `data-gi="${esc(i.id)}"` : "disabled";
    const home = !i.bought && i.pantry_label ? badge("plain", i.pantry_label) : "";   // why less is bought (the pantry covered part)
    const usual = i.staple ? badge("plain", tr("常买")) : "";
    return `<div class="grow"><button class="item gitem" ${attrs} aria-pressed="${i.bought}" aria-label="${esc(i.bought ? tr("{name}，已买", { name: i.name }) : i.name)}">
      <span class="gchk" aria-hidden="true"></span><span class="row-title">${esc(i.name)}</span>${hb}${usual}${home}<span class="val">${esc(val)}${age}</span></button>${i.bought ? "" : storeLinks(i.links, i.name)}</div>`;
  }

  function listHtml() {
    if (!list || list.error) return `<div class="empty">${esc(tr("暂无清单"))}</div>`;
    const items = list.items || [];
    const head = `<div class="section-head"><h2>${esc(tr("本周采购"))}</h2><span class="small muted">${md(list.week_start)}–${md(list.week_end)}</span></div>`;
    if (!items.length) return `${head}<div class="card"><div class="empty">${esc(tr("暂无清单"))}</div></div>`;
    const totals = Object.entries(list.est_total || {}).map(([c, v]) => short(v, c)).join(" + ") || "—";
    const stats = `<div class="card gstats"><div><div class="l">${esc(tr("已买"))}</div><div class="v">${list.progress.bought}<small> / ${list.progress.total}</small></div></div>
      <div><div class="l">${esc(tr("估价"))}</div><div class="v">${esc(totals)}</div></div>
      <div><div class="l">${esc(tr("未估价"))}</div><div class="v">${list.unpriced}<small> ${esc(tr("项"))}</small></div></div>${halfStat()}</div>`;
    const pr = list.prices;
    const storeTotals = pr && (Object.keys(pr.by_store || {}).length || pr.missing?.length || pr.stale?.length)
      ? `<div class="gwarn">${Object.values(pr.by_store || {}).map((s) => badge("plain", `${s.label} ${short(s.total, "AUD")} · ${s.items}`)).join("")}
        ${Object.entries(pr.savings || {}).map(([c, v]) => badge("good", tr("特价省 {v}", { v: short(v, c) }))).join("")}
        ${pr.missing?.length ? badge("mid", tr("缺价 {n}", { n: pr.missing.length })) : ""}${pr.stale?.length ? badge("plain", tr("旧价 {n}", { n: pr.stale.length })) : ""}</div>` : "";
    const warns = (list.warnings || []).length
      ? `<div class="gwarn">${list.warnings.map((w) => badge(w.code === "product" ? "mid" : "bad", w.label)).join("")}</div>` : "";
    const by = new Map();
    for (const i of items) { if (!by.has(i.category)) by.set(i.category, []); by.get(i.category).push(i); }
    const cats = [...by.keys()].sort((a, b) => CAT_ORDER.indexOf(a) - CAT_ORDER.indexOf(b));
    const groups = cats.map((c) => {
      const xs = by.get(c);
      return `<div class="ggroup"><div class="ghead"><span>${esc(xs[0].category_label)}</span><span class="small muted">${xs.filter((x) => x.bought).length}/${xs.length}</span></div>
        <div class="card list">${xs.map(itemRow).join("")}</div></div>`;
    }).join("");
    return head + stats + storeTotals + warns + groups;
  }

  function halfStat() {
    const h = list.half_price;
    if (!h || !h.items) return "";
    const save = Object.entries(h.save || {}).map(([c, v]) => short(v, c)).join(" + ") || "—";
    return `<div><div class="l">${esc(tr("半价省 · {n}", { n: h.items }))}</div><div class="v good">${esc(save)}</div></div>`;
  }

  function specialsHtml() {
    const xs = specials && !specials.error ? specials.items || [] : [];
    if (!xs.length) return "";
    const row = (x) => `<div class="grow"><a class="item gsp" href="${esc(x.url)}"${IN_APP ? "" : ` target="_blank"`} rel="noopener noreferrer">
      <span class="row-title">${esc(x.name.startsWith(`${x.label} `) ? x.name.slice(x.label.length + 1) : x.name)}</span>${(x.flags || []).length ? badge("mid", x.flags[0]) : ""}
      <span class="val"><span class="gstore on">${esc(x.label)}</span> ${esc(money(x.price, "AUD"))}${x.was_price ? ` <s>${esc(money(x.was_price, "AUD"))}</s>` : ""}</span></a></div>`;
    return `<div class="section-head"><h2>${esc(tr("本周半价"))}</h2><span class="small muted">${md(specials.week_start)}–${md(specials.valid_to)}</span></div>
      <div class="card list">${xs.map(row).join("")}</div>`;
  }

  function sprodRow(p) {
    const o = p.offer;
    const val = o ? `${money(o.price, "AUD")}` : "—";
    return `<div class="grow"><button class="item" data-gsp="${esc(p.id)}"><span class="row-title">${esc(p.name)}${p.size ? `<span class="small muted"> ${esc(p.size)}</span>` : ""}</span>
      ${o ? halfBadge(o) || specialBadge(o) : ""}<span class="val">${esc(val)}${o ? `<small class="gage${o.stale ? " old" : ""}">${esc(ageLabel(o.age_days))}</small>` : ""}</span></button>
      ${p.url ? storeLinks([{ store: p.store, label: p.store_label, kind: "product", url: p.url }], p.name) : `<span class="glinks"><span class="gstore">${esc(p.store_label)}</span></span>`}</div>`;
  }

  function sprodsHtml() {
    const xs = Array.isArray(sprods) ? sprods : [];
    if (!xs.length) return "";
    return `<div class="section-head"><h2>${esc(tr("价格记录"))}</h2><span class="small muted">${xs.length}</span></div>
      <div class="card list">${xs.map(sprodRow).join("")}</div>`;
  }

  function spark(points) {
    if (points.length < 2) return "";
    const W = 300, H = 64, pad = 6;
    const ps = points.map((p) => p.price);
    const lo = Math.min(...ps), hi = Math.max(...ps), span = hi - lo || 1;
    const x = (i) => pad + (i * (W - 2 * pad)) / (points.length - 1);
    const y = (v) => H - pad - ((v - lo) * (H - 2 * pad)) / span;
    const line = points.map((p, i) => `${x(i).toFixed(1)},${y(p.price).toFixed(1)}`).join(" ");
    const dots = points.map((p, i) => (p.special || p.kind === "paid")
      ? `<circle cx="${x(i).toFixed(1)}" cy="${y(p.price).toFixed(1)}" r="3" class="${p.special ? "gsp-dot" : "gpaid-dot"}"/>` : "").join("");
    return `<svg class="gspark" viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(tr("价格走势"))}"><polyline points="${line}" fill="none"/>${dots}</svg>`;
  }

  async function sprodSheet(id) {
    let d;
    try {
      d = await C.api(`${base()}/store-products/${encodeURIComponent(id)}`);
    } catch (e) {
      C.toast(e.message || tr("没有找到"));
      return;
    }
    const kv = (k, v) => `<div class="kv"><span class="k">${esc(k)}</span><span class="v">${v == null || v === "" ? "—" : esc(v)}</span></div>`;
    const lb = d.last_purchase;
    const sp = d.specials || {};
    C.openSheet(d.name, `${spark(d.points || [])}
      ${kv(tr("超市"), d.store_label)}${kv(tr("规格"), d.size)}
      ${kv(tr("最近"), d.latest != null ? `${money(d.latest, "AUD")} · ${ageLabel(d.latest_age_days)}` : null)}
      ${kv(tr("最低"), d.min != null ? money(d.min, "AUD") : null)}${kv(tr("平均"), d.avg != null ? money(d.avg, "AUD") : null)}
      ${kv(tr("打折"), sp.count ? (sp.every_days ? tr("{n} 次 · 约每 {d} 天", { n: sp.count, d: sp.every_days }) : tr("{n} 次", { n: sp.count })) : tr("没见过"))}
      ${lb ? kv(tr("上次买"), `${md(lb.date)} · ${lb.unit_paid != null ? money(lb.unit_paid, "AUD") : "—"}${lb.on_special ? ` · ${tr("特价")}` : ""}`) : ""}
      ${lb?.verdict ? `<div class="gwarn" style="margin-top:8px">${verdictBadge(lb.verdict) || badge("plain", tr("正常"))}</div>` : ""}
      ${d.url ? `<div class="kv"><span class="k">${esc(tr("链接"))}</span>${storeLinks([{ store: d.store, label: d.store_label, kind: "product", url: d.url }], d.name)}</div>` : ""}`);
  }

  const nextBuy = (s) => tr("下次该买 {d}", { d: md(s.next_due) });

  function productRow(p) {
    const val = [p.size_label, p.price != null ? money(p.price, p.currency) : ""].filter(Boolean).join(" · ");
    const s = p.staple && p.staple_info;
    const usual = s ? badge(s.due ? "mid" : "plain", nextBuy(s)) : "";   // a badge does not shrink: the date stays readable at phone width
    return `<div class="grow"><button class="item" data-gp="${esc(p.id)}"><span class="row-title">${esc(p.name)}${p.brand ? `<span class="small muted"> ${esc(p.brand)}</span>` : ""}</span>
      ${usual}${halfBadge(p.offer)}${p.flags?.length ? badge("mid", p.flags[0]) : ""}<span class="val">${esc(val)}</span></button>${storeLinks(p.links, p.name)}</div>`;
  }

  function productsHtml() {
    if (!products || products.error) return `<div class="empty">${esc(tr("暂无商品"))}</div>`;
    const qq = q.trim().toLowerCase();
    const shown = products.filter((p) => !qq || [p.name, p.brand, p.store, p.category_label, ...(p.matches || [])].some((t) => String(t || "").toLowerCase().includes(qq)));
    if (!shown.length) return `<div class="empty">${esc(qq ? tr("没有找到") : tr("暂无商品"))}</div>`;
    return `<div class="card list">${shown.map(productRow).join("")}</div>`;
  }

  function productSheet(id) {
    const p = (products || []).find((x) => String(x.id) === String(id));
    if (!p) return;
    const kv = (k, v) => `<div class="kv"><span class="k">${esc(k)}</span><span class="v">${v == null || v === "" ? "—" : esc(v)}</span></div>`;
    const n = p.nutrition || {};
    const per = p.package_unit === "ml" ? "100 ml" : "100 g";
    const unitP = p.unit_price != null ? `${money(p.unit_price, p.currency)} / ${p.package_unit === "pcs" ? tr("个") : p.package_unit === "ml" ? "L" : "kg"}` : null;
    const flags = (p.flags || []).map((f) => badge("mid", f)).join("");
    const s = p.staple && p.staple_info;
    const amount = s ? (s.unit === "pack" ? tr("{n} 包", { n: s.qty }) : `${s.qty} ${s.unit === "pcs" ? tr("个") : s.unit}`) : "";
    const usual = s ? `${kv(tr("常买"), tr("每 {n} 天 · {amount}", { n: s.every_days, amount }))}${kv(tr("下次该买"), md(s.next_due))}` : "";
    C.openSheet(p.name, `${flags ? `<div class="gwarn">${flags}</div>` : ""}${usual}
      ${kv(tr("品牌"), p.brand)}${kv(tr("门店"), p.store_label)}${(p.links || []).length ? `<div class="kv"><span class="k">${tr("链接")}</span>${storeLinks(p.links, p.name)}</div>` : ""}${kv(tr("类别"), p.category_label)}${kv(tr("规格"), p.size_label)}
      ${kv(tr("价格"), p.price != null ? money(p.price, p.currency) : null)}${kv(tr("单价"), unitP)}${kv(tr("价格日期"), p.price_on ? md(p.price_on) : null)}
      ${p.offer ? kv(tr("本周价"), `${p.offer.label} ${money(p.offer.price, "AUD")}${p.offer.was_price ? ` / ${money(p.offer.was_price, "AUD")}` : ""}${p.offer.half_price ? ` · ${tr("半价")}` : ""}`) : ""}
      <div class="ghead" style="margin-top:16px"><span>${esc(tr("每 {per}", { per }))}</span></div>
      ${kv(tr("热量"), n.kcal != null ? `${num(n.kcal, 0)} kcal` : null)}${kv(tr("蛋白质"), n.protein_g != null ? `${num(n.protein_g)} g` : null)}
      ${kv(tr("碳水"), n.carbs_g != null ? `${num(n.carbs_g)} g` : null)}${kv(tr("脂肪"), n.fat_g != null ? `${num(n.fat_g)} g` : null)}
      ${kv(tr("饱和脂肪"), n.sat_fat_g != null ? `${num(n.sat_fat_g)} g` : null)}${kv(tr("糖"), n.sugar_g != null ? `${num(n.sugar_g)} g` : null)}
      ${kv(tr("钠"), n.sodium_mg != null ? `${num(n.sodium_mg, 0)} mg` : null)}${p.nutrition_source ? kv(tr("来源"), p.nutrition_source) : ""}${p.barcode ? kv(tr("条码"), p.barcode) : ""}`);
  }

  function pantryRow(p) {
    const age = p.days_ago == null ? "" : p.days_ago <= 0 ? tr("今天更新") : tr("{n} 天前更新", { n: p.days_ago });
    const left = p.days_left;
    const exp = p.expiring && left != null
      ? badge(left <= 0 ? "bad" : "mid", left < 0 ? tr("已过期") : left === 0 ? tr("今天到期") : tr("{n} 天后到期", { n: left })) : "";
    const btn = info.self ? `<span class="glinks"><button class="gstore" data-gpu="${esc(p.id)}" aria-label="${esc(tr("{name} 用完了", { name: p.name }))}">${esc(tr("用完了"))}</button></span>` : "";
    return `<div class="grow"><div class="item"><span class="row-title">${esc(p.name)}</span>${exp}
      <span class="val">${esc(p.label)}${exp ? "" : `<small class="gage">${esc(age)}</small>`}</span></div>${btn}</div>`;   // phone width: the expiry badge wins over 「x 天前更新」
  }

  function pantryHtml() {
    const xs = Array.isArray(pantry) ? pantry : [];
    const auto = pantryAuto && !pantryAuto.error
      ? `<div class="small muted gauto">${esc(pantryAuto.enabled ? tr("记餐自动扣存货：开") : tr("记餐自动扣存货：关"))}</div>` : "";
    const head = `<div class="section-head"><h2>${esc(tr("家里存货"))}</h2><span class="small muted">${xs.length || ""}</span></div>${auto}`;
    if (!xs.length) return `${head}<div class="card"><div class="empty">${esc(tr("暂无存货"))}</div></div>`;
    const by = new Map();
    for (const p of xs) { if (!by.has(p.category)) by.set(p.category, []); by.get(p.category).push(p); }   // the server sorts by category, then name
    return head + [...by.values()].map((ps) => `<div class="ggroup"><div class="ghead"><span>${esc(ps[0].category_label)}</span></div>
      <div class="card list">${ps.map(pantryRow).join("")}</div></div>`).join("");
  }

  function paintPantry() {
    const el = $("#g-pantry");
    if (!el) return;
    el.innerHTML = pantryHtml();
    bind(el);
  }

  async function putPantry(item) {
    pantry = await C.api("/v1/users/me/grocery/pantry", { method: "PUT", body: { items: [item] } });
    paintPantry();
  }

  async function usedUp(id) {
    const p = (pantry || []).find((x) => String(x.id) === String(id));
    if (!p) return;
    try {
      await putPantry({ name: p.name, quantity: 0, unit: p.unit });
    } catch (e) {
      C.toast(e.message || tr("没有保存"));
      return;
    }
    C.toast(tr("已用完 · {name}", { name: p.name }), tr("撤销"), async () => {
      try {
        await putPantry({ name: p.name, quantity: p.quantity, unit: p.unit, product_id: p.product_id, best_before: p.best_before });
        C.toast(tr("已撤销 · {name}", { name: p.name }));
      } catch (e) {
        C.toast(e.message || tr("没有保存"));
      }
    });
  }

  function storesHtml() {
    if (!stores || stores.error) return "";
    return `<div class="section-head"><h2>${esc(tr("超市"))}</h2></div><div class="gconn">${stores.stores.map((s) =>
      `<button class="gconn-b" data-gs="${esc(s.key)}" aria-pressed="${s.connected}">${esc(s.label)}</button>`).join("")}</div>`;
  }

  async function toggleStore(key) {
    const next = stores.stores.filter((s) => (s.key === key ? !s.connected : s.connected)).map((s) => s.key);
    try {
      stores = await C.api("/v1/users/me/grocery/stores", { method: "PUT", body: { connected: next } });
      [list, products, specials, sprods] = await Promise.all([C.api(`${base()}/list${info.date ? `?week=${encodeURIComponent(info.date)}` : ""}`),
        C.api(`${base()}/products`), C.api(`${base()}/specials?limit=8`).catch((e) => ({ error: e })),
        C.api(`${base()}/store-products`).catch(() => null)]);
    } catch (e) {
      C.toast(e.message || tr("没有保存"));
      return;
    }
    const box = $("#grocery");
    if (!box) return;
    $("#g-list", box).innerHTML = listHtml();
    $("#g-products", box).innerHTML = productsHtml();
    $("#g-stores", box).innerHTML = storesHtml();
    $("#g-specials", box).innerHTML = specialsHtml();
    $("#g-sprods", box).innerHTML = sprodsHtml();
    bind(box);
  }

  function paint(view) {
    const box = $("#grocery", view || document);
    if (!box) return;
    $("#g-list", box).innerHTML = listHtml();
    bind(box);
  }

  async function tick(id) {
    const item = (list?.items || []).find((x) => String(x.id) === String(id));
    if (!item) return;
    item.bought = !item.bought;   // optimistic
    list.progress.bought += item.bought ? 1 : -1;
    paint();
    try {
      list = await C.api(`/v1/users/me/grocery/list/items/${encodeURIComponent(id)}`, { method: "PATCH", body: { bought: item.bought } });
      paint();
      pantry = await C.api(`${base()}/pantry`).catch(() => pantry);   // ticking adds to the pantry, unticking takes it back
      paintPantry();
    } catch (e) {
      item.bought = !item.bought;
      list.progress.bought += item.bought ? 1 : -1;
      paint();
      C.toast(e.message || tr("没有保存"));
    }
  }

  function bind(root) {
    for (const b of root.querySelectorAll("[data-gi]")) b.onclick = () => tick(b.dataset.gi);
    for (const b of root.querySelectorAll("[data-gp]")) b.onclick = () => productSheet(b.dataset.gp);
    for (const b of root.querySelectorAll("[data-gs]")) b.onclick = () => toggleStore(b.dataset.gs);
    for (const b of root.querySelectorAll("[data-gsp]")) b.onclick = () => sprodSheet(b.dataset.gsp);
    for (const b of root.querySelectorAll("[data-gpu]")) b.onclick = () => usedUp(b.dataset.gpu);
  }

  async function foodShop(i) {
    info = i;
    const wk = i.date ? `?week=${encodeURIComponent(i.date)}` : "";
    [list, products, stores, specials, sprods, pantry, pantryAuto] = await Promise.all([
      C.api(`${base()}/list${wk}`).catch((e) => ({ error: e })),
      C.api(`${base()}/products`).catch((e) => ({ error: e })),
      i.self ? C.api(`${base()}/stores`).catch((e) => ({ error: e })) : null,
      C.api(`${base()}/specials?limit=8`).catch((e) => ({ error: e })),
      C.api(`${base()}/store-products`).catch(() => null),
      C.api(`${base()}/pantry`).catch(() => null),
      C.api(`${base()}/pantry-auto`).catch((e) => ({ error: e })),
    ]);
    return `<div id="grocery"><div class="section" id="g-list">${listHtml()}</div>
      <div class="section" id="g-pantry">${pantryHtml()}</div>
      <div class="section" id="g-specials">${specialsHtml()}</div>
      <div class="section" id="g-sprods">${sprodsHtml()}</div>
      <div class="section"><div class="section-head"><h2>${esc(tr("商品库"))}</h2><span class="small muted">${Array.isArray(products) ? products.length : ""}</span></div>
        <input class="search" id="g-q" type="search" placeholder="${esc(tr("搜索"))}" value="${esc(q)}" aria-label="${esc(tr("搜索商品"))}"><div id="g-products">${productsHtml()}</div></div>
      <div class="section" id="g-stores">${storesHtml()}</div></div>`;
  }

  function bindFoodShop(view) {
    const box = $("#grocery", view);
    if (!box) return;
    bind(box);
    const input = $("#g-q", box);
    if (input) input.oninput = () => { q = input.value; $("#g-products", box).innerHTML = productsHtml(); bind($("#g-products", box)); };
  }

  (window.BodyPlugins = window.BodyPlugins || []).push({
    init: (ctx) => { C = ctx; }, foodShop, bindFoodShop,
  });
})();
