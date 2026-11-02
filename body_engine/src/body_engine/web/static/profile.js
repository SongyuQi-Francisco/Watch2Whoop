/* Profile and electronic consent (body_engine/profile/). Separate from app.js: registered in window.BodyPlugins, app.js calls init after startup,
   and the Me page gets a "Profile" (「个人资料」) row.
   - First login (a test user who hasn't consented yet): a full-screen sheet shows the key points + full text of the health-data consent form (《健康数据使用同意书》); the user must tick "I have read and agree" (「我已阅读并同意」) to continue;
     until then the server does not process this person's health data. When the consent form is revised, people who already agreed are asked to confirm again (data is not blocked).
   - Flag consent_one_tap (P.consent.one_tap): the sheet is one 「开始使用」 button and one line naming the two texts
     (《数据与隐私说明》 = the consent form above, 《Jarvis AI 说明》 = the fallback AI's points when GET /v1/consent has `ai`); each name
     opens its full text. One tap records the consent (method one_tap) and the Jarvis terms together; a revised text is
     reconfirmed the same way. The texts and their versions are the same as with the tick box.
   - If the profile (birthday / sex / height) is empty after consenting, a "Complete your profile" (「完善个人资料」) prompt appears once; it can be postponed with "Later" (「以后再填」) (remembered on this device, can be edited any time on the Me page).
   Endpoints: GET/PATCH /v1/users/me/profile, GET /v1/consent, POST /v1/users/me/consent. */
(() => {
  "use strict";
  let C = null;
  let P = null;              // result of the latest GET /profile
  let MY_AVATAR = null;      // my picture (/v1/web/me, avatars.js, flag `avatars`)
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
    .consent-circle{background:var(--surface);border-radius:14px;padding:12px 16px;margin:10px 0;font-size:14px;line-height:1.5}
    .consent.c1{display:flex;overflow:hidden}
    .c1-in{flex:1;display:flex;flex-direction:column;width:100%;max-width:440px;margin:0 auto;box-sizing:border-box;
      padding:calc(env(safe-area-inset-top) + 28px) 24px calc(env(safe-area-inset-bottom) + 20px)}
    .c1-top{flex:1;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:14px;text-align:center}
    .c1-top img{width:84px;height:84px}.c1-top h1{margin:0;font-size:26px}
    .c1-join{font-size:13px;line-height:1.5;color:var(--text-2);text-align:center;margin:0 0 12px}
    .c1-go{width:100%;flex:none;min-height:52px;font-size:17px}
    .c1-terms{font-size:12px;line-height:1.6;color:var(--text-2);text-align:center;margin:12px 0 0}
    .c1-terms .link{font-size:12px;padding:0;color:var(--text);font-weight:600}
    .c1-doc{position:fixed;inset:0;z-index:61;background:var(--bg);overflow:auto;-webkit-overflow-scrolling:touch}
    .c1-bar{position:sticky;top:0;display:flex;align-items:center;gap:6px;background:var(--bg);border-bottom:1px solid var(--hair);
      padding:calc(env(safe-area-inset-top) + 8px) 12px 8px}
    .c1-bar b{font-size:16px}.c1-bar .link{font-size:15px;padding:6px 8px 6px 0}
    .c1-body{max-width:560px;margin:0 auto;padding:4px 16px calc(env(safe-area-inset-bottom) + 24px)}
    .c1-body .consent-full{margin:12px 0}.c1-body ul{padding-left:20px;line-height:1.6}
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
    if (P?.consent?.one_tap) { oneTap(reconfirm, c); return; }
    const el = document.createElement("div");
    el.className = "consent";
    el.id = "consent";
    el.setAttribute("role", "dialog");
    el.setAttribute("aria-modal", "true");
    // circles of activation codes used at sign-up: joined after this consent, with these scopes (friends/codes.py)
    const labels = window.BodyCircles?.labels || {};
    const joining = (P?.circle_pending || []).map((j) => `<p class="consent-circle">${esc(tr("同意后你会加入圈子「{name}」，圈子里的人能看到你的：{list}。随时可以在「圈子」里修改或退出。",
      { name: j.name, list: j.scopes.map((x) => labels[x] || x).join(" · ") || tr("什么都不共享") }))}</p>`).join("");
    el.innerHTML = `<div class="consent-in">
      <h1>${esc(reconfirm ? tr("同意书更新了") : tr("使用前请确认"))}</h1>
      <p class="para" style="margin:0">${esc(reconfirm ? tr("《健康数据使用同意书》有了新版本，请再看一遍。") : tr("Watch2Whoop 会处理你的健康数据。请先看完下面的要点（或全文），同意后才开始处理。"))}</p>
      <ul>${c.summary.map((s) => `<li>${esc(s)}</li>`).join("")}</ul>${joining}
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
    $("#consent-yes", el).onclick = () => submit(el, { version: c.version, agree: true, language: c.language || undefined });   // the text that was read
  }

  async function submit(el, body) {
    $("#consent-yes", el).disabled = true;
    try {
      P = await C.api("/v1/users/me/consent", { method: "POST", body });
    } catch (e) {
      $("#consent-msg", el).textContent = e.message;
      $("#consent-yes", el).disabled = false;
      return;
    }
    el.remove();
    document.body.style.overflow = "";
    const joined = (P.circles_joined || []).map((j) => j.name);
    C.toast(joined.length ? tr("谢谢！已加入「{name}」", { name: joined.join(" · ") }) : tr("谢谢！同步上来的数据一小时内开始处理"));
    if (joined.length) C.reload?.();   // the people you may view changed (also reloads the AI state)
    else if (body.ai_version) window.BodyAI?.refreshAi?.();   // Jarvis no longer asks for its own consent
    // The sync status was read while the consent form was open ("consent", no setup card): read it again now so the
    // 「开启数据同步」 card and sheet appear without waiting for the next refresh (sync-status.js, forced)
    window.BodySyncStatus?.refresh?.();
    maybePrompt();
  }

  // One-tap consent (flag consent_one_tap): a single button and one line naming the texts; tapping a name opens its full text
  function oneTap(reconfirm, c) {
    const el = document.createElement("div");
    el.className = "consent c1";
    el.id = "consent";
    el.setAttribute("role", "dialog");
    el.setAttribute("aria-modal", "true");
    el.setAttribute("aria-labelledby", "c1-h");
    const labels = window.BodyCircles?.labels || {};
    const joining = (P?.circle_pending || []).map((j) => `<p class="c1-join">${esc(tr("加入圈子「{name}」，成员能看到：{list}",
      { name: j.name, list: j.scopes.map((x) => labels[x] || x).join(" · ") || tr("什么都不共享") }))}</p>`).join("");
    const link = (doc, name) => `<button class="link" type="button" data-doc="${doc}">${esc(name)}</button>`;
    const data = link("data", tr("《数据与隐私说明》"));
    const terms = c.ai
      ? esc(tr("继续即表示同意{data}和{ai}")).replace("{data}", data).replace("{ai}", link("ai", tr("《Jarvis AI 说明》")))
      : esc(tr("继续即表示同意{doc}")).replace("{doc}", data);
    el.innerHTML = `<div class="c1-in">
      <div class="c1-top"><img src="icon.svg" alt=""><h1 id="c1-h">${reconfirm ? esc(tr("说明有更新")) : "Watch2Whoop"}</h1></div>
      ${joining}
      <button class="btn btn-primary c1-go" id="consent-yes" type="button">${esc(reconfirm ? tr("继续") : tr("开始使用"))}</button>
      <p class="c1-terms">${terms}</p>
      <p class="small muted" id="consent-msg" role="alert" style="text-align:center;margin:8px 0 0"></p></div>
      <div class="c1-doc" id="c1-doc" hidden><div class="c1-bar"><button class="link" type="button" id="c1-back">‹ ${esc(tr("返回"))}</button>
        <b id="c1-title"></b></div><div class="c1-body" id="c1-body"></div></div>`;
    document.body.appendChild(el);
    document.body.style.overflow = "hidden";
    const doc = $("#c1-doc", el);
    el.querySelectorAll("[data-doc]").forEach((b) => {
      b.onclick = () => {
        const ai = b.dataset.doc === "ai";
        $("#c1-title", el).textContent = ai ? tr("Jarvis AI 说明") : tr("数据与隐私说明");
        $("#c1-body", el).innerHTML = ai ? `<ul>${c.ai.points.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>`
          : `<div class="consent-full">${md(c.text)}</div>`;
        doc.hidden = false;
        doc.scrollTop = 0;
      };
    });
    $("#c1-back", el).onclick = () => { doc.hidden = true; };
    $("#consent-yes", el).onclick = () => submit(el, { version: c.version, agree: true, language: c.language || undefined,
      method: "one_tap", ai_version: c.ai?.version || undefined });
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
      ${window.BodyAvatar?.profileRow(MY_AVATAR, p.display_name) || ""}
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
      window.BodyAvatar?.bindProfile(root, MY_AVATAR, p.display_name);
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

  async function init(ctx, me) {
    C = ctx;
    MY_AVATAR = me?.me?.avatar || null;
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
