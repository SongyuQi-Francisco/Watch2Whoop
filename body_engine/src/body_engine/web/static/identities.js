/* Sign-in methods of the signed-in account (account/identities.py): Me → 「登录方式」 lists the bound e-mails / Apple ID and
   binds more: 「绑定邮箱」 (code from POST /v1/auth/email/start, then POST /v1/account/identities/email) and
   「绑定 Apple 登录」 (inside the Watch2Whoop app only, when the app sets window.Watch2WhoopFeatures.appleLink: the page posts
   "apple-link" on the app's JS channel, the app runs Sign in with Apple and calls
   window.BodyIdentities.appleResult({identity_token, nonce}) — or appleResult(null) when the user cancels).
   An e-mail / Apple ID that already belongs to another account is refused (409 identity_taken); accounts are never merged.
   Also: the login page's new hint texts and the 「登录 / 注册」 wording go through tr() below.
   Loaded before account.js (which uses window.BodyIdentities.tr for its login hint). */
(() => {
  "use strict";
  const $ = (s, r = document) => r.querySelector(s);
  const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  let C = null;
  let ME = null;
  let ACCT = null;

  // ---- i18n bridge until #20's i18n.js is merged (same convention as #20: the Chinese source text is the key, {name}
  // placeholders; window.I18N wins when present). After #20 merges: move EN into i18n-en.js and use #20's tr.
  // Language as in #20: localStorage "be.lang" (zh / en), else the device language.
  const EN = {   // i18n-ok (translation table)
    "登录 / 注册": "Sign in / sign up",
    "用邮箱登录 / 注册": "Sign in or sign up with e-mail",
    "以前用激活链接登录？请管理员先绑定你的邮箱，再用邮箱登录": "Used an activation link before? Ask your admin to bind your e-mail first, then sign in with it",
    "已经有账号？这个邮箱可能还没绑定：退出后用激活链接登录，或请管理员绑定邮箱": "Already have an account? This e-mail may not be bound yet: sign out and use your activation link, or ask your admin to bind it",
    "登录方式": "Sign-in methods",
    "{n} 种": "{n}",
    "邮箱": "E-mail",
    "绑定邮箱": "Add e-mail",
    "绑定 Apple 登录": "Add Sign in with Apple",
    "在 Watch2Whoop app 里绑定 Apple 登录": "Add Sign in with Apple inside the Watch2Whoop app",
    "还没有绑定登录方式": "No sign-in method yet",
    "发送验证码": "Send code",
    "6 位验证码": "6-digit code",
    "绑定": "Add",
    "已绑定": "Added",
    "邮箱格式不对": "Invalid e-mail address",
    "验证码不对或已过期": "Wrong or expired code",
    "验证码已发送，10 分钟内有效": "Code sent, valid for 10 minutes",
    "发送太频繁，过一会儿再试": "Too many requests, try again in a moment",
    "这个邮箱或 Apple ID 已经属于另一个账号": "This e-mail or Apple ID already belongs to another account",
    "已取消": "Cancelled",
  };
  const lang = () => {
    try { const v = localStorage.getItem("be.lang"); if (v === "zh" || v === "en") return v; } catch (e) { /* storage blocked */ }
    const l = String((navigator.languages && navigator.languages[0]) || navigator.language || "").toLowerCase();
    return l.startsWith("zh") ? "zh" : "en";
  };
  const tr = (key, params) => {
    if (window.I18N?.t) return window.I18N.t(key, params);
    const out = lang() === "en" ? EN[key] ?? key : key;
    return params ? out.replace(/\{(\w+)\}/g, (m, k) => (params[k] != null ? String(params[k]) : m)) : out;
  };
  // static texts on the login page that carry data-i18n with a key from EN (the rest of the page is translated by #20)
  function applyStatic(root = document) {
    if (window.I18N?.apply) return;
    for (const el of root.querySelectorAll("[data-i18n]")) if (EN[el.dataset.i18n]) el.textContent = tr(el.dataset.i18n);
  }

  async function call(path, method = "GET", body) {
    const headers = { Accept: "application/json" };
    if (method !== "GET") headers["X-Body-Web"] = "1";
    if (body !== undefined) headers["Content-Type"] = "application/json";
    let r;
    try { r = await fetch(path, { method, headers, credentials: "same-origin", body: body === undefined ? undefined : JSON.stringify(body) }); }
    catch (e) { const err = new Error("offline"); err.status = 0; throw err; }
    let data = null;
    try { data = await r.json(); } catch (e) { /* no body */ }
    if (!r.ok) { const err = new Error(String(data?.detail || r.status)); err.status = r.status; throw err; }
    return data;
  }
  const errText = (err) => (err.status === 409 ? tr("这个邮箱或 Apple ID 已经属于另一个账号")
    : err.status === 401 ? tr("验证码不对或已过期") : err.status === 429 ? tr("发送太频繁，过一会儿再试")
    : err.status === 400 ? tr("邮箱格式不对") : err.message);

  const appleLinkInApp = () => !!window.Watch2Whoop && !!window.Watch2WhoopFeatures?.appleLink;

  function listHtml(ids) {
    if (!ids.length) return `<p class="para muted">${esc(tr("还没有绑定登录方式"))}</p>`;
    return `<div class="group">${ids.map((i) => `<div class="item"><span class="row-title">${esc(i.provider === "apple" ? "Apple" : tr("邮箱"))}</span>
      <span class="val">${esc(i.email || (i.provider === "apple" ? "Apple ID" : ""))}</span></div>`).join("")}</div>`;
  }

  function methodsSheet() {
    C.openSheet(tr("登录方式"), `<div id="idm-body"><p class="para muted">…</p></div>`, async (root) => {
      const body = $("#idm-body", root);
      try { ACCT = await call("/v1/account"); } catch (err) { body.innerHTML = `<p class="para">${esc(errText(err))}</p>`; return; }
      const ids = ACCT.identities || [];
      const apple = ids.some((i) => i.provider === "apple");
      body.innerHTML = `${listHtml(ids)}
        <div class="btns"><button class="btn" id="idm-email">${esc(tr("绑定邮箱"))}</button>
        ${ACCT.apple_link && !apple ? `<button class="btn" id="idm-apple" ${appleLinkInApp() ? "" : "disabled"}>${esc(tr("绑定 Apple 登录"))}</button>` : ""}</div>
        ${ACCT.apple_link && !apple && !appleLinkInApp() ? `<p class="para small muted">${esc(tr("在 Watch2Whoop app 里绑定 Apple 登录"))}</p>` : ""}
        <div id="idm-form" hidden>
          <input id="idm-addr" class="input" type="email" autocomplete="email" autocapitalize="off" autocorrect="off" spellcheck="false" placeholder="${esc(tr("邮箱"))}" aria-label="${esc(tr("邮箱"))}">
          <div class="btns"><button class="btn" id="idm-send">${esc(tr("发送验证码"))}</button></div>
          <input id="idm-code" class="input" inputmode="numeric" autocomplete="one-time-code" maxlength="6" placeholder="${esc(tr("6 位验证码"))}" aria-label="${esc(tr("6 位验证码"))}" hidden>
          <div class="btns"><button class="btn btn-primary" id="idm-bind" hidden>${esc(tr("绑定"))}</button></div>
        </div>
        <p class="acct-err" id="idm-msg" role="alert"></p>`;
      const msg = (t) => { $("#idm-msg", root).textContent = t || ""; };
      $("#idm-email", root).onclick = () => { $("#idm-form", root).hidden = false; $("#idm-addr", root).focus(); };
      $("#idm-send", root).onclick = async () => {
        const email = $("#idm-addr", root).value.trim();
        msg("");
        if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) { msg(tr("邮箱格式不对")); return; }
        try {
          await call("/v1/auth/email/start", "POST", { email, lang: lang() });
          $("#idm-code", root).hidden = $("#idm-bind", root).hidden = false;
          $("#idm-code", root).focus();
          msg(tr("验证码已发送，10 分钟内有效"));
        } catch (err) { msg(errText(err)); }
      };
      $("#idm-bind", root).onclick = async () => {
        msg("");
        try {
          await call("/v1/account/identities/email", "POST", { email: $("#idm-addr", root).value.trim(), code: $("#idm-code", root).value.trim() });
          C.toast(tr("已绑定"));
          methodsSheet();
        } catch (err) { msg(errText(err)); }
      };
      const ab = $("#idm-apple", root);
      if (ab) ab.onclick = () => { msg(""); try { window.Watch2Whoop.postMessage("apple-link"); } catch (e) { /* older app */ } };
      // the app answers through window.BodyIdentities.appleResult
      appleDone = async (res) => {
        if (!res || !res.identity_token) { msg(tr("已取消")); return; }
        try {
          await call("/v1/account/identities/apple", "POST", { identity_token: res.identity_token, nonce: res.nonce || null });
          C.toast(tr("已绑定"));
          methodsSheet();
        } catch (err) { msg(errText(err)); }
      };
    });
  }
  let appleDone = null;

  window.BodyIdentities = { tr, appleResult: (res) => appleDone?.(res) };
  (window.BodyPlugins = window.BodyPlugins || []).push({
    init: (ctx, me) => { C = ctx; ME = me?.me || null; },
    meItem: (chev) => (ME ? `<button class="item" id="me-signin"><span class="row-title">${esc(tr("登录方式"))}</span><span class="val" id="me-signin-val"></span>${chev || ""}</button>` : ""),
    bindMe: async (view) => {
      const b = $("#me-signin", view);
      if (!b) return;
      b.onclick = methodsSheet;
      try {
        ACCT = await call("/v1/account");
        const n = (ACCT.identities || []).length;
        const v = $("#me-signin-val", view);
        if (v) v.textContent = n ? tr("{n} 种", { n }) : "—";
      } catch (e) { /* the row still opens the sheet */ }
    },
  });

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", () => applyStatic());
  else applyStatic();
})();
