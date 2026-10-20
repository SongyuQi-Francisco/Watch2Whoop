/* 「我的饮食档案」 (issue #32, assistant/diet_profile.py): one collapsed line at the top of 饮食 (recipes and shopping);
   tapping it opens a sheet with collapsible blocks (targets, why these numbers, constraints, a day of eating, when to adjust,
   version log — the log starts collapsed). Open / closed state per block is remembered in localStorage (this browser only).
   Separate from app.js: registered in window.BodyPlugins with init(ctx), foodTop(info) -> HTML, bindFoodTop(view).
   Endpoints (scope memory; goals need nutrition, energy metrics): GET /v1/users/{u}/diet-profile[?brief=true].
   Content is written by the user's AI (update_diet_profile); this page only reads. */
(() => {
  "use strict";
  let C = null;
  let info = null;
  const $ = (s, r = document) => r.querySelector(s);
  const esc = (v) => C.esc(v);
  const path = (brief) => `/v1/users/${info.owner}/diet-profile${brief ? "?brief=true" : ""}`;
  const num = (v, d = 0) => (v == null ? "—" : Number(v).toLocaleString("zh-CN", { maximumFractionDigits: d }));
  const md = (iso) => (iso ? `${iso.slice(5, 7)}-${iso.slice(8, 10)}` : "");
  const CHEV = `<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M6 3.5L10.5 8 6 12.5" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>`;

  // ---------- remembered open / closed blocks (per browser; storage may be unavailable)
  const STORE_KEY = "w2w.dietProfile.open";
  const DEFAULT_OPEN = { goals: true, why: true, constraints: true, day_plan: true, adjust_rules: true, versions: false };
  function openState() {
    try { return { ...DEFAULT_OPEN, ...(JSON.parse(localStorage.getItem(STORE_KEY) || "{}") || {}) }; } catch (e) { return { ...DEFAULT_OPEN }; }
  }
  function saveOpen(key, open) {
    try { localStorage.setItem(STORE_KEY, JSON.stringify({ ...openState(), [key]: open })); } catch (e) { /* private mode */ }
  }

  // ---------- one-line entry
  function foodTop(i) {
    info = i;
    return `<div class="dp-entry-wrap" id="dp-entry" hidden></div>`;
  }

  async function bindFoodTop(view) {
    const box = $("#dp-entry", view);
    if (!box) return;
    const owner = info.owner;
    let s;
    try { s = await C.api(path(true)); } catch (e) { return; }   // no memory share: no entry
    if (owner !== info.owner || !box.isConnected) return;
    const any = s.version || s.sections || s.goal_effective_from;
    if (!any && !info.self) return;
    // Version label: the diet profile's own version; without one, the goal version in effect (goals/nutrition.py)
    const label = s.version ? esc(s.version) : s.goal_effective_from ? `目标 ${md(s.goal_effective_from)} 起` : "";
    const meta = any ? `${label}${s.updated_on ? `（${md(s.updated_on)} 更新）` : ""}` : "未建立";
    box.innerHTML = `<button class="card item dp-entry" aria-haspopup="dialog"><span class="row-title">我的饮食档案</span>
      <span class="val small muted">${meta}</span><span class="chev">${CHEV}</span></button>`;
    box.hidden = false;
    $(".dp-entry", box).onclick = () => openProfile();
  }

  // ---------- sheet
  function block(key, title, body, extra = "") {
    if (!body) return "";
    return `<details class="dp-block" data-dp="${key}" ${openState()[key] ? "open" : ""}><summary><span>${title}</span>${extra ? `<span class="small muted">${extra}</span>` : ""}</summary>
      <div class="dp-body">${body}</div></details>`;
  }
  const row = (l, v, sub = "") => `<div class="dp-row"><div class="dp-l">${l}${sub ? `<div class="small muted">${sub}</div>` : ""}</div><div class="dp-v">${v}</div></div>`;
  const stat = (l, v, u = "") => `<div><div class="l">${l}</div><div class="v">${v}${u ? `<small> ${u}</small>` : ""}</div></div>`;
  const textBody = (sec) => `<div class="dp-text">${esc(sec.text)}</div>`;

  function goalsBody(p, targets) {
    const g = p.goals;
    let html = "";
    if (g && g.sets?.length) {
      html += g.sets.map((s) => row(esc(s.name || "目标"), `${num(s.calories)} kcal`,
        [s.protein_g != null && `蛋白 ${num(s.protein_g)}`, s.carbs_g != null && `碳水 ${num(s.carbs_g)}`, s.fat_g != null && `脂肪 ${num(s.fat_g)} g`,
          s.sat_fat_max_g != null && `饱和脂肪 ≤ ${num(s.sat_fat_max_g)} g`, s.added_sugar_max_g != null && `添加糖 ≤ ${num(s.added_sugar_max_g)} g`,
          s.fiber_min_g != null && `纤维 ≥ ${num(s.fiber_min_g)} g`, s.days && `${s.days} 天 / 周`].filter(Boolean).join(" · "))).join("");
      if (g.effective_from) html += row("生效", `${md(g.effective_from)} 起`, esc(g.reason || ""));
    } else if (g) html += row("目标", "—");
    const t = targets?.data;
    const water = t?.water_ml ?? g?.sets?.find((s) => s.water_ml)?.water_ml;
    if (water != null) html += row("喝水", `${num(water)} ml`);
    if (t?.steps != null) html += row("步数", num(t.steps));
    if (t?.note) html += `<div class="dp-note small muted">${esc(t.note)}</div>`;
    else if (targets && !t) html += textBody(targets);
    return html;
  }

  function whyBody(p, rat) {
    let html = "";
    const e = p.energy, plan = p.plan;
    if (e) {
      html += `<div class="dp-stats">${stat("基础代谢", num(e.basal_kcal), "kcal")}${stat("活动", num(e.active_kcal), "kcal")}${stat("总消耗", num(e.total_kcal), "kcal")}</div>
        <div class="small muted dp-cap">近 ${e.days} 天平均 · ${md(e.start)}–${md(e.end)}</div>`;
    }
    if (plan) {
      const deficit = plan.deficit_kcal >= 0 ? `−${num(plan.deficit_kcal)}` : `+${num(-plan.deficit_kcal)}`;
      const loss = plan.loss_kg_per_week >= 0 ? `−${num(plan.loss_kg_per_week, 2)}` : `+${num(-plan.loss_kg_per_week, 2)}`;
      html += `<div class="dp-stats">${stat("目标", num(plan.target_kcal), "kcal")}${stat("每天", deficit, "kcal")}${stat("预计每周", loss, "kg")}</div>`;
      if (plan.fast) html += `<div class="dp-flag"><span class="badge bad">减得偏快</span></div>`;
    }
    const d = rat?.data;
    const w = p.weight?.kg;
    if (w != null || d?.goal_weight_kg != null) html += row("体重", `${num(w, 1)} → ${num(d?.goal_weight_kg, 1)} kg`);
    for (const c of d?.checkpoints || []) html += row(`检查点 ${md(c.date)}`, c.weight_kg != null ? `${num(c.weight_kg, 1)} kg` : "", esc(c.note || ""));
    if (d?.note) html += `<div class="dp-note small muted">${esc(d.note)}</div>`;
    else if (rat && !d) html += textBody(rat);
    return html;
  }

  function listBody(sec, a, b, sep = "") {
    if (!sec) return "";
    if (!sec.data) return textBody(sec);
    return sec.data.items.map((x) => `<div class="dp-row dp-stack"><div class="dp-l">${sep}${esc(x[a])}</div><div class="small muted">${esc(x[b])}</div></div>`).join("");
  }

  function dayBody(sec) {
    if (!sec) return "";
    if (!sec.data) return textBody(sec);
    const d = sec.data;
    let html = (d.meals || []).map((m) => row(esc(m.meal), m.kcal != null ? `${num(m.kcal)} kcal` : "",
      esc(m.foods) + (m.protein_g != null ? ` · 蛋白 ${num(m.protein_g)} g` : ""))).join("");
    if (d.staples?.length) html += `<div class="dp-chips">${d.staples.map((s) => `<span class="badge plain">${esc(s)}</span>`).join("")}</div>`;
    return html;
  }

  // Diet profile versions + goal versions (goals/nutrition.py), newest first
  function logItems(p) {
    const items = (p.versions || []).map((v) => ({ at: String(v.changed_on), label: v.version, changes: v.changes, reason: v.reason }));
    for (const g of p.goal_history || []) {
      const kcal = g.training_days && g.training_kcal !== g.rest_kcal ? `训练日 ${num(g.training_kcal)} / 休息日 ${num(g.rest_kcal)} kcal` : `${num(g.rest_kcal)} kcal`;
      items.push({ at: String(g.effective_from), label: "目标", changes: kcal, reason: g.reason });
    }
    return items.sort((a, b) => (a.at < b.at ? 1 : a.at > b.at ? -1 : 0));
  }
  function versionsBody(items) {
    return items.map((v) => `<div class="dp-row dp-stack dp-ver"><div class="dp-l"><b>${esc(v.label)}</b><span class="small muted"> ${md(v.at)}</span></div>
      <div>${esc(v.changes)}</div>${v.reason ? `<div class="small muted">${esc(v.reason)}</div>` : ""}</div>`).join("");
  }

  function sheetHtml(p) {
    const sec = Object.fromEntries((p.sections || []).map((s) => [s.section, s]));
    const log = logItems(p);
    const html = [
      p.hidden?.includes("goals") ? "" : block("goals", "当前目标", goalsBody(p, sec.targets)),
      block("why", "为什么是这个数", whyBody(p, sec.rationale)),
      block("constraints", "健康约束", listBody(sec.constraints, "rule", "basis")),
      block("day_plan", "一天怎么吃", dayBody(sec.day_plan)),
      block("adjust_rules", "什么时候调整", listBody(sec.adjust_rules, "when", "action")),
      log.length ? block("versions", "版本记录", versionsBody(log), `${log.length}`) : "",
    ].join("");
    return html || `<div class="empty">暂无</div>`;
  }

  async function openProfile() {
    let p;
    try { p = await C.api(path(false)); } catch (e) { C.toast?.("没读到饮食档案"); return; }
    const title = `我的饮食档案${p.version ? ` · ${p.version}` : ""}`;
    C.openSheet(title, `<div class="dp">${sheetHtml(p)}</div>`, (body) => {
      for (const d of body.querySelectorAll("details[data-dp]")) d.addEventListener("toggle", () => saveOpen(d.dataset.dp, d.open));
    });
  }

  (window.BodyPlugins = window.BodyPlugins || []).push({
    init: (ctx) => { C = ctx; }, foodTop, bindFoodTop,
  });
})();
