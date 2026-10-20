/* Grocery (饮食 → 采购, docs/grocery-plan.md): this week's shopping list (tap to tick) and the product library.
   Separate from app.js: registered in window.BodyPlugins with init(ctx), foodShop(info) -> HTML, bindFoodShop(view).
   ctx = { api, openSheet, closeSheet, toast, esc }; info = { owner: "me" | id, self, date }.
   Endpoints (scope nutrition): GET /v1/users/{u}/grocery/list?week=, PATCH …/list/items/{id} {bought}, GET …/products.
   Input comes from the user's AI (save_product, generate_shopping_list); this page only shows numbers and short labels. */
(() => {
  "use strict";
  let C = null;
  let info = null;
  let list = null;      // last GET /grocery/list
  let products = null;  // last GET /grocery/products
  let q = "";
  const $ = (s, r = document) => r.querySelector(s);
  const esc = (v) => C.esc(v);
  const tr = (k, p) => window.I18N.t(k, p);
  const base = () => `/v1/users/${info.owner}/grocery`;
  const CUR = { AUD: "A$", CNY: "¥", USD: "$", NZD: "NZ$", EUR: "€", GBP: "£", JPY: "¥", MYR: "RM" };
  const money = (v, cur) => (v == null ? "" : `${CUR[cur] || (cur ? `${cur} ` : "")}${Number(v).toFixed(v >= 100 ? 0 : 2)}`);
  const num = (v, d = 1) => (v == null ? "—" : Number(v).toLocaleString(window.I18N.locale(), { maximumFractionDigits: d }));
  const md = (iso) => window.I18N.monthDay(new Date(`${iso.slice(0, 10)}T12:00:00`));
  const badge = (cls, t) => `<span class="badge ${cls}">${esc(t)}</span>`;
  const CAT_ORDER = ["produce", "meat", "seafood", "dairy_eggs", "grains", "pantry", "frozen", "drinks", "snacks", "supplements", "household", "other"];

  function itemRow(i) {
    const val = [i.qty_label, i.est_cost != null ? money(i.paid_price ?? i.est_cost, i.currency) : ""].filter(Boolean).join(" · ");
    const attrs = info.self ? `data-gi="${esc(i.id)}"` : "disabled";
    return `<button class="item gitem" ${attrs} aria-pressed="${i.bought}" aria-label="${esc(i.bought ? tr("{name}，已买", { name: i.name }) : i.name)}">
      <span class="gchk" aria-hidden="true"></span><span class="row-title">${esc(i.name)}</span><span class="val">${esc(val)}</span></button>`;
  }

  function listHtml() {
    if (!list || list.error) return `<div class="empty">${esc(tr("暂无清单"))}</div>`;
    const items = list.items || [];
    const head = `<div class="section-head"><h2>${esc(tr("本周采购"))}</h2><span class="small muted">${md(list.week_start)}–${md(list.week_end)}</span></div>`;
    if (!items.length) return `${head}<div class="card"><div class="empty">${esc(tr("暂无清单"))}</div></div>`;
    const totals = Object.entries(list.est_total || {}).map(([c, v]) => money(v, c)).join(" + ") || "—";
    const stats = `<div class="card gstats"><div><div class="l">${esc(tr("已买"))}</div><div class="v">${list.progress.bought}<small> / ${list.progress.total}</small></div></div>
      <div><div class="l">${esc(tr("估价"))}</div><div class="v">${esc(totals)}</div></div>
      <div><div class="l">${esc(tr("未估价"))}</div><div class="v">${list.unpriced}<small> ${esc(tr("项"))}</small></div></div></div>`;
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
    return head + stats + warns + groups;
  }

  function productRow(p) {
    const val = [p.size_label, p.price != null ? money(p.price, p.currency) : ""].filter(Boolean).join(" · ");
    return `<button class="item" data-gp="${esc(p.id)}"><span class="row-title">${esc(p.name)}${p.brand ? `<span class="small muted"> ${esc(p.brand)}</span>` : ""}</span>
      ${p.flags?.length ? badge("mid", p.flags[0]) : ""}<span class="val">${esc(val)}</span></button>`;
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
    C.openSheet(p.name, `${flags ? `<div class="gwarn">${flags}</div>` : ""}
      ${kv(tr("品牌"), p.brand)}${kv(tr("门店"), p.store)}${kv(tr("类别"), p.category_label)}${kv(tr("规格"), p.size_label)}
      ${kv(tr("价格"), p.price != null ? money(p.price, p.currency) : null)}${kv(tr("单价"), unitP)}${kv(tr("价格日期"), p.price_on ? md(p.price_on) : null)}
      <div class="ghead" style="margin-top:16px"><span>${esc(tr("每 {per}", { per }))}</span></div>
      ${kv(tr("热量"), n.kcal != null ? `${num(n.kcal, 0)} kcal` : null)}${kv(tr("蛋白质"), n.protein_g != null ? `${num(n.protein_g)} g` : null)}
      ${kv(tr("碳水"), n.carbs_g != null ? `${num(n.carbs_g)} g` : null)}${kv(tr("脂肪"), n.fat_g != null ? `${num(n.fat_g)} g` : null)}
      ${kv(tr("饱和脂肪"), n.sat_fat_g != null ? `${num(n.sat_fat_g)} g` : null)}${kv(tr("糖"), n.sugar_g != null ? `${num(n.sugar_g)} g` : null)}
      ${kv(tr("钠"), n.sodium_mg != null ? `${num(n.sodium_mg, 0)} mg` : null)}${p.barcode ? kv(tr("条码"), p.barcode) : ""}`);
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
  }

  async function foodShop(i) {
    info = i;
    const wk = i.date ? `?week=${encodeURIComponent(i.date)}` : "";
    [list, products] = await Promise.all([
      C.api(`${base()}/list${wk}`).catch((e) => ({ error: e })),
      C.api(`${base()}/products`).catch((e) => ({ error: e })),
    ]);
    return `<div id="grocery"><div class="section" id="g-list">${listHtml()}</div>
      <div class="section"><div class="section-head"><h2>${esc(tr("商品库"))}</h2><span class="small muted">${Array.isArray(products) ? products.length : ""}</span></div>
        <input class="search" id="g-q" type="search" placeholder="${esc(tr("搜索"))}" value="${esc(q)}" aria-label="${esc(tr("搜索商品"))}"><div id="g-products">${productsHtml()}</div></div></div>`;
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
