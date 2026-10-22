/* The user's own exercises (issue #189, rollout flag muscle_load; training/custom.py, /training/custom-exercises):
   动作库 → 我的动作 lists them, edits one (names, aliases, primary / secondary muscles, equipment), deletes one, adds one by hand.
   Mostly the user's AI adds them (add_custom_exercise, from 「让 Jarvis 补进动作库」 on the muscle load map); nobody else sees them.
   Muscle and equipment names come from the server (already in the page language).
   ctx: { api, esc, openSheet(title, html, after), closeSheet, toast, setBack(fn|null), back() (the library), onChange() } */
(function (root) {
  "use strict";
  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => [...r.querySelectorAll(s)];
  const tr = (k, p) => root.I18N.t(k, p);
  const SEP = () => tr("、");
  const URL = "/v1/users/me/training/custom-exercises";
  const MAX_PRIMARY = 4;
  let C = null, D = null;

  const CSS = `
    .cx-row{display:flex;align-items:center;gap:8px;padding:11px 2px;border-top:1px solid var(--hair)}
    .cx-row:first-child{border-top:0} .cx-row .cx-t{flex:1;min-width:0}
    .cx-row .btn{flex:none;min-height:36px;padding:0 12px;font-size:14px;background:var(--surface-2)}
    .cx-chips{display:flex;gap:6px;flex-wrap:wrap;margin:4px 0 10px}
    .cx-chip{border:0;border-radius:999px;padding:6px 11px;font:inherit;font-size:13px;background:var(--surface-2);color:var(--text)}
    .cx-chip[aria-pressed="true"]{background:var(--train);color:#fff}
    .cx-f label{display:block;font-size:13px;color:var(--text-2);margin:10px 0 4px}
    .cx-f .input{width:100%}`;
  function css() {
    if (document.getElementById("cx-css")) return;
    document.head.append(Object.assign(document.createElement("style"), { id: "cx-css", textContent: CSS }));
  }
  const on = () => !!root.BodyFlags?.on("muscle_load");
  const name = (x) => (root.I18N.lang === "en" && x.name_en ? x.name_en : x.name_zh);

  async function load() { D = await C.api(URL); return D; }

  // ---------------------------------------------------------------- list
  function openList(ctx) { C = ctx || C; css(); return drawListSheet(); }
  function drawList() {
    const { esc } = C;
    const rows = D.exercises.map((x) => `<div class="cx-row"><div class="cx-t"><div class="row-title">${esc(name(x))}${x.source === "ai" ? ` <span class="badge plain">AI</span>` : ""}</div>
        <div class="small muted">${esc([x.primary_names.join(SEP()), x.secondary_names.length ? `${tr("辅助")} ${x.secondary_names.join(SEP())}` : "", x.equipment_names.join(SEP())].filter(Boolean).join(" · "))}</div></div>
        <button class="btn" data-cx-edit="${x.id}">${tr("改")}</button><button class="btn btn-danger" data-cx-del="${x.id}">${tr("删")}</button></div>`).join("");
    $("#sheet-body").innerHTML = `
      <div class="card list">${rows || `<div class="empty faint">${tr("还没有自己的动作；肌肉图上没对上动作库的动作，可以让 Jarvis 补进来")}</div>`}</div>
      <div class="btns"><button class="btn btn-primary" id="cx-add">${tr("添加动作")}</button></div>`;
    const root = $("#sheet-body");
    $("#cx-add", root).onclick = () => openForm(null);
    $$("[data-cx-edit]", root).forEach((b) => { b.onclick = () => openForm(D.exercises.find((x) => String(x.id) === b.dataset.cxEdit)); });
    $$("[data-cx-del]", root).forEach((b) => { b.onclick = () => remove(D.exercises.find((x) => String(x.id) === b.dataset.cxDel)); });
  }

  async function remove(x) {
    if (!x || !confirm(tr("删掉「{name}」？记录里这个名字的组会重新算作没对上动作库。", { name: name(x) }))) return;
    try { await C.api(`${URL}/${x.id}`, { method: "DELETE" }); } catch (e) { C.toast(e.message); return; }
    C.toast(tr("已删除"));
    C.onChange?.();
    await load().catch(() => null);
    if (D) drawList();
  }

  // ---------------------------------------------------------------- add / edit
  // form values -> request body, or { error } (already translated). Pure: tests/test_custom_exercises.py runs it in node.
  function formBody(f) {
    const nameZh = String(f.name_zh || "").trim();
    if (!nameZh) return { error: tr("中文名不能为空") };
    if (!f.primary?.length) return { error: tr("至少选一个主要肌群") };
    if (f.primary.length > MAX_PRIMARY) return { error: tr("主要肌群最多 {n} 个", { n: MAX_PRIMARY }) };
    const aliases = String(f.aliases || "").split(/[,，、\n]/).map((a) => a.trim()).filter(Boolean);   // i18n-ok (Chinese separators typed in the field)
    return { body: { name_zh: nameZh, name_en: String(f.name_en || "").trim(), aliases, primary_muscles: [...f.primary],
      secondary_muscles: [...(f.secondary || [])].filter((m) => !f.primary.includes(m)), equipment: [...(f.equipment || [])] } };
  }

  function openForm(x) {
    const { esc } = C;
    const sel = { primary: new Set(x?.primary_muscles || []), secondary: new Set(x?.secondary_muscles || []), equipment: new Set(x?.equipment || []) };
    const chips = (group, items) => `<div class="cx-chips" data-group="${group}">${items.map((o) => `<button type="button" class="cx-chip" data-k="${esc(o.key)}" aria-pressed="${sel[group].has(o.key)}">${esc(o.name)}</button>`).join("")}</div>`;
    C.openSheet(x ? tr("改动作") : tr("添加动作"), `<div class="cx-f">
      <label>${tr("中文名")}</label><input class="input" id="cx-zh" maxlength="80" value="${esc(x?.name_zh || "")}">
      <label>${tr("英文名")}</label><input class="input" id="cx-en" maxlength="80" value="${esc(x?.name_en || "")}">
      <label>${tr("别名（逗号分隔）")}</label><input class="input" id="cx-al" value="${esc((x?.aliases || []).join(", "))}">
      <label>${tr("主要肌群")} <span class="small muted">${tr("最多 {n} 个", { n: MAX_PRIMARY })}</span></label>${chips("primary", D.muscles)}
      <label>${tr("次要肌群")}</label>${chips("secondary", D.muscles)}
      <label>${tr("器械")}</label>${chips("equipment", D.equipment)}
      <p class="small muted" id="cx-msg"></p>
      <div class="btns"><button class="btn btn-primary" id="cx-save">${tr("保存")}</button></div></div>`,
    (root) => {
      C.setBack(() => drawListSheet());
      $$("[data-group]", root).forEach((g) => {
        const group = g.dataset.group;
        $$(".cx-chip", g).forEach((b) => {
          b.onclick = () => {
            const k = b.dataset.k;
            if (sel[group].has(k)) sel[group].delete(k);
            else sel[group].add(k);
            b.setAttribute("aria-pressed", sel[group].has(k));
          };
        });
      });
      $("#cx-save", root).onclick = async () => {
        const r = formBody({ name_zh: $("#cx-zh", root).value, name_en: $("#cx-en", root).value, aliases: $("#cx-al", root).value,
          primary: [...sel.primary], secondary: [...sel.secondary], equipment: [...sel.equipment] });
        if (r.error) { $("#cx-msg", root).textContent = r.error; return; }
        try { await C.api(x ? `${URL}/${x.id}` : URL, { method: x ? "PATCH" : "POST", body: r.body }); }
        catch (e) { $("#cx-msg", root).textContent = e.message; return; }
        C.toast(tr("已保存"));
        C.onChange?.();
        drawListSheet();
      };
    });
  }
  async function drawListSheet() {
    C.openSheet(tr("我的动作"), `<div class="empty faint">…</div>`);
    C.setBack(C.back);
    try { await load(); } catch (e) { $("#sheet-body").innerHTML = `<div class="empty faint">${C.esc(e.message || "")}</div>`; return; }
    drawList();
  }

  const exported = { on, openList, formBody };
  if (typeof module !== "undefined" && module.exports) module.exports = exported; else root.BodyCustomExercises = exported;
})(typeof window !== "undefined" ? window : globalThis);
