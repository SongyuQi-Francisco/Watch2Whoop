/* Profile and electronic consent (body_engine/profile/). Separate from app.js: registered in window.BodyPlugins, app.js calls init after startup,
   and the Me page gets a "Profile" (「个人资料」) row.
   - First login (a test user who hasn't consented yet): a full-screen sheet shows the key points + full text of the health-data consent form (《健康数据使用同意书》); the user must tick "I have read and agree" (「我已阅读并同意」) to continue;
     until then the server does not process this person's health data. When the consent form is revised, people who already agreed are asked to confirm again (data is not blocked).
   - If the profile (birthday / sex / height) is empty after consenting, a "Complete your profile" (「完善个人资料」) prompt appears once; it can be postponed with "Later" (「以后再填」) (remembered on this device, can be edited any time on the Me page).
   Endpoints: GET/PATCH /v1/users/me/profile, GET /v1/consent, POST /v1/users/me/consent. */
(() => {
  "use strict";
  let C = null;
  let P = null;              // result of the latest GET /profile
  const $ = (s, r = document) => r.querySelector(s);
  const esc = (v) => C.esc(v);
  const tr = (k, p) => window.I18N.t(k, p);
  const LATER_KEY = "be.profile.later";
  const ZH_NAME = "中文";   // i18n-ok (the language picker shows each language in its own name)
  const TZS = ["Australia/Sydney", "Australia/Melbourne", "Australia/Brisbane", "Australia/Adelaide", "Australia/Perth",
    "Pacific/Auckland", "Asia/Shanghai", "Asia/Hong_Kong", "Asia/Taipei", "Asia/Singapore", "Asia/Kuala_Lumpur",
    "Asia/Tokyo", "Europe/London", "America/Los_Angeles", "America/New_York"];
  const CSS = `
    .consent{position:fixed;inset:0;z-index:60;background:var(--bg);color:var(--text);overflow:auto;-webkit-overflow-scrolling:touch}
    .consent-in{max-width:560px;margin:0 auto;padding:calc(env(safe-area-inset-top) + 28px) 20px calc(env(safe-area-inset-bottom) + 28px)}
    .consent h1{font-size:24px;margin:0 0 6px}.consent ul{padding-left:20px;margin:14px 0}.consent li{margin:8px 0;line-height:1.5}
    .consent-full{background:var(--surface);border-radius:14px;padding:4px 16px;margin:10px 0;font-size:14px;line-height:1.6;color:var(--text-2)}
    .consent-full h2{font-size:16px;color:var(--text);margin:16px 0 6px}.consent-full h3{font-size:15px;color:var(--text)}
    .consent-check{display:flex;gap:10px;align-items:flex-start;margin:18px 0 4px;font-size:15px;line-height:1.4}
    .consent-check input{width:22px;height:22px;flex:none;margin:0}
    .consent .btn[disabled]{opacity:.4}
    #pf-birth{-webkit-appearance:none;appearance:none;display:block;min-width:0;max-width:100%;box-sizing:border-box;text-align:left}`;

  function injectCss() {
    if ($("#profile-css")) return;
    const st = document.createElement("style");
    st.id = "profile-css";
    st.textContent = CSS;
    document.head.appendChild(st);
  }

  // The full consent text is Markdown: only headings, lists, paragraphs, bold, inline code and horizontal rules are used
  function md(text) {
    const inline = (t) => esc(t).replace(/\*\*(.+?)\*\*/g, "<b>$1</b>").replace(/`([^`]+)`/g, "<code>$1</code>");
    const out = [];
    let list = false;
    for (const raw of text.split("\n")) {
      const line = raw.trimEnd();
      const li = /^\s*[-*]\s+(.*)$/.exec(line);
      if (!li && list) { out.push("</ul>"); list = false; }
      if (li) { if (!list) { out.push("<ul>"); list = true; } out.push(`<li>${inline(li[1])}</li>`); continue; }
      const h = /^(#{1,3})\s+(.*)$/.exec(line);
      if (h) { out.push(`<h${h[1].length + 1}>${inline(h[2])}</h${h[1].length + 1}>`); continue; }
      if (/^-{3,}$/.test(line)) { out.push("<hr>"); continue; }
      if (line) out.push(`<p>${inline(line)}</p>`);
    }
    if (list) out.push("</ul>");
    return out.join("");
  }

  async function load() {
    P = await C.api("/v1/users/me/profile");
    return P;
  }

  // ---------------------------------------------------------------- consent
  async function showConsent(reconfirm) {
    let c;
    try { c = await C.api("/v1/consent"); } catch (e) { C.toast(e.message); return; }
    injectCss();
    const el = document.createElement("div");
    el.className = "consent";
    el.id = "consent";
    el.setAttribute("role", "dialog");
    el.setAttribute("aria-modal", "true");
    el.innerHTML = `<div class="consent-in">
      <h1>${esc(reconfirm ? tr("同意书更新了") : tr("使用前请确认"))}</h1>
      <p class="para" style="margin:0">${esc(reconfirm ? tr("《健康数据使用同意书》有了新版本，请再看一遍。") : tr("Watch2Whoop 会处理你的健康数据。请先看完下面的要点（或全文），同意后才开始处理。"))}</p>
      <ul>${c.summary.map((s) => `<li>${esc(s)}</li>`).join("")}</ul>
      <button class="link" id="consent-more" type="button">${esc(tr("阅读完整同意书（版本 {v}）", { v: c.version }))}</button>
      <div class="consent-full" id="consent-full" hidden>${md(c.text)}</div>
      <label class="consent-check"><input type="checkbox" id="consent-ok"><span>${esc(tr("我已阅读并同意《健康数据使用同意书》"))}</span></label>
      <div class="btns"><button class="btn btn-primary" id="consent-yes" disabled>${esc(tr("同意并继续"))}</button>
        ${reconfirm ? "" : `<button class="btn" id="consent-no">${esc(tr("不同意"))}</button>`}</div>
      <p class="small muted" id="consent-msg" style="margin-top:14px"></p></div>`;
    document.body.appendChild(el);
    document.body.style.overflow = "hidden";
    $("#consent-more", el).onclick = () => { const f = $("#consent-full", el); f.hidden = !f.hidden; };
    $("#consent-ok", el).onchange = (e) => { $("#consent-yes", el).disabled = !e.target.checked; };
    const no = $("#consent-no", el);
    if (no) no.onclick = () => {
      $("#consent-msg", el).textContent = tr("不同意就不能使用 Watch2Whoop。你的健康数据目前只暂存、没有处理；请告诉管理员删除你的账号和暂存的数据。改主意了随时回来点「同意并继续」。");
    };
    $("#consent-yes", el).onclick = async () => {
      $("#consent-yes", el).disabled = true;
      try {
        P = await C.api("/v1/users/me/consent", { method: "POST", body: { version: c.version, agree: true, language: c.language || undefined } });   // the text that was read
      } catch (e) {
        $("#consent-msg", el).textContent = e.message;
        $("#consent-yes", el).disabled = false;
        return;
      }
      el.remove();
      document.body.style.overflow = "";
      C.toast(tr("谢谢！同步上来的数据一小时内开始处理"));
      maybePrompt();
    };
  }

  // ---------------------------------------------------------------- profile
  function laterSet() { try { localStorage.setItem(LATER_KEY, new Date().toISOString()); } catch (e) { /* private mode */ } }
  function laterGet() { try { return localStorage.getItem(LATER_KEY); } catch (e) { return null; } }

  let prompted = false;
  function maybePrompt() {
    if (prompted || !P || !P.missing.length || laterGet()) return;
    prompted = true;
    openProfile(true);
  }

  function tzOptions(cur) {
    let guess = "";
    try { guess = Intl.DateTimeFormat().resolvedOptions().timeZone || ""; } catch (e) { /* old browsers */ }
    const list = [...new Set([cur, guess, ...TZS].filter(Boolean))];
    return list.map((z) => `<option value="${esc(z)}"${z === cur ? " selected" : ""}>${esc(z)}</option>`).join("");
  }

  async function openProfile(first) {
    injectCss();   // iOS Safari draws <input type=date> wider than the sheet without it
    try { if (!P || !first) await load(); } catch (e) { C.toast(e.message); return; }
    const p = P;
    const v = (x) => (x == null ? "" : esc(x));
    C.openSheet(tr(first ? "profile.complete" : "profile.title"), `
      ${first ? `<p class="para" style="margin:0 0 4px">${esc(tr("profile.intro"))}</p>` : ""}
      <div class="field"><label>${tr("profile.name")}</label><input class="input" id="pf-name" maxlength="30" value="${v(p.display_name)}"></div>
      <div class="field"><label>${tr("profile.birth")}</label><input class="input" id="pf-birth" type="date" min="1920-01-01" value="${v(p.birth_date)}"></div>
      <div class="field"><label>${tr("profile.sex")}</label><select class="input" id="pf-sex">
        <option value="">${tr("profile.sexNone")}</option><option value="male"${p.sex === "male" ? " selected" : ""}>${tr("profile.male")}</option>
        <option value="female"${p.sex === "female" ? " selected" : ""}>${tr("profile.female")}</option></select></div>
      <div class="field"><label>${tr("profile.height")}</label><input class="input" id="pf-height" type="number" inputmode="decimal" min="100" max="230" step="0.1" value="${v(p.height_cm)}"></div>
      <div class="field"><label>${tr("profile.goal")}</label><input class="input" id="pf-goal" type="number" inputmode="decimal" min="30" max="250" step="0.1" value="${v(p.goal_weight_kg)}"></div>
      <div class="field"><label>${tr("profile.tz")}</label><select class="input" id="pf-tz">${tzOptions(p.timezone)}</select></div>
      <div class="field"><label>${tr("profile.lang")}</label><select class="input" id="pf-lang">
        <option value="">${tr("profile.langAuto")}</option><option value="zh"${p.language === "zh" ? " selected" : ""}>${ZH_NAME}</option>
        <option value="en"${p.language === "en" ? " selected" : ""}>English</option></select></div>
      <p class="small muted" id="pf-msg" style="margin-top:10px"></p>
      <div class="btns"><button class="btn btn-primary" id="pf-save">${tr("common.save")}</button>${first ? `<button class="btn" id="pf-later">${tr("profile.later")}</button>` : ""}</div>`,
    (root) => {
      const later = $("#pf-later", root);
      if (later) later.onclick = () => { laterSet(); C.closeSheet(); };
      $("#pf-save", root).onclick = async () => {
        const num = (id) => { const x = $(id, root).value.trim(); return x === "" ? null : Number(x); };
        const body = { display_name: $("#pf-name", root).value.trim(), birth_date: $("#pf-birth", root).value || null,
          sex: $("#pf-sex", root).value || null, height_cm: num("#pf-height"), goal_weight_kg: num("#pf-goal"),
          timezone: $("#pf-tz", root).value, language: $("#pf-lang", root).value || null };
        const renamed = body.display_name !== p.display_name || body.timezone !== p.timezone;
        try {
          P = await C.api("/v1/users/me/profile", { method: "PATCH", body });
        } catch (e) { $("#pf-msg", root).textContent = e.message; return; }
        laterSet();
        const relang = window.I18N.setPref(P.language);   // the whole page is drawn in the new language after a reload
        C.closeSheet();
        C.toast(tr("common.saved"));
        if (renamed || relang) setTimeout(() => location.reload(), 600);   // Display name / timezone are used in the header and dates: reload
        else updateItem();
      };
    });
  }

  function updateItem() {
    const el = document.getElementById("me-profile-val");
    if (el && P) el.textContent = P.missing.length ? tr("profile.incomplete") : P.display_name;
  }

  async function init(ctx) {
    C = ctx;
    try { await load(); } catch (e) { return; }   // older servers don't have this endpoint
    if ($("#consent")) return;                     // The startup flow ran again (refresh, re-login) and the sheet is already there
    if (P.consent.required) showConsent(false);
    else if (P.consent.reconfirm) showConsent(true);
    else maybePrompt();
  }

  window.BodyProfile = { open: () => openProfile(false), consent: (reconfirm) => showConsent(reconfirm) };
  (window.BodyPlugins = window.BodyPlugins || []).push({
    init,
    meItem: (chev) => `<button class="item" id="me-profile"><span class="row-title">${tr("profile.title")}</span><span class="val" id="me-profile-val">${P ? esc(P.missing.length ? tr("profile.incomplete") : P.display_name) : ""}</span>${chev || ""}</button>`,
    bindMe: (view) => { const b = $("#me-profile", view); if (b) b.onclick = () => openProfile(false); updateItem(); },
  });
})();
