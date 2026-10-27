/* 「记体重」 (body_engine/weightlog/): the Me page row opens a sheet to log a weigh-in by hand: the weight (kg), a date (default today)
   and, optionally, what the measurement was like (chips, several allowed) and one line of note; the latest weigh-ins with their
   chips sit below. POST /v1/users/me/weight {date, weight_kg, context_tags, note, measured_at, overwrite?} (offline/api.py; the same
   409 "already has another value" -> 「用这次的覆盖」 as 记录 → 日记), GET /v1/users/me/weight/recent. Own account only. Separate from
   app.js: registered in window.BodyPlugins (init, meItem, bindMe); a write waits on the device while offline when offline_mode is on.
   The chip keys are weightlog/params.CONTEXT_TAGS (tests/test_weightlog.py checks the two agree). */
(() => {
  "use strict";
  let C = null;
  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => [...r.querySelectorAll(s)];
  const tr = (k, p) => window.I18N.t(k, p);
  const esc = (v) => C.esc(v);
  const MIN_KG = 30, MAX_KG = 250;   // = the server's CHECKIN_RANGES["weight_kg"]
  // Lucide "weight" (https://lucide.dev, ISC; notices: THIRD_PARTY_NOTICES.md)
  const ICON = `<svg class="wt-ico" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="5" r="3"/><path d="M6.5 8a2 2 0 0 0-1.905 1.46L2.1 18.5A2 2 0 0 0 4 21h16a2 2 0 0 0 1.925-2.54L19.4 9.5A2 2 0 0 0 17.5 8z"/></svg>`;
  // [key, label]: the keys are the server's; order = chip order
  const TAGS = [
    ["fasting", () => tr("早上空腹")], ["after_toilet", () => tr("如厕后")], ["after_meal", () => tr("饭后")],
    ["after_workout", () => tr("运动后")], ["before_bed", () => tr("睡前")], ["clothed", () => tr("穿衣")], ["unclothed", () => tr("未穿衣")],
  ];
  const EXCLUSIVE = [["clothed", "unclothed"]];
  const tagLabel = (k) => (TAGS.find((t) => t[0] === k) || [k, () => k])[1]();
  const pad = (n) => String(n).padStart(2, "0");
  const localDay = (d = new Date()) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
  const fmtKg = (v) => window.I18N.num(v, 1);
  const shortDay = (iso) => { const [, m, d] = iso.split("-"); return `${Number(m)}/${Number(d)}`; };

  function css() {
    if ($("#wt-css")) return;
    const s = document.createElement("style");
    s.id = "wt-css";
    s.textContent = ".wt-ico{vertical-align:-3px;margin-right:8px;color:var(--text-2)}"
      + ".wt-top{display:flex;gap:10px;align-items:stretch}.wt-kg{flex:1;min-width:0;display:flex;align-items:baseline;gap:6px;background:var(--surface);border-radius:12px;padding:6px 14px}"
      + ".sheet .wt-kg{background:var(--in-sheet)}.wt-kg input{border:0;background:none;font:600 32px/1.2 inherit;font-variant-numeric:tabular-nums;width:100%;min-width:0;padding:4px 0}.wt-kg input:focus{outline:none}.wt-kg span{color:var(--text-2)}"
      + ".wt-date{flex:none;width:156px;min-width:0;max-width:46%;padding-left:10px;padding-right:8px}.wt-sec{margin-top:16px}.wt-sec>label{display:block;font-size:12px;color:var(--text-2);font-weight:500;margin-bottom:8px}"
      + ".wt-recent{margin-top:20px}.wt-row{display:flex;align-items:baseline;gap:10px;padding:9px 0;border-top:1px solid var(--hair)}"
      + ".wt-row .d{color:var(--text-2);font-size:13px;width:44px;flex:none}.wt-row .k{font-weight:600;font-variant-numeric:tabular-nums;flex:none}"
      + ".wt-row .c{color:var(--text-2);font-size:13px;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}";
    document.head.appendChild(s);
  }

  async function loadRecent(root) {
    const box = $("#wt-recent", root);
    if (!box) return;
    let items = [];
    try { items = (await C.api("/v1/users/me/weight/recent?limit=8")).items || []; } catch (e) { return; }   // offline / older server: no list
    if (!$("#wt-recent")) return;   // the sheet was closed meanwhile
    box.innerHTML = items.length ? items.map((r) => `<div class="wt-row"><span class="d">${esc(shortDay(r.date))}</span><span class="k">${esc(fmtKg(r.weight_kg))} kg</span>
      <span class="c">${esc([...r.tags.map(tagLabel), r.note].filter(Boolean).join(" · "))}</span></div>`).join("") : "";
  }

  function open() {
    css();
    const today = C.today?.() || localDay();
    C.openSheet(tr("记体重"), `
      <div class="wt-top">
        <label class="wt-kg"><input id="wt-kg" type="number" inputmode="decimal" step="0.1" min="${MIN_KG}" max="${MAX_KG}" placeholder="0.0" aria-label="${esc(tr("体重"))}"><span>kg</span></label>
        <input class="input wt-date" id="wt-date" type="date" max="${esc(today)}" value="${esc(today)}" aria-label="${esc(tr("日期"))}">
      </div>
      <div class="wt-sec"><label>${esc(tr("测量情况"))}</label><div class="tags" id="wt-tags">${TAGS.map(([k, l]) =>
        `<button type="button" class="tag" data-wt="${k}" aria-pressed="false"><span>${esc(l())}</span></button>`).join("")}</div></div>
      <div class="wt-sec"><input class="input" id="wt-note" maxlength="120" placeholder="${esc(tr("备注（选填）"))}" aria-label="${esc(tr("备注"))}"></div>
      <div class="btns"><button class="btn btn-primary" id="wt-save" style="flex:1">${esc(tr("记下"))}</button></div>
      <div class="wt-recent" id="wt-recent"></div>`,
    (root) => { bind(root, today); loadRecent(root); });
  }

  function bind(root, today) {
    const chosen = new Set();
    for (const b of $$("[data-wt]", root)) {
      b.onclick = () => {
        const k = b.dataset.wt, on = !chosen.has(k);
        on ? chosen.add(k) : chosen.delete(k);
        if (on) for (const g of EXCLUSIVE) if (g.includes(k)) for (const o of g) if (o !== k) chosen.delete(o);
        for (const x of $$("[data-wt]", root)) { const sel = chosen.has(x.dataset.wt); x.classList.toggle("on", sel); x.setAttribute("aria-pressed", String(sel)); }
      };
    }
    $("#wt-save", root).onclick = () => save(root, today, chosen);
    $("#wt-kg", root).onkeydown = (e) => { if (e.key === "Enter") save(root, today, chosen); };
  }

  async function save(root, today, chosen, overwrite) {
    const kg = Number($("#wt-kg", root).value), day = $("#wt-date", root).value || today;
    if (!(kg >= MIN_KG && kg <= MAX_KG)) { C.toast(tr("体重写 30–250 kg")); return; }
    if (day > today) { C.toast(tr("不能记未来的日子")); return; }
    const note = $("#wt-note", root).value.trim();
    const body = { date: day, weight_kg: kg, client_id: window.crypto?.randomUUID?.() || `w${Date.now()}`, context_tags: TAGS.map((t) => t[0]).filter((k) => chosen.has(k)),
      note, ...(day === today ? { measured_at: new Date().toISOString() } : {}), ...(overwrite ? { overwrite: true } : {}) };
    const btn = $("#wt-save", root);
    btn.disabled = true;
    try {
      const r = await C.api("/v1/users/me/weight", { method: "POST", body, queue: { kind: "weight", label: tr("体重 {kg} kg · {day}", { kg: fmtKg(kg), day: shortDay(day) }), group: `w:${day}`, coalesce: `w:${day}` } });
      C.dropCache?.();
      if (r.queued) C.toast(tr("离线，已存在本机，联网后自动提交"));
      else { C.toast(tr("已记录 {v}", { v: `${fmtKg(kg)} kg` })); loadRecent(root); }
      $("#wt-kg", root).value = "";
      $("#wt-note", root).value = "";
    } catch (err) {
      if (err.status === 409 && err.data?.detail?.conflict === "weight") C.toast(err.message, tr("用这次的覆盖"), () => save(root, today, chosen, true));
      else C.toast(err.status === 0 ? tr("离线，连上网络后重试") : err.message);
    }
    btn.disabled = false;
  }

  if (typeof module !== "undefined") module.exports = { TAGS, EXCLUSIVE };
  if (typeof window === "undefined") return;
  (window.BodyPlugins = window.BodyPlugins || []).push({
    init: (ctx) => { C = ctx; },
    meItem: (chev) => `<button class="item" id="me-weight"><span class="row-title">${ICON}${tr("记体重")}</span>${chev || ""}</button>`,
    bindMe: (view) => { const b = $("#me-weight", view); if (b) b.onclick = open; },
  });
})();
